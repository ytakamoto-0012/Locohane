"""execute_python_code系の書き込みサンドボックスガード生成。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .. import path_memory

from . import _state
from ._workdir import _resolve_exec_workdir, _resolve_workdir, _restrict_default_workdir


def _register_exec_output_files(workdir: Path, before_snapshot: dict[Path, float], thread_id: str) -> str:
    """execute_python_code の実行前後で workdir 直下のファイル差分を検知し、
    新規作成/更新されたファイルを path_memory へ自動登録する。

    LLMが execute_python_code のコード内で相対パス書き込みしたファイル
    （中間生成物）を、後続の run_script 等へ渡す際にLLMが絶対パスを手で
    組み立て直す必要が無いようにするため（cwdが `_tmp_<thread_id>` に
    切り替わったことをLLMが意識しなくて済む）。

    Args:
        workdir: execute_python_code が使った実行用ディレクトリ
            （_resolve_exec_workdir() の戻り値）。
        before_snapshot: 実行前に取得した {ファイルパス: mtime} のスナップショット。
        thread_id: path_memory への登録に使うセッションID。
            execute_python_code_background 経由の呼び出しでは
            check_script_job 呼び出し時の cl.user_session とジョブ起動時の
            セッションが一致する保証に頼らず、job.thread_id を明示的に渡す。

    Returns:
        新規作成/更新ファイルがあれば「[生成/更新ファイル]」見出し付きの
        文字列（1行1ファイル、`@N ファイル名（新規作成|更新）` 形式）。
        対象ファイルが無い場合は空文字列。パスメモリーへ登録できなかった
        ファイルは絶対パスをそのまま表示する。
    """
    try:
        after_files = [p for p in workdir.iterdir() if p.is_file()]
    except OSError:
        return ""

    changed: list[tuple[Path, str]] = []
    for p in after_files:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        prev = before_snapshot.get(p)
        if prev is None:
            changed.append((p, "新規作成"))
        elif mtime != prev:
            changed.append((p, "更新"))
    if not changed:
        return ""

    lines = []
    for p, kind in changed:
        index = None
        if _state._PATH_MEMORY_DIR is not None:
            index = path_memory.register(
                thread_id,
                str(p),
                _state._PATH_MEMORY_DIR,
                _state._PATH_MEMORY_MAX_ENTRIES,
                description=f"execute_python_codeが{kind}",
            )
        if index is not None:
            lines.append(f"@{index} {p.name}（{kind}）")
        else:
            lines.append(f"{p}（{kind}、パスメモリー登録失敗のため絶対パスをそのまま使用）")
    return "[生成/更新ファイル]\n" + "\n".join(lines)


def _exec_guard_roots() -> tuple[list[Path], list[Path]]:
    """execute_python_code系（execute_python_code/execute_python_code_background）
    のガードに渡す (allowed_roots, display_roots) を組み立てる。

    実際の作業ディレクトリ（_resolve_workdir(need_write=True)。書き込み
    不可と判定されていれば default_workdir へ自動的に倒れる。倒れた場合は
    _restrict_default_workdir() により `_tmp_<thread_id>` へさらに縮小
    される）・`_tmp_<thread_id>`（_resolve_exec_workdir()。work_dir が
    default_workdir以外を指している場合でも常に念のため加える）に加え、
    path_memory_dir（LLM生成コードが path_memory.register()/resolve() を
    直接呼ぶ場合にロックファイル書き込みが必要になるLocohane内部の
    状態ディレクトリ）を allowed_roots に含める。default_workdir自体
    （`_tmp_<thread_id>` 以外のサブディレクトリや直下）は意図的に含めない
    （default_workdirはサーバー側の共有ディレクトリのため、他セッションが
    誤参照する事故を避けるため常に自セッション専用の`_tmp_<thread_id>`
    だけに書き込みを限定する。_restrict_default_workdir参照）。
    4箇所の呼び出し元での重複を避けるための共通ヘルパー。

    display_roots は allowed_roots から path_memory_dir を除いたもの。
    書き込みガード違反時にLLMへ「ここに書き直せ」と提示する候補は
    path_memory_dir（ロックファイル専用でユーザー成果物の置き場ではない）を
    含めるべきではないため分離している。

    以前は実行用ディレクトリ（_tmp_<thread_id>）の親から作業ディレクトリを
    逆算していたが、_tmp_<thread_id> が常に default_workdir 配下に固定
    されたため（_resolve_exec_workdir() 参照）、ここで独立に解決する
    必要がある（そうしないと実際の work_dir が allowed_roots から漏れ、
    execute_python_code がユーザー指定の作業ディレクトリへ書き込めなく
    なってしまう）。
    """
    roots = [_restrict_default_workdir(_resolve_workdir(need_write=True)), _resolve_exec_workdir()]
    display_roots = list(roots)
    if _state._PATH_MEMORY_DIR is not None:
        roots.append(_state._PATH_MEMORY_DIR)
    return roots, display_roots


def _python_fs_guard_preamble(
    allowed_roots: Sequence[Path],
    tmp_dir_roots: Sequence[Path] = (),
    display_roots: Sequence[Path] | None = None,
) -> str:
    """execute_python_code / run_script が実行するコードの先頭（または
    サブプロセスの sitecustomize.py）に連結する、書き込みサンドボックス用の
    ガードコードを生成する。

    LLMが生成したコードや、スキルの scripts/ 配下のスクリプトは絶対パスや
    `..` で任意の場所へ書き込めてしまい、cwd を作業用ディレクトリに絞る
    だけでは他ドライブやプロジェクト本体を誤って書き換える事故を防げない。
    ここで生成するコードは、サブプロセス内で `open`/`os`/`shutil` の
    書き込み・削除・改名系関数をモンキーパッチし、allowed_roots（作業
    ディレクトリと default_workdir）配下以外への操作を場所を問わず
    ブロックする（原則「書き込みは常にサンドボックス配下限定」。以前は
    Locohaneのプロジェクトフォルダだけを保護し、それ以外のドライブ・
    フォルダへの書き込みは無制限に許可していたが、それでは
    「作業ディレクトリの外に書き込まれる」事故を防げないため、常に
    allowed_roots 配下限定へ強化した）。悪意ある回避（ctypes直叩き等）
    までは防げないベストエフォートのガードであり、あくまで「LLMが
    悪気なく意図しない場所に書き込んでしまう」事故防止が目的。

    加えて、`subprocess.Popen`（`run`/`call`/`check_call`/`check_output`
    もこれを経由する）・`os.system`・`os.popen` をモンキーパッチし、
    コマンド名（basename、拡張子は無視）が git / npm / pip / pip3 、
    および copy / xcopy / move / robocopy / del / erase / ren / rename /
    rd / rmdir 等のファイル操作コマンド、cmd / cmd.exe / powershell / pwsh
    等のシェルラッパーのいずれかに一致する場合は場所を問わず
    PermissionError にする。生成・実行させたコードが誤ってリポジトリ操作や
    パッケージインストールを行う事故を防ぐことに加え、`open`/`os`/`shutil`
    への書き込みガードを `os.system("copy ...")` 等のシェル経由で回避
    されるのを防ぐ（tune-prompt iter1でsystem_prompt_scale/002実行中、
    LLMが実際にこの手口で allowed_roots 外へファイルをコピーすることに
    成功した事例を確認したため追加。`cmd /c copy ...` のような多段の
    シェルラッパー経由でのコマンド名偽装までは防げないベストエフォートの
    対策）。こちらは allowed_roots による除外はない（常に全面禁止）。

    さらに tmp_dir_roots 配下（`_tmp_<thread_id>`。execute_python_code系の
    中間生成物置き場、`_resolve_exec_workdir()`）については、自セッション
    以外の `_tmp_<X>` への読み取り（`open()`）・書き込み・削除・改名を
    allowed_roots の内外を問わず一律ブロックする（他セッションの一時
    ファイルをLLM生成コードが誤って読む・書き換える事故の防止。
    ディレクトリ一覧取得 `os.listdir`/`os.scandir`/`Path.iterdir`/`os.walk`
    はv1では対象外）。自セッション自身の `_tmp_<own>` はこれまで通り
    無制限に読み書きできる。

    Args:
        allowed_roots: 書き込み・削除を許可するディレクトリの一覧
            （実行用ディレクトリと `_tmp_<thread_id>`。呼び出し元
            （_exec_guard_roots/_run_script_guard_env）が
            _restrict_default_workdir() によって default_workdir自体を
            渡さないよう既に縮小済み）。
        tmp_dir_roots: `_tmp_<thread_id>` が実際に作られうる親ディレクトリの
            一覧（`_tmp_dir_parents()` の戻り値）。他セッション判定にのみ
            使い、allowed_roots とは独立（execute_python_code_readonly は
            allowed_roots=[] で全面書き込み禁止だが、他セッションtmp判定
            自体はここに渡す tmp_dir_roots で別途機能する）。
        display_roots: 書き込みガード違反時にLLMへ「ここに書き直せ」と
            提示する候補の一覧。省略時は allowed_roots をそのまま使う。
            allowed_roots には path_memory_dir 等、内部用でユーザー
            成果物の置き場として案内すべきでないパスが混じることがあるため
            分離できるようにしている（_exec_guard_roots/
            _run_script_guard_env 参照）。重複は自動的に除去される。

    Returns:
        コード文字列の先頭に連結する、あるいは sitecustomize.py として
        そのまま配置できるモンキーパッチ処理のPythonソース。呼び出し元の
        コード自体には何も変更を加えない。
    """
    # tuple の repr をそのまま埋め込む（旧実装は ", ".join(...) を "(...,)" で
    # 囲んでいたため、allowed_roots が空リストのとき ("",) という「空文字列
    # 1件を含むタプル」になってしまい、os.path.realpath("") がカレント
    # ディレクトリを指す結果、書き込みガードが実質無効化される不具合が
    # あった。repr(tuple(...)) なら空リストは正しく "()" になる。
    allowed_repr = repr(tuple(str(p) for p in allowed_roots))
    tmp_roots_repr = repr(tuple(str(p) for p in tmp_dir_roots))
    display_repr = repr(tuple(str(p) for p in (display_roots if display_roots is not None else allowed_roots)))
    return f'''\
import builtins as _guard_builtins
import io as _guard_io
import os as _guard_os
import shutil as _guard_shutil

_GUARD_ALLOWED = [_guard_os.path.realpath(_p) for _p in {allowed_repr}]
_GUARD_DISPLAY = list(dict.fromkeys(_guard_os.path.realpath(_p) for _p in {display_repr}))
_GUARD_TMP_ROOTS = [_guard_os.path.realpath(_p) for _p in {tmp_roots_repr}]
_GUARD_OWN_TMP_NAME = "_tmp_" + (_guard_os.environ.get("AGENT_EXEC_TMP_NAME") or _guard_os.environ.get("AGENT_THREAD_ID", "_no_session"))


def _guard_check_foreign_tmp(_path):
    try:
        _target = _guard_os.path.realpath(_guard_os.fspath(_path))
    except TypeError:
        return
    for _root in _GUARD_TMP_ROOTS:
        if _target == _root:
            return
        if _target.startswith(_root + _guard_os.sep):
            _first = _target[len(_root) + 1 :].split(_guard_os.sep, 1)[0]
            if _first.startswith("_tmp_") and _first != _GUARD_OWN_TMP_NAME:
                _own_dir = _guard_os.path.join(_root, _GUARD_OWN_TMP_NAME)
                raise PermissionError(
                    f"[一時ディレクトリガード] 他セッションの一時ディレクトリへは"
                    f"アクセスできません: {{_path}}\\n"
                    f"あなた自身の一時フォルダを使ってやり直してください: {{_own_dir}}"
                )
            return


def _guard_check(_path, _op):
    _guard_check_foreign_tmp(_path)
    try:
        _target = _guard_os.path.realpath(_guard_os.fspath(_path))
    except TypeError:
        return
    for _root in _GUARD_ALLOWED:
        if _target == _root or _target.startswith(_root + _guard_os.sep):
            return
    if _GUARD_ALLOWED:
        _allowed_list = "、".join(_GUARD_DISPLAY or _GUARD_ALLOWED)
        raise PermissionError(
            f"[書き込みサンドボックスガード] 作業ディレクトリ配下以外は{{_op}}できません: {{_path}}\\n"
            f"次のいずれか配下に書き込み先を変更してやり直してください: {{_allowed_list}}\\n"
            "パスが分からない・合っているか不安な場合は check_work_dir_status ツールで確認してください。"
        )
    raise PermissionError(
        f"[書き込みサンドボックスガード] このツールは書き込み・削除が一切できません: {{_path}}\\n"
        "書き込みが必要な場合は execute_python_code ツールを使ってやり直してください。"
    )


_guard_orig_open = _guard_builtins.open


def _guard_open(_file, _mode="r", *_args, **_kwargs):
    if any(_c in _mode for _c in ("w", "a", "x", "+")):
        _guard_check(_file, "書き込み")
    else:
        _guard_check_foreign_tmp(_file)
    return _guard_orig_open(_file, _mode, *_args, **_kwargs)


_guard_builtins.open = _guard_open
_guard_io.open = _guard_open

for _guard_name in ("remove", "unlink", "rename", "replace", "rmdir", "removedirs", "mkdir", "makedirs", "truncate"):
    def _guard_make_os(_orig, _name):
        def _fn(_path, *_args, **_kwargs):
            _guard_check(_path, _name)
            if _name in ("rename", "replace") and _args:
                _guard_check(_args[0], _name)
            return _orig(_path, *_args, **_kwargs)

        return _fn

    _guard_orig = getattr(_guard_os, _guard_name, None)
    if _guard_orig is not None:
        setattr(_guard_os, _guard_name, _guard_make_os(_guard_orig, _guard_name))

for _guard_name in ("rmtree", "move"):
    def _guard_make_shutil(_orig, _name):
        def _fn(_src, *_args, **_kwargs):
            _guard_check(_src, _name)
            if _args:
                _guard_check(_args[0], _name)
            return _orig(_src, *_args, **_kwargs)

        return _fn

    _guard_orig = getattr(_guard_shutil, _guard_name, None)
    if _guard_orig is not None:
        setattr(_guard_shutil, _guard_name, _guard_make_shutil(_guard_orig, _guard_name))

for _guard_name in ("copy", "copy2", "copyfile", "copytree"):
    def _guard_make_shutil_copy(_orig, _name):
        def _fn(_src, *_args, **_kwargs):
            # コピー元は読み取りのみ（open()の読み取りモードと同じ扱い）。
            # 他セッションの一時ディレクトリからの読み取りだけは引き続きブロックする。
            _guard_check_foreign_tmp(_src)
            if _args:
                _guard_check(_args[0], _name)
            return _orig(_src, *_args, **_kwargs)

        return _fn

    _guard_orig = getattr(_guard_shutil, _guard_name, None)
    if _guard_orig is not None:
        setattr(_guard_shutil, _guard_name, _guard_make_shutil_copy(_guard_orig, _guard_name))

del _guard_name, _guard_orig

import subprocess as _guard_subprocess

_GUARD_BLOCKED_CMDS = {{
    "git", "npm", "pip", "pip3",
    "copy", "xcopy", "move", "robocopy", "del", "erase",
    "ren", "rename", "rd", "rmdir",
    "cmd", "cmd.exe", "powershell", "pwsh",
}}


def _guard_cmd_basename(_arg):
    try:
        _s = _guard_os.fspath(_arg)
    except TypeError:
        _s = _arg
    _base = _guard_os.path.basename(str(_s)).lower()
    for _ext in (".exe", ".cmd", ".bat"):
        if _base.endswith(_ext):
            _base = _base[: -len(_ext)]
            break
    return _base


def _guard_check_cmd(_args):
    if isinstance(_args, (str, bytes)):
        _tokens = str(_args).strip().split()
        _first = _tokens[0] if _tokens else ""
    elif isinstance(_args, _guard_os.PathLike):
        _first = _args
    elif _args:
        _first = _args[0]
    else:
        _first = ""
    if _guard_cmd_basename(_first) in _GUARD_BLOCKED_CMDS:
        raise PermissionError(
            f"[execute_python_codeガード] git/npm/pipコマンドの実行は禁止されています: {{_args}}"
            "。これはインストール済みかどうかとは無関係の一律禁止です。"
            "既存ライブラリは大抵インストール済みなので、先にimportやスクリプトの"
            "実行を試してください。それでも失敗する場合のみ、新規ライブラリが"
            "必要とユーザーに報告してください（このツールで自分でインストールする"
            "ことはできません）。"
        )


_guard_orig_popen_init = _guard_subprocess.Popen.__init__


def _guard_popen_init(self, args, *_a, **_kw):
    _guard_check_cmd(args)
    _guard_orig_popen_init(self, args, *_a, **_kw)


_guard_subprocess.Popen.__init__ = _guard_popen_init

_guard_orig_system = _guard_os.system


def _guard_os_system(_cmd):
    _guard_check_cmd(_cmd)
    return _guard_orig_system(_cmd)


_guard_os.system = _guard_os_system

_guard_orig_os_popen = _guard_os.popen


def _guard_os_popen(_cmd, *_a, **_kw):
    _guard_check_cmd(_cmd)
    return _guard_orig_os_popen(_cmd, *_a, **_kw)


_guard_os.popen = _guard_os_popen
'''
