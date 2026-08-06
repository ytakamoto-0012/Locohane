---
name: pdf-tools
description: PDFファイルからテキストを抽出して読む、PDFページを画像化してレイアウト・図表・スキャン内容をLLMに視覚的に把握させる、およびテキストからPDFファイルを生成する（日本語対応）。ユーザーがPDFの内容を確認したいとき、PDFの文章を要約・検索したいとき、PDFの表・図・レイアウトを確認したいとき、テキスト抽出できないスキャンPDFの内容を知りたいとき、レポートや文書をPDFとして出力・保存したいときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pdf-tools

PDFの読み込み（テキスト抽出／ページの画像化）とPDFの生成を行うスキルです。3つの
スクリプトを `run_script` ツールで実行して結果を得ます。日本語のPDF読み書きに
対応しています。

各スクリプトは正常系なら終了コード0でJSON1行を標準出力へ、異常系なら
終了コード非0でエラーメッセージを標準エラーへ出力します。

`read_pdf.py` は抽出したページ本文を直接標準出力へは返さず、一時JSON
ファイルへ書き出してそのパス（`result_path`）を返します。中身を確認するには
`Read` ツールで `result_path`（または `path_memory` の `@N`）を読んでください
（内容は複数行に整形されているため `offset`/`limit` で部分読み込みできます）。

## 1. read_pdf.py — PDFからテキスト抽出

呼び出し例:
```json
{
    "skill_name": "pdf-tools",
    "script_filename": "read_pdf.py",
    "script_args": ["C:\\Users\\me\\report.pdf", "--start-page", "1", "--max-pages", "20"]
}
```
`--start-page`/`--max-pages` は省略可（既定 start-page=1, max-pages=20）。

出力例:
```json
{"path": "C:\\foo\\report.pdf", "total_pages": 42, "start_page": 1, "end_page": 20,
 "metadata": {"title": "資料タイトル", "author": null, "subject": null},
 "pages_count": 20,
 "result_path": "C:\\...\\_tmp_<thread_id>\\pdf_read\\1a2b3c4d_20260805_153012_123456.json",
 "path_memory": {"@7": "C:\\...\\_tmp_<thread_id>\\pdf_read\\1a2b3c4d_20260805_153012_123456.json"}}
```
ページ本文（`pages`、各要素は `{"page", "text"}`）は標準出力からは省かれ、
`result_path` が指すJSONファイルにのみ含まれます。`Read` ツールで
`result_path`（または `path_memory` の `@N`）を読み、各要素の `text` を
つなげてユーザーに内容を報告するか、要約して伝えてください。
`total_pages` が `max_pages` より多い場合は、続きを読みたければ `--start-page` を
`end_page + 1` に指定して再度呼び出すことを案内してください（`read_file.py` の
offset/limitと同じ考え方のページ版です）。

エッジケース:
- ファイル不在・ディレクトリ指定はエラー終了します。
- 壊れたPDF/PDF以外のファイルを指定した場合もエラー終了します。
- パスワード保護されたPDFは、空パスワードでの復号を試みて失敗すればエラー終了します
  （「パスワード保護されたPDFです」という旨がstderrに出ます）。
- `start_page` が総ページ数を超える場合はエラーにはならず、
  `pages_count: 0`, `start_page`/`end_page`: `null` を終了コード0で返します。
- スキャン画像のみのPDF（OCRされていないもの）は `text` が空文字になることがあります。
  その場合は下記の `render_pdf_pages.py` でページを画像化し、`analyze_image` で
  LLMに直接見せる方法を使ってください（このスキルはOCRを行いません）。

## 2. render_pdf_pages.py — PDFページを画像化してLLMに見せる

`read_pdf.py` でテキストがうまく取れないスキャンPDF・画像主体のPDFの内容を
知りたいときに使うほか、テキストは取得できても表・図・段組みなどテキストだけ
では読み取れない情報があるため、文書の意図をより高精度に汲み取りたいときも
こちらを使って画像として内容を確認します。

呼び出し例:
```json
{
    "skill_name": "pdf-tools",
    "script_filename": "render_pdf_pages.py",
    "script_args": ["C:\\Users\\me\\report.pdf", "--start-page", "1", "--max-pages", "3"]
}
```
`--start-page`/`--max-pages`（既定3、最大5にクランプ）/`--dpi`（既定150、
72〜300にクランプ）は省略可。

