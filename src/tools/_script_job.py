"""run_script_background/execute_python_code_background が共有するバックグラウンドジョブ基盤。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import chainlit as cl

from . import _state
from ._duplicate_guard import _track_failure_streak
from ._path_memory_helpers import _resolve_path_memory_token
from ._python_fs_guard import _register_exec_output_files
from ._safe_path import _resolve_script_filename
from ._state import _AGENT_TYPE_RUN_SCRIPT_ALLOWLIST, _SUBAGENT_AGENT_TYPE
from ._subprocess_env import _run_script_guard_env
from ._workdir import _resolve_workdir, _restrict_default_workdir

logger = logging.getLogger(__name__)


def _prepare_script_execution(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> tuple[list[str], Path] | str:
    """run_script / run_script_background 共通の前処理。

    引数のパスメモリー解決 → スクリプトパス解決 → 作業ディレクトリ解決 →
    計画承認チェック → 実行コマンド組み立て、までを行う。
    (skill_name, script_filename) が config.ini の
    [scripts].plan_approval_exempt_scripts に登録されている場合は
    計画未承認でも実行できる。

    メインエージェント自身の直接呼び出し回数ガード（[main_agent_tool_guard]）は
    ここではなく ImageAwareToolNode.invoke/ainvoke（_guard_main_agent_tool_limit）
    側でツール実行前にかかるため、ここには現れない。

    Returns:
        検証に成功すれば (cmd, workdir) のタプル。失敗すれば
        「エラー: ...」形式の文字列（呼び出し側はそのまま返せばよい）。
    """
    current_agent_type = _SUBAGENT_AGENT_TYPE.get()
    allowed = _AGENT_TYPE_RUN_SCRIPT_ALLOWLIST.get(current_agent_type) if current_agent_type else None
    if allowed is not None and skill_name not in allowed and (skill_name, script_filename) not in allowed:
        return (
            f"エラー: agent_type=\"{current_agent_type}\" から呼び出せる run_script のスキル/スクリプトは "
            f"{sorted(str(a) for a in allowed)} に限定されています"
            f"（skill={skill_name}, script={script_filename} は対象外）。"
            "ファイルの内容確認が必要な場合は、委譲元に対応するサブエージェント"
            "（office文書/PDFなら analyze-docs、書き込みが要るなら worker）へ"
            "改めて委譲するよう伝えてください。"
        )

    args = script_args or []
    # args 内の各要素で `@N`（パスメモリー参照）を実パスへ解決する。
    # 対象外の文字列（トークン形式でない）はそのまま通す。
    resolved_args = []
    for a in args:
        resolved, error = _resolve_path_memory_token(a)
        if error:
            return f"エラー: {error}"
        resolved_args.append(resolved)
    args = resolved_args
    try:
        script_path = _resolve_script_filename(skill_name, script_filename)
    except ValueError as e:
        return f"エラー: {e}"
    # work_dir未設定・書き込み不可等でdefault_workdirへフォールバックする
    # 場合、cwdをdefault_workdir直下ではなく`_tmp_<thread_id>`にする
    # （_restrict_default_workdir参照。サーバー側共有ディレクトリである
    # default_workdir直下に、相対パス書き込みの既定の置き場として
    # 全セッション共通で成果物が積まれるのを防ぐ）。
    workdir = _restrict_default_workdir(_resolve_workdir(need_write=True))

    is_plan_exempt = (skill_name, script_filename) in _state._PLAN_APPROVAL_EXEMPT_SCRIPTS
    if not is_plan_exempt and not cl.user_session.get("plan_approved"):
        logger.info("run_script: 計画未承認のためブロック skill=%s script=%s", skill_name, script_filename)
        return (
            "エラー: 計画が未承認のため実行できません"
            f"（skill={skill_name}, script={script_filename}）。"
            "create_plan で計画を作成し、approve_plan でユーザーの承認を得てから"
            "実行してください。自分のtoolsにcreate_plan/approve_planが無い"
            "（サブエージェントである）場合は、それ以上試行せずこのエラーを"
            "そのまま最終回答として委譲元へ報告してください。"
        )

    # .py は設定の Python で、それ以外はそのまま実行を試みる。
    if script_path.suffix == ".py":
        cmd = [_state._SCRIPT_PYTHON, str(script_path), *args]
    else:
        cmd = [str(script_path), *args]
    return cmd, workdir


async def _run_script_impl(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """run_script の実行本体。

    公開ツールの引数名は "args" ではなく "script_args"（"args"/"kwargs" は
    pydantic の ValidatedFunction が *args/**kwargs 用プレースホルダとして
    予約している名前と衝突し、生成されるスキーマのフィールド名が
    "v__args" に化けて run_script() 呼び出しが TypeError になるため使えない）。
    """
    prepared = _prepare_script_execution(skill_name, script_filename, script_args)
    if isinstance(prepared, str):
        return prepared
    cmd, workdir = prepared
    env, guard_dir = _run_script_guard_env(workdir)

    logger.info("run_script: %s %s cwd=%s", skill_name, script_filename, workdir)
    try:
        # 承認待ちの await 済みで別スレッドの必要はないが、subprocess.run 自体は
        # ブロッキング呼び出しのため、to_thread でイベントループのブロックを避ける。
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_state._SCRIPT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"エラー: スクリプトが {_state._SCRIPT_TIMEOUT} 秒でタイムアウトしました。"
    except OSError as e:
        return f"エラー: スクリプトを実行できませんでした: {e}"
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    # stdout / stderr / 終了コードをまとめて返す（LLM が結果を解釈できるように）。
    parts = [f"[終了コード] {proc.returncode}"]
    if proc.stdout:
        parts.append(f"[標準出力]\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"[標準エラー]\n{proc.stderr.rstrip()}")
    warning = _track_failure_streak("run_script_failure_streak", proc.returncode != 0, "run_script")
    if warning:
        parts.append(warning)
    return "\n".join(parts)


@dataclass
class _BackgroundJob:
    """run_script_background で起動したジョブの状態。

    モジュールレベルの _BACKGROUND_JOBS に job_id をキーとして保持する。
    """

    process: asyncio.subprocess.Process
    thread_id: str
    skill_name: str
    script_filename: str
    started_at: float
    stdout_chunks: list[str]
    stderr_chunks: list[str]
    status: str  # "running" | "completed" | "failed" | "timeout" | "killed" | "error"
    returncode: int | None
    error_message: str | None
    runner_task: "asyncio.Task | None" = None
    # execute_python_code_background 由来のジョブのみ設定される
    # （run_script_background 由来のジョブでは None のまま）。
    tmp_path: "Path | None" = None
    workdir: "Path | None" = None
    before_snapshot: "dict[Path, float] | None" = None
    # run_script_background / execute_python_code_background 由来のジョブの
    # 書き込みガード用一時ディレクトリ（_run_script_guard_env が作成）。
    # ジョブ終了後に _run_background_job の finally で削除する。ガード注入に
    # 失敗した場合や execute_python_code_background 由来の場合は None のまま
    # （execute_python_code_background 側はコード先頭連結方式のためこの
    # フィールドを使わない）。
    guard_dir: "Path | None" = None
    # check_script_job が直前に「実行中」ステータスを返した時刻
    # （time.monotonic()）。None は未確認（起動直後でまだ一度も
    # check_script_job が呼ばれていない）ことを表す。連続呼び出しの
    # 最短間隔（_state._SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS）を
    # サーバー側で強制するために使う。
    last_polled_at: float | None = None


# run_script_background のジョブレジストリ。プロセス内メモリのみで永続化は
# しない（アプリ再起動でジョブは失われるが、そもそも実行中プロセスも
# 再起動で失われるため実害はない）。
_BACKGROUND_JOBS: dict[str, _BackgroundJob] = {}

# check_script_job が「実行中」ステータスで返す標準出力/標準エラーの末尾の
# 最大文字数（全量を返すとコンテキストを圧迫するため切り詰める）。
# 値は config.ini の [scripts].background_job_output_tail_chars（=
# _state._SCRIPT_BACKGROUND_JOB_OUTPUT_TAIL_CHARS）で管理する。
_JOB_OUTPUT_TAIL_CHARS: int = _state._SCRIPT_BACKGROUND_JOB_OUTPUT_TAIL_CHARS


async def _read_stream_into(stream: "asyncio.StreamReader | None", chunks: list[str]) -> None:
    """サブプロセスの stdout/stderr を EOF まで読み、行単位で chunks に追記する。"""
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        chunks.append(line.decode("utf-8", errors="replace"))


def _format_background_job_progress(job: "_BackgroundJob", job_id: str) -> str:
    """run_script_background/execute_python_code_background ジョブの実行中の
    状況を表す文字列を組み立てる。

    _push_background_job_progress（人間向けのUI直接push）と check_script_job
    の running 分岐（フォールバック経路でのLLM向け応答）の両方から呼ぶ、
    表示フォーマット共通化のためのヘルパー（dispatch_agent の
    _format_dispatch_agent_progress と同じ役割）。
    """
    elapsed = int(time.monotonic() - job.started_at)
    parts = [f"実行中です（経過 {elapsed} 秒・job_id={job_id}）。"]
    stdout_tail = "".join(job.stdout_chunks)[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
    stderr_tail = "".join(job.stderr_chunks)[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
    if stdout_tail:
        parts.append(f"[標準出力（末尾）]\n{stdout_tail}")
    if stderr_tail:
        parts.append(f"[標準エラー（末尾）]\n{stderr_tail}")
    return "\n".join(parts)


async def _push_background_job_progress(job: "_BackgroundJob", job_id: str) -> None:
    """run_script_background/execute_python_code_background の実行中、人間向けに
    進捗をチャットへ直接pushする。

    cl.Message送信のみでLLM呼び出しを一切伴わないためトークンを消費しない
    （dispatch_agent の _push_dispatch_agent_progress と同じ設計）。
    author=_SUBAGENT_MESSAGE_AUTHOR は使わない（サブエージェントの最終回答
    専用の識別子のため）。代わりに type="system_message" を使う
    （app.py がコンテキスト圧縮の通知等、既存の一時的なシステム通知に
    使っている慣習と同じ）。

    _run_background_job の finally で確実にキャンセルされる想定
    （_push_dispatch_agent_progress と同じ理由）。

    metadata={"ephemeral_progress": True} を付ける（_push_dispatch_agent_progress
    と同じ）。ChatThreadDataLayer.create_step（src/thread_store.py）がこの
    フラグを見て永続化をスキップするため、スレッド再開時に「経過N秒・
    job_id=xxx」という実行時点でしか意味を持たない古い進捗表示が復元されない
    （2026-08-21 ユーザー報告）。ライブ表示（emitter.send_step）には影響しない。
    """
    while job.status == "running":
        await asyncio.sleep(_state._SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS)
        if job.status != "running":
            break
        await cl.Message(
            content=_format_background_job_progress(job, job_id),
            type="system_message",
            metadata={"ephemeral_progress": True},
        ).send()


async def _run_background_job(job: "_BackgroundJob", job_id: str) -> None:
    """バックグラウンドジョブのランナータスク本体。

    stdout/stderr の読み取りと終了コード取得を並行して行い、
    background_max_runtime_seconds を超えたら強制終了する。
    stop_script_job が先に status を "killed" にしていた場合はそれを
    上書きしない。

    進捗push タスク（_push_background_job_progress）をここで生成・管理する。
    run_script_background/execute_python_code_background 自身が安全上限
    フォールバックでターンを終えた後も、このタスク（＝ジョブ本体）が生きて
    いる限り進捗pushは動き続ける（dispatch_agent の _run_dispatch_agent_job
    と同じ設計）。
    """
    progress_task = asyncio.create_task(_push_background_job_progress(job, job_id))
    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream_into(job.process.stdout, job.stdout_chunks),
                    _read_stream_into(job.process.stderr, job.stderr_chunks),
                    job.process.wait(),
                ),
                timeout=_state._SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS,
            )
        except asyncio.TimeoutError:
            job.process.kill()
            await job.process.wait()
            if job.status != "killed":
                job.status = "timeout"
            job.returncode = job.process.returncode
            return
        except Exception as e:  # noqa: BLE001 - ストリーム読み取り自体の異常はエラー扱いで返す
            if job.status != "killed":
                job.status = "error"
                job.error_message = f"{e}\n{traceback.format_exc()}"
            return

        job.returncode = job.process.returncode
        if job.status == "killed":
            return
        job.status = "completed" if job.returncode == 0 else "failed"
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        # execute_python_code_background が書き出した一時 .py ファイルの後始末。
        # run_script_background 由来のジョブでは tmp_path が None のため何もしない。
        if job.tmp_path is not None:
            job.tmp_path.unlink(missing_ok=True)
        # run_script_background 由来のジョブの書き込みガード用一時ディレクトリの後始末
        # （_run_script_guard_env が作成。ガード注入に失敗した場合は None のため何もしない）。
        if job.guard_dir is not None:
            shutil.rmtree(job.guard_dir, ignore_errors=True)


def _purge_stale_background_jobs() -> None:
    """完了済みのまま check_script_job で回収されなかったジョブを掃除する。

    専用のクリーンアップループは持たず、run_script_background の呼び出しの
    度に opportunistic に走らせる。
    """
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _BACKGROUND_JOBS.items()
        if job.status != "running" and now - job.started_at > _state._SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS
    ]
    for job_id in stale:
        _BACKGROUND_JOBS.pop(job_id, None)


def _format_job_result(job: "_BackgroundJob") -> str:
    """終了済みジョブの結果を run_script と同じ表示形式に整形する。"""
    returncode_label = job.returncode if job.returncode is not None else "不明"
    parts = [f"[終了コード] {returncode_label}"]
    stdout = "".join(job.stdout_chunks).rstrip()
    stderr = "".join(job.stderr_chunks).rstrip()
    if stdout:
        parts.append(f"[標準出力]\n{stdout}")
    if stderr:
        parts.append(f"[標準エラー]\n{stderr}")
    # execute_python_code_background 由来のジョブのみ workdir/before_snapshot が
    # 設定されている（run_script_background 由来では None のためスキップされる）。
    if job.workdir is not None and job.before_snapshot is not None:
        path_memory_note = _register_exec_output_files(job.workdir, job.before_snapshot, job.thread_id)
        if path_memory_note:
            parts.append(path_memory_note)
    return "\n".join(parts)


def _background_job_started_message(job_id: str) -> str:
    """run_script_background/execute_python_code_background が安全上限
    （[scripts].background_inline_wait_max_seconds）に達してもなおジョブが
    終わっていない場合に、フォールバックとしてLLMへ返す案内文。

    通常はこの文言に到達しない（run_script_background/
    execute_python_code_background 自身がジョブ完了までツール呼び出し内で
    待ち続け、最終結果を直接返すため）。到達するのは、設定された安全上限を
    超えるほど長時間のジョブだけである（dispatch_agent の
    _dispatch_agent_job_started_message と同じ設計）。

    以前は「途中で打ち切る場合は stop_script_job を使ってください」という
    表現だけだったが、これが「長時間かかる処理は打ち切るべきもの」という
    誤読を誘発し、ユーザーが完走を求めているのにモデルが自発的に
    stop_script_job を呼んで途中終了させてしまう事例が確認された。
    処理時間の長さ自体は打ち切る理由にならないことを明記する。
    """
    return (
        f"バックグラウンドで実行中です（job_id={job_id}）。長時間かかっているため、"
        "いったんこのターンを終えて制御を返します（ジョブ自体は裏側で動き続けます）。\n"
        "完了確認・結果取得には check_script_job（job_id指定）を使うこと。"
        "処理に時間がかかっていること自体は打ち切る理由にはならない。"
        "ユーザーから明示的に中断・キャンセルを指示された場合にのみ"
        "stop_script_job（job_id指定）を使うこと。"
    )


def _resolve_job(job_id: str) -> "_BackgroundJob | str":
    """job_id を現在のセッション所有のジョブへ解決する（他セッションは拒否）。"""
    job = _BACKGROUND_JOBS.get(job_id)
    if job is None:
        return f"エラー: job_id '{job_id}' は見つかりません（既に取得済みか、無効なIDです）。"
    thread_id = cl.user_session.get("thread_id") or ""
    if job.thread_id != thread_id:
        return f"エラー: job_id '{job_id}' は現在のセッションのものではありません。"
    return job


def _finalize_script_job_result(job: "_BackgroundJob", job_id: str) -> str:
    """終端状態（completed/failed/timeout/killed/error）のジョブを最終結果
    文字列へ整形し、レジストリから取り除く。

    run_script_background/execute_python_code_background（安全上限内に完了
    した場合）と check_script_job（終端状態を取得した場合）の両方から呼ぶ、
    ワンショット取得（一度取得したら同じ job_id は再利用できない）の共通処理
    （dispatch_agent の _finalize_dispatch_agent_job_result と同じ役割）。
    stop_script_job は自身がジョブを終端させた直後に独自の「強制終了しました。」
    接頭辞で結果を返すため、こちらは使わない。
    """
    result = _format_job_result(job)
    if job.status == "timeout":
        result = (
            f"エラー: バックグラウンド実行が {_state._SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS} " f"秒の上限に達したため強制終了しました。\n{result}"
        )
    elif job.status == "killed":
        result = f"stop_script_job により強制終了されました。\n{result}"
    elif job.status == "error":
        result = f"エラー: バックグラウンド実行中に問題が発生しました: {job.error_message}"
    _BACKGROUND_JOBS.pop(job_id, None)
    return result
