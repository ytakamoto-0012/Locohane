"""パスメモリー（ファイルパスの短縮参照）レジストリの読み書き。

Read/Glob/Grep 等のツールが返す長い絶対パスに、短い数値インデックス（@N）を
自動で割り当てて記録し、以降のツール呼び出しでは @N を渡せば実パスに解決
できるようにする。ローカルLLMが長いパス文字列を複数回のツール呼び出しに
またがって正確に再生成できずタイプミスを頻発させる問題への対策。

src/tools.py が `from . import path_memory` で直接importし、Read/Glob/Grep/
json_query/list_path_memory/analyze_image/run_script の @N 登録・解決に使う
（旧 skills/path-memory/scripts/_registry.py。ISSUE-003 で SKILL.md を持つ
Agent Skill としての公開をやめ、アプリ基盤側の内部実装モジュールとして
src/ へ移設した）。動作パラメータ（thread_id・レジストリ保存先・登録上限）は
関数の引数として明示的に渡す方式に統一し、環境変数の読み取りは run_script
経由でサブプロセスとして起動される他スキルのスクリプトが自己登録したい
場合のための env_params() にのみ閉じる。
"""

from __future__ import annotations

import contextlib
import json
import msvcrt
import os
import re
import time
from pathlib import Path

_TOKEN_RE = re.compile(r"^@(\d+)$")
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL_SECONDS = 0.05


