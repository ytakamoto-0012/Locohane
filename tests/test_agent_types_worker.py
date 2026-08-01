"""agents/worker.md（書き込み可能な作業用サブエージェント）の回帰テスト。

explore は読み取り専用の境界（execute_python_code/run_script を持たない）を
維持する必要がある一方、worker はそれらを持ち、承認済み計画のもとで
成果ファイルを書き出せる必要がある。scan_agent_types() が両方を正しく
読み分け、_resolve_agent_types() が意図したツール集合へ解決することを
検証する。
"""

from pathlib import Path

from src.agent_types import scan_agent_types
from src.tools import _resolve_agent_types

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def test_worker_agent_definition_is_discovered() -> None:
    agent_types = scan_agent_types(_AGENTS_DIR)
    names = {a.name for a in agent_types}

    assert "worker" in names


def test_worker_resolves_with_write_tools() -> None:
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    worker_tool_names = {t.name for t in resolved["worker"].tools}

    assert "execute_python_code" in worker_tool_names
    assert "run_script" in worker_tool_names
    assert "analyze_image" in worker_tool_names


def test_explore_remains_read_only() -> None:
    # worker追加が既存の読み取り専用境界を壊していないことの回帰確認。
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    explore_tool_names = {t.name for t in resolved["explore"].tools}

    assert "execute_python_code" not in explore_tool_names
    assert "run_script" not in explore_tool_names