出力例:
```json
{"path": "C:\\foo\\report.pdf", "total_pages": 42, "start_page": 1, "end_page": 3, "dpi": 150,
 "images": [
   {"page": 1, "image_path": "C:\\...\\_tmp_<thread_id>\\pdf_rendered\\1a2b3c4d_p1.png"},
   {"page": 2, "image_path": "C:\\...\\_tmp_<thread_id>\\pdf_rendered\\1a2b3c4d_p2.png"},
   {"page": 3, "image_path": "C:\\...\\_tmp_<thread_id>\\pdf_rendered\\1a2b3c4d_p3.png"}
 ]}
```

**重要（2段階手順）**: このスクリプト自体はPNGファイルを保存してパスをJSONで
返すだけで、LLMへ画像を見せるところまでは行いません。`images` の各要素の
`image_path`（絶対パス）を、続けて `analyze_image` ツール（このスキル専用ではなく
共通ツール）の `relative_path` 引数にそのまま渡して呼び出してください（`analyze_image`
は絶対パスもそのまま読める）。1回の `analyze_image` 呼び出しで1ページ分が見えるので、
複数ページある場合はページ数分 `analyze_image` を呼びます。

エッジケース:
- ファイル不在・ディレクトリ指定・壊れたPDF/暗号化PDFはエラー終了します。
- `start_page` が総ページ数を超える場合はエラーにはならず、`images: []` を
  終了コード0で返します。
- `max_pages` は5にクランプされます。総ページ数が多い文書を広く画像化したい場合は
  `--start-page` を変えて複数回に分けて呼び出してください（コスト抑制のため）。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/pdf_rendered/`）に保存されます。同一PDF・同一ページの
  再実行時は上書きされ、会話終了時に自動的に削除されます。

## 3. create_pdf.py — テキストからPDF生成

呼び出し例:
```json
{
    "skill_name": "pdf-tools",
    "script_filename": "create_pdf.py",
    "script_args": ["C:\\Users\\me\\out.pdf", "--title", "報告書", "--text", "本文のテキストです。"]
}
```
`--title` は省略可（先頭見出し）。本文が長い/複数行にわたる場合は `--text`
の代わりに `["<出力先PDFの絶対パス>", "--title", "<省略可>", "--text-file", "<本文を書いたUTF-8テキストファイルの絶対パス>"]`
を使う（`--text`/`--text-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。

本文の書式:
- 空行（`\n\n`）で段落を区切ります。
- 段落内の単一の改行は改行のまま維持されます（`<br/>` 相当）。
- 日本語フォント（明朝体）で描画されます。特殊なマークアップ記法は不要で、
  プレーンテキストをそのまま渡してください。

出力例:
```json
{"output_path": "C:\\foo\\report.pdf", "size_bytes": 18234}
```
生成が終わったら `output_path` をユーザーに伝えてください。

エッジケース:
- `--text` と `--text-file` を両方指定、または両方省略した場合はエラー終了します。
- `--text-file` に指定したファイルが存在しない場合はエラー終了します。
- 出力先ディレクトリが存在しない場合は自動的に作成されます。
- 既存ファイルと同名の場合は上書きされます（上書きしてよいか事前にユーザーへ確認するとよい）。

## エッジケース共通

- 依存パッケージ `pypdf`（テキスト抽出）・`pypdfium2`（画像化）・`reportlab`（生成）が
  実行環境に無いと `ModuleNotFoundError` で終了コード非0になります。その場合は導入者へ
  `pip install pypdf pypdfium2 reportlab` の実施を促してください。
- いずれのスクリプトも例外を投げず、エラーはstderr+終了コード非0で返します。
  `run_script` の戻り値テキストの `[標準エラー]` セクションを確認してください。

## パスメモリー（`@N`）

`create_pdf.py` が生成したPDF、`render_pdf_pages.py` が生成した画像、
`read_pdf.py` が書き出す結果JSON（`result_path`）は、出力JSONに
`path_memory`（例: `{"@12": "C:\\foo\\report.pdf"}`）として
自動登録されます。続けて `run_script` を呼ぶ場合、絶対パスの代わりに
その `@N` を `script_args` にそのまま渡せます（自動的に実パスへ解決
されます）。
