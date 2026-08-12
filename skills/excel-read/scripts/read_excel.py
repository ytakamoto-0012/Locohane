"""xlsx/xlsm/xls ファイルのシート一覧またはセルデータを読み込みJSONで出力する。

excel-tools スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script から
    python read_excel.py <file_path> [--sheet <シート名またはインデックス>]
                          [--offset N] [--limit N] [--data-only] [--include-style]
の形で呼ばれる。

拡張子でライブラリを切り替える:
- .xlsx / .xlsm -> openpyxl（既定は読み取り専用モード）
- .xls          -> xlrd（xlrd 2.x はレガシー .xls 専用。.xlsx は読めない。
                    --include-styleは非対応）

--sheet を省略した場合はシート名と行数・列数の一覧のみを返す（大きいファイルを
うっかり全件読み込まないための既定動作）。--sheet を指定した場合のみセル
データ本体を --offset/--limit の範囲で返す。

--include-style を付けると、書き込んだ太字・背景色・セル結合・構造化テーブル
などが意図通りかを読み返して検証できるようになる（既定では返らない）。この
場合セル単位の書式（apply_styleと同じキー体系）に加え、シート全体の
merged_cells/tablesも返すが、openpyxlのread_onlyモードが使えなくなるぶん
通常モードよりメモリ効率は落ちる。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from _common import cell_to_json, resolve_sheet_name, setup_utf8_stdio, summarize_result, write_json_result
from _style import extract_style

# group_by等の列グルーピングロジックは excel-edit スキル側の _excel_shared.py が唯一の実体
# （読み書き双方で同一ロジックを使う必要があり、複製すると読み込み結果と書き込み側の
# 挿入位置解決が食い違う恐れがあるため、複製せず隣接スキルフォルダから直接importする。
# excel-read は excel-edit が同梱されていることを前提とする）。
_EXCEL_EDIT_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "excel-edit" / "scripts"
if str(_EXCEL_EDIT_SCRIPTS) not in sys.path:
    sys.path.append(str(_EXCEL_EDIT_SCRIPTS))
from _excel_shared import group_column_values  # noqa: E402


def _query_group_by(ws, query: dict, max_row: int) -> dict:
    if "column" not in query:
        raise ValueError("group_byクエリには'column'が必須です")
    return {
        "op": "group_by",
        "column": query["column"],
        "items": group_column_values(ws, query["column"], max_row),
    }


_QUERY_HANDLERS = {
    "group_by": _query_group_by,
}


def _run_queries(ws, queries: list[dict], max_row: int) -> list[dict]:
    results = []
    for query in queries:
        op_name = query.get("op")
        handler = _QUERY_HANDLERS.get(op_name)
        if handler is None:
            raise ValueError(f"未対応のqueryです: {op_name!r}（対応op: {sorted(_QUERY_HANDLERS)}）")
        results.append(handler(ws, query, max_row))
    return results


def _cell_json(cell, include_style: bool) -> object:
    value = cell_to_json(cell.value)
    if not include_style:
        return value
    style = extract_style(cell)
    return {"value": value, "style": style} if style else {"value": value}


def _read_xlsx(
    path: Path,
    sheet_arg: str | None,
    offset: int,
    limit: int,
    data_only: bool,
    include_style: bool,
    queries: list[dict] | None = None,
) -> dict:
    import openpyxl

    if queries and sheet_arg is None:
        raise ValueError("--query-json は --sheet 指定時のみ使えます")

    wb = openpyxl.load_workbook(str(path), data_only=data_only, read_only=not include_style)
    try:
        names = wb.sheetnames
        if sheet_arg is None:
            sheets = [
                {"name": name, "max_row": wb[name].max_row or 0, "max_column": wb[name].max_column or 0}
                for name in names
            ]
            return {"path": str(path), "mode": "sheets", "sheets": sheets}

        resolved = resolve_sheet_name(names, sheet_arg)
        ws = wb[resolved]
        total_rows = ws.max_row or 0
        rows: list[list[object]] = []
        if total_rows:
            max_row = min(total_rows, offset + limit)
            if offset < max_row:
                for row in ws.iter_rows(min_row=offset + 1, max_row=max_row):
                    rows.append([_cell_json(cell, include_style) for cell in row])
        result = {
            "path": str(path),
            "mode": "rows",
            "sheet": resolved,
            "total_rows": total_rows,
            "total_columns": ws.max_column or 0,
            "start_row": offset + 1 if rows else None,
            "end_row": offset + len(rows) if rows else None,
            "rows": rows,
        }
        if include_style:
            result["merged_cells"] = [str(r) for r in ws.merged_cells.ranges]
            result["tables"] = [
                {
                    "name": t.name,
                    "range": t.ref,
                    "style": t.tableStyleInfo.name if t.tableStyleInfo else None,
                }
                for t in ws.tables.values()
            ]
        if queries:
            result["query_results"] = _run_queries(ws, queries, total_rows)
        return result
    finally:
        wb.close()


def _read_xls(path: Path, sheet_arg: str | None, offset: int, limit: int) -> dict:
    import xlrd

    book = xlrd.open_workbook(str(path))
    names = book.sheet_names()
    if sheet_arg is None:
        sheets = []
        for name in names:
            sh = book.sheet_by_name(name)
            sheets.append({"name": name, "max_row": sh.nrows, "max_column": sh.ncols})
        return {"path": str(path), "mode": "sheets", "sheets": sheets}

    resolved = resolve_sheet_name(names, sheet_arg)
    sh = book.sheet_by_name(resolved)
    total_rows = sh.nrows
    end = min(total_rows, offset + limit)
    rows: list[list[object]] = []
    for r in range(offset, end):
        row_values = []
        for c in range(sh.ncols):
            cell = sh.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                dt = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
                row_values.append(dt.isoformat())
            else:
                row_values.append(cell.value)
        rows.append(row_values)
    return {
        "path": str(path),
        "mode": "rows",
        "sheet": resolved,
        "total_rows": total_rows,
        "total_columns": sh.ncols,
        "start_row": offset + 1 if rows else None,
        "end_row": offset + len(rows) if rows else None,
        "rows": rows,
    }


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("file_path")
    parser.add_argument("--sheet", default=None, help="シート名または0始まりインデックス（省略時はシート一覧のみ返す）")
    parser.add_argument("--offset", type=int, default=0, help="読み飛ばす行数（0始まり、既定0）")
    parser.add_argument("--limit", type=int, default=200, help="読み込む最大行数（既定200）")
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="xlsx/xlsmで数式ではなく最後にExcelが計算したキャッシュ値を返す（xlsのみ影響なし）",
    )
    parser.add_argument(
        "--include-style",
        action="store_true",
        help="太字・背景色等の書式、セル結合、構造化テーブルも合わせて返す（.xlsx/.xlsmのみ対応。既定offで通常より読み込みが重くなる）",
    )
    parser.add_argument(
        "--query-json",
        default=None,
        help=(
            "構造化クエリの配列を1行JSON化した文字列（.xlsx/.xlsmのみ、--sheet必須）。"
            '例: \'[{"op": "group_by", "column": "A"}]\' '
            "→ 指定列を値ごとに連続する行範囲へグルーピングして{value,start_row,end_row,row_count}の"
            "配列で返す。insert_rows/merge_cells後の検証や、結合予定の列（月・区分等）の範囲確認を"
            "生のrowsを目で数えて手計算する代わりに使う。"
        ),
    )
    args = parser.parse_args()

    path = Path(args.file_path)
    if not path.is_file():
        print(f"ファイルが見つかりません: {args.file_path}", file=sys.stderr)
        return 1

    ext = path.suffix.lower()
    offset = max(args.offset, 0)
    limit = max(args.limit, 0)

    if ext == ".xls" and args.include_style:
        print("--include-style は .xlsx/.xlsm のみ対応です", file=sys.stderr)
        return 1
    if ext == ".xls" and args.query_json:
        print("--query-json は .xlsx/.xlsm のみ対応です", file=sys.stderr)
        return 1

    queries: list[dict] | None = None
    if args.query_json:
        try:
            queries = json.loads(args.query_json)
        except json.JSONDecodeError as e:
            print(f"--query-jsonのJSON解析に失敗しました: {e}", file=sys.stderr)
            return 1
        if not isinstance(queries, list):
            print("--query-jsonはJSON配列で指定してください", file=sys.stderr)
            return 1

    try:
        if ext in (".xlsx", ".xlsm"):
            result = _read_xlsx(path, args.sheet, offset, limit, args.data_only, args.include_style, queries)
        elif ext == ".xls":
            result = _read_xls(path, args.sheet, offset, limit)
        else:
            print(f"未対応の拡張子です（.xlsx/.xlsm/.xls のみ対応）: {ext}", file=sys.stderr)
            return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ImportError as e:
        print(f"必要なライブラリが見つかりません: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - 破損ファイル等、openpyxl/xlrdが送出する多様な例外を丸めて報告する
        print(f"読み込みに失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    summary = summarize_result(result, ["rows", "sheets"])
    summary.update(write_json_result(result, "excel_read", path))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
