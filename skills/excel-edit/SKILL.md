---
name: excel-edit
description: xlsx/xlsmファイルの新規作成・既存編集を行うスキル。セル値・書式（フォント/背景色/罫線/列幅/結合）の設定、行列の挿入削除、グラフ追加、条件付き書式、データ検証、構造化テーブルに対応。Excel本体は不要（openpyxlで直接書き込む）。数式文字列は書き込めるが計算はしない（計算結果の確認・エラーセル検出はexcel-recalcスキルを使う）。ユーザーが表データを新規作成/編集したいとき、書式付きのレポートやグラフ入りのExcelを出力したいとき、既存のxlsx/xlsmにデータや装飾を追記・修正したいときに使う。読み込み専用ならexcel-read、VBAマクロの読み書きはexcel-vba-read/excel-vba-edit、見た目の画像確認はexcel-renderを使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-edit

xlsx/xlsm の新規作成・既存編集を行うスキル。`edit_excel.py` を `run_script` で実行する。

## 呼び出し

```json
{"skill_name": "excel-edit", "script_filename": "edit_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--ops-json", "[{\"op\": \"set_cell\", \"sheet\": \"Sheet1\", \"cell\": \"A1\", \"value\": \"合計\"}]"]}
```
- 新規作成: `script_args`末尾に`"--new"`（既存ファイルがあれば`"--overwrite"`も必須、無いとエラー）。
- `--new`なし: 対象パスを読み込んで編集（不在ならエラー）。触れなかった既存セル・書式・シートはそのまま保持。
- `--output`省略で対象パスへ上書き保存。別ファイル保存時のみ`"--output", "<絶対パス>"`を追加。
- `.xlsm`はマクロを保持したまま編集可。マクロ自体の読み書きはexcel-vba-read/excel-vba-editスキル。

## 入出力の型とopsの渡し方

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。未導入ライブラリは`ImportError`終了コード1（原因: `openpyxl`）→該当する`pip install <パッケージ名>`をユーザーに促す。

opsは通常`--ops-json '<ops配列を1行JSON化した文字列>'`で渡す（`run_script`はシェルを介さず引数をそのまま子プロセスへ渡すため引用符エスケープは不要）。次のいずれかに該当する場合は、最初から（構文エラーを待たず）`execute_python_code`でopsをlist/dictとして組み立て`json.dump`で作業ディレクトリ配下の一時ファイルへ書き出し、`--ops-file <絶対パス>`で渡す：①ops要素数が5個超、②1つの文字列値に日本語10文字超を含む。`--ops-json`で構文エラー（引用符の閉じ忘れ等）が1回でも出たら、直そうとせず即座に`--ops-file`方式へ切り替える。

生成・更新したファイルは出力JSONの`path_memory`（例`{"@12": "C:\\foo\\book.xlsx"}`）に自動登録される。以降`run_script`の`script_args`には絶対パスの代わりに`@N`をそのまま渡せる。

## opsの一覧

| op | 必須 | 任意 | 説明 |
|---|---|---|---|
| `add_sheet` | `name` | `index` | シート追加。同名シートが既存ならエラー（自動リネームに頼らず明示エラーにするため。既存シートへ追記したいなら`set_range`等にそのシート名を指定） |
| `delete_sheet` | `name` | - | シート削除 |
| `rename_sheet` | `name`,`new_name` | - | シート名変更 |
| `set_cell` | `sheet`,`cell` | `value`,`style` | 単一セルへ値・書式設定 |
| `set_range` | `sheet`,`start_cell`,`rows` | `style`,`header_style`,`row_styles`,`format_table` | 起点セルから複数行を一括書込。`header_style`は1行目のみ適用。列幅は自動調整（全角=2文字換算、既存幅より縮まない）。**`header_style`を渡すと自動的に`format_table`相当（見出し配色・罫線・縞模様・見出し行固定・列幅調整）が既定で適用される**（`format_table:false`で無効化可。逆に`header_style`省略でも`format_table:true`で強制適用可） |
| `set_style` | `sheet`,`range` | `style` | 値は変えず既存セルへ書式のみ適用 |
| `format_table` | `sheet`,`range` | `header_fill`(既定`1F4E78`),`header_font_color`(既定`FFFFFF`),`band_fill`(既定`F2F2F2`),`banded`(既定true),`border`(既定`thin`),`freeze_header`(既定true),`autofit`(既定true) | 書き込み済みの表を後から仕上げる。`range`の1行目を見出しとみなす。本体行のフォント色・太字は変更しない（`role`色分けを壊さないため） |
| `insert_rows` | `sheet`,`index` | `count`(既定1) | 行挿入（1始まり位置） |
| `delete_rows` | `sheet`,`index` | `count`(既定1) | 行削除 |
| `insert_cols` | `sheet`,`index` | `count`(既定1) | 列挿入（1始まり） |
| `delete_cols` | `sheet`,`index` | `count`(既定1) | 列削除 |
| `set_column_width` | `sheet`,`column`,`width` | - | 列幅を手動指定 |
| `merge_cells` | `sheet`,`range` | - | セル結合（例`"A1:C1"`） |
| `unmerge_cells` | `sheet`,`range` | - | 結合解除。未結合範囲指定はエラー |
| `add_table` | `sheet`,`name`,`range` | `style`(既定`TableStyleMedium9`),`banded`(既定true) | 構造化テーブル作成（フィルター・構造化参照付き。`format_table`の見た目装飾とは別物）。`range`1行目の既存値がヘッダー名になる。`name`はブック内一意な識別子 |
| `update_table` | `sheet`,`name` | `range`,`style`,`banded` | 既存テーブルの範囲拡張・スタイル変更 |
| `remove_table` | `sheet`,`name` | - | テーブル定義のみ削除（値・書式は残る） |
| `freeze_panes` | `sheet`,`cell` | - | ウィンドウ枠固定（`"A2"`で1行目固定） |
| `add_chart` | `sheet`,`type`,`data_range`,`anchor` | `title`,`categories_range`,`titles_from_data`(既定true) | グラフ追加。`type`は`bar`/`line`/`pie`/`scatter` |
| `add_conditional_format` | `sheet`,`range`,`rule_type`,`params` | - | 条件付き書式（下記表参照） |
| `add_data_validation` | `sheet`,`range`,`type` | `formula1`,`formula2`,`prompt`,`prompt_title`,`error_message`,`error_title`,`allow_blank`(既定true) | データ検証（下記参照） |

