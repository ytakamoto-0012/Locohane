"""SKILL.md の description がどれだけ狙い通りにトリガーされるかを評価する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
trigger_eval.json（`[{"query": str, "should_trigger": bool}, ...]`）の各
クエリを複数回（既定3回、ローカルLLMの応答ブレを平均化するため）実行し、
`read_skill` が対象スキル名で実際に呼ばれたかどうかからトリガー率を集計する。

1クエリ1プロセスで evals.run_case を都度呼ぶのではなく、
`evals/cases/<target>/` にまとめてケースを生成したうえで
`python evals/run_all.py <target>` を1プロセスとしてバックグラウンド起動する
（run_all.py が直列実行してくれるため、ローカルllama-serverへの多重
リクエストを避けられる）。

使い方:
    python run_trigger_eval.py start --eval-set trigger_eval.json \\
        --skill-name my-new-skill --workspace <path> [--repeats 3] [--python-exe <path>]
    python run_trigger_eval.py status --job-id <job_id> --workspace <path>

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from _common import DEFAULT_MAIN_PYTHON, _is_process_alive, print_json, project_root, start_background


def _make_case_dict(case_id: str, target: str, query: str) -> dict:
    # expect / judge のどちらかが無いと case_schema.py の load_case() が
    # ValueError を送出する。ここではルールベース判定は使わず（判定は
    # status 側で transcript を直接解析する）、常に pass する無害な
    # ダミールールを1つ入れて「expect あり」の形だけ満たす。
    return {
        "id": case_id,
        "target": target,
        "turns": [query],
        "expect": {"tool_not_called": ["__skill_creator_trigger_probe_unused_tool__"]},
        "judge": None,
        "auto_approve": True,
        "scripted_text_answers": [],
        "work_dir": None,
        "timeout_seconds": None,
        "notes": "skill-creator run_trigger_eval.py が生成した一時トリガー評価ケース",
    }


def _cmd_start(args: argparse.Namespace) -> int:
    eval_set_path = Path(args.eval_set)
    if not eval_set_path.is_file():
        print(f"エラー: eval-set が見つかりません: {eval_set_path}", file=sys.stderr)
        return 1

    queries = json.loads(eval_set_path.read_text(encoding="utf-8"))
    if not isinstance(queries, list) or not queries:
        print("エラー: eval-set は1件以上のオブジェクトを持つJSON配列である必要があります", file=sys.stderr)
        return 1

    root = project_root()
    target = f"trigger_{args.skill_name}_{uuid.uuid4().hex[:8]}"
    cases_dir = root / "evals" / "cases" / target
    cases_dir.mkdir(parents=True, exist_ok=True)

    meta_queries = []
    for qi, item in enumerate(queries):
        query = item["query"]
        should_trigger = bool(item["should_trigger"])
        meta_queries.append({"query_index": qi, "query": query, "should_trigger": should_trigger})
        for ri in range(args.repeats):
            case_id = f"q{qi:03d}_r{ri:02d}"
            case_dict = _make_case_dict(case_id, target, query)
            (cases_dir / f"{case_id}.yaml").write_text(
                json.dumps(case_dict, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    (cases_dir / "_trigger_meta.json").write_text(
        json.dumps(
            {"skill_name": args.skill_name, "repeats": args.repeats, "queries": meta_queries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "jobs").mkdir(exist_ok=True)

    cmd = [args.python_exe, str(root / "evals" / "run_all.py"), target]
    result = start_background(cmd, cwd=root, env=__import__("os").environ.copy(), workspace=workspace)

    meta_path = workspace / "jobs" / result["job_id"] / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["target"] = target
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    result["target"] = target
    result["case_count"] = len(meta_queries) * args.repeats
    print_json(result)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = project_root()
    workspace = Path(args.workspace)
    job_dir = workspace / "jobs" / args.job_id
    meta_path = job_dir / "meta.json"
    if not meta_path.is_file():
        print_json({"error": f"ジョブが見つかりません: {args.job_id}"})
        return 1

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if _is_process_alive(meta["pid"]):
        print_json({"job_id": args.job_id, "status": "running", "pid": meta["pid"]})
        return 0

    target = meta["target"]
    results_root = root / "evals" / "results" / target
    if not results_root.is_dir():
        print_json({"job_id": args.job_id, "status": "finished", "error": "結果ディレクトリが見つかりません", "target": target})
        return 1

    timestamp_dirs = sorted(p for p in results_root.iterdir() if p.is_dir())
    if not timestamp_dirs:
        print_json({"job_id": args.job_id, "status": "finished", "error": "結果が生成されていません", "target": target})
        return 1

    results_path = timestamp_dirs[-1] / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))

    trigger_meta_path = root / "evals" / "cases" / target / "_trigger_meta.json"
    trigger_meta = json.loads(trigger_meta_path.read_text(encoding="utf-8"))
    skill_name = trigger_meta["skill_name"]
    repeats = trigger_meta["repeats"]

    triggered_by_case: dict[str, bool] = {}
    for r in results:
        case_id = r.get("case_id")
        transcript = r.get("transcript") or []
        triggered = False
        for entry in transcript:
            for tc in entry.get("tool_calls") or []:
                if tc.get("name") == "read_skill" and (tc.get("args") or {}).get("skill_name") == skill_name:
                    triggered = True
        triggered_by_case[case_id] = triggered

    per_query = []
    correct = 0
    for q in trigger_meta["queries"]:
        qi = q["query_index"]
        trig_count = sum(1 for ri in range(repeats) if triggered_by_case.get(f"q{qi:03d}_r{ri:02d}"))
        trigger_rate = trig_count / repeats if repeats else 0.0
        matched = (trigger_rate >= 0.5) == q["should_trigger"]
        correct += 1 if matched else 0
        per_query.append(
            {
                "query": q["query"],
                "should_trigger": q["should_trigger"],
                "trigger_rate": trigger_rate,
                "matched": matched,
            }
        )

    total = len(trigger_meta["queries"])
    print_json(
        {
            "job_id": args.job_id,
            "status": "finished",
            "target": target,
            "accuracy": (correct / total) if total else None,
            "per_query": per_query,
            "results_path": str(results_path),
        }
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--eval-set", required=True, help='[{"query":str,"should_trigger":bool}, ...] のJSONファイル')
    p_start.add_argument("--skill-name", required=True)
    p_start.add_argument("--repeats", type=int, default=3)
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
