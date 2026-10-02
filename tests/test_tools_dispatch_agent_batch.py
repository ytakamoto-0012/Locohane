"""dispatch_agent_batch（ハーネス側でのグループ分割・並列委譲）の回帰テスト。

低パラメータモデルは「全グループ分の dispatch_agent を1回の応答で並べる」指示を
安定して守れなかった（evals/tuning_log.md の並列発行チューニング参照）。
dispatch_agent_batch はファイル一覧取得・グループ分け・担当ファイル割り当て・
並列実行をハーネス側で行うため、その機械的な部分を検証する。
"""

import asyncio
import sys

import pytest

from src import tools

# パッケージ属性 tools.dispatch_agent_batch は @tool オブジェクトで上書きされているため、
# モジュール本体は sys.modules から取る。
_BATCH_MODULE = sys.modules["src.tools.dispatch_agent_batch"]


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeMessage:
    def __init__(self, content: str = "", **kwargs) -> None:
        self.content = content

    async def send(self) -> None:
        pass

    async def update(self) -> None:
        pass


def _setup(monkeypatch, tmp_path, names: list[str]) -> tuple:
    monkeypatch.setattr(tools._state, "_LLM_CONFIG", object())
    monkeypatch.setattr(
        tools._state,
        "_AGENT_TYPES",
        {"worker": tools._state.ResolvedAgentType(description="", system_prompt="", tools=[])},
    )
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())
    monkeypatch.setattr(tools.cl, "Message", _FakeMessage)
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_SEMAPHORES", {})
    monkeypatch.setattr(tools._dispatch_agent_job, "_DISPATCH_AGENT_JOBS", {})
    monkeypatch.setattr(tools._state, "_SUBAGENT_MAX_ITERATIONS", 100)
    workdir = tmp_path / "workdir"
    images = workdir / "images"
    images.mkdir(parents=True)
    for name in names:
        (images / name).write_bytes(b"x")
    monkeypatch.setattr(tools._state, "_DEFAULT_WORKDIR", workdir)
    notes: list[dict] = []

    class _FakeNoteTool:
        def invoke(self, args):
            notes.append(args)
            return "書き込みました"

    monkeypatch.setattr(_BATCH_MODULE, "write_thread_note", _FakeNoteTool())
    return images, notes


_TC_ID_COUNTER = 0


async def _invoke(**kwargs) -> str:
    global _TC_ID_COUNTER
    _TC_ID_COUNTER += 1
    result = await tools.dispatch_agent_batch.ainvoke(
        {"name": "dispatch_agent_batch", "args": kwargs, "id": f"test-batch-{_TC_ID_COUNTER}", "type": "tool_call"}
    )
    return result.content if hasattr(result, "content") else result


def _capture_tasks(monkeypatch) -> list[str]:
    captured: list[str] = []

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        captured.append(task)
        return "処理件数: OK"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)
    return captured


@pytest.mark.asyncio
async def test_batch_splits_files_by_name_into_groups(monkeypatch, tmp_path) -> None:
    names = [f"IMG_{i:03d}.png" for i in range(7)]
    images, notes = _setup(monkeypatch, tmp_path, list(reversed(names)) + ["memo.txt"])
    captured = _capture_tasks(monkeypatch)

    result = await _invoke(task="画像を解析して md へ書き出す", agent_type="worker", pattern="*.png", path=str(images), group_size=3)

    assert len(captured) == 3
    # 各グループの task には共通指示と、ファイル名順に割り当てた担当ファイルの絶対パスが入る。
    by_group = sorted(captured, key=lambda t: t.split("グループ")[1])
    expected = [names[0:3], names[3:6], names[6:7]]
    for task, group in zip(by_group, expected):
        assert "画像を解析して md へ書き出す" in task
        for name in group:
            assert str((images / name).resolve()) in task
        assert "memo.txt" not in task
    assert "7 件を 3 グループ" in result
    assert "完了 3" in result
    assert len(notes) == 1 and "dispatch_agent_batch結果" in notes[0]["topic"]


@pytest.mark.asyncio
async def test_batch_runs_groups_concurrently_when_max_parallel_allows(monkeypatch, tmp_path) -> None:
    images, _ = _setup(monkeypatch, tmp_path, [f"a{i}.jpg" for i in range(4)])
    monkeypatch.setattr(tools._state, "_DISPATCH_AGENT_MAX_PARALLEL", 0)
    concurrent = 0
    max_concurrent = 0

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return "ok"

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    await _invoke(task="t", agent_type="worker", pattern="*.jpg", path=str(images), group_size=1)

    assert max_concurrent == 4


@pytest.mark.asyncio
async def test_batch_brace_pattern_matches_case_insensitively_sorted(monkeypatch, tmp_path) -> None:
    images, _ = _setup(monkeypatch, tmp_path, ["b.JPG", "a.png", "c.heic"])
    captured = _capture_tasks(monkeypatch)

    await _invoke(task="t", agent_type="worker", pattern="*.{jpg,png}", path=str(images), group_size=10)

    assert len(captured) == 1
    task = captured[0]
    assert task.index("a.png") < task.index("b.JPG")
    assert "c.heic" not in task


@pytest.mark.asyncio
async def test_batch_returns_error_without_jobs_when_no_files(monkeypatch, tmp_path) -> None:
    images, _ = _setup(monkeypatch, tmp_path, ["a.txt"])
    captured = _capture_tasks(monkeypatch)

    result = await _invoke(task="t", agent_type="worker", pattern="*.png", path=str(images))

    assert result.startswith("エラー:")
    assert captured == []
    assert tools._dispatch_agent_job._DISPATCH_AGENT_JOBS == {}


@pytest.mark.asyncio
async def test_batch_rejects_unknown_agent_type(monkeypatch, tmp_path) -> None:
    images, _ = _setup(monkeypatch, tmp_path, ["a.png"])

    result = await _invoke(task="t", agent_type="nope", pattern="*.png", path=str(images))

    assert result.startswith("エラー: 不明な agent_type")


@pytest.mark.asyncio
async def test_batch_truncates_long_group_results_in_return_value(monkeypatch, tmp_path) -> None:
    images, notes = _setup(monkeypatch, tmp_path, ["a.png"])

    async def fake_run_subagent(task, tools_list, system_prompt, llm_config, max_iterations, **kwargs):
        return "長い結果" * 500

    monkeypatch.setattr(tools._dispatch_agent_job.subagent, "run_subagent", fake_run_subagent)

    result = await _invoke(task="t", agent_type="worker", pattern="*.png", path=str(images))

    assert "（以下略）" in result
    assert len(result) < 1500
    # 全文は thread note 側へ保存される。
    assert "長い結果" * 500 in notes[0]["content"]


@pytest.mark.asyncio
async def test_batch_clamps_group_size_to_max_iterations(monkeypatch, tmp_path) -> None:
    images, _ = _setup(monkeypatch, tmp_path, [f"f{i}.png" for i in range(5)])
    monkeypatch.setattr(tools._state, "_SUBAGENT_MAX_ITERATIONS", 2)
    captured = _capture_tasks(monkeypatch)

    await _invoke(task="t", agent_type="worker", pattern="*.png", path=str(images), group_size=50)

    assert len(captured) == 3
