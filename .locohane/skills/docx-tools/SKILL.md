---
name: docx-tools
description: Word文書（.docx）の読み込み（段落・表・文書プロパティ・変更履歴有無の取得）、新規docxファイルの生成（見出し・段落・箇条書き・表・画像・ページ設定・ヘッダーフッター・ページ番号などを含む本格的な文書作成）、既存Word文書の編集（文字列の検索置換、段落の末尾追加・削除、Track Changes/変更履歴つきの検索置換、変更履歴の一括確定・却下）を行う。ユーザーがWord文書の内容を確認したいとき、docxの文章を要約・検索したいとき、レポートや議事録、案内文書などをWordファイルとして出力・保存したいとき、既存のWordファイルの文言を修正・追記したいとき、変更履歴（校閲）付きで修正を提案したいときに使う。.doc（レガシーのバイナリ形式）の読み込みには対応していない。
license: MIT
metadata:
  author: ytakamoto
  version: "1.1"
---

# docx-tools

Word文書（.docx）の読み込み・生成・編集を行うスキルです。3つのスクリプトを
`run_script` ツールで実行して結果を得ます。

このスキルは `.docx`（Word 2007以降のXML形式）のみを扱います。**`.doc`
（レガシーのバイナリ形式）は読み込み・生成のどちらにも対応していません。**
ユーザーが `.doc` ファイルを渡してきた場合は、Microsoft Wordで開いて
「名前を付けて保存」から `.docx` 形式に変換してもらうよう案内してください。

各スクリプトは正常系なら終了コード0でJSON1行を標準出力へ、異常系なら
終了コード非0でエラーメッセージを標準エラーへ出力します。

## 1. read_docx.py — docxの読み込み（段落・表・プロパティ取得）

呼び出し例:
```json
{
    "skill_name": "docx-tools",
    "script_filename": "read_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--offset", "0", "--limit", "300"]
}
```
`--offset`/`--limit` は省略可（既定 offset=0, limit=300）。段落単位のページングです（`read_file.py` の行番号版と
同じ考え方）。表（`tables`）は既定ですべて返します。

出力例:
```json
{
  "path": "C:\\foo\\report.docx",
  "total_paragraphs": 450,
  "start_index": 0,
  "end_index": 299,
  "paragraphs": [
    {"index": 0, "style": "Title", "text": "報告書タイトル"},
    {"index": 1, "style": "Heading 1", "text": "1. 概要"},
    {"index": 2, "style": "Normal", "text": "本文テキスト..."}
  ],
  "table_count": 1,
  "tables": [[["項目", "値"], ["売上", "1200万円"]]],
  "core_properties": {"title": "報告書", "author": "山田太郎", "created": "2026-01-10T09:00:00", "modified": null},
  "track_changes": {"has_pending_revisions": false, "insertion_count": 0, "deletion_count": 0}
}
```

- `paragraphs` の各要素の `style` は見出しレベルの判定に使えます
  （`"Title"`, `"Heading 1"`, `"Heading 2"`, ... , `"Normal"` など）。
  `text` を `style` に応じて章立てとしてユーザーに報告してください。
- `tables` は「表1つ＝行の配列（各行はセル文字列の配列）」です。
- `total_paragraphs` が `end_index + 1` より多い場合は続きがあります。
  `--offset` を `end_index + 1` に指定して再度呼び出すことを案内してください。
- `track_changes.has_pending_revisions` が `true` の場合、文書内に未確定の
  変更履歴（`w:ins`/`w:del`）があります。`edit_docx.py` の `find_replace` は
  この変更履歴内のテキストを検出できないため、編集前に
  `accept_all_changes`/`reject_all_changes` で確定させるべきかユーザーに
  確認するとよいです。

### エッジケース

- ファイル不在・ディレクトリ指定はエラー終了します。
- 拡張子が `.doc` の場合は専用のエラーメッセージ（上記の変換案内）を返して
  終了コード1になります。`.docx`/`.doc` 以外の拡張子もエラー終了します。
- 壊れた `.docx`（ZIP/XMLとして不正）はエラー終了します。
- パスワード保護された `.docx` は python-docx が開けずエラー終了します。

## 2. create_docx.py — JSON仕様からdocxを新規生成

このプロジェクトには汎用のファイル書き込みツールが無いため、文書の内容は
LLM自身が組み立てたJSON文字列を **`--data` 引数にそのまま渡す**ことで
生成します（ユーザーがあらかじめJSONファイルを用意している場合のみ
`--data-file` でパス指定も可）。`pptx-tools` の `create_pptx.py` と同じ設計です。

