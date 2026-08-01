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
import io
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

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


def _downscale_to_jpeg(data: bytes, max_long_side: int, jpeg_quality: int) -> bytes | None:
    """長辺が max_long_side を超える画像だけを縮小し、JPEGバイト列として返す。

    Vision モデルが1枚の画像に費やすトークン量は画素数でほぼ決まるため、
    スマホ撮影のような 4032x3024 の画像をそのまま渡すと、数枚でサブエージェントの
    トークン上限に達してしまう（実測: レシピ写真297枚の中央値2.1MB・合計745MB）。

    Args:
        data: 元画像のバイト列。
        max_long_side: 縮小後の長辺のピクセル数。
        jpeg_quality: 再エンコード時のJPEG品質（1-95）。

    Returns:
        縮小したJPEGのバイト列。縮小が不要（既に長辺が上限以下）だった場合と、
        画像として読めなかった場合は None（呼び出し元は元のバイト列をそのまま使う）。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            # 元バイト列をそのまま渡していたときはEXIFの回転情報も一緒に渡って
            # いたが、再エンコードすると失われる。ここで実際に回転させておかないと
            # 横倒しの写真をモデルに読ませることになる。
            img = ImageOps.exif_transpose(img)
            long_side = max(img.size)
            if long_side <= max_long_side:
                return None
            scale = max_long_side / long_side
            new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(new_size, Image.LANCZOS)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                # JPEGは透過を持てない。そのまま変換すると透過部分が黒く潰れて
                # 文字が読めなくなるため、白背景へ合成してから変換する。
                background = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=jpeg_quality)
            return buffer.getvalue()
    except Exception:
        # 縮小の失敗で画像を渡せなくなる方が損失が大きいため、元画像へフォールバックする。
        logger.exception("画像の縮小に失敗しました。元の解像度のまま渡します")
        return None


def to_data_url(
    path: str | Path,
    *,
    max_long_side: int = 0,
    jpeg_quality: int = 85,
) -> str:
    """画像ファイルを base64 エンコードし、data URL 文字列として返す。

    Args:
        path: 画像ファイルの絶対パス。拡張子は _MIME_BY_EXT に含まれるもの
            であることを is_image_file() で事前に確認しておくこと。
        max_long_side: 縮小後の長辺のピクセル数の上限（config.ini
            [images].max_long_side_pixels）。0以下、または画像の長辺が既に
            この値以下の場合は**再エンコードせず元のバイト列をそのまま使う**
            （劣化もPillowのコストも発生しない）。
        jpeg_quality: 縮小時のJPEG品質（config.ini [images].jpeg_quality）。

    Returns:
        "data:<mime>;base64,<...>" 形式の文字列。縮小した場合の mime は
        常に image/jpeg になる。

    Raises:
        KeyError: 拡張子が _MIME_BY_EXT にない場合。
    """
    p = Path(path)
    mime = _MIME_BY_EXT[p.suffix.lower()]
    data = p.read_bytes()
    if max_long_side > 0:
        downscaled = _downscale_to_jpeg(data, max_long_side, jpeg_quality)
        if downscaled is not None:
            data = downscaled
            mime = "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
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
