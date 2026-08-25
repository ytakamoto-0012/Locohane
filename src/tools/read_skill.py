"""read_skill ツール（progressive disclosure 第2段階）。"""

from __future__ import annotations

from langchain_core.tools import tool
import logging

from ._duplicate_guard import _check_file_tools_duplicate
from ._safe_path import _safe_path

logger = logging.getLogger(__name__)


@tool
def read_skill(skill_name: str) -> str:
    """スキルの SKILL.md 本文全体を読み込んで返す。

    ユーザーの要求に合致するスキルを選んだら、まずこのツールで本文（手順）を読むこと。
    Agent Skills 標準の progressive disclosure における第2段階（Read）に相当する。

    Args:
        skill_name: 読み込むスキルのフォルダ名（= SKILL.md の name）。

    Returns:
        SKILL.md の本文全体（UTF-8 テキスト）。skill_name が skills ルート外を
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
    return skill_md.read_text(encoding="utf-8")
