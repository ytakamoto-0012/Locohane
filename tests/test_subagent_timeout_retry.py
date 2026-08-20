"""run_subagent の on_iteration コールバック・llm_timeout_max_retries の回帰テスト。

背景: dispatch_agent が「1回のLLM呼び出しタイムアウトでジョブ全体を諦めて
しまう」既存の run_subagent の挙動（TimeoutError/httpx.TimeoutException を
検知したら即座に打ち切りメッセージを返す、src/subagent.py の run_subagent 内
except 節）に対して耐性を持たせるため、_invoke_with_timeout_retry を新設した。
llm_timeout_max_retries=0（既定・明示的に指定しない呼び出し元向けの安全側
デフォルト）では即座に打ち切ることを固定化しつつ、正の値を渡した場合に
モデル再構築・リトライで復旧できることを確認する。
"""

import pytest
from langchain_core.messages import AIMessage

from src import subagent


class _FakeConfig:
    thinking_loop_guard_max_retries = 0
    subagent_empty_response_max_retries = 0
    subagent_token_guard_enabled = False
    track_token_usage = False
    context_trim_enabled = False
    context_compaction_enabled = False


class _FakeModel:
    """ainvoke() が state["calls"] を進めながら、失敗回数分だけ TimeoutError を送出する。"""

    def __init__(self, state: dict, fail_times: int, final_message: AIMessage) -> None:
        self._state = state
        self._fail_times = fail_times
        self._final_message = final_message

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self._state["calls"] += 1
        if self._state["calls"] <= self._fail_times:
            raise TimeoutError("llama-server busy")
        return self._final_message


def _make_fake_build_model(fail_times: int, final_message: AIMessage):
    """build_model() の差し替え。呼ばれるたびに新しい _FakeModel を返すが、
    失敗判定は state["calls"]（全体の呼び出し回数）で共有するため、
    「モデルを再構築してから再試行する」実際の挙動を模せる。
    """
    state = {"calls": 0}

    def _fake_build_model(config, role):
        return _FakeModel(state, fail_times, final_message)

    return _fake_build_model, state


_FINAL = AIMessage(content="完了しました")


@pytest.mark.asyncio
async def test_on_iteration_called_with_iteration_and_max_iterations(monkeypatch) -> None:
    fake_build_model, _ = _make_fake_build_model(fail_times=0, final_message=_FINAL)
    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    calls = []
    result = await subagent.run_subagent(
        task="t",
        tools=[],
        system_prompt="sp",
        config=_FakeConfig(),
        max_iterations=5,
        on_iteration=lambda iteration, max_iterations: calls.append((iteration, max_iterations)),
    )

    assert result == "完了しました"
    assert calls == [(1, 5)]


@pytest.mark.asyncio
async def test_default_zero_retries_truncates_immediately_on_timeout(monkeypatch) -> None:
    """llm_timeout_max_retries 既定値0（明示的に指定しない呼び出し元向けの
    安全側デフォルト）では、初回のタイムアウトで即座に打ち切りメッセージを
    返す（回帰確認）。
    """
    fake_build_model, state = _make_fake_build_model(fail_times=1, final_message=_FINAL)
    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    result = await subagent.run_subagent(
        task="t",
        tools=[],
        system_prompt="sp",
        config=_FakeConfig(),
        max_iterations=5,
    )

    assert subagent.is_truncated_result(result)
    assert "タイムアウト" in result
    assert state["calls"] == 1  # リトライしていない


@pytest.mark.asyncio
async def test_positive_retries_recovers_from_transient_timeout(monkeypatch) -> None:
    fake_build_model, state = _make_fake_build_model(fail_times=2, final_message=_FINAL)
    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    result = await subagent.run_subagent(
        task="t",
        tools=[],
        system_prompt="sp",
        config=_FakeConfig(),
        max_iterations=5,
        llm_timeout_max_retries=3,
    )

    assert result == "完了しました"
    assert not subagent.is_truncated_result(result)
    assert state["calls"] == 3  # 2回失敗 + 3回目で成功


@pytest.mark.asyncio
async def test_retries_exhausted_still_truncates(monkeypatch) -> None:
    fake_build_model, state = _make_fake_build_model(fail_times=99, final_message=_FINAL)
    monkeypatch.setattr(subagent, "build_model", fake_build_model)

    result = await subagent.run_subagent(
        task="t",
        tools=[],
        system_prompt="sp",
        config=_FakeConfig(),
        max_iterations=5,
        llm_timeout_max_retries=2,
    )

    assert subagent.is_truncated_result(result)
    assert state["calls"] == 3  # 初回 + リトライ2回 = 3回とも失敗
