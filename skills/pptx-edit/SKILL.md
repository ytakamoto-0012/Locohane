---
name: pptx-edit
description: 既存のPowerPointテンプレート（.pptx）のテーマ・マスター・レイアウトのデザインを保ったまま部分編集するスキル。テキスト・表・発表者ノートの差し替え、スライドの複製・削除・並び替え、画像の差し替えができる。既存のPowerPointテンプレート（社内フォーマット等）を流用して一部だけ差し替えたいとき、スライドを複製・削除・並び替えしたいときに使う。実行前に必ず`pptx-inspect`でshape_indexを把握すること。デザインを保たない単純な新規生成は`pptx-create`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pptx-edit

既存のpptx（社内テンプレート等）のテーマ・マスター・レイアウトのデザインを保ったまま、
指定したスライドの中身だけを書き換える／スライドを複製・削除・並び替えるスキルです。
`edit_pptx.py` を `run_script` ツールで実行して結果を得ます。**必ず `template_path`
とは別の `output_path` に保存され**、テンプレート自体は変更されません（同じパスを
指定した場合は後述の通りエラーになります）。

編集前に**必ず** `pptx-inspect` スキルの `inspect_pptx.py` でスライド構造を確認し、
`shape_index` を把握してください（推測で `shape_index` を組み立てない）。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "pptx-edit",
    "script_filename": "edit_pptx.py",
    "script_args": ["C:\\Users\\me\\template.pptx", "C:\\Users\\me\\edited.pptx", "--data", "{\"operations\": [{\"op\": \"set_title\", \"slide\": 2, \"text\": \"更新後サマリ\"}]}"]
}
```
`--data` の値は下記「JSON操作列スキーマ」に従うJSON文字列です。JSONが長い
場合は `--data` の代わりに `["<テンプレート>", "<出力先>", "--data-file", "<操作列JSONを書いたUTF-8ファイルの絶対パス>"]`
を使う（`--data`/`--data-file` はどちらか一方を必ず指定。両方指定・両方省略はエラー）。
テンプレートと同じパスへ保存したい場合のみ `script_args` に `"--overwrite"` を追加する
（**ユーザーに上書きしてよいか確認してから**付けること）。

## JSON操作列スキーマ

トップレベルは `{"operations": [...]}` で、各要素が1つの操作です。**配列の先頭から順に**
1つのPresentationへ適用されるため、`duplicate_slide`/`delete_slide`/`reorder_slides` を使うと
それ以降の操作で参照するスライド番号がずれます（下記`slide`キーの注記を参照）。

すべての操作の `slide` キーは1始まりで、**その操作を適用する時点での**スライド番号を指します。

| op | 主なキー | 内容 |
|---|---|---|
| `set_title` | `slide`, `text` | タイトルプレースホルダのテキストを差し替え |
| `set_text` | `slide`, `shape_index`, `bullets` | 任意shapeのテキストを差し替え（`bullets`は`pptx-create`と同じ、文字列または`{"text":..,"level":..}`のリスト） |
| `set_table_cell` | `slide`, `shape_index`, `row`, `col`, `text` | 既存表の1セルを差し替え（row/colは0始まり、ヘッダー行も含む） |
| `set_table` | `slide`, `shape_index`, `headers`, `rows` | 既存表を丸ごと差し替え。**既存表と行数・列数が完全一致する場合のみ**可能（python-pptxは既存表の行列数の増減に非対応。行列数を変えたい場合は`pptx-create`で新規スライドとして作る） |
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

## duplicate_slide の非対応shape

`duplicate_slide` は複製元スライドにチャート・SmartArt・動画・OLEオブジェクト・グループ図形
（`shape_type`が`CHART`/`DIAGRAM`/`IGX_GRAPHIC`/`MEDIA`/`WEB_VIDEO`/`EMBEDDED_OLE_OBJECT`/
`LINKED_OLE_OBJECT`/`OLE_CONTROL_OBJECT`/`GROUP`のいずれか）が含まれる場合、
壊れたpptxを生成しないよう**エラー終了**します（`pptx-inspect`の`shape_type`で事前確認可能）。
プレースホルダ・テキストボックス・表・画像（`PICTURE`）は複製に対応しています。

## エッジケース

- `template_path` と `output_path` が同じで `--overwrite` 未指定の場合はエラー終了します。
- `--data` と `--data-file` を両方指定、または両方省略した場合はエラー終了します。
- 存在しない `slide` / `shape_index`、shape種別が合わない操作（例: 表でないshapeへの
  `set_table_cell`）、`set_table` の行列数不一致、`reorder_slides` の順列が現在の全スライド数と
  不一致、`replace_picture` の `image_path` 不在は、いずれもその操作番号を添えてエラー終了します。
- 操作の途中でエラーになった場合、`output_path` へのファイル保存は行われません
  （テンプレート自体もその場では変更されないため、途中失敗しても既存ファイルへの影響はありません）。
- 未対応の `op` 値を指定した場合、対応一覧を添えてエラー終了します。
- 依存パッケージ `python-pptx` が実行環境に入っていないと `ModuleNotFoundError` で
  終了コード非0になります。その場合は導入者へ `pip install python-pptx` の実施を促してください。

## 編集後の確認

編集結果のレイアウトを画像で確認したい場合は `pptx-render` スキルの
`render_pptx.py` + `analyze_image` を使ってください。

## パスメモリー（`@N`）

`edit_pptx.py` が生成したファイルは、出力JSONに `path_memory`
（例: `{"@12": "C:\\foo\\edited.pptx"}`）として自動登録されます。続けて
`run_script` を呼ぶ場合、絶対パスの代わりにその `@N` を `script_args` に
そのまま渡せます（自動的に実パスへ解決されます）。
