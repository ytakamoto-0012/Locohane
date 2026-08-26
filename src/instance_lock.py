"""同一データディレクトリに対する多重起動を防ぐ、プロセス排他ロック。

data/ 配下の checkpoints.sqlite（LangGraph の会話状態）・chat_threads.sqlite
（スレッド一覧、src/thread_store.py）は、プロセス内で1本の aiosqlite 接続を
アプリ寿命ずっと使い回す設計になっており、複数プロセスから同時に書き込まれる
ことを想定していない。ユーザーが誤って同じ data/ を指す状態で Locohane を
二重起動（例: ターミナルを2つ開いて同じ app.bat を実行）すると、2つの
プロセスが同じ .sqlite ファイルへ別々に書き込み合い、
"database disk image is malformed" 等でファイルが破損する事故が実際に
発生した（2026-08-26 ユーザー報告）。

このモジュールは data/ 直下に app.lock という空ファイルを作り、OSのファイル
ロック（Windows: msvcrt.locking）で排他制御する。ロックはプロセスが保持する
ファイルハンドルに紐づき、プロセス終了時（正常終了・クラッシュ問わず）に
OSが自動的に解放するため、PIDファイル方式のような「前回異常終了時の古い
ロックが残り続けて誤検知する」問題が起きない。
"""

from __future__ import annotations

import msvcrt
from pathlib import Path

# プロセス生存中、ハンドルを保持し続けるためのモジュールグローバル。
# ローカル変数のままにすると関数を抜けた時点でGCされ、ロックが即座に
# 解放されてしまう。
_lock_file = None


class InstanceAlreadyRunningError(RuntimeError):
    """同じデータディレクトリに対して、既に別の Locohane プロセスが起動中。"""


def acquire(lock_path: Path) -> None:
    """lock_path に対する排他ロックを取得する。

    既に別プロセスが保持している場合は InstanceAlreadyRunningError を送出する
    （ロックは取得できない＝起動を続けさせない）。呼び出し元はできる限り早い
    タイミング（DBファイルを開く前）で呼ぶこと。
    """
    global _lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not lock_path.exists() or lock_path.stat().st_size == 0:
        # msvcrt.locking はロック対象バイト範囲が実際にファイル内に存在する
        # ことを要求するため、新規作成時（0バイト）は1バイト書いておく。
        # 既に別プロセスがロック中でもここは新規作成時のみの経路であり
        # 到達しない（作成済みなら st_size>0 でスキップされる）ため競合しない。
        lock_path.write_bytes(b"0")
    f = open(lock_path, "r+b")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        f.close()
        raise InstanceAlreadyRunningError(
            f"Locohane は既に別プロセスで起動中です（データディレクトリ: {lock_path.parent}）。"
            "同じ data/ を共有したまま二重起動すると checkpoints.sqlite / "
            "chat_threads.sqlite が同時書き込みで破損するため、起動を中止しました。"
            "先に起動済みのプロセス（別のターミナル/ポート）を終了してから再実行してください。"
        ) from exc
    _lock_file = f


def release() -> None:
    """保持中のロックを解放する（プロセス正常終了時のベストエフォート）。

    未取得なら何もしない。プロセスがクラッシュした場合でもOSがハンドルを
    自動的に閉じるためロックは解放される。
    """
    global _lock_file
    if _lock_file is None:
        return
    try:
        _lock_file.seek(0)
        msvcrt.locking(_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        _lock_file.close()
        _lock_file = None
