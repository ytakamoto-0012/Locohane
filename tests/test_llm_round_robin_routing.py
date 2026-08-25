"""src/llm/routing.py の round_robin ルーティング戦略の回帰テスト。

round_robin は呼び出しごとに順番に接続先を回すが、provider="llama_cpp" の
接続先だけは選ぶ前に GET /slots で空きスロットの有無を確認する
（_probe_llama_cpp_slots_available）。空きが無ければスキップして次点へ回し、
候補全てが埋まっていれば空きが出るまで待機する（_select_round_robin_endpoint）。
provider="openai_compatible"（既定）の接続先は確認を行わず、従来通り即座に
選ばれる。

sticky戦略は「ルーティングとして有効ではなかった」ため完全に削除された
（本ファイルが置き換えたテストは以前 tests/test_llm_sticky_routing.py に
あった）。
"""

import pytest

from src import llm
from src.config import LLMEndpoint


def _endpoints(n: int) -> tuple[LLMEndpoint, ...]:
    return tuple(LLMEndpoint(base_url=f"http://host{i}/v1", api_key="dummy", model="m") for i in range(n))


def _llama_cpp_endpoints(n: int) -> tuple[LLMEndpoint, ...]:
    return tuple(LLMEndpoint(base_url=f"http://host{i}/v1", api_key="dummy", model="m", provider="llama_cpp") for i in range(n))


def _unique_session_id(suffix: str) -> str:
    return f"test-round-robin-{suffix}-{id(object())}"


