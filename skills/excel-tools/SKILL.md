---
name: excel-tools
description: xlsx/xls/xlsmファイルの読み込み、既存xlsx/xlsmの編集（セル書式・行列操作・グラフ・条件付き書式・データ検証を含む）、数式の再計算、xlsm/xlsのVBAマクロコードの読み込み・xlsmへのVBAマクロコードの追加/上書き/削除・マクロの実行、Excelページの画像化（OLE→PDF→PNG、余白自動除去）。ユーザーがExcelファイルの中身を確認したいとき、表データを新規に作成/編集したいとき、書式付きのレポートやグラフ入りのExcelを出力したいとき、数式の計算結果を確認したいとき、VBAマクロのコード（標準モジュール・クラスモジュール・Sub/Function）を見たい/書きたい/実行したいとき、Excelのレイアウトや罫線を確認したいときに使う。xlsxを扱う場面では、officecli-xlsxスキルが利用可能な場合は原則そちらを優先して使用し、本スキルはofficecliが利用できない場合のフォールバックとして使う。
license: MIT
metadata:
  author: ytakamoto
  version: "2.0"
---

# excel-tools

xlsx/xls/xlsm の読み込み・編集・数式再計算・VBAマクロコードの読み書き/実行を
行うスキルです。5つのスクリプトを `run_script` ツールで実行して結果を得ます。

- `read_excel.py` … 読み込み専用（シート一覧・セルデータ取得）
- `edit_excel.py` … 新規作成・既存編集の両方（セル書き込み・書式・行列操作・
  グラフ・条件付き書式・データ検証）
- `recalc_excel.py` … Excel本体をバックグラウンド起動して数式を再計算し、
  エラーセルを検出する
- `read_vba.py` … VBAマクロコードの読み込み専用（モジュール一覧・ソース取得）
- `edit_vba.py` … VBAマクロコードの追加・上書き・削除・実行

読み込みは `.xlsx`/`.xlsm` を `openpyxl`、レガシー形式の `.xls` を `xlrd` で
処理します。編集・生成は `.xlsx`/`.xlsm` のみ対応します（`.xls` は生成・
編集不可、読み込みのみ）。VBAマクロコードの読み込みは `.xlsm`/`.xls` に対応します
（`read_vba.py`、`oletools` 使用、Excel本体不要）。VBAマクロコードの追加・
上書き・削除・実行は `.xlsm` のみ対応します（`edit_vba.py`、Excel本体が必須。
詳細は後述のセクション参照）。

各スクリプトは正常系なら終了コード0でJSON1行を標準出力へ、異常系なら
終了コード非0でエラーメッセージを標準エラーへ出力します。

`read_excel.py`・`read_vba.py` は本文データ（シート一覧・セル行・VBAモジュール
一覧・VBAコード全文）を直接標準出力へは返さず、一時JSONファイルへ書き出して
そのパス（`result_path`）を返します。中身を確認するには `Read` ツールで
`result_path`（または `path_memory` の `@N`）を読んでください（内容は複数行に
整形されているため `offset`/`limit` で部分読み込みできます）。

このプロジェクトには汎用のファイル書き込みツールが無いため、構造化データ
（ops等）は **LLMが組み立てたJSON文字列をそのまま `run_script` の
`script_args` の1要素として渡す**ことでスクリプトへ伝えます
（`run_script` はシェルを介さず引数リストをそのまま子プロセスに渡すため、
引用符のエスケープを気にする必要はありません）。ユーザーが既存のJSON
ファイルを持っている場合は `--ops-file`/`--data-file` 系の引数でパス
指定も可能です。**opsの要素数が多い・見出しやセル値に日本語の長い文字列を
複数含む等で `--ops-json` に直接渡すJSON文字列が長くなる場合は、最初から
（構文エラーを待たず）`execute_python_code` で ops を Python の list/dict
として組み立てて `json.dump` で作業ディレクトリ配下の一時ファイル
（例: `ops.json`）へ書き出し、`--ops-file <そのファイルの絶対パス>` で
渡す方法を使うこと。目安として、opsの要素数が5個を超える、または
1つの文字列値に日本語が10文字を超えて含まれる場合はこちらを使う。
万一 `--ops-json` で構文エラー（引用符の閉じ忘れ等）が1回でも発生した
場合は、無理に1行のJSON文字列として組み立て直すことに固執せず、直ちに
`--ops-file` 方式へ切り替えること。** そちらのほうが構文エラーを
起こしにくい。

## 1. read_excel.py — シート一覧・セルデータの読み込み

呼び出し例（シート一覧を確認する場合、`--sheet` 省略）:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "read_excel.py",
    "script_args": ["C:\\Users\\me\\book.xlsx"]
}
```

呼び出し例（シートのセルデータを読む場合）:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "read_excel.py",
    "script_args": ["C:\\Users\\me\\book.xlsx", "--sheet", "Sheet1", "--offset", "0", "--limit", "200"]
}
```
`--sheet`/`--offset`/`--limit`/`--data-only`/`--include-style` は省略可
（`--data-only`/`--include-style` は値を取らないフラグ）。

### 手順（推奨フロー）

1. まず `--sheet` を付けずに実行し、シート名と行数・列数の一覧を確認する。
2. 読みたいシートが分かったら `--sheet` にシート名（またはシート一覧内での
   0始まりインデックス）を指定して実行し、セルデータを取得する。
3. シートの行数が多い場合は `--offset`/`--limit`（既定 offset=0, limit=200）
   で範囲を分けて読み進める。`total_rows` を見て続きが必要か判断する。
4. 数式が入ったセルは既定では数式文字列（例: `"=SUM(A1:A10)"`）が返る。
   Excelが最後に計算した値が欲しい場合は `--data-only` を付ける（xlsxのみ
   有効）。数式を新規に書き込んだ直後で最新の計算値が欲しい場合は、先に
   `recalc_excel.py` を実行してから読み直すこと。
