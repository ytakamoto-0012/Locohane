---
name: pptx-tools
description: pptx(PowerPoint)ファイルの読み込み（スライドのタイトル・本文・表・発表者ノートの抽出）、JSON構造を指定した新規pptxファイルの生成、既存pptxテンプレートのデザインを保ったままの部分編集（テキスト・表・ノートの差し替え、スライドの複製・削除・並び替え、画像の差し替え）、PPTXスライドの画像化（OLE→PDF→PNG、余白自動除去）を行う。ユーザーがPowerPointファイルの内容を確認・要約したいとき、pptxからテキストや表を抽出したいとき、新しいプレゼン資料・スライドを作成してほしいとき、簡単な資料をpptx形式で出力したいとき、既存のPowerPointテンプレート（社内フォーマット等）を流用して一部だけ差し替えたいとき、スライドを複製・削除・並び替えしたいとき、スライドのレイアウトや図表を確認したいときに使う。pptxを扱う場面では、officecli-pptxスキルが利用可能な場合は原則そちらを優先して使用し、本スキルはofficecliが利用できない場合のフォールバックとして使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.1"
---

# pptx-tools

pptxの読み込み（テキスト・表・発表者ノートの抽出）、pptxの生成、既存pptxテンプレートの
部分編集（デザインを保ったままの差し替え・複製・削除・並び替え）を行うスキルです。
4つのスクリプトを `run_script` ツールで実行して結果を得ます。
`python-pptx` を使っており、LibreOffice等の外部アプリやサムネイル画像化には対応しません。
新規生成（`create_pptx.py`）は同梱の16:9テンプレート（`assets/template_16x9.pptx`、
python-pptx既定テーマ準拠）のデザインになりますが、既存テンプレートの編集
（`inspect_pptx.py` / `edit_pptx.py`）は元ファイルのテーマ・マスター・レイアウトの
デザイン（アスペクト比を含む）をそのまま保持します。

各スクリプトは正常系なら終了コード0でJSON1行を標準出力へ、異常系なら
終了コード非0でエラーメッセージを標準エラーへ出力します。

`read_pptx.py`・`inspect_pptx.py` はスライドごとの本文データを直接標準出力へは
返さず、一時JSONファイルへ書き出してそのパス（`result_path`）を返します。
中身を確認するには `Read` ツールで `result_path`（または `path_memory` の
`@N`）を読んでください（内容は複数行に整形されているため `offset`/`limit`
で部分読み込みできます）。

## 1. read_pptx.py — pptxからテキスト・表・ノート抽出

呼び出し例:
```json
{
    "skill_name": "pptx-tools",
    "script_filename": "read_pptx.py",
    "script_args": ["C:\\Users\\me\\sample.pptx", "--start-slide", "1", "--max-slides", "20"]
}
```
`--start-slide`/`--max-slides` は省略可（既定 start-slide=1, max-slides=20）。

出力例:
```json
{"path": "C:\\foo\\sample.pptx", "total_slides": 12, "start_slide": 1, "end_slide": 12,
 "slides_count": 12,
 "result_path": "C:\\...\\_tmp_<thread_id>\\pptx_read\\1a2b3c4d_20260805_153012_123456.json"}
```
スライドごとの本文（`slides`、各要素は `{"index", "title", "texts", "tables", "notes"}`）は
標準出力からは省かれ、`result_path` が指すJSONファイルにのみ含まれます。
`Read` ツールで読んで内容を確認してください。各要素のキーの意味は以下の通りです。

- `title`: そのスライドのタイトルプレースホルダのテキスト（無ければ `null`）。
- `texts`: タイトル以外のテキストを持つ図形のテキスト一覧（1図形＝1要素、複数行はそのまま`\n`区切り）。
- `tables`: 表があれば2次元配列（1行目を含む全行）のリストとして格納。表が無ければ `[]`。
- `notes`: 発表者ノートのテキスト（無ければ `null`）。

`total_slides` が `max_slides` より多い場合は、続きを読みたければ `--start-slide` を
`end_slide + 1` に指定して再度呼び出すことを案内してください（`read_pdf.py` の
start-page/max-pageと同じ考え方のスライド版です）。

エッジケース:
- ファイル不在・ディレクトリ指定・pptx以外の壊れたファイルはエラー終了します。
- `start_slide` が総スライド数を超える場合はエラーにはならず、
  `slides_count: 0`, `start_slide`/`end_slide`: `null` を終了コード0で返します。

## 2. create_pptx.py — JSON定義から新規pptx生成

