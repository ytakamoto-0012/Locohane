"""会話履歴中の古い ToolMessage を切り詰め、LLMへの入力を抑える。

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

from langchain_core.messages import BaseMessage, ToolMessage

_MARKER_TEMPLATE = (
    "\n...(切り詰め: 元は{original_len}文字中、先頭{limit}文字のみ表示していま"
    "す。全文はこの会話の履歴に保存されていますが、入力容量の都合でモデルへは"
    "渡されていません。詳細が必要な場合は、同じ引数での再実行ではなく、別の"
    "範囲指定（例: Readツールのoffset/limit）で読み直してください)"
)


def trim_old_tool_messages(
    messages: list[BaseMessage], *, keep_recent: int, max_chars: int
) -> list[BaseMessage]:
    """直近 keep_recent 件の ToolMessage は全文保持し、それより古いものは
    content を先頭 max_chars 文字に切り詰める。

    Args:
        messages: state["messages"]（元の全履歴。書き換えない）。
        keep_recent: 全文保持する直近 ToolMessage の件数。
        max_chars: 切り詰め後に残す本文の最大文字数（マーカー文言は含まない）。

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
        if len(m.content) <= max_chars:
            result.append(m)
            continue
        marker = _MARKER_TEMPLATE.format(original_len=len(m.content), limit=max_chars)
        result.append(m.model_copy(update={"content": m.content[:max_chars] + marker}))
    return result
