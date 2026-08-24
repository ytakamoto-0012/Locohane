"""src/llm.py の sticky ルーティング戦略（空きホスト優先割り当て・能動解放）の回帰テスト。

sticky戦略は本来「会話単位で常に同じ接続先に固定する」ものだが、以前は
crc32ハッシュのみで決めていたため、複数の会話が同じ接続先に衝突する一方で
別の接続先が誰にも使われず空いている、という偏りが起こり得た。

前半は、新しい会話が接続先を初めて選ぶ際に「他のどの会話も固定して
いない空き接続先」を優先すること、一度固定した接続先はforget_session()
されるまで変わらないこと、空きが無ければ従来通りハッシュで衝突を許容
することを確認する。

後半は、forget_session()が呼ばれずに占有記録が残ったケース（タブを閉じずに
ネットワーク切断された等）への対策として追加した、provider="llama_cpp"
限定の能動解放（GET /slots で実際に生成中か確認してから強制解放する）を、
_probe_llama_cpp_slots_idle をモック化して検証する。
"""

import asyncio

import pytest

from src import llm
from src.config import LLMEndpoint


def _endpoints(n: int) -> tuple[LLMEndpoint, ...]:
    return tuple(LLMEndpoint(base_url=f"http://host{i}/v1", api_key="dummy", model="m") for i in range(n))


def _llama_cpp_endpoints(n: int) -> tuple[LLMEndpoint, ...]:
    return tuple(LLMEndpoint(base_url=f"http://host{i}/v1", api_key="dummy", model="m", provider="llama_cpp") for i in range(n))


def _unique_session_id(suffix: str) -> str:
    return f"test-sticky-{suffix}-{id(object())}"


@pytest.mark.asyncio
async def test_new_sessions_prefer_unoccupied_endpoints() -> None:
    endpoints = _endpoints(3)
    session_a = _unique_session_id("a")
    session_b = _unique_session_id("b")
    session_c = _unique_session_id("c")
    try:
        llm.set_current_session(session_a)
        picked_a = await llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_b)
        picked_b = await llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_c)
        picked_c = await llm._select_endpoint("main", endpoints, "sticky")

        # 3接続先に対して3会話なので、空きがある限り全員別々の接続先になる。
        assert {picked_a.base_url, picked_b.base_url, picked_c.base_url} == {e.base_url for e in endpoints}
    finally:
        for session_id in (session_a, session_b, session_c):
            llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_sticky_binding_persists_across_calls_even_if_endpoint_becomes_free() -> None:
    endpoints = _endpoints(2)
    session_a = _unique_session_id("persist-a")
    session_b = _unique_session_id("persist-b")
    try:
        llm.set_current_session(session_a)
        first = await llm._select_endpoint("main", endpoints, "sticky")

        llm.set_current_session(session_b)
        await llm._select_endpoint("main", endpoints, "sticky")

        # 他会話が終了して接続先が空いても、既に固定済みの会話は動かない。
        llm.forget_session(session_b)

        llm.set_current_session(session_a)
        second = await llm._select_endpoint("main", endpoints, "sticky")
        assert second.base_url == first.base_url
    finally:
        llm.forget_session(session_a)
        llm.forget_session(session_b)


@pytest.mark.asyncio
async def test_sticky_falls_back_to_hash_when_no_endpoint_is_free() -> None:
    endpoints = _endpoints(1)
    session_id = _unique_session_id("single")
    try:
        llm.set_current_session(session_id)
        picked = await llm._select_endpoint("main", endpoints, "sticky")
        assert picked.base_url == endpoints[0].base_url
    finally:
        llm.forget_session(session_id)


@pytest.mark.asyncio
async def test_forget_session_releases_occupied_endpoint_for_new_sessions() -> None:
    endpoints = _endpoints(2)
    session_a = _unique_session_id("release-a")
    session_b = _unique_session_id("release-b")
    session_c = _unique_session_id("release-c")
    try:
        llm.set_current_session(session_a)
        picked_a = await llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_b)
        picked_b = await llm._select_endpoint("main", endpoints, "sticky")
        assert {picked_a.base_url, picked_b.base_url} == {e.base_url for e in endpoints}

        # aを終了させ、その接続先を解放する。
        llm.forget_session(session_a)

        llm.set_current_session(session_c)
        picked_c = await llm._select_endpoint("main", endpoints, "sticky")
        # cはaが使っていた（今は空いた）接続先を選ぶはず。
        assert picked_c.base_url == picked_a.base_url
    finally:
        for session_id in (session_a, session_b, session_c):
            llm.forget_session(session_id)


