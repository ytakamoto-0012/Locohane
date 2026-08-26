"""src/instance_lock.py（多重起動防止の排他ロック）の回帰テスト。"""

from __future__ import annotations

import msvcrt

import pytest

from src import instance_lock


@pytest.fixture(autouse=True)
def _release_after_each_test():
    """各テスト後に本プロセスが保持したロックを必ず解放し、次のテストへ持ち越さない。"""
    yield
    instance_lock.release()


def test_acquire_creates_lock_file(tmp_path):
    lock_path = tmp_path / "app.lock"
    instance_lock.acquire(lock_path)
    assert lock_path.exists()


def test_second_acquire_in_same_process_fails(tmp_path):
    """同一プロセス内でも、既にロック中のバイト範囲を再ロックしようとすると失敗する
    （別プロセスからの二重起動を模擬するのに十分な検証になる。実際の二重起動
    シナリオはOS側の排他制御そのものであり、プロセスをまたいでも同じAPIが
    同じ理由で失敗する）。
    """
    lock_path = tmp_path / "app.lock"
    instance_lock.acquire(lock_path)

    # 別プロセスを模して、独立したファイルハンドルから同じロックを取りにいく。
    f = open(lock_path, "r+b")
    try:
        with pytest.raises(OSError):
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    finally:
        f.close()


def test_release_allows_reacquire(tmp_path):
    lock_path = tmp_path / "app.lock"
    instance_lock.acquire(lock_path)
    instance_lock.release()

    # 解放後は同じパスへ再取得できる（プロセス終了時にOSが自動解放するのと同じ効果）。
    instance_lock.acquire(lock_path)


def test_release_without_acquire_is_noop():
    instance_lock.release()


def test_acquire_creates_parent_directory(tmp_path):
    lock_path = tmp_path / "nested" / "data" / "app.lock"
    instance_lock.acquire(lock_path)
    assert lock_path.exists()
