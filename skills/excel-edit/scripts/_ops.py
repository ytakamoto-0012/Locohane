"""edit_excel.py が適用する「操作（op）」のディスパッチ実装。

各関数は (workbook, op_dict) を受け取り、openpyxl のワークブックへ副作用を
適用する。値を返さない（呼び出し側が例外の有無だけを見る）。

run_script からは直接実行されない。edit_excel.py から import して使う。
"""

from __future__ import annotations

import unicodedata

from openpyxl.chart import BarChart, LineChart, PieChart, Reference, ScatterChart
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule, FormulaRule, IconSetRule
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from _common import resolve_sheet_name
from _style import apply_style

_CHART_CLASSES = {
    "bar": BarChart,
    "line": LineChart,
    "pie": PieChart,
    "scatter": ScatterChart,
}


def _sheet(wb, name: str):
    resolved = resolve_sheet_name(wb.sheetnames, name)
    return wb[resolved]


def _reference(ws, range_str: str) -> Reference:
    min_col, min_row, max_col, max_row = range_boundaries(range_str)
    return Reference(ws, min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row)


def _display_width(value: object) -> float:
    """全角文字（東アジアのWide/Fullwidth/Ambiguous）を半角2文字分として数えた表示幅。

    日本語混じりの表で `len(str(value))` を使うと全角文字が半角と同じ1として
    数えられ、実際の見た目より大幅に狭い列幅になってしまうため。
    """
    if value is None:
        return 0.0
    width = 0.0
    for ch in str(value):
        width += 2.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1.0
    return width


def _col_width_from_rows(rows: list[list[object]], start_col: int) -> dict[str, float]:
    from openpyxl.utils import get_column_letter

    widths: dict[str, float] = {}
    for row in rows:
        for offset, value in enumerate(row):
            letter = get_column_letter(start_col + offset)
            widths[letter] = max(widths.get(letter, 0.0), _display_width(value))
    return widths


def _apply_col_widths(ws, widths: dict[str, float]) -> None:
    """計算した列幅を反映する。既存の列幅より狭くなる場合は反映しない（縮めない）。

    同一シートへ `set_range`/`format_table` を複数回呼んでも、後から呼んだ
    範囲が狭いという理由で先に確保した幅が失われないようにするため。
    """
    for letter, width in widths.items():
        target = min(max(width + 2, 8), 60)
        current = ws.column_dimensions[letter].width
        if current is None or target > current:
            ws.column_dimensions[letter].width = target


def op_add_sheet(wb, op: dict) -> None:
    name = str(op["name"])[:31]
    if name in wb.sheetnames:
        raise ValueError(
            f"シート'{name}'は既に存在します（rename_sheetで別名にするか、"
            "既存シートへの書き込みに切り替えてください）"
        )
    wb.create_sheet(title=name, index=op.get("index"))


def op_delete_sheet(wb, op: dict) -> None:
    wb.remove(_sheet(wb, op["name"]))


def op_rename_sheet(wb, op: dict) -> None:
    _sheet(wb, op["name"]).title = str(op["new_name"])[:31]


