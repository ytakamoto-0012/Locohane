"""check_script_job ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import time

from . import _state
from ._script_job import _finalize_script_job_result, _format_background_job_progress, _resolve_job


@tool
async def check_script_job(job_id: str) -> str:
    """run_script_background/execute_python_code_background で起動したジョブの
    状況・結果を確認する。

    通常は run_script_background/execute_python_code_background 自身が
    ジョブ完了まで待ってから最終結果を直接返すため、このツールを呼ぶ必要は
    無い。呼ぶのは、それらが安全上限に達して job_id を返した場合
    （＝よほど長時間のジョブ）のみ。

    実行中であれば経過秒数と、現時点までの標準出力・標準エラーの末尾
    （最大4000文字）を返す。完了・失敗・タイムアウト・強制終了のいずれかで
    終わっていれば、run_script と同じ形式
    （"[終了コード] N" に続けて "[標準出力]"/"[標準エラー]"）で最終結果を返し、
    以降は同じ job_id を指定できなくなる（登録から削除される）。
    他セッションが起動した job_id は参照できない。

    実行中（"実行中です（経過 N 秒）。"）が返ってきた場合、数秒間隔で連続
    して呼び直さないこと。経過をユーザーへ一言伝えたらそのターンを終えて
    次のユーザー発言を待つか、十分な間隔（数十秒〜）を空けてから改めて
    呼ぶこと。処理に時間がかかっていること自体は異常でも打ち切る理由でもない。
    なお、この指示に反して短い間隔で呼び直した場合はサーバー側で拒否され、
    「まだ確認間隔が短すぎます」のような拒否メッセージが返る（既定の最小
    間隔は20秒）。

    Args:
        job_id: run_script_background/execute_python_code_background の
            戻り値に含まれるID。

    Returns:
        状況または最終結果を表す文字列。job_id が不明・他セッションのもので
        ある場合、または直前の「実行中」応答から
        background_min_poll_interval_seconds 未満しか経っていない場合は
        「エラー: ...」/拒否メッセージ（[scripts].background_min_poll_message、
        既定は「まだ確認間隔が短すぎます...」）形式の文字列。
    """
    resolved = _resolve_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status == "running":
        now = time.monotonic()
        if (
            _state._SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS > 0
            and job.last_polled_at is not None
            and (now - job.last_polled_at) < _state._SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS
        ):
            wait_remaining = int(_state._SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS - (now - job.last_polled_at)) + 1
            return _state._SCRIPT_BACKGROUND_MIN_POLL_MESSAGE.format(
                wait_remaining=wait_remaining,
                job_id=job_id,
                min_interval=_state._SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS,
            )
        job.last_polled_at = now
        return _format_background_job_progress(job, job_id)

    return _finalize_script_job_result(job, job_id)