呼び出し例:
```json
{
    "skill_name": "pptx-tools",
    "script_filename": "create_pptx.py",
    "script_args": ["C:\\Users\\me\\out.pptx", "--data", "{\"slides\": [{\"layout\": \"title\", \"title\": \"四半期報告\", \"subtitle\": \"2026年度 第2四半期\"}]}"]
}
```
`--data` の値は下記「JSON定義スキーマ」に従うJSON文字列です。スライド数が
多くJSONが長大になる場合は `--data` の代わりに
`["<出力先pptxの絶対パス>", "--data-file", "<スライド定義JSONを書いたUTF-8ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。

### JSON定義スキーマ

トップレベルは `{"slides": [...]}` で、各要素が1スライド分の定義です。
`layout` キーで種類を指定します（省略時は `content`）。

| layout | 用途 | 主なキー |
|---|---|---|
| `title` | 表紙スライド | `title`, `subtitle` |
| `content` | タイトル＋箇条書き | `title`, `bullets`, `notes` |
| `section` | 章区切りスライド | `title` |
| `two_content` | 2カラムの箇条書き | `title`, `left_bullets`, `right_bullets` |
| `table` | タイトル＋表 | `title`, `table` |
| `picture` | タイトル＋画像 | `title`, `image_path`, `caption` |
| `blank` | 白紙（notesのみ） | `notes` |

- `bullets` / `left_bullets` / `right_bullets` は文字列のリスト、または
  `{"text": "本文", "level": 1}` のようなdict（`level`はインデント段数、0始まり、省略時0）を
  混在させたリストです。
- `table` は `{"headers": ["列1", "列2"], "rows": [["a", "b"], ["c", "d"]]}` の形（`headers`は省略可）。
- `image_path` は挿入したい画像ファイルの絶対パス（実行環境から読める必要があります）。
- `notes`（発表者ノート）は全layout共通で指定できます。

サンプルJSON（`--data` にそのまま渡せる1行にする場合はこれを圧縮してください）:
```json
{
  "slides": [
    {"layout": "title", "title": "四半期報告", "subtitle": "2026年度 第2四半期"},
    {"layout": "content", "title": "サマリ", "bullets": ["売上は前年比+12%", {"text": "詳細は次スライド", "level": 1}]},
    {"layout": "table", "title": "実績一覧", "table": {"headers": ["月", "売上"], "rows": [["4月", "120"], ["5月", "135"]]}},
    {"layout": "blank", "notes": "質疑応答用の白紙ページ"}
  ]
}
```

出力例:
```json
{"output_path": "C:\\foo\\out.pptx", "total_slides": 4, "size_bytes": 34200}
```
生成が終わったら `output_path` と `total_slides` をユーザーに伝えてください。

エッジケース:
- `--data` と `--data-file` を両方指定、または両方省略した場合はエラー終了します。
- `--data-file` に指定したファイルが存在しない場合はエラー終了します。
- JSONとして解析できない場合、`slides` キーが無い/空の場合はエラー終了します。
- 未対応の `layout` 値を指定した場合、対応一覧を添えてエラー終了します。
- `layout: table` で `table` キーが無い、`headers`/`rows` が両方空の場合はエラー終了します。
- `layout: picture` で `image_path` が無い、または指定パスにファイルが存在しない場合はエラー終了します。
- 出力先ディレクトリが存在しない場合は自動的に作成されます。
- 既存ファイルと同名の場合は上書きされます（上書きしてよいか事前にユーザーへ確認するとよい）。
- 生成されるデザインは同梱の16:9テンプレート（python-pptxの既定テーマ準拠）です。会社ロゴや独自テンプレートの適用はこのスキルの対象外です。

## 3. inspect_pptx.py — 編集対象を特定するための構造読み取り

既存テンプレートを編集する前に**必ず**このスクリプトでスライド構造を確認し、
`shape_index` を把握してから `edit_pptx.py` を呼んでください。
`read_pptx.py` は人間向けの要約（title/texts/tables/notes）を返すのに対し、
こちらは編集に必要なshape単位の構造情報を返します。

呼び出し例:
```json
{
    "skill_name": "pptx-tools",
    "script_filename": "inspect_pptx.py",
    "script_args": ["C:\\Users\\me\\template.pptx", "--start-slide", "1", "--max-slides", "20"]
}
```
`--start-slide`/`--max-slides` は省略可。

出力例:
```json
{"path": "C:\\foo\\template.pptx", "total_slides": 4, "start_slide": 1, "end_slide": 4,
 "slides_count": 4,
 "result_path": "C:\\...\\_tmp_<thread_id>\\pptx_inspect\\1a2b3c4d_20260805_153012_123456.json"}
