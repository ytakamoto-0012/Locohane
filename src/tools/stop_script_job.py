"""stop_script_job ツール。"""

from __future__ import annotations

from langchain_core.tools import tool

from . import _script_job
from ._script_job import _format_job_result, _resolve_job


@tool
async def stop_script_job(job_id: str) -> str:
    """run_script_background で起動したジョブを強制終了する。

    ユーザーから明示的に中断・キャンセル・停止を指示された場合にのみ使う
    こと。処理に時間がかかっていること自体（check_script_job が実行中を
    返し続けること）は、自分の判断で打ち切ってよい理由にはならない。
    強制終了時点までの標準出力・標準エラーを添えて結果を返し、登録から
    削除する。他セッションが起動した job_id は操作できない。

    Args:
        job_id: run_script_background の戻り値に含まれるID。

    Returns:
        強制終了結果を表す文字列。job_id が不明・他セッションのものである、
        または既に終了済みの場合は「エラー: ...」形式の文字列。
    """
    resolved = _resolve_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status != "running":
        return f"エラー: job_id '{job_id}' は既に終了しています（status={job.status}）。" "check_script_job で結果を取得してください。"

    job.status = "killed"
    try:
        job.process.kill()
    except ProcessLookupError:
        pass
    if job.runner_task is not None:
        await job.runner_task

    result = _format_job_result(job)
    _script_job._BACKGROUND_JOBS.pop(job_id, None)
    return f"強制終了しました。\n{result}"
