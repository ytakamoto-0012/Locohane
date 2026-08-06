"""会話履歴中の古い ToolMessage / AIMessage を切り詰め、LLMへの入力を抑える。

長いReActループ（ファイル読み込み等のツール呼び出しを繰り返すタスク）では、
サイズ上限のないツール実行結果（例: OCR結果のMarkdown全文）が ToolMessage
としてそのまま会話履歴に蓄積し続け、llama.cppへのプロンプトプリフィルが
極端に遅くなる（本番ログで100秒以上の遅延を実測）。

src/graph.py の prebuilt 実装（pre_model_hook）・handwritten 実装
（call_model 直前）の両方から呼ばれる共通ロジック。ToolMessage は1件も
削除しない（LangGraph の create_react_agent が要求する「AIMessage.tool_calls
と対応する ToolMessage が揃っていること」という不変条件を壊さないため）。
content だけを短縮したコピーに差し替えることで、checkpointer 上の永続履歴
には手を付けず、今回のLLM呼び出しへの入力だけを縮める。
"""

from __future__ import annotations

import copy

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

_MARKER_TEMPLATE = (
    "\n...[truncated: {original_len} chars total, first {limit} chars shown. "
    "Full text preserved in conversation history. "
    "To read the rest, re-run the tool with different offset/limit parameters]"
)

# Read/Glob/Grep/json_query/analyze_image は src/tools.py の
# _check_file_tools_duplicate / 個別の重複ガードにより「同一引数での
# 再呼び出しは上限回数まで」しか許されない（結果が変わらない読み取り専用
# ツールのため）。ガードのエラー文言は「会話履歴にある前回の実行結果を
# 参照してください」と案内するが、その前回結果が一般的な max_chars で
# 切り詰められていると、実際にはモデルへ渡っていない分を参照させることに
# なり、案内が機能しない。そのためこれらのツール名の ToolMessage だけは
# 別枠の guarded_tool_max_chars を使えるようにする（ツール名は @tool の
# 明示指定と一致させること）。
_DUPLICATE_GUARD_TOOL_NAMES = frozenset({"Read", "Glob", "Grep", "json_query", "analyze_image"})


def trim_old_tool_messages(
    messages: list[BaseMessage],
    *,
    keep_recent: int,
    max_chars: int,
    guarded_tool_max_chars: int | None = None,
) -> list[BaseMessage]:
    """直近 keep_recent 件の ToolMessage は全文保持し、それより古いものは
    content を先頭 max_chars 文字に切り詰める。

    Args:
        messages: state["messages"]（元の全履歴。書き換えない）。
        keep_recent: 全文保持する直近 ToolMessage の件数。
        max_chars: 切り詰め後に残す本文の最大文字数（マーカー文言は含まない）。
            _DUPLICATE_GUARD_TOOL_NAMES に含まれないツールの ToolMessage に
            適用する。
        guarded_tool_max_chars: _DUPLICATE_GUARD_TOOL_NAMES に含まれる
            ツール（Read/Glob/Grep/json_query/analyze_image）の ToolMessage
            にだけ適用する切り詰め文字数。None の場合は max_chars を使う
            （従来どおりの挙動）。

    Returns:
        content だけ差し替えたコピーを含むメッセージ列。書き換え不要な
        メッセージは元のオブジェクトをそのまま含む。
    """
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    keep = set(tool_indices[-keep_recent:]) if keep_recent > 0 else set()

    result: list[BaseMessage] = []
    for i, m in enumerate(messages):
        if i in keep or not isinstance(m, ToolMessage) or not isinstance(m.content, str):
            result.append(m)
            continue
        limit = max_chars
        if guarded_tool_max_chars is not None and getattr(m, "name", None) in _DUPLICATE_GUARD_TOOL_NAMES:
            limit = guarded_tool_max_chars
        if len(m.content) <= limit:
            result.append(m)
            continue
        marker = _MARKER_TEMPLATE.format(original_len=len(m.content), limit=limit)
        result.append(m.model_copy(update={"content": m.content[:limit] + marker}))
    return result


def _truncate(text: str, max_chars: int) -> str | None:
    """max_chars を超える文字列を切り詰める。切り詰め不要なら None を返す。"""
    if len(text) <= max_chars:
        return None
    return text[:max_chars] + _MARKER_TEMPLATE.format(original_len=len(text), limit=max_chars)


def _trim_tool_call_args(tool_calls: list[dict], max_chars: int) -> list[dict] | None:
    """tool_calls の args に含まれる長い文字列値だけを切り詰める。

    `id`/`name`、および tool_calls の件数は一切変更しない。ToolMessage との
    対応は `tool_call_id` で取られるため、args の中身だけを縮めるぶんには
    「AIMessage.tool_calls と対応する ToolMessage が揃っていること」という
    LangGraph の不変条件を壊さない。

    Args:
        tool_calls: AIMessage.tool_calls（LangChain 正規化済みの dict のリスト）。
        max_chars: 切り詰め後に残す文字数。

    Returns:
        切り詰めが発生した場合のみ新しいリスト。1件も切り詰めなければ None。
    """
    changed = False
    new_calls: list[dict] = []
    for call in tool_calls:
        args = call.get("args")
        if not isinstance(args, dict):
            new_calls.append(call)
            continue
        new_args = None
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            truncated = _truncate(value, max_chars)
            if truncated is None:
                continue
            if new_args is None:
                new_args = copy.copy(args)
            new_args[key] = truncated
        if new_args is None:
            new_calls.append(call)
            continue
        new_call = copy.copy(call)
        new_call["args"] = new_args
        new_calls.append(new_call)
        changed = True
    return new_calls if changed else None


def trim_old_ai_messages(messages: list[BaseMessage], *, keep_recent: int, max_chars: int) -> list[BaseMessage]:
    """直近 keep_recent 件の AIMessage は全文保持し、それより古いものは
    content と tool_calls の引数を先頭 max_chars 文字に切り詰める。

    trim_old_tool_messages() は ToolMessage しか見ないため、モデル自身が
    `execute_python_code` の `code` 引数へファイル本文を書き写すような使い方を
    すると、ツール結果側だけを絞っても入力が膨らみ続ける（実測: 大量ファイル
    処理で1リクエストあたり24,833→128,000トークンまで単調増加し、コンテキスト
    上限に張り付いて処理が停止した）。その経路を塞ぐための関数。

    Args:
        messages: state["messages"]（元の全履歴。書き換えない）。
        keep_recent: 全文保持する直近 AIMessage の件数。
        max_chars: 切り詰め後に残す本文の最大文字数（マーカー文言は含まない）。

    Returns:
        content / tool_calls.args だけ差し替えたコピーを含むメッセージ列。
        書き換え不要なメッセージは元のオブジェクトをそのまま含む。
    """
    ai_indices = [i for i, m in enumerate(messages) if isinstance(m, AIMessage)]
    keep = set(ai_indices[-keep_recent:]) if keep_recent > 0 else set()

    result: list[BaseMessage] = []
    for i, m in enumerate(messages):
        if i in keep or not isinstance(m, AIMessage):
            result.append(m)
            continue
        update: dict = {}
        if isinstance(m.content, str):
            truncated = _truncate(m.content, max_chars)
            if truncated is not None:
                update["content"] = truncated
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            new_calls = _trim_tool_call_args(tool_calls, max_chars)
            if new_calls is not None:
                update["tool_calls"] = new_calls
        result.append(m.model_copy(update=update) if update else m)
    return result
