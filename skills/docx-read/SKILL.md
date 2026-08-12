---
name: docx-read
description: Word文書（.docx）の読み込み専用スキル。段落・表・文書プロパティ（タイトル・著者・作成日等）・Track Changes（変更履歴）の有無を取得する。Excel等と異なりアプリケーション本体は不要。ユーザーがWord文書の内容を確認・要約・検索したいとき、文書の章立てや表の中身を把握したいとき、編集前に既存の変更履歴の有無を確認したいときに使う。.doc（レガシーのバイナリ形式）の読み込みには対応していない。新規作成は`docx-create`、既存文書の編集は`docx-edit`、レイアウトを画像で確認したい場合は`docx-render`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# docx-read

Word文書（`.docx`）を読み込み、段落・表・文書プロパティ・Track Changes（変更履歴）の
有無を取得するスキルです。`read_docx.py` を `run_script` ツールで実行して結果を得ます。

このスキルは `.docx`（Word 2007以降のXML形式）のみを扱います。**`.doc`
（レガシーのバイナリ形式）は読み込みに対応していません。** ユーザーが `.doc`
ファイルを渡してきた場合は、Microsoft Wordで開いて「名前を付けて保存」から
`.docx` 形式に変換してもらうよう案内してください。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

本文データ（段落・表）は直接標準出力へは返さず、一時JSONファイルへ書き出して
そのパス（`result_path`）を返します。中身を確認するには `Read` ツールで
`result_path`（または `path_memory` の `@N`）を読んでください（内容は複数行に
整形されているため `offset`/`limit` で部分読み込みできます）。

## read_docx.py — docxの読み込み（段落・表・プロパティ取得）

呼び出し例:
```json
{
    "skill_name": "docx-read",
    "script_filename": "read_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--offset", "0", "--limit", "300"]
}
```
`--offset`/`--limit` は省略可（既定 offset=0, limit=300）。段落単位のページングです
（`read_file.py` の行番号版と同じ考え方）。表（`tables`）は既定ですべて返します。

出力例:
```json
{
  "path": "C:\\foo\\report.docx",
  "total_paragraphs": 450,
  "start_index": 0,
  "end_index": 299,
  "paragraphs_count": 300,
  "table_count": 1,
  "tables_count": 1,
  "body_order_count": 452,
  "core_properties": {"title": "報告書", "author": "山田太郎", "created": "2026-01-10T09:00:00", "modified": null},
  "track_changes": {"has_pending_revisions": false, "insertion_count": 0, "deletion_count": 0},
  "result_path": "C:\\...\\_tmp_<thread_id>\\docx_read\\1a2b3c4d_20260805_153012_123456.json",
  "path_memory": {"@7": "C:\\...\\_tmp_<thread_id>\\docx_read\\1a2b3c4d_20260805_153012_123456.json"}
}
```

段落本文（`paragraphs`、各要素は `{"index", "style", "text"}`）と表本体
（`tables`、「表1つ＝行の配列（各行はセル文字列の配列）」）は上記のように
標準出力からは省かれ、`result_path` が指すJSONファイルにのみ含まれます。
`Read` ツールで `result_path` を読んで内容を確認してください。

- `paragraphs` の各要素の `style` は見出しレベルの判定に使えます
  （`"Title"`, `"Heading 1"`, `"Heading 2"`, ... , `"Normal"` など）。
  `text` を `style` に応じて章立てとしてユーザーに報告してください。
- `total_paragraphs` が `end_index + 1` より多い場合は続きがあります。
  `--offset` を `end_index + 1` に指定して再度呼び出すことを案内してください。
- `track_changes.has_pending_revisions` が `true` の場合、文書内に未確定の
  変更履歴（`w:ins`/`w:del`）があります。`docx-edit` の `find_replace` は
  この変更履歴内のテキストを検出できないため、編集前に
  `accept_all_changes`/`reject_all_changes` で確定させるべきかユーザーに
  確認するとよいです。
- 標準出力の`body_order_count`は本文の段落＋表の要素数（上記出力例の件数）。本体の
  `body_order`（`[{"type":"paragraph"/"table","index":N}, ...]`）は、段落と表が
  文書内でどの順で交互に現れるかを表す。`paragraphs`/`tables`は別々のフラットな
  リストで返るため、これが無いと「表2の直前/直後にある段落はどれか」が分からない。
  表の近くに段落を追記したい、章立てと表の対応関係を把握したい、といった
  位置関係が必要な場面で使う（`paragraphs`/`tables`と同じく`result_path`側にのみ
  含まれる）。

### エッジケース

- ファイル不在・ディレクトリ指定はエラー終了します。
- 拡張子が `.doc` の場合は専用のエラーメッセージ（上記の変換案内）を返して
  終了コード1になります。`.docx`/`.doc` 以外の拡張子もエラー終了します。
- 壊れた `.docx`（ZIP/XMLとして不正）はエラー終了します。
- パスワード保護された `.docx` は python-docx が開けずエラー終了します。
- 依存パッケージ `python-docx`（import名は `docx`）が実行環境に無いと
  `ModuleNotFoundError` で終了コード非0になります。その場合は導入者へ
  `pip install python-docx` の実施を促してください。

## レイアウト・表・強調表現を画像で確認したい場合

テキスト抽出だけでは読み取れないレイアウト・表・画像の配置・強調表現が
知りたい場合は `docx-render` スキルの `render_docx.py` + `analyze_image` を使ってください。

## パスメモリー（`@N`）

`read_docx.py` が書き出す結果JSON（`result_path`）は、出力JSONに
`path_memory`（例: `{"@7": "C:\\...\\1a2b3c4d.json"}`）として自動登録されます。
続けて `run_script`/`Read` を呼ぶ場合、絶対パスの代わりにその `@N` を
そのまま渡せます（自動的に実パスへ解決されます）。