5. `edit_excel.py` で書き込んだ太字・背景色・セル結合・構造化テーブルが
   意図通りかを検証したい場合は `--include-style` を付ける。既定では
   `--include-style` を付けないこと（後述の通り読み込みが重くなるため）。

### 出力例（シート一覧モード、`--sheet` 省略時）

```json
{"path": "C:\\foo\\book.xlsx", "mode": "sheets", "sheets_count": 3,
 "result_path": "C:\\...\\_tmp_<thread_id>\\excel_read\\1a2b3c4d_20260805_153012_123456.json"}
```
`sheets`（各要素 `{"name", "max_row", "max_column"}`）は標準出力からは省かれ、
`result_path` が指すJSONファイルにのみ含まれます。`Read` ツールで
`result_path` を読んでシート一覧を確認してください。

### 出力例（セルデータモード、`--sheet` 指定時）

```json
{"path": "C:\\foo\\book.xlsx", "mode": "rows", "sheet": "Sheet1", "total_rows": 120, "total_columns": 5, "start_row": 1, "end_row": 120, "rows_count": 120,
 "result_path": "C:\\...\\_tmp_<thread_id>\\excel_read\\1a2b3c4d_20260805_153012_123456.json"}
```

`rows`（1行=1配列（セル値の配列）のリスト。日付・時刻のセルはISO8601文字列
（例: `"2026-07-13T00:00:00"`）に変換済み、空セルは `null`）は標準出力からは
省かれ、`result_path` のJSONファイルにのみ含まれます。

### 出力例（`--include-style` 指定時）

```json
{"path": "C:\\foo\\book.xlsx", "mode": "rows", "sheet": "Sheet1", "total_rows": 3, "total_columns": 3, "start_row": 1, "end_row": 3, "rows_count": 3,
 "merged_cells": ["A1:B1"], "tables": [{"name": "MyTable", "range": "A1:C3", "style": "TableStyleMedium9"}],
 "result_path": "C:\\...\\_tmp_<thread_id>\\excel_read\\1a2b3c4d_20260805_153012_123456.json"}
```
`rows` の本体（各セルは `{"value": ..., "style": {...}}` の形。`style` は
`edit_excel.py` の `set_cell`/`set_range`等が受け取る `style` 辞書
（下記「style 共通スキーマ」参照）と同じキー体系。**書いた書式をそのまま
読み返して確認できる**ため、既定値と一致する項目（`bold: false`等）は
省略され、実際に設定されている項目だけが入る。書式が何も設定されて
いないセルは `style` キー自体が省略され `{"value": ...}` のみになる）。

トップレベルの `merged_cells` はシート全体のセル結合範囲（読み込んだ
`--offset`/`--limit`の範囲に関わらず全件）、`tables` はシート内の
構造化テーブル（`add_table`op等で作成したもの）の一覧。

`--include-style` は書式・セル結合・テーブル情報を取得するため
`read_only=False`（openpyxlの通常モード）でファイル全体を読み込む。
既定（`--include-style`なし）の `read_only=True` より遅くメモリも
多く使うため、大きいファイルでは必要な時だけ使うこと。

### エッジケース

- ファイルが存在しない、拡張子が `.xlsx`/`.xlsm`/`.xls` 以外、指定シートが
  見つからない、破損したファイル等はいずれもエラーメッセージ＋終了コード1。
  その内容をそのままユーザーに伝えること。
- `openpyxl`/`xlrd` が実行環境に無い場合は `ImportError` で終了コード1に
  なる。その場合は導入者へ `pip install openpyxl xlrd` の実施を促すこと。
- `--include-style` は `.xls` では非対応（エラーメッセージ＋終了コード1）。

## 2. edit_excel.py — 新規作成・既存編集

