"""check_work_dir_status ツールと作業ディレクトリアクセス確認。"""

from __future__ import annotations

from dataclasses import dataclass
from langchain_core.tools import tool
from pathlib import Path
import chainlit as cl
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
        パス・状態（読み書き可能/読み取り専用/アクセス不可/存在しない）・
        フォールバック先をまとめた文字列。書き込みのフォールバック先は
        既定フォルダ直下ではなく専用サブフォルダ（_tmp_<thread_id>）。
    """
    if _state._DEFAULT_WORKDIR is None:
        return "エラー: init_tools() が未実行です"
    exec_tmp_dir = _resolve_exec_workdir()
    work_dir = cl.user_session.get("work_dir")
    if not work_dir:
        return (
            f"作業ディレクトリ: 未設定（既定フォルダ {_state._DEFAULT_WORKDIR} を使用）\n"
            "状態: 読み書き可能\n"
            f"書き込み先: {exec_tmp_dir}"
        )
    status = probe_workdir_access(Path(work_dir))
    cl.user_session.set("work_dir_access", status)
    if not status.exists:
        label = "存在しません（このPCから直接アクセスできません）"
    elif not status.readable:
        label = "アクセスできません（読み取り不可）"
    elif not status.writable:
        label = "読み取り専用（書き込み不可）"
    else:
        label = "読み書き可能"
    lines = [f"作業ディレクトリ: {work_dir}", f"状態: {label}"]
    if status.error:
        lines.append(f"詳細: {status.error}")
    if not status.exists or not status.readable:
        lines.append(f"読み取り先: {_state._DEFAULT_WORKDIR}")
        lines.append(f"書き込み先: {exec_tmp_dir}")
    elif not status.writable:
        lines.append(f"書き込み先: {exec_tmp_dir}（読み取りは元の作業ディレクトリのまま）")
    return "\n".join(lines)
