"""src/tools.py の _subprocess_env() が config.ini [paths].bin_path を
PATHへ反映することの回帰テスト。

officecli-xlsx 等のスキルはコマンド名（`officecli ...`）を素の状態で叩く
前提のため、OS側のPATH環境変数へユーザーが手動登録していないと
run_script/execute_python_code のサブプロセスから「コマンドが見つからない」
で失敗する。bin_path に配置先を明示しておけば、evals・app.py実行時のどちらも
追加の手動設定なしで呼び出せるようにするための機能（2026-08-01追加、
officecli優先化の設計変更にあわせて発覚した運用上の抜け）。
"""

import os
from dataclasses import dataclass, field

import pytest

from src import tools


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {"thread_id": "thread-1"}

    def get(self, key, default=None):
        return self._data.get(key, default)


@dataclass
class _FakeConfig:
    bin_path: list = field(default_factory=list)
    project_locohane_dirs: list = field(default_factory=list)


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setattr(tools, "_PATH_MEMORY_DIR", None)
    monkeypatch.setattr(tools, "_PATH_MEMORY_MAX_ENTRIES", 500)
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())


def test_existing_bin_dir_is_prepended_to_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / ".officecli" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(tools, "_LLM_CONFIG", _FakeConfig(bin_path=[bin_dir]))

    env = tools._subprocess_env()

    entries = env["PATH"].split(os.pathsep)
    assert str(bin_dir) == entries[0]


def test_nonexistent_bin_dir_is_ignored(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist" / "bin"
    original_path = os.environ.get("PATH", "")
    monkeypatch.setattr(tools, "_LLM_CONFIG", _FakeConfig(bin_path=[missing]))

    env = tools._subprocess_env()

    assert str(missing) not in env.get("PATH", "")
    assert env.get("PATH", "") == original_path


def test_no_config_leaves_path_untouched(monkeypatch):
    original_path = os.environ.get("PATH", "")
    monkeypatch.setattr(tools, "_LLM_CONFIG", None)

    env = tools._subprocess_env()

    assert env.get("PATH", "") == original_path


def test_multiple_bin_dirs_all_prepended_in_order(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(tools, "_LLM_CONFIG", _FakeConfig(bin_path=[first, second]))

    env = tools._subprocess_env()

    entries = env["PATH"].split(os.pathsep)
    assert entries[0] == str(first)
    assert entries[1] == str(second)