@contextlib.contextmanager
def _locked(lock_path: Path):
    """`register()` の read-modify-write をプロセス・タスク間で排他制御する。

    モデルが同一ターンで複数のツール呼び出し（Read/Glob/Grep等の並列実行、
    または `run_script` 経由で別プロセス起動される他スキルのスクリプト）を
    並列に発行すると、複数の呼び出しが同時にレジストリJSONを読み込み・
    追記・保存するため、ロック無しでは後勝ちで前の登録が失われるrace
    conditionが起きる（tune-prompt調査、020/021ケースで「@N が見つかりません」
    として実際に発生）。

    Windows標準の `msvcrt.locking()` のみを使い、pip追加依存を避ける。
    ロック保持プロセスが異常終了してもOSがハンドルクローズ時に自動で
    ロックを解放するため、stale lock（陳腐化したロックファイル）の
    後始末は不要。

    Args:
        lock_path: サイドカーロックファイルのパス（レジストリ本体の
            JSONファイルとは別ファイル）。

    Yields:
        ロックを取得できたら True、`_LOCK_TIMEOUT_SECONDS` 以内に
        取得できなければ False（呼び出し側は書き込みを諦めること）。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")
    try:
        # サイズ0のファイルは msvcrt.locking() のロック対象バイトが
        # 存在しないため、確実にロックできるよう1バイト確保しておく。
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"\0")
            f.flush()
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        acquired = False
        while True:
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        try:
            yield acquired
        finally:
            if acquired:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()


def is_path_memory_token(token: str) -> bool:
    """文字列が `@N`（N は1以上の整数）形式のパスメモリー参照かどうかを判定する。"""
    return bool(_TOKEN_RE.match(token))


def _registry_path(thread_id: str, path_memory_dir: Path) -> Path:
    return path_memory_dir / f"{thread_id}.json"


def _load(registry_path: Path) -> list[dict]:
    if not registry_path.is_file():
        return []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save(registry_path: Path, entries: list[dict]) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def register(
    thread_id: str,
    path: str,
    path_memory_dir: Path,
    max_entries: int,
    description: str | None = None,
) -> int | None:
    """パスをレジストリへ登録し、1始まりのインデックスを返す。

    Args:
        thread_id: 登録先の会話を識別する文字列。
        path: 登録する絶対パス文字列。
        path_memory_dir: レジストリファイルの保存先ディレクトリ。
        max_entries: 1会話あたりの登録上限件数。
        description: このパスに添える短い説明（例: "execute_python_codeが
            新規作成"）。省略時（None）は付与しない。

    Returns:
        登録（または既存エントリの再利用）に成功すればそのインデックス
        （1始まり）。上限に達していて新規登録できない場合、または
        ロック取得がタイムアウトした場合は None。

    Notes:
        同一パスが既に登録済みの場合は新規追加せず、そのインデックスを
        再利用しつつ `valid`（ファイルが実在するか）を最新の状態へ更新する。
        この場合 `description` を渡していれば既存エントリの説明も
        上書きする（省略時は既存の説明を保持する）。
        読み込みから書き込みまでをファイルロックで排他制御しており、
        並列呼び出し時のrace conditionによる登録消失を防ぐ（`_locked`
        docstring参照）。
    """
    registry_path = _registry_path(thread_id, path_memory_dir)
    lock_path = registry_path.parent / f"{registry_path.name}.lock"
    with _locked(lock_path) as acquired:
        if not acquired:
            return None
        entries = _load(registry_path)
        for i, entry in enumerate(entries):
            if entry.get("path") == path:
                entries[i]["valid"] = Path(path).exists()
                if description is not None:
                    entries[i]["description"] = description
                _save(registry_path, entries)
                return i + 1
        if len(entries) >= max_entries:
            return None
        entries.append({"path": path, "valid": Path(path).exists(), "description": description})
        _save(registry_path, entries)
        return len(entries)


def resolve(thread_id: str, token: str, path_memory_dir: Path) -> str | None:
    """`@N` 形式のトークンを実パスへ解決する。

    Args:
        thread_id: 会話を識別する文字列。
        token: 解決したい文字列（`@N` 形式でなければ即 None）。
        path_memory_dir: レジストリファイルの保存先ディレクトリ。

    Returns:
        該当インデックスのパス。トークンが `@N` 形式でない、または該当する
        登録が無い場合は None。
    """
    m = _TOKEN_RE.match(token)
    if not m:
        return None
    index = int(m.group(1))
    entries = _load(_registry_path(thread_id, path_memory_dir))
    if 1 <= index <= len(entries):
        return entries[index - 1].get("path")
    return None


def list_entries(thread_id: str, path_memory_dir: Path) -> list[dict]:
    """登録済み全件を返す。

    Args:
        thread_id: 会話を識別する文字列。
        path_memory_dir: レジストリファイルの保存先ディレクトリ。

    Returns:
        `[{"index": int, "path": str, "valid": bool, "description": str | None}, ...]`
        （登録順）。
    """
    entries = _load(_registry_path(thread_id, path_memory_dir))
    return [
        {
            "index": i + 1,
            "path": e.get("path"),
            "valid": bool(e.get("valid", False)),
            "description": e.get("description"),
        }
        for i, e in enumerate(entries)
    ]


def exec_tmp_dir(category: str | None = None) -> Path:
    """execute_python_code の中間生成物と同じ `_tmp_<thread_id>/` を返す（無ければ作成する）。

    run_script 経由でサブプロセス起動される他スキルのスクリプトが、自分専用の
    中間生成物置き場を作りたい場合に使う。基準は run_script の cwd
    （_resolve_workdir() の結果である作業ディレクトリ）ではなく、常に
    default_workdir（AGENT_DEFAULT_WORKDIR）にする。cwd はユーザーが
    ChatSettings で指定した work_dir になりうるが、work_dir は保持日数ベースの
    自動削除（default_workdir の retention_days）の対象外のため、cwd を基準に
    すると中間生成物が消えずに溜まり続ける事故につながる（過去に実際に
    発生。cwd基準はバグであり仕様ではない）。ディレクトリ名は
    AGENT_EXEC_TMP_NAME（`_exec_tmp_name()` が生成する作成時刻プレフィックス
    付きthread_id。メインプロセスの `_resolve_exec_workdir()` と同じ名前）を
    読み、未設定時は AGENT_THREAD_ID（env_params() と同じ、生のthread_id）
    へフォールバックし、どちらも無ければ "_no_session" にフォールバックする。

    Args:
        category: `_tmp_<name>` 直下にさらに切るサブディレクトリ名
            （例: "pdf_rendered"）。省略時は `_tmp_<name>` 自体を返す。

    Returns:
        作成済みの絶対パス。
    """
    name = os.environ.get("AGENT_EXEC_TMP_NAME") or os.environ.get("AGENT_THREAD_ID") or "_no_session"
    base = Path(os.environ.get("AGENT_DEFAULT_WORKDIR") or "./data/temp")
    out_dir = base / f"_tmp_{name}"
    if category:
        out_dir = out_dir / category
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def env_params() -> tuple[str, Path, int]:
    """環境変数から (thread_id, path_memory_dir, max_entries) を読む。

    run_script 経由でサブプロセスとして起動される他スキルのスクリプトが、
    自身の出力パスを自己登録したい場合に使う（src/path_memory.py を
    sys.path 経由でimportし、この関数でパラメータを取得する）。
    src/tools.py からの直接import（同一プロセス内呼び出し）はこの関数を
    使わず、パラメータを直接引数で渡す。

    Returns:
        (thread_id, path_memory_dir, max_entries) のタプル。環境変数が
        未設定の場合はそれぞれ "_no_session" / "./data/path_memory" / 500
        にフォールバックする。
    """
    thread_id = os.environ.get("AGENT_THREAD_ID") or "_no_session"
    path_memory_dir = Path(os.environ.get("AGENT_PATH_MEMORY_DIR") or "./data/path_memory")
    max_entries = int(os.environ.get("AGENT_PATH_MEMORY_MAX_ENTRIES") or "500")
    return thread_id, path_memory_dir, max_entries
