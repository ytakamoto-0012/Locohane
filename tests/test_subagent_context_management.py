"""サブエージェントにも [context_trim]/[context_compaction] を適用する変更の回帰テスト。

Claude Codeがメイン会話・サブエージェントでコンテキスト管理機能の有無を
区別しない方式に倣い、src/subagent.py の run_subagent にも同じロジックを
適用した（要望: 「context_trimとcontext_compactionをサブエージェントにも」）。
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src import subagent
from src.subagent import _build_llm_input


class _FakeConfig:
    thinking_loop_guard_max_retries = 0
    subagent_empty_response_max_retries = 0
    subagent_token_guard_enabled = False
    track_token_usage = False
    context_trim_enabled = True
    context_trim_keep_recent_tool_messages = 1
    context_trim_truncated_max_chars = 20
    context_trim_duplicate_guard_tool_max_chars = 20
    context_trim_ai_messages = False
    context_trim_keep_recent_ai_messages = 1
    context_compaction_enabled = False


def test_build_llm_input_trims_old_tool_messages_without_mutating_original() -> None:
    """context_trim_enabled=True なら、古い ToolMessage を切り詰めたコピーを返し、
    呼び出し元の messages 本体（run_subagent の永続履歴）は書き換えない。
    """
    long_content = "x" * 1000
    messages = [
        SystemMessage(content="sp"),
        HumanMessage(content="task"),
        ToolMessage(content=long_content, name="Read", tool_call_id="c0"),
        AIMessage(content="解釈"),
        ToolMessage(content=long_content, name="Read", tool_call_id="c1"),
    ]
    config = _FakeConfig()

    llm_input = _build_llm_input(messages, config)

    # 直近1件（c1）は全文保持、古い方（c0）は切り詰められる。
    assert llm_input[2].content != long_content
    assert len(llm_input[2].content) < 1000
    assert llm_input[4].content == long_content
    # 元の messages は書き換えられていない（永続履歴を守る context_trim の方針）。
    assert messages[2].content == long_content


def test_build_llm_input_noop_when_disabled() -> None:
    config = _FakeConfig()
    config.context_trim_enabled = False
    messages = [SystemMessage(content="sp"), HumanMessage(content="task")]

    assert _build_llm_input(messages, config) is messages


class _ToolCallThenFinalModel:
    """1回目は tool_calls を含む応答、2回目は最終回答を返す固定シナリオ。"""

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[{"name": "dummy_tool", "args": {}, "id": "call-1"}])
        return AIMessage(content="完了しました")


@tool
def dummy_tool() -> str:
    """テスト用の何もしないツール。"""
    return "ok"


@pytest.mark.asyncio
async def test_compaction_excludes_leading_system_message(monkeypatch) -> None:
    """圧縮対象から SystemMessage（messages[0]）を除外して maybe_compact に渡すことの回帰テスト。

    graph.py のメインエージェントは system_prompt を state["messages"] に含めない
    構造だが、run_subagent の messages はローカルリストの先頭に SystemMessage を
    積む構造が異なる。除外せずに圧縮対象へ渡すと、要約後にサブエージェントが
    システムプロンプトを失う（本テストが検知したい退行）。
    """
    config = _FakeConfig()
    config.context_trim_enabled = False
    config.context_compaction_enabled = True
    config.track_token_usage = True

    fake_model = _ToolCallThenFinalModel()
    monkeypatch.setattr(subagent, "build_model", lambda config, role: fake_model)

    # should_compact は初回のツール実行直後にだけ True を返す（無限圧縮ループ回避）。
    call_state = {"should_compact_calls": 0}

    def fake_should_compact(cumulative_usage, last_usage, message_count, config):
        call_state["should_compact_calls"] += 1
        return call_state["should_compact_calls"] == 1

    captured_maybe_compact_args = {}

    async def fake_maybe_compact(messages, model, config):
        captured_maybe_compact_args["messages"] = list(messages)
        return [HumanMessage(content="[要約]圧縮済み")]

    monkeypatch.setattr(subagent, "should_compact", fake_should_compact)
    monkeypatch.setattr(subagent, "maybe_compact", fake_maybe_compact)

    result = await subagent.run_subagent(
        task="t",
        tools=[dummy_tool],
        system_prompt="サブエージェント専用システムプロンプト",
        config=config,
        max_iterations=5,
    )

    assert result == "完了しました"
    assert "messages" in captured_maybe_compact_args
    passed_messages = captured_maybe_compact_args["messages"]
    # SystemMessage が圧縮対象（=要約に飲み込まれて消える可能性のある側）に
    # 含まれていないこと。
    assert not any(isinstance(m, SystemMessage) for m in passed_messages)
