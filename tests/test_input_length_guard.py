"""src/input_length_guard.py の回帰テスト。

src/main_token_guard.py は「LLM応答後の usage_metadata.total_tokens」を
ReActループ内で監視するのに対し、こちらは「ユーザーの1ターンの生入力の文字数」を
LLM呼び出し前に一度だけチェックする別機構。閾値を超えても LLM 呼び出し自体は
止めず、分割対応を促す注意書きを本文の先頭へ追加する。
"""

from dataclasses import dataclass

from src.input_length_guard import INPUT_LENGTH_GUARD_MARKER, apply_input_length_guard


@dataclass
class _FakeConfig:
    graph_input_length_guard_enabled: bool = True
    graph_input_length_guard_threshold_chars: int = 100
    graph_input_length_guard_warning_text: str = "分割して進めてください"


def test_no_notice_when_below_threshold() -> None:
    config = _FakeConfig(graph_input_length_guard_threshold_chars=100)
    text = "a" * 50

    result = apply_input_length_guard(text, len(text), config)

    assert result == text


def test_no_notice_when_exactly_at_threshold() -> None:
    config = _FakeConfig(graph_input_length_guard_threshold_chars=100)
    text = "a" * 100

    result = apply_input_length_guard(text, len(text), config)

    assert result == text


def test_notice_prepended_when_above_threshold() -> None:
    config = _FakeConfig(
        graph_input_length_guard_threshold_chars=100,
        graph_input_length_guard_warning_text="分割して進めてください",
    )
    text = "a" * 150

    result = apply_input_length_guard(text, len(text), config)

    assert result.startswith(INPUT_LENGTH_GUARD_MARKER)
    assert "分割して進めてください" in result
    assert result.endswith(text)


def test_disabled_guard_never_injects() -> None:
    config = _FakeConfig(
        graph_input_length_guard_enabled=False,
        graph_input_length_guard_threshold_chars=100,
    )
    text = "a" * 999999

    result = apply_input_length_guard(text, len(text), config)

    assert result == text


def test_raw_length_used_instead_of_text_length() -> None:
    # UNCパス置換等で本文の実文字数が変わっていても、判定には
    # register_raw_unc_paths_in_text 適用前の raw_length を使う。
    config = _FakeConfig(graph_input_length_guard_threshold_chars=100)
    text = "short after replacement"

    result = apply_input_length_guard(text, raw_length=200, config=config)

    assert result.startswith(INPUT_LENGTH_GUARD_MARKER)
    assert result.endswith(text)
