"""ドラフトスキルの中のファイル（SKILL.md・scripts/・references/ 等）を書く。

skill-creator スキルの実行スクリプト（progressive disclosure 第3段階）。

    # ファイル全体を書く（新規作成・上書き）
    python write_draft_file.py --name my-skill --path SKILL.md --content "...全文..."
    # 長い内容は、作業フォルダに書いたファイルから写す
    python write_draft_file.py --name my-skill --path scripts/run.py --content-file <作業フォルダのファイル>
    # 一部だけ置き換える（old がファイル内で1か所だけ一致する必要がある）
    python write_draft_file.py --name my-skill --path SKILL.md --old "古い文" --new "新しい文"

書き込み先はドラフトのスキルフォルダの中だけ（`..`・絶対パス・_draft_meta.json は拒否）。
SKILL.md を書いた後は frontmatter を検証し、違反していれば書き込みを取り消す
（不正な SKILL.md は Locohane がスキルとして読み込まないため）。

自己完結（標準ライブラリのみ）。依存なし。
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath

from _common import (
    DRAFT_META_FILENAME,
    SkillCreatorError,
    draft_context,
    print_json,
    resolve_draft,
    run_main,
    touch_meta,
    validate_skill_md,
)


def _target_path(skill_dir: Path, raw: str) -> Path:
    rel = PurePosixPath(raw.replace("\\", "/"))
    if not raw.strip() or rel.is_absolute() or ".." in rel.parts or ":" in raw:
        raise SkillCreatorError(f"--path はスキルフォルダからの相対パスで指定してください: {raw!r}")
    if rel.parts[0] == DRAFT_META_FILENAME:
        raise SkillCreatorError(f"{DRAFT_META_FILENAME} は書き換えられません。")
    target = (skill_dir / Path(*rel.parts)).resolve()
    if not target.is_relative_to(skill_dir.resolve()):
        raise SkillCreatorError(f"スキルフォルダの外へは書き込めません: {raw!r}")
    return target


def _new_text(target: Path, args: argparse.Namespace) -> str:
    modes = [args.content is not None, args.content_file is not None, args.old is not None]
    if sum(modes) != 1:
        raise SkillCreatorError("--content / --content-file / --old（と --new）のどれか1つを指定してください。")
    if args.content is not None:
        return args.content
    if args.content_file is not None:
        source = Path(args.content_file)
        if not source.is_file():
            raise SkillCreatorError(f"--content-file が見つかりません: {source}")
        try:
            return source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise SkillCreatorError(f"--content-file を読めません: {e}") from e
    if args.new is None:
        raise SkillCreatorError("--old には --new も指定してください。")
    if not target.is_file():
        raise SkillCreatorError(f"置き換え対象のファイルがありません: {target.name}（全体を書くなら --content）")
    text = target.read_text(encoding="utf-8")
    count = text.count(args.old)
    if count != 1:
        raise SkillCreatorError(
            f"--old がファイル内で{count}か所一致しました（1か所だけ一致する必要があります）。前後の文も含めて指定してください。"
        )
    return text.replace(args.old, args.new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="ドラフト名（自分のドラフトはスキル名だけでよい）")
    parser.add_argument("--path", required=True, help="スキルフォルダからの相対パス（例: SKILL.md, scripts/run.py）")
    parser.add_argument("--content", help="ファイル全体の内容")
    parser.add_argument("--content-file", help="内容を写すファイル（作業フォルダに書いた長い内容など）")
    parser.add_argument("--old", help="置き換える部分（ファイル内で1か所だけ一致すること）")
    parser.add_argument("--new", help="--old を置き換える内容")
    args = parser.parse_args()

    ctx = draft_context()
    ref = resolve_draft(ctx, args.name, "write")
    target = _target_path(ref.dir, args.path)
    text = _new_text(target, args)

    previous = target.read_bytes() if target.is_file() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    if target == (ref.dir / "SKILL.md").resolve():
        error = validate_skill_md(ref.dir)
        if error:
            if previous is None:
                target.unlink()
            else:
                target.write_bytes(previous)
            raise SkillCreatorError(f"SKILL.md の frontmatter が不正なため書き込みを取り消しました: {error}")
    touch_meta(ctx, ref)
    print_json({"draft": ref.full_name, "path": str(target), "chars": len(text)})
    return 0


if __name__ == "__main__":
    run_main(main)
