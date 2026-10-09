"""ドラフトスキルの eval ケースを、実際のローカルLLMで（繰り返し）実行する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。

    # ドラフトありで全ケースを1回ずつ
    python run_isolated_eval.py start --name my-skill
    # スキル安定化トライアウト（各ケースを10回ずつ、全回合格で合格）
    python run_isolated_eval.py start --name my-skill --repeat 10
    # 比較用: ドラフト無し（新規スキルならスキル無し、改善案なら正式スキルのまま）
    python run_isolated_eval.py start --name my-skill --mode without_skill --case 001_basic
    # 結果の確認（running の間は1分ほど待ってから再度呼ぶ）
    python run_isolated_eval.py status --name my-skill --job-id <job_id>

評価は evals/run_all.py を `--cases-dir <ドラフト>/evals` でバックグラウンド実行する。
with_skill ではドラフトを `--skill-overlay` で本番のスキル構成（会社専用の
project_locohane_dir を含む）の上に重ねるため、評価中のスキル名はスキル名そのもの
（例: my-skill）になる。改善案ドラフトは同名の正式スキルを上書きした状態で評価される。

結果（status）は _draft_meta.json の tryouts にも記録される。昇格（promote-skill）では
スキル開発者が同じ評価を実行し直すため、ここでの記録は参考扱い。

ローカルの llama.cpp server は1つなので、評価は1件ずつ（前のジョブが finished に
なってから次を start する）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import (
    DEFAULT_MAIN_PYTHON,
    DRAFT_EVALS_DIRNAME,
    SkillCreatorError,
    draft_context,
    is_process_alive,
    load_job,
    log_tail,
    now_iso,
    print_json,
    project_root,
    read_meta,
    resolve_draft,
    run_main,
    start_background,
    workspace_dir,
    write_meta,
)

# status で返す最終回答の最大文字数（judge 判定の材料。全文は results_path を読む）。
_FINAL_ANSWER_PREVIEW_CHARS = 600


def _classify(result: dict) -> str:
    """evals/run_all.py の _classify() と同じ分類。"""
    if result.get("error"):
        return "error"
    if result.get("rules_pass") is False:
        return "fail"
    if result.get("judge"):
        return "judge"
    return "pass"


def _cmd_start(args: argparse.Namespace) -> int:
    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write")
    cases_dir = ref.dir / DRAFT_EVALS_DIRNAME
    available = sorted(p.stem for p in cases_dir.glob("*.yaml"))
    if not available:
        raise SkillCreatorError(f"ケースがありません。make_eval_case.py で {ref.full_name} のケースを作ってください。")
    missing = [c for c in args.case if c not in available]
    if missing:
        raise SkillCreatorError(f"ケースが見つかりません: {missing}（ある: {available}）")
    if args.repeat < 1:
        raise SkillCreatorError("--repeat は1以上にしてください。")

    ws = workspace_dir(ctx, ref)
    results_root = ws / "results" / args.mode
    existing = sorted(p.name for p in results_root.iterdir()) if results_root.is_dir() else []
    cmd = [
        args.python_exe,
        str(project_root() / "evals" / "run_all.py"),
        *args.case,
        "--cases-dir",
        str(cases_dir),
        "--results-dir",
        str(results_root),
        "--repeat",
        str(args.repeat),
    ]
    if args.mode == "with_skill":
        cmd += ["--skill-overlay", str(ref.dir)]
    if args.instance:
        cmd += ["--instance", args.instance]
    job = start_background(
        cmd,
        ws,
        {
            "draft": ref.full_name,
            "mode": args.mode,
            "repeat": args.repeat,
            "cases": args.case or available,
            "results_root": str(results_root),
            "existing_results": existing,
            "started_at": now_iso(),
        },
    )
    job["runs"] = len(args.case or available) * args.repeat
    print_json(job)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write")
    ws = workspace_dir(ctx, ref)
    job = load_job(ws, args.job_id)
    if is_process_alive(job["pid"]):
        done = log_tail(job, 10_000).count("実行中:")
        print_json({"job_id": args.job_id, "status": "running", "started_runs": done, "total_runs": len(job["cases"]) * job["repeat"]})
        return 0

    results_root = Path(job["results_root"])
    new_dirs = sorted(
        p for p in (results_root.iterdir() if results_root.is_dir() else []) if p.is_dir() and p.name not in job["existing_results"]
    )
    if not new_dirs or not (new_dirs[-1] / "results.json").is_file():
        print_json({"job_id": args.job_id, "status": "finished", "error": "結果が生成されていません", "log_tail": log_tail(job)})
        return 1
    out_dir = new_dirs[-1]
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))

    cases: dict[str, dict[str, int]] = {}
    runs = []
    for r in results:
        outcome = _classify(r)
        cases.setdefault(r.get("case_id", "?"), {"pass": 0, "fail": 0, "judge": 0, "error": 0})[outcome] += 1
        runs.append(
            {
                "case_id": r.get("case_id"),
                "repeat_index": r.get("repeat_index", 1),
                "outcome": outcome,
                "failed_rules": [k for k, v in (r.get("rule_results") or {}).items() if not v.get("pass")],
                "judge": r.get("judge"),
                "error": r.get("error"),
                "final_answer": (r.get("final_answer") or "")[:_FINAL_ANSWER_PREVIEW_CHARS],
            }
        )
    if any(c["fail"] or c["error"] for c in cases.values()):
        verdict = "fail"
    elif any(c["judge"] for c in cases.values()):
        verdict = "needs_judge"
    else:
        verdict = "pass"

    meta = read_meta(ref)
    tryouts = meta.setdefault("tryouts", [])
    if not any(t.get("job_id") == args.job_id for t in tryouts):
        tryouts.append(
            {
                "job_id": args.job_id,
                "at": now_iso(),
                "by": ctx.user,
                "mode": job["mode"],
                "repeat": job["repeat"],
                "verdict": verdict,
                "cases": cases,
                "results_dir": str(out_dir),
            }
        )
        write_meta(ref, meta)
    print_json(
        {
            "job_id": args.job_id,
            "status": "finished",
            "mode": job["mode"],
            "repeat": job["repeat"],
            "verdict": verdict,
            "cases": cases,
            "runs": runs,
            "results_path": str(out_dir / "results.json"),
        }
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--name", required=True, help="ドラフト名（自分のドラフトはスキル名だけでよい）")
    p_start.add_argument("--mode", choices=["with_skill", "without_skill"], default="with_skill")
    p_start.add_argument("--repeat", type=int, default=1, help="各ケースの繰り返し回数（スキル安定化トライアウト）")
    p_start.add_argument("--case", action="append", default=[], help="実行するケースID（複数可。省略時は全ケース）")
    p_start.add_argument("--instance", default=None, help="評価に使うインスタンス（省略時は今のインスタンス）")
    p_start.add_argument("--python-exe", default=DEFAULT_MAIN_PYTHON)

    p_status = sub.add_parser("status")
    p_status.add_argument("--name", required=True)
    p_status.add_argument("--job-id", required=True)

    args = parser.parse_args()
    return _cmd_start(args) if args.command == "start" else _cmd_status(args)


if __name__ == "__main__":
    run_main(main)
