"""agents/worker.md（書き込み可能な作業用サブエージェント）の回帰テスト。

explore は読み取り専用の境界（execute_python_code に加え run_script も
持たない）を維持する必要がある一方、worker はそれに加えて execute_python_code
も持ち、承認済み計画のもとで成果ファイルを書き出せる必要がある。Web検索が
必要な調査だけは別種別の explore-websearch へ分離しており、その run_script は
web-search スキルの search_web.py 専用（agents/explore-websearch.md 本文の
指示で限定、他の書き込み系スクリプトは呼ばない前提）として許可している。
scan_agent_types() がこれらを正しく読み分け、_resolve_agent_types() が
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
    # explore はWeb検索も含め run_script 自体を持たない（explore-websearch と分離）。
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    explore_tool_names = {t.name for t in resolved["explore"].tools}

    assert "execute_python_code" not in explore_tool_names
    assert "run_script" not in explore_tool_names


def test_explore_websearch_resolves_with_web_search_run_script() -> None:
    # explore-websearch はrun_scriptを持つが、web-search（search_web.py）専用として
    # 許可済み（プロンプト側の限定に加えコード側 _AGENT_TYPE_RUN_SCRIPT_ALLOWLIST
    # でも強制。詳細は test_tools_run_script_agent_type_skill_allowlist.py）。
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    explore_websearch_tool_names = {t.name for t in resolved["explore-websearch"].tools}

    assert "execute_python_code" not in explore_websearch_tool_names
    assert "run_script" in explore_websearch_tool_names
