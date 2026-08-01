"""src/context_compaction.py の _find_cut_index() の回帰テスト。

旧実装は HumanMessage の個数だけで切断点を決めていたが、2つの欠陥があった:

1. analyze_image の画像フォローアップやループガードの nudge はツール往復の
   途中に HumanMessage を挿入するため、HumanMessage の位置で切ると
   ToolMessage が対応する AIMessage を失う（OpenAI互換APIがエラーを返す）。
2. 1ターン内でLLM呼び出しを何十回も繰り返す長時間タスクでは、その間
   HumanMessage が1件も増えないため、圧縮の機会が一度も来なかった
   （実測: 1ターンで34回LLM呼び出しが発生した大量ファイル処理タスクで、
   compactionが一度も発火せずコンテキスト上限に張り付いた）。

さらに実装時に見つかった off-by-one: 「未処理のtool_callが無くなった位置」の
message自身のインデックスをそのままスライス境界に使うと、その位置の
ToolMessage自体がスライスから漏れる（境界は message index + 1 でなければ
ならない）。このテストはその境界計算も含めて検証する。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.context_compaction import _find_cut_index


def _tool_call(call_id: str) -> dict:
    return {"name": "x", "args": {}, "id": call_id}


def test_does_not_cut_between_tool_call_and_its_delayed_response() -> None:
    # ToolMessage(a) → HumanMessage(画像) → ToolMessage(b) という並びを含む。
    # b の応答が返るまでの間（インデックス2,3,4）は絶対に切断点にしてはならない。
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="", tool_calls=[_tool_call("a"), _tool_call("b")]),
        ToolMessage(content="ra", tool_call_id="a"),
        HumanMessage(content="image"),
        ToolMessage(content="rb", tool_call_id="b"),
        AIMessage(content="final1"),
        HumanMessage(content="q2"),
        AIMessage(content="final2"),
    ]

    cut_index = _find_cut_index(messages, keep_recent_turns=1)

    assert cut_index == 6
    assert cut_index not in (2, 3, 4)
    old_messages = messages[:cut_index]
    # 両方の tool_call が old_messages 内で自己完結していること
    # （AIMessage の tool_calls と対応する ToolMessage が両方含まれる）。
    old_tool_call_ids = {
        tc["id"] for m in old_messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    old_response_ids = {m.tool_call_id for m in old_messages if isinstance(m, ToolMessage)}
    assert old_tool_call_ids == old_response_ids


def test_cut_point_found_mid_turn_with_single_human_message() -> None:
    # ユーザー発言が1件しかない（1ターン継続中）が、その中で3回の完結した
    # ツール往復がある。keep_recent_turns（デフォルト2）を満たすユーザー
    # ターンが無いため、往復単位のフォールバックで切断点を見つけられること。
    messages = [
        HumanMessage(content="start"),
        AIMessage(content="", tool_calls=[_tool_call("a1")]),
        ToolMessage(content="r1", tool_call_id="a1"),
        AIMessage(content="", tool_calls=[_tool_call("a2")]),
        ToolMessage(content="r2", tool_call_id="a2"),
        AIMessage(content="", tool_calls=[_tool_call("a3")]),
        ToolMessage(content="r3", tool_call_id="a3"),
    ]

    cut_index = _find_cut_index(messages, keep_recent_turns=2)

    assert cut_index is not None
    old_messages = messages[:cut_index]
    kept_messages = messages[cut_index:]
    assert old_messages == messages[:3]  # 最初の1往復（a1）だけが要約対象
    # kept_messages 側の tool_calls も自己完結していること。
    kept_tool_call_ids = {
        tc["id"] for m in kept_messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    kept_response_ids = {m.tool_call_id for m in kept_messages if isinstance(m, ToolMessage)}
    assert kept_tool_call_ids == kept_response_ids


def test_returns_none_when_only_pending_tool_call_exists() -> None:
    # まだ応答が返っていない tool_call だけの会話は、安全に切り取れる
    # 完結した往復が無いため None を返す。
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="", tool_calls=[_tool_call("a")]),
    ]

    assert _find_cut_index(messages, keep_recent_turns=2) is None


def test_returns_none_when_not_enough_history_to_compact() -> None:
    messages = [HumanMessage(content="q1"), AIMessage(content="final")]

    assert _find_cut_index(messages, keep_recent_turns=2) is None