補足: `sheet`はシート名または0始まりインデックス。`rows`/`value`は文字列・数値・真偽値・`null`。`=`で始まる文字列は数式として扱われる（このスクリプトは評価しない。計算値確認はexcel-recalcスキルの`recalc_excel.py`）。

## style共通スキーマ（`set_cell`/`set_range`/`set_style`/`header_style`で共通）

```json
{"bold": true, "italic": false, "font_color": "0000FF", "font_size": 11,
 "fill_color": "FFFF00", "number_format": "#,##0.00",
 "align": "center", "valign": "center", "wrap_text": false,
 "border": "thin", "role": "input"}
```
全キー省略可。`font_color`/`fill_color`はRRGGBB16進数。`align`は`left`/`center`/`right`、`valign`は`top`/`center`/`bottom`。`border`は文字列（`thin`/`medium`/`thick`、4辺一括）または`{"top":"thin","bottom":"thin"}`（辺別、キーは`top`/`bottom`/`left`/`right`）。

`role`によるセル色分け（Anthropic公式スキル準拠、推奨）— `font_color`を書く代わりに`role`を指定すると自動で規約色になる:

| role | 色 | 意味 |
|---|---|---|
| `input` | 青`0000FF` | ハードコードした入力値 |
| `formula` | 黒`000000` | 同一シート内で完結する数式 |
| `link` | 緑`008000` | 他シートを参照する数式 |

表を作る際はこの規約で`role`を使い分けると「どこが手入力/どこが計算か」が一目で分かる。

## 美しい表を作る基本レシピ（必須ルール）

**表形式データ（レポート・一覧・予定表等）は`set_range`に`header_style`を付けるだけでよい**（中身は空`{}`でも「1行目が見出し」の合図として機能する）。見出し配色・罫線・縞模様・見出し行固定・列幅調整が自動適用される。`fill_color`/`font_color`/`border`を1セルずつ自前で組み立てるのは避ける（呼び出しが増え見た目もバラつく）。配色を明示指定されたときのみ`format_table`のオプション（`header_fill`等）で上書きする。

```json
[{"op": "set_range", "sheet": "Sheet1", "start_cell": "A1",
  "rows": [["月","行事","担当"], ["4月","入学式","田中"], ["5月","遠足","佐藤"]],
  "header_style": {}}]
```

自動仕上げ不要（見出しなし生データ等）のときだけ`"format_table": false`を付ける。

**見出し行とデータ行は同じ`set_range`呼び出しの`rows`にまとめる**（見出しとデータを別呼び出しに分割しない。分割時に先頭行を誤って見出し装飾しないガードはあるが、呼び出し回数が増えるだけで利点がない）。複数の表を作る場合は表ごとに個別の`set_range`を呼べば干渉しない。

## 行ごとに異なる背景色を付けたいとき（必須ルール）

**書き込み後に行番号を目で数えて`set_style`で塗り直すのは避ける**（見出し行を含めるかの数え間違いで1行ズレる事故が起きやすい）。`set_range`の`rows`と**同じ要素数**の`row_styles`（各要素は1行分のstyle辞書、色なしは`null`）を渡す。`rows`を組み立てる時点で対応するstyleを同じ配列に並べるためズレようがない。

