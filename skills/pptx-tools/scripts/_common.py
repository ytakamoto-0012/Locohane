"""pptx-tools スキル内の各スクリプトが共有するヘルパー関数。

run_script からは直接実行されない（scripts/ prefix チェックを通らないため）。
scripts/ 配下の各スクリプトが同一ディレクトリから import して使う。
"""

from __future__ import annotations

import sys

from pptx.enum.shapes import MSO_SHAPE_TYPE


def setup_utf8_stdio() -> None:
    """標準出力/標準エラーをUTF-8に固定する。

    Windows環境ではパイプ経由のPython子プロセスの既定エンコーディングが
    システムのANSIコードページ（例: cp932）になり、run_script側の
    encoding="utf-8" デコードと食い違って日本語が文字化けするため、
    各スクリプトの先頭で必ず呼ぶこと。
    """
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def describe_shape(shape, index: int) -> dict:
    """1つのshapeの構造情報をdictにする。

    inspect_pptx.py（一覧表示）と edit_pptx.py（エラーメッセージ・対象種別
    チェック）の両方から使う共有ロジック。ここで定義する `shape_index` は
    `enumerate(slide.shapes)` の0始まり連番であり、edit_pptx.py の各操作が
    指定する `shape_index` と完全に一致する仕様として両スクリプトで揃える。
    """
    info: dict = {
        "shape_index": index,
        "name": shape.name,
        "shape_type": str(shape.shape_type) if shape.shape_type is not None else None,
        "is_placeholder": shape.is_placeholder,
        "placeholder_idx": None,
        "placeholder_type": None,
        "has_text_frame": shape.has_text_frame,
        "text_preview": None,
        "has_table": shape.has_table,
        "table_dims": None,
        "has_picture": shape.shape_type == MSO_SHAPE_TYPE.PICTURE,
    }
    if shape.is_placeholder:
        info["placeholder_idx"] = shape.placeholder_format.idx
        info["placeholder_type"] = str(shape.placeholder_format.type)
    if shape.has_text_frame:
        text = shape.text_frame.text
        info["text_preview"] = text[:50] if text else ""
    if shape.has_table:
        table = shape.table
        info["table_dims"] = {"rows": len(table.rows), "cols": len(table.columns)}
    return info
