"""dispatch_agent の並列実行数をガードするセマフォの回帰テスト。

ToolNode は同一AIMessage内の複数tool_callsを並列実行するため、モデルが
dispatch_agent を1ターンで複数回呼ぶと、単一インスタンスのllama-server
へ複数リクエストが同時に飛ぶ。本番で、この並列実行がLangGraphチェック
ポイントの破損（AIMessageのtool_callsに対応するToolMessageが欠落し、
次のモデル呼び出しでValueErrorが発生する）につながる事象が確認された
ため、実際のLLM呼び出し（run_subagent）の同時実行数が
_DISPATCH_AGENT_SEMAPHORES の設定値（config.ini の [subagent].max_parallel
由来）どおりに制御されることを検証する。

_DISPATCH_AGENT_SEMAPHORES はセッション（llm.get_current_session()）ごとに
独立した Semaphore を遅延生成する辞書であるため、各テストは monkeypatch で
辞書を空に差し替えてから実行し、他テストが同じセッションキー（未設定時は
None）で生成した Semaphore を再利用してしまわないようにする。
"""

import asyncio

import pytest

from src import llm, tools


class _FakeUserSession:
    """dispatch_agent の finally が main_agent_glob_guard カウンタをリセットする際に
    触れる cl.user_session を、Chainlit実行コンテキスト無しでも動くよう差し替える。
    """

    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _setup(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_LLM_CONFIG", object())
    monkeypatch.setattr(
        tools,
        "_AGENT_TYPES",
        {"explore": tools.ResolvedAgentType(description="", system_prompt="", tools=[])},
    )
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())
    # 他テストが同じセッションキー（未設定時は None）で生成した Semaphore を
    # 再利用しないよう、辞書を空の状態から始める。
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_SEMAPHORES", {})


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
    # モジュールデフォルト（init_tools() 未実行時）は max_parallel=1 ＝ 完全直列化。
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 1
    assert sorted(results) == ["done:a", "done:b", "done:c"]


@pytest.mark.asyncio
async def test_dispatch_agent_max_parallel_two_caps_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_MAX_PARALLEL", 2)
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 2
    assert sorted(results) == ["done:a", "done:b", "done:c"]


@pytest.mark.asyncio
async def test_dispatch_agent_max_parallel_disabled_allows_full_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_MAX_PARALLEL", 0)
    results, max_concurrent = await _dispatch_three(monkeypatch)

    assert max_concurrent == 3
    assert sorted(results) == ["done:a", "done:b", "done:c"]


@pytest.mark.asyncio
async def test_dispatch_agent_max_parallel_is_independent_per_session(monkeypatch) -> None:
    """max_parallel=1 でも、別セッション（thread_id）同士は互いに待ち合わない。

    _DISPATCH_AGENT_SEMAPHORES はセッションごとに独立した Semaphore を持つため、
    セッションAでdispatch_agentが直列化されていても、セッションBの
    dispatch_agent呼び出しをブロックしないことを確認する。
    """
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_DISPATCH_AGENT_MAX_PARALLEL", 1)

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

    async def run_in_session(thread_id: str, task: str) -> str:
        llm.set_current_session(thread_id)
        return await tools.dispatch_agent.ainvoke({"task": task, "agent_type": "explore"})

    loop = asyncio.get_event_loop()
    start = loop.time()
    results = await asyncio.gather(
        run_in_session("session-a", "a"),
        run_in_session("session-b", "b"),
    )
    elapsed = loop.time() - start

    assert max_concurrent == 2
    assert sorted(results) == ["done:a", "done:b"]
    # セッション間で待ち合っていなければ、2回分の asyncio.sleep(0.05) が
    # 並行して走るため約0.05秒。待ち合っていれば約0.1秒になるはず。
    assert elapsed < 0.09
