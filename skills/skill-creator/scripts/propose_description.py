"""現在の description とトリガー評価の失敗例から改善案をLLMに提案させる。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
run_trigger_eval.py の集計結果（per_query の matched: false の項目）を
そのまま渡すことを想定している。単発の chat completion 呼び出しだが、
ローカルLLMの応答生成に時間がかかる場合に備え、他の評価系スクリプトと
同様 start/status の非同期パターンに統一している。

実際のLLM呼び出しは `_llm_helper.py`（Locohane本体のPython実行環境が
必要）をサブプロセスとして起動して行う。

使い方:
    python propose_description.py start --skill-name my-new-skill \\
        --current-description "..." --failed-queries failed.json \\
        --workspace <path> [--python-exe <path>]
    python propose_description.py status --job-id <job_id> --workspace <path>

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from _common import DEFAULT_MAIN_PYTHON, check_job, print_json, project_root, start_background

_SYSTEM_PROMPT = (
    "あなたはローカルLLM向けAgent SkillsシステムのSKILL.mdのdescriptionを改善する"
    "アシスタントです。descriptionはLLMがこのスキルを使うべきか判断する唯一の手がかりで、"
    "「何をするか」と「どんなユーザー発話のときに使うべきか」の両方を含める必要があります。"
    "1〜1024文字、日本語で、既存のトーンを保ちつつ、失敗例を踏まえて具体的なユーザー発話の"
    "パターンや文脈を補うよう改善してください。改善後のdescription本文のみを出力し、"
    "前置きや説明、コードブロックの囲みは付けないでください。"
)


def _build_user_prompt(skill_name: str, current_description: str, failed_queries: list[dict]) -> str:
    lines = [
        f"スキル名: {skill_name}",
        f"現在のdescription:\n{current_description}",
        "",
        "以下はトリガー精度評価で期待と異なる結果になったクエリです"
        "（should_trigger=trueなのに実際は使われなかった、または"
        "should_trigger=falseなのに実際は使われてしまった、のどちらか）:",
    ]
    for item in failed_queries:
        expectation = "使われるべき" if item.get("should_trigger") else "使われるべきでない"
        actual_rate = item.get("trigger_rate")
        lines.append(f"- 発話例: {item.get('query')!r} / 期待: {expectation} / 実際のトリガー率: {actual_rate}")
    lines.append("")
    lines.append("これらの失敗例を踏まえ、改善したdescriptionを1つ提案してください。")
    return "\n".join(lines)


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        failed_queries = json.loads(Path(args.failed_queries).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"エラー: --failed-queries を読み込めません: {e}", file=sys.stderr)
        return 1

    user_prompt = _build_user_prompt(args.skill_name, args.current_description, failed_queries)

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "jobs").mkdir(exist_ok=True)

    fd, input_path_str = tempfile.mkstemp(suffix=".json", dir=str(workspace))
    input_path = Path(input_path_str)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump({"system": _SYSTEM_PROMPT, "user": user_prompt}, f, ensure_ascii=False)

    root = project_root()
    helper_path = Path(__file__).resolve().parent / "_llm_helper.py"
    cmd = [args.python_exe, str(helper_path), str(input_path)]
    result = start_background(cmd, cwd=root, env=__import__("os").environ.copy(), workspace=workspace)
    print_json(result)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    result = check_job(args.job_id, Path(args.workspace))
    print_json(result)
    return 0 if "error" not in result else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--skill-name", required=True)
    p_start.add_argument("--current-description", required=True)
    p_start.add_argument("--failed-queries", required=True, help="run_trigger_eval.py status の per_query から抽出したJSON配列")
    p_start.add_argument("--workspace", required=True)
    p_start.add_argument("--python-exe", default=DEFAULT_MAIN_PYTHON)

    p_status = sub.add_parser("status")
    p_status.add_argument("--job-id", required=True)
    p_status.add_argument("--workspace", required=True)

    args = parser.parse_args()
    if args.command == "start":
        return _cmd_start(args)
    return _cmd_status(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
