"""計画承認（approve_plan）ボタンのクロスセッション引き継ぎの回帰テスト。

背景（2026-08-21 ユーザー報告）: 左サイドバーでの会話切り替え
（frontend/src/components/Sidebar.tsx の goToThread）はフルページリロード
のため、approve_plan の承認待ち（cl.AskActionMessage.send()）を出したまま
別スレッドへ移動して戻ってくると、Chainlit的には元セッションとは完全な
別セッションになり（sessionIdState が毎回 uuid4() で新規発行され直すため）、
承認ボタンがどこにも表示されなくなる。

src.tools._ask_action_with_cross_session_relay（元セッション側）と
app._register_pending_plan_ask/_relay_pending_plan_ask（app.on_chat_resume
から起動される、戻ってきた新セッション側）が asyncio.Future を介して
連携することで、先に応答が来た方を採用する。
"""

import asyncio

import pytest

import app
import src.tools as tools


class _ScriptedAskActionMessage:
    """cl.AskActionMessage の差し替え。呼び出しごとに behaviors から1つずつ
    非同期の振る舞いを消費する（呼び出し順=クラスインスタンス化順ではなく
    実際に send() が実行された順、に一致させるため behaviors はテスト側で
    呼び出し順を意識して積む）。
    """

    behaviors: list = []

    def __init__(self, content, actions, timeout=90, **kwargs):
        self.content = content
        self.actions = actions
        self.timeout = timeout

    async def send(self):
        behavior = _ScriptedAskActionMessage.behaviors.pop(0)
        return await behavior()


@pytest.mark.asyncio
async def test_local_session_wins_when_it_answers_first(monkeypatch) -> None:
    """通常時（スレッド切り替えが起きない）は今まで通り、送信元セッション自身の
    AskActionMessage の応答がそのまま採用され、_pending_plan_asks も残らない。
    """
    thread_id = "local-wins-thread"
    app._pending_plan_asks.pop(thread_id, None)

    response = {"name": "approve", "payload": {"value": "approve"}, "label": "approve", "tooltip": "", "forId": "x", "id": "y"}

    async def local_send_immediate():
        return response

    _ScriptedAskActionMessage.behaviors = [local_send_immediate]
    monkeypatch.setattr(tools.cl, "AskActionMessage", _ScriptedAskActionMessage)

    actions = [tools.cl.Action(name="approve", payload={"value": "approve"}, label="approve")]
    res = await tools._ask_action_with_cross_session_relay(thread_id, "content", actions, 30)

    assert res == response
    assert thread_id not in app._pending_plan_asks


@pytest.mark.asyncio
async def test_relayed_session_answer_unblocks_dead_original_session(monkeypatch) -> None:
    """元セッション側の AskActionMessage.send() が（切断済みで）応答不能のまま
    ハングしていても、app._relay_pending_plan_ask（=戻ってきた新セッション側が
    on_chat_resume 経由で呼ぶもの）が答えると、元セッション側の待機がそちらの
    回答で解決され、ハングしていた方はキャンセルされる。
    """
    thread_id = "relay-wins-thread"
    app._pending_plan_asks.pop(thread_id, None)

    local_started = asyncio.Event()
    hang_forever = asyncio.Event()
    cancelled = {"value": False}

    async def local_send_hangs():
        local_started.set()
        try:
            await hang_forever.wait()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    relay_response = {"name": "approve", "payload": {"value": "approve"}, "label": "approve", "tooltip": "", "forId": "x", "id": "y"}

    async def relay_send_immediate():
        return relay_response

    _ScriptedAskActionMessage.behaviors = [local_send_hangs, relay_send_immediate]
    monkeypatch.setattr(tools.cl, "AskActionMessage", _ScriptedAskActionMessage)
    monkeypatch.setattr(app.cl, "AskActionMessage", _ScriptedAskActionMessage)

    actions = [tools.cl.Action(name="approve", payload={"value": "approve"}, label="approve")]
    local_call_task = asyncio.create_task(
        tools._ask_action_with_cross_session_relay(thread_id, "content", actions, 30)
    )

    await asyncio.wait_for(local_started.wait(), timeout=1)
    assert thread_id in app._pending_plan_asks

    # 戻ってきた新セッション（on_chat_resume）が同じ内容を出し直して回答する。
    await app._relay_pending_plan_ask(thread_id)

    res = await asyncio.wait_for(local_call_task, timeout=1)

    assert res == relay_response
    assert cancelled["value"] is True, "応答不能になった元セッション側の待機がキャンセルされていない"
    assert thread_id not in app._pending_plan_asks


@pytest.mark.asyncio
async def test_relay_pending_plan_ask_is_noop_without_registration() -> None:
    """未登録・解決済みのスレッドに対しては何もしない（呼び出しても安全）。"""
    thread_id = "no-such-thread"
    app._pending_plan_asks.pop(thread_id, None)
    await app._relay_pending_plan_ask(thread_id)  # 例外にならないこと
    assert thread_id not in app._pending_plan_asks
