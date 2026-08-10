---
name: pptx-create
description: JSON定義を渡すだけでPowerPoint（.pptx）を新規生成するスキル。表紙・箇条書き・章区切り・2カラム・表・画像・白紙の各レイアウトに対応し、同梱の16:9テンプレート（python-pptx既定テーマ準拠）で1回のスクリプト実行でプレゼン資料を作成できる。新しいプレゼン資料・スライドを作成してほしいとき、簡単な資料をpptx形式で出力したいときに使う。既存テンプレートのデザインを保った部分編集は`pptx-edit`（事前に`pptx-inspect`が必要）、内容確認は`pptx-read`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pptx-create

JSON定義から新規pptxを生成するスキルです。`create_pptx.py` を `run_script`
ツールで実行して結果を得ます。`python-pptx` を使っており、LibreOffice等の
外部アプリやサムネイル画像化には対応しません。生成されるデザインは同梱の
16:9テンプレート（`assets/template_16x9.pptx`、python-pptx既定テーマ準拠）
になります。会社ロゴや独自テンプレートの適用が必要な場合は、このスキルでは
なく `pptx-edit`（既存テンプレートの部分編集）を使ってください。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "pptx-create",
    "script_filename": "create_pptx.py",
    "script_args": ["C:\\Users\\me\\out.pptx", "--data", "{\"slides\": [{\"layout\": \"title\", \"title\": \"四半期報告\", \"subtitle\": \"2026年度 第2四半期\"}]}"]
}
```
`--data` の値は下記「JSON定義スキーマ」に従うJSON文字列です。スライド数が
多くJSONが長大になる場合は `--data` の代わりに
`["<出力先pptxの絶対パス>", "--data-file", "<スライド定義JSONを書いたUTF-8ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。

## JSON定義スキーマ

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

## エッジケース

- `--data` と `--data-file` を両方指定、または両方省略した場合はエラー終了します。
- `--data-file` に指定したファイルが存在しない場合はエラー終了します。
- JSONとして解析できない場合、`slides` キーが無い/空の場合はエラー終了します。
- 未対応の `layout` 値を指定した場合、対応一覧を添えてエラー終了します。
- `layout: table` で `table` キーが無い、`headers`/`rows` が両方空の場合はエラー終了します。
- `layout: picture` で `image_path` が無い、または指定パスにファイルが存在しない場合はエラー終了します。
- 出力先ディレクトリが存在しない場合は自動的に作成されます。
- 既存ファイルと同名の場合は上書きされます（上書きしてよいか事前にユーザーへ確認するとよい）。
- 生成されるデザインは同梱の16:9テンプレート（python-pptxの既定テーマ準拠）です。会社ロゴや独自テンプレートの適用はこのスキルの対象外です。
- 依存パッケージ `python-pptx` が実行環境に入っていないと `ModuleNotFoundError` で
  終了コード非0になります。その場合は導入者へ `pip install python-pptx` の実施を促してください。

## テンプレート資産について

同梱の `assets/template_16x9.pptx` は `scripts/build_template_16x9.py`
（python-pptxの既定4:3テンプレートを16:9へスケーリングするビルドスクリプト）で
生成されたものです。テンプレート仕様自体を見直す場合のみ実行し、生成物を
`assets/` にコミットし直します（通常の生成フローでLLMがこのスクリプトを
実行する必要はありません）。

## 生成後の確認・既存テンプレートの編集

生成した資料のレイアウトを画像で確認したい場合は `pptx-render` スキルの
`render_pptx.py` + `analyze_image` を使ってください。既存のPowerPointテンプレート
（社内フォーマット等）のデザインを保ったまま一部だけ差し替えたい場合は、
このスキルではなく `pptx-inspect`→`pptx-edit` を使ってください。

## パスメモリー（`@N`）

`create_pptx.py` が生成したファイルは、出力JSONに `path_memory`
（例: `{"@12": "C:\\foo\\out.pptx"}`）として自動登録されます。続けて
`run_script` を呼ぶ場合、絶対パスの代わりにその `@N` を `script_args` に
そのまま渡せます（自動的に実パスへ解決されます）。
