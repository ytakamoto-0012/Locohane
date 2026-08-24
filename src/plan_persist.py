"""plan/plan_approved の変更を、ターン完了を待たずに即座に thread_store へ
永続化するためのフック。app.py（Chainlit エントリスクリプト）と src/tools.py
の双方から参照されるため、どちらにも属さないこの中立モジュールに置く
（src/ask_relay.py と同じ理由。`from app import ...` を tools.py 側からやると
chainlit run が app.py を未初期化の別モジュールとして再実行してしまい、
稼働中のハンドラを壊す。ask_relay.py 冒頭のdocstring参照）。

背景（2026-08-24 ユーザー報告）: 従来、plan/plan_approved はターン完了時
（_on_message_impl 末尾、work_dirと共に thread_store へまとめてスナップ
ショット）にしか永続化されておらず、work_dir と同じ「異常終了でスナップ
ショットに到達しないと保存されない」不具合を抱えていた
（app.py の _persist_work_dir docstring参照、2026-08-21に work_dir 側は
修正済みだった）。承認直後のターンが停止ボタン・通信エラー・思考ループ
上限・recursion_limit・計画却下等で異常終了すると、approve_plan で
承認済みになった最新状態が一度もDBへ書き込まれず、スレッド再開時に
古い（未承認の）metadataへ巻き戻ってPlan Modeへ戻ってしまっていた。
plan_approved を変更する各ツール（create_plan/approve_plan/
update_task_progress/lock_plan_mode/toggle_plan_mode_from_ui）が、
変更直後にここを呼んで即座に保存する。
"""

from __future__ import annotations

from typing import Awaitable, Callable

_persist_fn: Callable[[], Awaitable[None]] | None = None


def register_plan_persist(fn: Callable[[], Awaitable[None]] | None) -> None:
    """app.py の _on_app_startup 等から一度だけ呼び、実際の永続化処理
    （thread_store.save_thread を呼ぶ app.py 側の関数）を登録する。
    """
    global _persist_fn
    _persist_fn = fn


async def persist_plan_state() -> None:
    """登録済みの永続化処理を呼ぶ。未登録（app.py起動前・テスト環境等で
    register_plan_persist が一度も呼ばれていない場合）は何もしない
    （src/ask_relay.py の resolve_pending_ask 系と同じベストエフォート方針）。
    """
    if _persist_fn is not None:
        await _persist_fn()