def op_set_cell(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    cell = ws[op["cell"]]
    cell.value = op.get("value")
    apply_style(cell, op.get("style"))


def op_set_range(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    min_col, min_row, _, _ = range_boundaries(f"{op['start_cell']}:{op['start_cell']}")
    rows = op.get("rows") or []
    style = op.get("style")
    header_style = op.get("header_style", style)
    row_styles = op.get("row_styles")
    if row_styles is not None and len(row_styles) != len(rows):
        raise ValueError(
            f"row_stylesの要素数({len(row_styles)})はrowsの行数({len(rows)})と"
            "一致させてください（見出し行分も含めて1要素=1行）"
        )
    for r_offset, row in enumerate(rows):
        if row_styles is not None:
            row_style = row_styles[r_offset]
        else:
            row_style = header_style if r_offset == 0 else style
        for c_offset, value in enumerate(row):
            cell = ws.cell(row=min_row + r_offset, column=min_col + c_offset, value=value)
            apply_style(cell, row_style)
    _apply_col_widths(ws, _col_width_from_rows(rows, min_col))

    format_table_opt = op.get("format_table")
    # header_style を渡した時点で「1行目は見出し」という意図が明示されているので、
    # 明示的に format_table=false でオプトアウトしない限り既定で美しい表に仕上げる。
    # LLM（特に指示追従力の弱い小型ローカルモデル）に新しいフラグを覚えさせず、
    # 既存の呼び出しパターン（header_styleを渡す）だけで綺麗な見た目になるようにするため。
    should_format = format_table_opt is not False and (format_table_opt or "header_style" in op)
    if should_format and rows:
        max_col = min_col + max(len(row) for row in rows) - 1
        max_row = min_row + len(rows) - 1
        opts = format_table_opt if isinstance(format_table_opt, dict) else {}
        _format_table_range(ws, min_row, max_row, min_col, max_col, opts)


def op_set_style(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    # ws[range] は単一セル指定（例"A1"）だとCellを、複数セル範囲だと
    # タプルのタプルを返し戻り値の型が揺れるため、range_boundariesで
    # 座標に正規化してcell()で個別に取得する（set_rangeと同じ方式）。
    min_col, min_row, max_col, max_row = range_boundaries(op["range"])
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            apply_style(ws.cell(row=row, column=col), op.get("style"))


def _format_table_range(ws, min_row: int, max_row: int, min_col: int, max_col: int, opts: dict) -> None:
    """指定範囲（1行目=見出し行）へ、配色・罫線・縞模様・見出し固定・列幅調整を
    一括で適用する。`op_format_table`（独立op）と `op_set_range` の
    `format_table` インラインオプションの両方から使う共通実装。

    本体行のスタイルには意図的に font 関連キーを含めない
    （`apply_style`はfont系キーが無ければcell.fontに触れないため、`role`規約等で
    既に当ててある文字色・太字を上書きしない）。
    """
    border_style = opts.get("border", "thin")
    banded = opts.get("banded", True)
    band_fill = opts.get("band_fill", "F2F2F2")
    header_fill = opts.get("header_fill", "1F4E78")

    # min_row の直前行が既に見出し色（header_fill）で塗られている場合、この
    # 呼び出しは新規テーブルの先頭ではなく、見出し行とデータ行を別々の
    # set_range 呼び出しに分けてしまった「既存テーブルへの追記」とみなし、
    # min_row を見出しとして再装飾しない。LLMが `header_style` を見出し行と
    # データ行の両方の呼び出しに付けてしまっても、データ側の先頭行が誤って
    # 見出し配色になる事故を防ぐため。
    prev_fill_rgb = str(ws.cell(row=min_row - 1, column=min_col).fill.fgColor.rgb or "") if min_row > 1 else ""
    is_continuation = prev_fill_rgb.upper().endswith(header_fill.upper())

    body_start = min_row
    if not is_continuation:
        header_style = {
            "bold": True,
            "font_color": opts.get("header_font_color", "FFFFFF"),
            "fill_color": header_fill,
            "align": "center",
            "valign": "center",
            "border": border_style,
        }
        for col in range(min_col, max_col + 1):
            apply_style(ws.cell(row=min_row, column=col), header_style)
        body_start = min_row + 1

    for row_idx, row in enumerate(range(body_start, max_row + 1)):
        body_style = {"border": border_style}
        if banded and row_idx % 2 == 1:
            body_style["fill_color"] = band_fill
        for col in range(min_col, max_col + 1):
            apply_style(ws.cell(row=row, column=col), body_style)

    if opts.get("freeze_header", True):
        ws.freeze_panes = ws.cell(row=body_start, column=min_col).coordinate

    if opts.get("autofit", True):
        rows_values = [
            [ws.cell(row=r, column=c).value for c in range(min_col, max_col + 1)]
            for r in range(min_row, max_row + 1)
        ]
        _apply_col_widths(ws, _col_width_from_rows(rows_values, min_col))


def op_format_table(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    min_col, min_row, max_col, max_row = range_boundaries(op["range"])
    _format_table_range(ws, min_row, max_row, min_col, max_col, op)


def op_insert_rows(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).insert_rows(op["index"], op.get("count", 1))


def op_delete_rows(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).delete_rows(op["index"], op.get("count", 1))


def op_insert_cols(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).insert_cols(op["index"], op.get("count", 1))


def op_delete_cols(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).delete_cols(op["index"], op.get("count", 1))


def op_set_column_width(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).column_dimensions[op["column"]].width = op["width"]


def op_merge_cells(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).merge_cells(op["range"])


def op_unmerge_cells(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).unmerge_cells(op["range"])


def op_add_table(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    tbl = Table(displayName=op["name"], ref=op["range"])
    tbl.tableStyleInfo = TableStyleInfo(
        name=op.get("style", "TableStyleMedium9"),
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=op.get("banded", True),
        showColumnStripes=False,
    )
    ws.add_table(tbl)


def op_update_table(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    tbl = ws.tables.get(op["name"])
    if tbl is None:
        raise KeyError(f"テーブルが見つかりません: {op['name']!r}")
    if op.get("range"):
        tbl.ref = op["range"]
    if op.get("style"):
        tbl.tableStyleInfo.name = op["style"]
    if "banded" in op:
        tbl.tableStyleInfo.showRowStripes = bool(op["banded"])


def op_remove_table(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    if op["name"] not in ws.tables:
        raise KeyError(f"テーブルが見つかりません: {op['name']!r}")
    del ws.tables[op["name"]]


def op_freeze_panes(wb, op: dict) -> None:
    _sheet(wb, op["sheet"]).freeze_panes = op["cell"]


def op_add_chart(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    chart_cls = _CHART_CLASSES.get(op["type"])
    if chart_cls is None:
        raise ValueError(f"未対応のグラフ種別です（bar/line/pie/scatterのみ対応）: {op['type']}")
    chart = chart_cls()
    if op.get("title"):
        chart.title = op["title"]
    data_ref = _reference(ws, op["data_range"])
    chart.add_data(data_ref, titles_from_data=op.get("titles_from_data", True))
    if op.get("categories_range"):
        chart.set_categories(_reference(ws, op["categories_range"]))
    ws.add_chart(chart, op["anchor"])


_CF_BUILDERS = {
    "color_scale": lambda p: ColorScaleRule(**p),
    "cell_is": lambda p: CellIsRule(**p),
    "formula": lambda p: FormulaRule(**p),
    "data_bar": lambda p: DataBarRule(**p),
    "icon_set": lambda p: IconSetRule(**p),
}


def op_add_conditional_format(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    builder = _CF_BUILDERS.get(op["rule_type"])
    if builder is None:
        raise ValueError(
            f"未対応のrule_typeです（color_scale/cell_is/formula/data_bar/icon_setのみ対応）: {op['rule_type']}"
        )
    try:
        rule = builder(op.get("params") or {})
    except TypeError as e:
        raise ValueError(f"params が rule_type={op['rule_type']!r} に対して不正です: {e}")
    ws.conditional_formatting.add(op["range"], rule)


def op_add_data_validation(wb, op: dict) -> None:
    ws = _sheet(wb, op["sheet"])
    dv = DataValidation(
        type=op["type"],
        formula1=op.get("formula1"),
        formula2=op.get("formula2"),
        allow_blank=op.get("allow_blank", True),
    )
    if op.get("prompt"):
        dv.prompt = op["prompt"]
        dv.promptTitle = op.get("prompt_title", "")
        dv.showInputMessage = True
    if op.get("error_message"):
        dv.error = op["error_message"]
        dv.errorTitle = op.get("error_title", "")
        dv.showErrorMessage = True
    ws.add_data_validation(dv)
    dv.add(op["range"])


OP_HANDLERS = {
    "add_sheet": op_add_sheet,
    "delete_sheet": op_delete_sheet,
    "rename_sheet": op_rename_sheet,
    "set_cell": op_set_cell,
    "set_range": op_set_range,
    "set_style": op_set_style,
    "format_table": op_format_table,
    "insert_rows": op_insert_rows,
    "delete_rows": op_delete_rows,
    "insert_cols": op_insert_cols,
    "delete_cols": op_delete_cols,
    "set_column_width": op_set_column_width,
    "merge_cells": op_merge_cells,
    "unmerge_cells": op_unmerge_cells,
    "add_table": op_add_table,
    "update_table": op_update_table,
    "remove_table": op_remove_table,
    "freeze_panes": op_freeze_panes,
    "add_chart": op_add_chart,
    "add_conditional_format": op_add_conditional_format,
    "add_data_validation": op_add_data_validation,
}


def apply_op(wb, op: dict) -> None:
    op_name = op.get("op")
    handler = OP_HANDLERS.get(op_name)
    if handler is None:
        raise ValueError(f"未対応のopです: {op_name!r}（対応op: {sorted(OP_HANDLERS)}）")
    handler(wb, op)
