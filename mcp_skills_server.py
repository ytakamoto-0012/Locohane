"""Locohaneの skills/ 配下のスキルを、MCPリソース（配布方式）として公開するサーバー。

詳細な設計・注意点は MCP_README.md を参照。要点のみ:

- 配布されるのは SKILL.md および `scripts/`/`references/`/`assets/` 配下の
  全ファイル（テキストとして中身を渡すだけで、Locohane自身はコードを実行しない）。
- SkillsDirectoryProvider（PyPI: fastmcp）はスキルフォルダ配下を再帰的に
  無条件でスキャンするため（除外パターンの指定機能なし）、`web-search` スキルの
  `scripts/.env`（TAVILY_API_KEY）のような機密ファイルがそのまま配布されて
  しまう。これを避けるため、配布用の一時コピーを作る際に EXCLUDE_NAMES に
  該当するファイルを除外している。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS_SRC = PROJECT_ROOT / "skills"

# 配布対象から除外するファイル名／ディレクトリ名。
# .env系: APIキー等の機密情報を含みうるもの（新しいスキルに追加した場合はここにも追記すること）。
# __pycache__: 配布に無意味なPythonコンパイルキャッシュ。
EXCLUDE_NAMES = (".env", ".env.local", ".env.*", "__pycache__")


def build_publish_dir() -> Path:
    """機密ファイルを除いた配布用コピーを一時ディレクトリに作成して返す。"""
    publish_dir = Path(tempfile.mkdtemp(prefix="locohane_mcp_skills_"))
    for skill_dir in sorted(SKILLS_SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        shutil.copytree(
            skill_dir,
            publish_dir / skill_dir.name,
            ignore=shutil.ignore_patterns(*EXCLUDE_NAMES),
        )
    return publish_dir


mcp = FastMCP("locohane-skills")
mcp.add_provider(
    SkillsDirectoryProvider(
        roots=build_publish_dir(),
        supporting_files="resources",
    )
)


if __name__ == "__main__":
    mcp.run()
