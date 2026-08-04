"""テキストからPDFファイルを生成する（日本語対応）。

pdf-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python create_pdf.py <output_path> [--title T] (--text TEXT | --text-file PATH)
の形で呼ばれる。

reportlab の組み込みCIDフォント（HeiseiMin-W3=明朝, HeiseiKakuGo-W5=ゴシック）を
使うため、外部フォントファイルの用意なしで日本語を描画できる。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from _common import register_output_path, setup_utf8_stdio

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def build_flowables(title: str | None, body_text: str) -> list:
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))

    title_style = ParagraphStyle(
        "Title", fontName="HeiseiKakuGo-W5", fontSize=16, leading=22, spaceAfter=12
    )
    body_style = ParagraphStyle(
        "Body", fontName="HeiseiMin-W3", fontSize=11, leading=17, spaceAfter=10
    )

    flowables: list = []
    if title:
        flowables.append(Paragraph(escape(title), title_style))

    for para in body_text.split("\n\n"):
        if not para.strip():
            continue
        html = escape(para).replace("\n", "<br/>")
        flowables.append(Paragraph(html, body_style))
    return flowables


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("output_path")
    parser.add_argument("--title")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text")
    group.add_argument("--text-file")
    args = parser.parse_args()

    if args.text_file:
        text_file = Path(args.text_file)
        if not text_file.exists():
            print(f"本文ファイルが見つかりません: {args.text_file}", file=sys.stderr)
            return 1
        try:
            body_text = text_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            body_text = text_file.read_text(encoding="cp932")
    else:
        body_text = args.text or ""

    if not body_text.strip() and not args.title:
        print("本文とタイトルが両方とも空です。", file=sys.stderr)
        return 1

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        # reportlabは title/author/subject 未指定時に "(anonymous)" 等の
        # プレースホルダーをメタデータへ埋め込むため、明示的に空にしておく。
        title=args.title or "",
        author="",
        subject="",
    )
    flowables = build_flowables(args.title, body_text)
    if not flowables:
        flowables = [Spacer(1, 1)]

    try:
        doc.build(flowables)
    except Exception as e:  # noqa: BLE001 - reportlab の生成失敗はすべてエラー扱いにする
        print(f"PDF生成に失敗しました: {e}", file=sys.stderr)
        return 1

    result = {"output_path": str(output_path), "size_bytes": output_path.stat().st_size}
    path_memory = register_output_path(output_path, description="create_pdfが生成したPDF")
    if path_memory:
        result["path_memory"] = path_memory
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
