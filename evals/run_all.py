"""指定カテゴリの eval ケースを全件、または指定ケースのみ実行し、結果を集計する。

使い方:
    python evals/run_all.py system_prompt
    python evals/run_all.py system_prompt 001_annual_schedule_investigation_before_plan 005_glob_single_call_then_delegate
    python evals/run_all.py system_prompt --instance <インスタンス名>

第3引数以降にケースID（ファイル名から拡張子`.yaml`を除いたもの）を
指定すると、そのケースのみを実行する（tune-promptスキルの対話選択で使用）。
省略時は従来通り対象ディレクトリ配下の全ケースを実行する。

--instance で設定ダッシュボードのインスタンス（instances/<name>/）を指定すると、
全ケースをそのインスタンスの設定で実行する（省略時は環境変数
LOCOHANE_INSTANCE、それも無ければ default。evals/instance.py 参照）。

evals/cases/<target>/*.yaml を昇順に glob し、ケースごとに
`<このプロセスと同じ python> -m evals.run_case <file>` をサブプロセスとして
直列実行する（ローカル1台の llama.cpp server に同時多重リクエストを
かけないための配慮）。結果は evals/results/<target>/<timestamp>/ 配下に
results.json（全件の生データ）と summary.md（pass/fail 一覧 + judge待ち
ケースの transcript 抜粋）として保存し、同じ内容を標準出力にも表示する。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# `python evals/run_all.py` はスクリプト直接実行のため sys.path[0] が evals/ に
# なり、`import evals.xxx` が解決できない（run_case.py 同様の対処）。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.case_schema import load_case  # noqa: E402
from evals.instance import resolve_instance_name  # noqa: E402
# 無言終了時の自動リトライ（src/graph.py の ainvoke_ensuring_final_text、
# 既定 max_retries=2）や大量画像を扱うケースはグラフの ainvoke が複数回・
# 長時間かかることがあるため、600秒では単体実行なら成功するケースまで
# タイムアウト扱いになることがある（tune-prompt iter13で確認）。実運用の
# app.py にはこの制限は存在しないため、テストハーネス側の都合として
# 900秒に緩和する。
CASE_TIMEOUT_SECONDS = 900

# Windows のコンソールコードページ（既定 cp932）でサマリの日本語が
# 文字化けするのを防ぐ。
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _iter_case_paths(target: str, case_ids: list[str] | None = None) -> list[Path]:
    """evals/cases/<target>/*.yaml を昇順に列挙する。

    Args:
        target: チューニング対象カテゴリ名（例: "system_prompt"）。
        case_ids: 指定された場合、これらのケースID（拡張子抜きファイル名）
            のみに絞り込む。None または空リストなら全件。

    Returns:
        yaml ファイルパスの昇順リスト。

    Raises:
        SystemExit: 対象ディレクトリが存在しない場合、または指定した
            case_ids に対応する yaml が見つからない場合。
    """
    cases_dir = PROJECT_ROOT / "evals" / "cases" / target
    if not cases_dir.is_dir():
        raise SystemExit(f"ケースディレクトリが見つかりません: {cases_dir}")
    all_paths = sorted(cases_dir.glob("*.yaml"))
    if not case_ids:
        return all_paths

    by_stem = {p.stem: p for p in all_paths}
    missing = [cid for cid in case_ids if cid not in by_stem]
    if missing:
        raise SystemExit(
            f"指定されたケースが見つかりません: {missing}（対象: {cases_dir}）"
        )
    return [by_stem[cid] for cid in case_ids]


def _run_one(case_path: Path, instance_name: str) -> dict:
    """1ケースを run_case.py のサブプロセスとして実行し、結果 dict を返す。

    Args:
        case_path: 実行する eval ケースの yaml パス。
        instance_name: 実行対象インスタンス名（run_case.py の --instance へ渡す）。

    Returns:
        run_case.py が出力した結果 JSON をパースした dict。標準出力が空、
        または JSON として不正な場合はエラーを表す dict を返す（例外は伝播させない）。

    Raises:
        subprocess.TimeoutExpired: タイムアウト秒数以内に終わらなかった場合
            （呼び出し側で捕捉する）。
    """
    # ケースが timeout_seconds を指定していればそちらを優先する（大量ファイルを
    # 扱う重量級ケース等、既定値では完走できないケース専用の上書き）。
    case = load_case(case_path)
    timeout = case.timeout_seconds or CASE_TIMEOUT_SECONDS
    proc = subprocess.run(
        [sys.executable, "-m", "evals.run_case", str(case_path), "--instance", instance_name],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    stdout = proc.stdout.strip()
    if not stdout:
        return {
            "case_id": case_path.stem,
            "instance": instance_name,
            "error": "no_output",
            "detail": f"標準出力が空でした（終了コード {proc.returncode}）。",
        }
    try:
        return json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as e:
        return {
            "case_id": case_path.stem,
            "instance": instance_name,
            "error": "invalid_json",
            "detail": f"結果のJSON解析に失敗しました: {e}",
        }


def _render_summary(target: str, instance_name: str, results: list[dict]) -> str:
    """結果一覧から人間可読な Markdown サマリを組み立てる。

    Args:
        target: チューニング対象カテゴリ名。
        instance_name: 実行対象インスタンス名。
        results: _run_one() の戻り値のリスト。

    Returns:
        pass/fail/judge待ち/error の集計表 + judge待ちケースの詳細を含む Markdown。
    """
    lines = [f"# eval 結果: {target}", "", f"インスタンス: {instance_name}", ""]
    n_pass = n_fail = n_judge = n_error = 0
    token_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    n_token_measured = 0
    for r in results:
        cid = r.get("case_id", "?")
        if r.get("error"):
            n_error += 1
            lines.append(f"- ⚠ {cid}: ERROR ({r['error']}) {r.get('detail', '')}")
            continue

        usage = r.get("token_usage_total") or {}
        usage_suffix = ""
        if usage.get("total_tokens"):
            n_token_measured += 1
            for key in token_total:
                token_total[key] += usage.get(key, 0) or 0
            usage_suffix = (
                f" [tokens in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)} total={usage.get('total_tokens', 0)}]"
            )

        cutoffs = r.get("turn_cutoffs") or []
        cutoff_suffix = ""
        if cutoffs:
            detail = ", ".join(f"{c['reason']}(turn {c['turn_index']})" for c in cutoffs)
            cutoff_suffix = f" [⚠ turn cutoff: {detail}]"

        rules_pass = r.get("rules_pass")
        judge = r.get("judge")
        if rules_pass is False:
            n_fail += 1
            failed = [k for k, v in r.get("rule_results", {}).items() if not v["pass"]]
            lines.append(f"- ✗ {cid}: ルールFAIL ({', '.join(failed)}){usage_suffix}{cutoff_suffix}")
        elif judge:
            n_judge += 1
            status = "PASS" if rules_pass else "未定義"
            lines.append(f"- ? {cid}: 要judge判定（ルールは{status}）{usage_suffix}{cutoff_suffix}")
        else:
            n_pass += 1
            lines.append(f"- ✓ {cid}: ルールPASS{usage_suffix}{cutoff_suffix}")

    lines.append("")
    lines.append(
        f"集計: pass={n_pass} fail={n_fail} judge待ち={n_judge} error={n_error} / 全{len(results)}件"
    )
    if n_token_measured:
        lines.append(
            f"トークン使用量合計: 入力 {token_total['input_tokens']} / "
            f"出力 {token_total['output_tokens']} / 合計 {token_total['total_tokens']}"
            f"（計測できた{n_token_measured}/{len(results)}件）"
        )
    lines.append("")

    judge_cases = [r for r in results if not r.get("error") and r.get("judge")]
    if judge_cases:
        lines.append("## judge が必要なケースの詳細")
        for r in judge_cases:
            lines.append(f"### {r['case_id']}")
            lines.append(f"judge指示: {r['judge']}")
            lines.append(f"最終回答: {r.get('final_answer', '')}")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(prog="python evals/run_all.py")
    parser.add_argument("target")
    parser.add_argument("case_ids", nargs="*")
    parser.add_argument(
        "--instance",
        help="実行対象インスタンス名（instances/<name>/。省略時は環境変数 LOCOHANE_INSTANCE、それも無ければ default）",
    )
    args = parser.parse_args()
    target = args.target
    # 全ケースを走らせる前に一度だけ解決・検証する（存在しないインスタンス名で
    # 全ケースが同じエラーになるのを避ける）。
    instance_name = resolve_instance_name(args.instance)

    case_paths = _iter_case_paths(target, args.case_ids)
    if not case_paths:
        print(f"対象ケースが1件もありません: evals/cases/{target}/", file=sys.stderr)
        return 1

    print(f"対象インスタンス: {instance_name}", file=sys.stderr)
    results = []
    for path in case_paths:
        print(f"実行中: {path.name}", file=sys.stderr)
        try:
            results.append(_run_one(path, instance_name))
        except subprocess.TimeoutExpired as e:
            results.append(
                {
                    "case_id": path.stem,
                    "instance": instance_name,
                    "error": "timeout",
                    "detail": f"{e.timeout:.0f}秒でタイムアウトしました。",
                }
            )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_ROOT / "evals" / "results" / target / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = _render_summary(target, instance_name, results)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")

    print(summary)
    print(f"\n詳細: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