# --- 能動解放（provider="llama_cpp" 限定、GET /slots による確認） ---
#
# いずれも接続先2件を占有者2セッション（a・filler相当）で埋め切ってから、
# 3人目のセッションを要求させる。接続先1件だけだと _select_endpoint の
# 「使用可能な接続先が1件だけの場合は常にそれを返す」早期リターン
# （eligible_indices の長さ==1）を通ってしまい、sticky分岐・占有記録・
# 能動解放ロジック自体を素通りしてしまうため。


@pytest.mark.asyncio
async def test_active_release_skipped_for_openai_compatible_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider未指定（openai_compatible）は、占有が古くても /slots を一切呼ばず解放しないこと。"""

    async def _fail_if_called(base_url: str, timeout_seconds: float) -> bool | None:
        raise AssertionError("openai_compatible の接続先で /slots を呼んではいけない")

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fail_if_called)

    endpoints = _endpoints(2)  # provider既定値=openai_compatible
    session_a = _unique_session_id("skip-provider-a")
    session_c = _unique_session_id("skip-provider-c")
    session_b = _unique_session_id("skip-provider-b")
    try:
        llm.set_current_session(session_a)
        picked_a = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        llm.set_current_session(session_c)
        picked_c = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        assert {picked_a.base_url, picked_c.base_url} == {e.base_url for e in endpoints}

        # 両方の接続先が占有された状態で3人目が要求しても、providerが
        # openai_compatibleのため能動解放は試みられない（呼ばれたら失敗する
        # モック）。従来通りハッシュ衝突を許容するだけで例外は起きない。
        llm.set_current_session(session_b)
        picked_b = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        assert picked_b.base_url in {picked_a.base_url, picked_c.base_url}
        assert ("main", session_a) in llm._STICKY_ASSIGNED_INDEX
        assert ("main", session_c) in llm._STICKY_ASSIGNED_INDEX
    finally:
        for sid in (session_a, session_c, session_b):
            llm.forget_session(sid)


@pytest.mark.asyncio
async def test_active_release_skipped_when_idle_age_not_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """llama_cpp かつ idle-age未達（占有者が直近アクティブ）なら /slots を呼ばないこと。"""

    async def _fail_if_called(base_url: str, timeout_seconds: float) -> bool | None:
        raise AssertionError("idle-age未達の占有先で /slots を呼んではいけない")

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fail_if_called)

    endpoints = _llama_cpp_endpoints(2)
    session_a = _unique_session_id("idle-age-a")
    session_c = _unique_session_id("idle-age-c")
    session_b = _unique_session_id("idle-age-b")
    try:
        llm.set_current_session(session_a)
        # min_idle_seconds を巨大にし、直前に更新した activity timestamp が
        # 絶対に「十分放置された」とみなされないようにする。
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=10_000.0)
        llm.set_current_session(session_c)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=10_000.0)

        llm.set_current_session(session_b)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=10_000.0)
        # 例外なく完了すれば（=probeが呼ばれなければ）OK。両方の占有が
        # 生き残っていることも併せて確認する。
        assert ("main", session_a) in llm._STICKY_ASSIGNED_INDEX
        assert ("main", session_c) in llm._STICKY_ASSIGNED_INDEX
    finally:
        for sid in (session_a, session_c, session_b):
            llm.forget_session(sid)


@pytest.mark.asyncio
async def test_active_release_reassigns_when_probe_confirms_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """llama_cpp + idle-age達成 + /slots がidle(True)を返す → 占有解放され新セッションに再割当されること。"""

    async def _fake_idle(base_url: str, timeout_seconds: float) -> bool | None:
        return True

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fake_idle)

    endpoints = _llama_cpp_endpoints(2)
    session_a = _unique_session_id("release-confirm-a")
    session_c = _unique_session_id("release-confirm-c")
    session_b = _unique_session_id("release-confirm-b")
    try:
        llm.set_current_session(session_a)
        picked_a = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        llm.set_current_session(session_c)
        picked_c = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        assert {picked_a.base_url, picked_c.base_url} == {e.base_url for e in endpoints}

        llm.set_current_session(session_b)
        picked_b = await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        # bはa・cのどちらか一方が占有していた接続先を横取りできたはず。
        assert picked_b.base_url in {picked_a.base_url, picked_c.base_url}

        a_evicted = ("main", session_a) not in llm._STICKY_ASSIGNED_INDEX
        c_evicted = ("main", session_c) not in llm._STICKY_ASSIGNED_INDEX
        # ちょうど一方だけが強制解放され、もう一方は占有記録が残っている。
        assert a_evicted != c_evicted
        assert ("main", session_b) in llm._STICKY_ASSIGNED_INDEX
    finally:
        for sid in (session_a, session_c, session_b):
            llm.forget_session(sid)


@pytest.mark.asyncio
async def test_active_release_keeps_occupant_when_probe_reports_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """/slots がbusy(False)を返す → 解放されず、占有記録が保持されたままハッシュ衝突フォールバックへ進むこと。"""

    async def _fake_busy(base_url: str, timeout_seconds: float) -> bool | None:
        return False

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fake_busy)

    endpoints = _llama_cpp_endpoints(2)
    session_a = _unique_session_id("busy-a")
    session_c = _unique_session_id("busy-c")
    session_b = _unique_session_id("busy-b")
    try:
        llm.set_current_session(session_a)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        llm.set_current_session(session_c)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        llm.set_current_session(session_b)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        # a・cどちらの占有記録も解放されていない。
        assert ("main", session_a) in llm._STICKY_ASSIGNED_INDEX
        assert ("main", session_c) in llm._STICKY_ASSIGNED_INDEX
    finally:
        for sid in (session_a, session_c, session_b):
            llm.forget_session(sid)


@pytest.mark.asyncio
async def test_active_release_fails_safe_when_probe_result_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """/slots が確認不能（None、通信エラー等）を返す → フェイルセーフで解放されないこと。"""

    async def _fake_unknown(base_url: str, timeout_seconds: float) -> bool | None:
        return None

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fake_unknown)

    endpoints = _llama_cpp_endpoints(2)
    session_a = _unique_session_id("unknown-a")
    session_c = _unique_session_id("unknown-c")
    session_b = _unique_session_id("unknown-b")
    try:
        llm.set_current_session(session_a)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        llm.set_current_session(session_c)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        llm.set_current_session(session_b)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        assert ("main", session_a) in llm._STICKY_ASSIGNED_INDEX
        assert ("main", session_c) in llm._STICKY_ASSIGNED_INDEX
    finally:
        for sid in (session_a, session_c, session_b):
            llm.forget_session(sid)


@pytest.mark.asyncio
async def test_active_release_serializes_concurrent_assignment_to_avoid_double_booking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数の新規会話が同時に同じ占有先を「空いている」と判定して、二重に
    占有してしまわないこと（_STICKY_ASSIGNMENT_LOCK の回帰テスト）。

    _select_endpoint は GET /slots の await をまたぐ非同期関数になったため、
    ロックが無いと2つの新規会話が同時にidle判定を得て、両方が同じ接続先へ
    占有を確定させてしまう（sticky本来の排他性が崩れる）。probeモックの中で
    意図的に asyncio.sleep(0) を挟み、他コルーチンへの制御譲渡を誘発する。
    """

    async def _fake_idle_with_yield(base_url: str, timeout_seconds: float) -> bool | None:
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(llm, "_probe_llama_cpp_slots_idle", _fake_idle_with_yield)

    endpoints = _llama_cpp_endpoints(2)
    session_a = _unique_session_id("race-a")
    session_c = _unique_session_id("race-c")
    session_d = _unique_session_id("race-d")
    session_e = _unique_session_id("race-e")
    try:
        llm.set_current_session(session_a)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)
        llm.set_current_session(session_c)
        await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        async def _select_as(session_id: str):
            llm.set_current_session(session_id)
            return await llm._select_endpoint("main", endpoints, "sticky", min_idle_seconds=0.0)

        # d・eが同時に（両方とも空きが無い状態で）能動解放を試みる。probeが
        # 常にidle=Trueかつmin_idle_seconds=0.0（idle-ageガード無効）なので、
        # ロックが正しく直列化していれば「dが解放して占有→次にeが直前の
        # dの占有を解放して占有」という連鎖が起きうる（これ自体は本テストの
        # モック設定ゆえの現象であり、二重占有ではない）。重要なのは、
        # いかなる瞬間・いかなるindexも複数セッションに同時占有されない
        # ことだけ。
        await asyncio.gather(_select_as(session_d), _select_as(session_e))

        occupants_by_index: dict[int, set[str]] = {}
        for (r, i), sessions in llm._STICKY_ENDPOINT_OCCUPANTS.items():
            if r == "main" and sessions:
                occupants_by_index[i] = set(sessions)
        for index, sessions in occupants_by_index.items():
            assert len(sessions) <= 1, f"index={index} に複数セッションが二重占有されています: {sessions}"
    finally:
        for sid in (session_a, session_c, session_d, session_e):
            llm.forget_session(sid)
