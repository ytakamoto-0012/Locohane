"""セマンティックHTMLからPDFファイルを生成する（日本語対応）。

pdf-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python create_pdf.py <output_path> [--title T] (--html HTML | --html-file PATH)
        [--accent-color RRGGBB] [--page-size a4|letter] [--orientation portrait|landscape]
        [--margin-cm N] [--header-text T] [--footer-text T] [--page-number]
の形で呼ばれる。

呼び出し側（LLM）が見出し(h1-h3)・段落(p)・表(table)・箇条書き(ul/ol)等の
セマンティックなHTMLを直接書き、CSSは本スクリプトが同梱テーマ
（見出し=ゴシック太字、本文=明朝、指定アクセント色）から組み立てる。
xhtml2pdf（内部はreportlab）を使うため、JS実行は行われず自由なコード実行の
リスクはない。

日本語はreportlab内蔵のCIDフォント（HeiseiMin-W3=明朝, HeiseiKakuGo-W5=ゴシック）
をCSSのfont-familyで直接指定して使う。これは外部フォントファイルの用意が不要な
反面、実際のグリフはPDFに埋め込まれず表示側（Acrobat等のビューア）が環境に応じた
CJKフォントで代替表示する（xhtml2pdfの@font-faceによる独自TTF埋め込みは検証の結果
確実に動作しないため採用していない）。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from xml.sax.saxutils import escape

from _common import register_output_path, setup_utf8_stdio

from xhtml2pdf import pisa

ACCENT_DEFAULT = "1F4E78"
PAGE_SIZES = {"a4", "letter"}
ORIENTATIONS = {"portrait", "landscape"}


def build_css(accent: str, page_size: str, orientation: str, margin_cm: float,
              header_text: str | None, footer_text: str | None, page_number: bool) -> str:
    frames = []
    if header_text:
        frames.append(f"""
        @frame header_frame {{
            -pdf-frame-content: headerContent;
            top: {margin_cm - 1.0 if margin_cm > 1.0 else 0.2}cm;
            margin-left: {margin_cm}cm; margin-right: {margin_cm}cm; height: 1cm;
        }}""")
    if footer_text or page_number:
        frames.append(f"""
        @frame footer_frame {{
            -pdf-frame-content: footerContent;
            bottom: {margin_cm - 1.2 if margin_cm > 1.2 else 0.2}cm;
            margin-left: {margin_cm}cm; margin-right: {margin_cm}cm; height: 1cm;
        }}""")
    frame_block = "".join(frames)

    return f"""
@page {{
    size: {page_size} {orientation};
    margin: {margin_cm}cm;
    {frame_block}
}}
body {{ font-family: HeiseiMin-W3; font-size: 10.5pt; line-height: 1.7; color: #1a1a1a; }}
h1, h2, h3, h4 {{ font-family: HeiseiKakuGo-W5; color: #{accent}; }}
h1 {{ font-size: 20pt; border-bottom: 2pt solid #{accent}; padding-bottom: 4pt; margin: 0 0 14pt 0; }}
h2 {{ font-size: 15pt; margin: 16pt 0 8pt 0; }}
h3 {{ font-size: 12.5pt; margin: 12pt 0 6pt 0; }}
h4 {{ font-size: 11pt; margin: 10pt 0 4pt 0; }}
p {{ margin: 0 0 8pt 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 8pt 0 12pt 0; }}
th {{ background-color: #{accent}; color: #FFFFFF; font-family: HeiseiKakuGo-W5; font-size: 9.5pt;
      padding: 5pt 8pt; border: 0.5pt solid #{accent}; text-align: left; }}
td {{ font-size: 9.5pt; padding: 5pt 8pt; border: 0.5pt solid #CCCCCC; }}
ul, ol {{ margin: 0 0 8pt 0; padding-left: 18pt; }}
li {{ margin-bottom: 3pt; }}
.callout {{ background-color: #EDF2F8; border-left: 3pt solid #{accent}; padding: 8pt 10pt; margin: 8pt 0; }}
hr {{ border: none; border-top: 0.5pt solid #CCCCCC; margin: 10pt 0; }}
.page-break {{ page-break-before: always; }}
img {{ max-width: 100%; }}
#headerContent {{ font-family: HeiseiKakuGo-W5; font-size: 8pt; color: #888888; }}
#footerContent {{ font-family: HeiseiKakuGo-W5; font-size: 8pt; color: #888888; text-align: center; }}
"""


def build_html(body_html: str, css: str, header_text: str | None, footer_text: str | None,
                page_number: bool, pdf_title: str | None) -> str:
    header_div = f'<div id="headerContent">{escape(header_text)}</div>' if header_text else ""
    footer_parts = []
    if footer_text:
        footer_parts.append(escape(footer_text))
    if page_number:
        footer_parts.append('<pdf:pagenumber /> / <pdf:pagecount />')
    footer_div = f'<div id="footerContent">{"　|　".join(footer_parts)}</div>' if footer_parts else ""
    title_tag = f"<title>{escape(pdf_title)}</title>" if pdf_title else ""

    return f"""<html><head>{title_tag}<style>{css}</style></head>
<body>
{header_div}
{footer_div}
{body_html}
</body></html>"""


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("output_path")
    parser.add_argument("--title")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--html")
    group.add_argument("--html-file")
    parser.add_argument("--accent-color", default=ACCENT_DEFAULT)
    parser.add_argument("--page-size", default="a4", choices=sorted(PAGE_SIZES))
    parser.add_argument("--orientation", default="portrait", choices=sorted(ORIENTATIONS))
    parser.add_argument("--margin-cm", type=float, default=2.0)
    parser.add_argument("--header-text")
    parser.add_argument("--footer-text")
    parser.add_argument("--page-number", action="store_true")
    args = parser.parse_args()

    if args.html_file:
        html_file = Path(args.html_file)
        if not html_file.exists():
            print(f"本文HTMLファイルが見つかりません: {args.html_file}", file=sys.stderr)
            return 1
        try:
            body_html = html_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            body_html = html_file.read_text(encoding="cp932")
    else:
        body_html = args.html or ""

    if not body_html.strip():
        print("本文HTMLが空です。", file=sys.stderr)
        return 1

    accent = args.accent_color.lstrip("#").upper()
    if len(accent) != 6 or any(c not in "0123456789ABCDEF" for c in accent):
        print(f"--accent-color はRRGGBB形式の16進数で指定してください: {args.accent_color}", file=sys.stderr)
        return 1

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    css = build_css(
        accent, args.page_size, args.orientation, args.margin_cm,
        args.header_text, args.footer_text, args.page_number,
    )
    full_html = build_html(
        body_html, css, args.header_text, args.footer_text, args.page_number, args.title,
    )

    try:
        with output_path.open("wb") as f:
            result = pisa.CreatePDF(full_html, dest=f)
        if result.err:
            print(f"PDF生成に失敗しました（xhtml2pdfエラー数: {result.err}）", file=sys.stderr)
            return 1
    except Exception as e:  # noqa: BLE001 - xhtml2pdfの生成失敗はすべてエラー扱いにする
        print(f"PDF生成に失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    result_json = {"output_path": str(output_path), "size_bytes": output_path.stat().st_size}
    path_memory = register_output_path(output_path, description="create_pdfが生成したPDF")
    if path_memory:
        result_json["path_memory"] = path_memory
    print(json.dumps(result_json, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
