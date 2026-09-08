"""app.py の _find_orphaned_tool_calls() の回帰テスト。

孤立tool_call（AIMessage.tool_calls はあるが対応する ToolMessage が無い
状態）は、以前は履歴の末尾のAIMessageしか見ておらず、孤立発生後に
loop_nudge等の後続メッセージが追記される・コンテキスト圧縮で圧縮後の
保持ウィンドウの途中に残る、といった経路で検出漏れになっていた
（issue/20260804_234928_orphaned_tool_call_dual_session_freeze.md の再発）。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import _build_orphaned_placeholder_message, _find_orphaned_tool_calls


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


def test_build_orphaned_placeholder_message_for_dispatch_agent_includes_rescue_hint() -> None:
    """dispatch_agentの孤立tool_callには、write_scratch_noteによる緊急退避内容を
    次回どう扱うべきかの案内（explore委譲→write_thread_note→ユーザー指示に従う）が
    追記されること（サブエージェント強制停止時の会話履歴退避機能の一部）。
    """
    tc = {"name": "dispatch_agent", "args": {}, "id": "tc-1", "type": "tool_call"}

    message = _build_orphaned_placeholder_message(tc, "ユーザーの停止操作等により、")

    assert message.tool_call_id == "tc-1"
    assert message.name == "dispatch_agent"
    assert "ユーザーの停止操作等により、このツール呼び出しの実行が中断されました。" in message.content
    assert "write_scratch_note" in message.content
    assert "explore" in message.content
    assert "write_thread_note" in message.content


def test_build_orphaned_placeholder_message_for_other_tool_has_no_rescue_hint() -> None:
    tc = {"name": "read_skill", "args": {}, "id": "tc-2", "type": "tool_call"}

    message = _build_orphaned_placeholder_message(tc, "直前のセッション異常により、")

    assert message.tool_call_id == "tc-2"
    assert "直前のセッション異常により、このツール呼び出しの実行が中断されました。" in message.content
    assert "write_scratch_note" not in message.content
