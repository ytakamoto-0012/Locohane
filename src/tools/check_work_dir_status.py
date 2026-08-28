"""check_work_dir_status ツールと作業ディレクトリアクセス確認。"""

from __future__ import annotations

from dataclasses import dataclass
from langchain_core.tools import tool
from pathlib import Path
import chainlit as cl
import json
import os
import uuid

from . import _state
from ._workdir import _resolve_exec_workdir


@dataclass
class WorkDirAccessStatus:
    """作業ディレクトリへの実際のアクセス可否（probe_workdir_access の戻り値）。

    サーバー駆動でローカルネットワーク上の別PCから利用される場合、ユーザーの
    PCからは見える/書き込めるパスでも、サーバープロセス側からは見えない、
    または見えても書き込み権限が無い（読み取り専用共有など）ことがある。
    os.access() はWindowsのネットワーク共有・ACL構成では実態と食い違う
    ことがあるため、実際のI/Oで判定する（probe_workdir_access 参照）。
    """

    path: str
    exists: bool
    readable: bool
    writable: bool
    error: str | None = None


def probe_workdir_access(path: Path) -> WorkDirAccessStatus:
    """作業ディレクトリの読み取り/書き込み可否を実際のI/Oで検証する。

    Args:
        path: 検証対象のディレクトリパス。

    Returns:
        存在確認・読み取り確認（os.listdir）・書き込み確認（一時ファイルの
        作成/削除）の結果をまとめた WorkDirAccessStatus。
    """
    if not path.is_dir():
        return WorkDirAccessStatus(str(path), exists=False, readable=False, writable=False)
    try:
        os.listdir(path)
    except OSError as e:
        return WorkDirAccessStatus(str(path), exists=True, readable=False, writable=False, error=str(e))
    probe = path / f".agent_write_test_{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return WorkDirAccessStatus(str(path), exists=True, readable=True, writable=False, error=str(e))
    return WorkDirAccessStatus(str(path), exists=True, readable=True, writable=True)

@tool
def check_work_dir_status() -> str:
    """作業ディレクトリのアクセス状況を確認する。

    run_script / execute_python_code が原因不明の読み書きエラーを返した
    ときに使う。

    Returns:
        absolute_path・state（read_write/read_only/unreadable/not_found）・
        read_dir・write_dir 等をキーに持つJSON文字列。パス値を他の説明文と
        混在させると低パラメータモデルが説明文込みで1つのパスとして誤認識
        することがあるため、パスは必ず独立したキーに分離する。書き込みの
        フォールバック先は既定フォルダ直下ではなく専用サブフォルダ
        （_tmp_<thread_id>）。
    """
    if _state._DEFAULT_WORKDIR is None:
        return json.dumps({"error": "init_tools() が未実行です"}, ensure_ascii=False)
    exec_tmp_dir = _resolve_exec_workdir()
    work_dir = cl.user_session.get("work_dir")
    if not work_dir:
        result = {
            "absolute_path": str(_state._DEFAULT_WORKDIR),
            "state": "read_write",
            "source": "default",
            "write_dir": str(exec_tmp_dir),
        }
        return json.dumps(result, ensure_ascii=False)
    status = probe_workdir_access(Path(work_dir))
    cl.user_session.set("work_dir_access", status)
    if not status.exists:
        state = "not_found"
    elif not status.readable:
        state = "unreadable"
    elif not status.writable:
        state = "read_only"
    else:
        state = "read_write"
    result = {"absolute_path": work_dir, "state": state}
    if status.error:
        result["detail"] = status.error
    if not status.exists or not status.readable:
        result["read_dir"] = str(_state._DEFAULT_WORKDIR)
        result["write_dir"] = str(exec_tmp_dir)
    elif not status.writable:
        result["write_dir"] = str(exec_tmp_dir)
        result["note"] = "読み取りは元の作業ディレクトリのまま"
    return json.dumps(result, ensure_ascii=False)
