"""[thinking_loop_guard].target（ループ検知の監視対象）のテスト。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from src.llm.chat_model import ChatLlamaCpp
from src.llm.loop_guard import ThinkingLoopDetected, _chunk_delta_text

_LOOP_TEXT = "`approve_plan` を実行。（結果確認）よし。\n"


def _chunk(content: str = "", reasoning: str | None = None) -> ChatGenerationChunk:
    kwargs = {"reasoning_content": reasoning} if reasoning else {}
    return ChatGenerationChunk(message=AIMessageChunk(content=content, additional_kwargs=kwargs))


def test_chunk_delta_text_includes_reasoning_by_default():
    assert _chunk_delta_text(_chunk("A", "R")) == "AR"


def test_chunk_delta_text_content_only():
    assert _chunk_delta_text(_chunk("A", "R"), include_reasoning=False) == "A"
    assert _chunk_delta_text(_chunk("", "R"), include_reasoning=False) == ""


def _stream_response(field: str) -> httpx.MockTransport:
    """field（"reasoning_content" か "content"）に同じ文を繰り返し流す応答。"""

    def handler(request):
        chunks = [
            {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
             "choices": [{"index": 0, "delta": {field: _LOOP_TEXT}, "finish_reason": None}]}
            for _ in range(80)
        ]
        chunks.append({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                       "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]})
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


def _model(field: str, include_reasoning: bool) -> ChatLlamaCpp:
    return ChatLlamaCpp(
        base_url="http://fake/v1",
        api_key="x",
        model="m",
        streaming=True,
        max_retries=0,
        http_async_client=httpx.AsyncClient(transport=_stream_response(field)),
        loop_guard_window_chars=200,
        loop_guard_check_interval_chars=50,
        loop_guard_confirm_count=2,
        loop_guard_max_history_chars=2000,
        loop_guard_match_ratio_threshold=0.6,
        loop_guard_include_reasoning=include_reasoning,
    )


def _run(model: ChatLlamaCpp):
    return asyncio.run(model.ainvoke([HumanMessage(content="go")]))


@pytest.mark.parametrize("field", ["reasoning_content", "content"])
def test_all_detects_loops_in_both(field):
    with pytest.raises(ThinkingLoopDetected):
        _run(_model(field, include_reasoning=True))


def test_content_only_ignores_reasoning_loop():
    result = _run(_model("reasoning_content", include_reasoning=False))
    assert result.content == "done"


def test_content_only_still_detects_content_loop():
    with pytest.raises(ThinkingLoopDetected):
        _run(_model("content", include_reasoning=False))