呼び出し例（既存ファイルへセルを書き込む場合）:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "edit_excel.py",
    "script_args": ["C:\\Users\\me\\book.xlsx", "--ops-json", "[{\"op\": \"set_cell\", \"sheet\": \"Sheet1\", \"cell\": \"A1\", \"value\": \"合計\"}]"]
}
```
`--ops-json` の値は ops 配列を1行のJSON文字列にしたものです（下記「opsの一覧」参照）。

- 新規ワークブックを作成したい場合は `script_args` の末尾に `"--new"` を追加する
  （既存ファイルがある場合は `"--overwrite"` も追加しないとエラーになる）。
- `--new` を付けない場合は対象パスを読み込んで編集する（存在しないと
  エラー）。編集で触れなかった既存のセル・書式・シートはそのまま保持
  される。
- `--output` を省略すると対象パスへ上書き保存する。別ファイルとして保存
  したい場合のみ `script_args` に `"--output", "<保存先パス>"` を追加する。
- `.xlsm` はマクロ（VBAプロジェクト）を保持したまま編集できる。マクロ自体の
  コードの読み込み・追加・変更・削除・実行は `read_vba.py`/`edit_vba.py`
  （下記セクション4・5参照）を使う。

### ops（操作）の一覧

`ops` は適用したい操作を順番に並べたJSON配列。各要素は `"op"` キーで
種別を判別する。1回の呼び出しに複数opをまとめて渡してよい（例: シート追加
→データ書き込み→書式→グラフ追加、を1コールで実行できる）。

| op | 必須パラメータ | 任意パラメータ | 説明 |
|---|---|---|---|
| `add_sheet` | `name` | `index` | シート追加。**同名シートが既に存在する場合はエラーになる**（openpyxlの自動リネーム（例: 「Sheet1」→「Sheet11」）に頼らず、明示的にエラーで気づけるようにするため。既存シートへ追記したい場合はそのシート名をそのまま`set_range`等で指定する） |
| `delete_sheet` | `name` | - | シート削除 |
| `rename_sheet` | `name`, `new_name` | - | シート名変更 |
| `set_cell` | `sheet`, `cell` | `value`, `style` | 単一セルへ値・書式を設定 |
| `set_range` | `sheet`, `start_cell`, `rows` | `style`, `header_style`, `row_styles`, `format_table` | 起点セルから複数行を一括書き込み。`header_style` は1行目のみに適用（見出し行用）。列幅は自動調整される（全角文字は2文字分としてカウントする表示幅ベース。同じシートへ複数回呼んでも既存の列幅より縮まない）。**`header_style` を渡すと、それだけで書き込んだ範囲全体に下記`format_table`op相当の仕上げ（見出し配色・罫線・縞模様・見出し行固定・列幅再調整）が自動適用される**（既定動作。`format_table: false` を追加すると無効化できる。逆に`header_style`を省略していても`format_table: true`（または`format_table`opと同じキーを持つオブジェクト）を渡せば仕上げを強制できる） |
| `set_style` | `sheet`, `range` | `style` | 値は変えずに既存セルへ書式のみ適用 |
| `format_table` | `sheet`, `range` | `header_fill`(既定`"1F4E78"`), `header_font_color`(既定`"FFFFFF"`), `band_fill`(既定`"F2F2F2"`), `banded`(既定true), `border`(既定`"thin"`), `freeze_header`(既定true), `autofit`(既定true) | 表の仕上げ用op。**既に書き込み済みの表**に対して後から使う。`range`の1行目を見出し行とみなし、見出し配色・全セル罫線・1行おきの縞模様・見出し行の下でのウィンドウ枠固定・列幅再調整を一括適用する。本体行のフォント色・太字は変更しない（`role`規約で当てた文字色を壊さないため）。新規に書き込みながら仕上げたいだけなら、独立opとして呼ぶより`set_range`の`format_table`オプションを使うほうが呼び出し回数が少なくて済む |
| `insert_rows` | `sheet`, `index` | `count`(既定1) | 行挿入（1始まり行番号の位置に挿入） |
| `delete_rows` | `sheet`, `index` | `count`(既定1) | 行削除 |
| `insert_cols` | `sheet`, `index` | `count`(既定1) | 列挿入（1始まり列番号） |
| `delete_cols` | `sheet`, `index` | `count`(既定1) | 列削除 |
| `set_column_width` | `sheet`, `column`, `width` | - | 列幅を手動指定（例: `column: "A"`） |
| `merge_cells` | `sheet`, `range` | - | セル結合（例: `range: "A1:C1"`） |
| `unmerge_cells` | `sheet`, `range` | - | セル結合の解除（例: `range: "A1:C1"`。結合されていない範囲を指定した場合はopenpyxlの例外がそのまま捕捉されエラー終了する） |
| `add_table` | `sheet`, `name`, `range` | `style`(既定`"TableStyleMedium9"`), `banded`(既定true) | Excelの構造化テーブル（フィルターボタン・構造化参照付き。`format_table`opの見た目だけの装飾とは別物）を作成する。`range`の1行目は既存のセル値がそのままヘッダー名になる。`name`はブック内で一意な英数字の識別子（`displayName`） |
| `update_table` | `sheet`, `name` | `range`, `style`, `banded` | 既存テーブルの範囲拡張・スタイル変更 |
| `remove_table` | `sheet`, `name` | - | テーブル定義の削除（セルの値・書式自体は残る。テーブルとしての構造化参照・フィルターボタンのみ解除） |
| `freeze_panes` | `sheet`, `cell` | - | ウィンドウ枠の固定（例: `cell: "A2"` で1行目を固定） |
| `add_chart` | `sheet`, `type`, `data_range`, `anchor` | `title`, `categories_range`, `titles_from_data`(既定true) | グラフ追加。`type` は `bar`/`line`/`pie`/`scatter` |
| `add_conditional_format` | `sheet`, `range`, `rule_type`, `params` | - | 条件付き書式。下記参照 |
| `add_data_validation` | `sheet`, `range`, `type` | `formula1`, `formula2`, `prompt`, `prompt_title`, `error_message`, `error_title`, `allow_blank`(既定true) | データ検証（入力規則）。下記参照 |

`sheet` はシート名または `read_excel.py` で得たシート一覧内の0始まり
インデックスで指定できる。

`rows`/`value` のセルは文字列・数値・真偽値・`null`（空セル）に対応する。
`"=SUM(B2:B3)"` のように `=` で始まる文字列はExcel上で数式として評価
される（このスクリプト自身は数式を評価しない。計算値を確認したい場合は
`recalc_excel.py` を使う）。

### style 共通スキーマ（`set_cell`/`set_range`/`set_style`/`header_style`で共通）

```json
{
  "bold": true, "italic": false,
  "font_color": "0000FF", "font_size": 11,
  "fill_color": "FFFF00",
  "number_format": "#,##0.00",
  "align": "center", "valign": "center", "wrap_text": false,
  "border": "thin",
  "role": "input"
}
```

すべて省略可。`font_color`/`fill_color` はRRGGBBの16進数（例: 赤 `FF0000`）。
`align` は `left`/`center`/`right`、`valign` は `top`/`center`/`bottom`。
`border` は文字列（`thin`/`medium`/`thick`。4辺すべてに適用）または
`{"top": "thin", "bottom": "thin"}` のように辺ごとの指定も可（辺キーは
`top`/`bottom`/`left`/`right`）。

**`role` によるセルの色分け規約（Anthropic公式スキルに準拠、推奨）**:
財務モデルやデータ集計シートを作る際、`font_color` を明示する代わりに
`role` を指定すると自動的に規約色になる。

- `role: "input"` → 青 `0000FF`（ハードコードした入力値のセル）
- `role: "formula"` → 黒 `000000`（同一シート内で完結する数式）
- `role: "link"` → 緑 `008000`（他シートを参照する数式）

表を作るときはこの規約に沿って `role` を使い分けると、後で人間が見ても
「どこが手入力でどこが計算か」が一目で分かるシートになる。

### 美しい表を作る基本レシピ（特別な操作は不要）

**表形式のデータ（レポート・一覧・予定表等）を書き込むときは、いつも通り
`set_range` に `header_style` を付けて呼ぶだけでよい。** それだけで見出し
配色・全セル罫線・1行おきの縞模様・見出し行固定・列幅調整が自動的に適用
される（`header_style`の中身は空オブジェクト`{}`でもよい。「1行目が見出し」
という合図として扱われる）。`fill_color`/`font_color`/`border` を自前で
1セルずつ組み立てる必要は無い（むしろ避けること。呼び出しが増える上、
表ごとに見た目がバラバラになりやすい）。ユーザーから配色を明確に指定された
場合のみ、その指定を`format_table`のオプション（`header_fill`等）に反映して
上書きする。

```json
[
  {"op": "set_range", "sheet": "Sheet1", "start_cell": "A1",
   "rows": [["月", "行事", "担当"], ["4月", "入学式", "田中"], ["5月", "遠足", "佐藤"]],
   "header_style": {}}
]
```

自動仕上げが不要な場合（見出し行のない生データの書き込み等）だけ
`"format_table": false` を付けて無効化する。

**見出し行とデータ行は同じ`set_range`呼び出しの`rows`内にまとめること
（見出し行だけを1回、データ行だけを別の`set_range`呼び出しに分割しない）。**
分割した場合でもデータ側の先頭行を誤って見出しとして再装飾しないよう
ガードは入っているが（直前行が見出し色なら装飾をスキップする）、
呼び出し回数が増えるだけで得るものがないため、上記の基本レシピ通り
1回にまとめるほうが確実で速い。

シートを跨いで複数の表を作る場合は、表ごとに個別の `set_range` 呼び出しを
行えばよい（1回のopで自動的にその範囲だけに適用されるため、表同士が
干渉することはない）。

### 行ごとに異なる背景色を付けたいとき（月ごとの区切り色、区分別の色分け等）

**書き込み後に行番号を目で数えて別途 `set_style` で塗り直すのは避けること。**
見出し行を含めるかどうかの数え間違いで1行分ズレる事故が起きやすい
（実例: `rows` の2要素目＝実データ1行目のつもりが、見出し行を1行分
多く数えてしまい、データ1行目に色が付かないまま2行目から色が付いた
状態になった）。

行ごとに異なる背景色・文字色を付けたい場合は、`set_range` の `rows` と
**同じ要素数**の `row_styles`（各要素は1行分のstyle辞書、色を付けない行は
`null`）を渡すこと。`rows` を組み立てる時点で対応するstyleを同じ配列内に
並べるため、後から行番号を数え直す必要が無くなりズレようがない。

```json
{"op": "set_range", "sheet": "月間予定表", "start_cell": "A2",
 "rows": [["月", "行事名"], ["1月", "役員選考"], ["1月", "新年会"], ["2月", "ひな祭り"]],
 "header_style": {"bold": true, "fill_color": "D9E2F3"},
 "row_styles": [null, {"fill_color": "DAEEF3"}, {"fill_color": "DAEEF3"}, {"fill_color": "F8CBAD"}]}