呼び出し例:
```json
{
    "skill_name": "docx-tools",
    "script_filename": "create_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--data", "{\"blocks\": [{\"type\": \"heading\", \"text\": \"1. 概要\", \"level\": 1}, {\"type\": \"paragraph\", \"text\": \"本文です。\"}]}"]
}
```
`--data` の値は下記「JSON仕様の形式」に従うJSON文字列です。JSONが長くなる
場合は `--data` の代わりに `["<出力先パス.docx>", "--data-file", "<JSON仕様ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。

### JSON仕様の形式

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

#### blocks の各 type

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

### 手順

1. ユーザーの依頼内容から上記JSON仕様を組み立てる。
2. `run_script` を上記引数で呼び出す。
3. 出力先が既に存在する場合は確認なく上書きされるため、既存ファイルの
   上書きになる可能性があるときは実行前にユーザーへ確認すること。
4. 成功時は `output_path` をユーザーへ伝える。`warnings` が空でなければ
   その内容（未知の type がスキップされた旨など）も併せて伝える。

### 出力例

```json
{"output_path": "C:\\foo\\report.docx", "blocks_written": 8, "warnings": []}
```

### エッジケース

- 出力先の拡張子が `.docx` でない、`--data`/`--data-file` の両方を
  指定・両方省略、渡したJSON文字列がパース不能、`table.rows` が空、
  `image.path` が存在しないファイル、のいずれもエラーメッセージ＋終了コード1。
  内容をそのままユーザーに伝えること。
- 出力先が既に存在する場合は確認なく上書きされます（`pdf-tools`/`pptx-tools`
  と同じ挙動）。上書きしてよいか事前にユーザーへ確認するとよいです。
- 出力先の親ディレクトリが存在しない場合は自動的に作成されます。
- ページ番号（`page_number: true`）はWordの「フィールド」機能を使うため、
  PDF等に変換しない限り、Word上で一度開かれるかフィールド更新（F9）される
  までは数字が反映されない場合があります。

## 3. edit_docx.py — 既存docxの編集（検索置換・段落追加削除・Track Changes）

既存の `.docx` ファイルを読み込み、`ops`（操作の配列）を順番に適用して保存します。
新規作成はこのスクリプトではなく `create_docx.py` を使ってください
（`edit_docx.py` は既存ファイル専用で `--new` は持ちません）。

呼び出し例:
```json
{
    "skill_name": "docx-tools",
    "script_filename": "edit_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--ops-json", "[{\"op\": \"find_replace\", \"old_text\": \"株式会社A\", \"new_text\": \"株式会社B\"}]"]
}
```
`--ops-json` の値は下記「ops の形式」に従うJSON配列を1行の文字列にしたものです。
JSONが長くなる場合は `--ops-json` の代わりに `["<編集対象.docxの絶対パス>", "--ops-file", "<JSON配列を書いたファイルの絶対パス>"]`
を使う（`--ops-json`/`--ops-file` はどちらか一方を必ず指定）。別名保存したい
場合は `script_args` に `"--output", "<保存先パス>"` を追加する（省略時は編集対象へ上書き）。

### 手順（推奨フロー）

1. 編集前に `read_docx.py` で対象文書の段落indexや `track_changes.has_pending_revisions`
   を確認する。
2. `has_pending_revisions` が `true` の場合、そのまま編集を続けると
   `find_replace` が既存の変更履歴内テキストを検出できないことがある旨を
   ユーザーへ伝え、必要なら `accept_all_changes`/`reject_all_changes` で
   確定させてから編集するか確認する。
3. `ops` 配列を組み立てて `edit_docx.py` を実行する。
4. `--output` を省略した場合は編集対象へ上書きされるため、事前にユーザーへ確認する。

### ops の形式

トップレベルは `ops` の配列そのものです（1回の呼び出しに複数opをまとめてよい）。

```json
[
  {"op": "find_replace", "old_text": "株式会社A", "new_text": "株式会社B", "track_changes": true, "author": "山田太郎"},
  {"op": "find_replace", "old_text": "旧価格: 1000円", "new_text": "新価格: 1200円", "paragraph_index": 12, "occurrence": "first"},
  {"op": "append_block", "block": {"type": "heading", "text": "4. 追記事項", "level": 1}},
  {"op": "append_block", "block": {"type": "paragraph", "text": "本追記は2026年7月13日付で追加されました。"}},
  {"op": "delete_paragraph", "index": 25},
  {"op": "accept_all_changes"},
  {"op": "reject_all_changes"}
]
```

| op | 必須キー | 主な省略可キー | 説明 |
|---|---|---|---|
| `find_replace` | `old_text` | `new_text`（既定`""`）、`track_changes`（既定`false`）、`paragraph_index`（既定=全段落を対象）、`occurrence`（`"all"`既定/`"first"`）、`author`、`date` | 段落内テキストの検索置換 |
| `append_block` | `block`（`create_docx.py` の `blocks` 要素と同一形式） | なし | 文書末尾にブロックを追加（常に非トラッキング） |
| `delete_paragraph` | `index` | なし | 指定indexの段落を削除（非トラッキングのみ） |
| `accept_all_changes` | なし | なし | 文書内の全 `w:ins`/`w:del` を確定（採用） |
| `reject_all_changes` | なし | なし | 文書内の全 `w:ins`/`w:del` を却下（元に戻す） |

`append_block` の `block` は `create_docx.py` の `blocks` 配列の要素と同じ
形式（`heading`/`paragraph`/`bullet_list`/`number_list`/`table`/`image`/`page_break`）
が使えます。詳細は「2. create_docx.py」の `blocks` の各 type 表を参照してください。

### find_replace の制約（重要）

- `old_text` は「1つのrun」に収まっている必要があります。Word文書内では
  1つの段落が複数のrun（書式の異なる文字の塊）に分割されていることが多く、
  書式が混在する文字列の中間や、複数runにまたがる文字列は検出できません。
  検出できない場合はエラーになるので、`old_text` を短くする、`read_docx.py`
  で段落テキストを確認して検索範囲を調整する、といった対応を行ってください。
- 既存の未確定Track Changes（`w:ins`/`w:del`）内のテキストは検索対象外です。
- タブ・改行・フィールドを含むrunは検索対象外です。
- `occurrence: "first"` を指定しない限り、対象段落内の一致箇所はすべて
  置換されます。

### Track Changesについて

- `track_changes: true` が効くのは `find_replace` のみです。`append_block`/
  `delete_paragraph` は常に非トラッキング（確定済みの変更として書き込まれる）。
- `author`/`date` を省略すると `author="AI Agent"`、`date` は実行時刻
  （UTC・ISO8601）になります。
- 生成された変更履歴はMicrosoft Wordの「校閲」タブで表示・個別に承認/却下
  できます。一括で確定/取り消ししたい場合は `accept_all_changes`/
  `reject_all_changes` を使ってください。
- `accept_all_changes`/`reject_all_changes` は表内の変更履歴も対象になります
  （文書body配下を再帰的に走査するため）。ヘッダー/フッターは対象外です。
- 表の行/列単位の変更履歴、段落プロパティ変更、書式のみの変更、移動
  （`moveFrom`/`moveTo`）には対応していません。これらはWord自身が付与した
  変更履歴を含む文書を開いた場合にのみ発生しうるもので、本スキル自身が
  生成する変更履歴は常にrun単位の `w:ins`/`w:del` のみのため影響しません。

### 段落マーク削除（段落統合）について

`delete_paragraph` は `track_changes` に対応していません（段落マーク自体の
削除・前後の段落統合はOOXML上複雑なため非対応）。変更履歴として段落を
「消したい」場合は、段落自体を削除するのではなく、段落内の全文を
`find_replace`（`track_changes: true`）で空文字へ置換することで代替して
ください（段落マークは残りますが、Word上は取り消し線付きの削除として
表示されます）。

複数の `delete_paragraph` を1回の `ops` に含める場合、削除するたびに後続の
indexがずれるため、**大きいindexから先に削除する**よう指定してください
（例: index 30 と index 10 を両方削除したい場合は 30 を先に指定する）。

### 出力例

```json
{
  "path": "C:\\foo\\report.docx",
  "paragraph_count": 44,
  "table_count": 2,
  "applied_ops": 3,
  "op_results": [
    {"op": "find_replace", "replaced_count": 1},
    {"op": "append_block", "block_type": "heading"},
    {"op": "delete_paragraph", "deleted_index": 25}
  ]
}
```

### エッジケース

- 対象ファイル不在・ディレクトリ指定・`.doc`拡張子・非`.docx`拡張子・
  壊れた`.docx`は `read_docx.py` と同様にエラーメッセージ＋終了コード1。
- `ops` がJSON配列でない、各要素に `op` キーが無い、`old_text` が見つからない、
  存在しない `index`/`paragraph_index`、未対応の `op`/`block.type`、
  `delete_paragraph` に `track_changes: true` を指定、のいずれもエラー
  メッセージ＋終了コード1。どのop（何番目・どの種別）が失敗したかが
  メッセージに含まれるので、そのまま報告するかopを修正して再実行すること。
- 一部の op が失敗した場合、それ以前に成功した op を含めて保存は行われません
  （途中まで適用された状態のまま元ファイルは無傷）。
- `--output` を省略すると編集対象へ上書き保存されます。別ファイルとして
  保存したい場合のみ `--output` を指定してください。

## エッジケース共通

- 依存パッケージ `python-docx`（import名は `docx`）が実行環境に無いと
  `ModuleNotFoundError` で終了コード非0になります。その場合は導入者へ
  `pip install python-docx` の実施を促してください。
- いずれのスクリプトも例外を投げず、エラーはstderr+終了コード非0で返します。
  `run_script` の戻り値テキストの `[標準エラー]` セクションを確認してください。
