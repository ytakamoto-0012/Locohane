"""skill-creator の各スクリプトが共有するヘルパー。

Locohane プロジェクトルート（evals/, src/ を含む）を直接呼び出すための
経路解決と、run_script の同期実行タイムアウト（config.ini の
[scripts].timeout、既定値は環境依存で数百秒程度）を超えうる処理（実際に
llama.cpp サーバーへ問い合わせる eval 実行）をバックグラウンド起動して
後からポーリングするための最小限のジョブ管理を提供する。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

# Locohane本体（evals/, src/ 以下。langchain/langgraph/chainlit 等に依存）を
# 動かすための Python 実行環境。config.ini の [scripts].python は
# run_script 用の別環境（このファイル自身を実行している環境）であり、
# evals.run_case 等 Locohane 本体のモジュールを import できるとは限らない
# ため区別する。CLAUDE.md の「Python実行環境」に記載の値をデフォルトにし、
# 各スクリプトは --python-exe で上書きできるようにする。
DEFAULT_MAIN_PYTHON = r"C:\DT_Python\Python311\env_local_agent_system\Scripts\python.exe"


def project_root() -> Path:
    """Locohane プロジェクトルートの絶対パスを返す。

    このファイルは skills/skill-creator/scripts/_common.py に置かれる
    前提で、parents[3] がプロジェクトルート（config.ini や evals/ がある
    ディレクトリ）になる。
    """
    return Path(__file__).resolve().parents[3]


def workspace_dir(skill_dir: Path) -> Path:
    """スキルディレクトリの兄弟に `<skill-name>-workspace/` を用意して返す。

    Args:
        skill_dir: 評価対象スキルのディレクトリ（例: skills/my-new-skill）。

    Returns:
        `<skill_dir>-workspace/`（無ければ作成、jobs/ サブディレクトリも用意）。
    """
    ws = skill_dir.parent / f"{skill_dir.name}-workspace"
    (ws / "jobs").mkdir(parents=True, exist_ok=True)
    return ws


def start_background(cmd: list[str], cwd: Path, env: dict[str, str], workspace: Path) -> dict:
    """cmd をバックグラウンドで起動し、ポーリング用のジョブ情報を返す。

    呼び出し元プロセス（run_script 経由で起動された本スクリプト自体）が
    終了しても子プロセスが生き続けるよう、Windows では新しいプロセス
    グループとして起動する。

    Args:
        cmd: 実行するコマンド（例: [sys.executable, "-m", "evals.run_case", "..."]）。
        cwd: 子プロセスの作業ディレクトリ（通常は project_root()）。
        env: 子プロセスへ渡す環境変数。
        workspace: workspace_dir() が返したディレクトリ。

    Returns:
        `{"job_id", "pid", "log_path", "status": "started"}`。
    """
    job_id = uuid.uuid4().hex[:12]
    job_dir = workspace / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "output.log"
    meta_path = job_dir / "meta.json"

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    meta = {"job_id": job_id, "pid": proc.pid, "cmd": cmd, "log_path": str(log_path)}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"job_id": job_id, "pid": proc.pid, "log_path": str(log_path), "status": "started"}


def _is_process_alive(pid: int) -> bool:
    """Windows の tasklist で PID の生存を確認する（追加依存ライブラリ不要）。"""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
    )
    return str(pid) in result.stdout


def check_job(job_id: str, workspace: Path) -> dict:
    """ジョブの状態を確認する。実行中なら running、完了していれば結果を返す。

    完了判定は「対象 PID が tasklist に存在しない」ことで行う。完了後は
    ログファイル（子プロセスの標準出力/標準エラーをそのまま連結したもの）の
    最終行を JSON としてパースし、それを結果として返す（評価系スクリプトは
    いずれも最後に1行の JSON を print する契約で統一している）。

    Args:
        job_id: start_background() が返した job_id。
        workspace: workspace_dir() が返したディレクトリ。

    Returns:
        `{"job_id", "status": "running", "pid"}` または
        `{"job_id", "status": "finished", "result": {...}}` または
        `{"job_id", "status": "finished", "error", "log_tail"}`（最終行が
        JSON として解釈できない場合）または `{"error": "..."}`（job_id 不明）。
    """
    job_dir = workspace / "jobs" / job_id
    meta_path = job_dir / "meta.json"
    if not meta_path.is_file():
        return {"error": f"ジョブが見つかりません: {job_id}"}

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    log_path = Path(meta["log_path"])
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""

    if _is_process_alive(meta["pid"]):
        return {"job_id": job_id, "status": "running", "pid": meta["pid"]}

    lines = [ln for ln in log_text.splitlines() if ln.strip()]
    if not lines:
        return {
            "job_id": job_id,
            "status": "finished",
            "error": "プロセスは終了しましたが出力がありません",
            "log_tail": log_text[-2000:],
        }
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "job_id": job_id,
            "status": "finished",
            "error": "最終行がJSONとして解釈できません（スクリプトが異常終了した可能性）",
            "log_tail": "\n".join(lines[-40:]),
        }
    return {"job_id": job_id, "status": "finished", "result": result}


def print_json(obj: dict) -> None:
    """契約どおり1行のJSONを標準出力へ書く。"""
    print(json.dumps(obj, ensure_ascii=False))


def load_locohane_config():
    """プロジェクトルートの src.config.load_config() を呼んで Config を返す。

    scripts/ 配下は通常 skills フォルダの中で完結するが、skill-creator は
    Locohane 自体の設定・評価基盤を扱うメタスキルのため、プロジェクトの
    src パッケージを直接 import する。
    """
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.config import load_config  # noqa: PLC0415 (遅延import: sys.path調整後に読む必要がある)

    return load_config()
