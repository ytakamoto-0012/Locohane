"""src/llm.py の sticky ルーティング戦略（空きホスト優先割り当て）の回帰テスト。

sticky戦略は本来「会話単位で常に同じ接続先に固定する」ものだが、以前は
crc32ハッシュのみで決めていたため、複数の会話が同じ接続先に衝突する一方で
別の接続先が誰にも使われず空いている、という偏りが起こり得た。

本テストは、新しい会話が接続先を初めて選ぶ際に「他のどの会話も固定して
いない空き接続先」を優先すること、一度固定した接続先はforget_session()
されるまで変わらないこと、空きが無ければ従来通りハッシュで衝突を許容
することを確認する。
"""

from src import llm
from src.config import LLMEndpoint


def _endpoints(n: int) -> tuple[LLMEndpoint, ...]:
    return tuple(LLMEndpoint(base_url=f"http://host{i}/v1", api_key="dummy", model="m") for i in range(n))


def _unique_session_id(suffix: str) -> str:
    return f"test-sticky-{suffix}-{id(object())}"


def test_new_sessions_prefer_unoccupied_endpoints() -> None:
    endpoints = _endpoints(3)
    session_a = _unique_session_id("a")
    session_b = _unique_session_id("b")
    session_c = _unique_session_id("c")
    try:
        llm.set_current_session(session_a)
        picked_a = llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_b)
        picked_b = llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_c)
        picked_c = llm._select_endpoint("main", endpoints, "sticky")

        # 3接続先に対して3会話なので、空きがある限り全員別々の接続先になる。
        assert {picked_a.base_url, picked_b.base_url, picked_c.base_url} == {e.base_url for e in endpoints}
    finally:
        for session_id in (session_a, session_b, session_c):
            llm.forget_session(session_id)


def test_sticky_binding_persists_across_calls_even_if_endpoint_becomes_free() -> None:
    endpoints = _endpoints(2)
    session_a = _unique_session_id("persist-a")
    session_b = _unique_session_id("persist-b")
    try:
        llm.set_current_session(session_a)
        first = llm._select_endpoint("main", endpoints, "sticky")

        llm.set_current_session(session_b)
        llm._select_endpoint("main", endpoints, "sticky")

        # 他会話が終了して接続先が空いても、既に固定済みの会話は動かない。
        llm.forget_session(session_b)

        llm.set_current_session(session_a)
        second = llm._select_endpoint("main", endpoints, "sticky")
        assert second.base_url == first.base_url
    finally:
        llm.forget_session(session_a)
        llm.forget_session(session_b)


def test_sticky_falls_back_to_hash_when_no_endpoint_is_free() -> None:
    endpoints = _endpoints(1)
    session_id = _unique_session_id("single")
    try:
        llm.set_current_session(session_id)
        picked = llm._select_endpoint("main", endpoints, "sticky")
        assert picked.base_url == endpoints[0].base_url
    finally:
        llm.forget_session(session_id)


def test_forget_session_releases_occupied_endpoint_for_new_sessions() -> None:
    endpoints = _endpoints(2)
    session_a = _unique_session_id("release-a")
    session_b = _unique_session_id("release-b")
    session_c = _unique_session_id("release-c")
    try:
        llm.set_current_session(session_a)
        picked_a = llm._select_endpoint("main", endpoints, "sticky")
        llm.set_current_session(session_b)
        picked_b = llm._select_endpoint("main", endpoints, "sticky")
        assert {picked_a.base_url, picked_b.base_url} == {e.base_url for e in endpoints}

        # aを終了させ、その接続先を解放する。
        llm.forget_session(session_a)

        llm.set_current_session(session_c)
        picked_c = llm._select_endpoint("main", endpoints, "sticky")
        # cはaが使っていた（今は空いた）接続先を選ぶはず。
        assert picked_c.base_url == picked_a.base_url
    finally:
        for session_id in (session_a, session_b, session_c):
            llm.forget_session(session_id)
