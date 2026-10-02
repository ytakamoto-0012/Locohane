"""dispatch_agent_batch ツール。"""

from __future__ import annotations

from datetime import datetime
from langchain_core.tools import InjectedToolCallId, tool
from pathlib import Path
from typing import Annotated
import asyncio
import chainlit as cl
import logging
import time
import uuid

from . import _dispatch_agent_job
from . import _state
from ._dispatch_agent_job import _SUBAGENT_MESSAGE_AUTHOR, _DispatchAgentJob, _finalize_dispatch_agent_job_result
from ._dispatch_agent_job import _purge_stale_dispatch_agent_jobs, _run_dispatch_agent_job
from ._path_memory_helpers import _resolve_path_memory_tokens_in_text
from ._safe_path import _resolve_file_tools_path
from ._workdir import _foreign_tmp_dir_names
from .dispatch_agent import _task_with_orchestrator_skill_hint, _task_with_plan_hint, _task_with_work_dir_hint
from .glob_tool import GLOB_PATTERN_ERRORS, _expand_braces, glob_pattern_error_message
from .thread_notes import write_thread_note
from .write_scratch_note import sanitize_run_id
from ..subagent import is_truncated_result

logger = logging.getLogger(__name__)

# 戻り値（メインエージェントの会話履歴に積まれる）へ載せる、グループごとの
# サブエージェント最終回答の最大文字数。全文は thread note 側へ保存する。
# workerが書き出したファイル名の一覧表まで返してくると、20グループ分で
# メインの1リクエストあたりのトークンが目標（64000）を超えた実測があるため
# （evals/tuning_log.md par_iter01）、ここで機械的に切り詰める。
_GROUP_RESULT_PREVIEW_CHARS = 400

# 1回の呼び出しで起動するグループ（=サブエージェントのジョブ）数の上限。
# pattern="*" で大きなフォルダを指定した場合等に、確認なしで数百件のジョブが
# 起動されるのを防ぐ（画像なら group_size=15 で750件まで）。
_MAX_GROUPS = 50

# 安全上限超過で先にターンを終えた後も、全グループ完了まで動き続ける
# 集約進捗pushタスクへの参照（asyncio はタスクを弱参照しか持たないため、
# ここで保持しないと実行途中でGCされうる）。
_BATCH_PROGRESS_TASKS: set[asyncio.Task] = set()


def _list_target_files(base: Path, pattern: str) -> list[Path]:
    """base 直下で pattern に一致するファイルを、ファイル名順で返す。

    Glob ツール（glob_search）は更新日時降順で返すため、メインエージェントが
    「1〜15件目」のような範囲で各サブエージェントへ指示すると、受け取った側が
    自分でGlobし直した並びと一致する保証が無い。ここではグループ分けまで
    ハーネス側で行い、各グループへは絶対パスそのものを渡すため、並び順は
    人間が追いやすく再現性のあるファイル名順（大文字小文字を区別しない）に固定する。
    Glob と同じく直下のみを対象とし、`{a,b}` のブレース展開と他セッションの
    一時ディレクトリ除外も揃える。
    """
    exclude_names = _foreign_tmp_dir_names()
    resolved_base = base.resolve()
    seen: set[Path] = set()
    files: list[Path] = []
    for expanded in _expand_braces(pattern):
        for p in base.glob(expanded):
            if p.parent.resolve() != resolved_base or not p.is_file():
                continue
            if exclude_names and set(p.parts) & exclude_names:
                continue
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
    files.sort(key=lambda p: p.name.casefold())
    return files


def _file_listing(group: list[Path]) -> str:
    return "\n".join(f"- {p}" for p in group)


def _group_task(task: str, group: list[Path], index: int, group_count: int, start: int, total: int) -> str:
    """共通の指示文に、そのグループの担当ファイル（絶対パス）を機械的に付け足す。

    メインエージェントにファイル名を書き写させると、後半のグループで実在しない
    連番ファイル名を捏造した実測がある（evals/tuning_log.md par_iter01）ため、
    担当範囲はLLMを経由させずここで確定させる。
    """
    end = start + len(group) - 1
    return (
        f"{task}\n\n"
        f"[担当ファイル（グループ{index}/{group_count}、{len(group)}件。全{total}件中 {start}〜{end}件目）]\n"
        f"{_file_listing(group)}\n"
        "上記の担当ファイルだけを処理する（他のファイルは別のサブエージェントが並行して処理している）。"
        "最終回答は処理件数・書き出し件数・失敗した対象とその理由だけを簡潔に返す（ファイル名の全件一覧は返さない）。"
    )


