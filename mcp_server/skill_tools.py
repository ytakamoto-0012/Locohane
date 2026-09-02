"""skills/ を実際に呼び出す3つのMCPツール（Tools方式）。

Locohane本体の3段階progressive disclosure
（`src/skills.py` の frontmatterパース、`src/tools/read_skill.py`、
`src/tools/run_script.py`）と同じ考え方を、Chainlitに依存しない
standalone プロセスとして実装し直したもの:

- list_skills       … Discovery相当（全スキルの name: description 一覧）
- read_skill        … Read相当（SKILL.md 本文全体）
- run_skill_script   … Execute相当（scripts/ 配下のスクリプトを実際に実行）

対象ディレクトリは `config.SKILLS_SRC`（元の `skills/`。複数ルート指定可、
`LOCOHANE_MCP_SKILLS_SRC`参照）を直接見る。
`publish.build_publish_dir()` が作る配布用コピー（`.env` 等除外済み）は
Resources方式（外部への配布）専用であり、ここでの実行はMCPサーバーと
同一マシン上の信頼された呼び出し元がその場で行うだけなので、
`web-search` の `scripts/.env`（TAVILY_API_KEY）等も問題なく使える必要がある
（脅威モデルが異なるため、意図的に別ディレクトリを見ている）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from .config import SCRIPT_TIMEOUT_SECONDS, SKILLS_SRC, WORKDIR


def _parse_frontmatter(text: str) -> dict | None:
    """SKILL.md 先頭の `---` で囲まれた YAML frontmatter を dict で返す。

    `src/skills.py` の `_parse_frontmatter()` と同じロジック（依存を持たせず
    ここに直接複製。standalone プロセスのため Locohane 本体の `src/` は
    importしない）。
    """
    if not text.lstrip().startswith("---"):
        return None
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _safe_skill_path(skill_name: str, relative: str) -> Path | None:
    """`<root>/skill_name/relative` を解決し、境界外なら None を返す。

    `src/tools/_safe_path.py` の `_safe_path()` と同じ考え方
    （`resolve()` 後に `is_relative_to()` でいずれかの `SKILLS_SRC` ルート
    配下に限定するディレクトリトラバーサル対策、かつ複数ルートを前方から
    順に解決し実在する最初の候補を返す）を依存なしで再実装したもの。
    どのルートにも実在しない場合は先頭ルート基準の候補を返す（「見つかりません」
    エラーへ自然に流すため）。全ルートで境界外と判定された場合のみ None。
    """
    candidates: list[Path] = []
    for root in SKILLS_SRC:
        candidate = (root / skill_name / relative).resolve()
        if not candidate.is_relative_to(root.resolve()):
            continue
        candidates.append(candidate)
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _resolve_script_path(skill_name: str, script_filename: str) -> Path:
    """スキルの scripts/ 配下からファイル名でスクリプトを探し、絶対パスを返す。

    `src/tools/_safe_path.py` の `_resolve_script_filename()` と同じ考え方。
    呼び出し側は `scripts/` プレフィックスを書く必要はなく、ファイル名のみ
    渡せば `scripts/` 配下を再帰探索して解決する（同名ファイルが複数階層に
    ある場合は最も浅い階層を採用）。

    Raises:
        ValueError: skill_name が SKILLS_SRC 外を指す場合、scripts/ が
            無い場合、該当ファイルが見つからない場合。
    """
    basename = os.path.basename(script_filename)
    if not basename:
        raise ValueError(f"スクリプトのファイル名を指定してください: {script_filename!r}")
    scripts_root = _safe_skill_path(skill_name, "scripts")
    if scripts_root is None:
        raise ValueError(f"skills ディレクトリ外へのアクセスは許可されません: {skill_name}")
    if not scripts_root.is_dir():
        raise ValueError(f"スキル '{skill_name}' に scripts/ ディレクトリがありません")
    matches = [p for p in scripts_root.rglob(basename) if p.is_file()]
    if not matches:
        raise ValueError(f"スクリプトが見つかりません: {basename}（skill={skill_name}）")
    matches.sort(key=lambda p: (len(p.relative_to(scripts_root).parts), str(p)))
    return matches[0]


def list_skills() -> str:
    """Locohaneのスキル一覧を「name: description」形式の1行ずつで返す。

    どのスキルが使えるかを把握するための最初の一手。ここで気になるスキルの
    name を見つけたら read_skill(skill_name) で手順本文を読むこと。

    Returns:
        各行が "name: description" のテキスト（Agent Skills仕様の
        frontmatterを持たない・検証に失敗したスキルフォルダは除外される）。
        `SKILLS_SRC` が複数ルートを持つ場合、同名スキルは先頭に近いルートの
        ものが優先され1回だけ列挙される。
    """
    lines: list[str] = []
    seen: set[str] = set()
    for root in SKILLS_SRC:
        if not root.is_dir():
            continue
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name in seen:
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            if fm is None:
                continue
            name = fm.get("name")
            description = fm.get("description")
            if isinstance(name, str) and isinstance(description, str):
                seen.add(skill_dir.name)
                lines.append(f"{name}: {description}")
    return "\n".join(lines)


def read_skill(skill_name: str) -> str:
    """スキルの SKILL.md 本文全体を読み込んで返す。

    list_skills で見つけたスキルの手順・呼び出し方法（scripts/配下のどの
    ファイルをどんな引数で呼ぶか）を確認するために使う。

    Args:
        skill_name: 読み込むスキルのフォルダ名（= SKILL.md の name）。

    Returns:
        SKILL.md の本文全体（UTF-8テキスト）。skill_name が不正、または
        SKILL.md が存在しない場合は「エラー: ...」形式の文字列を返す。
    """
    skill_md = _safe_skill_path(skill_name, "SKILL.md")
    if skill_md is None:
        return f"エラー: skills ディレクトリ外へのアクセスは許可されません: {skill_name}"
    if not skill_md.is_file():
        return f"エラー: スキル '{skill_name}' の SKILL.md が見つかりません。"
    return skill_md.read_text(encoding="utf-8")


def run_skill_script(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """スキルの scripts/ 配下のスクリプトを実際に実行し、結果を返す。

    read_skill で確認した呼び出し方法（`python <script>.py <args...>`
    形式で書かれている）に従い、script_filename と script_args を渡すこと。
    生成物の出力先（output_path等）は相対パスに頼らず、絶対パスで
    script_args に指定すること。実行時の作業ディレクトリ（cwd）はこのMCP
    サーバー自身の起動時cwdをそのまま使う（環境変数 LOCOHANE_MCP_SKILLS_WORKDIR が
    設定されていればそちらを優先する）。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 実行したいスクリプトのファイル名（例: "count.py"）。
            scripts/ プレフィックスは不要。
        script_args: スクリプトへ渡す追加引数のリスト（省略可）。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラーがあれば
        それぞれ「[標準出力]」「[標準エラー]」の見出し付きで連結した文字列。
        スキルが見つからない・タイムアウトした・起動自体に失敗した場合は
        いずれも例外を送出せず「エラー: ...」形式で返す。
    """
    try:
        script_path = _resolve_script_path(skill_name, script_filename)
    except ValueError as e:
        return f"エラー: {e}"

    if WORKDIR is not None:
        WORKDIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(script_path), *(script_args or [])]
    try:
        result = subprocess.run(
            cmd,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"エラー: スクリプトが {SCRIPT_TIMEOUT_SECONDS} 秒でタイムアウトしました。"
    except OSError as e:
        return f"エラー: スクリプトの起動に失敗しました: {e}"

    parts = [f"[終了コード] {result.returncode}"]
    if result.stdout:
        parts.append(f"[標準出力]\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"[標準エラー]\n{result.stderr.rstrip()}")
    return "\n".join(parts)
