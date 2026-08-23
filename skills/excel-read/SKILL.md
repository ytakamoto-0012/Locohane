---
name: excel-read
description: xlsx/xlsm/xlsファイルの読み込み専用スキル（シート一覧・セルデータの取得）。数式セルは数式文字列またはExcelが最後に計算した値のどちらかを選んで取得できる。xlsx/xlsmはセルの値に加えて書式（太字・背景色・罫線・結合セル・構造化テーブル）のstyle情報も常に取得できる。Excel本体は不要（openpyxlで直接読む）。ユーザーがExcelファイルの中身を確認・要約・検索したいとき、表データの値を読み取りたいとき、書き込んだ内容や書式が正しく反映されているか検証したいときに使う。VBAマクロコードを読みたい場合はexcel-vba-read、罫線・グラフ・レイアウトを画像として見たい場合はexcel-render、値の新規作成・編集はexcel-editを使う。
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

`script_args`は`[file_path, ...フラグ]`の順で1つの配列に並べる（`file_path`は必ず先頭、フラグの前後関係は自由）。

## 引数一覧

| 引数 | 必須/任意 | 値の型 | 既定値 | 説明 |
|---|---|---|---|---|
| `file_path`（位置引数、フラグ名なし） | 必須 | 文字列（絶対パス） | - | 読み込み対象の`.xlsx`/`.xlsm`/`.xls`ファイルパス。`script_args`配列の先頭要素として渡す |
| `--sheet` | 任意 | 文字列 | 省略＝シート一覧モード | シート名、または0始まりインデックス（例`"0"`＝先頭シート）。指定するとセルデータ取得モードになる |
| `--offset` | 任意 | 整数文字列 | `"0"` | 読み飛ばす行数（0始まり）。分割読み込み時に前回の`end_row`をそのまま次回`offset`に使う |
| `--limit` | 任意 | 整数文字列 | `"200"` | 読み込む最大行数。大きい表でも一度に全件読まず、`total_rows`を見ながら分割読み込みする |
| `--data-only` | 任意・値なしフラグ | - | 付けない＝数式文字列を返す | 数式セルを数式文字列ではなくExcelが最後に計算したキャッシュ値で返す。`.xlsx`/`.xlsm`のみ有効（`.xls`は常に値のみで無関係） |
| `--query-json` | 任意 | 文字列（JSON配列を1行化） | なし | 構造化クエリ（詳細は下記「構造化クエリ」節）。`.xlsx`/`.xlsm`のみ対応かつ`--sheet`必須（`.xls`指定・`--sheet`省略はエラー） |

`--sheet`を省略した場合はシート一覧のみを返す（大きいファイルを誤って全件読み込まないための既定動作）。それ以外の引数はすべて省略可。

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。未導入ライブラリは`ImportError`終了コード1（原因: `openpyxl`/`xlrd`）→該当する`pip install <パッケージ名>`をユーザーに促す。

このスクリプトは本文（シート一覧/rows）を標準出力に出さず、一時JSONへ書き出して`result_path`を返す。`Read`ツールで`result_path`（または`path_memory`の`@N`）を読む（`offset`/`limit`で分割読み込み可）。`@N`は出力JSONの`path_memory`に自動登録され、以降`run_script`の`script_args`には絶対パスの代わりに`@N`をそのまま渡せる。

## 手順

1. まず`--sheet`なしで実行しシート名・行数・列数を確認する。
2. `--sheet`にシート名（またはシート一覧内0始まりインデックス）を指定してセルデータ取得。
3. 行数が多いときは引数を使い分ける：
   - `--offset`/`--limit`: セル値を上から順に読みたいときに使う。1回あたり`--limit`行ずつ`rows`に返る。`total_rows`を見て続きが要るか判断し、次回`--offset`に前回の`end_row`を渡して続きから読む。
   - `--query-json`: 列の値ごとの行範囲（グルーピング列の範囲確認、`insert_rows`/`merge_cells`後の検証など）を知りたいときに使う（下記「構造化クエリ」節）。生の`rows`を目で数えて行範囲を手計算しない。`--offset`/`--limit`の指定値には影響されず、常にシート全体（1行目〜`total_rows`）が対象になる。`rows`本体が不要なら`--limit`は既定`200`のままでよい（`query_results`は`--limit`の値に関係なく全件返る）。
