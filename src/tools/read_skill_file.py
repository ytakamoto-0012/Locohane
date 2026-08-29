"""read_skill_file ツール（progressive disclosure 第3段階）。"""

from __future__ import annotations

from langchain_core.tools import tool
import logging

from ._duplicate_guard import _check_file_tools_duplicate
from ._safe_path import _missing_skill_prefix_hint, _safe_path

logger = logging.getLogger(__name__)


@tool
def read_skill_file(relative_path: str) -> str:
    """skills ディレクトリ配下のファイルを読み込んで返す。

    SKILL.md 本文が references/assets を参照している場合など、必要時のみ使う。
    Agent Skills 標準の progressive disclosure における第3段階（Execute）の一部。

    Args:
        relative_path: skills ルートからの相対パス。read_skill(skill_name) で
            そのスキルを読んだ後でも、先頭に必ずスキルフォルダ名を含めること
            （例: "references/notes.md" ではなく
            "excel-knowledge/references/notes.md"）。

    Returns:
        ファイル内容（UTF-8、デコード不能なバイト列は errors="replace" で置換）。
        skills ルート外を指す場合やファイルが存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    try:
        path = _safe_path(relative_path)
    except ValueError as e:
        return f"エラー: {e}"
    if not path.is_file():
        hint = _missing_skill_prefix_hint(relative_path)
        if hint:
            return f"エラー: ファイルが見つかりません: {relative_path}{hint}"
        return (
            f"エラー: ファイルが見つかりません: {relative_path}"
            "（read_skill_file は skills ディレクトリ配下限定です。作業ディレクトリ配下の"
            "ファイルを読みたい場合は Read ツールを使ってください）"
        )
    dup_error = _check_file_tools_duplicate("read_skill_file", f"read_skill_file\x00{path}")
    if dup_error:
        return dup_error
    logger.info("read_skill_file: %s", relative_path)
    return path.read_text(encoding="utf-8", errors="replace")
