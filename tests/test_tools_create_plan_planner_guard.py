"""create_plan の「事前にplanner委譲が必要」ガードの回帰テスト。

背景（本番ログ 2026-08-14, app_20260814_174555.log）: メインエージェントが
システムプロンプトの5ステップワークフロー（調査→設計→create_plan→
approve_plan→update_task_progress）のうち「設計」（dispatch_agent
(agent_type="planner") への委譲）を一度も呼ばずに create_plan を呼び、
自分の記憶だけで worker への委譲task文を執筆した結果、その文面が内部で
自己矛盾し（アンカー行のみ更新 vs 全行更新、対応表と具体例の行番号の不一致）、
annual_schedule.xlsx の修正が失敗した。

システムプロンプトの指示のみでは確実性が無い（create_plan→approve_plan の
順序と同じ理由づけ、_guard_awaiting_approve_plan 参照）ため、create_plan
自体に「同一ターンで dispatch_agent(agent_type='planner') が完了している
必要がある」というコード側ガードを追加した。本テストはこのガードの
成立・解除・消費（1回のplanner完了は1回のcreate_planにしか使えない）を検証する。
"""

import pytest

from src import tools


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeMessage:
    def __init__(self, content: str = "", **kwargs) -> None:
        self.content = content

    async def send(self) -> None:
        pass


def _setup(monkeypatch, *, require_planner: bool = True) -> _FakeUserSession:
    monkeypatch.setattr(tools._state, "_LLM_CONFIG", object())
    monkeypatch.setattr(
        tools._state,
        "_AGENT_TYPES",
        {
            "planner": tools._state.ResolvedAgentType(description="", system_prompt="", tools=[]),
            "worker": tools._state.ResolvedAgentType(description="", system_prompt="", tools=[]),
        },
    )
    session = _FakeUserSession()
    monkeypatch.setattr(tools.cl, "user_session", session)
    monkeypatch.setattr(tools.cl, "Message", _FakeMessage)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_SEMAPHORES", {})
    monkeypatch.setattr(tools._dispatch_agent_job, "_DISPATCH_AGENT_JOBS", {})
    monkeypatch.setattr(tools._state, "_PLAN_REQUIRE_PLANNER_DISPATCH", require_planner)
    return session


async def _dispatch_planner(monkeypatch) -> None:
    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        return "計画草案です。"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    await tools.dispatch_agent.ainvoke({"task": "設計してください", "agent_type": "planner"})


_STEPS = [{"content": "作業する", "activeForm": "作業中"}]


@pytest.mark.asyncio
async def test_create_plan_blocked_without_prior_planner_dispatch(monkeypatch) -> None:
    session = _setup(monkeypatch)

    result = await tools.create_plan.ainvoke({"steps": _STEPS})

    assert result.startswith("エラー")
    assert "planner" in result
    assert session.get("plan") is None


@pytest.mark.asyncio
async def test_create_plan_succeeds_after_planner_dispatch(monkeypatch) -> None:
    session = _setup(monkeypatch)
    await _dispatch_planner(monkeypatch)

    result = await tools.create_plan.ainvoke({"steps": _STEPS})

    assert not result.startswith("エラー")
    assert session.get("plan") is not None


@pytest.mark.asyncio
async def test_planner_dispatch_flag_is_consumed_by_create_plan(monkeypatch) -> None:
    """1回のplanner完了は1回のcreate_planにしか使えない（同一ターンでの
    再create_planには、その都度新しいplanner委譲が必要）。"""
    session = _setup(monkeypatch)
    await _dispatch_planner(monkeypatch)

    first = await tools.create_plan.ainvoke({"steps": _STEPS})
    assert not first.startswith("エラー")

    second = await tools.create_plan.ainvoke({"steps": _STEPS})
    assert second.startswith("エラー")
    assert "planner" in second


async def _dispatch_planner_info_insufficient(monkeypatch) -> None:
    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        return "情報不足のため、対象ファイルの原文を確認してください。"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    await tools.dispatch_agent.ainvoke({"task": "設計してください", "agent_type": "planner"})


@pytest.mark.asyncio
async def test_create_plan_blocked_when_planner_reports_info_insufficient(monkeypatch) -> None:
    """plannerが「情報不足」と返した場合、メインエージェントがそれを無視して
    create_planを呼んでもブロックされる（応答を無視した誤呼び出しの回帰防止）。"""
    session = _setup(monkeypatch)
    await _dispatch_planner_info_insufficient(monkeypatch)

    result = await tools.create_plan.ainvoke({"steps": _STEPS})

    assert result.startswith("エラー")
    assert "情報不足" in result
    assert session.get("plan") is None


async def _dispatch_planner_with_incidental_info_insufficient_mention(monkeypatch) -> None:
    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        return (
            "1. steps候補\n- 商品リストの情報不足の項目に注意喚起を追加する\n\n"
            "2. detail_markdown草案\n- 背景・目的: 在庫確認\n- 成果物: report.xlsx 1件"
        )

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    await tools.dispatch_agent.ainvoke({"task": "設計してください", "agent_type": "planner"})


@pytest.mark.asyncio
async def test_create_plan_not_blocked_when_plan_merely_mentions_info_insufficient(monkeypatch) -> None:
    """steps候補/detail_markdownを伴う正常な計画草案が、その本文中にたまたま
    「情報不足」という語句を含むだけで誤ってブロックされないことを確認する
    （単純な部分文字列一致だった旧実装の誤検知バグの回帰防止）。"""
    session = _setup(monkeypatch)
    await _dispatch_planner_with_incidental_info_insufficient_mention(monkeypatch)

    result = await tools.create_plan.ainvoke({"steps": _STEPS})

    assert not result.startswith("エラー")
    assert session.get("plan") is not None


@pytest.mark.asyncio
async def test_create_plan_guard_disabled_by_config(monkeypatch) -> None:
    session = _setup(monkeypatch, require_planner=False)

    result = await tools.create_plan.ainvoke({"steps": _STEPS})

    assert not result.startswith("エラー")
    assert session.get("plan") is not None
