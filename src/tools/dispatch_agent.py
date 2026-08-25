"""dispatch_agent ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import asyncio
import chainlit as cl
import logging
import time
import uuid

from . import _dispatch_agent_job
from . import _state
from ._dispatch_agent_job import _DispatchAgentJob, _dispatch_agent_job_started_message, _finalize_dispatch_agent_job_result, _purge_stale_dispatch_agent_jobs, _run_dispatch_agent_job
from ._path_memory_helpers import _resolve_path_memory_tokens_in_text
from ._plan_render import current_plan_status_text
from ._workdir import _resolve_workdir

logger = logging.getLogger(__name__)


def _task_with_work_dir_hint(task: str) -> str:
    """dispatch_agent の task 文の先頭に、実際の作業ディレクトリを事実として付与する。

    委譲元（メインエージェント）が task 文中に書いたパスは、モデルの記憶からの
    書き起こしで誤っていたり未検証だったりしうる（本番実例: 存在しない
    "E:\\akiyo\\レシピ\\md" を渡され、サブエージェントが自力でGlobして偶然
    見つけた無関係な "E:\\akiyo\\md" を答えとして扱ってしまった）。サブエージェントは
    メインエージェントの会話文脈を一切持たないため、この事実を照合する独自の
    手掛かりを持たない。_resolve_workdir() が返す確認済みの作業ディレクトリを
    task 本文の先頭へ機械的に付与することで、サブエージェント自身のツール呼び出しの
    挙動やプロンプト内指示への追従性に頼らず、常に正しいground truthを与える。

    _resolve_workdir() は init_tools() 未実行時のみ RuntimeError を送出するが、
    ヒント注入1つの失敗で dispatch_agent 全体を失敗させないよう、ここで
    握りつぶして task をそのまま返す。
    """
    try:
        work_dir = _resolve_workdir()
    except RuntimeError:
        return task
    return f"作業ディレクトリ: {work_dir}\n\n{task}"

def _task_with_plan_hint(task: str) -> str:
    """dispatch_agent の task 文の先頭に、現在の実行計画を事実として付与する。

    本番インシデント: create_plan の detail_markdown（プロース）は1ファイル
    成果物を約束していたが、実際の steps は月間・週間版を別々の
    dispatch_agent 呼び出しへ分割委譲しており、委譲されたworkerは
    「月間版を作って」としか伝えられず計画全体の約束を知る術が無かった。
    _task_with_work_dir_hint と同じ「委譲元の書き起こしを信用せず、
    cl.user_session の ground truth を機械的に注入する」パターンをここにも
    適用し、委譲元がtask文に計画全体を書き忘れてもサブエージェントが
    常に計画全体・現在位置を認識できるようにする。
    """
    status = current_plan_status_text()
    if not status:
        return task
    return f"[実行計画（進行中・最優先タスク）]\n{status}\n\n{task}"

@tool
async def dispatch_agent(task: str, agent_type: str) -> str:
    """タスクを独立したサブエージェントへ委譲し、最終回答のみを受け取る。

    調査や複数ステップの下調べなど、詳細な思考過程やツール呼び出しの
    経緯までは自分の会話履歴に残す必要が無い作業に使う。サブエージェントは
    agent_type で選んだ種別のツールセットを使って自律的に作業するが、
    その内部の思考過程・ツール呼び出しはあなたの会話履歴には一切残らず、最終回答の
    テキストのみが返る（ログファイルには内部の記録が残る）。run_script が
    呼ばれた場合、その承認確認はサブエージェントの実行中にそのまま
    ユーザーへ表示される。数十〜数百件規模のファイル（画像等）を扱う調査は、
    年・サブフォルダ等の単位でこのツールへ分割委任すると効率的。
    サブエージェントはさらに別のサブエージェントへ委譲することはできない。

    このターン自体は（下記の安全上限に達しない限り）完了までブロックされる
    （Chainlit UI上は「実行中」表示が続く）。待っている間、人間向けに
    チャットへ直接進捗（経過時間・反復回数・進捗メモ）が自動で通知されるため、
    自分から何度も呼び直してポーリングする必要は無い（進捗はコード側が
    直接チャットへ送るため、LLMの呼び出し回数・トークン消費は増えない）。
    1回の呼び出しでサブエージェントの最終回答がそのまま返る。

    設定した安全上限（[subagent].background_inline_wait_max_seconds）を超えても
    なお完了しない場合に限り、job_id を含む案内文を返してこのターンを終える
    （ジョブ自体は裏側で動き続ける）。この場合のみ、後続ターンで
    check_dispatch_agent_job（結果取得）・stop_dispatch_agent_job（明示的な
    中断指示があった場合のみ）を使う。

    Args:
        task: サブエージェントに依頼したいタスクの説明。必要な背景情報・
            期待する出力形式を過不足なく書くこと（サブエージェントは
            この会話の文脈を一切知らない）。対象パスは記憶から書き起こさず、
            直前の glob_file.py 等で得た `@N`（パスメモリー参照）をそのまま
            文中に埋め込んでよい（解決できるものは実パスへ自動置換される）。
        agent_type: 使用するサブエージェントの種別名（必須、暗黙の既定値は
            無い）。利用可能な種別とそれぞれの用途はシステムプロンプトの
            一覧を参照し、タスクの内容に合った種別を毎回明示的に選ぶこと。

    Returns:
        通常はサブエージェントの最終回答テキスト。安全上限に達した場合のみ
        job_id を含む案内文字列。init_tools() が未実行の場合、agent_type が
        不明な場合は起動前に「エラー: ...」を返す（この場合 job は作られない）。
        サブエージェントの実行自体に失敗した場合も、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    if _state._LLM_CONFIG is None:
        return "エラー: init_tools() が未実行です"
    resolved = _state._AGENT_TYPES.get(agent_type)
    if resolved is None:
        available = ", ".join(sorted(_state._AGENT_TYPES)) or "（登録なし）"
        return f"エラー: 不明な agent_type '{agent_type}' です。利用可能: {available}"
    task = _resolve_path_memory_tokens_in_text(task)
    task = _task_with_work_dir_hint(task)
    task = _task_with_plan_hint(task)
    logger.info("dispatch_agent: task=%r agent_type=%r", task, agent_type)

    _purge_stale_dispatch_agent_jobs()

    job = _DispatchAgentJob(
        thread_id=cl.user_session.get("thread_id") or "",
        run_id=uuid.uuid4().hex,
        agent_type=agent_type,
        task_preview=task[:200],
        started_at=time.monotonic(),
        status="running",
        result=None,
        error_message=None,
        max_iterations=_state._SUBAGENT_MAX_ITERATIONS,
    )
    job_id = uuid.uuid4().hex[:12]
    job.runner_task = asyncio.create_task(_run_dispatch_agent_job(job, job_id, task, resolved))
    _dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id] = job

    # asyncio.shield: このwait_forがタイムアウトして例外を送出しても、
    # job.runner_task 自体はキャンセルされず裏側で動き続ける
    # （安全上限超過時のフォールバック挙動の要）。0以下は無期限待ち。
    wait_timeout = _state._DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS if _state._DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS > 0 else None
    try:
        await asyncio.wait_for(asyncio.shield(job.runner_task), timeout=wait_timeout)
    except asyncio.TimeoutError:
        # job.runner_task はこの時点でまだ完了していない（完了済みならTimeoutErrorは
        # 発生しない）ため、ジョブ側のfinally節が実行されるより確実に前にこの
        # 代入が間に合う。
        job.turn_still_waiting = False
        logger.warning(
            "dispatch_agent: 安全上限(%s秒)に達したため job_id を返してターンを終えます: job_id=%s",
            wait_timeout,
            job_id,
        )
        return _dispatch_agent_job_started_message(job_id)
    except asyncio.CancelledError:
        # 停止ボタン等でこのターン自体がキャンセルされた場合。job.runner_task は
        # shieldにより生き続けるため、TimeoutError時と同様にリセットを禁止する。
        job.turn_still_waiting = False
        raise

    return _finalize_dispatch_agent_job_result(job, job_id)
