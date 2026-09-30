"""ダッシュボードの「APIリファレンス」メニューに表示する Markdown の読み込み・HTML化。

原本は admin/API_REFERENCE.md。リクエストのたびにファイルを読み直すため、
md を編集すれば管理ツールを再起動しなくても次の表示で反映される。
"""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt

API_REFERENCE_PATH = Path(__file__).resolve().parent / "API_REFERENCE.md"

# html=False: md 内の生HTMLはエスケープして表示する（innerHTML へ流し込むため）。
_md = MarkdownIt("commonmark", {"html": False}).enable("table")


def render(path: Path = API_REFERENCE_PATH) -> dict[str, str]:
    """md を読み、{"markdown": 原文, "html": 変換後HTML} を返す。

    Raises:
        FileNotFoundError: md が存在しない。
    """
    text = path.read_text(encoding="utf-8")
    return {"markdown": text, "html": _md.render(text)}
