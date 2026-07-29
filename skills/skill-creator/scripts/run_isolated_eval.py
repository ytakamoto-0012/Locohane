"""対象スキルの有無を切り替えたうえで evals.run_case を1件バックグラウンド実行する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
実際にローカルのllama.cpp serverへ問い合わせて ReAct ループ全体を回すため
数十秒〜数百秒かかりうる。run_script の同期実行タイムアウト（config.ini の
[scripts].timeout）内に収まる保証がないため、本スクリプトは起動して
即座に job_id を返す（start）／後から状態を確認する（status）の
2サブコマンドに分かれている。

使い方:
    python run_isolated_eval.py start --case <case.yaml> --skill-name <name> \\
        [--skill-root skills|locohane] [--mode with_skill|without_skill|old_skill] \\
        [--replacement-dir <path>] [--python-exe <path>]
    python run_isolated_eval.py status --job-id <job_id> --skill-name <name> \\
        [--skill-root skills|locohane]

--skill-root の既定値は locohane（`.locohane/skills/`）。skill-creator が
新規作成するスキルは常にそちらへ置く運用のため。プロジェクトルート直下の
`skills/`（word-counter 等の既存スキルの置き場）を評価対象にしたい場合の
み `--skill-root skills` を明示する。

with_skill:    本番の skills_dir / locohane_skills_dir をそのまま使う。
without_skill: 対象スキルの有無以外は本番と同じ状態にした一時ディレクトリを
               作り、対象スキルのフォルダだけ除外して評価する（baseline）。
old_skill:     --replacement-dir に指定した旧バージョンのスキル一式で
               対象スキルフォルダを差し替えて評価する（改善前後の比較用）。

いずれのモードでも他の既存スキル（word-counter等）は変更しないため、
「対象スキルの有無・新旧」だけを分離した公平な比較ができる。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from _common import (
    DEFAULT_MAIN_PYTHON,
    check_job,
    print_json,
    project_root,
    start_background,
    workspace_dir,
)

_ENV_VAR_BY_ROOT = {"skills": "SKILLS_DIR", "locohane": "LOCOHANE_SKILLS_DIR"}


def _skill_root_dir(root_key: str) -> Path:
    root = project_root()
    return root / "skills" if root_key == "skills" else root / ".locohane" / "skills"


def _build_isolated_env(skill_root: str, skill_name: str, mode: str, replacement_dir: str | None) -> dict[str, str]:
    """mode に応じて一時 skills_dir を用意し、環境変数にセットして返す。"""
    env = dict(os.environ)
    if mode == "with_skill":
        return env

    root_dir = _skill_root_dir(skill_root)
    tmp_root = Path(tempfile.mkdtemp(prefix="skill-creator-eval-"))
    if root_dir.is_dir():
        for child in root_dir.iterdir():
            dest = tmp_root / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)

    target_in_tmp = tmp_root / skill_name
    if mode == "without_skill":
        if target_in_tmp.exists():
            shutil.rmtree(target_in_tmp)
    elif mode == "old_skill":
        if not replacement_dir:
            raise ValueError("--mode old_skill には --replacement-dir が必須です")
        if target_in_tmp.exists():
            shutil.rmtree(target_in_tmp)
        shutil.copytree(Path(replacement_dir), target_in_tmp)
    else:
        raise ValueError(f"未知のmode: {mode}")

    env[_ENV_VAR_BY_ROOT[skill_root]] = str(tmp_root)
    return env


def _cmd_start(args: argparse.Namespace) -> int:
    case_path = Path(args.case)
    if not case_path.is_file():
        print(f"エラー: ケースファイルが見つかりません: {case_path}", file=sys.stderr)
        return 1

    skill_dir = _skill_root_dir(args.skill_root) / args.skill_name
    if args.mode == "without_skill" and not skill_dir.is_dir():
        print(f"エラー: 対象スキルディレクトリが見つかりません: {skill_dir}", file=sys.stderr)
        return 1

    try:
        env = _build_isolated_env(args.skill_root, args.skill_name, args.mode, args.replacement_dir)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    workspace = Path(args.workspace) if args.workspace else workspace_dir(skill_dir)
    cmd = [args.python_exe, "-m", "evals.run_case", str(case_path.resolve())]
    result = start_background(cmd, cwd=project_root(), env=env, workspace=workspace)
    result["mode"] = args.mode
    result["workspace"] = str(workspace)
    print_json(result)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    skill_dir = _skill_root_dir(args.skill_root) / args.skill_name
    workspace = Path(args.workspace) if args.workspace else workspace_dir(skill_dir)
    result = check_job(args.job_id, workspace)
    print_json(result)
    return 0 if "error" not in result else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--case", required=True, help="evals case yaml の絶対パス")
    p_start.add_argument("--skill-name", required=True)
    p_start.add_argument("--skill-root", choices=["skills", "locohane"], default="locohane")
    p_start.add_argument("--mode", choices=["with_skill", "without_skill", "old_skill"], default="with_skill")
    p_start.add_argument("--replacement-dir", default=None, help="mode=old_skill のときの旧バージョン一式のパス")
    p_start.add_argument("--workspace", default=None)
    p_start.add_argument("--python-exe", default=DEFAULT_MAIN_PYTHON)

    p_status = sub.add_parser("status")
    p_status.add_argument("--job-id", required=True)
    p_status.add_argument("--skill-name", required=True)
    p_status.add_argument("--skill-root", choices=["skills", "locohane"], default="locohane")
    p_status.add_argument("--workspace", default=None)

    args = parser.parse_args()
    if args.command == "start":
        return _cmd_start(args)
    return _cmd_status(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
