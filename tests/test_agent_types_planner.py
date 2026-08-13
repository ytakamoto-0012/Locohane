"""agents/planner.md（設計専用のサブエージェント）の回帰テスト。

背景: 本番インシデントで、調査結果からいきなりメインエージェントが
create_plan の引数（steps/detail_markdown）をその場で組み立てていたため、
成果物の具体的な中身（1ファイルか2ファイルか等）を練る専用の思考ステップが
存在せず、両者が自己矛盾する計画が作られてしまった。この再発防止として、
「調査」と「create_plan」の間に「設計」ステップ（dispatch_agent(agent_type=
"planner")への委譲）を挟む。planner は verifier/explore と同様、書き込み・
実行系ツールを一切持たない読み取り専用の設計専用エージェントであるべき。
scan_agent_types() が正しく発見し、_resolve_agent_types() が意図した
（実行系を含まない）ツール集合へ解決することを検証する。
"""

from pathlib import Path

from src.agent_types import scan_agent_types
from src.tools import _resolve_agent_types

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def test_planner_agent_definition_is_discovered() -> None:
    agent_types = scan_agent_types(_AGENTS_DIR)
    names = {a.name for a in agent_types}

    assert "planner" in names


def test_planner_resolves_without_write_or_execution_tools() -> None:
    agent_types = scan_agent_types(_AGENTS_DIR)
    resolved = _resolve_agent_types(agent_types)

    planner_tool_names = {t.name for t in resolved["planner"].tools}

    assert "execute_python_code" not in planner_tool_names
    assert "run_script" not in planner_tool_names
    assert "read_skill" in planner_tool_names
    assert "read_skill_file" in planner_tool_names
    assert "get_tool_source" in planner_tool_names
    assert "Read" in planner_tool_names
    assert "Grep" in planner_tool_names
