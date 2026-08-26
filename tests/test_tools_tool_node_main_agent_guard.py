"""[main_agent_tool_guard] の許可リスト方式への回帰テスト。

背景: 従来は entries に登録された対象だけ回数制限がかかり、未登録の
ツール・run_scriptスキルスクリプトはメインエージェントから無制限に呼べて
しまうブロックリスト方式だった。これを許可リスト方式（entries未登録＝
呼び出し不可）へ変更したため、その核心動作
（src/tools/tool_node.py の _guard_main_agent_tool_limit）を検証する。
"""

from types import SimpleNamespace

import pytest

from src.tools import tool_node


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _make_input(name: str, args: dict | None = None) -> dict:
    return {
        "__type": "tool_call_with_context",
        "tool_call": {"name": name, "args": args or {}, "id": "call_1"},
        "state": {},
    }


def _setup(monkeypatch, *, entries, enabled: bool = True):
    cfg = SimpleNamespace(
        main_agent_tool_guard_enabled=enabled,
        main_agent_tool_guard_allow_entries=frozenset(entries),
    )
    monkeypatch.setattr(tool_node._state, "_LLM_CONFIG", cfg)
    monkeypatch.setattr(tool_node._state, "_AGENT_TYPES", {"worker": object()})
    session = _FakeUserSession()
    monkeypatch.setattr(tool_node.cl, "user_session", session)
    return session


def test_unregistered_tool_is_blocked(monkeypatch) -> None:
    """許可リスト（entries）に無い名前は、たとえ無害なツールでも一切呼べない。"""
    _setup(monkeypatch, entries=[("Glob", 1)])

    result = tool_node._guard_main_agent_tool_limit(_make_input("some_unlisted_tool"))

    assert result is not None
    content = result["messages"][0].content
    assert "some_unlisted_tool" in content
    assert "許可されていません" in content


def test_max_calls_minus_one_is_unlimited(monkeypatch) -> None:
    """max_calls=-1 で登録したツールは、何度呼んでも許可される。"""
    _setup(monkeypatch, entries=[("dispatch_agent", -1)])

    for _ in range(5):
        result = tool_node._guard_main_agent_tool_limit(_make_input("dispatch_agent"))
        assert result is None


def test_max_calls_zero_always_blocks(monkeypatch) -> None:
    """max_calls=0 で登録したツールは常に完全ブロックされる（従来通り）。"""
    _setup(monkeypatch, entries=[("Read", 0)])

    result = tool_node._guard_main_agent_tool_limit(_make_input("Read"))

    assert result is not None
    content = result["messages"][0].content
    assert "max_calls=0" in content


def test_positive_max_calls_blocks_after_limit(monkeypatch) -> None:
    """正の max_calls は従来通り、上限到達後にブロックされる。"""
    _setup(monkeypatch, entries=[("Glob", 2)])

    first = tool_node._guard_main_agent_tool_limit(_make_input("Glob"))
    second = tool_node._guard_main_agent_tool_limit(_make_input("Glob"))
    third = tool_node._guard_main_agent_tool_limit(_make_input("Glob"))

    assert first is None
    assert second is None
    assert third is not None
    assert "呼び出し上限" in third["messages"][0].content


def test_run_script_pair_unregistered_is_blocked(monkeypatch) -> None:
    """run_script経由のスキルスクリプトも、[skill, script]ペア未登録ならブロックされる。"""
    _setup(monkeypatch, entries=[(("pdf-tools", "render_pdf_pages.py"), 0)])

    result = tool_node._guard_main_agent_tool_limit(
        _make_input("run_script", {"skill_name": "web-search", "script_filename": "search_web.py"})
    )

    assert result is not None
    assert "許可されていません" in result["messages"][0].content


def test_run_script_pair_registered_unlimited(monkeypatch) -> None:
    _setup(monkeypatch, entries=[(("pdf-tools", "render_pdf_pages.py"), -1)])

    result = tool_node._guard_main_agent_tool_limit(
        _make_input("run_script", {"skill_name": "pdf-tools", "script_filename": "render_pdf_pages.py"})
    )

    assert result is None


def test_subagent_call_is_not_guarded(monkeypatch) -> None:
    """サブエージェント（dispatch_agent配下）内部での呼び出しは、未登録でも対象外。"""
    _setup(monkeypatch, entries=[])
    token = tool_node._IN_SUBAGENT.set(True)
    try:
        result = tool_node._guard_main_agent_tool_limit(_make_input("some_unlisted_tool"))
    finally:
        tool_node._IN_SUBAGENT.reset(token)

    assert result is None


def test_guard_disabled_allows_everything(monkeypatch) -> None:
    _setup(monkeypatch, entries=[], enabled=False)

    result = tool_node._guard_main_agent_tool_limit(_make_input("some_unlisted_tool"))

    assert result is None


@pytest.mark.parametrize("entries", [[], [("Glob", 1)]])
def test_empty_entries_blocks_all_unregistered(monkeypatch, entries) -> None:
    """entries が空、または対象外の名前しか登録されていない場合、
    許可リスト方式では未登録名は常にブロックされる（旧・素通り仕様の回帰防止）。"""
    _setup(monkeypatch, entries=entries)

    result = tool_node._guard_main_agent_tool_limit(_make_input("totally_unknown_tool"))

    assert result is not None
