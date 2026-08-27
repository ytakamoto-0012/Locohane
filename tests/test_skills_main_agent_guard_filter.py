"""filter_skills_for_main_agent_guard() の回帰テスト。

main_agent_tool_guard 有効時、[skill_name, script_filename] ペアが
max_calls≠0 で許可されていないスキルは、メインエージェントの {{skills}} から
除外する（run_script/run_script_backgroundを直接呼んでも常に拒否されるため、
見せたままだと無駄な往復が発生する。src/tools/tool_node.py の
filter_main_agent_tools と同じ理由づけ）。
"""

from pathlib import Path
from types import SimpleNamespace

from src.skills import Skill, filter_skills_for_main_agent_guard


def _make_skill(name: str) -> Skill:
    return Skill(
        name=name,
        description="test skill",
        dir_path=Path(f"skills/{name}"),
        skill_md_path=Path(f"skills/{name}/SKILL.md"),
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
