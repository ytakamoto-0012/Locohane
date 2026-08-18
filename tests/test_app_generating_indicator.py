"""左サイドバー「生成中インジケーター」用の _generating_thread_ids 管理の回帰テスト。

背景: 会話の生成中に別スレッドをクリックすると、フロントは完全に別セッションと
して新規接続し直すため、元スレッドのターンはドキュメント通りバックグラウンドで
継続する（on_chat_end参照）にもかかわらず、それを他のセッションから知る手段が
無く「会話が終了したように見える」というユーザー報告（2026-08-19）があった。
on_message を薄いラッパー（マーキング）＋ _on_message_impl（実処理）に分割し、
プロセス全体の一時集合 _generating_thread_ids で追跡する。
"""

import pytest

import app


class _FakeUserSession:
    def __init__(self, thread_id: str | None):
        self._data = {"thread_id": thread_id}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def test_mark_and_unmark_are_idempotent() -> None:
    app._generating_thread_ids.clear()
    app._mark_thread_generating("t1")
    app._mark_thread_generating("t1")  # 二重マークしても壊れない
    assert app._generating_thread_ids == {"t1"}

    app._unmark_thread_generating("t1")
    app._unmark_thread_generating("t1")  # 未登録に対する解除もエラーにならない
    assert app._generating_thread_ids == set()


@pytest.mark.asyncio
async def test_on_message_marks_during_impl_and_unmarks_on_success(monkeypatch) -> None:
    app._generating_thread_ids.clear()
    monkeypatch.setattr(app.cl, "user_session", _FakeUserSession("t1"))

    seen_during_impl = {}

    async def fake_impl(message):
        seen_during_impl["marked"] = "t1" in app._generating_thread_ids

    monkeypatch.setattr(app, "_on_message_impl", fake_impl)

    await app.on_message(object())

    assert seen_during_impl["marked"] is True
    assert "t1" not in app._generating_thread_ids


@pytest.mark.asyncio
async def test_on_message_unmarks_even_when_impl_raises(monkeypatch) -> None:
    app._generating_thread_ids.clear()
    monkeypatch.setattr(app.cl, "user_session", _FakeUserSession("t1"))

    async def failing_impl(message):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "_on_message_impl", failing_impl)

    with pytest.raises(RuntimeError):
        await app.on_message(object())

    assert "t1" not in app._generating_thread_ids


@pytest.mark.asyncio
async def test_on_message_unmarks_on_cancellation() -> None:
    """停止ボタン押下（session.current_task.cancel()）相当のCancelledErrorでも
    finallyで確実に解除されること。
    """
    app._generating_thread_ids.clear()

    class _Session:
        def get(self, key, default=None):
            return "t1" if key == "thread_id" else default

        def set(self, key, value):
            pass

    orig_session = app.cl.user_session
    app.cl.user_session = _Session()
    try:

        async def cancelled_impl(message):
            raise __import__("asyncio").CancelledError()

        orig_impl = app._on_message_impl
        app._on_message_impl = cancelled_impl
        try:
            with pytest.raises(__import__("asyncio").CancelledError):
                await app.on_message(object())
        finally:
            app._on_message_impl = orig_impl
    finally:
        app.cl.user_session = orig_session

    assert "t1" not in app._generating_thread_ids


@pytest.mark.asyncio
async def test_on_message_noop_when_thread_id_missing(monkeypatch) -> None:
    """_setup()/on_chat_start完了前に何らかの理由でthread_id未設定のまま
    呼ばれた異常系でも、マーキング処理自体が例外にならないこと。
    """
    app._generating_thread_ids.clear()
    monkeypatch.setattr(app.cl, "user_session", _FakeUserSession(None))

    called = []

    async def fake_impl(message):
        called.append(True)

    monkeypatch.setattr(app, "_on_message_impl", fake_impl)

    await app.on_message(object())

    assert called == [True]
    assert app._generating_thread_ids == set()
