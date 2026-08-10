---
name: docx-create
description: JSON仕様を渡すだけでWord文書（.docx）を新規生成するスキル。見出し・段落・箇条書き・番号付きリスト・表・画像・改ページ・ページ設定（用紙サイズ/余白/縦横）・ヘッダーフッター・ページ番号を含む本格的な文書を1回のスクリプト実行で作成できる。レポートや議事録、案内文書などをWordファイルとして出力・保存したいときに使う。既存docxの部分編集は`docx-edit`、内容確認は`docx-read`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# docx-create

JSON仕様からWord文書（`.docx`）を新規生成するスキルです。`create_docx.py` を
`run_script` ツールで実行して結果を得ます。

このプロジェクトには汎用のファイル書き込みツールが無いため、文書の内容は
LLM自身が組み立てたJSON文字列を **`--data` 引数にそのまま渡す**ことで
生成します（ユーザーがあらかじめJSONファイルを用意している場合のみ
`--data-file` でパス指定も可）。`pptx-create` の `create_pptx.py` と同じ設計です。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "docx-create",
    "script_filename": "create_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--data", "{\"blocks\": [{\"type\": \"heading\", \"text\": \"1. 概要\", \"level\": 1}, {\"type\": \"paragraph\", \"text\": \"本文です。\"}]}"]
}
```
`--data` の値は下記「JSON仕様の形式」に従うJSON文字列です。JSONが長くなる
場合は `--data` の代わりに `["<出力先パス.docx>", "--data-file", "<JSON仕様ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。

## JSON仕様の形式

```json
{
  "core_properties": {"title": "報告書", "author": "山田太郎"},
  "page": {
    "size": "a4",
    "orientation": "portrait",
    "margin_cm": {"top": 2.5, "bottom": 2.5, "left": 3.0, "right": 2.5}
  },
  "header_text": "社外秘",
  "footer_text": "株式会社サンプル",
  "page_number": true,
  "blocks": [
    {"type": "heading", "text": "1. 概要", "level": 1},
    {"type": "paragraph", "text": "本文のテキストです。", "bold": false},
    {"type": "paragraph", "runs": [
      {"text": "重要: ", "bold": true, "color": "FF0000"},
      {"text": "納期は来月末です。"}
    ]},
    {"type": "bullet_list", "items": ["項目A", "項目B", "項目C"]},
    {"type": "number_list", "items": ["手順1", "手順2"]},
    {"type": "table", "rows": [["項目", "値"], ["売上", "1200万円"]], "header_row": true},
    {"type": "image", "path": "C:\\foo\\chart.png", "width_cm": 10},
    {"type": "page_break"}
  ]
}
```

トップレベルのキーはすべて省略可能です（`blocks` のみ指定すれば最小構成の
文書が作れます）。

- `core_properties`: 省略可。`title`/`author` を文書プロパティに設定する。
- `page`: 省略可。`size` は `"a4"`（既定）または `"letter"`。`orientation`
  は `"portrait"`（既定）または `"landscape"`。`margin_cm` は上下左右の
  余白（cm単位、既定は上下2.5cm・左3.0cm・右2.5cm）。
- `header_text`/`footer_text`: 省略可。ヘッダー・フッターに表示する文字列。
- `page_number`: 省略可・既定 `false`。`true` にするとフッター中央に
  ページ番号フィールド（Word上で自動計算される `{PAGE}`）を追加する。
  Word側の設定によってはフィールドが `1` 等の実数値ではなく灰色表示に
  なることがあるが、印刷・PDF変換時には正しいページ番号が表示される
  （フィールドはWordが開いた時点で計算されるため）。
- `blocks`: 本文の要素を先頭から順に並べた配列。各要素の `type` ごとの
  仕様は以下の通り。

### blocks の各 type

| type | 必須キー | 主な省略可キー | 説明 |
|---|---|---|---|
| `heading` | `text` | `level`（既定1、0=Title・1〜9=見出しレベル） | 見出し段落 |
| `paragraph` | `text` または `runs` | `alignment`（`left`/`center`/`right`/`justify`）、`bold`/`italic`/`underline`/`color`/`font`/`size_pt`（`text` 使用時のみ直接効く） | 通常の段落。複数の書式が混在する文なら `runs` を使う |
| `bullet_list` | `items`（文字列配列） | なし | 行頭記号付き箇条書き |
| `number_list` | `items`（文字列配列） | なし | 番号付きリスト |
| `table` | `rows`（行の配列。各行はセル文字列の配列） | `header_row`（true なら1行目を太字に） | 表。列数は各行の最大長に合わせる |
| `image` | `path`（画像ファイルの絶対パス） | `width_cm`、`height_cm`（片方のみ指定でアスペクト比維持） | 画像挿入。パスは実在するファイルの絶対パスであること |
| `page_break` | なし | なし | 改ページ |

`paragraph` の `runs` 配列の各要素は `text` 必須、`bold`/`italic`/
`underline`（真偽値）、`color`（`"FF0000"` のようなRRGGBB16進文字列）、
`font`（フォント名。游明朝など日本語フォント名も可）、`size_pt`
（フォントサイズ、pt単位）を指定できます。既定フォントは游明朝です。

未知の `type` を指定した要素はエラーにせずスキップされ、生成結果の
`warnings` にその旨が記録されます（後述）。

## 手順

1. ユーザーの依頼内容から上記JSON仕様を組み立てる。
2. `run_script` を上記引数で呼び出す。
3. 出力先が既に存在する場合は確認なく上書きされるため、既存ファイルの
   上書きになる可能性があるときは実行前にユーザーへ確認すること。
4. 成功時は `output_path` をユーザーへ伝える。`warnings` が空でなければ
   その内容（未知の type がスキップされた旨など）も併せて伝える。

## 出力例

```json
{"output_path": "C:\\foo\\report.docx", "blocks_written": 8, "warnings": []}
```

## エッジケース

- 出力先の拡張子が `.docx` でない、`--data`/`--data-file` の両方を
  指定・両方省略、渡したJSON文字列がパース不能、`table.rows` が空、
  `image.path` が存在しないファイル、のいずれもエラーメッセージ＋終了コード1。
  内容をそのままユーザーに伝えること。
- 出力先が既に存在する場合は確認なく上書きされます（`pdf-tools`/`pptx-create`
  と同じ挙動）。上書きしてよいか事前にユーザーへ確認するとよいです。
- 出力先の親ディレクトリが存在しない場合は自動的に作成されます。
- ページ番号（`page_number: true`）はWordの「フィールド」機能を使うため、
  PDF等に変換しない限り、Word上で一度開かれるかフィールド更新（F9）される
  までは数字が反映されない場合があります。
- 依存パッケージ `python-docx`（import名は `docx`）が実行環境に無いと
  `ModuleNotFoundError` で終了コード非0になります。その場合は導入者へ
  `pip install python-docx` の実施を促してください。

## 生成後の確認・既存文書の編集

生成した文書のレイアウトを画像で確認したい場合は `docx-render` スキルの
`render_docx.py` + `analyze_image` を使ってください。既存のdocxファイルを
部分的に編集したい場合はこのスキルではなく `docx-edit` を使ってください
（このスキルは新規作成専用で `--new` のような追記モードは持ちません）。

## パスメモリー（`@N`）

`create_docx.py` が生成したファイルは、出力JSONに `path_memory`
（例: `{"@12": "C:\\foo\\report.docx"}`）として自動登録されます。続けて
`run_script` を呼ぶ場合、絶対パスの代わりにその `@N` を `script_args` に
そのまま渡せます（自動的に実パスへ解決されます）。
