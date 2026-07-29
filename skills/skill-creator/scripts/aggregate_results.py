"""複数の evals.run_case 結果を集計して比較用の Markdown レポートを作る。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
`run_isolated_eval.py status` の出力（`{"status": "finished", "result": {...}}`）
をそのまま、または evals.run_case の生JSON（`evals/results/*/results.json`の
1要素など）を直接、`--input <label>=<path>` で複数渡す。

judge が指定されたケースは本スクリプトでは合否を決めない（run_case.py の
設計上、judge の合否は transcript を読んだ人間役＝呼び出し元のLLM自身が
判定する）。ここでは pass_rate・トークン使用量・所要時間の比較と、
judge 判定待ちのケース一覧を出す。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import print_json


def _load_result(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("status") == "finished" and "result" in data:
        return data["result"]
    return data


def _summarize(label: str, result: dict) -> dict:
    rules_pass = result.get("rules_pass")
    judge = result.get("judge")
    token_total = (result.get("token_usage_total") or {}).get("total_tokens")
    turn_timings = result.get("turn_timings") or []
    duration_seconds = (
        sum((t.get("max_llm_total_seconds") or 0) for t in turn_timings if isinstance(t, dict)) or None
    )
    return {
        "label": label,
        "case_id": result.get("case_id"),
        "rules_pass": rules_pass,
        "needs_judge": bool(judge),
        "judge_instruction": judge,
        "error": result.get("error"),
        "total_tokens": token_total,
        "duration_seconds": duration_seconds,
        "turn_cutoffs": result.get("turn_cutoffs"),
    }


def _render_markdown(summaries: list[dict]) -> str:
    lines = ["# skill-creator 評価結果比較", ""]
    lines.append("| 実行 | ケースID | ルール判定 | judge判定要否 | トークン合計 | 所要秒数 | エラー |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in summaries:
        rules = "PASS" if s["rules_pass"] is True else ("FAIL" if s["rules_pass"] is False else "-")
        judge = "要判定" if s["needs_judge"] else "-"
        tokens = s["total_tokens"] if s["total_tokens"] is not None else "-"
        duration = f"{s['duration_seconds']:.1f}" if s["duration_seconds"] is not None else "-"
        error = s["error"] or "-"
        lines.append(f"| {s['label']} | {s['case_id']} | {rules} | {judge} | {tokens} | {duration} | {error} |")

    judge_cases = [s for s in summaries if s["needs_judge"]]
    if judge_cases:
        lines.append("")
        lines.append("## judge判定が必要なケース")
        lines.append("")
        lines.append("以下は自分（transcriptを読んでいるLLM）が判定すること。ルールベースでは自動判定できない。")
        for s in judge_cases:
            lines.append("")
            lines.append(f"### {s['label']} / {s['case_id']}")
            lines.append(f"- 判定指示: {s['judge_instruction']}")

    cutoff_cases = [s for s in summaries if s["turn_cutoffs"]]
    if cutoff_cases:
        lines.append("")
        lines.append("## turn_cutoff が発生したケース（要注意）")
        for s in cutoff_cases:
            lines.append(f"- {s['label']} / {s['case_id']}: {s['turn_cutoffs']}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="label=path 形式で複数指定（例: --input with_skill=a.json --input baseline=b.json）",
    )
    parser.add_argument("--output", required=True, help="benchmark.md の出力先パス")
    args = parser.parse_args()

    summaries = []
    for item in args.input:
        if "=" not in item:
            print(f"エラー: --input は label=path 形式である必要があります: {item}", file=sys.stderr)
            return 1
        label, path_str = item.split("=", 1)
        path = Path(path_str)
        if not path.is_file():
            print(f"エラー: ファイルが見つかりません: {path}", file=sys.stderr)
            return 1
        result = _load_result(path)
        summaries.append(_summarize(label, result))

    markdown = _render_markdown(summaries)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    print_json({"output_path": str(output_path), "cases": len(summaries)})
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