```

`row_styles` の1要素目は見出し行に対応する（`header_style` と両方渡した
場合は `row_styles` が優先される）。要素数が `rows` と一致しない場合は
エラーになる。

### 同じ値が続く列はセルを結合する（月・区分・期間などのグルーピング列）

年間予定表・週間予定表のように、1つの列（例:「月」列）で**同じ値が2行以上
連続する**表を作るときは、その値を行ごとに繰り返し書いたままにせず
`merge_cells` で結合すること。

1. `set_range` で表を書き込む（この時点では重複した値をそのまま書いて
   よい。例: A2〜A4すべてに「1月」と書く）。
2. 同じ値が連続している範囲だけを `merge_cells` で結合する。**結合すると
   左上セル以外の値は自動的に消える**ため、結合前に空欄にする必要は無い。
3. 結合したセルには `set_style` で `{"valign": "center"}` を当てると
   縦方向中央揃えになり見やすい。

```json
[
  {"op": "set_range", "sheet": "週間予定表", "start_cell": "A2",
   "rows": [["1月", "第1週"], ["1月", "第2週"], ["2月", "第1週"]],
   "header_style": {}},
  {"op": "merge_cells", "sheet": "週間予定表", "range": "A2:A3"},
  {"op": "set_style", "sheet": "週間予定表", "range": "A2:A3", "style": {"valign": "center"}}
]
```

1回の `merge_cells` は連続した矩形範囲しか結合できない。値のグループが
複数ある場合（上記例の「1月」と「2月」）はグループごとに
`merge_cells`＋`set_style` を1回ずつ呼ぶこと。

**既に書き込み済みの表**（`insert_rows`で追記した後など）に後から仕上げを
かけたい場合のみ、独立opの `format_table`（`sheet`, `range` 指定）を使う。

```json
[{"op": "format_table", "sheet": "Sheet1", "range": "A1:C3"}]
```

`role` 規約で入力セルに青文字などを当てている場合も、`format_table` は
本体行のフォント色を変更しないため、後から呼んでも壊れない
（呼び出し順序は「値・role色分け → `format_table`」のどちらが先でもよい）。

### 条件付き書式（`add_conditional_format`）

`rule_type` と `params` は openpyxl の `openpyxl.formatting.rule` の
コンストラクタ引数にそのまま渡される。

| rule_type | params例 |
|---|---|
| `color_scale` | `{"start_type": "min", "start_color": "FFFFFF", "end_type": "max", "end_color": "FF0000"}` |
| `cell_is` | `{"operator": "greaterThan", "formula": ["100"], "fill": null}`（`fill`はPatternFillオブジェクトが必要なため実質省略。色付けしたい場合は事前に`set_style`で該当セルへ書式を当てておく運用でもよい） |
| `formula` | `{"formula": ["$A1=\"NG\""]}` |
| `data_bar` | `{"start_type": "min", "end_type": "max", "color": "638EC6"}` |
| `icon_set` | `{"icon_style": "3TrafficLights1", "type": "percent"}` |

不正な `params` を渡すと `ops[N]の適用に失敗しました` として詳細な
Pythonの例外メッセージがそのまま返るので、それを手がかりに修正すること。

### データ検証（`add_data_validation`）

例: プルダウンリストを設定する場合

```json
{"op": "add_data_validation", "sheet": "Sheet1", "range": "C2:C100", "type": "list", "formula1": "\"OK,NG,保留\"", "prompt": "OK/NG/保留のいずれかを選択", "error_message": "リストにない値です"}
```

`type` は `list`/`whole`/`decimal`/`date`/`textLength`/`custom` に対応
（openpyxlの `DataValidation` の `type` 引数と同じ）。

### 手順

1. ユーザーの依頼内容から ops の配列を組み立てる（表データ投入は
   `set_range`、既存表への追記は `insert_rows`+`set_range`、表全体の見た目
   仕上げは `format_table`、個別セルの細かい調整は `set_style`、グラフが
   要る場合は `add_chart`、というように必要なopを組み合わせる）。
2. `run_script` を上記引数で呼び出す。
3. 既存ファイルを上書きしてよいか不明な場合（`--new`使用時に対象が既に
   存在する場合）は `--overwrite` を付けずに一度実行し、エラーが出たら
   ユーザーに上書き可否を確認する。
4. 成功時は `{"path": ..., "sheets": [...], "applied_ops": N}` が返るので、
   保存先パスをユーザーへ伝える。

### エッジケース

- 拡張子が `.xlsx`/`.xlsm` でない、`ops` がJSON配列でない、各opに `op`
  キーが無い、存在しないシートやopを指定した、`--new`なしでファイル不在、
  `--new`ありで`--overwrite`無しに既存ファイルを指定、などはいずれも
  エラーメッセージ＋終了コード1。どのop（何番目・どの種別）が失敗したか
  がメッセージに含まれるので、そのまま報告するかopを修正して再実行する。
- `openpyxl` が無い場合は `ImportError` で終了コード1。

## 3. recalc_excel.py — 数式の再計算・エラーセル検出

呼び出し例:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "recalc_excel.py",
    "script_args": ["C:\\Users\\me\\book.xlsx"]
}
```

