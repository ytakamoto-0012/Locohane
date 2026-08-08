"""agents/worker.md（書き込み可能な作業用サブエージェント）の回帰テスト。

explore は読み取り専用の境界（execute_python_code を持たない）を維持する
必要がある一方、worker はそれに加えて execute_python_code も持ち、承認済み
計画のもとで成果ファイルを書き出せる必要がある。explore の run_script は
web-search スキルの search_web.py 専用（agents/explore.md 本文の指示で
限定、他の書き込み系スクリプトは呼ばない前提）として許可している。
scan_agent_types() が両方を正しく読み分け、_resolve_agent_types() が
意図したツール集合へ解決することを検証する。
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
    # run_scriptはweb-search（search_web.py）専用として許可済み（プロンプト側で限定）。
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    explore_tool_names = {t.name for t in resolved["explore"].tools}

    assert "execute_python_code" not in explore_tool_names
    assert "run_script" in explore_tool_names