4. 数式セルは既定で数式文字列（`"=SUM(A1:A10)"`）を返す。Excelが最後に計算した値が欲しければ`--data-only`（xlsxのみ、`rows`・`query_results`両方の値表示に影響する）。数式を書き込んだ直後の最新値が欲しい場合は先にexcel-recalcスキルの`recalc_excel.py`を実行してから読み直す。
5. `.xlsx`/`.xlsm`は`--sheet`指定時、excel-editスキルの`edit_excel.py`で書いた書式（太字・背景色・結合・テーブル）も含めセル単位のstyle情報を常に返す（`read_only=False`でファイル全体を読むため`.xls`より低速）。`--query-json`の`query_results`にはstyle情報は含まれない（`rows`側のみ）。

## 出力

`result_path`（JSONファイル）のトップレベルキーはどちらのモードでも`path`（読み込んだファイルパス）と`mode`（`"sheets"`または`"rows"`）を含む。それ以外は`--sheet`の有無・拡張子（`.xlsx`/`.xlsm`か`.xls`か）・`--query-json`の組み合わせで変わる。

**シート一覧モード**（`--sheet`省略時）: `sheets`＝各要素`{"name","max_row","max_column"}`のリストのみ。

**セルデータモード**（`--sheet`指定時）共通キー: `sheet`（解決後のシート名）、`total_rows`/`total_columns`（シート全体の行数・列数）、`start_row`/`end_row`（今回`rows`に含まれる1始まり行番号の範囲。1行も返らなければ両方`null`。次回`--offset`にはこの`end_row`をそのまま渡せる）、`rows`（1行1配列のリスト、`--offset`/`--limit`の範囲のみ）。

- `.xls`のとき: `rows`のセル値は日付/時刻はISO8601文字列、空セルは`null`（style情報は非対応）。
- `.xlsx`/`.xlsm`のとき: `rows`の各セルが`{"value":..., "style":{...}}`。`value`は日付/時刻はISO8601文字列、空セルは`null`、数式セルは既定で数式文字列（例`"=SUM(A1:A10)"`）、`--data-only`を付けるとExcelが最後に計算したキャッシュ値（例`15`）に変わる。加えてトップレベルに`merged_cells`（シート全体、offset/limit範囲に関わらず全件）と`tables`（構造化テーブル一覧）が付く。`style`のキー体系:
```json
{"bold": true, "italic": false, "font_color": "0000FF", "font_size": 11,
 "fill_color": "FFFF00", "number_format": "#,##0.00",
 "align": "center", "valign": "center", "wrap_text": false,
 "border": "thin"}
```
既定値と一致する項目は省略、書式なしセルは`style`キー自体省略。
- `.xlsx`/`.xlsm`の追加フィールド:
  - `column_widths`: 返却範囲に含まれる各列の幅（`{"A": 12.3, "B": null, ...}`）。未設定列は`null`。
  - `row_heights`: 返却範囲（`start_row`〜`end_row`）の各行の高さ（`{"3": 15.0, ...}`）。未設定行は省略。
  - `warnings`: 構造的な不備の警告配列（該当なしならキー省略）。`wrap_text`が有効でないセルで、推定表示幅が列幅を超えている場合に文字切れの可能性を指摘。加えて、数式セル（`--data-only`未指定時のみ判定可能）の参照範囲が自身のセルを含む場合（例: `N8`セルの`=SUM(B8:N8)`）はExcelの循環参照エラーの可能性を指摘する（シート修飾された参照`Sheet2!A1:B2`は判定対象外）。
- `--query-json`ありのとき: 上記`rows`一式に加え、トップレベルに`query_results`が付く。**`query_results`は`--offset`/`--limit`の範囲に関わらずシート全体が対象**（詳細・例は次の「構造化クエリ」節）。`query_results`にはstyle情報は含まれない。

## 構造化クエリ（`--query-json`、必須ルール）

**`insert_rows`/`merge_cells`後の検証や、グルーピングされた列（月・区分など、結合セル化される列）の範囲確認では、生の`rows`を目で数えて行範囲を手計算しない。** 代わりに`--query-json`でクエリを渡すと、列の値ごとに連続する行範囲へグルーピングした結果を1回の呼び出しで返す。

