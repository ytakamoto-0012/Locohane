"""run_script_readonly の回帰テスト。

run_script との違いを検証する:
- 計画未承認（plan_approved が未設定/False）でも実行できる
  （書き込みが一切できないことを根拠に承認チェック自体を行わない）。
- agent_type ごとのスキル/スクリプト制限（_AGENT_TYPE_RUN_SCRIPT_ALLOWLIST）は
  run_script と同様に適用される（_resolve_run_script_command 経由で共有）。
- 実行するスクリプトが書き込み・削除を試みても、作業ディレクトリ配下を
  含め場所を問わずブロックされる（allowed_roots=[]）。
- 書き込みガードの注入自体に失敗した場合はガード無しにフォールバックせず、
  実行を中止してエラーを返す（fail-closed。run_script との非対称性の理由は
  計画承認という後段の歯止めが無いため）。
"""

import sys

import pytest

from src import tools


class _FakeUserSession:
    def __init__(self, thread_id: str = "thread-1"):
        self._data: dict = {"thread_id": thread_id}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(tools._state, "_SKILLS_ROOTS", [root])
    return root


@pytest.fixture(autouse=True)
def _base_env(tmp_path, monkeypatch):
    monkeypatch.setattr(tools._state, "_PATH_MEMORY_DIR", None)
    monkeypatch.setattr(tools._state, "_PATH_MEMORY_MAX_ENTRIES", 500)
    monkeypatch.setattr(tools._state, "_LLM_CONFIG", None)
    monkeypatch.setattr(tools._state, "_SCRIPT_PYTHON", sys.executable)
    monkeypatch.setattr(tools._state, "_SCRIPT_TIMEOUT", 30)
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())
    default_workdir = tmp_path / "default_workdir"
    default_workdir.mkdir()
    monkeypatch.setattr(tools._state, "_DEFAULT_WORKDIR", default_workdir)


def _write_script(skills_root, skill_name: str, script_filename: str, body: str) -> None:
    scripts_dir = skills_root / skill_name / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / script_filename).write_text(body, encoding="utf-8")


def test_tool_schema_matches_run_script():
    schema = tools.run_script_readonly.args
    assert set(schema.keys()) == {"skill_name", "script_filename", "script_args"}


@pytest.mark.asyncio
async def test_runs_without_plan_approval(skills_root):
    """plan_approved 未設定でも run_script と異なりブロックされない。"""
    _write_script(skills_root, "demo-skill", "read_only.py", "print('ok')\n")

    result = await tools.run_script_readonly.ainvoke({"skill_name": "demo-skill", "script_filename": "read_only.py"})

    assert "計画が未承認" not in result
    assert "[終了コード] 0" in result
    assert "[標準出力]\nok" in result


@pytest.mark.asyncio
async def test_write_inside_workdir_is_blocked(skills_root, tmp_path):
    """run_script なら許可される作業ディレクトリ配下への書き込みも一律ブロックされる。"""
    _write_script(
        skills_root,
        "demo-skill",
        "try_write.py",
        'open("out.txt", "w", encoding="utf-8").write("x")\n',
    )

    result = await tools.run_script_readonly.ainvoke({"skill_name": "demo-skill", "script_filename": "try_write.py"})

    assert "[終了コード] 0" not in result
    assert "書き込み・削除が一切できません" in result
    assert not (tools._state._DEFAULT_WORKDIR / "out.txt").exists()


@pytest.mark.asyncio
async def test_unknown_skill_returns_error(skills_root):
    result = await tools.run_script_readonly.ainvoke({"skill_name": "no-such-skill", "script_filename": "x.py"})

    assert result.startswith("エラー:")


@pytest.mark.asyncio
async def test_guard_injection_failure_aborts_execution(skills_root, monkeypatch):
    """ガード注入自体に失敗した場合、run_script のようにガード無しへフォール
    バックせず実行自体を中止する（計画承認という後段の歯止めが無いため）。
    """
    _write_script(skills_root, "demo-skill", "read_only.py", "print('should not run')\n")
    monkeypatch.setattr(tools._script_job, "_run_script_readonly_guard_env", lambda workdir: None)

    result = await tools.run_script_readonly.ainvoke({"skill_name": "demo-skill", "script_filename": "read_only.py"})

    assert result.startswith("エラー:")
    assert "should not run" not in result


def test_agent_type_allowlist_is_enforced():
    token = tools._state._SUBAGENT_AGENT_TYPE.set("explore-websearch")
    try:
        result = tools._script_job._prepare_readonly_script_execution("excel-read", "read_excel.py", ["dummy.xlsx"])
    finally:
        tools._state._SUBAGENT_AGENT_TYPE.reset(token)

    assert isinstance(result, str)
    assert result.startswith("エラー")
    assert "explore-websearch" in result


def test_no_agent_type_context_is_unrestricted(skills_root):
    _write_script(skills_root, "demo-skill", "read_only.py", "print('ok')\n")

    result = tools._script_job._prepare_readonly_script_execution("demo-skill", "read_only.py")

    assert not isinstance(result, str)
