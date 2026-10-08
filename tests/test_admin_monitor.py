"""admin/monitor.py（モニター画面の読み取り処理）と src/runtime_status.py のテスト。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from admin import monitor
from src import runtime_status


def _token_line(ts: str, thread_id: str, call_in: int, cum: int, cum_main: int) -> str:
    return (
        f"{ts},123 [thread={thread_id}] INFO app.py: トークン使用量 thread_id={thread_id} "
        f"call(in={call_in},out=10,total={call_in + 10}) turn(in=1,out=1,total=2) "
        f"cumulative(in=1,out=1,total={cum}) cumulative_main(in=1,out=1,total={cum_main})\n"
    )


def test_token_history_classifies_sub_calls_and_compaction(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tid = "t-1"
    (log_dir / "app_20261001_000000.log").write_text(
        _token_line("2026-10-01 00:00:01", tid, 100, 110, 110)
        + _token_line("2026-10-01 00:00:02", "other", 999, 999, 999)
        # サブエージェント: cumulative_main が増えない
        + _token_line("2026-10-01 00:00:03", tid, 50, 170, 110),
        encoding="utf-8",
    )
    # 後のファイル。圧縮で cumulative_main が 0 に戻った後の呼び出し
    (log_dir / "app_20261001_120000.log").write_text(
        _token_line("2026-10-01 12:00:00", tid, 30, 210, 40), encoding="utf-8"
    )
    points = monitor.token_history(log_dir, tid)
    assert [p["call_in"] for p in points] == [100, 50, 30]
    assert [p["is_sub"] for p in points] == [False, True, False]
    assert [p["compacted"] for p in points] == [False, False, True]
    assert points[0]["ts"] == "2026-10-01T00:00:01"


def test_tail_log_filters_level_and_joins_multiline(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app_20261001_000000.log").write_text(
        "2026-10-01 00:00:01,1 [thread=-] INFO x: info\n"
        "2026-10-01 00:00:02,1 [thread=abc] ERROR x: boom\n"
        "Traceback (most recent call last):\n"
        "  ValueError\n"
        "2026-10-01 00:00:03,1 [thread=-] WARNING x: warn\n",
        encoding="utf-8",
    )
    entries = monitor.tail_log(log_dir, min_level="WARNING")
    assert [e["level"] for e in entries] == ["WARNING", "ERROR"]
    assert "ValueError" in entries[1]["message"]
    assert entries[1]["thread_id"] == "abc"
    assert monitor.tail_log(log_dir, min_level="INFO", thread_id="abc")[0]["message"].startswith("boom")


@pytest.fixture
def thread_db(tmp_path: Path) -> Path:
    db = tmp_path / "chat_threads.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT, created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', tags_json TEXT);"
        "CREATE TABLE steps (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at TEXT, data_json TEXT NOT NULL);"
    )
    meta = json.dumps({"token_usage_cumulative": {"input": 90, "output": 10, "total": 100}})
    conn.execute("INSERT INTO threads VALUES ('t1','alice','会話1','2026-10-01T00:00:00','2026-10-01T01:00:00',?,NULL)", (meta,))
    conn.execute("INSERT INTO threads VALUES ('t2','bob','会話2','2026-10-01T00:00:00','2026-10-01T02:00:00','{}',NULL)")
    steps = [
        {"id": "s1", "type": "assistant_message", "output": "📁 作業ディレクトリ: 未設定"},
        {"id": "s2", "type": "user_message", "output": "こんにちは"},
        {"id": "s3", "type": "tool", "name": "Read", "input": "{}", "output": "x" * (monitor.MAX_FIELD_CHARS + 5)},
        {"id": "s4", "type": "assistant_message", "output": "はい"},
    ]
    for i, s in enumerate(steps):
        conn.execute("INSERT INTO steps VALUES (?, 't1', ?, ?)", (s["id"], f"2026-10-01T00:00:0{i}", json.dumps(s)))
    conn.commit()
    conn.close()
    return db


def test_thread_detail_hides_control_and_internal_steps_by_default(thread_db: Path):
    detail = monitor.thread_detail(thread_db, "t1")
    assert [s["output"] for s in detail["steps"]] == ["こんにちは", "はい"]
    assert detail["token_usage_cumulative"]["total"] == 100

    full = monitor.thread_detail(thread_db, "t1", include_internal=True)
    assert [s["id"] for s in full["steps"]] == ["s1", "s2", "s3", "s4"]
    assert full["steps"][2]["truncated"] is True
    assert monitor.thread_detail(thread_db, "missing") is None


def test_list_threads_and_user_summary(thread_db: Path):
    result = monitor.list_threads(thread_db, owner="alice")
    assert result["total"] == 1
    assert result["threads"][0]["tokens_total"] == 100
    assert result["threads"][0]["user_messages"] == 1
    assert monitor.list_threads(thread_db, query="会話2")["threads"][0]["id"] == "t2"
    summary = {u["user"]: u for u in monitor.user_summary(thread_db)}
    assert summary["alice"]["tokens_total"] == 100
    assert summary["bob"]["threads"] == 1


def test_summarize_runtime_groups_by_user():
    status = {
        "pid": 1,
        "sessions": [
            {"session_id": "a", "user": "alice", "thread_id": "t1", "has_first_interaction": True},
            {"session_id": "b", "user": "alice", "thread_id": "t9", "has_first_interaction": False},
        ],
        "generating": [{"thread_id": "t2", "owner": "bob", "started_at": None, "waiting_for_user": True}],
        "updated_at": "x",
    }
    view = monitor.summarize_runtime(status, {"t1": "会話1", "t2": "会話2"})
    assert view["users"] == [
        {"user": "alice", "sessions": 2, "generating": 0},
        {"user": "bob", "sessions": 0, "generating": 1},
    ]
    assert view["generating"][0]["thread_name"] == "会話2"
    assert monitor.summarize_runtime(None, {})["available"] is False


def test_runtime_status_writer_writes_only_on_change(tmp_path: Path):
    path = tmp_path / runtime_status.RUNTIME_STATUS_FILENAME
    snapshots = [{"sessions": [1]}, {"sessions": [1]}, {"sessions": [2]}]
    writes: list[dict] = []
    original = runtime_status.write_atomic

    def spy(p, data):
        writes.append(data)
        original(p, data)

    def collect():
        if not snapshots:
            raise asyncio.CancelledError
        return snapshots.pop(0)

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await runtime_status.run_writer_loop(path, collect, 0)

    import unittest.mock

    with unittest.mock.patch.object(runtime_status, "write_atomic", spy):
        asyncio.run(run())
    assert [w["sessions"] for w in writes] == [[1], [2]]
    assert json.loads(path.read_text(encoding="utf-8"))["sessions"] == [2]
    runtime_status.remove(path)
    assert not path.exists()