```
スライド単位のshape構造（`slides`、各要素は
`{"index", "layout_name", "layout_index", "shapes", "notes_present"}`。
`shapes` の各要素は `{"shape_index", "name", "shape_type", "is_placeholder",
"placeholder_idx", "placeholder_type", "has_text_frame", "text_preview",
"has_table", "table_dims", "has_picture"}`）は標準出力からは省かれ、
`result_path` が指すJSONファイルにのみ含まれます。`Read` ツールで読んで
`shape_index` を確認してから `edit_pptx.py` を呼んでください。

- `shape_index` は `edit_pptx.py` の各操作で指定する `shape_index` と完全に一致します
  （このスライド内での0始まり連番）。
- `text_preview` は先頭50文字までの切り詰め表示です（編集対象を見分けるための参考情報で、
  全文取得には `read_pptx.py` を使ってください）。
- ページングは `read_pptx.py` と同じ設計です（`total_slides` が `max_slides` を超える場合は
  `--start-slide` を `end_slide + 1` にして再度呼び出す）。

エッジケース: ファイル不在・ディレクトリ指定・壊れたファイルはエラー終了します
（`read_pptx.py` と同じ挙動）。

## 4. edit_pptx.py — 既存pptxテンプレートへの操作列適用

既存のpptx（社内テンプレート等）のテーマ・マスター・レイアウトのデザインを保ったまま、
指定したスライドの中身だけを書き換える／スライドを複製・削除・並び替えます。
**必ず `template_path` とは別の `output_path` に保存され**、テンプレート自体は変更されません
（同じパスを指定した場合は後述の通りエラーになります）。

呼び出し例:
```json
{
    "skill_name": "pptx-tools",
    "script_filename": "edit_pptx.py",
    "script_args": ["C:\\Users\\me\\template.pptx", "C:\\Users\\me\\edited.pptx", "--data", "{\"operations\": [{\"op\": \"set_title\", \"slide\": 2, \"text\": \"更新後サマリ\"}]}"]
}
```
`--data` の値は下記「JSON操作列スキーマ」に従うJSON文字列です。JSONが長い
場合は `--data` の代わりに `["<テンプレート>", "<出力先>", "--data-file", "<操作列JSONを書いたUTF-8ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。
テンプレートと同じパスへ保存したい場合のみ `script_args` に `"--overwrite"` を追加する
（**ユーザーに上書きしてよいか確認してから**付けること）。

### JSON操作列スキーマ

トップレベルは `{"operations": [...]}` で、各要素が1つの操作です。**配列の先頭から順に**
1つのPresentationへ適用されるため、`duplicate_slide`/`delete_slide`/`reorder_slides` を使うと
それ以降の操作で参照するスライド番号がずれます（下記`slide`キーの注記を参照）。

すべての操作の `slide` キーは1始まりで、**その操作を適用する時点での**スライド番号を指します。

| op | 主なキー | 内容 |
|---|---|---|
| `set_title` | `slide`, `text` | タイトルプレースホルダのテキストを差し替え |
| `set_text` | `slide`, `shape_index`, `bullets` | 任意shapeのテキストを差し替え（`bullets`は`create_pptx.py`と同じ、文字列または`{"text":..,"level":..}`のリスト） |
| `set_table_cell` | `slide`, `shape_index`, `row`, `col`, `text` | 既存表の1セルを差し替え（row/colは0始まり、ヘッダー行も含む） |
| `set_table` | `slide`, `shape_index`, `headers`, `rows` | 既存表を丸ごと差し替え。**既存表と行数・列数が完全一致する場合のみ**可能（python-pptxは既存表の行列数の増減に非対応。行列数を変えたい場合は`create_pptx.py`で新規スライドとして作る） |
| `set_notes` | `slide`, `text` | 発表者ノートを差し替え |
| `replace_picture` | `slide`, `shape_index`, `image_path` | 既存画像shapeの位置・サイズを保ったまま画像だけ差し替え（差し替え後、z順序は最前面に移動する点に注意） |
| `duplicate_slide` | `slide`, `insert_after`(省略時は`slide`と同じ), `count`(省略時1) | 指定スライドを同じレイアウトのまま複製し、`insert_after`の直後に`count`枚挿入。プレースホルダ・表・テキストボックス・画像を含めて複製できる |
| `delete_slide` | `slide` | 指定スライドを削除 |
| `reorder_slides` | `order` | **その時点での全スライド番号(1始まり)の順列**を渡し、その並びに変更（例: `[2,1,3]`で1番目と2番目を入替） |

サンプルJSON:
```json
{"operations": [
  {"op": "set_title", "slide": 2, "text": "更新後サマリ"},
  {"op": "set_table_cell", "slide": 3, "shape_index": 2, "row": 1, "col": 1, "text": "150"},
  {"op": "duplicate_slide", "slide": 4, "insert_after": 4, "count": 2},
  {"op": "delete_slide", "slide": 1}
]}
```

出力例:
```json
{"output_path": "C:\\foo\\edited.pptx", "total_slides": 5, "size_bytes": 37998, "applied_operations": 4}
```
生成が終わったら `output_path` と `total_slides` をユーザーに伝えてください。

### duplicate_slide の非対応shape

`duplicate_slide` は複製元スライドにチャート・SmartArt・動画・OLEオブジェクト・グループ図形
（`shape_type`が`CHART`/`DIAGRAM`/`IGX_GRAPHIC`/`MEDIA`/`WEB_VIDEO`/`EMBEDDED_OLE_OBJECT`/
`LINKED_OLE_OBJECT`/`OLE_CONTROL_OBJECT`/`GROUP`のいずれか）が含まれる場合、
壊れたpptxを生成しないよう**エラー終了**します（`inspect_pptx.py`の`shape_type`で事前確認可能）。
プレースホルダ・テキストボックス・表・画像（`PICTURE`）は複製に対応しています。

### エッジケース

- `template_path` と `output_path` が同じで `--overwrite` 未指定の場合はエラー終了します。
- `--data` と `--data-file` を両方指定、または両方省略した場合はエラー終了します。
- 存在しない `slide` / `shape_index`、shape種別が合わない操作（例: 表でないshapeへの
  `set_table_cell`）、`set_table` の行列数不一致、`reorder_slides` の順列が現在の全スライド数と
  不一致、`replace_picture` の `image_path` 不在は、いずれもその操作番号を添えてエラー終了します。
- 操作の途中でエラーになった場合、`output_path` へのファイル保存は行われません
  （テンプレート自体もその場では変更されないため、途中失敗しても既存ファイルへの影響はありません）。
- 未対応の `op` 値を指定した場合、対応一覧を添えてエラー終了します。

## 4. render_pptx.py — PPTXスライドを画像化してLLMに見せる

`read_pptx.py` でテキストは取得できても、レイアウト・図表・画像の配置・強調表現
などテキストだけでは読み取れない情報があるため、資料の意図をより高精度に汲み取り
たいときはこちらを使って画像として内容を確認します。

呼び出し例:
```json
{
    "skill_name": "pptx-tools",
    "script_filename": "render_pptx.py",
    "script_args": ["C:\\Users\\me\\presentation.pptx", "--start-page", "1", "--max-pages", "3"]
}
```
`--start-page`/`--max-pages`（既定3、最大5にクランプ）/`--dpi`（既定300、
72〜600にクランプ）/`--no-crop`（余白除去をオフ）は省略可。

出力例:
```json
{"path": "C:\\foo\\presentation.pptx", "tool": "pptx", "total_pages": 12, "start_page": 1, "end_page": 3, "dpi": 300, "target_dpi": 150, "crop_applied": true,
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
1回の `analyze_image` 呼び出しで1スライド分が見えるので、複数スライドある場合は
スライド数分 `analyze_image` を呼びます。

**余白除去について**: 既定では白黒境界判定で余白を自動除去します。スライドの
背景色や図形の配置を正確に把握できるので、このオプションを有効にしてください。

エッジケース:
- ファイル不在・ディレクトリ指定・壊れたファイル/PowerPoint未インストールはエラー終了します。
- `start_slide` が総スライド数を超える場合はエラーにはならず、`images: []` を
  終了コード0で返します。
- `max_pages` は5にクランプされます。総スライド数が多いプレゼンを広く画像化
  したい場合は `--start-page` を変えて複数回に分けて呼び出してください。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/rendered/`）に保存されます。同一ファイルの再実行時は
  上書きされ、会話終了時に自動的に削除されます。
- 依存パッケージ `pywin32`・`pypdfium2`・`pillow` が実行環境に無い場合は
  `ImportError` で終了コード非0になります。

## エッジケース共通

- 依存パッケージ `python-pptx` が実行環境に入っていないと `ModuleNotFoundError` で
  終了コード非0になります。その場合は導入者へ `pip install python-pptx` の実施を促してください。
- いずれのスクリプトも例外を投げず、エラーはstderr+終了コード非0で返します。
  `run_script` の戻り値テキストの `[標準エラー]` セクションを確認してください。

## パスメモリー（`@N`）

`create_pptx.py`・`edit_pptx.py` が生成・更新したファイルは、出力JSONに
`path_memory`（例: `{"@12": "C:\\foo\\out.pptx"}`）として自動登録されます。
続けて `run_script` を呼ぶ場合、絶対パスの代わりにその `@N` を
`script_args` にそのまま渡せます（自動的に実パスへ解決されます）。
`read_pptx.py`・`inspect_pptx.py` が書き出す結果JSON（`result_path`）も
同様に自動登録されます。
