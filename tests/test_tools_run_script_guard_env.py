"""src/tools.py の _run_script_guard_env() の回帰テスト。

run_script はスキル作者が書いた既存の scripts/ 配下のファイルをそのまま
実行するため、execute_python_code のようにコード文字列の先頭へガードの
ソースを連結する方法が使えない。代わりに sitecustomize.py を一時
ディレクトリへ書き出し、PYTHONPATH の先頭に追加することで、対象
スクリプトのソースを一切変更せずに書き込み・削除系呼び出しへガードを
差し込んでいることを、実際にサブプロセスを起動して検証する
（sitecustomize はサブプロセスの解釈系起動時に自動 import されるため、
in-process呼び出しでは検証できない）。
"""

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
    monkeypatch.setattr(tools, "_PATH_MEMORY_DIR", None)
    monkeypatch.setattr(tools, "_PATH_MEMORY_MAX_ENTRIES", 500)
    monkeypatch.setattr(tools, "_LLM_CONFIG", None)
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())
    default_workdir = tmp_path / "default_workdir"
    default_workdir.mkdir()
    monkeypatch.setattr(tools, "_DEFAULT_WORKDIR", default_workdir)


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

    env, guard_dir = tools._run_script_guard_env(workdir)

    assert guard_dir is not None
    assert (guard_dir / "sitecustomize.py").exists()
    assert str(guard_dir) in env["PYTHONPATH"].split(tools.os.pathsep)
    shutil.rmtree(guard_dir, ignore_errors=True)


def test_subprocess_write_outside_workdir_is_blocked(tmp_path):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    env, guard_dir = tools._run_script_guard_env(workdir)
    target = outside / "leaked.txt"

    try:
        result = _run_script(workdir, f'open(r"{target}", "w", encoding="utf-8").write("x")\n', env)
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode != 0
    assert "書き込みサンドボックスガード" in result.stderr
    assert not target.exists()


def test_subprocess_write_inside_workdir_succeeds(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env, guard_dir = tools._run_script_guard_env(workdir)
    target = workdir / "out.txt"

    try:
        result = _run_script(workdir, f'open(r"{target}", "w", encoding="utf-8").write("ok")\n', env)
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "ok"


def test_subprocess_write_inside_default_workdir_succeeds(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env, guard_dir = tools._run_script_guard_env(workdir)
    target = tools._DEFAULT_WORKDIR / "out.txt"

    try:
        result = _run_script(workdir, f'open(r"{target}", "w", encoding="utf-8").write("ok")\n', env)
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "ok"


def test_subprocess_read_outside_workdir_is_permitted(tmp_path):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    existing = outside / "template.txt"
    existing.write_text("hello", encoding="utf-8")
    env, guard_dir = tools._run_script_guard_env(workdir)

    try:
        result = _run_script(workdir, f'print(open(r"{existing}", "r", encoding="utf-8").read())\n', env)
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hello"
