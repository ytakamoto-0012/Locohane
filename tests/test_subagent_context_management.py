"""サブエージェントにも [context_trim]/[context_compaction] を適用する変更の回帰テスト。

Claude Codeがメイン会話・サブエージェントでコンテキスト管理機能の有無を
区別しない方式に倣い、src/subagent.py の run_subagent にも同じロジックを
適用した（要望: 「context_trimとcontext_compactionをサブエージェントにも」）。
"""

import pytest
from dataclasses import dataclass
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src import subagent
from src.context_compaction import _PRE_NOTE_MARKER
from src.subagent import _build_llm_input


@dataclass
class _FakeConfig:
    """_build_llm_input/run_subagent が実際に参照するのは context_trim_subagent_*/
    context_compaction_subagent_* 側（[context_trim.subagent]/
    [context_compaction.subagent] 導入後の src/subagent.py の実装。config.py
    docstring参照）。run_subagent 内の _subagent_compaction_config が
    dataclasses.replace() を使うため、このフェイクも dataclass にする必要があり
    （通常クラスだと "replace() should be called on dataclass instances" で
    落ちる）、かつ replace() の changes には main側 context_compaction_* の
    フィールド名がそのまま使われるため main側フィールドも定義しておく必要がある
    （_subagent_compaction_config は「context_compaction_* を subagent_* の
    値で置き換えたビュー」を作る実装のため）。
    """

    thinking_loop_guard_max_retries: int = 0
    subagent_empty_response_max_retries: int = 0
    subagent_token_guard_enabled: bool = False
    track_token_usage: bool = False
    context_trim_subagent_enabled: bool = True
    context_trim_subagent_keep_recent_tool_messages: int = 1
    context_trim_subagent_truncated_max_chars: int = 20
    context_trim_subagent_duplicate_guard_tool_max_chars: int = 20
    context_trim_subagent_ai_messages: bool = False
    context_trim_subagent_keep_recent_ai_messages: int = 1
    context_trim_subagent_trigger_total_tokens: int = 0
    context_compaction_enabled: bool = False
    context_compaction_token_threshold: int = 0
    context_compaction_single_request_token_threshold: int = 0
    context_compaction_keep_recent_turns: int = 0
    context_compaction_min_messages_to_compact: int = 0
    context_compaction_prompt_path: str | None = None
    context_compaction_summary_source_max_chars: int = 0
    context_compaction_pre_note_threshold: int = 0
    context_compaction_pre_note_warning_text: str = ""
    context_compaction_subagent_enabled: bool = False
    context_compaction_subagent_token_threshold: int = 0
    context_compaction_subagent_single_request_token_threshold: int = 0
    context_compaction_subagent_keep_recent_turns: int = 0
    context_compaction_subagent_min_messages_to_compact: int = 0
    context_compaction_subagent_prompt_path: str | None = None
    context_compaction_subagent_summary_source_max_chars: int = 0
    context_compaction_subagent_pre_note_threshold: int = 0
    context_compaction_subagent_pre_note_warning_text: str = ""
    subagent_token_guard_soft_threshold: int = 999999999
    subagent_token_guard_hard_threshold: int = 999999999
    subagent_token_guard_soft_warning_text: str = ""


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
    config.context_trim_subagent_enabled = False
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
    config.context_trim_subagent_enabled = False
    config.context_compaction_subagent_enabled = True
    config.track_token_usage = True

    fake_model = _ToolCallThenFinalModel()

    async def fake_build_model(config, role):
        return fake_model

    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    # should_compact は初回のツール実行直後にだけ True を返す（無限圧縮ループ回避）。
    call_state = {"should_compact_calls": 0}

    def fake_should_compact(cumulative_usage, last_usage, message_count, config):
        call_state["should_compact_calls"] += 1
        return call_state["should_compact_calls"] == 1

    captured_maybe_compact_args = {}

    async def fake_maybe_compact(messages, model, config, *, role="sub"):
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


