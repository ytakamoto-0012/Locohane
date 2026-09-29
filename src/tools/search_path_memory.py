"""search_path_memory ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl
import json

from .. import path_memory

from . import _state


@tool
def search_path_memory(query: str = "", top_k: int = 5) -> str:
    """現在の会話のパスメモリー（`@N`）から、query に似たパスを探す。

    ファイル名・フォルダ名の一部を渡せばよい（全角半角・大文字小文字・
    `\\` と `/` の違いや多少の誤字は吸収する）。`@N` が分からないとき、
    または「パスメモリー @N は登録されていません」と返されたときに使う。
    読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。

    Args:
        query: 探したいファイル名・フォルダ名の一部、またはパス。
            空なら直近に登録された top_k 件を返す。
        top_k: 返す最大件数（既定5）。

    Returns:
        `{"entries": [{"index", "path", "valid", "description", "score"}, ...]}`
        のJSON文字列（query ありは類似度順、空なら新しい順。空の場合 score は無い）。
        登録後に削除・移動されて現在存在しないパスは含まない。
        該当が無ければ `hint` を添える。
    """
    if _state._PATH_MEMORY_DIR is None:
        return json.dumps({"entries": []}, ensure_ascii=False)
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    if query.strip():
        entries = path_memory.search_entries(
            thread_id,
            query,
            _state._PATH_MEMORY_DIR,
            top_k=top_k,
            min_score=_state._PATH_MEMORY_SEARCH_MIN_SCORE,
            filename_weight=_state._PATH_MEMORY_SEARCH_FILENAME_WEIGHT,
        )
    else:
        entries = path_memory.recent_entries(thread_id, _state._PATH_MEMORY_DIR, top_k=top_k)
    result: dict = {"entries": entries}
    if not entries:
        result["hint"] = "該当する登録はありません。Glob で探し直してください。"
    return json.dumps(result, ensure_ascii=False)
