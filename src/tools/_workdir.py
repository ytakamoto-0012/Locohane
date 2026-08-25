"""作業ディレクトリ（cwd）解決の共有ヘルパー。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import chainlit as cl

from . import _state

if TYPE_CHECKING:
    # 型注釈でのみ使用（check_work_dir_status.py との循環importを避けるため実行時はimportしない）。
    from .check_work_dir_status import WorkDirAccessStatus


def _foreign_tmp_dir_error(path: Path) -> str | None:
    """path が自セッション以外の `_tmp_<thread_id>` 配下なら拒否メッセージを返す。

    `_tmp_<thread_id>`（execute_python_code系の中間生成物置き場、
    `_resolve_exec_workdir()`）は作業ディレクトリ・default_workdir直下に
    全セッション共通の兄弟フォルダとして並ぶため、素朴にパスを許可すると
    他セッションの一時ファイルまで読めてしまう（LLMが他セッションの
    残留ファイルを自セッションの生成物と誤認する事故の原因）。

    誤検知を避けるため、「作業ディレクトリ/default_workdir 直下の
    最初の階層が `_tmp_` で始まる名前かどうか」だけを見る（パスの
    どこかに `_tmp_` を含む文字列があるかではない）。無関係な場所に
    たまたま `_tmp_` で始まる名前のフォルダがあっても影響しない。

    さらに、この階層が実際にディレクトリである場合のみ拒否する
    （`_foreign_tmp_dir_names()` と同じ条件）。`_resolve_exec_workdir()`
    が作るセッション作業フォルダは常にディレクトリであり、ファイルには
    なりえないため、LLMが作業ディレクトリ直下に `_tmp_ops.json` のような
    `_tmp_` で始まる名前の**ファイル**を自分で作成した場合まで誤って
    「他セッションの一時ディレクトリ」として読み取り拒否してしまう回帰が
    あった（2026-08-22 発見・修正）。

    Args:
        path: 解決済みの絶対パス（未解決でも内部で resolve() する）。

    Returns:
        他セッションの一時ディレクトリ配下と判定できればエラー文字列、
        そうでなければ None。
    """
    own_name = f"_tmp_{cl.user_session.get('thread_id') or '_no_session'}"
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in _tmp_dir_parents(_resolve_workdir()):
        try:
            rel = resolved.relative_to(parent)
        except ValueError:
            continue
        if not rel.parts:
            continue
        first = rel.parts[0]
        if first.startswith("_tmp_") and first != own_name:
            try:
                is_dir = (parent / first).is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                return f"エラー: 他セッションの一時ディレクトリ（{first}）は読み取れません。"
        return None
    return None

def _foreign_tmp_dir_names() -> frozenset[str]:
    """自セッション以外の `_tmp_<thread_id>` ディレクトリ名の集合を返す。

    Glob/Grep が作業ディレクトリ本体などの祖先から再帰検索する際、
    他セッションの `_tmp_<X>` サブツリーだけを走査・結果から除外するために
    glob_tool.py の `glob_search`/grep_tool.py の `grep_search` の
    `exclude_names` へ渡す。
    """
    own_name = f"_tmp_{cl.user_session.get('thread_id') or '_no_session'}"
    names: set[str] = set()
    for parent in _tmp_dir_parents(_resolve_workdir()):
        try:
            entries = list(parent.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("_tmp_") and entry.name != own_name and entry.is_dir():
                names.add(entry.name)
    return frozenset(names)

def _resolve_workdir(need_write: bool = False) -> Path:
    """run_script が subprocess.run に渡す cwd を決定する。

    Chainlit の ChatSettings（独自フロントエンドには表示されないが
    on_settings_update の経路自体は残っている）でユーザーがセッションに
    作業ディレクトリを設定していればそれを使い（app.py の
    on_settings_update が cl.user_session["work_dir"] に絶対パス文字列を
    保存する）、未設定なら config.ini の [default_workdir].dir
    （init_tools() で注入された _state._DEFAULT_WORKDIR）にフォールバックする。

    サーバー/クライアントでファイルシステムが分離している環境（別PCから
    利用する場合）では、ユーザー指定の work_dir がサーバー側から見て
    アクセス不可・書き込み不可なことがある。app.py の _apply_work_dir が
    設定時に cl.user_session["work_dir_access"]（WorkDirAccessStatus）へ
    実測結果をキャッシュしており、ここではそれを参照して機械的に
    default_workdir へフォールバックする（LLMが確認を怠っても安全側に
    倒れる）。読み取り専用共有から既存ファイルを読ませたいだけのケースを
    妨げないよう、need_write=False（既定）では読み取り可否のみを見る。

    read_skill / read_skill_file / スクリプト本体の場所解決には影響しない
    （それらは常に _safe_path 経由で skills ルート配下に固定される）。

    Args:
        need_write: True の場合、書き込み可否（status.writable）も見て
            フォールバック判定する。既存ファイルの読み取りのみが目的の
            呼び出し元は False のままでよい。

    Returns:
        呼び出し元が使う絶対パス。work_dir が未設定、またはアクセス不可・
        （need_write時は）書き込み不可と判定されていれば default_workdir。

    Raises:
        RuntimeError: init_tools() が未実行で _state._DEFAULT_WORKDIR が None の場合。
    """
    if _state._DEFAULT_WORKDIR is None:
        raise RuntimeError("init_tools() が未実行です")
    work_dir = cl.user_session.get("work_dir")
    if not work_dir:
        return _state._DEFAULT_WORKDIR
    status: WorkDirAccessStatus | None = cl.user_session.get("work_dir_access")
    if status is not None:
        if not status.exists or not status.readable:
            return _state._DEFAULT_WORKDIR
        if need_write and not status.writable:
            return _state._DEFAULT_WORKDIR
    return Path(work_dir)

def _resolve_exec_workdir() -> Path:
    """execute_python_code / run_script が中間生成物を書く実行用ディレクトリ。

    常に default_workdir 配下に `_tmp_<thread_id>` を作って返す（無ければ
    作成する）。LLMがコード内で相対パスで書き出すファイル（ops.json 等の
    中間生成物）が作業ディレクトリ直下に散らからないようにするため。
    `_tmp/<thread_id>` のような親子階層ではなく `_tmp_<thread_id>` という
    単一のディレクトリ名にしているのは、日数ベースの自動削除（cleanup_old_dirs、
    app.py）が丸ごと rmtree した際、親ディレクトリ（`_tmp`）が空のまま
    残り続ける問題を避けるため。

    以前はユーザー指定の work_dir 直下に作っていたが、生成中に別スレッドへ
    切り替えるとソケット切断（on_chat_end）で即座に rmtree され、裏で
    継続中の処理と競合する問題があった。default_workdir 固定にすることで
    on_chat_end での即時削除自体を廃止し、default_workdir_retention_days
    による日数ベースの自動削除（app.py）に一本化した（work_dir は元々
    サーバー/クライアントでファイルシステムが分離しうるため書き込み不可の
    こともあり得るが、default_workdir はサーバー側の設定のため常に
    書き込み可能という前提が置ける）。

    provide_download / show_image / _resolve_analyze_image_path は
    ユーザーへの成果物提供に使う関数のため、意図的にこの関数を使わず
    _resolve_workdir() のまま据え置く（最終成果物はユーザー指定の work_dir
    直下に置かれる想定のため。execute_python_code の書き込みガード
    （_exec_guard_roots）は _tmp_<thread_id> の実体とは独立に、実際の
    work_dir も allowed_roots へ含めている）。

    Returns:
        `_tmp_<thread_id>` ディレクトリの絶対パス。
    """
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    d = _state._DEFAULT_WORKDIR / f"_tmp_{thread_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _restrict_default_workdir(path: Path) -> Path:
    """書き込み先/cwd候補が default_workdir そのものなら `_tmp_<thread_id>` へ縮小する。

    default_workdir はサーバー側の共有ディレクトリで全セッションから同じ
    パスが見えるため、直下やその他のサブディレクトリへ書き込みを許すと、
    別セッションがそのファイルを参照して誤動作する事故につながる
    （work_dir 未設定時は run_script/execute_python_code の書き込み先が
    素朴には default_workdir 直下になっていたため発生しえた）。
    自セッション専用の `_tmp_<thread_id>`（_resolve_exec_workdir() と同じ
    ディレクトリ）だけに縮小することで、成果物は常にセッション専用領域へ
    閉じ込め、ユーザーへの提示は provide_download/show_image 経由に統一する。

    work_dir がユーザー指定で default_workdir と無関係な場所を指している
    場合はそのまま通す（制限対象は default_workdir そのものが渡された
    ケースのみ）。

    Args:
        path: 書き込み許可ルート、または cwd の候補（_resolve_workdir() の
            戻り値など）。

    Returns:
        path が default_workdir と同一なら `_tmp_<thread_id>`、それ以外は
        path をそのまま返す。
    """
    if _state._DEFAULT_WORKDIR is not None and path.resolve() == _state._DEFAULT_WORKDIR:
        return _resolve_exec_workdir()
    return path

def _tmp_dir_parents(primary: Path) -> list[Path]:
    """`_tmp_<thread_id>` が実際に作られうる親ディレクトリの一覧を返す。

    `_resolve_exec_workdir()` は通常 `primary`（呼び出し元が渡す実行用
    ディレクトリの親、または run_script の cwd そのもの）配下に
    `_tmp_<thread_id>` を作るが、mkdir失敗時は `_state._DEFAULT_WORKDIR` 配下へ
    フォールバックする（1402-1408行目）。他セッションの `_tmp_<X>` 検出
    （`_foreign_tmp_dir_error`/`_foreign_tmp_dir_names`/ガードプリアンブルの
    `tmp_dir_roots`）は、このフォールバック先も含めて両方を見る必要がある。

    Args:
        primary: 実行用ディレクトリの親（execute_python_code系）、または
            run_script の cwd（run_script系。この場合それ自体が親）。

    Returns:
        重複・非存在を除いた既存ディレクトリのリスト。
    """
    candidates = [primary]
    if _state._DEFAULT_WORKDIR is not None:
        candidates.append(_state._DEFAULT_WORKDIR)
    seen: set[Path] = set()
    result: list[Path] = []
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result