```json
{"op": "set_range", "sheet": "月間予定表", "start_cell": "A2",
 "rows": [["月","行事名"], ["1月","役員選考"], ["1月","新年会"], ["2月","ひな祭り"]],
 "header_style": {"bold": true, "fill_color": "D9E2F3"},
 "row_styles": [null, {"fill_color": "DAEEF3"}, {"fill_color": "DAEEF3"}, {"fill_color": "F8CBAD"}]}
```
`row_styles`の1要素目は見出し行に対応（`header_style`と両方渡すと`row_styles`優先）。要素数不一致はエラー。

## 同じ値が続く列はセルを結合する（月・区分・期間などのグルーピング列）

同じ値が2行以上連続する列（例:「月」列）は、値を繰り返し書いたままにせず結合する。
1. `set_range`で表を書き込む（この時点では重複値をそのまま書いてよい）。
2. 連続範囲だけ`merge_cells`で結合する（結合すると左上以外の値は自動的に消えるため事前の空欄化は不要）。
3. 結合セルに`set_style`で`{"valign":"center"}`を当てると縦中央揃えになる。

```json
[{"op": "set_range", "sheet": "週間予定表", "start_cell": "A2",
  "rows": [["1月","第1週"], ["1月","第2週"], ["2月","第1週"]], "header_style": {}},
 {"op": "merge_cells", "sheet": "週間予定表", "range": "A2:A3"},
 {"op": "set_style", "sheet": "週間予定表", "range": "A2:A3", "style": {"valign": "center"}}]
```
1回の`merge_cells`は連続矩形のみ結合可。値グループが複数（上記の「1月」「2月」）ならグループごとに`merge_cells`＋`set_style`を1回ずつ呼ぶ。

**既に書き込み済みの表**（`insert_rows`で追記した後など）に後から仕上げをかけたいだけなら独立opの`format_table`を使う: `[{"op": "format_table", "sheet": "Sheet1", "range": "A1:C3"}]`。`role`規約で入力セルに色を当てていても`format_table`は本体行のフォント色を変更しないため壊れない（呼び出し順序は値→`format_table`でもその逆でもよい）。

## 条件付き書式（`add_conditional_format`）

`rule_type`/`params`は`openpyxl.formatting.rule`のコンストラクタ引数にそのまま渡る。

| rule_type | params例 |
|---|---|
| `color_scale` | `{"start_type":"min","start_color":"FFFFFF","end_type":"max","end_color":"FF0000"}` |
| `cell_is` | `{"operator":"greaterThan","formula":["100"],"fill":null}`（`fill`はPatternFillが必要なため実質省略。色付けは事前に`set_style`で当てておく運用でもよい） |
| `formula` | `{"formula":["$A1=\"NG\""]}` |
| `data_bar` | `{"start_type":"min","end_type":"max","color":"638EC6"}` |
| `icon_set` | `{"icon_style":"3TrafficLights1","type":"percent"}` |

不正な`params`は「`ops[N]の適用に失敗しました`」＋Pythonの例外詳細が返るのでそれを手がかりに修正する。

## データ検証（`add_data_validation`）

プルダウン例:
```json
{"op": "add_data_validation", "sheet": "Sheet1", "range": "C2:C100", "type": "list",
 "formula1": "\"OK,NG,保留\"", "prompt": "OK/NG/保留のいずれかを選択", "error_message": "リストにない値です"}
```
`type`は`list`/`whole`/`decimal`/`date`/`textLength`/`custom`（openpyxl `DataValidation.type`と同じ）。

## 手順とエッジケース

1. 依頼内容からopsを組み立てる（データ投入=`set_range`、既存表への追記=`insert_rows`+`set_range`、見た目仕上げ=`format_table`、個別セル調整=`set_style`、グラフ=`add_chart`、を組合せる）。
2. `run_script`を呼ぶ。
3. `--new`使用時に対象が既存で上書き可否不明なら、`--overwrite`なしで一度実行しエラーからユーザーに確認する。
4. 成功時`{"path":...,"sheets":[...],"applied_ops":N}`が返るので保存先パスを伝える。

エッジケース: 拡張子がxlsx/xlsm以外／opsがJSON配列でない／opに`op`キーがない／存在しないシートやop種別を指定／`--new`なしでファイル不在／`--new`かつ`--overwrite`なしで既存ファイル、はいずれもエラー＋終了コード1（何番目のどのopが失敗したかメッセージに含まれる。それを手がかりに修正して再実行）。

## デザイン確認（必須ルール）

デザイン・レイアウト（配色・罫線・列幅・グラフ配置・印刷時の見え方等）の調整を行う前後には、必ずexcel-renderスキル（`render_excel.py`＋`analyze_image`）でシートを画像として確認する。excel-readのセル値・style情報だけで見た目を判断して完了報告しない。

## 禁止事項

- `--new`かつ`--overwrite`なしで既存ファイルへ強制上書きする（エラーになった場合はユーザーに上書き可否を確認してから再実行する）。
- `.xls`への編集・生成（拡張子の対応外操作）。
