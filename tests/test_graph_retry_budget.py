"""ainvoke_ensuring_final_text のリトライ予算が2種類独立であることの回帰テスト。

旧実装は「ループ検知」と「無言終了」の両フェーズで `attempt >= total_budget`
という共有条件を併用しており、序盤にループ検知でリトライを使い切ると、終盤に
無言終了が起きても再試行が1回も行われなかった。1ターン内でLLM呼び出しが
数十〜数百回に及ぶ長時間タスク（レシピ画像297枚ケース）で、序盤のループ検知2回
により終盤の空応答が再試行されず、35枚処理した時点で無言終了する実例が
観測されたため、予算を独立させた。
"""

import pytest
from langchain_core.messages import AIMessage

from src.graph import ainvoke_ensuring_final_text
from src.llm import ThinkingLoopDetected


class _FakeGraph:
    """ainvoke の戻り値／例外をシナリオで差し替えられるスタブ。"""

    def __init__(self, scenario: list):
        self._scenario = list(scenario)
        self.calls = 0
        self.updated_states: list = []

    async def ainvoke(self, inputs, config=None):
        self.calls += 1
        item = self._scenario.pop(0) if self._scenario else {"messages": [AIMessage(content="ok")]}
        if isinstance(item, Exception):
            raise item
        return item

    async def aupdate_state(self, config, values):
        self.updated_states.append(values)


def _empty() -> dict:
    """無言終了（tool_calls も本文も無い AIMessage）を表す戻り値。"""
    return {"messages": [AIMessage(content="")]}


def _ok() -> dict:
    return {"messages": [AIMessage(content="done")]}


@pytest.mark.asyncio
async def test_loop_retries_do_not_consume_empty_response_budget() -> None:
    """序盤のループ検知でリトライを使っても、終盤の空応答は再試行される。"""
    graph = _FakeGraph(
        [
            ThinkingLoopDetected("loop1"),
            ThinkingLoopDetected("loop2"),
            _empty(),  # ここで旧実装は予算切れになり再試行されなかった
            _ok(),
        ]
    )

    result = await ainvoke_ensuring_final_text(
        graph, {"messages": []}, {}, max_retries=2, loop_max_retries=2
    )

    assert graph.calls == 4, "ループ2回＋空応答1回の再試行がすべて行われること"
    assert result["messages"][-1].content == "done"


@pytest.mark.asyncio
async def test_empty_response_budget_is_capped() -> None:
    """空応答が続く場合は max_retries 回で打ち切り、最後の結果を返す。"""
    graph = _FakeGraph([_empty(), _empty(), _empty(), _empty(), _empty()])

    result = await ainvoke_ensuring_final_text(
        graph, {"messages": []}, {}, max_retries=2, loop_max_retries=2
    )

    assert graph.calls == 3, "初回 + max_retries(2) で打ち切ること"
    assert result["messages"][-1].content == ""


@pytest.mark.asyncio
async def test_loop_budget_is_capped_and_raises() -> None:
    """ループ検知が loop_max_retries を超えたら送出する。"""
    graph = _FakeGraph(
        [ThinkingLoopDetected("l1"), ThinkingLoopDetected("l2"), ThinkingLoopDetected("l3")]
    )

    with pytest.raises(ThinkingLoopDetected):
        await ainvoke_ensuring_final_text(
            graph, {"messages": []}, {}, max_retries=2, loop_max_retries=2
        )

    assert graph.calls == 3, "初回 + loop_max_retries(2) で送出すること"
    assert graph.updated_states, "raise 前に注入した nudge を除去すること"


@pytest.mark.asyncio
async def test_total_attempts_are_bounded() -> None:
    """両方の異常が交互に続いても、全体の試行回数は total_budget + 1 を超えない。"""
    graph = _FakeGraph(
        [ThinkingLoopDetected("l1"), _empty(), ThinkingLoopDetected("l2"), _empty(), _empty()]
    )

    await ainvoke_ensuring_final_text(
        graph, {"messages": []}, {}, max_retries=2, loop_max_retries=2
    )

    assert graph.calls <= 5, "初回 + (max_retries + loop_max_retries) を超えないこと"