`edit_excel.py` で書き込んだ数式はそのままでは評価されない（openpyxlは
数式を評価しない）ため、LLMが計算結果を確認したい場合はこのスクリプトを
実行してから `read_excel.py --data-only` で読み直す。ローカルに
インストール済みのMicrosoft Excelを画面に表示せず起動し、実際に
計算・上書き保存する。

### 出力例

```json
{"path": "C:\\foo\\book.xlsx", "recalculated": true, "errors": [{"sheet": "Sheet1", "cell": "B5", "value": "#DIV/0!"}]}
```

`errors` が空配列であれば数式エラーは無い。1件でもあればユーザーへ
「どのシートのどのセルがエラーか」を報告し、必要なら該当セルを
`edit_excel.py` の `set_cell` で修正する。

### 制約・注意点（実行前にユーザーへ伝えるべき情報）

- **ローカルにMicrosoft Excelがインストールされ、対話セッションから
  呼び出されている必要がある**（サーバーサービスとしての実行では動かない
  可能性が高い）。このプロジェクトはネイティブWindows環境前提のため通常は
  問題ないが、Excel未導入の環境ではこのスクリプトだけ使えない。
- 内部で実際にExcelプロセス（EXCEL.EXE）を一時的に起動する。処理完了時は
  必ず終了させるが、**万一スクリプトが強制終了された場合はEXCEL.EXEが
  残留する可能性がある**（その場合はタスクマネージャーで手動終了が必要な
  旨をユーザーに伝えること）。
- 大きなワークブックは計算に時間がかかり、`run_script` のタイムアウト
  （既定60秒、`config.ini` の `script_timeout`）を超える可能性がある。
  超過した場合はタイムアウトを増やすようユーザーに提案すること。
- インターネットからダウンロードした（Mark of the Web が付いた）ファイルは
  Excelが保護されたビューで開いてしまい正しく再計算できない場合がある。
  このスキルで生成・編集したファイルであれば通常は問題ない。

### エッジケース

- ファイル不在、拡張子が `.xlsx`/`.xlsm`/`.xls` 以外はエラーメッセージ＋
  終了コード1。
- `pywin32`（`win32com.client`）が実行環境に無い場合は `ImportError` で
  終了コード1。その場合は導入者へ `pip install pywin32` の実施を促すこと。
- Excel側のCOMエラー（未インストール、ライセンス未認証、ファイルが他
  プロセスで開かれている等）は "Excelでの再計算に失敗しました: ..." として
  終了コード1。エラーメッセージの内容をそのままユーザーに伝えること。

## 4. read_vba.py — VBAマクロコードの読み込み

呼び出し例（モジュール一覧を確認する場合、`--module` 省略）:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "read_vba.py",
    "script_args": ["C:\\Users\\me\\book.xlsm"]
}
```

呼び出し例（特定モジュールのソースコードを取得する場合）:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "read_vba.py",
    "script_args": ["C:\\Users\\me\\book.xlsm", "--module", "Module1"]
}
```

`oletools`（`olevba`）でファイルのバイト列から直接VBAソースコードを抽出する。
**Excel本体・COMを一切使わないため、Excel未インストールの環境でも動く**
（`edit_vba.py`とは対照的に前提条件が緩い）。

### 出力例（モジュール一覧モード）

```json
{"path": "C:\\foo\\book.xlsm", "has_vba": true, "modules_count": 2,
 "result_path": "C:\\...\\_tmp_<thread_id>\\excel_vba_read\\1a2b3c4d_20260805_153012_123456.json"}
```
`modules`（各要素 `{"name", "type", "line_count"}`）は標準出力からは省かれ、
`result_path` が指すJSONファイルにのみ含まれます。

### 出力例（`--module` 指定時）

```json
{"path": "C:\\foo\\book.xlsm", "module": "Module1", "type": "standard", "code_length": 45,
 "result_path": "C:\\...\\_tmp_<thread_id>\\excel_vba_read\\1a2b3c4d_20260805_153012_123456.json"}
```
ソースコード全文（`code`）は標準出力からは省かれ、`result_path` の
JSONファイルにのみ含まれます。`Read` ツールで読んでください。

