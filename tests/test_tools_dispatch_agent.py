"""dispatch_agent / check_dispatch_agent_job / stop_dispatch_agent_job の回帰テスト。

設計変更の経緯: 当初は run_script_background と同じ「即座に job_id を返し、
LLM自身が check_dispatch_agent_job でポーリングする」方式で実装したが、
実運用でのフィードバックにより「LLMが自分でポーリングするとその都度トークンを
消費する」問題が判明した。そのため dispatch_agent は現在、ジョブ完了
まで（安全上限まで）ツール呼び出し内でブロックし、最終結果を1回のLLM往復で
直接返す設計に変わっている。安全上限を超えた場合のみ、従来どおり job_id を
返すフォールバック経路が残る。人間向けの進捗表示は cl.Message による直接push
（LLM非経由・トークン消費ゼロ）に置き換わった。

このファイルのテストはこの契約を検証する。
"""

import asyncio
import importlib
import re

import pytest

from src import tools

# tools.write_scratch_note は@toolオブジェクトで上書き済みのため、モジュール自体は
# importlib で sys.modules から直接取得する。
write_scratch_note_module = importlib.import_module("src.tools.write_scratch_note")


class _FakeUserSession:
    def __init__(self, thread_id: str = "thread-1"):
        self._data: dict = {"thread_id": thread_id}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeMessage:
    """tools.cl.Message の差し替え。send() された内容を sent_messages へ記録する。"""

    _sent: list | None = None  # _install_fake_message が差し替える

    def __init__(self, content: str = "", **kwargs) -> None:
        self.content = content

    async def send(self) -> None:
        if _FakeMessage._sent is not None:
            _FakeMessage._sent.append(self.content)


def _install_fake_message(monkeypatch) -> list:
    sent: list = []
    _FakeMessage._sent = sent
    monkeypatch.setattr(tools.cl, "Message", _FakeMessage)
    return sent


def _setup(monkeypatch, tmp_path=None, thread_id: str = "thread-1") -> None:
    monkeypatch.setattr(tools._state, "_LLM_CONFIG", object())
    monkeypatch.setattr(
        tools._state,
        "_AGENT_TYPES",
        {"explore": tools._state.ResolvedAgentType(description="", system_prompt="", tools=[])},
    )
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession(thread_id))
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_SEMAPHORES", {})
    monkeypatch.setattr(tools._dispatch_agent_job, "_DISPATCH_AGENT_JOBS", {})
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS", 1800)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS", 0)
    # 既定は「タイムアウトせずすぐ終わる」テストが安全上限に絶対到達しないよう
    # 十分大きめにしつつ、バグで本当にハングした場合にテストが1800秒待たされ
    # ないよう短めにしておく。安全上限フォールバックを検証するテストでは
    # さらに小さい値へ個別に上書きする。
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 5)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS", 5)
    _install_fake_message(monkeypatch)
    if tmp_path is not None:
        workdir = tmp_path / "workdir"
        workdir.mkdir(exist_ok=True)
        monkeypatch.setattr(tools._state, "_DEFAULT_WORKDIR", workdir)


def _extract_job_id(text: str) -> str:
    m = re.search(r"job_id=(\w+)", text)
    assert m, f"job_id が含まれていません: {text!r}"
    return m.group(1)


@pytest.mark.asyncio
async def test_normal_completion_returns_final_result_directly(monkeypatch, tmp_path) -> None:
    """安全上限内に終わる通常ケースでは、1回の呼び出しで最終結果がそのまま返る。"""
    _setup(monkeypatch, tmp_path=tmp_path)
    tools.cl.user_session.set("main_agent_tool_guard_call_count", {"Glob": 1})

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        return f"done:{task.splitlines()[-1]}"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    result = await tools.dispatch_agent.ainvoke({"task": "investigate", "agent_type": "explore"})

    assert result == "done:investigate"
    assert "job_id=" not in result
    assert tools._dispatch_agent_job._DISPATCH_AGENT_JOBS == {}  # 完了時点でレジストリから取り除かれている
    # 安全上限フォールバックを経ない通常完了では、同一ターン内での
    # 複数回delegateを妨げないよう従来通りガードカウンタがリセットされる。
    assert tools.cl.user_session.get("main_agent_tool_guard_call_count") is None


