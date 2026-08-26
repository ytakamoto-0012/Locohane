"""dispatch_agent が使うバックグラウンドジョブ基盤。"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import chainlit as cl
import logging
import time
import traceback

from .. import subagent
from ..subagent import is_truncated_result

from . import _state
from ._script_job import _JOB_OUTPUT_TAIL_CHARS
from ._state import _IN_SUBAGENT, _SUBAGENT_AGENT_TYPE, _SUBAGENT_RUN_ID, ResolvedAgentType, _get_session_semaphore
from .write_scratch_note import _scratch_notes_path, _scratch_notes_path_for_run

logger = logging.getLogger(__name__)


def _append_scratch_note_hint(result: str) -> str:
    """打ち切られたサブエージェントの結果に、スクラッチノートの案内を追記する。

    write_scratch_note で途中経過が書き残されていれば、そのパスを案内する。
    委譲元はそちらを Read すれば、打ち切りにより未整理のまま返ってくる
    ツール結果の生データより、サブエージェント自身が構造化して書き残した
    内容を優先して参照できる。呼び出し時点で _SUBAGENT_RUN_ID がまだ
    現在の実行を指している必要があるため、dispatch_agent の finally で
    リセットする前に呼ぶこと。
    """
    if not is_truncated_result(result):
        return result
    path = _scratch_notes_path()
    if not path.is_file():
        return result
    return (
        f"{result}\n\n[このサブエージェントは write_scratch_note で途中経過を"
        f"書き残しています。Read で {path} を確認すると、打ち切り前に整理された"
        "内容が得られます。]"
    )


@dataclass
class _DispatchAgentJob:
    """dispatch_agent で起動したジョブの状態。

    モジュールレベルの _DISPATCH_AGENT_JOBS に job_id をキーとして保持する。
    _BackgroundJob（run_script_background 用）とは別のデータクラスにする。
    サブプロセスを持たず（process/stdout_chunks 等が不要）、代わりに
    run_subagent 専用の run_id（_SUBAGENT_RUN_ID に相当）を持つ点が異なる。
    """

    thread_id: str
    run_id: str  # このジョブ専用の _SUBAGENT_RUN_ID・_IN_SUBAGENT の値
    agent_type: str
    task_preview: str  # ログ・状況確認表示専用（task 先頭一部。全文は保持しない）
    started_at: float  # time.monotonic()
    status: str  # "running" | "completed" | "killed" | "error"
    result: str | None
    error_message: str | None
    max_iterations: int  # run_subagent に渡した反復上限（進捗表示用に保持）
    runner_task: "asyncio.Task | None" = None
    # run_subagent の on_iteration コールバックが更新する、現在の反復回数
    # （進捗push・check_dispatch_agent_job の running 分岐の表示に使う）。
    current_iteration: int = 0
    # check_dispatch_agent_job が直前に「実行中」ステータスを返した時刻
    # （time.monotonic()）。_BackgroundJob.last_polled_at と同じ理由づけ
    # （連続呼び出しの最短間隔をサーバー側で強制するため）。
    last_polled_at: float | None = None
    # dispatch_agent() がこのジョブの完了をまだ（安全上限内で）待っているとみなせる
    # 間は True。安全上限超過・停止ボタン等でジョブの完了を待たずに呼び出し元の
    # ターンが先に終わった場合、dispatch_agent() 側がジョブ完了より前に
    # （wait_forの例外捕捉時に同期的に）False へ変える。
    # _run_dispatch_agent_job の finally 節が job.runner_task の完了処理の
    # 一部として実行されるのは asyncio.wait_for が制御を戻すより必ず前になる
    # （wait_forは対象タスクの完了＝finally込みの完了を待って初めて返るため）。
    # そのため「dispatch_agent()側で完了後にTrueにする」という実装は間に合わず、
    # 逆に「初期値Trueで、フォールバック検知時にのみ即座にFalseへ落とす」という
    # 向きでなければ正しく機能しない。True のときのみ、_run_dispatch_agent_job の
    # finally節でmain_agent_tool_guard_call_countのリセットを行ってよい（結果を
    # 受け取るのがまだ同じターンだと保証できるため）。False の場合にリセットすると、
    # その間に同一セッションで始まった別の新しいターンのツールガードカウンタを
    # 横から無効化してしまう。
    turn_still_waiting: bool = True


# dispatch_agent のジョブレジストリ。_script_job._BACKGROUND_JOBS と同じ理由で
# プロセス内メモリのみで永続化はしない（アプリ再起動でジョブは失われるが、
# その内部で走っていた run_subagent 自体も再起動で失われるため実害はない）。
_DISPATCH_AGENT_JOBS: dict[str, _DispatchAgentJob] = {}

# app.py の SUBAGENT_MESSAGE_AUTHOR、frontend/src/utils/messageTree.ts の
# SUBAGENT_MESSAGE_AUTHOR と一致させる（UI側でメインエージェントの回答と
# 区別して表示するための cl.Message author 識別子）。
_SUBAGENT_MESSAGE_AUTHOR = "サブエージェント"


def _format_dispatch_agent_progress(job: "_DispatchAgentJob", job_id: str) -> str:
    """dispatch_agent ジョブの実行中の状況を表す文字列を組み立てる。

    _push_dispatch_agent_progress（人間向けのUI直接push）と
    check_dispatch_agent_job の running 分岐（フォールバック経路でのLLM向け
    応答）の両方から呼ぶ、表示フォーマット共通化のためのヘルパー。
    """
    elapsed = int(time.monotonic() - job.started_at)
    parts = [f"実行中です（経過 {elapsed} 秒・反復 {job.current_iteration}/{job.max_iterations} 回・job_id={job_id}）。"]
    note_path = _scratch_notes_path_for_run(job.run_id)
    if note_path.is_file():
        note_tail = note_path.read_text(encoding="utf-8", errors="replace")[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
        if note_tail:
            parts.append(f"[進捗メモ（末尾）]\n{note_tail}")
    return "\n".join(parts)


async def _push_dispatch_agent_progress(job: "_DispatchAgentJob", job_id: str) -> None:
    """dispatch_agent の実行中、人間向けに進捗をチャットへ直接pushする。

    cl.Message送信のみでLLM呼び出しを一切伴わないため、トークンを消費しない。
    「LLM自身がcheck_dispatch_agent_jobを繰り返し呼ぶとその都度LLM推論コストが
    かかる」というフィードバックを受け、dispatch_agent 自体は
    ジョブ完了までLLMに戻らずブロックする設計にしている（_run_dispatch_agent_job
    参照）。その間も人間が「動いているか」を確認できるよう、この関数が
    代わりに進捗を伝える。

    contextvarベースのChainlitセッションコンテキストは asyncio.create_task
    生成時点でコピーされる。このタスクは dispatch_agent の
    ツール呼び出しがまだ生きている（＝元のセッションコンテキストの中にいる）
    間に生成されるため、cl.Message(...).send() が正しいセッションへ届く。

    _run_dispatch_agent_job の finally で確実にキャンセルされる想定
    （ループ条件（job.status=="running"）だけに頼ると、ジョブ完了後も
    次の asyncio.sleep が明けるまでタスクが残留してしまうため）。

    metadata={"ephemeral_progress": True} を付ける（_push_background_job_progress
    と同じ）。ChatThreadDataLayer.create_step（src/thread_store.py）がこの
    フラグを見て永続化をスキップするため、スレッド再開時に「経過N秒・
    反復N/N回・job_id=xxx」という実行時点でしか意味を持たない古い進捗表示が
    復元されない（2026-08-21 ユーザー報告）。ライブ表示（emitter.send_step）
    には影響しない。サブエージェントの実際の発言（write_scratch_note等が
    生む本来のステップ）にはこのフラグを付けないため、それらは通常通り
    履歴に残る。
    """
    while job.status == "running":
        await asyncio.sleep(_state._DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS)
        if job.status != "running":
            break
        await cl.Message(
            content=_format_dispatch_agent_progress(job, job_id),
            author=_SUBAGENT_MESSAGE_AUTHOR,
            metadata={"ephemeral_progress": True},
        ).send()


async def _run_dispatch_agent_job(job: "_DispatchAgentJob", job_id: str, task: str, resolved: "ResolvedAgentType") -> None:
    """dispatch_agent のランナータスク本体。

    asyncio.create_task() はタスク生成時点のコンテキストをコピーし、以後
    タスク内部の contextvar.set()/reset() は呼び出し元（dispatch_agent）へは
    伝播しない。そのため _IN_SUBAGENT / _SUBAGENT_RUN_ID の設定・セマフォ確保・
    run_subagent() 呼び出し・_append_scratch_note_hint・
    main_agent_tool_guard_call_count のリセットは、このタスクの中で行う必要がある
    （呼び出し元側で行っても、値が呼び出し元のコンテキストにしか残らず
    このタスクの中からは見えない）。

    dispatch_agent は起動後、このタスクの完了を（安全上限まで）同じツール
    呼び出し内で待ち続ける設計だが、それでも run_subagent 自体はこのタスク
    として独立させておく。理由: (1) 安全上限を超えた場合に dispatch_agent
    側だけ制御をLLMへ返しつつ、このタスクは裏側で動き続けさせる必要がある
    （asyncio.shield で保護）。(2) 進捗push タスク（_push_dispatch_agent_progress）
    と並行させる必要がある。

    例外はここで確定的に捕捉し job.status="error" へ変換する。fire-and-forget
    の asyncio.Task 内で未捕捉の例外は「Task exception was never retrieved」
    という警告だけを残して静かに失われ、回収経路が無くなるため
    （_run_background_job と同じ理由）。asyncio.CancelledError
    （stop_dispatch_agent_job による task.cancel()）はここで握りつぶさず
    そのまま伝播させる。stop_dispatch_agent_job が先に status="killed" を
    設定済みのため、下記の各分岐は「killed を上書きしない」ガードを持つ
    （_run_background_job の status!="killed" ガードと同じレース対策）。
    """
    token = _IN_SUBAGENT.set(True)
    run_id_token = _SUBAGENT_RUN_ID.set(job.run_id)
    agent_type_token = _SUBAGENT_AGENT_TYPE.set(job.agent_type)
    progress_task = asyncio.create_task(_push_dispatch_agent_progress(job, job_id))

    def _on_iteration(iteration: int, max_iterations: int) -> None:
        job.current_iteration = iteration

    try:
        sem = _get_session_semaphore(_state._DISPATCH_AGENT_SEMAPHORES, _state._DISPATCH_AGENT_MAX_PARALLEL)
        if sem is not None:
            async with sem:
                result = await subagent.run_subagent(
                    task,
                    resolved.tools,
                    resolved.system_prompt,
                    _state._LLM_CONFIG,
                    job.max_iterations,
                    on_iteration=_on_iteration,
                    llm_timeout_max_retries=_state._DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES,
                )
        else:
            result = await subagent.run_subagent(
                task,
                resolved.tools,
                resolved.system_prompt,
                _state._LLM_CONFIG,
                job.max_iterations,
                on_iteration=_on_iteration,
                llm_timeout_max_retries=_state._DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES,
            )
        result = _append_scratch_note_hint(result)
        if job.status != "killed":
            job.result = result
            job.status = "completed"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - fire-and-forgetタスクの例外を消さず確定的に捕捉する
        logger.exception("dispatch_agent 失敗 (run_id=%s)", job.run_id)
        if job.status != "killed":
            job.status = "error"
            # str(e) が空文字列になる例外がある（例: asyncio.TimeoutError()）。
            # 「エラー: サブエージェントの実行に失敗しました: 」のように原因が
            # 一切分からない文言になっていた実例が本番ログで確認された
            # （issue/20260808_022438_dispatch_agent_background_failure.md）。
            # 空の場合は例外の型名で補い、必ず何らかの手がかりを残す。
            job.error_message = f"{str(e) or type(e).__name__}\n{traceback.format_exc()}"
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        _IN_SUBAGENT.reset(token)
        _SUBAGENT_RUN_ID.reset(run_id_token)
        _SUBAGENT_AGENT_TYPE.reset(agent_type_token)
        # 委譲が完了するたびに main_agent_tool_guard のカウンタをリセットする。
        # 1ターン内で複数回「調査→delegate」を繰り返す正当なケースを妨げないため。
        # ただし、安全上限フォールバックや停止ボタン等で呼び出し元のターンが
        # 既に終わっている場合（turn_still_waiting=False）はリセットしない。
        # ターンが終わっている＝同一セッションで別の新しいターンが既に進行中の
        # 可能性があり、そちらのガードカウンタを横から無効化してしまうため。
        if job.turn_still_waiting:
            cl.user_session.set("main_agent_tool_guard_call_count", None)


def _finalize_dispatch_agent_job_result(job: "_DispatchAgentJob", job_id: str) -> str:
    """終端状態（completed/killed/error）のジョブを最終結果文字列へ整形し、レジストリから取り除く。

    dispatch_agent（安全上限内に完了した場合）と
    check_dispatch_agent_job（終端状態を取得した場合）の両方から呼ぶ、
    ワンショット取得（一度取得したら同じ job_id は再利用できない）の共通処理。
    """
    if job.status == "completed":
        result = job.result or ""
        if job.agent_type == "planner":
            # create_plan の直前チェック（_state._PLAN_REQUIRE_PLANNER_DISPATCH）が
            # 消費するフラグ。ここで完了を記録しておくことで、dispatch_agent
            # が安全上限内に即応した場合・check_dispatch_agent_job経由で
            # 後続ターンに完了を取得した場合の両方をカバーする。
            # ただしplannerが情報不足（agents/planner.mdの指示で「情報不足」と
            # 明記して返す）でsteps/detail_markdownの草案を返さなかった場合は
            # フラグを立てず、メインエージェントがこの回答を無視して
            # create_planを呼んでもガードで止められるようにする。
            if "情報不足" in result:
                cl.user_session.set("planner_dispatched_since_plan", False)
                cl.user_session.set("planner_info_insufficient", True)
            else:
                cl.user_session.set("planner_dispatched_since_plan", True)
                cl.user_session.set("planner_info_insufficient", False)
    elif job.status == "killed":
        result = f"stop_dispatch_agent_job により強制終了されました。\n{job.result or '（強制終了時点で最終回答は未生成でした）'}"
    else:  # "error"
        result = f"エラー: サブエージェントの実行に失敗しました: {job.error_message}"
    _DISPATCH_AGENT_JOBS.pop(job_id, None)
    return result


def _purge_stale_dispatch_agent_jobs() -> None:
    """完了済みのまま check_dispatch_agent_job で回収されなかったジョブを掃除する。

    専用のクリーンアップループは持たず、dispatch_agent の呼び出しの
    度に opportunistic に走らせる（_purge_stale_background_jobs と同じ方針）。
    """
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _DISPATCH_AGENT_JOBS.items()
        if job.status != "running" and now - job.started_at > _state._DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS
    ]
    for job_id in stale:
        _DISPATCH_AGENT_JOBS.pop(job_id, None)


def _dispatch_agent_job_started_message(job_id: str) -> str:
    """dispatch_agent が安全上限（background_inline_wait_max_seconds）に
    達してもなおジョブが終わっていない場合に、フォールバックとしてLLMへ返す案内文。

    通常はこの文言に到達しない（dispatch_agent 自身がジョブ完了まで
    ツール呼び出し内で待ち続け、最終結果を直接返すため）。到達するのは、
    設定された安全上限を超えるほど長時間のジョブだけである。
    _background_job_started_message と同じ理由づけ（処理時間の長さ自体は
    打ち切る理由にならないことを明記し、モデルの自発的な早期キャンセルを防ぐ）。
    """
    return (
        f"バックグラウンドで実行中です（job_id={job_id}）。長時間かかっているため、"
        "いったんこのターンを終えて制御を返します（ジョブ自体は裏側で動き続けます）。\n"
        "完了確認・結果取得には check_dispatch_agent_job（job_id指定）を使うこと。"
        "処理に時間がかかっていること自体は打ち切る理由にはならない。"
        "ユーザーから明示的に中断・キャンセルを指示された場合にのみ"
        "stop_dispatch_agent_job（job_id指定）を使うこと。"
    )


async def cancel_dispatch_agent_jobs_for_thread(thread_id: str) -> None:
    """指定セッションで実行中の dispatch_agent ジョブを全て強制終了する。

    app.py の on_stop（自セッション停止）・_stop_thread_generating
    （閲覧側からのcross-session停止）から呼ぶ。これらはメイングラフの
    タスク（session.current_task）しかキャンセルできず、dispatch_agent が
    起動した job.runner_task は asyncio.shield() で保護されているため
    その影響を受けない。放置すると、停止ボタンを押してバッジが「停止」に
    なった後もサブエージェントのLLM呼び出し・進捗push
    （_push_dispatch_agent_progress）が裏側で動き続ける（2026-08-26
    ユーザー報告。実測ログ: 停止ボタン押下から約107秒後にジョブが
    「11回で完了」と自然終了していた）。

    stop_dispatch_agent_job ツールと同じ job.status="killed" +
    runner_task.cancel() を、対象thread_idの実行中ジョブ全件へ適用する。
    ツール版と異なり、結果を待つ呼び出し元（LLM）がもう存在しない
    （ターン自体が終わっている）ため、進捗メモの案内文などは組み立てず、
    レジストリから取り除くだけでよい。
    """
    targets = [
        (job_id, job)
        for job_id, job in _DISPATCH_AGENT_JOBS.items()
        if job.thread_id == thread_id and job.status == "running"
    ]
    for job_id, job in targets:
        job.status = "killed"
        logger.warning(
            "dispatch_agent: 停止ボタンにより強制終了します (run_id=%s, job_id=%s, iter=%d/%d)",
            job.run_id,
            job_id,
            job.current_iteration,
            job.max_iterations,
        )
        if job.runner_task is not None:
            job.runner_task.cancel()
            try:
                await job.runner_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - 停止処理自体は他ジョブの後始末を妨げない
                logger.debug(
                    "dispatch_agent強制終了中に例外が発生しました (job_id=%s)",
                    job_id,
                    exc_info=True,
                )
        _DISPATCH_AGENT_JOBS.pop(job_id, None)


def _resolve_dispatch_agent_job(job_id: str) -> "_DispatchAgentJob | str":
    """job_id を現在のセッション所有のジョブへ解決する（他セッションは拒否）。

    _resolve_job と同じ方針。
    """
    job = _DISPATCH_AGENT_JOBS.get(job_id)
    if job is None:
        return f"エラー: job_id '{job_id}' は見つかりません（既に取得済みか、無効なIDです）。"
    thread_id = cl.user_session.get("thread_id") or ""
    if job.thread_id != thread_id:
        return f"エラー: job_id '{job_id}' は現在のセッションのものではありません。"
    return job
