"""list_path_memory ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl
import json

from .. import path_memory

from . import _state


@tool
def list_path_memory() -> str:
    """現在の会話のパスメモリー（`@N`）登録内容を一覧表示する。

    `@N` が何のファイルを指していたか思い出せなくなったとき、または
    Read/Glob/Grep/analyze_image が「パスメモリー @N は登録されていません」と
    返したときに使う。読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。

    Returns:
        `{"entries": [{"index", "path", "valid", "description"}, ...]}` の
        JSON文字列（登録順）。`valid` が false の場合、登録時点では存在したが
        その後削除・移動された可能性がある。path_memory機能が利用できない
        環境では `{"entries": []}` を返す。
    """
    if _state._PATH_MEMORY_DIR is None:
        return json.dumps({"entries": []}, ensure_ascii=False)
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    entries = path_memory.list_entries(thread_id, _state._PATH_MEMORY_DIR)
    return json.dumps({"entries": entries}, ensure_ascii=False)
