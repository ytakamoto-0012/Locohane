"""src/tools.py の _python_fs_guard_preamble() の回帰テスト。

execute_python_code / execute_python_code_background はLLMが生成した
Pythonコードをサブプロセスでその場実行し、run_script はスキル作者が
書いた既存スクリプトをサブプロセスで実行するため、cwdを絞るだけでは
絶対パスや `..` で作業ディレクトリの外（他ドライブやLocohaneプロジェクト
フォルダ本体を含む）を誤って書き換える事故を防げない。
_python_fs_guard_preamble() が生成するガードコードは、allowed_roots
（作業ディレクトリ・default_workdir相当）配下以外への書き込み・削除・
改名を、場所を問わず常にブロックする（原則「書き込みは常にサンドボックス
配下限定」）。読み取りは対象外で常に無制限。

モックせず、実際にサブプロセスとして実行して振る舞いを検証する
（ガードはサブプロセス内でのモンキーパッチとして機能するため、
in-process呼び出しでは検証できない）。
"""

import os
import subprocess
import sys

import pytest

from src import tools


@pytest.fixture
def guard_dirs(tmp_path):
    allowed_root = tmp_path / "workdir"
    outside_root = tmp_path / "external"
    allowed_root.mkdir(parents=True)
    outside_root.mkdir(parents=True)
    return allowed_root, outside_root


def _run_guarded(tmp_path, allowed_roots, body: str) -> subprocess.CompletedProcess:
    guard_src = tools._python_fs_guard_preamble(allowed_roots)
    script_path = tmp_path / "script.py"
    script_path.write_text(guard_src + "\n" + body, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_write_outside_allowed_root_is_blocked(tmp_path, guard_dirs):
    allowed_root, outside_root = guard_dirs
    target = outside_root / "user_data.txt"
    body = f'open(r"{target}", "w", encoding="utf-8").write("x")\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode != 0
    assert "書き込みサンドボックスガード" in result.stderr
    assert not target.exists()


def test_write_inside_allowed_root_succeeds(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    target = allowed_root / "out.txt"
    body = f'open(r"{target}", "w", encoding="utf-8").write("ok")\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "ok"


def test_os_remove_outside_allowed_root_is_blocked(tmp_path, guard_dirs):
    allowed_root, outside_root = guard_dirs
    protected = outside_root / "config.ini"
    protected.write_text("original", encoding="utf-8")
    body = f'import os\nos.remove(r"{protected}")\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode != 0
    assert "書き込みサンドボックスガード" in result.stderr
    assert protected.read_text(encoding="utf-8") == "original"


def test_shutil_move_into_outside_root_is_blocked(tmp_path, guard_dirs):
    allowed_root, outside_root = guard_dirs
    src = allowed_root / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = outside_root / "app.py"
    body = f'import shutil\nshutil.move(r"{src}", r"{dst}")\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode != 0
    assert "書き込みサンドボックスガード" in result.stderr
    assert not dst.exists()
    assert src.exists()


def test_read_outside_allowed_root_is_permitted(tmp_path, guard_dirs):
    allowed_root, outside_root = guard_dirs
    existing = outside_root / "README.md"
    existing.write_text("hello", encoding="utf-8")
    body = f'print(open(r"{existing}", "r", encoding="utf-8").read())\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hello"
