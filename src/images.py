"""画像ファイル→OpenAI互換 data URL 変換の共通ヘルパー。

app.py（ユーザーアップロード画像）と src/tools.py（view_image ツール）の
両方から使う。画像をVision対応モデルへ渡す唯一の経路は、OpenAI互換APIの
`{"type": "image_url", "image_url": {"url": "data:<mime>;base64,<...>"}}`
という content 要素なので、そこへの変換だけをここに集約する。

image_followup_message() は tools.py（ImageAwareToolNode）と subagent.py
（サブエージェントの独立ループ）の両方が、view_image の
response_format="content_and_artifact" が返す artifact から画像付き
HumanMessage を組み立てる際に使う共有ヘルパー。tools.py にも subagent.py
にも依存しない末端モジュールなので、ここに置いても循環 import にならない。
"""

from __future__ import annotations

import base64
from pathlib import Path

from langchain_core.messages import HumanMessage

# 対応拡張子→MIMEタイプ。mimetypes モジュールの環境依存を避けるため固定表で持つ。
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def is_image_file(path: str | Path) -> bool:
    """拡張子が対応画像形式（png/jpg/jpeg/gif/webp/bmp）かどうかを返す。"""
    return Path(path).suffix.lower() in _MIME_BY_EXT


def to_data_url(path: str | Path) -> str:
    """画像ファイルを base64 エンコードし、data URL 文字列として返す。

    Args:
        path: 画像ファイルの絶対パス。拡張子は _MIME_BY_EXT に含まれるもの
            であることを is_image_file() で事前に確認しておくこと。

    Returns:
        "data:<mime>;base64,<...>" 形式の文字列。

    Raises:
        KeyError: 拡張子が _MIME_BY_EXT にない場合。
    """
    p = Path(path)
    mime = _MIME_BY_EXT[p.suffix.lower()]
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def image_followup_message(artifact: dict | None) -> HumanMessage | None:
    """artifact に画像URLがあれば、画像を content に持つ HumanMessage を返す。

    Args:
        artifact: ツール実行結果の artifact（例: view_image が返す
            {"image_url": "data:<mime>;base64,<...>"}）。None や
            image_url を持たない dict の場合は None を返す。

    Returns:
        画像付き HumanMessage、または該当なしの場合は None。
    """
    if isinstance(artifact, dict) and "image_url" in artifact:
        return HumanMessage(
            content=[{"type": "image_url", "image_url": {"url": artifact["image_url"]}}]
        )
    return None