```json
{"skill_name": "excel-read", "script_filename": "read_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--sheet", "月間予定表", "--query-json", "[{\"op\": \"group_by\", \"column\": \"A\"}]"]}
```
出力（`result_path`内、`query_results`キー）:
```json
"query_results": [
  {"op": "group_by", "column": "A",
   "items": [{"value": "4月", "start_row": 2, "end_row": 2, "row_count": 1},
             {"value": "5月", "start_row": 3, "end_row": 4, "row_count": 2}]}
]
```
- `group_by`の必須キー: `"op": "group_by"`と`"column"`（列アルファベット`"A"`または1始まりの列番号、例`"3"`＝C列）。`column`欠落はエラー＋終了コード1。
- 挙動: 指定列を**1行目から**`total_rows`まで上から走査し、非null値ごとに連続する行範囲（結合セルの非アンカー行・空欄継続行を含む）を`{value,start_row,end_row,row_count}`としてまとめる。
- **見出し行も除外されない。** 見出しセル（例`A1`の"月"）に値があれば、それ自体が`start_row:1,end_row:1`の独立した1件として`items`の先頭に混ざる。上記の出力例で`start_row`が`2`から始まっているのは見出しセルがたまたま空だったケースであり、一般には先頭itemが見出しかどうかを`start_row`で判定して除外する必要がある。
- `queries`は配列なので、`[{"op":"group_by","column":"A"}, {"op":"group_by","column":"C"}]`のように複数クエリを1回の呼び出しにまとめられる（列を変えて範囲確認したいときに個別呼び出しを繰り返さなくてよい）。
- `--sheet`必須（省略時・`.xls`拡張子ではエラー）。未対応のop名もエラー＋終了コード1（対応opの一覧をメッセージに含む）。
- excel-editスキルで`insert_row_group`を使う際の`anchor`（どの値の前/後に挿入するか）の確認にもそのまま使える。

### `list_images`（画像一覧）

```json
{"skill_name": "excel-read", "script_filename": "read_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--sheet", "Sheet1", "--query-json", "[{\"op\": \"list_images\"}]"]}
```
出力（`result_path`内、`query_results`キー）:
```json
"query_results": [
  {"op": "list_images",
   "items": [{"image_index": 0, "anchor_type": "absolute",
              "left_cm": 1.0, "top_cm": 1.0, "width_cm": 5.0, "height_cm": 3.0}]}
]
```
`image_index`はそのシート内での画像の0始まり通し番号（追加順）で、excel-editの
`set_image_position`の`image_index`と完全に一致する。`anchor_type`が`"absolute"`
以外（テンプレートに元から入っていた、セルに追従する配置の画像）の場合は
`left_cm`等が`null`になる（`set_image_position`で一度動かすと絶対座標に
置き換わり、以降は座標が取得できるようになる）。

### `list_charts`（グラフ一覧）

グラフの存在・タイトル・種類・位置を、画像化せず一覧確認できる。

```json
{"skill_name": "excel-read", "script_filename": "read_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--sheet", "Sheet1", "--query-json", "[{\"op\": \"list_charts\"}]"]}
```
出力（`result_path`内、`query_results`キー）:
```json
"query_results": [
  {"op": "list_charts",
   "items": [{"chart_index": 0, "type": "line", "title": "月別収支推移", "anchor": "F1"}]}
]
```
`chart_index`はそのシート内でのグラフの0始まり通し番号で、excel-editの
`update_chart`/`delete_chart`の`chart_index`と完全に一致する。`type`は
`line`/`bar`/`pie`/`scatter`のいずれか（対応外の種類はopenpyxlのクラス名を
そのまま返す）。`title`は未設定なら`null`。グラフが実在するか・タイトルが
意図通りかを確認したいだけなら、excel-renderで画像化するよりこちらが速い。

## エッジケース

ファイル不在／拡張子がxlsx・xlsm・xls以外／シート未検出／破損ファイルはエラー＋終了コード1。`.xls`はstyle情報・`--query-json`非対応（`--query-json`指定時のみエラー、style情報はそもそも`.xlsx`/`.xlsm`限定で自動的に付かない）。

## 見た目の確認について

罫線・書式・グラフ・レイアウトなど、セル値・style情報だけでは判断できない見た目の確認は、このスキルではなくexcel-renderスキル（`render_excel.py`＋`analyze_image`）で画像として確認すること。
