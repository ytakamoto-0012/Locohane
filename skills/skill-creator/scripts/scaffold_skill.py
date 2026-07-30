"""新しいスキルの雛形（SKILL.md + scripts/ + references/）を生成する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script から次の形式で呼ばれる想定:

    python scaffold_skill.py --name my-new-skill --description "..." \\
        [--with-script]

新規スキルは常に `.locohane/skills/<name>/` に生成する（プロジェクト
ルート直下の `skills/` はビルトイン相当のスキルの置き場のため、
skill-creator が生成するスキルはそちらには書き込まない）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import print_json, project_root

_NAME_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*$")
_NAME_MAX = 64
_DESC_MAX = 1024

SKILL_MD_TEMPLATE = """---
name: {name}
description: {description}
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# {name}

（ここにスキルの概要を1〜2文で書く）

## 手順

1. （最初にやること）
2. `run_script` ツールを次の形式で呼び出す:
   ```json
   {{
       "skill_name": "{name}",
       "script_filename": "xxx.py",
       "script_args": ["..."]
   }}
   ```
3. スクリプトが返す JSON のキーをどう解釈してユーザーに報告するかを書く。

## 出力例

```json
{{"...": "..."}}
```

## エッジケース

- （入力が不正な場合の挙動、境界値など）
"""

SAMPLE_SCRIPT_TEMPLATE = '''"""{name} スキルの実行スクリプト（progressive disclosure 第3段階）。

run_script ツールから呼ばれる。標準ライブラリのみで自己完結させること
（依存が必要な場合は SKILL.md にインストール手順を明記する）。
"""

import json
import sys


def main() -> int:
    result = {{"ok": True}}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _validate_name_description(name: str, description: str) -> str | None:
    """src/skills.py の _validate() と同一ルールで検証する。理由文字列 or None。"""
    if not name:
        return "name が空です"
    if len(name) > _NAME_MAX:
        return f"name が {_NAME_MAX} 文字を超えている"
    if "--" in name or "__" in name or "-_" in name or "_-" in name:
        return "name に区切り文字 (- や _) の連続が含まれる"
    if not _NAME_RE.match(name):
        return "name は小文字英数字・ハイフン・アンダースコアのみ・先頭末尾は区切り文字不可"
    if not description.strip():
        return "description が空です"
    if len(description) > _DESC_MAX:
        return f"description が {_DESC_MAX} 文字を超えている"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--with-script", action="store_true")
    args = parser.parse_args()

    error = _validate_name_description(args.name, args.description)
    if error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    root = project_root()
    skills_root = root / ".locohane" / "skills"
    skill_dir = skills_root / args.name

    if skill_dir.exists():
        print(f"エラー: 既に存在します: {skill_dir}", file=sys.stderr)
        return 1

    created: list[str] = []
    skill_dir.mkdir(parents=True)
    created.append(str(skill_dir))

    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(
        SKILL_MD_TEMPLATE.format(name=args.name, description=args.description),
        encoding="utf-8",
    )
    created.append(str(skill_md_path))

    (skill_dir / "references").mkdir()
    created.append(str(skill_dir / "references"))

    if args.with_script:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        created.append(str(scripts_dir))
        sample_path = scripts_dir / "run.py"
        sample_path.write_text(SAMPLE_SCRIPT_TEMPLATE.format(name=args.name), encoding="utf-8")
        created.append(str(sample_path))

    print_json(
        {
            "skill_dir": str(skill_dir),
            "skill_md_path": str(skill_md_path),
            "created": created,
            "note": "アプリはホットリロードしないため、動作確認にはLocohaneアプリの再起動が必要です。",
        }
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
