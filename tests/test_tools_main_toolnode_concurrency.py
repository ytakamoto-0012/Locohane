"""メインエージェントの ImageAwareToolNode における並列実行数ガードの回帰テスト。

LangGraph の ToolNode は同一AIMessage内の複数tool_callsを asyncio.gather() で
完全並列実行する。dispatch_agent 専用の _DISPATCH_AGENT_SEMAPHORE と同じ理由づけ
で、メインエージェント側の全ツール呼び出し（ImageAwareToolNode 経由）についても
_TOOL_CALL_SEMAPHORE（config.ini の [graph].max_parallel 由来）が同時実行数を
制御することを検証する。同期 def ツール（別スレッドの ThreadPoolExecutor 上で
実行される）についても、awrap_tool_call 経由で正しくガードされることを合わせて
確認する（tool.func/coroutine を直接書き換える方式では効かない点の回帰確認）。
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from src import tools


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _setup(monkeypatch) -> None:
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())


def _make_node(*, include_sync_tool: bool = False):
    concurrent = 0
    max_concurrent = 0

    @tool
    async def dummy_a(x: str) -> str:
        """ダミー非同期ツールA。"""
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return f"done:{x}"

    @tool
    async def dummy_b(x: str) -> str:
        """ダミー非同期ツールB。"""
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return f"done:{x}"

    @tool
    def dummy_c_sync(x: str) -> str:
        """ダミー同期ツールC（別スレッドで実行される）。"""
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        import time

        time.sleep(0.05)
        concurrent -= 1
        return f"done:{x}"

    dummy_tools = [dummy_a, dummy_b]
    if include_sync_tool:
        dummy_tools.append(dummy_c_sync)

    node = tools.ImageAwareToolNode(dummy_tools)

    # ImageAwareToolNode（ToolNode）はグラフ実行時の Runtime コンテキストを
    # 要求するため、単体で ainvoke するのではなく、最小の StateGraph に
    # ノードとして組み込んでコンパイルしたグラフ経由で実行する
    # （本番の src/graph.py と同じ組み込み方）。
    graph = StateGraph(MessagesState)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile()

    def get_max_concurrent() -> int:
        return max_concurrent

    return compiled, get_max_concurrent


def _tool_call_input(names: list[str]) -> dict:
    tool_calls = [{"name": name, "args": {"x": name}, "id": f"call-{i}"} for i, name in enumerate(names)]
    return {"messages": [AIMessage(content="", tool_calls=tool_calls)]}


@pytest.mark.asyncio
async def test_main_toolnode_default_max_parallel_serializes_calls(monkeypatch) -> None:
    _setup(monkeypatch)
    node, get_max_concurrent = _make_node()
    # モジュールデフォルト（init_tools() 未実行時）は Semaphore(1) ＝ 完全直列化。

    await node.ainvoke(_tool_call_input(["dummy_a", "dummy_b"]))

    assert get_max_concurrent() == 1


@pytest.mark.asyncio
async def test_main_toolnode_max_parallel_two_caps_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_TOOL_CALL_SEMAPHORE", asyncio.Semaphore(2))
    node, get_max_concurrent = _make_node()

    await node.ainvoke(_tool_call_input(["dummy_a", "dummy_b"]))

    assert get_max_concurrent() == 2


@pytest.mark.asyncio
async def test_main_toolnode_max_parallel_disabled_allows_full_concurrency(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_TOOL_CALL_SEMAPHORE", None)
    node, get_max_concurrent = _make_node()

    await node.ainvoke(_tool_call_input(["dummy_a", "dummy_b"]))

    assert get_max_concurrent() == 2


@pytest.mark.asyncio
async def test_main_toolnode_guards_sync_tool_too(monkeypatch) -> None:
    """同期 def ツールも awrap_tool_call 経由で正しくガードされることを確認する。"""
    _setup(monkeypatch)
    monkeypatch.setattr(tools, "_TOOL_CALL_SEMAPHORE", asyncio.Semaphore(1))
    node, get_max_concurrent = _make_node(include_sync_tool=True)

    await node.ainvoke(_tool_call_input(["dummy_a", "dummy_b", "dummy_c_sync"]))

    assert get_max_concurrent() == 1
