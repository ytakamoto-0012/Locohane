"""evals/case_schema.py 互換のテストケース(yaml)を1件生成する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
生成したケースは `evals/cases/<target>/<case-id>.yaml` に配置され、
`run_isolated_eval.py`（本スキル）からも、既存の `python evals/run_all.py
<target>` からもそのまま実行できる（case_schema.py の EvalCase は
target/turns/expect/judge 等の必須項目さえ満たしていれば読める）。

YAML の手書き生成は特殊文字のエスケープで壊れやすいため、PyYAML には
依存せず「JSON は YAML のサブセットである」という性質を使い、
case 内容を JSON としてシリアライズしてそのまま .yaml として書き出す
（case_schema.py 側は yaml.safe_load() で読むため、有効な JSON は
そのまま正しく読める）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import print_json, project_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="evals/cases/<target>/ のディレクトリ名")
    parser.add_argument("--case-id", required=True, help="ケースID（ファイル名にも使う）")
    parser.add_argument("--turns", required=True, help='ユーザー発話のJSON配列。例: [\"...\"]')
    parser.add_argument("--expect", default=None, help="Expect の一部をJSONオブジェクトで指定")
    parser.add_argument("--judge", default=None, help="自由記述の判定指示（自分でtranscriptを読んで判定する）")
    parser.add_argument("--auto-approve", choices=["true", "false"], default="true")
    parser.add_argument("--scripted-text-answers", default=None, help="JSON配列")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=None)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    try:
        turns = json.loads(args.turns)
    except json.JSONDecodeError as e:
        print(f"エラー: --turns がJSONとして解釈できません: {e}", file=sys.stderr)
        return 1
    if not isinstance(turns, list) or not turns:
        print("エラー: --turns は1件以上の文字列を持つJSON配列である必要があります", file=sys.stderr)
        return 1

    expect = None
    if args.expect:
        try:
            expect = json.loads(args.expect)
        except json.JSONDecodeError as e:
            print(f"エラー: --expect がJSONとして解釈できません: {e}", file=sys.stderr)
            return 1

    if expect is None and not args.judge:
        print("エラー: --expect と --judge のどちらか（両方でも可）を指定してください", file=sys.stderr)
        return 1

    scripted = []
    if args.scripted_text_answers:
        try:
            scripted = json.loads(args.scripted_text_answers)
        except json.JSONDecodeError as e:
            print(f"エラー: --scripted-text-answers がJSONとして解釈できません: {e}", file=sys.stderr)
            return 1

    case_dict = {
        "id": args.case_id,
        "target": args.target,
        "turns": turns,
        "expect": expect,
        "judge": args.judge,
        "auto_approve": args.auto_approve == "true",
        "scripted_text_answers": scripted,
        "work_dir": args.work_dir,
        "timeout_seconds": args.timeout_seconds,
        "notes": args.notes,
    }

    cases_dir = project_root() / "evals" / "cases" / args.target
    cases_dir.mkdir(parents=True, exist_ok=True)
    case_path = cases_dir / f"{args.case_id}.yaml"
    case_path.write_text(json.dumps(case_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    print_json({"case_path": str(case_path), "target": args.target, "case_id": args.case_id})
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