class _ToolCallWithUsageThenFinalModel:
    """1回目はusage_metadata付きtool_calls応答を返し、2回目に渡された入力を
    記録した上で最終回答を返す固定シナリオ。"""

    def __init__(self, total_tokens: int) -> None:
        self.calls = 0
        self.total_tokens = total_tokens
        self.captured_second_input: list | None = None

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            msg = AIMessage(content="", tool_calls=[{"name": "dummy_tool", "args": {}, "id": "call-1"}])
            msg.usage_metadata = {
                "input_tokens": self.total_tokens - 10,
                "output_tokens": 10,
                "total_tokens": self.total_tokens,
            }
            return msg
        self.captured_second_input = list(messages)
        return AIMessage(content="完了しました")


@pytest.mark.asyncio
async def test_pre_note_nudge_injected_when_soft_threshold_not_reached(monkeypatch) -> None:
    """[context_compaction.subagent].pre_note_threshold 到達時、次のLLM呼び出しの
    入力へ write_thread_note を促す HumanMessage が差し込まれる。

    以前は maybe_append_precompact_note_nudge が src/subagent.py から一度も
    呼ばれておらず、[context_compaction.subagent].pre_note_threshold が
    設定として存在するのに何の効果も持たない実装漏れになっていた（この
    テストはその回帰防止）。
    """
    config = _FakeConfig()
    config.context_trim_subagent_enabled = False
    config.track_token_usage = True
    config.subagent_token_guard_enabled = True
    config.subagent_token_guard_soft_threshold = 100000  # 到達しない水準
    config.subagent_token_guard_hard_threshold = 200000
    config.context_compaction_subagent_enabled = True
    config.context_compaction_subagent_pre_note_threshold = 1000  # 到達する水準
    config.context_compaction_subagent_keep_recent_turns = 3
    config.context_compaction_subagent_min_messages_to_compact = 9999  # should_compactは発火させない

    fake_model = _ToolCallWithUsageThenFinalModel(total_tokens=1500)

    async def fake_build_model(config, role):
        return fake_model

    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    result = await subagent.run_subagent(
        task="t",
        tools=[dummy_tool],
        system_prompt="サブエージェント専用システムプロンプト",
        config=config,
        max_iterations=5,
    )

    assert result == "完了しました"
    assert fake_model.captured_second_input is not None
    assert any(
        isinstance(m, HumanMessage) and _PRE_NOTE_MARKER in m.content for m in fake_model.captured_second_input
    )


@pytest.mark.asyncio
async def test_pre_note_nudge_not_injected_when_soft_threshold_reached(monkeypatch) -> None:
    """token_guardのソフト警告が発動する場合は、同じ呼び出しでpre_noteを
    差し込まない（「これ以上調べるな」と「write_thread_noteを呼べ」が
    矛盾するのを避けるsrc/graph.pyと同じ排他方針の回帰）。
    """
    config = _FakeConfig()
    config.context_trim_subagent_enabled = False
    config.track_token_usage = True
    config.subagent_token_guard_enabled = True
    config.subagent_token_guard_soft_threshold = 1000  # 到達する水準
    config.subagent_token_guard_hard_threshold = 200000
    config.subagent_token_guard_soft_warning_text = "ソフト警告文言"
    config.context_compaction_subagent_enabled = True
    config.context_compaction_subagent_pre_note_threshold = 1000  # softと同時に到達する水準
    config.context_compaction_subagent_keep_recent_turns = 3
    config.context_compaction_subagent_min_messages_to_compact = 9999

    fake_model = _ToolCallWithUsageThenFinalModel(total_tokens=1500)

    async def fake_build_model(config, role):
        return fake_model

    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    result = await subagent.run_subagent(
        task="t",
        tools=[dummy_tool],
        system_prompt="サブエージェント専用システムプロンプト",
        config=config,
        max_iterations=5,
    )

    assert result == "完了しました"
    assert fake_model.captured_second_input is not None
    assert any(
        isinstance(m, HumanMessage) and m.content == "ソフト警告文言" for m in fake_model.captured_second_input
    )
    assert not any(
        isinstance(m, HumanMessage) and _PRE_NOTE_MARKER in m.content for m in fake_model.captured_second_input
    )
