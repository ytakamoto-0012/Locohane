"""create_plan 直後に approve_plan を呼ばない誤りが続いたときの強制（src/graph.py）のテスト。"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.graph import _approve_plan_forced_model, _select_main_model, should_force_approve_plan
from src.llm.chat_model import ChatLlamaCpp
from src.tools import AWAITING_APPROVE_PLAN_ERROR

_seq = 0


def _blocked_step(*names: str) -> list:
    """create_plan 直後のガードで弾かれた1ステップ（AIMessage と合成エラー）。"""
    global _seq
    calls = []
    for name in names:
        _seq += 1
        calls.append({"name": name, "args": {}, "id": f"c{_seq}"})
    results = [ToolMessage(content=AWAITING_APPROVE_PLAN_ERROR, tool_call_id=c["id"], name=c["name"], status="error") for c in calls]
    return [AIMessage(content="", tool_calls=calls), *results]


def _create_plan_step() -> list:
    global _seq
    _seq += 1
    call = {"name": "create_plan", "args": {}, "id": f"c{_seq}"}
    return [AIMessage(content="", tool_calls=[call]), ToolMessage(content="計画を作成しました", tool_call_id=call["id"], name="create_plan")]


def _history(*steps) -> list:
    messages: list = [SystemMessage(content="sys"), HumanMessage(content="go"), *_create_plan_step()]
    for step in steps:
        messages.extend(step)
    return messages


def test_not_forced_below_threshold():
    assert not should_force_approve_plan(_history(), 2)
    assert not should_force_approve_plan(_history(_blocked_step("dispatch_agent")), 2)


def test_forced_when_errors_reach_threshold():
    assert should_force_approve_plan(_history(_blocked_step("dispatch_agent"), _blocked_step("dispatch_agent")), 2)
    # 並列で複数呼んで全て弾かれた場合も1回と数える
    assert should_force_approve_plan(_history(_blocked_step("dispatch_agent", "create_memory")), 1)


def test_not_forced_when_some_result_succeeded():
    global _seq
    step = _blocked_step("dispatch_agent")
    _seq += 1
    step[0].tool_calls.append({"name": "get_plan_status", "args": {}, "id": f"c{_seq}"})
    step.append(ToolMessage(content="Plan Mode", tool_call_id=f"c{_seq}", name="get_plan_status"))
    assert not should_force_approve_plan(_history(_blocked_step("dispatch_agent"), step), 2)


def test_not_forced_when_streak_is_interrupted():
    # 弾かれた → 別の正常な結果 → 弾かれた、は連続していない
    assert not should_force_approve_plan(_history(_blocked_step("x"), _create_plan_step(), _blocked_step("x")), 2)


def test_not_forced_when_tail_is_human_message():
    messages = _history(_blocked_step("x"), _blocked_step("x"))
    messages.append(HumanMessage(content="トークンガード等のナッジ"))
    assert not should_force_approve_plan(messages, 2)


def test_disabled_by_zero_threshold():
    assert not should_force_approve_plan(_history(_blocked_step("x"), _blocked_step("x")), 0)


@tool
def approve_plan() -> str:
    """approve"""
    return "ok"


@tool
def dispatch_agent(task: str) -> str:
    """dispatch"""
    return "ok"


def _model() -> ChatLlamaCpp:
    return ChatLlamaCpp(base_url="http://127.0.0.1:1/v1", api_key="x", model="m")


def test_forced_model_binds_only_approve_plan_with_required():
    forced = _approve_plan_forced_model(_model(), [dispatch_agent, approve_plan])
    names = [t["function"]["name"] for t in forced.kwargs["tools"]]
    assert names == ["approve_plan"]
    assert forced.kwargs["tool_choice"] == "required"


def test_forced_model_none_without_approve_plan():
    assert _approve_plan_forced_model(_model(), [dispatch_agent]) is None


@dataclass
class _Cfg:
    plan_force_approve_plan_after_errors: int = 2


def test_select_main_model():
    normal, forced = object(), object()
    stuck = _history(_blocked_step("x"), _blocked_step("x"))
    assert _select_main_model(normal, forced, stuck, _Cfg()) is forced
    assert _select_main_model(normal, forced, _history(), _Cfg()) is normal
    # approve_plan がツールに無ければ強制しない
    assert _select_main_model(normal, None, stuck, _Cfg()) is normal
