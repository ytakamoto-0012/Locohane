"""run_script/execute_python_code のサブプロセス環境変数・書き込みガード注入。"""

from __future__ import annotations

from pathlib import Path
import chainlit as cl
import logging
import os
import tempfile

from . import _state
from ._python_fs_guard import _python_fs_guard_preamble
from ._workdir import _exec_tmp_name, _resolve_exec_workdir, _tmp_dir_parents

logger = logging.getLogger(__name__)


def _subprocess_env() -> dict[str, str]:
    """run_script/execute_python_code の子プロセスへ渡す環境変数を組み立てる。

    既存の PYTHONIOENCODING（日本語文字化け対策）に加え、パスメモリー
    （src/path_memory.py）用の AGENT_THREAD_ID/AGENT_PATH_MEMORY_DIR/
    AGENT_PATH_MEMORY_MAX_ENTRIES を注入する。run_script 経由で実行される
    スキルのスクリプトは、これらを `path_memory.env_params()` で読み、
    自分が出力するパスをレジストリへ登録できる。
    AGENT_SRC_DIR は execute_python_code のサブプロセスが
    `src/path_memory.py` をインポートするために使う。
    AGENT_DEFAULT_WORKDIR は `_resolve_exec_workdir()`/`path_memory.exec_tmp_dir()`
    が中間生成物置き場 `_tmp_<name>/` の基準ディレクトリを決めるために使う
    （run_script の cwd が指すユーザー指定 work_dir は保持日数ベースの自動削除
    対象外のため、cwd 基準にすると中間生成物が消えずに溜まり続ける。常に
    default_workdir 基準に固定することで自動削除の対象に含める。
    `_restrict_default_workdir` 参照）。
    AGENT_EXEC_TMP_NAME は `_tmp_<name>/` の `<name>` 部分（`_exec_tmp_name()`
    が生成する、作成時刻プレフィックス付きthread_id）。サブプロセス側
    （skills配下の各スクリプト・`path_memory.exec_tmp_dir()`）はこの値を使って
    メインプロセスの `_resolve_exec_workdir()` と同じディレクトリ名を組み立てる。
    未設定時は AGENT_THREAD_ID（生のthread_id、時刻プレフィックス無し）へ
    フォールバックする。

    config.ini `[paths].bin_path`（既定は空）に列挙されたディレクトリを PATH の
    先頭へ追加する。コマンド名を素の状態で叩く外部バイナリのスキルは、事前に
    ユーザーがOS側のPATH環境変数へ手動登録していないと「コマンドが見つからない」
    で失敗する。config.ini に配置先を明示しておけば、evals・app.py実行時の
    どちらでも追加の手動設定なしで呼び出せる。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env["AGENT_THREAD_ID"] = cl.user_session.get("thread_id") or "_no_session"
    env["AGENT_EXEC_TMP_NAME"] = _exec_tmp_name()
    if _state._PATH_MEMORY_DIR is not None:
        env["AGENT_PATH_MEMORY_DIR"] = str(_state._PATH_MEMORY_DIR)
    env["AGENT_PATH_MEMORY_MAX_ENTRIES"] = str(_state._PATH_MEMORY_MAX_ENTRIES)
    env["AGENT_SRC_DIR"] = str(_state._SRC_DIR)
    if _state._DEFAULT_WORKDIR is not None:
        env["AGENT_DEFAULT_WORKDIR"] = str(_state._DEFAULT_WORKDIR)
    cfg = _state._LLM_CONFIG
    if cfg is not None:
        bin_dirs = [d for d in cfg.bin_path if d.is_dir()]
        if bin_dirs:
            env["PATH"] = os.pathsep.join([*(str(d) for d in bin_dirs), env.get("PATH", "")])
    return env

def _run_script_guard_env(workdir: Path) -> tuple[dict[str, str], Path | None]:
    """run_script/run_script_background が起動するサブプロセスへ、書き込み
    サンドボックスガードを注入した環境変数を組み立てる。

    run_script はスキル作者が書いた既存の scripts/ 配下のファイルをそのまま
    実行するため、execute_python_code のようにコード文字列の先頭へガードの
    ソースを連結する方法が使えない（対象ファイルを書き換えるのは事故の元）。
    代わりに Python がサブプロセス起動時に sys.path 上の sitecustomize
    モジュールを自動 import する仕組みを利用し、_python_fs_guard_preamble が
    生成するのと同じモンキーパッチコードを一時ディレクトリへ sitecustomize.py
    として書き出し、PYTHONPATH の先頭に追加することで対象スクリプトのソースを
    一切変更せずに書き込み・削除系呼び出しへガードを差し込む。

    許可されるのは workdir（run_script の cwd = 作業ディレクトリ。呼び出し元の
    _prepare_script_execution が _restrict_default_workdir() 済みのため、
    work_dir未設定等でdefault_workdirへフォールバックした場合はここで
    既に `_tmp_<thread_id>` になっている）・`_tmp_<thread_id>`（念のため
    ここでも明示的に加える。default_workdirはサーバー側の共有ディレクトリの
    ため、直下やその他のサブディレクトリへの書き込みは許可せず、常に
    自セッション専用の`_tmp_<thread_id>`のみに限定する。他セッションが
    生成物を誤参照する事故の防止）・path_memory_dir（register_output_path() が
    ロックファイルを書き込むLocohane内部の状態ディレクトリ。ユーザー成果物の
    保存先ではないためexecute_python_code側のallowed_rootsとは異なりここにのみ追加）
    配下のみ。それ以外の場所（他ドライブ・Locohaneプロジェクト本体・
    default_workdir直下の他ディレクトリを含む）への書き込み・削除は
    PermissionError でブロックされる。読み取りは対象外だが、
    `_tmp_<thread_id>`（`workdir`直下に全セッション共通で並ぶ一時フォルダ）に
    限っては、自セッション以外への読み取り・書き込み・削除を allowed_roots の
    内外を問わず追加でブロックする（`_python_fs_guard_preamble` の
    `tmp_dir_roots` 参照）。

    Args:
        workdir: このスクリプト実行の cwd（_resolve_workdir の解決結果）。

    Returns:
        (env, guard_dir) のタプル。guard_dir は呼び出し側がサブプロセス
        終了後に削除する一時ディレクトリ。一時ファイル作成に失敗した場合は
        guard_dir が None になり、env にはガード無しの _subprocess_env() の
        結果のみが入る（ガード注入の失敗自体でスクリプト実行を止めない。
        書き込み制限が効かなくなるだけで、通常の権限エラー等は従来通り
        OS側で発生する）。
    """
    env = _subprocess_env()
    allowed_roots = [workdir]
    if _state._DEFAULT_WORKDIR is not None:
        allowed_roots.append(_resolve_exec_workdir())
    display_roots = list(allowed_roots)
    if _state._PATH_MEMORY_DIR is not None:
        allowed_roots.append(_state._PATH_MEMORY_DIR)
    try:
        guard_dir = Path(tempfile.mkdtemp(prefix="agent_fs_guard_"))
        guard_src = _python_fs_guard_preamble(
            allowed_roots, tmp_dir_roots=_tmp_dir_parents(workdir), display_roots=display_roots
        )
        (guard_dir / "sitecustomize.py").write_text(guard_src, encoding="utf-8")
    except OSError:
        logger.warning("run_script: 書き込みガード用の一時ファイル作成に失敗したため、ガード無しで実行します。")
        return env, None
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(guard_dir) + (os.pathsep + existing_path if existing_path else "")
    return env, guard_dir
