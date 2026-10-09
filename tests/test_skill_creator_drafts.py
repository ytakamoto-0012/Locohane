"""skill-creator のドラフト操作スクリプトと、evals/run_all.py のスキル安定化トライアウト集計の回帰テスト。

skill-creator のスクリプトは run_script 経由で、Locohane 本体が渡す環境変数
（AGENT_SKILL_DRAFT_DIR / AGENT_USER / AGENT_OTHER_USERS_DRAFTS / AGENT_SKILL_ROOTS）
だけを頼りにドラフト置き場を扱う。実際にサブプロセスとして起動して確かめる。
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PROJECT_ROOT / "skills" / "skill-creator" / "scripts"


@pytest.fixture
def draft_env(tmp_path):
    official = tmp_path / "skills"
    (official / "official-skill").mkdir(parents=True)
    (official / "official-skill" / "SKILL.md").write_text(
        "---\nname: official-skill\ndescription: 正式スキル。\n---\n\n# official-skill\n", encoding="utf-8"
    )
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "AGENT_SKILL_DRAFT_DIR": str(drafts),
        "AGENT_USER": "tanaka",
        "AGENT_OTHER_USERS_DRAFTS": "hidden",
        "AGENT_SKILL_ROOTS": str(official),
    }
    return {"env": env, "drafts": drafts, "official": official}


def _run(script: str, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _scaffold(draft_env, name="my-tool"):
    return _run("scaffold_skill.py", "--name", name, "--description", "テスト用のスキル。", env=draft_env["env"])


def test_scaffold_creates_draft_under_own_folder(draft_env):
    result = _scaffold(draft_env)
    assert result.returncode == 0, result.stderr
    skill_dir = draft_env["drafts"] / "tanaka" / "my-tool"
    assert (skill_dir / "SKILL.md").is_file() and (skill_dir / "evals").is_dir()
    meta = json.loads((skill_dir / "_draft_meta.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "new" and meta["author"] == "tanaka" and meta["status"] == "draft"


def test_scaffold_rejects_official_skill_name(draft_env):
    result = _run("scaffold_skill.py", "--name", "official-skill", "--description", "x", env=draft_env["env"])
    assert result.returncode == 1 and "fork_skill.py" in result.stderr


def test_scaffold_requires_session_user(draft_env):
    env = {k: v for k, v in draft_env["env"].items() if k != "AGENT_USER"}
    result = _run("scaffold_skill.py", "--name", "my-tool", "--description", "x", env=env)
    assert result.returncode == 1 and "ドラフト置き場が使えません" in result.stderr


@pytest.mark.parametrize("path", ["../escape.txt", "_draft_meta.json", "C:/abs.txt"])
def test_write_draft_file_rejects_paths_outside_skill(draft_env, path):
    _scaffold(draft_env)
    result = _run("write_draft_file.py", "--name", "my-tool", "--path", path, "--content", "x", env=draft_env["env"])
    assert result.returncode == 1


def test_write_draft_file_rolls_back_invalid_skill_md(draft_env):
    _scaffold(draft_env)
    skill_md = draft_env["drafts"] / "tanaka" / "my-tool" / "SKILL.md"
    before = skill_md.read_text(encoding="utf-8")
    result = _run("write_draft_file.py", "--name", "my-tool", "--path", "SKILL.md", "--content", "no frontmatter", env=draft_env["env"])
    assert result.returncode == 1 and skill_md.read_text(encoding="utf-8") == before


def test_write_draft_file_patch_requires_unique_match(draft_env):
    _scaffold(draft_env)
    env = draft_env["env"]
    ok = _run("write_draft_file.py", "--name", "my-tool", "--path", "SKILL.md", "--old", "# my-tool", "--new", "# 道具", env=env)
    assert ok.returncode == 0, ok.stderr
    missing = _run("write_draft_file.py", "--name", "my-tool", "--path", "SKILL.md", "--old", "存在しない文", "--new", "x", env=env)
    assert missing.returncode == 1 and "0か所" in missing.stderr


def test_fork_records_base_hash_and_keeps_official_untouched(draft_env):
    result = _run("fork_skill.py", "--name", "official-skill", env=draft_env["env"])
    assert result.returncode == 0, result.stderr
    meta = json.loads((draft_env["drafts"] / "tanaka" / "official-skill" / "_draft_meta.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "improve" and meta["base_skill"] == "official-skill" and len(meta["base_sha256"]) == 64
    assert sorted(p.name for p in (draft_env["official"] / "official-skill").iterdir()) == ["SKILL.md"]


def test_other_users_draft_cannot_be_written_unless_full(draft_env):
    other = draft_env["drafts"] / "suzuki" / "their-tool"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: their-tool\ndescription: 他人の。\n---\n", encoding="utf-8")
    args = ("write_draft_file.py", "--name", "suzuki/their-tool", "--path", "notes.md", "--content", "x")
    assert _run(*args, env=draft_env["env"]).returncode == 1
    full_env = {**draft_env["env"], "AGENT_OTHER_USERS_DRAFTS": "full"}
    assert _run(*args, env=full_env).returncode == 0


def test_list_drafts_hides_other_users_when_hidden(draft_env):
    _scaffold(draft_env)
    other = draft_env["drafts"] / "suzuki" / "their-tool"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: their-tool\ndescription: 他人の。\n---\n", encoding="utf-8")
    listed = json.loads(_run("list_drafts.py", env=draft_env["env"]).stdout)
    assert [d["draft"] for d in listed["drafts"]] == ["tanaka/my-tool"]
    listed = json.loads(_run("list_drafts.py", env={**draft_env["env"], "AGENT_OTHER_USERS_DRAFTS": "listed"}).stdout)
    assert [d["draft"] for d in listed["drafts"]] == ["tanaka/my-tool", "suzuki/their-tool"]


def test_make_eval_case_writes_into_draft(draft_env):
    _scaffold(draft_env)
    result = _run(
        "make_eval_case.py", "--name", "my-tool", "--case-id", "001_basic", "--turns", '["テストして"]', "--judge", "使えたか", env=draft_env["env"]
    )
    assert result.returncode == 0, result.stderr
    case = json.loads((draft_env["drafts"] / "tanaka" / "my-tool" / "evals" / "001_basic.yaml").read_text(encoding="utf-8"))
    assert case["target"] == "my-tool" and case["turns"] == ["テストして"]


def _load_run_all():
    spec = importlib.util.spec_from_file_location("evals_run_all_for_test", _PROJECT_ROOT / "evals" / "run_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("results", "verdict"),
    [
        ([{"case_id": "a", "rules_pass": True}, {"case_id": "a", "rules_pass": True}], "pass"),
        ([{"case_id": "a", "rules_pass": True}, {"case_id": "a", "rules_pass": False}], "fail"),
        ([{"case_id": "a", "rules_pass": True}, {"case_id": "a", "error": "timeout"}], "fail"),
        ([{"case_id": "a", "rules_pass": True, "judge": "x"}, {"case_id": "a", "rules_pass": True, "judge": "x"}], "needs_judge"),
    ],
)
def test_tryout_requires_every_repeat_to_pass(results, verdict):
    report = _load_run_all()._tryout_report(results, 2)
    assert report["verdict"] == verdict
    assert sum(report["cases"]["a"].values()) == 2
