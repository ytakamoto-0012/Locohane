"""SKILL.md の frontmatter を src/skills.py の _validate() と同一ルールで検証する。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script から次の形式で呼ばれる想定:

    python validate_skill.py --skill-dir "C:\\...\\skills\\my-new-skill"

frontmatter のパースは PyYAML 等の外部依存を避け、`---` 区切りの範囲を
単純な行走査で読む（name/description/license の単純なスカラー値のみを
拾えれば十分なため、YAML の入れ子構造は解釈しない）。厳密な検証が
必要な場合は `skills-ref validate` を別途使うこと（README.mdの
Agent Skills 仕様準拠範囲を参照）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _common import print_json

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NAME_MAX = 64
_DESC_MAX = 1024


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """先頭の `---\\n...\\n---` ブロックから name/description/license を拾う。

    src/skills.py の _parse_frontmatter() は PyYAML でパースしているが、
    ここでは name/description/license の単純な `key: value` 行のみを
    対象にした簡易パーサーで代用する（このスクリプトの目的は事前検証で
    あり、最終的な合否は起動時の scan_skills() が唯一の正）。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None

    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.startswith(" ") or line.startswith("\t"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in ("name", "description", "license") and value:
            # クォート囲みを除去
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key] = value
    return result


def _validate(name: str | None, description: str | None, dir_name: str) -> str | None:
    """src/skills.py の _validate() と同一ロジック。"""
    if not isinstance(name, str) or not name:
        return "name が無い、または文字列でない"
    if len(name) > _NAME_MAX:
        return f"name が {_NAME_MAX} 文字を超えている"
    if "--" in name:
        return "name に連続ハイフン (--) が含まれる"
    if not _NAME_RE.match(name):
        return "name は小文字英数字とハイフンのみ・先頭末尾ハイフン不可"
    if name != dir_name:
        return f"name '{name}' が親ディレクトリ名 '{dir_name}' と一致しない"
    if not isinstance(description, str) or not description.strip():
        return "description が無い、または空"
    if len(description) > _DESC_MAX:
        return f"description が {_DESC_MAX} 文字を超えている"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir", required=True)
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir)
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.is_file():
        print_json({"valid": False, "error": f"SKILL.md が見つかりません: {skill_md}"})
        return 1

    text = skill_md.read_text(encoding="utf-8", errors="replace")
    frontmatter = _parse_frontmatter(text)
    if frontmatter is None:
        print_json({"valid": False, "error": "frontmatter（先頭の --- ブロック）が見つかりません"})
        return 1

    reason = _validate(frontmatter.get("name"), frontmatter.get("description"), skill_dir.name)

    scripts_dir = skill_dir / "scripts"
    script_files = sorted(p.name for p in scripts_dir.glob("*")) if scripts_dir.is_dir() else []

    print_json(
        {
            "valid": reason is None,
            "error": reason,
            "name": frontmatter.get("name"),
            "description": frontmatter.get("description"),
            "description_length": len(frontmatter.get("description", "")),
            "dir_name": skill_dir.name,
            "has_scripts_dir": scripts_dir.is_dir(),
            "script_files": script_files,
        }
    )
    return 0 if reason is None else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
