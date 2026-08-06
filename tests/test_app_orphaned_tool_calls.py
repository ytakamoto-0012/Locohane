"""app.py の _find_orphaned_tool_calls() の回帰テスト。

孤立tool_call（AIMessage.tool_calls はあるが対応する ToolMessage が無い
状態）は、以前は履歴の末尾のAIMessageしか見ておらず、孤立発生後に
loop_nudge等の後続メッセージが追記される・コンテキスト圧縮で圧縮後の
保持ウィンドウの途中に残る、といった経路で検出漏れになっていた
（issue/20260804_234928_orphaned_tool_call_dual_session_freeze.md の再発）。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import _find_orphaned_tool_calls


def _ai_with_tool_call(tool_call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "dispatch_agent", "args": {}, "id": tool_call_id, "type": "tool_call"}],
    )


def test_orphaned_tool_call_at_tail_is_detected() -> None:
    messages = [HumanMessage(content="こんにちは"), _ai_with_tool_call("tc-1")]

    orphaned = _find_orphaned_tool_calls(messages)

    assert [tc["id"] for tc in orphaned] == ["tc-1"]


def test_orphaned_tool_call_not_at_tail_is_detected() -> None:
    # 孤立tool_call(tc-1)の後に、無関係なやり取りが続いているケース
    # （loop_nudge注入やコンテキスト圧縮後の保持ウィンドウで起こりうる）。
    messages = [
        HumanMessage(content="こんにちは"),
        _ai_with_tool_call("tc-1"),
        HumanMessage(content="続けて"),
        AIMessage(content="", tool_calls=[{"name": "read_skill", "args": {}, "id": "tc-2", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="tc-2"),
    ]

    orphaned = _find_orphaned_tool_calls(messages)

    assert [tc["id"] for tc in orphaned] == ["tc-1"]


def test_all_tool_calls_answered_returns_empty() -> None:
    messages = [
        HumanMessage(content="こんにちは"),
        _ai_with_tool_call("tc-1"),
        ToolMessage(content="ok", tool_call_id="tc-1"),
        AIMessage(content="完了しました"),
    ]

    assert _find_orphaned_tool_calls(messages) == []


def test_empty_messages_returns_empty() -> None:
    assert _find_orphaned_tool_calls([]) == []
