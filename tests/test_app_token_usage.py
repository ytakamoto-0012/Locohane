"""app.py のトークン使用量集計ヘルパーの回帰テスト。

_is_subagent_call() は、event["parent_ids"] に現在開いている（steps辞書に
残っている）Step の run_id が含まれるかどうかで、dispatch_agent 内部
（サブエージェント）由来のイベントかを判定する。
"""

import json

from app import TOKEN_USAGE_PREFIX, _format_token_usage, _is_subagent_call


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

    assert text.startswith(TOKEN_USAGE_PREFIX)
    payload = json.loads(text[len(TOKEN_USAGE_PREFIX) :])
    rows = {row["label"]: row for row in payload["rows"]}

    assert rows["リクエスト1回あたり"] == {"label": "リクエスト1回あたり", **call}
    assert rows["メインエージェント累計"] == {"label": "メインエージェント累計", **cumulative_main}
    assert rows["会話累計（サブエージェント含む）"] == {
        "label": "会話累計（サブエージェント含む）",
        **cumulative,
    }