def _is_active(job: _DispatchAgentJob) -> bool:
    """ジョブがまだ実行中（または順番待ち）か。

    runner_task が status を書き換えずにキャンセルされた場合（停止経路の
    一部）も終了扱いにし、集約進捗pushが永久に回り続けないようにする。
    """
    return job.status == "running" and job.runner_task is not None and not job.runner_task.done()


def _group_status(job: _DispatchAgentJob, result: str) -> str:
    if job.status == "error" or result.startswith("エラー:"):
        return "エラー"
    if job.status == "killed":
        return "強制終了"
    if is_truncated_result(job.result or ""):
        return "打ち切り"
    return "完了"


def _format_batch_progress(jobs: list[tuple[str, _DispatchAgentJob]], started_at: float) -> str:
    """全グループをまとめた進捗表示（グループ数分のメッセージを並べないため1件に集約する）。"""
    active = [(index, job_id, job) for index, (job_id, job) in enumerate(jobs, start=1) if _is_active(job)]
    running = [(index, job_id, job) for index, job_id, job in active if job.run_started_at is not None]
    waiting = len(active) - len(running)
    elapsed = int(time.monotonic() - started_at)
    lines = [
        f"一括実行中です（経過 {elapsed} 秒・全{len(jobs)}グループ: 完了 {len(jobs) - len(active)}・"
        f"実行中 {len(running)}・順番待ち {waiting}）。"
    ]
    for index, job_id, job in running:
        lines.append(f"- グループ{index}: 反復 {job.current_iteration}/{job.max_iterations} 回（job_id={job_id}）")
    return "\n".join(lines)


async def _push_batch_progress(jobs: list[tuple[str, _DispatchAgentJob]], started_at: float) -> None:
    """_push_dispatch_agent_progress のバッチ版（同じく1件のメッセージを .update() で書き換える）。

    安全上限超過でツール自体が先に返った後も、全グループが終わるまで動き続ける。
    送信失敗（セッション切断等）は進捗表示を諦めるだけで、ジョブには影響させない。
    """
    message: "cl.Message | None" = None
    try:
        while any(_is_active(job) for _, job in jobs):
            await asyncio.sleep(_state._DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS)
            if not any(_is_active(job) for _, job in jobs):
                break
            content = _format_batch_progress(jobs, started_at)
            if message is None:
                message = cl.Message(content=content, author=_SUBAGENT_MESSAGE_AUTHOR, metadata={"ephemeral_progress": True})
                await message.send()
            else:
                message.content = content
                await message.update()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 進捗表示の失敗でfire-and-forgetタスクの例外警告を残さない
        logger.debug("dispatch_agent_batch: 進捗pushに失敗しました", exc_info=True)


