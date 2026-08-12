"""Excelファイルを画像(PNG)としてレンダリングする。

excel-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python render_excel.py <excel_path>
の形で呼ばれる。全シート（PDF化後の全ページ）を画像化する。

動作概要:
  1. OLE（COM）で Excel をヘッドレス起動し、各シートの印刷設定を横1ページ×
     縦1ページに収まるフィット印刷へ強制した上でファイルをPDFへエクスポート。
  2. pypdfium2 で PDF ページを画像化（既定300DPI）。
  3. 白黒境界判定で余白を除去（既定）。
  4. シートの縮尺に応じてキャプチャDPIを動的にブースト。

生成したPNGは、作業ディレクトリ配下のセッション専用一時フォルダ
`_tmp_<thread_id>/rendered/` に保存する。
analyze_image は絶対パスをそのまま読めるため、出力JSONの image_path
（絶対パス）を analyze_image に渡せばLLMへ画像として見せられる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from _common import register_output_path, setup_utf8_stdio
from _render import render_office_file


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    args = parser.parse_args()

    path = Path(args.excel_path)
    if not path.exists():
        print(f"ファイルが見つかりません: {args.excel_path}", file=sys.stderr)
        return 1
    if path.is_dir():
        print(f"指定パスはディレクトリです（ファイル専用）: {args.excel_path}", file=sys.stderr)
        return 1
    if path.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
        print(f"対応拡張子は .xlsx/.xlsm/.xls のみです: {path.suffix}", file=sys.stderr)
        return 1

    try:
        result = render_office_file(
            path=path,
            tool="excel",
        )
    except ImportError as e:
        print(f"必要なライブラリが見つかりません（pywin32が必要です）: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Excelでのレンダリングに失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    # パスメモリーへ登録
    path_memory: dict[str, str] = {}
    for image in result.get("images", []):
        pm = register_output_path(image["image_path"], description=f"render_excelが生成したページ{image['page']}の画像")
        if pm:
            path_memory.update(pm)

    if path_memory:
        result["path_memory"] = path_memory

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
