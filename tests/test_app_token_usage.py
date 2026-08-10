"""app.py のトークン使用量集計ヘルパーの回帰テスト。

_is_subagent_call() は、event["parent_ids"] に現在開いている（steps辞書に
残っている）Step の run_id が含まれるかどうかで、dispatch_agent 内部
（サブエージェント）由来のイベントかを判定する。
"""

import dataclasses
import json

import app
from app import TOKEN_USAGE_PREFIX, _format_token_usage, _is_subagent_call, _token_usage_level


def _with_thresholds(monkeypatch, warn: int, alert: int) -> None:
    """app._config（frozen dataclass）の閾値だけを差し替えたコピーに一時的に入れ替える。"""
    monkeypatch.setattr(
        app,
        "_config",
        dataclasses.replace(
            app._config,
            ui_token_usage_warn_threshold=warn,
            ui_token_usage_alert_threshold=alert,
        ),
    )


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

    assert rows["リクエスト1回あたり"] == {"label": "リクエスト1回あたり", **call, "level": None}
    assert rows["メインエージェント累計"] == {"label": "メインエージェント累計", **cumulative_main}
    assert rows["会話累計（サブエージェント含む）"] == {
        "label": "会話累計（サブエージェント含む）",
        **cumulative,
    }


def test_token_usage_level_none_below_thresholds(monkeypatch) -> None:
    _with_thresholds(monkeypatch, warn=48000, alert=64000)

    assert _token_usage_level(1000) is None


def test_token_usage_level_warn_between_thresholds(monkeypatch) -> None:
    _with_thresholds(monkeypatch, warn=48000, alert=64000)

    assert _token_usage_level(48000) == "warn"
    assert _token_usage_level(63999) == "warn"


def test_token_usage_level_alert_at_or_above_alert_threshold(monkeypatch) -> None:
    _with_thresholds(monkeypatch, warn=48000, alert=64000)

    assert _token_usage_level(64000) == "alert"
    assert _token_usage_level(100000) == "alert"


def test_token_usage_level_disabled_when_thresholds_zero(monkeypatch) -> None:
    _with_thresholds(monkeypatch, warn=0, alert=0)

    assert _token_usage_level(1_000_000) is None
