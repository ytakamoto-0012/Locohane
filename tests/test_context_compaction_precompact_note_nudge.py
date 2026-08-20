"""src/context_compaction.py の maybe_append_precompact_note_nudge() の回帰テスト。

コンテキスト圧縮（要約）は永続履歴を書き換える恒久的な操作であり、要約LLMの
精度次第で古い会話中の具体的な事実が薄まって失われうる。圧縮が実際に発火する
前に、write_thread_noteへの書き出しを促す注意メッセージを1回だけ差し込む
（src/main_token_guard.py の maybe_append_token_guard と同じ設計）。
"""

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from src.context_compaction import _PRE_NOTE_MARKER, maybe_append_precompact_note_nudge


@dataclass
class _FakeConfig:
    context_compaction_enabled: bool = True
    context_compaction_pre_note_threshold: int = 1000
    context_compaction_pre_note_warning_text: str = "write_thread_noteへ書き出してください"


def _ai_with_usage(total_tokens: int) -> AIMessage:
    msg = AIMessage(content="ok")
    msg.usage_metadata = {
        "input_tokens": total_tokens - 10,
        "output_tokens": 10,
        "total_tokens": total_tokens,
    }
    return msg


def test_no_injection_when_below_threshold() -> None:
    config = _FakeConfig(context_compaction_pre_note_threshold=1000)
    messages = [HumanMessage(content="hi"), _ai_with_usage(500)]

    result = maybe_append_precompact_note_nudge(messages, config)

    assert result == messages


def test_injects_nudge_when_threshold_reached() -> None:
    config = _FakeConfig(context_compaction_pre_note_threshold=1000)
    messages = [HumanMessage(content="hi"), _ai_with_usage(1500)]

    result = maybe_append_precompact_note_nudge(messages, config)

    assert len(result) == len(messages) + 1
    added = result[-1]
    assert isinstance(added, HumanMessage)
    assert _PRE_NOTE_MARKER in added.content
    assert "write_thread_noteへ書き出してください" in added.content


def test_original_messages_are_not_mutated() -> None:
    config = _FakeConfig(context_compaction_pre_note_threshold=1000)
    messages = [HumanMessage(content="hi"), _ai_with_usage(1500)]
    original_length = len(messages)

    maybe_append_precompact_note_nudge(messages, config)

    assert len(messages) == original_length


def test_disabled_compaction_never_injects() -> None:
    config = _FakeConfig(context_compaction_enabled=False, context_compaction_pre_note_threshold=1000)
    messages = [HumanMessage(content="hi"), _ai_with_usage(999999)]

    result = maybe_append_precompact_note_nudge(messages, config)

    assert result == messages


def test_zero_threshold_disables_nudge() -> None:
    config = _FakeConfig(context_compaction_pre_note_threshold=0)
    messages = [HumanMessage(content="hi"), _ai_with_usage(999999)]

    result = maybe_append_precompact_note_nudge(messages, config)

    assert result == messages


def test_no_ai_message_never_injects() -> None:
    config = _FakeConfig(context_compaction_pre_note_threshold=1000)
    messages = [HumanMessage(content="hi")]

    result = maybe_append_precompact_note_nudge(messages, config)

    assert result == messages
