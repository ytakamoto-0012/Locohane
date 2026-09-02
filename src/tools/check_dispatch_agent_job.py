"""check_dispatch_agent_job ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import time

from . import _state
from ._dispatch_agent_job import _finalize_dispatch_agent_job_result, _format_dispatch_agent_progress, _resolve_dispatch_agent_job


@tool
async def check_dispatch_agent_job(job_id: str) -> str:
    """dispatch_agent で起動したジョブの状況・結果を確認する。

    通常は dispatch_agent 自身がジョブ完了まで待ってから最終回答を
    直接返すため、このツールを呼ぶ必要は無い。呼ぶのは、dispatch_agent
    が安全上限に達して job_id を返した場合（＝よほど長時間のジョブ）のみ。

    実行中であれば経過秒数・反復回数と、write_scratch_note で書き残した
    進捗メモ（あれば、末尾最大4000文字）を返す。完了・強制終了・エラーの
    いずれかで終わっていれば最終回答（dispatch_agent と同じ形式のテキスト）
    を返し、以降は同じ job_id を指定できなくなる（登録から削除される）。
    他セッションが起動した job_id は参照できない。

    サブエージェントの内部進捗を取得する手段はスクラッチノート以外に無い。
    agent_type によっては（write_scratch_note を持たない種別、または一度も
    書いていない場合）実行中の進捗欄が空のままとなるが、これは異常ではない。

    実行中が返ってきた場合、数秒間隔で連続して呼び直さないこと。経過を
    ユーザーへ一言伝えたらそのターンを終えて次のユーザー発言を待つか、
    十分な間隔（数十秒〜）を空けてから改めて呼ぶこと。処理に時間が
    かかっていること自体は異常でも打ち切る理由でもない。この指示に反して
    短い間隔で呼び直した場合はサーバー側で拒否され、「まだ確認間隔が
    短すぎます」のような拒否メッセージが返る。

    Args:
        job_id: dispatch_agent の戻り値に含まれるID。

    Returns:
        状況または最終結果を表す文字列。job_id が不明・他セッションのもので
        ある場合、または直前の「実行中」応答から
        [subagent].background_min_poll_interval_seconds 未満しか経っていない
        場合は「エラー: ...」/拒否メッセージ形式の文字列。
    """
    resolved = _resolve_dispatch_agent_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status == "running":
        now = time.monotonic()
        if (
            _state._DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS > 0
            and job.last_polled_at is not None
            and (now - job.last_polled_at) < _state._DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS
        ):
            wait_remaining = int(_state._DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS - (now - job.last_polled_at)) + 1
            return _state._DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE.format(
                wait_remaining=wait_remaining,
                job_id=job_id,
                min_interval=_state._DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS,
            )
        job.last_polled_at = now
        return _format_dispatch_agent_progress(job, job_id)

    return _finalize_dispatch_agent_job_result(job, job_id)