### 手順

1. まず `--module` を付けずに実行し、モジュール名・種別・行数の一覧を確認する。
2. 読みたいモジュールが分かったら `--module` にモジュール名を指定してソース
   コード全文を取得する。
3. `edit_vba.py` でコードを書き換える前に、このスクリプトで既存コードを
   確認しておくこと（`set_code` は差分パッチではなく全文置換のため）。

### エッジケース

- 対象は `.xlsm`/`.xls`。`.xlsx`（マクロを保存できない拡張子）を指定した
  場合は即エラーになる。
- VBAプロジェクトが存在しないファイル（マクロなしで保存された`.xlsm`等）は
  エラーにせず `{"has_vba": false, "modules": []}` として正常終了する。
- モジュール種別（`standard`/`class`/`document`/`form`）は名前パターンや
  コード内容から推測する**簡易的なヒューリスティック**であり、完全な保証は
  ない。正確な種別が必要な場合はExcel上のVBE（Alt+F11）で確認するよう
  ユーザーに伝えること。
- `--module` に存在しないモジュール名を指定した場合はエラーメッセージ
  （存在するモジュール一覧を含む）＋終了コード1。
- `oletools` が実行環境に無い場合は `ImportError` で終了コード1。その場合は
  導入者へ `pip install oletools` の実施を促すこと。
- UserForm（フォームモジュール）は一覧に `type: "form"` として表示され
  読み込みは可能だが、`edit_vba.py`での編集対象外（後述）。

## 5. edit_vba.py — VBAマクロコードの追加・上書き・削除・実行

### VBAコード生成時の制約（絶対厳守）

**既存データ・ファイルを破壊するコードや悪意あるコードは、いかなる理由でも
生成してはならない。** 具体的には以下を含むコードを生成しないこと:

- ファイル・フォルダの削除（`Kill`、`RmDir`、`FileSystemObject.DeleteFile`/
  `DeleteFolder`等）
- 外部コマンド・外部プロセスの実行（`Shell`、`WScript.Shell`、
  `CreateObject("WScript.Shell")`経由の`Run`/`Exec`等）
- レジストリの読み書き、他アプリケーションの起動・操作、ネットワーク経由の
  ファイルダウンロード＆実行など、ワークブック自身の範囲を超えたシステムへの
  アクセス
- 上記のいずれか、またはユーザーの意図が不明瞭な破壊的操作をユーザーが依頼
  してきた場合は、目的を確認するか、代わりにExcel上での手動対応を提案する
  こと（このスキル経由での生成は行わない）。

`add_module`/`set_code`/`find_replace`/`replace_procedure`/`insert_code`は
上記のうち`Kill`/`RmDir`/`Shell`/`WScript.Shell`/`DeleteFile`/`DeleteFolder`
を含むコードを**技術的にも検出してエラーで拒否する**（`_vba_ops.py`の
`_check_dangerous_code`によるガード。完全な悪意判定はできないため最終的な
判断はLLM自身の責任だが、明らかに危険なAPI呼び出しは機械的にブロックする）。

**加えて、コードを書き込む直前に自動的に簡易構文チェックを行う。**
（`_lint_vba_syntax`。Sub/End Sub・If/End If・For/Next等のブロック構文の
対応関係を正規表現で検証）失敗した場合はファイルへの書き込み・保存を
一切行わずエラーを返す（メッセージ形式は下記「エッジケース」参照）。
これにより、ブロック構文が崩れたVBAコードが書き込まれてしまうことを
防ぐ。なお、VBAオブジェクトモデルには「コンパイル成否を返すAPI」が
存在せず、COM経由の実コンパイルチェック（ダミーSub実行、CommandBars
経由のCompile実行、保存後の再オープン等、複数方式を検証）はいずれも
実効性が確認できなかったため、未宣言変数の重複宣言・型の不一致・
キーワードのスペルミス等の意味的なコンパイルエラーはこのスキルでは
検出できない（このチェックが通っても構文的に完全に正しいとは限らない）。

**既存シート・セル・ブックを上書き/更新する処理がユーザーの正当な目的で
どうしても必要な場合**（例:「このマクロで集計結果を書き戻して」「実行の
たびに元データを更新したい」等）は、**上書き処理の直前に必ず現在の状態を
別名で保存するバックアップ処理を含めること**。上書き系の`Save`/`Range.Value`
書き込み等は`_check_dangerous_code`では検出されない（正当な用途が大多数の
ため機械的にはブロックしない）ので、コード生成時にLLMが自主的にこの
パターンに従う必要がある。例（タイムスタンプ付きファイル名でバックアップ
してから上書きする定型パターン）:

```vba
Sub UpdateData()
    Dim backupPath As String
    backupPath = ThisWorkbook.Path & "\" & _
        Left(ThisWorkbook.Name, InStrRev(ThisWorkbook.Name, ".") - 1) & _
        "_backup_" & Format(Now, "yyyymmdd_hhnnss") & ".xlsm"
    ThisWorkbook.SaveCopyAs backupPath

    ' ここから既存データを上書きする本処理
    ' 例: Sheets("集計").Range("A1").Value = 123
End Sub
```

`ThisWorkbook.SaveCopyAs`はブックを閉じずに現在の内容のコピーを別名保存でき、
実行中のマクロ自身の状態に影響しないためこの用途に適する。バックアップ先は
既定でブックと同じフォルダ（`ThisWorkbook.Path`）にすること（ユーザーから
別の保存先を明示された場合はそちらに従う）。

