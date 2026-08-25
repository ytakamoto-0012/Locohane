"""update_task_progress ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl
import logging

from ..plan_persist import persist_plan_state

from ._plan_render import _render_plan_payload

logger = logging.getLogger(__name__)


_VALID_TASK_STATUSES = ("pending", "in_progress", "completed")

@tool
async def update_task_progress(step_index: int, status: str) -> str:
    """実行計画中のステップの進捗状態を更新し、表示中のチェックリストへ反映する。

    ステップの実行前に "in_progress"、完了後に "completed" を設定してユーザーに
    進捗を見せること。"in_progress" の間はチェックリスト上に content の代わりに
    create_plan で渡した activeForm が表示される。同時に "in_progress" にする
    ステップは1つまでにすること。全ステップが completed になると計画は完了した
    ものとみなし、承認状態を解除する（承認は作成済み計画の実行に限定した
    スコープのため、完了後の無関係な run_script/execute_python_code は
    再びブロックされる）。

    run_script_background/execute_python_code_background に対応するステップは、
    ジョブを起動しただけの時点では completed にしないこと。check_script_job
    自体は読み取り専用でいつでも呼べるが、起動直後に completed にすると
    承認状態が解除され Plan Mode 表示になるため、まだジョブが実行中なのに
    「計画をやり直す必要がある」と誤解し、不要な create_plan/approve_plan を
    繰り返す原因になる。check_script_job で最終結果（running 以外の状態）を
    確認できてから completed にすること。

    Args:
        step_index: create_plan で渡した steps のインデックス（0始まり）。
        status: "pending" | "in_progress" | "completed" のいずれか。

    Returns:
        更新内容を説明する短いテキスト。計画が未作成、step_index が範囲外、
        status が不正な値の場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    plan = cl.user_session.get("plan")
    if not plan:
        return "エラー: 計画がありません。先に create_plan を呼んでください。"
    if status not in _VALID_TASK_STATUSES:
        return f"エラー: status は {_VALID_TASK_STATUSES} のいずれかを指定してください: {status}"
    if not (0 <= step_index < len(plan)):
        return f"エラー: step_index が範囲外です（0〜{len(plan) - 1}）: {step_index}"

    plan[step_index]["status"] = status
    cl.user_session.set("plan", plan)
    finished = all(s["status"] == "completed" for s in plan)
    if finished:
        cl.user_session.set("plan_approved", False)
    # ステップ進捗も plan_approved と同じ理由で即時persistする。完了時に
    # 限定すると、途中のステップ更新後にターンが異常終了した場合（停止
    # ボタン・通信エラー等）、再開時のチェックリスト表示が前回persist時点
    # （create_plan直後など）まで巻き戻ってしまう。
    await persist_plan_state()

    message: cl.Message | None = cl.user_session.get("plan_message")
    if message is not None:
        message.content = _render_plan_payload(plan, finished=finished, approved=cl.user_session.get("plan_approved", False))
        await message.update()

    logger.info("update_task_progress: step=%d status=%s finished=%s", step_index, status, finished)
    label = plan[step_index]["content"]
    suffix = "\n計画は全ステップ完了しました。" if finished else ""
    return f"ステップ{step_index}「{label}」を {status} に更新しました。{suffix}"
