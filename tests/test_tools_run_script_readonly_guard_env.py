"""src/tools/_subprocess_env.py の _run_script_readonly_guard_env() の回帰テスト。

run_script_readonly は run_script と同じ sitecustomize.py 差し込み方式で
書き込みガードを注入するが、allowed_roots を空リストで渡すことで場所を
問わず全面的に書き込み・削除・改名を禁止する（execute_python_code_readonly と
同じ方針）。書き込みが一切できないことを根拠に計画承認（Plan Mode）を免除して
いるため、_run_script_guard_env と異なりガード注入に失敗した場合は
ガード無しにフォールバックせず None を返す（fail-closed）ことも検証する。
"""

import os
import shutil
import subprocess
import sys

import pytest

from src import tools


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {"thread_id": "thread-1"}

    def get(self, key, default=None):
        return self._data.get(key, default)


@pytest.fixture(autouse=True)
def _base_env(tmp_path, monkeypatch):
    monkeypatch.setattr(tools._state, "_PATH_MEMORY_DIR", None)
    monkeypatch.setattr(tools._state, "_PATH_MEMORY_MAX_ENTRIES", 500)
    monkeypatch.setattr(tools._state, "_LLM_CONFIG", None)
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())
    default_workdir = tmp_path / "default_workdir"
    default_workdir.mkdir()
    monkeypatch.setattr(tools._state, "_DEFAULT_WORKDIR", default_workdir)


def _run_script(workdir, script_body: str, env: dict) -> subprocess.CompletedProcess:
    script = workdir / "script.py"
    script.write_text(script_body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_guard_dir_created_with_sitecustomize(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    result = tools._subprocess_env._run_script_readonly_guard_env(workdir)

    assert result is not None
    env, guard_dir = result
    assert (guard_dir / "sitecustomize.py").exists()
    assert str(guard_dir) in env["PYTHONPATH"].split(os.pathsep)
    shutil.rmtree(guard_dir, ignore_errors=True)


def test_guard_injection_failure_returns_none(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    def _boom(*args, **kwargs):
        raise OSError("no tmp space")

    monkeypatch.setattr(tools._subprocess_env.tempfile, "mkdtemp", _boom)

    assert tools._subprocess_env._run_script_readonly_guard_env(workdir) is None


def test_subprocess_write_inside_workdir_is_blocked(tmp_path):
    """allowed_roots=[] のため、通常なら書き込み可能な workdir 配下への
    書き込みも一律ブロックされる（run_script の書き込みガードとの違い）。
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env, guard_dir = tools._subprocess_env._run_script_readonly_guard_env(workdir)
    target = workdir / "out.txt"

    try:
        result = _run_script(workdir, f'open(r"{target}", "w", encoding="utf-8").write("x")\n', env)
    finally:
        shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode != 0
    assert "書き込み・削除が一切できません" in result.stderr
    assert not target.exists()


def test_subprocess_delete_is_blocked(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    target = workdir / "existing.txt"
    target.write_text("data", encoding="utf-8")
    env, guard_dir = tools._subprocess_env._run_script_readonly_guard_env(workdir)

    try:
        result = _run_script(workdir, f'import os\nos.remove(r"{target}")\n', env)
    finally:
        shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode != 0
    assert "書き込み・削除が一切できません" in result.stderr
    assert target.exists()


def test_subprocess_read_outside_workdir_is_permitted(tmp_path):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    existing = outside / "template.txt"
    existing.write_text("hello", encoding="utf-8")
    env, guard_dir = tools._subprocess_env._run_script_readonly_guard_env(workdir)

    try:
        result = _run_script(workdir, f'print(open(r"{existing}", "r", encoding="utf-8").read())\n', env)
    finally:
        shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hello"


def test_subprocess_read_foreign_tmp_dir_is_blocked(tmp_path):
    """_FakeUserSession の thread_id は "thread-1"（自セッション）。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    foreign = workdir / "_tmp_thread-2"
    foreign.mkdir()
    leaked = foreign / "leaked.txt"
    leaked.write_text("secret", encoding="utf-8")
    env, guard_dir = tools._subprocess_env._run_script_readonly_guard_env(workdir)

    try:
        result = _run_script(workdir, f'open(r"{leaked}", "r", encoding="utf-8").read()\n', env)
    finally:
        shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode != 0
    assert "一時ディレクトリガード" in result.stderr