呼び出し例:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "edit_vba.py",
    "script_args": ["C:\\Users\\me\\book.xlsm", "--ops-json", "[{\"op\": \"set_code\", \"name\": \"Module1\", \"code\": \"Sub Foo()\\nEnd Sub\"}]"]
}
```

- 新規マクロ有効ブックを作成したい場合は `script_args` の末尾に `"--new"` を
  追加する（既存ファイルがある場合は `"--overwrite"` も追加しないとエラーに
  なる）。
- `--new` を付けない場合は対象パスを開いて編集する（存在しないとエラー）。
- `--output` を省略すると対象パスへ上書き保存する。別ファイルとして保存
  したい場合のみ `script_args` に `"--output", "<保存先パス>"` を追加する
  （出力先の拡張子は必ず `.xlsm`）。
- VBAコードは複数行の文字列になりやすいため、`--ops-json` に直接埋め込むより
  最初から `--ops-file`（冒頭の「opsの要素数が多い・長い文字列を含む場合」の
  ガイダンス参照）を使うことを基本にすること。

### 前提条件（実行前に必ずユーザーへ伝えること）

- **ローカルにMicrosoft Excelがインストールされ、対話セッションから
  呼び出されている必要がある**（`recalc_excel.py`と同じ制約。サーバー
  サービスとしての実行では動かない可能性が高い）。
- **Excelのトラストセンターで「VBA プロジェクト オブジェクト モデルへの
  アクセスを信頼する」が有効になっている必要がある**（既定は無効）。この
  設定はセキュリティ上の理由からプログラムから自動的に有効化できない。
  未設定の場合は `workbook.VBProject` へのアクセス自体がエラーになり、
  下記の設定手順を含むメッセージが返るので、その手順をそのままユーザーに
  案内すること。
  設定手順: Excelを開く → ファイル → オプション → トラストセンター →
  「トラストセンターの設定」→「マクロの設定」→
  「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」にチェック。
- 対象は `.xlsm` のみ（`.xls`・`.xlsx`は非対応）。
- **UserForm（ユーザーフォーム）の作成・編集は対象外**（標準モジュール・
  クラスモジュール・ドキュメントモジュール（`ThisWorkbook`/シートモジュール）
  のコードのみ対応）。
- `run_macro` を含む呼び出しのみ、Excel側でマクロの実行を許可する設定
  （`msoAutomationSecurityLow`）で開く。それ以外（`add_module`/`set_code`/
  `delete_module`のみ）の場合はマクロの自動実行を無効化した状態
  （`msoAutomationSecurityForceDisable`）で操作するため、`Workbook_Open`等の
  既存の自動マクロが編集中に意図せず実行されることはない。

### ops（操作）の一覧

| op | 必須パラメータ | 任意パラメータ | 説明 |
|---|---|---|---|
| `add_module` | `name`, `code` | `type`(既定`"standard"`。`"standard"`/`"class"`) | 標準モジュール・クラスモジュールを新規追加。同名モジュールが既に存在する場合はエラー |
| `set_code` | `name`, `code` | - | 既存モジュール（`ThisWorkbook`等ドキュメントモジュール含む）のコードを**全文置換**する |
| `find_replace` | `name`, `old_code`, `new_code` | - | モジュール内の一部コード（`old_code`）を`new_code`に置換する（差分パッチ）。`old_code`が一意に一致しない（0件・複数件）場合はエラー |
| `replace_procedure` | `name`, `procedure`, `code` | `kind`(`"sub"`/`"property_get"`/`"property_let"`/`"property_set"`。省略時は自動判別) | 指定したSub/Function/Propertyプロシージャ1つだけを丸ごと置き換える（他の部分には触れない） |
| `insert_code` | `name`, `code` | `position`(既定`"end"`。`"end"`/`"start"`/行番号) | 既存コードには触れずに新しいコード（通常は新規のSub/Function）を追加する |
| `delete_module` | `name` | - | モジュール削除。ドキュメントモジュール（`ThisWorkbook`/シートモジュール）は削除不可でエラーになる |
| `run_macro` | `name` | `args`(配列) | 指定したマクロ（Sub/Function）を実行する。戻り値があれば`results`配列に入って返る |

**`set_code`は必ずモジュール全文を渡す必要がある。** モジュールが長い場合、
全文を渡す方式は低パラメータのLLMにとって「一部だけ書き換えるつもりが
無関係な部分を欠落・改変してしまう」事故が起きやすい。**既存モジュールの
一部だけを変更したい場合は、必ず以下のいずれかの差分系opを優先して使うこと**
（`set_code`は「モジュールをまるごと新しい内容に総入れ替えしたい」場合のみに限る）。

- 数行〜1つの式・条件式だけを直したい → `find_replace`
  （`old_code`は`read_vba.py`で取得した実際のコードから一意に特定できる範囲を
  そのままコピーする。改行コード(CRLF/LF)の違いは自動で吸収される）
- 1つのSub/Function/Propertyの中身をまるごと書き直したい → `replace_procedure`
  （プロシージャの外側にある他のコードは一切渡さなくてよい）
- 既存コードは変えず、新しいSub/Functionを追加したい → `insert_code`
  （既存コードを一切やり取りしなくてよい）

### 出力例

```json
{"path": "C:\\foo\\book.xlsm", "applied_ops": 2, "results": [3]}
```

`results` には戻り値のあるop（`run_macro`で戻り値ありのFunctionを実行した場合
等）の結果のみ、発生順に入る（`add_module`/`set_code`/`delete_module`は
戻り値を持たないため`results`には現れない）。

### 手順

1. VBAコードを書く前に `read_vba.py` でモジュール一覧・既存コードを確認する。
2. 既存モジュールへの変更は、上記の通り基本的に `find_replace`/
   `replace_procedure`/`insert_code` のいずれかを使う（`set_code`はモジュール
   全体を新規に書き直す場合のみ使う）。
3. `run_script` を上記引数で呼び出す。
4. 既存ファイルを上書きしてよいか不明な場合（`--new`使用時に対象が既に
   存在する場合）は `--overwrite` を付けずに一度実行し、エラーが出たら
   ユーザーに上書き可否を確認する。

### エッジケース

- 拡張子が`.xlsm`でない、保存先の拡張子が`.xlsm`でない、`ops`がJSON配列で
  ない、各opに`op`キーが無い、存在しないモジュールを指定した、UserForm
  （フォームモジュール）を`add_module`の`type`に指定した、UserFormを
  `set_code`/`find_replace`/`replace_procedure`/`insert_code`の対象にした、
  ドキュメントモジュールを`delete_module`しようとした、`add_module`で既存
  モジュール名を指定した、`find_replace`の`old_code`がモジュール内に無い・
  複数箇所に一致した、`replace_procedure`の`procedure`が見つからない、などは
  いずれもエラーメッセージ＋終了コード1。どのop（何番目・どの種別）が
  失敗したかがメッセージに含まれるので、そのまま報告するかopを修正して
  再実行する。
- `replace_procedure`はCOMの`ProcStartLine`の仕様上、対象プロシージャの
  直前にある空行・コメント行も置き換え範囲に含まれる場合がある（軽微な
  整形上の副作用であり、コードの意味には影響しない）。
- `add_module`/`set_code`/`find_replace`/`replace_procedure`/`insert_code`に
  渡した`code`/`new_code`に`Kill`/`RmDir`/`Shell`/`WScript.Shell`/
  `DeleteFile`/`DeleteFolder`等の危険なAPI呼び出しが含まれる場合は
  「危険なVBA API（...）が含まれているため、このコードは書き込めません」
  としてエラー＋終了コード1になる（上記「VBAコード生成時の制約」参照）。
  このエラーが出た場合はコードを修正して再送するのではなく、まずユーザーに
  目的を確認すること。
- 書き込み直前の簡易構文チェックでSub/End Sub・If/End If・For/Next等の
  ブロック構文の対応崩れを検出した場合は「VBAコードの構文が不正な可能性が
  あります（...）」としてエラー＋終了コード1になる。取りこぼし（`#If`等の
  条件付きコンパイル、1行に複数ステートメントを並べるコロン複文、未宣言変数の
  重複宣言・型の不一致等の意味的エラー）はあるが、正しいコードを誤って拒否
  する方向のリスクは低い設計になっている。メッセージに従ってコードを見直し、
  再送すること。**このチェックが通っても意味的なコンパイルエラー（変数の
  重複宣言等）が残っている可能性があり、それはこのスキルでは検出できない。**
