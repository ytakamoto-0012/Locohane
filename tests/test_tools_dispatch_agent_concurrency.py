"""dispatch_agent の並列実行数をガードするセマフォの回帰テスト。

ToolNode は同一AIMessage内の複数tool_callsを並列実行するため、モデルが
dispatch_agent を1ターンで複数回呼ぶと、単一インスタンスのllama-server
へ複数リクエストが同時に飛ぶ。本番で、この並列実行がLangGraphチェック
ポイントの破損（AIMessageのtool_callsに対応するToolMessageが欠落し、
次のモデル呼び出しでValueErrorが発生する）につながる事象が確認された
ため、実際のLLM呼び出し（run_subagent）の同時実行数が
_DISPATCH_AGENT_SEMAPHORE の設定値（config.ini の [subagent].max_parallel
由来）どおりに制御されることを検証する。
"""

import asyncio

import pytest

from src import tools


def _setup(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_LLM_CONFIG", object())
    monkeypatch.setattr(
        tools,
        "_AGENT_TYPES",
        {"explore": tools.ResolvedAgentType(description="", system_prompt="", tools=[])},
    )


async def _dispatch_three(monkeypatch) -> tuple[list[str], int]:
    concurrent = 0
    max_concurrent = 0

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return f"done:{task}"

    monkeypatch.setattr(tools, "run_subagent", fake_run_subagent)

    results = await asyncio.gather(
        tools.dispatch_agent.ainvoke({"task": "a", "agent_type": "explore"}),
        tools.dispatch_agent.ainvoke({"task": "b", "agent_type": "explore"}),
        tools.dispatch_agent.ainvoke({"task": "c", "agent_type": "explore"}),
    )
    return results, max_concurrent


@pytest.mark.asyncio
async def test_dispatch_agent_default_max_parallel_serializes_calls(monkeypatch) -> None:
    _setup(monkeypatch)
    # モジュールデフォルト（init_tools() 未実行時）は Semaphore(1) ＝ 完全直列化。
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 1
    assert sorted(results) == ["done:a", "done:b", "done:c"]


@pytest.mark.asyncio
async def test_dispatch_agent_max_parallel_two_caps_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_SEMAPHORE", asyncio.Semaphore(2))
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 2
    assert sorted(results) == ["done:a", "done:b", "done:c"]


@pytest.mark.asyncio
async def test_dispatch_agent_max_parallel_disabled_allows_full_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_SEMAPHORE", None)
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 3
    assert sorted(results) == ["done:a", "done:b", "done:c"]
