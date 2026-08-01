"""PDFページを画像(PNG)としてレンダリングする。

pdf-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python render_pdf_pages.py <pdf_path> [--start-page N] [--max-pages N] [--dpi N]
の形で呼ばれる。

生成したPNGはこのスキル自身の rendered/ ディレクトリ（skills ルート配下）に保存する。
view_image ツールが読めるのは skills ルート配下のファイルのみのため、レンダリング先を
スキル外の任意パスにはできない。出力JSONの relative_path をそのまま view_image に渡せば
LLMへ画像として見せられる。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _common import setup_utf8_stdio

import pypdfium2 as pdfium

RENDERED_DIR = Path(__file__).resolve().parent.parent / "rendered"
MAX_PAGES_CAP = 5
DPI_MIN = 72
DPI_MAX = 300


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    path = Path(args.pdf_path)
    if not path.exists():
        print(f"ファイルが見つかりません: {args.pdf_path}", file=sys.stderr)
        return 1
    if path.is_dir():
        print(f"指定パスはディレクトリです（ファイル専用）: {args.pdf_path}", file=sys.stderr)
        return 1

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as e:  # noqa: BLE001 - 壊れたPDF/暗号化PDF等はすべてエラー扱いにする
        print(f"PDFを開けませんでした（壊れているか暗号化されている可能性）: {e}", file=sys.stderr)
        return 1

    total_pages = len(pdf)
    max_pages = min(max(args.max_pages, 1), MAX_PAGES_CAP)
    dpi = min(max(args.dpi, DPI_MIN), DPI_MAX)
    scale = dpi / 72

    start_idx = max(args.start_page, 1) - 1
    end_idx = min(start_idx + max_pages, total_pages)

    RENDERED_DIR.mkdir(exist_ok=True)
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:8]

    images = []
    for i in range(start_idx, end_idx):
        page_num = i + 1
        filename = f"{digest}_p{page_num}.png"
        out_path = RENDERED_DIR / filename
        bitmap = pdf[i].render(scale=scale)
        bitmap.to_pil().save(out_path)
        images.append({"page": page_num, "relative_path": f"pdf-tools/rendered/{filename}"})

    result = {
        "path": str(path),
        "total_pages": total_pages,
        "start_page": start_idx + 1 if images else None,
        "end_page": end_idx if images else None,
        "dpi": dpi,
        "images": images,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
