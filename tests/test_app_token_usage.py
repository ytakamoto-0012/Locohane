"""app.py のトークン使用量集計ヘルパーの回帰テスト。

_is_subagent_call() は、event["parent_ids"] に現在開いている（steps辞書に
残っている）Step の run_id が含まれるかどうかで、dispatch_agent 内部
（サブエージェント）由来のイベントかを判定する。
"""

from app import _format_token_usage, _is_subagent_call


class _FakeStep:
    def __init__(self, step_id: str) -> None:
        self.id = step_id


def test_is_subagent_call_true_when_parent_is_open_step() -> None:
    steps = {"tool-run-1": _FakeStep("step-1")}
    event = {"parent_ids": ["some-other-run", "tool-run-1"]}

    assert _is_subagent_call(event, steps) is True


def test_is_subagent_call_false_for_top_level_event() -> None:
    steps = {"tool-run-1": _FakeStep("step-1")}
    event = {"parent_ids": ["unrelated-run"]}

    assert _is_subagent_call(event, steps) is False


def test_is_subagent_call_false_when_no_steps_open() -> None:
    event = {"parent_ids": ["some-run"]}

    assert _is_subagent_call(event, {}) is False


def test_format_token_usage_includes_all_three_tiers() -> None:
    call = {"input": 1, "output": 2, "total": 3}
    cumulative_main = {"input": 10, "output": 20, "total": 30}
    cumulative = {"input": 100, "output": 200, "total": 300}

    text = _format_token_usage(call, cumulative_main, cumulative)

    assert "リクエスト1回あたり" in text
    assert "メインエージェント累計" in text
    assert "会話累計" in text
    assert "入力: 1 " in text
    assert "入力: 10 " in text
    assert "入力: 100 " in text
