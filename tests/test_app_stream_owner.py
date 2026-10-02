"""app.py の出力元（メイン/各サブエージェント）ごとの思考Step・回答Message管理の回帰テスト。

[subagent].max_parallel を2以上にすると複数のサブエージェントが同時にトークンを流す。
思考Step・回答Messageを1つずつしか持たないと、別々のサブエージェントの思考が同じ
Stepへ交互に書き込まれ、片方のツール開始でもう片方の思考Stepまで閉じられていた。
"""

import pytest

import app
from app import _close_all_outputs, _close_owner_output, _resolve_parent_id, _stream_owner


class _FakeStep:
    def __init__(self, step_id: str) -> None:
        self.id = step_id
        self.metadata = None
        self.end = None
        self.updated = 0

    async def update(self) -> None:
        self.updated += 1


def test_stream_owner_picks_nearest_owner_ancestor() -> None:
    # parent_ids は「ルート→直近の親」の順。batch(外側) と group(内側) の両方が出力元のとき内側を選ぶ。
    event = {"parent_ids": ["graph", "batch", "group2", "model"]}
    assert _stream_owner(event, {"batch", "group2"}) == "group2"
    assert _stream_owner(event, {"batch"}) == "batch"


def test_stream_owner_returns_none_for_main_agent_events() -> None:
    assert _stream_owner({"parent_ids": ["graph", "agent"]}, {"batch"}) is None
    assert _stream_owner({}, {"batch"}) is None


def test_resolve_parent_id_prefers_nearest_open_step() -> None:
    steps = {"batch": _FakeStep("step-batch"), "group1": _FakeStep("step-group1")}
    event = {"parent_ids": ["graph", "batch", "group1", "model"]}
    assert _resolve_parent_id(event, steps) == "step-group1"


@pytest.mark.asyncio
async def test_close_owner_output_leaves_other_owners_open(monkeypatch) -> None:
    sent: list[str] = []

    async def fake_send_answer(answer) -> None:
        sent.append(answer)

    monkeypatch.setattr(app, "_send_answer", fake_send_answer)
    thinkings = {"g1": _FakeStep("t1"), "g2": _FakeStep("t2"), None: _FakeStep("tm")}
    answers = {"g1": "a1", "g2": "a2"}

    await _close_owner_output("g1", thinkings, answers)

    assert set(thinkings) == {"g2", None}
    assert answers == {"g2": "a2"}
    assert sent == ["a1"]


@pytest.mark.asyncio
async def test_close_all_outputs_closes_every_owner_with_reason(monkeypatch) -> None:
    sent: list[str] = []

    async def fake_send_answer(answer) -> None:
        sent.append(answer)

    monkeypatch.setattr(app, "_send_answer", fake_send_answer)
    t1, tm = _FakeStep("t1"), _FakeStep("tm")
    thinkings = {"g1": t1, None: tm}
    answers = {"g2": "a2", None: "am"}

    await _close_all_outputs(thinkings, answers, stopped_reason="loop_detected")

    assert thinkings == {} and answers == {}
    assert sorted(sent) == ["a2", "am"]
    assert t1.metadata == {"stopped_reason": "loop_detected"} and tm.updated == 1


@pytest.mark.asyncio
async def test_mark_background_group_steps_closes_only_unfinished_groups() -> None:
    """安全上限で batch が先に返った時、まだ終わっていないグループStepを「停止」ではなく
    「バックグラウンド継続」で閉じ、ターン終了時の _finalize_orphaned_steps の対象から外す。"""
    running, other = _FakeStep("step-group2"), _FakeStep("step-tool")
    steps = {"group2": running, "tool": other}

    # group1 は on_chain_end 済みで steps に無い。
    await app._mark_background_group_steps({"group1", "group2"}, steps)

    assert steps == {"tool": other}
    assert running.metadata == {"background": True}
    assert running.end is not None and running.updated == 1
    assert "バックグラウンド" in running.output
    assert other.updated == 0
