"""全く同じ応答を連続で繰り返すループの検知（src/tool_loop_guard.py）のテスト。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.llm import ThinkingLoopDetected, ToolCallLoopDetected, tool_loop_nudge_text
from src.tool_loop_guard import detect_tool_call_loop, raise_if_tool_call_loop

ERROR = "エラー: create_planの直後はapprove_planを呼んでください（他のツールは実行されませんでした）。"


@dataclass
class _Cfg:
    tool_loop_guard_enabled: bool = True
    tool_loop_guard_max_repeats: int = 3
    tool_loop_guard_exclude_tools: list[str] = field(default_factory=lambda: ["check_script_job"])


_seq = 0


def _step(*calls: tuple[str, dict, str], content: str = "", status: str = "success") -> list:
    """(ツール名, 引数, 結果) の組で1ステップ（AIMessage と ToolMessage 群）を作る。"""
    global _seq
    tool_calls, results = [], []
    for name, args, result in calls:
        _seq += 1
        tool_calls.append({"name": name, "args": args, "id": f"c{_seq}"})
        results.append(ToolMessage(content=result, tool_call_id=f"c{_seq}", name=name, status=status))
    return [AIMessage(content=content, tool_calls=tool_calls), *results]


def _history(*steps) -> list:
    messages: list = [SystemMessage(content="sys"), HumanMessage(content="go")]
    for step in steps:
        messages.extend(step)
    return messages


def _err(task: str = "x") -> list:
    return _step(("dispatch_agent", {"task": task}, ERROR))


def test_same_response_three_times_is_a_loop():
    loop = detect_tool_call_loop(_history(_err(), _err(), _err()), _Cfg())
    assert loop is not None and loop.repeats == 3 and loop.tool_names == ("dispatch_agent",)
    assert "create_planの直後" in loop.result_excerpt


def test_two_repeats_is_not_a_loop():
    assert detect_tool_call_loop(_history(_err(), _err()), _Cfg()) is None


def test_same_response_is_a_loop_even_if_results_differ():
    steps = [_step(("run_script", {"s": "a.py"}, f"took {i}ms")) for i in range(3)]
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is not None


def test_success_results_are_not_required():
    steps = [_step(("get_plan_status", {}, "Plan Mode")) for _ in range(3)]
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is not None


def test_different_args_are_not_a_loop_even_with_the_same_error():
    assert detect_tool_call_loop(_history(_err("a"), _err("b"), _err("c")), _Cfg()) is None


def test_different_text_is_a_different_response():
    steps = [_step(("Read", {"p": "a"}, "A"), content=f"試行{i}") for i in range(3)]
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is None


def test_reasoning_is_ignored():
    steps = []
    for i in range(3):
        step = _step(("Read", {"p": "a"}, "A"))
        step[0].additional_kwargs["reasoning_content"] = f"考え{i}"
        steps.append(step)
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is not None


def test_a_different_response_breaks_the_streak():
    assert detect_tool_call_loop(_history(_err(), _err("y"), _err(), _err()), _Cfg()) is None


def test_trailing_human_message_breaks_the_streak():
    messages = _history(_err(), _err(), _err())
    messages.append(HumanMessage(content="注意メッセージ"))
    assert detect_tool_call_loop(messages, _Cfg()) is None
    # 注意メッセージの後も、また3回繰り返せば検知する
    messages.extend(_err() + _err())
    assert detect_tool_call_loop(messages, _Cfg()) is None
    messages.extend(_err())
    assert detect_tool_call_loop(messages, _Cfg()) is not None


def test_image_followups_are_skipped():
    followup = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}])
    steps = [_step(("analyze_image", {"p": "a.png"}, "説明")) + [followup] for _ in range(3)]
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is not None


def test_excluded_tools_are_not_counted():
    steps = [_step(("check_script_job", {"job": "1"}, "running")) for _ in range(5)]
    assert detect_tool_call_loop(_history(*steps), _Cfg()) is None


def test_parallel_calls_are_compared_as_a_set():
    steps = [_step(("Read", {"p": "a"}, "A"), ("Grep", {"q": "b"}, "B")) for _ in range(3)]
    loop = detect_tool_call_loop(_history(*steps), _Cfg())
    assert loop is not None and loop.tool_names == ("Read", "Grep")


@pytest.mark.parametrize("cfg", [_Cfg(tool_loop_guard_enabled=False), _Cfg(tool_loop_guard_max_repeats=1)])
def test_disabled(cfg):
    assert detect_tool_call_loop(_history(_err(), _err(), _err()), cfg) is None


def test_raise_if_tool_call_loop_raises_thinking_loop_subclass():
    with pytest.raises(ToolCallLoopDetected) as info:
        raise_if_tool_call_loop(_history(_err(), _err(), _err()), _Cfg())
    assert isinstance(info.value, ThinkingLoopDetected)
    assert info.value.client_broken is False
    assert "dispatch_agent" in info.value.detail
    raise_if_tool_call_loop(_history(_err()), _Cfg())  # 検知しなければ何もしない


def test_tool_loop_nudge_text_uses_own_messages_and_appends_detail():
    exc = ToolCallLoopDetected("（自動検知: 詳細）")
    assert tool_loop_nudge_text(["注意A", "注意B"], 1, exc) == "注意B\n\n（自動検知: 詳細）"
    # 空なら組み込みの既定文言＋詳細
    text = tool_loop_nudge_text([], 0, exc)
    assert text.startswith("全く同じツール呼び出しを繰り返しています") and text.endswith("（自動検知: 詳細）")


def test_detail_has_no_instruction_only_facts():
    loop = detect_tool_call_loop(_history(_err(), _err(), _err()), _Cfg())
    assert loop.detail().startswith("（自動検知: 直近3回、全く同じツール呼び出し（dispatch_agent）を繰り返しています。")
    assert "切り替えて" not in loop.detail()


# --- サブエージェント（src/subagent.py の run_subagent） ---


class _LoopingModel:
    """常に同じツールを呼ぶ偽モデル。受け取った入力を記録する。"""

    def __init__(self) -> None:
        self.inputs: list[list] = []

    def bind_tools(self, tools, tool_choice=None):
        return self

    async def ainvoke(self, messages):
        self.inputs.append(list(messages))
        n = len(self.inputs)
        return AIMessage(content="", tool_calls=[{"name": "dispatch_agent", "args": {"task": "t"}, "id": f"s{n}", "type": "tool_call"}])


class _ErrorTool:
    name = "dispatch_agent"

    async def ainvoke(self, call):
        return ToolMessage(content=ERROR, tool_call_id=call["id"], name="dispatch_agent", status="success")


@pytest.mark.asyncio
async def test_subagent_nudges_then_truncates(monkeypatch):
    from dataclasses import replace

    from src import subagent
    from test_subagent_timeout_retry import _FakeConfig

    model = _LoopingModel()

    async def fake_build_model(config, role):
        return model

    monkeypatch.setattr(subagent, "build_model", fake_build_model)
    config = replace(
        _FakeConfig(),
        thinking_loop_guard_max_retries=1,
        thinking_loop_guard_nudge_messages=["思考ループ用"],
        tool_loop_guard_nudge_messages=("ツールループ用1",),
        tool_loop_guard_enabled=True,
    )
    result = await subagent.run_subagent(task="t", tools=[_ErrorTool()], system_prompt="sp", config=config, max_iterations=20)

    # 3回目の後に注意メッセージ（詳細付き）が入り、また3回繰り返したところで打ち切られる
    nudged = [i for i, msgs in enumerate(model.inputs) if isinstance(msgs[-1], HumanMessage) and "自動検知" in msgs[-1].content]
    assert nudged == [3]
    assert model.inputs[3][-1].content.startswith("ツールループ用1")
    assert len(model.inputs) == 6
    assert "全く同じツール呼び出し（dispatch_agent）が繰り返され" in result
