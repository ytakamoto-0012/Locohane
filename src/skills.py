"""スキル発見ミドルウェア（自作部分の中核）。

Agent Skills 標準の progressive disclosure「第1段階 Discovery」を実装する。
仕様: https://agentskills.io/specification

やること（それだけ）:
1. 起動時に skills_dir 配下の各サブフォルダを走査する。
2. 各 SKILL.md の YAML frontmatter（name, description）だけをパース・検証する。
3. 仕様に準拠しない SKILL.md はスキップし警告ログを出す（全体を落とさない）。
4. name + description の一覧をシステムプロンプトへ注入する文字列を組み立てる。

本文（第2段階）や scripts/references/assets（第3段階）はここでは読まない。
それらは tools.py のツール（read_skill / read_skill_file / run_script）が担当する。

賢い仕掛け（動的import・ホットリロード）は入れない。走査して読む、それだけ。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 仕様の name 検証ルール:
#   1〜64文字 / 小文字英数字・ハイフン・アンダースコアのみ / 先頭末尾の区切り文字不可 / 区切り文字の連続不可
# （下記正規表現で「先頭末尾不可」を担保。区切り文字の連続は別途チェック。）
_NAME_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*$")
_NAME_MAX = 64
_DESC_MAX = 1024


@dataclass(frozen=True)
class Skill:
    """発見された 1 つのスキル（frontmatter のメタデータのみ保持）。

    scan_skills() の走査結果として生成される。本文（SKILL.md の
    Markdown 部分）はここでは保持せず、必要時に read_skill ツールが
    skill_md_path から読み直す。

    Attributes:
        name: SKILL.md frontmatter の name（親ディレクトリ名と一致必須）。
        description: SKILL.md frontmatter の description。
        dir_path: スキルのディレクトリパス（skills/<name>/）。
        skill_md_path: SKILL.md 自体のパス（skills/<name>/SKILL.md）。
    """

    name: str
    description: str
    dir_path: Path       # skills/<name>/
    skill_md_path: Path  # skills/<name>/SKILL.md


def _parse_frontmatter(text: str) -> dict | None:
    """SKILL.md 先頭の `---` で囲まれた YAML frontmatter を dict で返す。

    frontmatter が無い / 壊れている場合は None を返す。

    Args:
        text: SKILL.md ファイルの全文（UTF-8 でデコード済み）。

    Returns:
        frontmatter を YAML パースした dict。以下の場合は None を返す:
        - 先頭が "---" で始まっていない（frontmatter が無い）
        - "---" が2回以上出現しない（frontmatter が閉じられていない）
        - YAML の構文が不正
        - パース結果が dict でない（例: リストやスカラー値）
    """
    # 仕様上 SKILL.md は「YAML frontmatter + Markdown 本文」。先頭が --- で始まる。
    if not text.lstrip().startswith("---"):
        return None
    # 先頭の --- 以降、次の --- までを切り出す。
    stripped = text.lstrip()
    parts = stripped.split("---", 2)
    # parts = ["", "<yaml>", "<body>"] を期待する。
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        logger.warning("frontmatter の YAML パースに失敗: %s", e)
        return None
    return data if isinstance(data, dict) else None


def _validate(name: object, description: object, dir_name: str) -> str | None:
    """name / description を仕様に照らして検証する。

    Agent Skills 仕様の name 検証ルール（1〜64文字、小文字英数字と
    ハイフン・アンダースコアのみ、先頭末尾の区切り文字不可、区切り文字の
    連続不可、親ディレクトリ名と一致必須）と、description の必須・
    文字数上限（1024文字）を検証する。

    Args:
        name: frontmatter から取得した name の生値（型不正を検出するため object）。
        description: frontmatter から取得した description の生値。
        dir_name: このスキルの親ディレクトリ名。name と一致するか検証する。

    Returns:
        検証に失敗した場合はその理由を説明する文字列。
        すべての検証を通過した場合は None。
    """
    if not isinstance(name, str) or not name:
        return "name が無い、または文字列でない"
    if len(name) > _NAME_MAX:
        return f"name が {_NAME_MAX} 文字を超えている"
    if "--" in name or "__" in name or "-_" in name or "_-" in name:
        return "name に区切り文字 (- や _) の連続が含まれる"
    if not _NAME_RE.match(name):
        return "name は小文字英数字・ハイフン・アンダースコアのみ・先頭末尾は区切り文字不可"
    if name != dir_name:
        return f"name '{name}' が親ディレクトリ名 '{dir_name}' と一致しない"
    if not isinstance(description, str) or not description.strip():
        return "description が無い、または空"
    if len(description) > _DESC_MAX:
        return f"description が {_DESC_MAX} 文字を超えている"
    return None


def _scan_one(root: Path) -> list[Skill]:
    """1つのディレクトリ直下を走査し、有効な Skill の一覧を返す（内部ヘルパー）。"""
    skills: list[Skill] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            logger.warning("SKILL.md が無いためスキップ: %s", entry.name)
            continue

        text = skill_md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm is None:
            logger.warning("frontmatter を読めないためスキップ: %s", entry.name)
            continue

        name = fm.get("name")
        description = fm.get("description")
        error = _validate(name, description, entry.name)
        if error:
            logger.warning("仕様違反のためスキップ (%s): %s", entry.name, error)
            continue

        skills.append(
            Skill(
                name=name,
                description=description.strip(),
                dir_path=entry,
                skill_md_path=skill_md,
            )
        )
        logger.info("スキル発見: %s", name)

    return skills


def scan_skills(skills_dirs: Path | str | Sequence[Path | str]) -> list[Skill]:
    """skills_dirs 配下を走査し、有効な Skill の一覧を返す。

    Agent Skills 標準の progressive disclosure「第1段階 Discovery」の実装。
    各ディレクトリ直下の各サブディレクトリについて SKILL.md の存在確認・
    frontmatter パース・仕様検証を行う。個々のスキルが仕様に準拠しない
    場合でも例外は送出せず、そのスキルのみスキップして走査を継続する
    （1つの不正なスキルが全体の起動を妨げないようにするため）。

    複数ディレクトリを渡した場合は渡した順に走査し、name をキーにマージする。
    同名スキルが複数ディレクトリに存在する場合は、後から走査したディレクトリの
    定義で上書きする（例: [skills_dir, *locohane_skills_dirs] の順で渡すと
    .locohane 側が優先される）。

    Args:
        skills_dirs: スキル群を格納するディレクトリのパス、またはその並び。

    Returns:
        検証を通過した Skill のリスト（ディレクトリ名の昇順）。
        いずれのディレクトリも存在しない場合や、有効なスキルが1つも
        見つからない場合は空リストを返す。
    """
    if isinstance(skills_dirs, (str, Path)):
        skills_dirs = [skills_dirs]

    merged: dict[str, Skill] = {}
    for skills_dir in skills_dirs:
        root = Path(skills_dir)
        if not root.is_dir():
            logger.debug("skills ディレクトリが存在しない（任意）: %s", root)
            continue
        for skill in _scan_one(root):
            if skill.name in merged:
                logger.info("スキル '%s' を %s の定義で上書き", skill.name, root)
            merged[skill.name] = skill

    return sorted(merged.values(), key=lambda s: s.name)


def render_skills_block(skills: list[Skill]) -> str:
    """スキル一覧を `{{skills}}` プレースホルダー用の箇条書きテキストに整形する。

    build_system_prompt() のほか、agent_types.py（サブエージェント種別の
    システムプロンプトにも同じ `{{skills}}` プレースホルダーがある）からも
    共有で使う。

    Args:
        skills: scan_skills() が返した有効な Skill のリスト。

    Returns:
        "- name: description" 形式の箇条書き。skills が空リストの場合は
        「利用可能なスキルはありません」という旨の文言を返す。
    """
    if skills:
        return "\n".join(f"- {s.name}: {s.description}" for s in skills)
    return "（利用可能なスキルはありません）"


def build_system_prompt(skills: list[Skill], template_path: Path | str) -> str:
    """テンプレートファイルにスキル一覧を差し込んでシステムプロンプトを組み立てる。

    固定文言（役割説明・progressive disclosure の使い方指示）は
    template_path のファイルに保守しやすい形で分離されている。
    このファイル内の `{{skills}}` プレースホルダーを、発見したスキルの
    name+description 一覧（第1段階 Discovery）で置換する。

    Args:
        skills: scan_skills() が返した有効な Skill のリスト。
            空リストの場合は「利用可能なスキルはありません」という
            旨の文言が差し込まれる。
        template_path: `{{skills}}` プレースホルダーを含むシステムプロンプトの
            テンプレートファイルのパス。

    Returns:
        LLM に渡すシステムプロンプト全文（複数行の文字列）。

    Raises:
        FileNotFoundError: template_path が存在しない場合。
    """
    template = Path(template_path).read_text(encoding="utf-8")
    return template.replace("{{skills}}", render_skills_block(skills))
