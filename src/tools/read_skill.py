"""read_skill ツール（progressive disclosure 第2段階）。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from pydantic import Field, create_model
import logging

from ..skills import strip_frontmatter, wrap_skill_content
from ._duplicate_guard import _check_file_tools_duplicate
from ._path_memory_helpers import _register_path_memory
from ._safe_path import _safe_path

logger = logging.getLogger(__name__)

# references/ 一覧に載せる最大件数（超過分は件数だけ示す）。
_REFERENCES_LIST_MAX = 50

# apply_skill_name_enum() で制約した正式スキル名（session_read_skill() がドラフト名を足す土台）。
_BASE_SKILL_NAMES: tuple[str, ...] = ()


def _render_references_section(skill_name: str, skill_dir: Path) -> str:
    """references/ 配下のファイル一覧を `@N` 付きで組み立てる（中身は読まない）。

    `@N` はそのまま read_skill_file に渡せる。低パラメータモデルがスキル名
    プレフィックスを付け忘れる等、パスを手で組み立てて誤る事例への対策。

    Returns:
        一覧の文字列。references/ が無い・空の場合は空文字列。
    """
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return ""
    files = sorted(p for p in refs_dir.rglob("*") if p.is_file())
    if not files:
        return ""
    shown = files[:_REFERENCES_LIST_MAX]
    path_memory_map = _register_path_memory([str(p) for p in shown], description=f"{skill_name} references")
    token_by_path = {path: token for token, path in path_memory_map.items()}
    lines = ["references/ のファイル（read_skill_file に @N をそのまま渡せる）:"]
    for p in shown:
        rel = f"{skill_name}/{p.relative_to(skill_dir).as_posix()}"
        token = token_by_path.get(str(p))
        lines.append(f"{token} {rel}" if token else rel)
    if len(files) > len(shown):
        lines.append(f"（他 {len(files) - len(shown)} 件）")
    return "\n".join(lines)


@tool
def read_skill(skill_name: str) -> str:
    """スキルの SKILL.md 本文全体を読み込んで返す。

    ユーザーの要求に合致するスキルを選んだら、まずこのツールで本文（手順）を読むこと。
    Agent Skills 標準の progressive disclosure における第2段階（Read）に相当する。
    references/ があれば末尾にファイル一覧（@N 付き）が付く。

    Args:
        skill_name: 読み込むスキルのフォルダ名（= SKILL.md の name）。

    Returns:
        `<skill_content name="...">` で囲んだ SKILL.md の本文（frontmatter は
        除く）と references/ のファイル一覧。skill_name が skills ルート外を
        指す場合や SKILL.md が存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す（LLM がそのまま読める形にするため）。
    """
    try:
        skill_md = _safe_path(f"{skill_name}/SKILL.md")
    except ValueError as e:
        return f"エラー: {e}"
    if not skill_md.is_file():
        return f"エラー: スキル '{skill_name}' の SKILL.md が見つかりません。"
    dup_error = _check_file_tools_duplicate("read_skill", f"read_skill\x00{skill_name}")
    if dup_error:
        return dup_error
    logger.info("read_skill: %s", skill_name)
    body = strip_frontmatter(skill_md.read_text(encoding="utf-8"))
    references = _render_references_section(skill_name, skill_md.parent)
    if references:
        body = f"{body}\n\n{references}"
    return wrap_skill_content(skill_name, body)


def apply_skill_name_enum(skill_names: Sequence[str]) -> None:
    """read_skill の skill_name 引数を、実在するスキル名の選択肢（enum）に制約する。

    `@tool` が型ヒントから作る str の引数スキーマを、Literal 型の引数を持つ
    スキーマへ差し替える。これで LLM へ送る tools パラメータに
    `"enum": [...]` が載り、存在しないスキル名の生成を防ぐ
    （pydantic の json_schema_extra で enum を足す方式は、langchain が
    tool_call_schema を作る際に落としてしまうため Literal を使う）。
    一覧に無い名前で呼ばれた場合は pydantic の検証エラーになり、
    ToolNode / サブエージェントのツール実行がエラー文字列として LLM へ返す。

    Args:
        skill_names: scan_skills() が返した有効なスキル名の並び。空の場合は
            何もしない（str のまま）。
    """
    global _BASE_SKILL_NAMES
    names = tuple(dict.fromkeys(skill_names))
    if not names:
        return
    _BASE_SKILL_NAMES = names
    read_skill.args_schema = _skill_name_schema(names)


def _skill_name_schema(names: tuple[str, ...]):
    """skill_name を names の選択肢（Literal）に制約した引数スキーマを作る。"""
    return create_model(
        "read_skill",
        skill_name=(Literal[names], Field(description="読み込むスキルのフォルダ名（= SKILL.md の name）。")),
    )


def session_read_skill(extra_names: Sequence[str]):
    """正式スキル名に extra_names（ドラフト名）を足した選択肢を持つ read_skill の複製を返す。

    ユーザー別ドラフト（src/skill_drafts.py）はセッションごとに見える範囲が違うため、
    モジュール共通の read_skill（全セッション共有）の args_schema は書き換えず、
    セッション用の複製だけに選択肢を足す（src/tools/session_tools.py から使う）。
    apply_skill_name_enum() が未実行（正式スキル名の制約が無い）なら制約を足さず、
    read_skill をそのまま返す。
    """
    if not _BASE_SKILL_NAMES or not extra_names:
        return read_skill
    names = tuple(dict.fromkeys((*_BASE_SKILL_NAMES, *extra_names)))
    return read_skill.model_copy(update={"args_schema": _skill_name_schema(names)})