@pytest.mark.asyncio
async def test_background_dispatch_injects_work_dir_hint_into_task(monkeypatch, tmp_path) -> None:
    """実際の作業ディレクトリをtaskの先頭に事実として与える。"""
    _setup(monkeypatch, tmp_path=tmp_path)
    captured: dict = {}

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        captured["task"] = task
        return "ok"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    await tools.dispatch_agent.ainvoke({"task": "investigate", "agent_type": "explore"})

    expected_work_dir = tmp_path / "workdir"
    assert captured["task"] == f"作業ディレクトリ: {expected_work_dir}\n\ninvestigate"


@pytest.mark.asyncio
async def test_safety_cap_fallback_returns_job_id_and_keeps_job_running(monkeypatch, tmp_path) -> None:
    """安全上限を超えるとjob_idを返してターンを終えるが、ジョブ自体は動き続ける（asyncio.shield）。"""
    _setup(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 0.05)
    gate = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        await gate.wait()
        return "done"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    started = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    assert "job_id=" in started
    job_id = _extract_job_id(started)

    job = tools._dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id]
    assert job.status == "running"
    assert not job.runner_task.done()  # shieldにより安全上限超過でもキャンセルされていない

    gate.set()
    await job.runner_task
    result = await tools.check_dispatch_agent_job.ainvoke({"job_id": job_id})
    assert result == "done"


@pytest.mark.asyncio
async def test_fallback_completion_does_not_reset_main_agent_tool_guard(monkeypatch, tmp_path) -> None:
    """安全上限フォールバック後にジョブが完了しても、別ターンのツールガード
    カウンタを横からリセットしない（cross-turnレースの回帰確認）。"""
    _setup(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 0.05)
    gate = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        await gate.wait()
        return "done"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    started = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    assert "job_id=" in started
    job_id = _extract_job_id(started)
    job = tools._dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id]

    # フォールバック発生後、同一セッションで別の新しいターンが始まり、
    # 既にツールガード上限まで積んでいる状態を模す。
    tools.cl.user_session.set("main_agent_tool_guard_call_count", {"Glob": 1})

    gate.set()
    await job.runner_task

    assert job.turn_still_waiting is False
    # 旧ジョブの完了によって新ターン側のカウンタが横から消されていないこと。
    assert tools.cl.user_session.get("main_agent_tool_guard_call_count") == {"Glob": 1}


@pytest.mark.asyncio
async def test_progress_is_pushed_without_llm_and_stops_after_completion(monkeypatch, tmp_path) -> None:
    """進捗pushはLLMを介さず cl.Message で直接送られ、ジョブ完了後は止まる。"""
    _setup(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS", 0.02)
    sent = _install_fake_message(monkeypatch)
    gate = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, on_iteration=None, **kwargs):
        if on_iteration is not None:
            on_iteration(1, max_iterations)
        await gate.wait()
        return "done"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    async def _release_after_delay():
        await asyncio.sleep(0.08)  # 複数回 push が発火するのを待つ
        gate.set()

    release_task = asyncio.create_task(_release_after_delay())
    result = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    await release_task

    assert result == "done"
    assert len(sent) >= 1
    assert any("反復 1/" in msg for msg in sent)
    assert any("経過" in msg for msg in sent)

    count_after_completion = len(sent)
    await asyncio.sleep(0.06)  # push間隔を跨いでも増えないことを確認
    assert len(sent) == count_after_completion


