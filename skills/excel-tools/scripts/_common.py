"""excel-tools スキル内の各スクリプトが共有するヘルパー関数。

run_script からは直接実行されない（scripts/ prefix チェックを通らないため）。
scripts/ 配下の各スクリプトが同一ディレクトリから import して使う。
標準ライブラリのみで完結する。
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time


def setup_utf8_stdio() -> None:
    """標準出力/標準エラーをUTF-8に固定する。

    Windows環境ではパイプ経由のPython子プロセスの既定エンコーディングが
    システムのANSIコードページ（例: cp932）になり、run_script側の
    encoding="utf-8" デコードと食い違って日本語が文字化けするため、
    各スクリプトの先頭で必ず呼ぶこと。
    """
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def cell_to_json(value: object) -> object:
    """セル値をJSON化できる型へ変換する（日時系はISO8601文字列にする）。"""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def resolve_sheet_name(names: list[str], sheet_arg: str) -> str:
    """シート名の完全一致、次に0始まりインデックスとして解決する。"""
    if sheet_arg in names:
        return sheet_arg
    try:
        idx = int(sheet_arg)
    except ValueError:
        raise ValueError(f"シートが見つかりません: {sheet_arg}（存在するシート: {names}）")
    if 0 <= idx < len(names):
        return names[idx]
    raise ValueError(f"シートインデックスが範囲外です: {sheet_arg}（シート数: {len(names)}）")
