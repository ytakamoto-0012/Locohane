"""promote-skill スキルの補助スクリプト（ドラフトの一覧・事前確認・昇格・差し戻し）。

判断（トライアウト結果の judge 判定・ユーザーの承認）は Claude Code が行い、
ここではファイル操作と機械的な確認だけを行う。CLAUDE.md 記載の Python 実行環境で、
プロジェクトルートから実行する:

    python .claude/skills/promote-skill/scripts/promote_helper.py list
    python .claude/skills/promote-skill/scripts/promote_helper.py check <ドラフトのフォルダ>
    python .claude/skills/promote-skill/scripts/promote_helper.py install <ドラフトのフォルダ> --dest <skills ルート> --instance <名前> --tryout <tryout.json>
    python .claude/skills/promote-skill/scripts/promote_helper.py return <ドラフトのフォルダ> --reason "..." [--reject]

ハッシュ・コピー規則は skill-creator（skills/skill-creator/scripts/_common.py）と共有する。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "skill-creator" / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT))

from _common import DRAFT_EVALS_DIRNAME, DRAFT_META_FILENAME, copy_ignore, tree_sha256  # noqa: E402

PROMOTION_LOG = PROJECT_ROOT / "evals" / "promotion_log.md"


def _instance_names() -> list[str]:
    root = PROJECT_ROOT / "instances"
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return names or ["default"]


def _instance_config(name: str) -> dict:
    """インスタンスを適用した実効設定のうち、昇格に要る値を別プロセスで求める。"""
    code = (
        "import json,sys; from evals.instance import apply_instance; apply_instance(sys.argv[1]);"
        "from src.config import load_config; c=load_config();"
        "print(json.dumps({'draft_dir': str(c.skill_draft_dir), 'tryout_repeats': c.skill_tryout_repeats,"
        "'skills_roots': [str(c.skills_dir), *[str(d) for d in c.locohane_skills_dirs]]}))"
    )
    out = subprocess.run([sys.executable, "-c", code, name], cwd=str(PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit(f"インスタンス {name} の設定を読めません: {out.stderr.strip()}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _read_meta(draft: Path) -> dict:
    path = draft / DRAFT_META_FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write_meta(draft: Path, meta: dict) -> None:
    (draft / DRAFT_META_FILENAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_list(_args) -> int:
    seen: set[str] = set()
    drafts = []
    for name in _instance_names():
        cfg = _instance_config(name)
        root = Path(cfg["draft_dir"])
        if str(root) in seen or not root.is_dir():
            continue
        seen.add(str(root))
        for owner in sorted(p for p in root.iterdir() if p.is_dir()):
            for skill in sorted(p for p in owner.iterdir() if p.is_dir() and not p.name.startswith("_")):
                if not (skill / "SKILL.md").is_file():
                    continue
                meta = _read_meta(skill)
                tryouts = meta.get("tryouts") or []
                drafts.append(
                    {
                        "instance": name,
                        "draft": f"{owner.name}/{skill.name}",
                        "path": str(skill),
                        "kind": meta.get("kind"),
                        "status": meta.get("status", "draft"),
                        "updated_at": meta.get("updated_at"),
                        "eval_cases": sorted(p.stem for p in (skill / DRAFT_EVALS_DIRNAME).glob("*.yaml")),
                        "last_tryout": tryouts[-1] if tryouts else None,
                        "tryout_repeats": cfg["tryout_repeats"],
                        "skills_roots": cfg["skills_roots"],
                    }
                )
    print(json.dumps(drafts, ensure_ascii=False, indent=2))
    return 0


def _check_problems(draft: Path, instance: str) -> tuple[list[str], list[Path], list[str]]:
    """昇格を止めるべき問題・同名の正式スキル・ケース一覧を返す（check と install で共有）。"""
    meta = _read_meta(draft)
    name = draft.name
    problems: list[str] = []
    official = [Path(r) / name for r in _instance_config(instance)["skills_roots"] if (Path(r) / name / "SKILL.md").is_file()]
    if meta.get("status", "draft") != "draft":
        problems.append(f"status が draft ではありません: {meta.get('status')}")
    if meta.get("kind") == "improve":
        base = Path(meta.get("base_root", "")) / meta.get("base_skill", name)
        if not (base / "SKILL.md").is_file():
            problems.append(f"元にした正式スキルが見つかりません: {base}")
        elif tree_sha256(base) != meta.get("base_sha256"):
            problems.append(f"元にした正式スキルがドラフト作成後に変更されています（上書きすると変更が失われる）: {base}")
    elif official:
        problems.append(f"同じ名前の正式スキルがあります: {[str(p) for p in official]}")
    cases = sorted(p.stem for p in (draft / DRAFT_EVALS_DIRNAME).glob("*.yaml"))
    if not cases:
        problems.append("evals/ にケースがありません（トライアウトできない）")
    return problems, official, cases


def cmd_check(args) -> int:
    draft = Path(args.draft).resolve()
    meta = _read_meta(draft)
    problems, official, cases = _check_problems(draft, args.instance)
    print(json.dumps({"draft": str(draft), "kind": meta.get("kind"), "official_same_name": [str(p) for p in official], "eval_cases": cases, "ok": not problems, "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def cmd_install(args) -> int:
    draft = Path(args.draft).resolve()
    dest_root = Path(args.dest).resolve()
    allowed = [Path(r).resolve() for r in _instance_config(args.instance)["skills_roots"]]
    if dest_root not in allowed:
        raise SystemExit(f"--dest は正式スキルの走査ルートのどれかにしてください: {[str(r) for r in allowed]}")
    problems, _, _ = _check_problems(draft, args.instance)
    if problems:
        raise SystemExit("昇格できません: " + " / ".join(problems))
    tryout = json.loads(Path(args.tryout).read_text(encoding="utf-8"))
    if tryout.get("verdict") != "pass" and not args.judged_pass:
        raise SystemExit("トライアウトが合格（verdict=pass）ではありません。judge を読んで全回合格と判断した場合のみ --judged-pass を付ける。")
    meta = _read_meta(draft)
    target = dest_root / draft.name
    backup = None
    if meta.get("kind") == "improve" and target.resolve() != (Path(meta.get("base_root", "")) / meta.get("base_skill", draft.name)).resolve():
        raise SystemExit(f"改善案は元にした正式スキルの場所へ昇格してください: {Path(meta.get('base_root', '')) / meta.get('base_skill', draft.name)}")
    if target.exists():
        if meta.get("kind") != "improve":
            raise SystemExit(f"昇格先に同名のスキルがあります: {target}")
        backup = PROJECT_ROOT / "evals" / "history" / "promote" / f"{draft.name}_{datetime.now():%Y%m%d_%H%M%S}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup)
        shutil.rmtree(target)
    shutil.copytree(draft, target, ignore=copy_ignore)
    cases_dest = PROJECT_ROOT / "evals" / "cases" / draft.name
    cases_dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for case in sorted((draft / DRAFT_EVALS_DIRNAME).glob("*.yaml")):
        shutil.copy2(case, cases_dest / case.name)
        copied.append(case.name)
    meta.update({"status": "promoted", "promoted_at": _now(), "promoted_to": str(target)})
    _write_meta(draft, meta)
    owner = draft.parent.name
    entry = (
        f"\n## {_now()} {draft.name}（{meta.get('kind')}、作成者 {owner}、インスタンス {args.instance}）\n\n"
        f"- 昇格先: {target}\n- ケース: {', '.join(copied)} → evals/cases/{draft.name}/\n"
        f"- トライアウト: 各{tryout.get('repeat')}回、判定 {tryout.get('verdict')}{'（judge 判定で全回合格）' if args.judged_pass else ''}\n"
        + (f"- 置き換え前のバックアップ: {backup}\n" if backup else "")
        + (f"- メモ: {args.note}\n" if args.note else "")
    )
    if not PROMOTION_LOG.exists():
        PROMOTION_LOG.write_text("# スキル昇格ログ\n\npromote-skill スキルで正式化したドラフトの記録。\n", encoding="utf-8")
    with PROMOTION_LOG.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(json.dumps({"installed": str(target), "cases": copied, "backup": str(backup) if backup else None}, ensure_ascii=False))
    return 0


def cmd_return(args) -> int:
    draft = Path(args.draft).resolve()
    meta = _read_meta(draft)
    meta.update({"status": "rejected" if args.reject else "returned", "returned_reason": args.reason, "returned_at": _now()})
    _write_meta(draft, meta)
    print(json.dumps({"draft": str(draft), "status": meta["status"]}, ensure_ascii=False))
    return 0


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(PROJECT_ROOT)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("check")
    p.add_argument("draft")
    p.add_argument("--instance", default="default")
    p = sub.add_parser("install")
    p.add_argument("draft")
    p.add_argument("--dest", required=True)
    p.add_argument("--instance", default="default")
    p.add_argument("--tryout", required=True, help="evals/run_all.py --repeat が書いた tryout.json")
    p.add_argument("--judged-pass", action="store_true", help="needs_judge を judge 判定で全回合格と判断した")
    p.add_argument("--note", default="")
    p = sub.add_parser("return")
    p.add_argument("draft")
    p.add_argument("--reason", required=True)
    p.add_argument("--reject", action="store_true", help="差し戻しではなく不採用にする")
    args = parser.parse_args()
    return {"list": cmd_list, "check": cmd_check, "install": cmd_install, "return": cmd_return}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