@pytest.mark.asyncio
async def test_cross_session_access_is_rejected(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path=tmp_path, thread_id="session-a")
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 0.05)
    gate = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        await gate.wait()
        return "done"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    started = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    job_id = _extract_job_id(started)

    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession("session-b"))
    check_result = await tools.check_dispatch_agent_job.ainvoke({"job_id": job_id})
    assert "現在のセッションのものではありません" in check_result
    stop_result = await tools.stop_dispatch_agent_job.ainvoke({"job_id": job_id})
    assert "現在のセッションのものではありません" in stop_result

    gate.set()
    await tools._dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id].runner_task


@pytest.mark.asyncio
async def test_poll_rate_limiting_rejects_too_soon(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 0.05)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS", 60)
    gate = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        await gate.wait()
        return "done"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    started = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    job_id = _extract_job_id(started)

    first = await tools.check_dispatch_agent_job.ainvoke({"job_id": job_id})
    assert "実行中です" in first

    second = await tools.check_dispatch_agent_job.ainvoke({"job_id": job_id})
    assert "確認間隔" in second
    assert job_id in second

    gate.set()
    await tools._dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id].runner_task


@pytest.mark.asyncio
async def test_stop_cancels_running_job_cleanly(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS", 0.05)
    started_running = asyncio.Event()

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        started_running.set()
        await asyncio.sleep(1000)  # stop_dispatch_agent_job にキャンセルされる想定

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    started = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})
    job_id = _extract_job_id(started)
    await started_running.wait()

    job = tools._dispatch_agent_job._DISPATCH_AGENT_JOBS[job_id]
    result = await tools.stop_dispatch_agent_job.ainvoke({"job_id": job_id})

    assert "強制終了しました" in result
    assert job.status == "killed"
    assert job.runner_task.cancelled() or job.runner_task.done()
    assert job_id not in tools._dispatch_agent_job._DISPATCH_AGENT_JOBS


@pytest.mark.asyncio
async def test_exception_inside_job_is_returned_as_error_not_lost(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path=tmp_path)

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    result = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})

    assert result.startswith("エラー: サブエージェントの実行に失敗しました: boom\n")
    assert "Traceback (most recent call last)" in result
    assert tools._dispatch_agent_job._DISPATCH_AGENT_JOBS == {}


@pytest.mark.asyncio
async def test_exception_with_empty_str_falls_back_to_type_name(monkeypatch, tmp_path) -> None:
    """本番incident・2026-08-08: str(e) が空文字列になる例外（asyncio.TimeoutError() 等）で
    「エラー: サブエージェントの実行に失敗しました: 」と原因が一切分からない文言に
    なっていた事象の回帰テスト（issue/20260808_022438_dispatch_agent_background_failure.md）。
    """
    _setup(monkeypatch, tmp_path=tmp_path)

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    result = await tools.dispatch_agent.ainvoke({"task": "t", "agent_type": "explore"})

    assert result.startswith("エラー: サブエージェントの実行に失敗しました: TimeoutError\n")
    assert "Traceback (most recent call last)" in result


def test_scratch_notes_path_for_run_is_isolated_per_run(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path=tmp_path)
    path_a = write_scratch_note_module._scratch_notes_path_for_run("run-a")
    path_b = write_scratch_note_module._scratch_notes_path_for_run("run-b")
    assert path_a != path_b
    assert "run-a" in path_a.name
    assert "run-b" in path_b.name


def test_dispatch_agent_family_is_base_only_not_subagent() -> None:
    assert tools.dispatch_agent in tools.registry._BASE_TOOLS
    assert tools.check_dispatch_agent_job in tools.registry._BASE_TOOLS
    assert tools.stop_dispatch_agent_job in tools.registry._BASE_TOOLS
    assert tools.dispatch_agent not in tools.registry._SUBAGENT_TOOLS
    assert tools.check_dispatch_agent_job not in tools.registry._SUBAGENT_TOOLS
    assert tools.stop_dispatch_agent_job not in tools.registry._SUBAGENT_TOOLS
