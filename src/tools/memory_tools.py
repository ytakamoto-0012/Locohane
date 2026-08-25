"""永続メモリー（create_memory 等）ツール群。"""

from __future__ import annotations

from langchain_core.tools import tool
from pathlib import Path
import logging

from .. import memory

from . import _state

logger = logging.getLogger(__name__)


def _require_memory_root() -> Path:
    """_state._MEMORY_ROOT を返す。init_tools() が未実行なら RuntimeError。"""
    if _state._MEMORY_ROOT is None:
        raise RuntimeError("init_tools() が未実行です")
    return _state._MEMORY_ROOT


@tool
def create_memory(name: str, description: str, memory_type: str, content: str) -> str:
    """新しい永続メモリーを保存する。

    スレッドをまたいで将来の会話へ引き継ぎたい価値ある事実を学んだときに使う。
    memory_type ごとの保存タイミング・保存してはいけないものはシステムプロンプトの
    Memory System セクションを参照すること。同名のメモリーが既にある場合はエラーに
    なるので、既存メモリーの更新には update_memory を使うこと（迷ったら先に
    search_memory / list_memories で重複が無いか確認する）。

    Args:
        name: 一意な名前（英数字・ハイフン・アンダースコアのみ、64文字以内）。
        description: 一行の説明文（索引 MEMORY.md にそのまま載る）。
        memory_type: "user" | "feedback" | "project" | "reference" のいずれか。
        content: メモリー本文。feedback/project タイプはルール/事実の後に
            「**Why:**」「**How to apply:**」の行を含めることが望ましい。

    Returns:
        保存したファイルパスを伝えるテキスト。name/memory_type が不正、
        description/content が空、同名のメモリーが既に存在する場合は、
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.create_memory(_require_memory_root(), name, description, memory_type, content)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("create_memory: %s", name)
    return f"メモリーを保存しました: {path}"


@tool
def update_memory(name: str, content: str) -> str:
    """既存の永続メモリーの本文を更新する（name/description/memory_typeは変わらない）。

    Args:
        name: 更新対象メモリーの名前。
        content: 新しい本文（既存の本文を丸ごと置き換える）。

    Returns:
        更新したファイルパスを伝えるテキスト。content が空、または name の
        メモリーが存在しない場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.update_memory(_require_memory_root(), name, content)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("update_memory: %s", name)
    return f"メモリーを更新しました: {path}"


@tool
def delete_memory(name: str) -> str:
    """永続メモリーを削除する。

    Args:
        name: 削除対象メモリーの名前。

    Returns:
        削除したファイルパスを伝えるテキスト。name のメモリーが存在しない場合は、
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.delete_memory(_require_memory_root(), name)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("delete_memory: %s", name)
    return f"メモリーを削除しました: {path}"


@tool
def read_memory(name: str) -> str:
    """永続メモリー1件を本文込みで全文読み込む。

    search_memory / list_memories は一覧（name+description）しか返さないため、
    メモリーの内容そのものを確認・引用する前には必ずこのツールで全文を読むこと。

    Args:
        name: 読み込むメモリーの名前。

    Returns:
        「[type] name\\ndescription\\n\\ncontent」形式の全文。name のメモリーが
        存在しない場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        mem = memory.read_memory(_require_memory_root(), name)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("read_memory: %s", name)
    return f"[{mem.memory_type}] {mem.name}\n{mem.description}\n\n{mem.content}"


@tool
def search_memory(query: str, memory_type: str | None = None) -> str:
    """永続メモリーを name/description/本文に対するキーワード部分一致で検索する。

    本文は返さず一覧（name+description）のみを返す（コンテキスト節約のため）。
    内容そのものが必要な場合は、ヒットした name を read_memory へ渡すこと。

    Args:
        query: 検索キーワード（大文字小文字は区別しない）。
        memory_type: 指定すれば "user"|"feedback"|"project"|"reference" の
            いずれかに絞り込む（省略時は全type対象）。

    Returns:
        ヒットしたメモリーの「- [type] name: description」一覧と件数。
        0件の場合は「一致するメモリーはありません」。query が空、または
        memory_type が不正な値の場合は、例外を送出せず「エラー: ...」形式の
        文字列を返す。
    """
    try:
        hits = memory.search_memories(_require_memory_root(), query, memory_type)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("search_memory: query=%r type=%s hits=%d", query, memory_type, len(hits))
    if not hits:
        return "一致するメモリーはありません。"
    lines = [f"- [{m.memory_type}] {m.name}: {m.description}" for m in hits]
    return f"{len(hits)}件ヒットしました。\n" + "\n".join(lines)


@tool
def list_memories(memory_type: str | None = None) -> str:
    """保存されている永続メモリーを一覧表示する。

    Args:
        memory_type: 指定すれば "user"|"feedback"|"project"|"reference" の
            いずれかに絞り込む（省略時は全type対象）。

    Returns:
        「- [type] name: description」一覧。0件の場合は
        「保存されているメモリーはありません」。memory_type が不正な値の
        場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        mems = memory.list_memories(_require_memory_root(), memory_type)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("list_memories: type=%s count=%d", memory_type, len(mems))
    if not mems:
        return "保存されているメモリーはありません。"
    return "\n".join(f"- [{m.memory_type}] {m.name}: {m.description}" for m in mems)
