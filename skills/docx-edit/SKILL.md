---
name: docx-edit
description: 既存のWord文書（.docx）を編集するスキル。文字列の検索置換、段落の末尾追加・削除、Track Changes（変更履歴）つきの検索置換、変更履歴の一括確定・却下ができる。ユーザーが見た目の変更を明示的に頼んできた場合のみ、既存段落・表の再配色・文字装飾・配置（左右中央揃え等）・インデント・行間を変えるset_paragraph_style/set_table_styleも使える。既存のWordファイルの文言を修正・追記したいとき、変更履歴（校閲）付きで修正を提案したいときに使う。新規作成は`docx-create`、編集前の内容確認は`docx-read`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# docx-edit

既存の `.docx` ファイルを読み込み、`ops`（操作の配列）を順番に適用して保存する
スキルです。`edit_docx.py` を `run_script` ツールで実行して結果を得ます。
新規作成はこのスキルではなく `docx-create` を使ってください（`edit_docx.py`
は既存ファイル専用で `--new` は持ちません）。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "docx-edit",
    "script_filename": "edit_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--ops-json", "[{\"op\": \"find_replace\", \"old_text\": \"株式会社A\", \"new_text\": \"株式会社B\"}]"]
}
```
`--ops-json` の値は下記「ops の形式」に従うJSON配列を1行の文字列にしたものです。
JSONが長くなる場合は `--ops-json` の代わりに `["<編集対象.docxの絶対パス>", "--ops-file", "<JSON配列を書いたファイルの絶対パス>"]`
を使う（`--ops-json`/`--ops-file` はどちらか一方を必ず指定）。別名保存したい
場合は `script_args` に `"--output", "<保存先パス>"` を追加する（省略時は編集対象へ上書き）。

## 手順（推奨フロー）

1. 編集前に `docx-read` スキルの `read_docx.py` で対象文書の段落indexや
   `track_changes.has_pending_revisions` を確認する。
2. `has_pending_revisions` が `true` の場合、そのまま編集を続けると
   `find_replace` が既存の変更履歴内テキストを検出できないことがある旨を
   ユーザーへ伝え、必要なら `accept_all_changes`/`reject_all_changes` で
   確定させてから編集するか確認する。
3. `ops` 配列を組み立てて `edit_docx.py` を実行する。
4. `--output` を省略した場合は編集対象へ上書きされるため、事前にユーザーへ確認する。

## ops の形式

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
| `append_block` | `block`（`docx-create` の `blocks` 要素と同一形式） | `theme`（`callout`のみ使用。既定`charcoal`） | 文書末尾にブロックを追加（常に非トラッキング） |
| `set_paragraph_style` | `index` | `role`(`heading`/`callout`)+`theme`、または`color`/`bold`/`italic`/`underline`/`font`/`size_pt`/`fill_color`/`border_color`/`align`(`left`/`center`/`right`/`justify`)/`indent_left_cm`/`line_spacing` | 既存段落を明示的に再配色・再配置（下記参照） |
| `set_table_style` | `table_index` | `theme`、または`header_fill`/`header_font_color`、`row`/`all_rows` | 既存の表の行（既定は見出し行=1行目）を明示的に再配色（下記参照） |
| `delete_paragraph` | `index` | なし | 指定indexの段落を削除（非トラッキングのみ） |
| `accept_all_changes` | なし | なし | 文書内の全 `w:ins`/`w:del` を確定（採用） |
| `reject_all_changes` | なし | なし | 文書内の全 `w:ins`/`w:del` を却下（元に戻す） |

`append_block` の `block` は `docx-create` の `blocks` 配列の要素と同じ形式
（`heading`/`paragraph`/`bullet_list`/`number_list`/`table`/`image`/`callout`/
`page_break`）が使えます。**ただし`docx-create`と違い、`heading`/`table`は
`theme`があっても配色しません**（この文書自身が持つWordの組み込みスタイルの
色をそのまま尊重するため）。既存文書に追記する内容が、文書側に既に確立した
見た目（見出しスタイル等）を持っている以上、それを上書きしないのが既定動作です。
唯一 `callout` はWordに対応するスタイルが無い新規概念なので、`theme`
（`charcoal`/`navy`/`forest`/`coral`/`terracotta`/`ocean`/`teal`/`berry`、
省略時`charcoal`）で配色されます。

## set_paragraph_style / set_table_style（既存デザイン・配置の明示的な変更）

`find_replace`/`append_block`/`delete_paragraph`は文字の中身だけを扱い、見た目には
一切触れません。**ユーザーが「もっと格好よく」「見やすく」「中央揃えにして」
「インデントを付けて」等、見た目・配置の変更を明示的に頼んできた場合にのみ**、
この2つのopで既存の段落・表を再配色・再配置してください。頼まれていないのに
先回りして呼ばないこと。

このopで「レイアウトを直して」系の要望はひととおりカバーできます：文字色・
背景色・左罫線色・太字/斜体/下線・フォント種類・フォントサイズ・文字揃え・
左インデント・行間（`set_paragraph_style`）と、表の任意行の再配色
（`set_table_style`）。それでも対応できない依頼（列幅、ページ余白・用紙サイズ、
表全体の罫線、複数段落への一括適用）は、この節末尾の「非対応」を参照し、
できない旨をユーザーに伝えてください。

```json
[
  {"op": "set_paragraph_style", "index": 3, "role": "heading", "theme": "navy"},
  {"op": "set_paragraph_style", "index": 5, "role": "callout", "theme": "navy"},
  {"op": "set_paragraph_style", "index": 7, "align": "center"},
  {"op": "set_paragraph_style", "index": 8, "indent_left_cm": 1.0, "line_spacing": 1.5},
  {"op": "set_table_style", "table_index": 0, "theme": "navy"},
  {"op": "set_table_style", "table_index": 0, "theme": "navy", "all_rows": true}
]
```

- `set_paragraph_style`: `index`（`doc.paragraphs`の0始まりindex、`find_replace`の
  `paragraph_index`と同じ数え方）を対象に、`role`(`"heading"`=文字色を`theme`の
  primary色＋太字／`"callout"`=`theme`のsecondary色で背景を塗りprimary色の左罫線)
  ＋`theme`、または個別キー（`color`/`bold`/`italic`/`underline`/`font`/`size_pt`/
  `fill_color`/`border_color`/`align`/`indent_left_cm`/`line_spacing`）で再配色・
  再配置する。個別キーは`role`由来の既定値より優先。**1回の呼び出しで対象にできる
  段落は1つ**なので、複数段落に同じ変更をしたい場合はopを複数並べる。
- `set_table_style`: `table_index`（`doc.tables`の0始まりindex）の表を対象に、
  `theme`または`header_fill`/`header_font_color`で再配色する。既定では**見出し行
  （1行目）のみ**（太字＋見出し用フォントも付与）。`row`（0始まり行番号を1つ指定）
  または`all_rows: true`を指定すると対象行を変えられる（この場合は塗り・文字色の
  みで太字強制はしない）。
- どちらも `theme`/スタイルキーが何も無い呼び出しはエラーになります。

**非対応（現状のop群でできないこと）**: 列幅・行高、ページ余白・用紙サイズ・
向き、表本体の罫線（見出しセルの網掛けのみ対応）、ヘッダー/フッター、画像の
挿入位置調整、複数段落への一括スタイル適用（1opにつき1段落）。

## find_replace の制約（重要）

- `old_text` は「1つのrun」に収まっている必要があります。Word文書内では
  1つの段落が複数のrun（書式の異なる文字の塊）に分割されていることが多く、
  書式が混在する文字列の中間や、複数runにまたがる文字列は検出できません。
  検出できない場合はエラーになるので、`old_text` を短くする、`read_docx.py`
  で段落テキストを確認して検索範囲を調整する、といった対応を行ってください。
- 既存の未確定Track Changes（`w:ins`/`w:del`）内のテキストは検索対象外です。
- タブ・改行・フィールドを含むrunは検索対象外です。
- `occurrence: "first"` を指定しない限り、対象段落内の一致箇所はすべて
  置換されます。

## Track Changesについて

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

## 段落マーク削除（段落統合）について

`delete_paragraph` は `track_changes` に対応していません（段落マーク自体の
削除・前後の段落統合はOOXML上複雑なため非対応）。変更履歴として段落を
「消したい」場合は、段落自体を削除するのではなく、段落内の全文を
`find_replace`（`track_changes: true`）で空文字へ置換することで代替して
ください（段落マークは残りますが、Word上は取り消し線付きの削除として
表示されます）。

複数の `delete_paragraph` を1回の `ops` に含める場合、削除するたびに後続の
indexがずれるため、**大きいindexから先に削除する**よう指定してください
（例: index 30 と index 10 を両方削除したい場合は 30 を先に指定する）。

## 出力例

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

## エッジケース

- 対象ファイル不在・ディレクトリ指定・`.doc`拡張子・非`.docx`拡張子・
  壊れた`.docx`は `read_docx.py` と同様にエラーメッセージ＋終了コード1。
- `ops` がJSON配列でない、各要素に `op` キーが無い、`old_text` が見つからない、
  存在しない `index`/`paragraph_index`/`table_index`/`set_table_style`の`row`、
  未対応の `op`/`block.type`/`theme`/`role`/`align`、
  `set_paragraph_style`/`set_table_style`でスタイルキーが1つも無い、
  `delete_paragraph` に `track_changes: true` を指定、
  のいずれもエラー
  メッセージ＋終了コード1。どのop（何番目・どの種別）が失敗したかが
  メッセージに含まれるので、そのまま報告するかopを修正して再実行すること。
- 一部の op が失敗した場合、それ以前に成功した op を含めて保存は行われません
  （途中まで適用された状態のまま元ファイルは無傷）。
- `--output` を省略すると編集対象へ上書き保存されます。別ファイルとして
  保存したい場合のみ `--output` を指定してください。
- 依存パッケージ `python-docx`（import名は `docx`）が実行環境に無いと
  `ModuleNotFoundError` で終了コード非0になります。その場合は導入者へ
  `pip install python-docx` の実施を促してください。

## 編集後の確認

編集結果のレイアウトを画像で確認したい場合は `docx-render` スキルの
`render_docx.py` + `analyze_image` を使ってください。

## パスメモリー（`@N`）

`edit_docx.py` が更新したファイルは、出力JSONに `path_memory`
（例: `{"@12": "C:\\foo\\report.docx"}`）として自動登録されます。続けて
`run_script` を呼ぶ場合、絶対パスの代わりにその `@N` を `script_args` に
そのまま渡せます（自動的に実パスへ解決されます）。
