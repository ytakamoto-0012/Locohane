"""ドラフトスキルの現在の description とトリガー評価の失敗例から、改善案をLLMに提案させる。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。

    python propose_description.py start --name my-skill --failed-queries <failed.json>
    python propose_description.py status --name my-skill --job-id <job_id>

現在の description はドラフトの SKILL.md から読む。--failed-queries には
run_trigger_eval.py status の per_query から matched: false の項目を抜き出した
JSON配列のファイルを渡す。単発の chat completion 呼び出しだが、ローカルLLMの
応答生成に時間がかかる場合に備え、他の評価系スクリプトと同様 start/status の
非同期パターンにしている。提案は自動では反映しない（write_draft_file.py で書く）。

実際のLLM呼び出しは `_llm_helper.py`（Locohane本体のPython実行環境が
必要）をサブプロセスとして起動して行う。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from _common import (
    DEFAULT_MAIN_PYTHON,
    SkillCreatorError,
    draft_context,
    is_process_alive,
    load_job,
    log_tail,
    parse_frontmatter,
    print_json,
    resolve_draft,
    run_main,
    start_background,
    workspace_dir,
)

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
        lines.append(f"- 発話例: {item.get('query')!r} / 期待: {expectation} / 実際のトリガー率: {item.get('trigger_rate')}")
    lines.append("")
    lines.append("これらの失敗例を踏まえ、改善したdescriptionを1つ提案してください。")
    return "\n".join(lines)


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        failed_queries = json.loads(Path(args.failed_queries).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SkillCreatorError(f"--failed-queries を読み込めません: {e}") from e
    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write")
    fm = parse_frontmatter((ref.dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")) or {}
    user_prompt = _build_user_prompt(ref.name, fm.get("description", ""), failed_queries)

    ws = workspace_dir(ctx, ref)
    fd, input_path = tempfile.mkstemp(suffix=".json", dir=str(ws))
    with open(fd, "w", encoding="utf-8") as f:
        json.dump({"system": _SYSTEM_PROMPT, "user": user_prompt}, f, ensure_ascii=False)
    helper_path = Path(__file__).resolve().parent / "_llm_helper.py"
    print_json(start_background([args.python_exe, str(helper_path), input_path], ws))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write")
    job = load_job(workspace_dir(ctx, ref), args.job_id)
    if is_process_alive(job["pid"]):
        print_json({"job_id": args.job_id, "status": "running", "pid": job["pid"]})
        return 0
    lines = [ln for ln in log_tail(job).splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        result = {}
    if "text" not in result:
        print_json(
            {
                "job_id": args.job_id,
                "status": "finished",
                "error": result.get("error", "提案を取得できませんでした"),
                "log_tail": "\n".join(lines[-20:]),
            }
        )
        return 1
    print_json({"job_id": args.job_id, "status": "finished", "proposed_description": result["text"].strip()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--name", required=True, help="ドラフト名（自分のドラフトはスキル名だけでよい）")
    p_start.add_argument("--failed-queries", required=True, help="per_query から matched:false を抜き出したJSON配列のファイル")
    p_start.add_argument("--python-exe", default=DEFAULT_MAIN_PYTHON)

    p_status = sub.add_parser("status")
    p_status.add_argument("--name", required=True)
    p_status.add_argument("--job-id", required=True)

    args = parser.parse_args()
    return _cmd_start(args) if args.command == "start" else _cmd_status(args)


if __name__ == "__main__":
    run_main(main)
