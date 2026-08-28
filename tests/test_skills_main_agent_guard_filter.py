"""filter_skills_for_main_agent_guard() / render_skills_block_with_hint() の回帰テスト。

main_agent_tool_guard 有効時、[skill_name, script_filename] ペアが
max_calls≠0 で許可されていないスキルは、メインエージェントの {{skills}} から
除外する（run_script/run_script_backgroundを直接呼んでも常に拒否されるため、
見せたままだと無駄な往復が発生する。src/tools/tool_node.py の
filter_main_agent_tools と同じ理由づけ）。ただし scripts/ を持たない
SKILL.mdのみのスキル（has_scripts=False）は、run_script自体の対象が無く
read_skill/read_skill_file だけで完結するため、allow_entries登録の有無に
関わらず常に一覧へ残す。
"""

from pathlib import Path
from types import SimpleNamespace

from src.skills import Skill, filter_skills_for_main_agent_guard, render_skills_block_with_hint


def _make_skill(name: str, *, has_scripts: bool = True) -> Skill:
    return Skill(
        name=name,
        description="test skill",
        dir_path=Path(f"skills/{name}"),
        skill_md_path=Path(f"skills/{name}/SKILL.md"),
        has_scripts=has_scripts,
    )


def _make_cfg(*, entries, enabled: bool = True):
    return SimpleNamespace(
        main_agent_tool_guard_enabled=enabled,
        main_agent_tool_guard_allow_entries=frozenset(entries),
    )


def test_guard_disabled_keeps_all_skills() -> None:
    skills = [_make_skill("pdf-tools"), _make_skill("web-search")]
    cfg = _make_cfg(entries=[], enabled=False)

    assert filter_skills_for_main_agent_guard(skills, cfg) == skills


def test_skill_without_allowed_pair_is_dropped() -> None:
    skills = [_make_skill("pdf-tools"), _make_skill("web-search")]
    cfg = _make_cfg(entries=[("Glob", 1), (("pdf-tools", "render_pdf_pages.py"), 0)])

    result = filter_skills_for_main_agent_guard(skills, cfg)

    assert [s.name for s in result] == []


def test_skill_with_allowed_pair_is_kept() -> None:
    skills = [_make_skill("pdf-tools"), _make_skill("web-search")]
    cfg = _make_cfg(entries=[(("pdf-tools", "render_pdf_pages.py"), -1)])

    result = filter_skills_for_main_agent_guard(skills, cfg)

    assert [s.name for s in result] == ["pdf-tools"]


def test_skill_without_scripts_is_kept_even_without_allowed_pair() -> None:
    """scripts/を持たないSKILL.mdのみのスキルは、allow_entries未登録でも一覧に残す。"""
    skills = [_make_skill("pdf-tools"), _make_skill("excel-knowledge", has_scripts=False)]
    cfg = _make_cfg(entries=[("Glob", 1)])

    result = filter_skills_for_main_agent_guard(skills, cfg)

    assert [s.name for s in result] == ["excel-knowledge"]


def test_render_skills_block_with_hint_annotates_only_blocked_skills() -> None:
    skills = [
        _make_skill("pdf-tools"),
        _make_skill("excel-knowledge", has_scripts=False),
        _make_skill("web-search"),
    ]
    cfg = _make_cfg(entries=[(("pdf-tools", "render_pdf_pages.py"), -1)])

    block = render_skills_block_with_hint(skills, cfg)
    lines = block.splitlines()

    assert lines[0] == "- pdf-tools: test skill"
    assert lines[1] == "- excel-knowledge: test skill"
    assert lines[2] == "- web-search: test skill（直接実行不可。詳細確認・実行は dispatch_agent へ委譲）"


def test_render_skills_block_with_hint_empty_list() -> None:
    cfg = _make_cfg(entries=[])

    assert render_skills_block_with_hint([], cfg) == "（利用可能なスキルはありません）"
