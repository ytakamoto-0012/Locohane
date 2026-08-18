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


def _run_guarded(
    tmp_path, allowed_roots, body: str, tmp_dir_roots=(), agent_thread_id: str | None = None
) -> subprocess.CompletedProcess:
    guard_src = tools._python_fs_guard_preamble(allowed_roots, tmp_dir_roots=tmp_dir_roots)
    script_path = tmp_path / "script.py"
    script_path.write_text(guard_src + "\n" + body, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if agent_thread_id is not None:
        env["AGENT_THREAD_ID"] = agent_thread_id
    else:
        env.pop("AGENT_THREAD_ID", None)
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


def test_os_system_copy_outside_allowed_root_is_blocked(tmp_path, guard_dirs):
    """os.system 経由のシェルコマンドで書き込みガードを回避できないことの回帰テスト。

    tune-prompt iter1（system_prompt_scale/002実行）で、run_script/
    execute_python_code のopen/os/shutilガードに阻まれたLLMが
    `os.system('copy /Y "src" "dst"')` を使い、allowed_roots外へ実際に
    ファイルをコピーすることに成功した事例を受けて追加。
    """
    allowed_root, outside_root = guard_dirs
    src = allowed_root / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = outside_root / "leaked.txt"
    body = f'import os\nos.system(r\'copy /Y "{src}" "{dst}"\')\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode != 0
    assert "execute_python_codeガード" in result.stderr
    assert not dst.exists()


def test_os_system_git_is_still_blocked(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    body = "import os\nos.system('git status')\n"

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode != 0
    assert "execute_python_codeガード" in result.stderr


def test_read_outside_allowed_root_is_permitted(tmp_path, guard_dirs):
    allowed_root, outside_root = guard_dirs
    existing = outside_root / "README.md"
    existing.write_text("hello", encoding="utf-8")
    body = f'print(open(r"{existing}", "r", encoding="utf-8").read())\n'

    result = _run_guarded(tmp_path, [allowed_root], body)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hello"


def test_read_foreign_tmp_dir_is_blocked(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    foreign = allowed_root / "_tmp_thread-2"
    foreign.mkdir()
    leaked = foreign / "leaked.txt"
    leaked.write_text("secret", encoding="utf-8")
    body = f'open(r"{leaked}", "r", encoding="utf-8").read()\n'

    result = _run_guarded(
        tmp_path, [allowed_root], body, tmp_dir_roots=[allowed_root], agent_thread_id="thread-1"
    )

    assert result.returncode != 0
    assert "一時ディレクトリガード" in result.stderr


def test_read_own_tmp_dir_is_permitted(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    own = allowed_root / "_tmp_thread-1"
    own.mkdir()
    mine = own / "mine.txt"
    mine.write_text("mine", encoding="utf-8")
    body = f'print(open(r"{mine}", "r", encoding="utf-8").read())\n'

    result = _run_guarded(
        tmp_path, [allowed_root], body, tmp_dir_roots=[allowed_root], agent_thread_id="thread-1"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "mine"


def test_remove_foreign_tmp_dir_is_blocked(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    foreign = allowed_root / "_tmp_thread-2"
    foreign.mkdir()
    target = foreign / "leaked.txt"
    target.write_text("secret", encoding="utf-8")
    body = f'import os\nos.remove(r"{target}")\n'

    result = _run_guarded(
        tmp_path, [allowed_root], body, tmp_dir_roots=[allowed_root], agent_thread_id="thread-1"
    )

    assert result.returncode != 0
    assert "一時ディレクトリガード" in result.stderr
    assert target.exists()


def test_rmtree_foreign_tmp_dir_is_blocked(tmp_path, guard_dirs):
    allowed_root, _ = guard_dirs
    foreign = allowed_root / "_tmp_thread-2"
    foreign.mkdir()
    (foreign / "leaked.txt").write_text("secret", encoding="utf-8")
    body = f'import shutil\nshutil.rmtree(r"{foreign}")\n'

    result = _run_guarded(
        tmp_path, [allowed_root], body, tmp_dir_roots=[allowed_root], agent_thread_id="thread-1"
    )

    assert result.returncode != 0
    assert "一時ディレクトリガード" in result.stderr
    assert foreign.exists()
