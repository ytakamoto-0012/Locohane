"""ドラフトスキルの一覧（自分の分と、見てよい他ユーザーの分）を返す。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。

    python list_drafts.py

各ドラフトの種類（new=新規 / improve=既存スキルの改善案）・状態・最後の
スキル安定化トライアウトの結果を返す。他ユーザーの分は
config.ini [skill_creator].other_users_drafts が listed 以上のときだけ出る
（listed では名前だけ。中身を読めるのは readable 以上）。差し戻された
ドラフト（status=returned）は、中身を修正すると draft に戻り再び昇格候補になる。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse

from _common import DraftRef, draft_context, parse_frontmatter, print_json, read_meta, run_main


def main() -> int:
    argparse.ArgumentParser().parse_args()
    ctx = draft_context()
    drafts = []
    if ctx.root.is_dir():
        for owner_dir in sorted(ctx.root.iterdir(), key=lambda p: (p.name != ctx.user, p.name)):
            if not owner_dir.is_dir() or not ctx.allowed(owner_dir.name, "list"):
                continue
            for skill_dir in sorted(owner_dir.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith("_") or not (skill_dir / "SKILL.md").is_file():
                    continue
                ref = DraftRef(owner_dir.name, skill_dir.name, skill_dir)
                if not ctx.allowed(ref.owner, "read"):
                    # listed モード: 名前だけ出す。中身（SKILL.md・来歴）は読み取り禁止で、
                    # 開こうとすると書き込みガードの PermissionError で一覧全体が失敗する。
                    drafts.append({"draft": ref.full_name, "own": False, "readable": False})
                    continue
                meta = read_meta(ref)
                fm = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")) or {}
                tryouts = meta.get("tryouts") or []
                drafts.append(
                    {
                        "draft": ref.full_name,
                        "own": ref.owner == ctx.user,
                        "readable": True,
                        "kind": meta.get("kind"),
                        "base_skill": meta.get("base_skill"),
                        "status": meta.get("status", "draft"),
                        "description": fm.get("description"),
                        "updated_at": meta.get("updated_at"),
                        "eval_cases": sorted(p.stem for p in (skill_dir / "evals").glob("*.yaml")),
                        "last_tryout": tryouts[-1] if tryouts else None,
                        "returned_reason": meta.get("returned_reason"),
                    }
                )
    print_json({"user": ctx.user, "drafts": drafts})
    return 0


if __name__ == "__main__":
    run_main(main)
