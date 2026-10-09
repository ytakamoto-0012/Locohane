"""ユーザー別ドラフトスキル（config.ini [skill_creator]）の可視性・権限判定。

skill-creator スキルが作るドラフト（未検証のスキル）は、起動時に全ユーザー
共通で走査される skills_dir / project_locohane_dir には置かず、
`<draft_dir>/<ユーザー名>/<スキル名>/` に置く。ドラフトは作成したユーザー
本人の会話でだけ、スキル名 `<ユーザー名>/<スキル名>` としてスキル一覧に出る
（draft_dir 自体を最後尾の skills ルートとして扱うため、read_skill /
read_skill_file / run_script の既存のパス解決がそのまま使える）。

他ユーザーのドラフトの扱いは [skill_creator].other_users_drafts で切り替える
（SKILL_DRAFT_VISIBILITY_MODES）。本人のドラフトは常に全操作できる。

「誰の会話か」は cl.user_session["draft_user"]（app.py がセッション開始・
再開時に resolve_log_username() の結果を入れる）だけで判定する。このキーが
無い文脈（evals のヘッドレス実行・Chainlit のセッション文脈外）では
ドラフトは一切見えない・使えない扱いになる（評価の再現性を保つため。
evals でドラフトを評価する場合は run_case.py の --skill-overlay で明示的に重ねる）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .skills import Skill, _parse_frontmatter, _skill_has_scripts, _validate

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

# ドラフトごとの来歴・状態・トライアウト履歴（skill-creator の scripts/_common.py と共有する名前）。
DRAFT_META_FILENAME = "_draft_meta.json"
# ユーザーフォルダ直下の、評価ジョブ・結果の置き場（スキルではない）。
WORKSPACE_DIRNAME = "_workspace"
# cl.user_session でドラフトの所有者判定に使うキー。
SESSION_USER_KEY = "draft_user"
# skill-creator 本体（ドラフト置き場への書き込みを許すのは skills_dir 配下のこれだけ）。
SKILL_CREATOR_NAME = "skill-creator"
# app.py が起動時のシステムプロンプトのスキル一覧末尾に残し、セッションごとに
# render_draft_skills_block() の結果へ差し替える目印。
DRAFT_SKILLS_MARKER = "<<DRAFT_SKILLS>>"

DraftOp = Literal["list", "read", "exec", "write"]

# other_users_drafts の値ごとに、他ユーザーのドラフトへ許す操作。
# write には評価（skill-creator の run_isolated_eval 等。workspace へ書き込む）も含む。
_OTHER_USER_OPS: dict[str, frozenset[str]] = {
    "hidden": frozenset(),
    "listed": frozenset({"list"}),
    "readable": frozenset({"list", "read"}),
    "full": frozenset({"list", "read", "exec", "write"}),
}

_OP_LABELS = {"list": "一覧表示", "read": "読み取り", "exec": "実行", "write": "書き込み・評価"}


@dataclass(frozen=True)
class DraftSkill:
    """セッションに見えるドラフト1件。

    Attributes:
        skill: name を `<所有者>/<スキル名>` に置き換えた Skill。
        owner: 所有者（draft_dir 直下のフォルダ名）。
        own: セッションのユーザー本人のドラフトか。
        readable: 読み取り（read_skill/read_skill_file）ができるか。
        executable: スクリプトを実行できるか。
    """

    skill: Skill
    owner: str
    own: bool
    readable: bool
    executable: bool


def current_draft_user() -> str | None:
    """現在の会話のユーザー名（ドラフト所有者判定用）を返す。

    cl.user_session はセッション文脈外で呼ぶと ChainlitContextException を
    送出するため、その場合も含めて判定できなければ None（＝ドラフト無し）。
    """
    try:
        import chainlit as cl  # noqa: PLC0415 (tests 等で chainlit 文脈が無い場合に備えて遅延import)

        user = cl.user_session.get(SESSION_USER_KEY)
    except Exception:  # noqa: BLE001 (ChainlitContextException を含むセッション文脈外の全般)
        return None
    return user or None


def is_allowed(owner: str, user: str | None, mode: str, op: DraftOp) -> bool:
    """owner のドラフトに対し、user が op を行ってよいか。"""
    if user is not None and owner == user:
        return True
    return op in _OTHER_USER_OPS.get(mode, frozenset())


def owner_of(path: Path, draft_dir: Path) -> tuple[bool, str | None]:
    """path が draft_dir 配下かと、その所有者（直下のフォルダ名）を返す。

    Returns:
        (draft_dir 配下か, 所有者)。draft_dir そのものなら (True, None)、
        配下でなければ (False, None)。
    """
    try:
        rel = path.resolve().relative_to(draft_dir.resolve())
    except (ValueError, OSError):
        return False, None
    return True, (rel.parts[0] if rel.parts else None)


def access_error(path: Path, op: DraftOp, config: "Config | None", user: str | None) -> str | None:
    """path が draft_dir 配下で、user に op が許されていなければエラー文字列を返す。

    draft_dir 直下（ユーザー名の一覧）は、他ユーザーのドラフトを読める
    モード（readable/full）のときだけ読める。draft_dir 配下以外は常に None。
    """
    draft_dir = getattr(config, "skill_draft_dir", None)
    if draft_dir is None:
        return None
    inside, owner = owner_of(path, draft_dir)
    if not inside:
        return None
    mode = config.skill_other_users_drafts
    if owner is None:
        if op == "read" and "read" in _OTHER_USER_OPS.get(mode, frozenset()):
            return None
        return "エラー: ドラフトスキル置き場の一覧は参照できません。"
    if is_allowed(owner, user, mode, op):
        return None
    return f"エラー: 他ユーザーのドラフトスキル（{owner}）は{_OP_LABELS[op]}できません。"


def unreadable_dirs(config: "Config | None", user: str | None) -> list[Path]:
    """user が読み取れない他ユーザーのドラフトフォルダの一覧を返す。

    Glob/Grep の再帰検索からの除外と、execute_python_code / run_script の
    書き込みガード（読み取りも禁止するルート）に使う。
    """
    draft_dir = getattr(config, "skill_draft_dir", None)
    if draft_dir is None or "read" in _OTHER_USER_OPS.get(config.skill_other_users_drafts, frozenset()):
        return []
    try:
        entries = list(draft_dir.iterdir())
    except OSError:
        return []
    return [e.resolve() for e in entries if e.is_dir() and e.name != user]


# 一覧に出さないドラフトの状態（_draft_meta.json の status）。昇格済みは正式スキルと
# 二重に見えてしまい、不採用はもう使わないため（ファイル自体は記録として残る）。
_HIDDEN_STATUSES = frozenset({"promoted", "rejected"})


def _draft_status(skill_dir: Path) -> str | None:
    """_draft_meta.json の status（読めなければ None）。"""
    try:
        return json.loads((skill_dir / DRAFT_META_FILENAME).read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError, AttributeError):
        return None


def _scan_owner_dir(owner_dir: Path) -> list[Skill]:
    """1ユーザー分のドラフトを走査する（_workspace 等 `_` 始まりは対象外）。"""
    skills: list[Skill] = []
    for entry in sorted(owner_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file() or _draft_status(entry) in _HIDDEN_STATUSES:
            continue
        try:
            fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm is None:
            logger.warning("ドラフト %s/%s: frontmatter を読めないためスキップ", owner_dir.name, entry.name)
            continue
        error = _validate(fm.get("name"), fm.get("description"), entry.name)
        if error:
            logger.warning("ドラフト %s/%s: 仕様違反のためスキップ（%s）", owner_dir.name, entry.name, error)
            continue
        skills.append(
            Skill(
                name=fm["name"],
                description=fm["description"].strip(),
                dir_path=entry,
                skill_md_path=skill_md,
                has_scripts=_skill_has_scripts(entry),
            )
        )
    return skills


def scan_visible_drafts(config: "Config | None", user: str | None) -> list[DraftSkill]:
    """user の会話に見えるドラフト（本人分＋モードに応じた他ユーザー分）を返す。

    user が None（セッション文脈外・evals）のときは常に空。
    並びは本人分が先、その後に他ユーザー分（所有者名・スキル名の昇順）。
    """
    draft_dir = getattr(config, "skill_draft_dir", None)
    if user is None or draft_dir is None or not draft_dir.is_dir():
        return []
    mode = config.skill_other_users_drafts
    result: list[DraftSkill] = []
    for owner_dir in sorted(draft_dir.iterdir(), key=lambda p: (p.name != user, p.name)):
        if not owner_dir.is_dir():
            continue
        owner = owner_dir.name
        if not is_allowed(owner, user, mode, "list"):
            continue
        for skill in _scan_owner_dir(owner_dir):
            result.append(
                DraftSkill(
                    skill=replace(skill, name=f"{owner}/{skill.name}"),
                    owner=owner,
                    own=owner == user,
                    readable=is_allowed(owner, user, mode, "read"),
                    executable=is_allowed(owner, user, mode, "exec"),
                )
            )
    return result


def render_draft_skills_block(drafts: list[DraftSkill]) -> str:
    """システムプロンプトのスキル一覧の末尾へ足すドラフトの箇条書きを返す（無ければ空文字列）。"""
    if not drafts:
        return ""
    lines = ["", "ドラフトスキル（未検証。正式スキルと同じ手順で使う）:"]
    for d in drafts:
        if d.own:
            note = "自分のドラフト"
        elif d.executable:
            note = f"{d.owner}のドラフト"
        elif d.readable:
            note = f"{d.owner}のドラフト・読み取りのみ"
        else:
            note = f"{d.owner}のドラフト・使用不可"
        lines.append(f"- {d.skill.name}: {d.skill.description}（{note}）")
    return "\n".join(lines)


def is_draft_path(path: Path, config: "Config | None") -> bool:
    """path が draft_dir 配下か。"""
    draft_dir = getattr(config, "skill_draft_dir", None)
    return draft_dir is not None and owner_of(path, draft_dir)[0]


def is_builtin_skill_creator(path: Path, config: "Config | None") -> bool:
    """path が skills_dir 配下の skill-creator 本体の中か。

    名前だけで判定すると .locohane/skills/skill-creator のような同名スキルにも
    ドラフト置き場への書き込みや承認免除が渡ってしまうため、実体の場所で判定する。
    """
    skills_dir = getattr(config, "skills_dir", None)
    if skills_dir is None:
        return False
    try:
        return path.resolve().is_relative_to((skills_dir / SKILL_CREATOR_NAME).resolve())
    except OSError:
        return False


def is_guard_exempt_script(path: Path, config: "Config | None") -> bool:
    """計画承認と [main_agent_tool_guard] を無条件で免除するスクリプトか。

    ドラフトのスクリプトと skill-creator 本体のスクリプトが対象。ドラフトの
    作成者はスキル開発の専門家とは限らず、config.ini の
    plan_approval_exempt_scripts / allow_entries を自分で登録できないため。
    書き込み先は通常どおり書き込みサンドボックスガードで制限される
    （skill-creator はドラフト置き場の自分のフォルダにも書ける）。
    """
    return is_draft_path(path, config) or is_builtin_skill_creator(path, config)
