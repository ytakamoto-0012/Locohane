"""style辞書（JSON）と openpyxl のスタイルオブジェクトを相互変換するヘルパー。

excel-tools スキルの edit_excel.py（書き込み: apply_style）と
read_excel.py（読み込み: extract_style）から import して使う。
run_script からは直接実行されない。

style辞書の形式（すべて省略可）:
{
  "bold": true, "italic": false,
  "font_color": "0000FF", "font_size": 11,
  "fill_color": "FFFF00",
  "number_format": "#,##0.00",
  "align": "center", "valign": "center", "wrap_text": false,
  "border": "thin",                      # または {"top": "thin", "bottom": "thin", ...}
  "role": "input"                        # font_color省略時のショートカット
}

role のショートカット（Anthropic公式スキルの財務モデル色分け規約に準拠）:
- "input"   -> 青 0000FF（ハードコードされた入力値）
- "formula" -> 黒 000000（同一シート内の数式）
- "link"    -> 緑 008000（他シートからの参照）
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

ROLE_COLORS = {
    "input": "0000FF",
    "formula": "000000",
    "link": "008000",
}

# Anthropic公式pptxスキルのDesign Ideas（配色パレット）に準拠した8テーマ。
# pptx-create スキルの THEMES と同じ名前・同じ色（クロススキルで統一した
# 見た目にできるよう意図的に揃えている）。primary=見出し・アクセント、
# secondary=補助色、accent=強調、text_on_primary=primaryを塗りに使うときの文字色。
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


def resolve_theme(name: str) -> dict:
    key = (name or "").strip().lower()
    if key not in THEMES:
        supported = ", ".join(sorted(THEMES))
        raise ValueError(f"未対応の theme です: {name!r}（対応: {supported}）")
    return THEMES[key]

_BORDER_SIDES = ("top", "bottom", "left", "right")


def build_font(style: dict) -> Font | None:
    if not any(k in style for k in ("bold", "italic", "font_color", "font_size", "role")):
        return None
    color = style.get("font_color") or ROLE_COLORS.get(style.get("role", ""))
    return Font(
        bold=bool(style.get("bold", False)),
        italic=bool(style.get("italic", False)),
        color=color,
        size=style.get("font_size"),
    )


def build_fill(style: dict) -> PatternFill | None:
    color = style.get("fill_color")
    if not color:
        return None
    return PatternFill(fill_type="solid", start_color=color, end_color=color)


def build_alignment(style: dict) -> Alignment | None:
    if not any(k in style for k in ("align", "valign", "wrap_text")):
        return None
    return Alignment(
        horizontal=style.get("align"),
        vertical=style.get("valign"),
        wrap_text=bool(style.get("wrap_text", False)),
    )


def build_border(style: dict) -> Border | None:
    spec = style.get("border")
    if not spec:
        return None
    if isinstance(spec, str):
        sides = {side: spec for side in _BORDER_SIDES}
    elif isinstance(spec, dict):
        sides = spec
    else:
        raise ValueError(f"border の指定が不正です: {spec!r}")
    return Border(**{side: Side(style=sides[side]) for side in sides if sides.get(side)})


def apply_style(cell, style: dict | None) -> None:
    """1セルに style 辞書の内容を適用する。"""
    if not style:
        return
    font = build_font(style)
    if font is not None:
        cell.font = font
    fill = build_fill(style)
    if fill is not None:
        cell.fill = fill
    alignment = build_alignment(style)
    if alignment is not None:
        cell.alignment = alignment
    border = build_border(style)
    if border is not None:
        cell.border = border
    if style.get("number_format"):
        cell.number_format = style["number_format"]


def _normalize_rgb(color) -> str | None:
    """openpyxlのColorオブジェクトからRRGGBB文字列を取り出す。

    ARGB（例 "00FFFF00"）は先頭2桁のアルファ成分を落とす。テーマ色や
    インデックスカラー等、単純なrgb文字列でない場合はNoneを返す。
    """
    if color is None:
        return None
    rgb = getattr(color, "rgb", None)
    if not isinstance(rgb, str):
        return None
    return rgb[-6:] if len(rgb) == 8 else rgb


def extract_style(cell) -> dict:
    """1セルの書式を、apply_style()が受け取るstyle辞書と同じキー体系で返す。

    既定値と一致するプロパティは省略し、実際に設定されている項目だけを返す。
    """
    style: dict = {}

    font = cell.font
    if font is not None:
        if font.bold:
            style["bold"] = True
        if font.italic:
            style["italic"] = True
        font_color = _normalize_rgb(font.color)
        if font_color and font_color != "000000":
            style["font_color"] = font_color
        if font.size and font.size != 11:
            style["font_size"] = font.size

    fill = cell.fill
    if fill is not None and fill.fill_type == "solid":
        fill_color = _normalize_rgb(fill.fgColor)
        if fill_color and fill_color != "FFFFFF":
            style["fill_color"] = fill_color

    alignment = cell.alignment
    if alignment is not None:
        if alignment.horizontal:
            style["align"] = alignment.horizontal
        if alignment.vertical:
            style["valign"] = alignment.vertical
        if alignment.wrap_text:
            style["wrap_text"] = True

    border = cell.border
    if border is not None:
        sides = {side: getattr(border, side).style for side in _BORDER_SIDES}
        sides = {side: v for side, v in sides.items() if v}
        if sides:
            values = set(sides.values())
            style["border"] = next(iter(values)) if len(values) == 1 and len(sides) == 4 else sides

    if cell.number_format and cell.number_format != "General":
        style["number_format"] = cell.number_format

    return style
