---
name: excel-read
description: xlsx/xlsm/xlsファイルの読み込み専用スキル（シート一覧・セルデータの取得）。数式セルは数式文字列またはExcelが最後に計算した値のどちらかを選んで取得できる。既存xlsx/xlsmに書式（太字・背景色・罫線・結合セル・構造化テーブル）を検証したいときはセル単位のstyle情報も取得できる。Excel本体は不要（openpyxlで直接読む）。ユーザーがExcelファイルの中身を確認・要約・検索したいとき、表データの値を読み取りたいとき、書き込んだ内容や書式が正しく反映されているか検証したいときに使う。VBAマクロコードを読みたい場合はexcel-vba-read、罫線・グラフ・レイアウトを画像として見たい場合はexcel-render、値の新規作成・編集はexcel-editを使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-read

xlsx/xlsm/xls のシート一覧・セルデータを読み込み専用で取得するスキル。
`read_excel.py` を `run_script` で実行する。

## 呼び出し

```json
{"skill_name": "excel-read", "script_filename": "read_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--sheet", "Sheet1", "--offset", "0", "--limit", "200"]}
```
`--sheet`省略でシート一覧モード。`--sheet`/`--offset`/`--limit`（既定0/200）/`--data-only`/`--include-style`はすべて省略可（後2つは値なしフラグ）。

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。未導入ライブラリは`ImportError`終了コード1（原因: `openpyxl`/`xlrd`）→該当する`pip install <パッケージ名>`をユーザーに促す。

このスクリプトは本文（シート一覧/rows）を標準出力に出さず、一時JSONへ書き出して`result_path`を返す。`Read`ツールで`result_path`（または`path_memory`の`@N`）を読む（`offset`/`limit`で分割読み込み可）。`@N`は出力JSONの`path_memory`に自動登録され、以降`run_script`の`script_args`には絶対パスの代わりに`@N`をそのまま渡せる。

## 手順

1. まず`--sheet`なしで実行しシート名・行数・列数を確認する。
2. `--sheet`にシート名（またはシート一覧内0始まりインデックス）を指定してセルデータ取得。
3. 行数が多ければ`--offset`/`--limit`で分割読み込み。`total_rows`を見て続きが要るか判断する。
4. 数式セルは既定で数式文字列（`"=SUM(A1:A10)"`）を返す。Excelが最後に計算した値が欲しければ`--data-only`（xlsxのみ）。数式を書き込んだ直後の最新値が欲しい場合は先にexcel-recalcスキルの`recalc_excel.py`を実行してから読み直す。
5. excel-editスキルの`edit_excel.py`で書いた書式（太字・背景色・結合・テーブル）を検証したいときだけ`--include-style`を付ける（`read_only=False`でファイル全体を読むため既定より遅い。大きいファイルでは必要時のみ）。

## 出力

`result_path`の中身（`--include-style`なし）:
- シート一覧モード: `sheets`＝各要素`{"name","max_row","max_column"}`
- セルデータモード: `rows`＝1行1配列のリスト。日付/時刻はISO8601文字列、空セルは`null`

`result_path`の中身（`--include-style`あり）: `rows`の各セルが`{"value":..., "style":{...}}`。`style`のキー体系:
```json
{"bold": true, "italic": false, "font_color": "0000FF", "font_size": 11,
 "fill_color": "FFFF00", "number_format": "#,##0.00",
 "align": "center", "valign": "center", "wrap_text": false,
 "border": "thin"}
```
既定値と一致する項目は省略、書式なしセルは`style`キー自体省略。トップレベルに`merged_cells`（シート全体、offset/limit範囲に関わらず全件）と`tables`（構造化テーブル一覧）も付く。

## エッジケース

ファイル不在／拡張子がxlsx・xlsm・xls以外／シート未検出／破損ファイルはエラー＋終了コード1。`--include-style`は`.xls`非対応。

## 見た目の確認について

罫線・書式・グラフ・レイアウトなど、セル値・style情報だけでは判断できない見た目の確認は、このスキルではなくexcel-renderスキル（`render_excel.py`＋`analyze_image`）で画像として確認すること。
