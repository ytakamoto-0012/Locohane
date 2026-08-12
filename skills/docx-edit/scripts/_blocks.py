"""docxの「ブロック」（見出し・段落・箇条書き・表・画像・改ページ）を組み立てる共通ロジック。

create_docx.py（新規作成）と _ops.py（既存ファイル編集の append_block op）の
両方から import される。ブロックのJSON仕様は SKILL.md を参照。

run_script からは直接実行されない。
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_FONT = "游明朝"
HEADING_FONT = "游ゴシック"

# docx-create スキルの THEMES と同じ名前・同じ色（同一文書内でappend_blockした
# 内容とdocx-createで作った内容の見た目を揃えられるよう意図的に揃えている）。
THEMES = {
    "charcoal": {"primary": "36454F", "secondary": "F2F2F2", "accent": "212121", "text_on_primary": "FFFFFF"},
    "navy": {"primary": "1E2761", "secondary": "CADCFC", "accent": "0B1440", "text_on_primary": "FFFFFF"},
    "forest": {"primary": "2C5F2D", "secondary": "97BC62", "accent": "1C3D1D", "text_on_primary": "FFFFFF"},
    "coral": {"primary": "2F3C7E", "secondary": "F9E795", "accent": "F96167", "text_on_primary": "FFFFFF"},
    "terracotta": {"primary": "B85042", "secondary": "E7E8D1", "accent": "A7BEAE", "text_on_primary": "FFFFFF"},
    "ocean": {"primary": "065A82", "secondary": "1C7293", "accent": "21295C", "text_on_primary": "FFFFFF"},
    "teal": {"primary": "028090", "secondary": "00A896", "accent": "02C39A", "text_on_primary": "FFFFFF"},
    "berry": {"primary": "6D2E46", "secondary": "A26769", "accent": "ECE2D0", "text_on_primary": "FFFFFF"},
}
DEFAULT_THEME = "charcoal"


def resolve_theme(name: str | None) -> dict:
    key = (name or DEFAULT_THEME).strip().lower()
    if key not in THEMES:
        supported = ", ".join(sorted(THEMES))
        raise ValueError(f"未対応の theme です: {name!r}（対応: {supported}）")
    return THEMES[key]


def _set_cell_shading(cell, color_hex: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    tcPr.append(shd)


def _set_paragraph_shading(paragraph, color_hex: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    pPr.append(shd)


def _set_paragraph_left_border(paragraph, color_hex: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "24")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color_hex)
    pBdr.append(left)
    pPr.append(pBdr)


def _set_east_asian_font(font_obj, element, font_name: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    font_obj.name = font_name
    rPr = element.get_or_add_rPr() if hasattr(element, "get_or_add_rPr") else element
    r_fonts = rPr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        rPr.append(r_fonts)
    r_fonts.set(qn("w:eastAsia"), font_name)


def _apply_run_props(run, run_spec: dict) -> None:
    if run_spec.get("bold"):
        run.bold = True
    if run_spec.get("italic"):
        run.italic = True
    if run_spec.get("underline"):
        run.underline = True

    from docx.shared import Pt, RGBColor

    if run_spec.get("size_pt"):
        run.font.size = Pt(float(run_spec["size_pt"]))
    if run_spec.get("color"):
        run.font.color.rgb = RGBColor.from_string(str(run_spec["color"]).lstrip("#").upper())
    if run_spec.get("font"):
        _set_east_asian_font(run.font, run._element, str(run_spec["font"]))


def _add_heading(doc, block: dict, theme: dict):
    # 既存文書へのappend_blockでは、その文書自身のWordテーマ（組み込み
    # 見出しスタイルが参照する色）をそのまま尊重する。theme引数はここでは
    # 使わない（callout等、文書側に対応物が無い新規要素にのみ使う）。
    level = max(0, min(int(block.get("level", 1)), 9))
    doc.add_heading(str(block.get("text", "")), level=level)


def _add_paragraph(doc, block: dict, theme: dict):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    align_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    para = doc.add_paragraph()
    if block.get("alignment") in align_map:
        para.alignment = align_map[block["alignment"]]

    runs = block.get("runs")
    if runs:
        for run_spec in runs:
            run = para.add_run(str(run_spec.get("text", "")))
            _apply_run_props(run, run_spec)
    else:
        run = para.add_run(str(block.get("text", "")))
        _apply_run_props(run, block)


def _add_list(doc, block: dict, theme: dict, style_name: str):
    for item in block.get("items") or []:
        doc.add_paragraph(str(item), style=style_name)


def _add_table(doc, block: dict, theme: dict):
    # heading同様、theme引数は使わず太字のみ（既存文書に配色を押し付けない）。
    # 後から配色したい場合は set_table_style op を明示的に呼ぶ。
    rows = block.get("rows") or []
    if not rows:
        raise ValueError("table.rows が空です（少なくとも1行必要）")
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    header_row = bool(block.get("header_row"))
    for i, row in enumerate(rows):
        for j in range(n_cols):
            value = row[j] if j < len(row) else ""
            cell = table.cell(i, j)
            cell.text = "" if value is None else str(value)
            if header_row and i == 0:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.bold = True


def _add_image(doc, block: dict, theme: dict):
    from docx.shared import Cm

    image_path = Path(str(block.get("path", "")))
    if not image_path.is_file():
        raise ValueError(f"画像ファイルが見つかりません: {block.get('path')}")
    kwargs = {}
    if block.get("width_cm"):
        kwargs["width"] = Cm(float(block["width_cm"]))
    if block.get("height_cm"):
        kwargs["height"] = Cm(float(block["height_cm"]))
    doc.add_picture(str(image_path), **kwargs)


def _add_callout(doc, block: dict, theme: dict):
    """左に太いアクセント罫線＋薄い塗りの強調ボックス段落（docx-createのcalloutと同じ）。"""
    para = doc.add_paragraph()
    _set_paragraph_shading(para, theme["secondary"])
    _set_paragraph_left_border(para, theme["primary"])
    run = para.add_run(str(block.get("text", "")))
    _apply_run_props(run, block)


BLOCK_HANDLERS = {
    "heading": _add_heading,
    "paragraph": _add_paragraph,
    "bullet_list": lambda doc, b, theme: _add_list(doc, b, theme, "List Bullet"),
    "number_list": lambda doc, b, theme: _add_list(doc, b, theme, "List Number"),
    "table": _add_table,
    "image": _add_image,
    "callout": _add_callout,
    "page_break": lambda doc, b, theme: doc.add_page_break(),
}