@tool
async def dispatch_agent_batch(
    task: str,
    agent_type: str,
    pattern: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    path: str = "",
    group_size: int = 15,
    orchestrator_skill: str | None = None,
) -> str:
    """フォルダ内の多数のファイルへ同じ処理を行う作業を、自動でグループに分けて複数のサブエージェントへ並列に委譲する。

    「imagesフォルダの全画像を解析してmdへ書き出す」のように、同じ指示を
    多数のファイルへ適用する場合に、dispatch_agent を自分で何度も呼ぶ代わりに
    これを1回だけ呼ぶ。ファイル一覧の取得・group_size 件ずつのグループ分け・
    各サブエージェントへの担当ファイルの割り当て・並列実行・結果の集計は
    このツールが行う。自分でファイル名を列挙したり、グループごとに
    dispatch_agent を呼び分けたりしなくてよい。

    サブエージェントへ渡る task 文には、共通の指示の後ろに
    「担当ファイル（絶対パスの一覧）」が自動で付け足される。task には全グループ
    共通の指示（出力先・出力形式・ファイル名規則・スキップ条件等）だけを書くこと。

    書き込みを伴う agent_type（worker 等）で使う場合は、dispatch_agent と同じく
    先に create_plan → approve_plan で承認を得ておくこと（計画のステップは
    「このツールで一括処理する」の1ステップでよい）。

    Args:
        task: 全グループ共通の指示。対象パスは `@N` を埋め込んでよい。
        agent_type: 使用するサブエージェントの種別名（dispatch_agent と同じ）。
        pattern: 対象ファイルのglobパターン（例: "*.{jpg,jpeg,png,heic}"）。
            ファイル名部分だけを書く（フォルダは path に渡す）。
            path の直下だけが対象（サブフォルダは探索しない）。拡張子の
            大文字・小文字はどちらか一方を書けば両方一致する。
        path: 対象フォルダの絶対パス（`@N` 可）。省略時は作業ディレクトリ。
        group_size: 1つのサブエージェントへ渡すファイル数（既定15。画像解析を
            伴うなら15以下）。1〜[subagent].max_iterations の範囲に丸める。
            グループ数が50を超える場合は起動前にエラーになる。
        orchestrator_skill: dispatch_agent と同じ（通常は省略）。

    Returns:
        グループごとの処理結果（状態と最終回答の先頭部分）をまとめたテキスト。
        「エラー」「打ち切り」「強制終了」のグループには担当ファイルの一覧も載る
        （再委任時はその一覧を dispatch_agent の task へそのまま入れる）。
        各グループの最終回答の全文は thread note に保存し、その topic 名を
        末尾に示す。安全上限（[subagent].background_inline_wait_max_seconds）に
        達した場合は、完了済みグループの結果に加えて、まだ実行中のグループの
        job_id を返す（check_dispatch_agent_job で後から結果を取得する）。
        対象ファイルが0件・agent_type が不明等の場合は起動前に
        「エラー: ...」を返す（この場合 job は作られない）。
    """
    if _state._LLM_CONFIG is None:
        return "エラー: init_tools() が未実行です"
    resolved = _state._AGENT_TYPES.get(agent_type)
    if resolved is None:
        available = ", ".join(sorted(_state._AGENT_TYPES)) or "（登録なし）"
        return f"エラー: 不明な agent_type '{agent_type}' です。利用可能: {available}"
    base, error = _resolve_file_tools_path(path)
    if error:
        return f"エラー: {error}"
    if not base.is_dir():
        hint = "（`@N` は例の記法です。実際の番号の `@12` 等か、作業ディレクトリからの相対パス `images` 等を渡すこと）" if "@N" in path else ""
        return f"エラー: 対象フォルダが見つかりません: {base}{hint}"
    try:
        files = _list_target_files(base, pattern)
    except GLOB_PATTERN_ERRORS as e:
        return f"エラー: {glob_pattern_error_message(e)}"
    if not files:
        return f"エラー: {base} の直下に pattern '{pattern}' に一致するファイルがありません。"

    size = max(1, min(group_size, _state._SUBAGENT_MAX_ITERATIONS))
    groups = [files[i : i + size] for i in range(0, len(files), size)]
    if len(groups) > _MAX_GROUPS:
        return (
            f"エラー: {base} の直下で pattern '{pattern}' に一致する {len(files)} 件は "
            f"{len(groups)} グループ（{size}件ずつ）になり、1回の上限（{_MAX_GROUPS}グループ）を超えます。"
            "pattern を絞り込むか、対象をサブフォルダ等に分けて複数回に分けて呼ぶこと（ジョブは起動していません）。"
        )

    common = _resolve_path_memory_tokens_in_text(task)
    task_texts: list[str] = []
    for index, group in enumerate(groups, start=1):
        start = (index - 1) * size + 1
        text = _group_task(common, group, index, len(groups), start, len(files))
        text = _task_with_work_dir_hint(text)
        text = _task_with_plan_hint(text)
        if orchestrator_skill is not None:
            text, error = _task_with_orchestrator_skill_hint(text, orchestrator_skill)
            if error:
                return error
        task_texts.append(text)

    logger.info(
        "dispatch_agent_batch: agent_type=%r base=%s pattern=%r files=%d groups=%d group_size=%d",
        agent_type,
        base,
        pattern,
        len(files),
        len(groups),
        size,
    )

    _purge_stale_dispatch_agent_jobs()

    thread_id = cl.user_session.get("thread_id") or ""
    batch_started_at = time.monotonic()
    jobs: list[tuple[str, _DispatchAgentJob]] = []
    for index, text in enumerate(task_texts, start=1):
        job = _DispatchAgentJob(
            thread_id=thread_id,
            # dispatch_agent と同じく tool_call_id 由来にする（グループ番号で一意化）。
            # app.py の _dispatch_agent_rescue_note_paths はこの命名規則で退避ファイルを探す。
            run_id=sanitize_run_id(f"{tool_call_id}_g{index}"),
            agent_type=agent_type,
            task_preview=text[:200],
            started_at=batch_started_at,
            status="running",
            result=None,
            error_message=None,
            max_iterations=_state._SUBAGENT_MAX_ITERATIONS,
            # 進捗はこのツールが全グループ分を1件にまとめてpushする。
            push_progress=False,
        )
        job_id = uuid.uuid4().hex[:12]
        # 同時実行数は _run_dispatch_agent_job 内のセッション単位セマフォ
        # （[subagent].max_parallel）がそのまま制御する。
        job.runner_task = asyncio.create_task(_run_dispatch_agent_job(job, job_id, text, resolved))
        _dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id] = job
        jobs.append((job_id, job))

    progress_task = asyncio.create_task(_push_batch_progress(jobs, batch_started_at))
    _BATCH_PROGRESS_TASKS.add(progress_task)
    progress_task.add_done_callback(_BATCH_PROGRESS_TASKS.discard)

    runner_tasks = [job.runner_task for _, job in jobs]
    inline_wait = _state._DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS
    wait_timeout = inline_wait if inline_wait > 0 else None
    timed_out = False
    try:
        # dispatch_agent と同じく shield で包み、安全上限超過時もジョブ自体は裏で続行させる。
        await asyncio.wait_for(asyncio.shield(asyncio.gather(*runner_tasks, return_exceptions=True)), timeout=wait_timeout)
    except asyncio.TimeoutError:
        timed_out = True
        for _, job in jobs:
            job.turn_still_waiting = False
        # 集約進捗pushは止めず、残りのグループが終わるまで人間向けの表示を続ける。
    except asyncio.CancelledError:
        # dispatch_agent と同じ理由（緊急退避の書き込み完了を待ってから再送出する）。
        progress_task.cancel()
        for _, job in jobs:
            job.turn_still_waiting = False
            if job.runner_task is not None:
                job.runner_task.cancel()
        await asyncio.gather(*runner_tasks, progress_task, return_exceptions=True)
        raise
    if not timed_out:
        progress_task.cancel()
        await asyncio.gather(progress_task, return_exceptions=True)

    summary_lines: list[str] = []
    full_lines: list[str] = []
    pending: list[str] = []
    counts: dict[str, int] = {}
    for index, ((job_id, job), group) in enumerate(zip(jobs, groups), start=1):
        start = (index - 1) * size + 1
        label = f"グループ{index}（{start}〜{start + len(group) - 1}件目、{len(group)}件、{group[0].name}〜{group[-1].name}）"
        if _is_active(job):
            # 安全上限超過でまだ終わっていないグループ。結果は check_dispatch_agent_job で取得する。
            pending.append(job_id)
            counts["実行中"] = counts.get("実行中", 0) + 1
            summary_lines.append(f"### {label}: 実行中（job_id={job_id}）\n担当ファイル:\n{_file_listing(group)}")
            continue
        if job.status == "running":
            # status を書き換えられないままキャンセルで終わったジョブ。
            job.status = "killed"
        result = _finalize_dispatch_agent_job_result(job, job_id)
        status = _group_status(job, result)
        counts[status] = counts.get(status, 0) + 1
        header = f"### {label}: {status}"
        preview = result if len(result) <= _GROUP_RESULT_PREVIEW_CHARS else result[:_GROUP_RESULT_PREVIEW_CHARS] + "…（以下略）"
        if status == "完了":
            summary_lines.append(f"{header}\n{preview}")
        else:
            # 再委任時にLLMがファイル名を推測で補わないよう、担当ファイルをそのまま返す。
            summary_lines.append(f"{header}\n{preview}\n担当ファイル:\n{_file_listing(group)}")
        full_lines.append(f"{header}\n担当ファイル:\n{_file_listing(group)}\n\n{result}")

    note_line = ""
    if full_lines:
        topic = f"dispatch_agent_batch結果（{agent_type}、{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）"
        try:
            note_result = write_thread_note.invoke({"topic": topic, "content": "\n\n".join(full_lines)})
        except Exception as e:  # noqa: BLE001 - 保存失敗で集計結果そのものを失わない
            logger.exception("dispatch_agent_batch: thread note への保存に失敗しました")
            note_result = f"エラー: thread note への保存に失敗しました: {e}"
        note_line = f'終了した各グループの最終回答の全文は thread note "{topic}" に保存しました。' if not note_result.startswith("エラー:") else note_result

    count_text = "、".join(f"{k} {v}" for k, v in counts.items())
    head = (
        f"{base} の {len(files)} 件を {len(groups)} グループ（{size}件ずつ）に分けて "
        f"{agent_type} へ並列に委譲しました（{count_text}）。"
    )
    tail: list[str] = []
    if set(counts) - {"完了", "実行中"}:
        tail.append("「エラー」「打ち切り」「強制終了」のグループは、表示した担当ファイルの一覧をそのまま task に入れて dispatch_agent で再委任すること。")
    if pending:
        logger.warning("dispatch_agent_batch: 安全上限(%s秒)に達したため job_id を返します: %s", wait_timeout, pending)
        head += f"安全上限（{wait_timeout}秒）に達したため、まだ実行中の {len(pending)} グループを残していったんこのターンを終えて制御を返します（ジョブ自体は裏側で動き続けます）。"
        tail.append(
            f"実行中の job_id: {', '.join(pending)}\n"
            "完了確認・結果取得には check_dispatch_agent_job（job_id指定）を使うこと。"
            "処理に時間がかかっていること自体は打ち切る理由にはならない。"
        )
    return "\n\n".join(part for part in [head, *summary_lines, note_line, *tail] if part)
