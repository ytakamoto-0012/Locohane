"""read_skill_file ツール（progressive disclosure 第3段階）。"""

from __future__ import annotations

from langchain_core.tools import tool
import logging

from pathlib import Path

from .. import skill_drafts
from . import _state
from ._duplicate_guard import _check_file_tools_duplicate
from ._path_memory_helpers import _PATH_MEMORY_TOKEN_RE, _resolve_path_memory_token
from ._safe_path import _missing_skill_prefix_hint, _safe_path

logger = logging.getLogger(__name__)

_WORKDIR_FILE_HINT = (
    "（read_skill_file は skills ディレクトリ配下限定です。作業ディレクトリ配下の"
    "ファイルは Read ツールで読んでください。Read が使えなければ dispatch_agent で委譲してください）"
)


def _resolve_skill_file_path(relative_path: str) -> Path:
    """relative_path（skills ルートからの相対パス、または `@N`）を絶対パスへ解決する。

    `@N` は read_skill が references/ 一覧に付けたパスメモリー参照を想定する。
    解決先が skills ルート配下に収まらない `@N`（作業ディレクトリ側のファイル等）は
    _safe_path() と同じく境界外として拒否する。

    Raises:
        ValueError: `@N` が未登録、または解決先が skills ルート外の場合。
    """
    if not _PATH_MEMORY_TOKEN_RE.match(relative_path):
        return _safe_path(relative_path)
    resolved, error = _resolve_path_memory_token(relative_path)
    if error:
        raise ValueError(error)
    path = Path(resolved).resolve()
    if not any(path.is_relative_to(root) for root in _state._SKILLS_ROOTS or []):
        raise ValueError(f"{relative_path} は skills ディレクトリ外のファイルです")
    draft_error = skill_drafts.access_error(path, "read", _state._LLM_CONFIG, skill_drafts.current_draft_user())
    if draft_error:
        raise ValueError(draft_error.removeprefix("エラー: "))
    return path


@tool
def read_skill_file(relative_path: str) -> str:
    """skills ディレクトリ配下のファイルを読み込んで返す。

    SKILL.md 本文が references/assets を参照している場合など、必要時のみ使う。
    Agent Skills 標準の progressive disclosure における第3段階（Execute）の一部。

    Args:
        relative_path: skills ルートからの相対パス、または read_skill の
            references/ 一覧にある `@N`。相対パスの場合、read_skill(skill_name) で
            そのスキルを読んだ後でも、先頭に必ずスキルフォルダ名を含めること
            （例: "references/notes.md" ではなく
            "excel-knowledge/references/notes.md"）。

    Returns:
        ファイル内容（UTF-8、デコード不能なバイト列は errors="replace" で置換）。
        skills ルート外を指す場合やファイルが存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    try:
        path = _resolve_skill_file_path(relative_path)
    except ValueError as e:
        return f"エラー: {e}{_WORKDIR_FILE_HINT}"
    if not path.is_file():
        # スキル名プレフィックス漏れか、作業ディレクトリのファイルを渡したのかは
        # 区別できないため、両方の代替行動を示す（メインエージェントが作業
        # ディレクトリのファイル名をそのまま渡す誤りが実際にあった）。
        hint = _missing_skill_prefix_hint(relative_path)
        return f"エラー: ファイルが見つかりません: {relative_path}{hint}{_WORKDIR_FILE_HINT}"
    dup_error = _check_file_tools_duplicate("read_skill_file", f"read_skill_file\x00{path}")
    if dup_error:
        return dup_error
    logger.info("read_skill_file: %s", relative_path)
    return path.read_text(encoding="utf-8", errors="replace")
