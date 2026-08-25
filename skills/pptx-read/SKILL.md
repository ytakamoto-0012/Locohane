---
name: pptx-read
description: PowerPoint（.pptx）の読み込み専用スキル。スライドごとのタイトル・本文テキスト・表・発表者ノートを人間向けの要約として抽出する。python-pptxを使いLibreOffice等の外部アプリは不要。ユーザーがPowerPointファイルの内容を確認・要約したいとき、pptxからテキストや表を抽出したいときに使う。新規作成は`pptx-create`、既存テンプレートの部分編集は`pptx-edit`（事前に`pptx-inspect`が必要）、レイアウトを画像で確認したい場合は`pptx-render`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pptx-read

pptxの読み込み（テキスト・表・発表者ノートの抽出）を行うスキルです。
`read_pptx.py` を `run_script` ツールで実行して結果を得ます。
`python-pptx` を使っており、LibreOffice等の外部アプリは不要です。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

スライドごとの本文データは直接標準出力へは返さず、一時JSONファイルへ書き出して
そのパス（`result_path`）を返します。中身を確認するには `Read` ツールで
`result_path`（または `path_memory` の `@N`）を読んでください（内容は複数行に
整形されているため `offset`/`limit` で部分読み込みできます）。

## read_pptx.py — pptxからテキスト・表・ノート抽出

呼び出し例:
```json
{
    "skill_name": "pptx-read",
    "script_filename": "read_pptx.py",
    "script_args": ["C:\\Users\\me\\sample.pptx", "--start-slide", "1", "--max-slides", "20"]
}
```
`--start-slide`/`--max-slides` は省略可（既定 start-slide=1, max-slides=20）。

出力例:
```json
{"path": "C:\\foo\\sample.pptx", "total_slides": 12, "start_slide": 1, "end_slide": 12,
 "slides_count": 12,
 "result_path": "C:\\...\\_tmp_<name>\\pptx_read\\1a2b3c4d_20260805_153012_123456.json"}
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

## エッジケース

- ファイル不在・ディレクトリ指定・pptx以外の壊れたファイルはエラー終了します。
- `start_slide` が総スライド数を超える場合はエラーにはならず、
  `slides_count: 0`, `start_slide`/`end_slide`: `null` を終了コード0で返します。
- 依存パッケージ `python-pptx` が実行環境に入っていないと `ModuleNotFoundError` で
  終了コード非0になります。その場合は導入者へ `pip install python-pptx` の実施を促してください。

## 編集対象を特定したい場合

既存テンプレートを編集する目的でshape単位の構造（`shape_index`等）を知りたい
場合は、このスキルではなく `pptx-inspect` を使ってください。

## パスメモリー（`@N`）

`read_pptx.py` が書き出す結果JSON（`result_path`）は、出力JSONに
`path_memory`（例: `{"@7": "C:\\...\\1a2b3c4d.json"}`）として自動登録されます。
続けて `run_script`/`Read` を呼ぶ場合、絶対パスの代わりにその `@N` を
そのまま渡せます（自動的に実パスへ解決されます）。