@pytest.mark.asyncio
async def test_round_robin_cycles_through_openai_compatible_endpoints_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider未指定（openai_compatible）は /slots を一切呼ばず、従来通り順番に回ること。"""

    async def _fail_if_called(base_url: str, timeout_seconds: float) -> bool | None:
        raise AssertionError("openai_compatible の接続先で /slots を呼んではいけない")

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fail_if_called)

    endpoints = _endpoints(3)
    session_id = _unique_session_id("cycle")
    try:
        llm.set_current_session(session_id)
        picks = []
        for _ in range(6):
            picked = await llm._select_endpoint("main", endpoints, "round_robin")
            picks.append(picked.base_url)

        # 1周目（先頭3件）で全接続先が重複なく登場し、2周目は同じ並びで
        # 繰り返される（周期性）こと。
        assert set(picks[:3]) == {e.base_url for e in endpoints}
        assert picks[:3] == picks[3:6]
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_skips_busy_llama_cpp_endpoint_and_uses_free_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """空きスロットが無い接続先はスキップされ、空きのある接続先が選ばれ続けること。"""

    endpoints = _llama_cpp_endpoints(2)
    busy_url = endpoints[0].base_url
    free_url = endpoints[1].base_url

    async def _fake_probe(base_url: str, timeout_seconds: float) -> bool | None:
        return base_url != busy_url

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fake_probe)

    session_id = _unique_session_id("skip-busy")
    try:
        llm.set_current_session(session_id)
        for _ in range(5):
            picked = await llm._select_endpoint(
                "main",
                endpoints,
                "round_robin",
                probe_timeout_seconds=1.0,
                busy_poll_interval_seconds=0.01,
            )
            assert picked.base_url == free_url
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_prefers_llama_cpp_only_when_openai_compatible_alternative_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """llama_cppの接続先が空き無しでも、providerがopenai_compatibleの接続先は確認なしで選ばれること。"""

    llama_endpoint = LLMEndpoint(base_url="http://llama/v1", api_key="dummy", model="m", provider="llama_cpp")
    plain_endpoint = LLMEndpoint(base_url="http://plain/v1", api_key="dummy", model="m")
    endpoints = (llama_endpoint, plain_endpoint)

    async def _always_busy(base_url: str, timeout_seconds: float) -> bool | None:
        return False

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _always_busy)

    session_id = _unique_session_id("mixed-provider")
    try:
        llm.set_current_session(session_id)
        for _ in range(4):
            picked = await llm._select_endpoint(
                "main",
                endpoints,
                "round_robin",
                probe_timeout_seconds=1.0,
                busy_poll_interval_seconds=0.01,
            )
            assert picked.base_url == plain_endpoint.base_url
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_waits_until_a_llama_cpp_slot_frees_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """候補全ての空きスロットが無い場合は待機し、空きが出た時点で選ばれること。"""

    calls: dict[str, int] = {}

    async def _fake_probe(base_url: str, timeout_seconds: float) -> bool | None:
        calls[base_url] = calls.get(base_url, 0) + 1
        # 各接続先とも1回目は busy、2回目以降は空きとして扱う。
        return calls[base_url] > 1

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fake_probe)

    endpoints = _llama_cpp_endpoints(2)
    session_id = _unique_session_id("wait-for-free")
    try:
        llm.set_current_session(session_id)
        picked = await llm._select_endpoint(
            "main",
            endpoints,
            "round_robin",
            probe_timeout_seconds=1.0,
            busy_poll_interval_seconds=0.01,
        )
        assert picked.base_url in {e.base_url for e in endpoints}
        # 1周目で両方busy判定を受けてから待機し、2周目で空きが見つかった
        # ことを示す（両方とも最低1回はprobeされている）。
        assert set(calls) == {e.base_url for e in endpoints}
        assert sum(calls.values()) >= 3
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_single_endpoint_still_waits_for_free_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """接続先が1件しか無くても、round_robinはstrategyに関わらず即座に選ぶ早期リターンを通らず、
    空き確認・待機を行うこと（random/priority_failoverとの違い）。
    """
    call_count = 0

    async def _fake_probe(base_url: str, timeout_seconds: float) -> bool | None:
        nonlocal call_count
        call_count += 1
        return call_count > 2

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fake_probe)

    endpoints = _llama_cpp_endpoints(1)
    session_id = _unique_session_id("single-wait")
    try:
        llm.set_current_session(session_id)
        picked = await llm._select_endpoint(
            "main",
            endpoints,
            "round_robin",
            probe_timeout_seconds=1.0,
            busy_poll_interval_seconds=0.01,
        )
        assert picked.base_url == endpoints[0].base_url
        assert call_count == 3
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_non_round_robin_strategies_skip_probe_for_single_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """random/priority_failoverは従来通り、接続先が1件なら空き確認なしで即座に選ぶこと。"""

    async def _fail_if_called(base_url: str, timeout_seconds: float) -> bool | None:
        raise AssertionError("単一接続先ではprobeを呼んではいけない（round_robin以外の戦略）")

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fail_if_called)

    endpoints = _llama_cpp_endpoints(1)
    for strategy in ("random", "priority_failover"):
        session_id = _unique_session_id(f"fastpath-{strategy}")
        try:
            llm.set_current_session(session_id)
            picked = await llm._select_endpoint("main", endpoints, strategy)
            assert picked.base_url == endpoints[0].base_url
        finally:
            llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_treats_unknown_probe_result_as_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """/slots が確認不能（None、通信エラー等）の場合はフェイルセーフで空きありとみなし、待機しないこと。"""

    calls = 0

    async def _fake_unknown(base_url: str, timeout_seconds: float) -> bool | None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _fake_unknown)

    endpoints = _llama_cpp_endpoints(2)
    session_id = _unique_session_id("unknown-probe")
    try:
        llm.set_current_session(session_id)
        picked = await llm._select_endpoint(
            "main",
            endpoints,
            "round_robin",
            probe_timeout_seconds=1.0,
            busy_poll_interval_seconds=0.01,
        )
        assert picked.base_url in {e.base_url for e in endpoints}
        # 最初に確認した1件がNoneのため即選択され、2件目以降は確認され
        # ない（=待機の1周目すら発生しない）はず。
        assert calls == 1
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_round_robin_recomputes_eligible_endpoints_on_each_busy_wait_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """待機の周回ごとに使用可能時間帯（eligible_indices）を再計算すること。

    _select_round_robin_endpoint が候補一覧を最初の1回だけ計算して固定して
    しまうと、空き待ちが長引いて start/end の境界をまたいだ場合に、新しく
    使用可能になった接続先を見逃してしまう（過去に見つかった問題の回帰）。
    """
    eligible_calls: list[list[int]] = []

    def _fake_eligible_indices(endpoints: tuple[LLMEndpoint, ...]) -> list[int]:
        # 1周目は index=1（plain_endpoint）がまだ時間帯外という想定で除外し、
        # 2周目以降は使用可能時間帯に入ったとして両方を候補にする。
        result = [0] if len(eligible_calls) == 0 else [0, 1]
        eligible_calls.append(result)
        return result

    monkeypatch.setattr(llm.routing, "_compute_eligible_indices", _fake_eligible_indices)

    async def _always_busy(base_url: str, timeout_seconds: float) -> bool | None:
        return False

    monkeypatch.setattr(llm.routing, "_probe_llama_cpp_slots_available", _always_busy)

    busy_endpoint = LLMEndpoint(base_url="http://busy/v1", api_key="dummy", model="m", provider="llama_cpp")
    plain_endpoint = LLMEndpoint(base_url="http://plain/v1", api_key="dummy", model="m")
    endpoints = (busy_endpoint, plain_endpoint)

    index = await llm.routing._select_round_robin_endpoint(
        "main",
        endpoints,
        probe_timeout_seconds=1.0,
        busy_poll_interval_seconds=0.01,
    )

    # 1周目はindex=0しか候補になく、それがbusyなので待機。2周目で候補一覧が
    # 再計算され、新たに候補入りしたindex=1（provider未指定なので確認不要）
    # が選ばれるはず。
    assert index == 1
    assert len(eligible_calls) >= 2


def test_sticky_is_no_longer_a_valid_routing_strategy() -> None:
    """sticky戦略は完全に削除され、config.pyのバリデーションも受け付けないこと。"""
    from src.config import LLM_ROUTING_STRATEGIES

    assert "sticky" not in LLM_ROUTING_STRATEGIES
    assert LLM_ROUTING_STRATEGIES == {"round_robin", "random", "priority_failover"}
