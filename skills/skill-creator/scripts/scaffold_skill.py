"""新しいドラフトスキルの雛形（SKILL.md + references/ + evals/ +（任意で）scripts/）を作る。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。

    python scaffold_skill.py --name my-new-skill --description "..." [--with-script]

作成先は会話のユーザー自身のドラフト置き場 `<draft_dir>/<ユーザー名>/<name>/`。
正式スキル（skills/ や project_locohane_dir 配下）には書き込まない。正式スキルと
同じ名前は拒否する（既存スキルを直したいときは fork_skill.py で改善案を作る）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import os

from _common import (
    DRAFT_EVALS_DIRNAME,
    SkillCreatorError,
    draft_context,
    find_official_skill,
    now_iso,
    print_json,
    resolve_draft,
    run_main,
    validate_name_description,
    write_meta,
)

SKILL_MD_TEMPLATE = """---
name: {name}
description: {description}
---

# {name}

（このスキルが何をするかを1〜2文で書く）

## 手順

1. （最初にやること）
2. 次のコマンドを実行する:
   ```
   python run.py --input <入力ファイル>
   ```
3. 出力JSONのキーの意味と、ユーザーへの報告のしかたを書く。

## 注意

- （入力が不正な場合の扱い、やってはいけないこと）
"""

SAMPLE_SCRIPT_TEMPLATE = '''"""{name} スキルの実行スクリプト。

標準ライブラリのみで自己完結させる（依存が必要なら SKILL.md に明記する）。
正常時は終了コード0で標準出力に1行のJSON、異常時は終了コード1で標準エラーに理由を出す。
"""

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    print(json.dumps({{"ok": True, "input": args.input}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="スキル名（小文字英数字とハイフン）")
    parser.add_argument("--description", required=True, help="何をするか・どんな発話で使うか")
    parser.add_argument("--with-script", action="store_true", help="scripts/run.py の見本も作る")
    args = parser.parse_args()

    error = validate_name_description(args.name, args.description)
    if error:
        raise SkillCreatorError(error)
    if find_official_skill(args.name) is not None:
        raise SkillCreatorError(
            f"正式スキル '{args.name}' と同じ名前です。別の名前にするか、既存スキルを直すなら fork_skill.py を使ってください。"
        )
    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write", must_exist=False)
    if ref.dir.exists():
        raise SkillCreatorError(f"同じ名前のドラフトが既にあります: {ref.full_name}")

    ref.dir.mkdir(parents=True)
    (ref.dir / "SKILL.md").write_text(SKILL_MD_TEMPLATE.format(name=args.name, description=args.description), encoding="utf-8")
    (ref.dir / "references").mkdir()
    (ref.dir / DRAFT_EVALS_DIRNAME).mkdir()
    if args.with_script:
        (ref.dir / "scripts").mkdir()
        (ref.dir / "scripts" / "run.py").write_text(SAMPLE_SCRIPT_TEMPLATE.format(name=args.name), encoding="utf-8")
    write_meta(
        ref,
        {
            "kind": "new",
            "author": ctx.user,
            "last_editor": ctx.user,
            "thread_id": os.environ.get("AGENT_THREAD_ID"),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "draft",
            "tryouts": [],
        },
    )
    print_json(
        {
            "draft": ref.full_name,
            "skill_dir": str(ref.dir),
            "note": "次のメッセージから、自分の会話でこのスキルが使えます。本文は write_draft_file.py で書き直してください。",
        }
    )
    return 0


if __name__ == "__main__":
    run_main(main)
