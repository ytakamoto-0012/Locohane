"""PPTXファイルを画像(PNG)としてレンダリングする。

pptx-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python render_pptx.py <pptx_path> [--start-page N] [--max-pages N] [--dpi N] [--crop]
の形で呼ばれる。

動作概要:
  1. OLE（COM）で PowerPoint をヘッドレス起動し、ファイルをPDFへエクスポート。
  2. pypdfium2 で PDF ページを画像化（既定300DPI）。
  3. --crop 指定時のみ、余白除去（コンテンツ領域のbboxでクロップ）。
  4. 目標DPI（150）に縮尺して保存。

既定はクロップなし（スライドの実際の端がそのまま見える）。スライドのサイズ・
余白はpptxファイル自体が定義済みで、ピクセル走査で推測する必要が無いうえ、
クロップすると余白の均等さ・中央揃え・スライドが薄すぎないか等のレイアウトQAに
必要な情報が失われるため。表計算のような「意味のあるデータ領域が可変」な
excel-renderとは事情が異なる。

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

DPI_MIN = 72
DPI_MAX = 600


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx_path")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--crop", action="store_true",
                        help="コンテンツ領域のbboxで余白を除去する（既定は無効。レイアウトQA目的では通常不要）")
    args = parser.parse_args()

    path = Path(args.pptx_path)
    if not path.exists():
        print(f"ファイルが見つかりません: {args.pptx_path}", file=sys.stderr)
        return 1
    if path.is_dir():
        print(f"指定パスはディレクトリです（ファイル専用）: {args.pptx_path}", file=sys.stderr)
        return 1
    if path.suffix.lower() != ".pptx":
        print(f"対応拡張子は .pptx のみです: {path.suffix}", file=sys.stderr)
        return 1

    try:
        dpi = min(max(args.dpi, DPI_MIN), DPI_MAX)
        result = render_office_file(
            path=path,
            tool="pptx",
            start_page=args.start_page,
            max_pages=args.max_pages,
            dpi=dpi,
            crop=args.crop,
        )
    except ImportError as e:
        print(f"必要なライブラリが見つかりません（pywin32が必要です）: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"PowerPointでのレンダリングに失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    # パスメモリーへ登録
    path_memory: dict[str, str] = {}
    for image in result.get("images", []):
        pm = register_output_path(image["image_path"],
                                  description=f"render_pptxが生成したスライド{image['page']}の画像")
        if pm:
            path_memory.update(pm)

    if path_memory:
        result["path_memory"] = path_memory

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