- トラストセンター未設定時のエラーメッセージは、上記「前提条件」の設定
  手順をそのままユーザーに案内すること。
- `pywin32`が実行環境に無い場合は`ImportError`で終了コード1。その場合は
  導入者へ`pip install pywin32`の実施を促すこと。
- Excel側のCOMエラー（未インストール、ファイルが他プロセスで開かれている
  等）は"VBAの編集に失敗しました: ..."として終了コード1。エラーメッセージの
  内容をそのままユーザーに伝えること。
- 万一スクリプトが強制終了された場合、`recalc_excel.py`と同様にEXCEL.EXE
  プロセスが残留する可能性がある（タスクマネージャーでの手動終了が必要）。

## 4. render_excel.py — Excelページを画像化してLLMに見せる

表の罫線・書式・グラフ・レイアウトを確認したいとき、`read_excel.py` で
数値だけ取れても「どのように表示されているか」を知りたいときに使います。

呼び出し例:
```json
{
    "skill_name": "excel-tools",
    "script_filename": "render_excel.py",
    "script_args": ["C:\\Users\\me\\book.xlsx", "--start-page", "1", "--max-pages", "3"]
}
```
`--start-page`/`--max-pages`（既定3、最大5にクランプ）/`--dpi`（既定300、
72〜600にクランプ）/`--no-crop`（余白除去をオフ）は省略可。

出力例:
```json
{"path": "C:\\foo\\book.xlsx", "tool": "excel", "total_pages": 5, "start_page": 1, "end_page": 3, "dpi": 300, "target_dpi": 150, "crop_applied": true,
 "images": [
   {"page": 1, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p1.png", "original_dpi": 300, "cropped": true},
   {"page": 2, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p2.png", "original_dpi": 300, "cropped": true},
   {"page": 3, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p3.png", "original_dpi": 300, "cropped": true}
 ]}
```

**重要（2段階手順）**: このスクリプト自体はPNGファイルを保存してパスをJSONで
返すだけで、LLMへ画像を見せるところまでは行いません。`images` の各要素の
`image_path`（絶対パス）を、続けて `analyze_image` ツール（このスキル専用ではなく
共通ツール）の `relative_path` 引数にそのまま渡して呼び出してください。
1回の `analyze_image` 呼び出しで1ページ分が見えるので、複数ページある場合は
ページ数分 `analyze_image` を呼びます。

**余白除去について**: 既定では白黒境界判定で余白を自動除去します。特にExcelは
余白が大きい場合が多いので、このオプションを付けることでコンテンツ領域の
解像度が高まり、文字が読みやすくなります。`--no-crop` を付けると余白除去を
オフにし、元のサイズのまま画像化します。

エッジケース:
- ファイル不在・ディレクトリ指定・壊れたファイル/Excel未インストールはエラー終了します。
- `start_page` が総ページ数を超える場合はエラーにはならず、`images: []` を
  終了コード0で返します。
- `max_pages` は5にクランプされます。総ページ数が多い文書を広く画像化したい
  場合は `--start-page` を変えて複数回に分けて呼び出してください。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/rendered/`）に保存されます。同一ファイルの再実行時は
  上書きされ、会話終了時に自動的に削除されます。
- 依存パッケージ `pywin32`・`pypdfium2`・`pillow` が実行環境に無い場合は
  `ImportError` で終了コード非0になります。

## パスメモリー（`@N`）

`edit_excel.py`・`edit_vba.py`・`recalc_excel.py` が生成・更新・再計算保存
したファイルは、出力JSONに `path_memory`（例: `{"@12": "C:\\foo\\book.xlsx"}`）
として自動登録されます。続けて `run_script` を呼ぶ場合、絶対パスの代わりに
その `@N` を `script_args` にそのまま渡せます（自動的に実パスへ解決
されます）。`read_excel.py`・`read_vba.py` が書き出す結果JSON
（`result_path`）も同様に自動登録されます。
