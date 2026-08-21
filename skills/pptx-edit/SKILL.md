---
name: pptx-edit
description: 既存のPowerPointテンプレート（.pptx）のテーマ・マスター・レイアウトのデザインを保ったまま部分編集するスキル。テキスト・表・発表者ノートの差し替え、スライドの複製・削除・並び替え、画像の差し替えができる。ユーザーが見た目の変更を明示的に頼んできた場合のみ、既存shapeの再配色・文字装飾・配置（左右中央揃え等）を変えるset_shape_style、図形の位置・サイズを変えるset_shape_positionも使える。既存のPowerPointテンプレート（社内フォーマット等）を流用して一部だけ差し替えたいとき、スライドを複製・削除・並び替えしたいときに使う。実行前に必ず`pptx-inspect`でshape_indexを把握すること。デザインを保たない単純な新規生成は`pptx-create`を使う。
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
1つのPresentationへ適用されます。

**`slide`キーは常に「このedit_pptx.py呼び出しを開始した時点（＝直前のpptx-inspectが見せていた状態）のスライド番号」を指します。** `duplicate_slide`/`delete_slide`を同一バッチ内で使って後続スライド番号がずれても、ツール側が自動的に追跡するため、以降の操作で「何番ズレたか」を手計算する必要はありません（`pptx-inspect`で確認した番号をそのままどのopにも使えます）。バッチ開始後に削除されたスライド番号を参照するとエラーになります。

例外は次の2つで、これらは「今現在どこに配置するか」を指定する操作のため、**その操作を適用する時点でのライブなスライド番号**を指します（バッチ開始時点の番号ではありません）:
- `duplicate_slide`の`insert_after`（省略時は複製元スライドの現在のライブ位置の直後に自動設定される）
- `reorder_slides`の`order`（`duplicate_slide`が作った新規スライドにはバッチ開始時点の番号が存在しないため、現在の全スライドを対象にせざるを得ない）

`delete_slide`/`duplicate_slide`と`reorder_slides`を同一バッチで併用する場合は、`reorder_slides`を操作列の最後に置くことを推奨します（構造変更を全て終えてから並び替える方が意図が明確になるため）。

| op | 必須キー | 主な省略可キー | 内容 |
|---|---|---|---|
| `set_title` | `slide` | `text`（省略時`""`＝空文字にする） | タイトルプレースホルダのテキストを差し替え |
| `set_text` | `slide`, `shape_index`, `bullets` | なし | 任意shapeのテキストを差し替え（`bullets`は`pptx-create`と同じ、文字列または`{"text":..,"level":..}`のリスト） |
| `set_table_cell` | `slide`, `shape_index`, `row`, `col` | `text`（省略時`""`＝空文字にする） | 既存表の1セルを差し替え（row/colは0始まり、ヘッダー行も含む） |
| `set_table` | `slide`, `shape_index` | `headers`、`rows` | 既存表を丸ごと差し替え。**既存表と行数・列数が完全一致する場合のみ**可能（python-pptxは既存表の行列数の増減に非対応。行列数を変えたい場合は`pptx-create`で新規スライドとして作る）。`headers`省略時は必要列数を`rows[0]`の要素数から推定する（`headers`と`rows`が両方省略／空だと0行0列扱いになり、既存表と一致しない限りエラー） |
| `set_notes` | `slide` | `text`（省略時`""`＝空文字にする） | 発表者ノートを差し替え |
| `replace_picture` | `slide`, `shape_index`, `image_path` | なし | 既存画像shapeの位置・サイズを保ったまま画像だけ差し替え（差し替え後、z順序は最前面に移動する点に注意） |
| `add_picture` | `slide`, `image_path`, `left_cm`, `top_cm` | `width_cm`/`height_cm`（省略時は原寸、片方のみ指定でアスペクト比維持） | 新規画像をスライドへ追加（下記参照） |
| `add_chart` | `slide`, `type`, `left_cm`, `top_cm`, `width_cm`, `height_cm`, `categories`, `series` | `title`、`theme`、`show_data_labels`(既定true) | 新規グラフをスライドへ追加（下記参照） |
| `set_shape_style` | `slide`, `shape_index` | `role`(`heading`/`table_header`)+`theme`、または`text_color`/`bold`/`italic`/`underline`/`font_size_pt`/`font_name`/`fill_color`/`border_color`/`align`(`left`/`center`/`right`/`justify`)、表shapeは`row`/`all_rows` | 既存shapeを明示的に再配色・再装飾・文字配置変更（下記参照） |
| `set_shape_position` | `slide`, `shape_index` | `left_cm`/`top_cm`/`width_cm`/`height_cm`（cm単位、いずれか1つ以上） | 既存shapeの位置・サイズを変更（下記参照） |
| `duplicate_slide` | `slide` | `insert_after`(省略時は複製元スライドの現在のライブ位置と同じ), `count`(省略時1) | 指定スライドを同じレイアウトのまま複製し、`insert_after`の直後に`count`枚挿入。プレースホルダ・表・テキストボックス・画像を含めて複製できる |
| `delete_slide` | `slide` | なし | 指定スライドを削除 |
| `reorder_slides` | `order` | なし | **その時点での全スライド番号(1始まり)の順列**を渡し、その並びに変更（例: `[2,1,3]`で1番目と2番目を入替） |

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
{"output_path": "C:\\foo\\edited.pptx", "backup_path": null, "total_slides": 5, "size_bytes": 37998, "applied_operations": 4}
```
生成が終わったら `output_path` と `total_slides` をユーザーに伝えてください。

**`backup_path`（自動バックアップ）**: `output_path`に既にファイルが存在する場合
（`--overwrite`でテンプレートへ上書き保存する場合や、既存の`output_path`を
再利用する場合）、保存直前にタイムスタンプ付きで同じフォルダへコピーしてから
上書きする。コピー先の絶対パスが入る。バックアップ対象が無かった場合（新規パスへの
保存等）は`null`。

## duplicate_slide の非対応shape

`duplicate_slide` は複製元スライドにチャート・SmartArt・動画・OLEオブジェクト・グループ図形
（`shape_type`が`CHART`/`DIAGRAM`/`IGX_GRAPHIC`/`MEDIA`/`WEB_VIDEO`/`EMBEDDED_OLE_OBJECT`/
`LINKED_OLE_OBJECT`/`OLE_CONTROL_OBJECT`/`GROUP`のいずれか）が含まれる場合、
壊れたpptxを生成しないよう**エラー終了**します（`pptx-inspect`の`shape_type`で事前確認可能）。
プレースホルダ・テキストボックス・表・画像（`PICTURE`）は複製に対応しています。

## set_shape_style / set_shape_position（既存デザイン・配置の明示的な変更）

このスキルの他のopはすべて中身（テキスト・表の値・画像・スライド構成）だけを
差し替え、**見た目には一切触れません**（テンプレートのテーマ・マスター・レイアウトを
保つのがこのスキルの存在意義のため）。`set_shape_style`/`set_shape_position` だけが
唯一の例外で、**ユーザーが「もっと格好よく」「見やすく」「中央に寄せて」「大きく
して」等、見た目・配置の変更を明示的に頼んできた場合にのみ**使ってください。
頼まれていないのに先回りして呼ばないこと。

このopで「レイアウトを直して」系の要望はひととおりカバーできます：文字色・塗り・
枠線色・太字/斜体/下線・フォント種類・フォントサイズ・文字揃え（`set_shape_style`）と、
図形自体の位置・サイズ（`set_shape_position`）。それでも対応できない依頼（表の罫線色、
影・グラデーション等の特殊効果、スライド背景色）は、この節末尾の「非対応」を参照し、
できない旨をユーザーに伝えてください。

```json
{"op": "set_shape_style", "slide": 2, "shape_index": 0, "role": "heading", "theme": "navy"}
{"op": "set_shape_style", "slide": 3, "shape_index": 2, "role": "table_header", "theme": "navy"}
{"op": "set_shape_style", "slide": 1, "shape_index": 1, "fill_color": "F2F2F2", "text_color": "1E2761", "bold": true, "align": "center"}
{"op": "set_shape_style", "slide": 1, "shape_index": 2, "italic": true, "underline": true, "font_name": "游ゴシック", "border_color": "1E2761"}
{"op": "set_shape_position", "slide": 1, "shape_index": 1, "left_cm": 2.5, "top_cm": 1.0}
{"op": "set_shape_position", "slide": 1, "shape_index": 1, "width_cm": 10, "height_cm": 6}
```

- `role`（省略可）: `"heading"`（`theme`のprimary色＋太字）または `"table_header"`
  （表shapeの見出し行に`theme`のprimary塗り＋text_on_primary文字色＋太字。
  表shape以外に指定するとエラー）。`theme`（`pptx-create`と同じ8種）と併用する。
- 個別指定: `text_color`/`bold`/`italic`/`underline`/`font_size_pt`/`font_name`（文字）、
  `fill_color`（塗り。テキストボックス等は shape 全体、表shapeは対象行のセル背景）、
  `border_color`（shapeの枠線色。**表shapeでは非対応**）、`align`（`left`/`center`/
  `right`/`justify`、段落の文字揃え）。`role`より優先される。
- 表shapeでは対象行を `role: "table_header"`（1行目のみ）／`row`（0始まり行番号
  を1つ指定）／`all_rows: true`（全行）のいずれかで決める。
- `set_shape_position` は `left_cm`/`top_cm`/`width_cm`/`height_cm` のうち指定した
  ものだけを変更し、省略したものは元の値のまま。`add_picture`/`add_chart`で
  追加した画像・グラフ（`shape_type`が`PICTURE`/`CHART`）にもそのまま使える
  （追加後に`pptx-inspect`で新しい`shape_index`を確認してから呼び出す）。
- `theme`のみ・スタイルキーが何も無い呼び出しはエラーになります。

**非対応（現状のop群でできないこと）**: 表shapeのセル罫線色、影・グラデーション・
反射等の図形効果、スライド背景色、行間・段落インデント、新規図形（テキスト
ボックス・オートシェイプ等）の追加、既存テンプレート内チャートの再配色
（新規追加は`add_chart`の`theme`指定で対応、既存チャートの色変更は非対応）。

## add_picture / add_chart（新規画像・グラフの追加）

```json
{"op": "add_picture", "slide": 2, "image_path": "C:\\img\\photo.png",
 "left_cm": 2.0, "top_cm": 3.0, "width_cm": 10.0, "height_cm": 6.0}
{"op": "add_chart", "slide": 3, "type": "bar",
 "left_cm": 2, "top_cm": 3, "width_cm": 20, "height_cm": 10,
 "categories": ["4月","5月","6月"],
 "series": [{"name": "売上", "values": [120, 135, 150]}],
 "title": "月次実績", "theme": "navy", "show_data_labels": true}
```

- `add_picture`: `left_cm`/`top_cm`は必須。`width_cm`/`height_cm`は両方省略で
  画像の原寸、片方のみ指定でアスペクト比を保ったまま他方を自動計算。
- `add_chart`: `type`は`bar`/`line`/`pie`のみ対応（**散布図は非対応**）。
  `categories`（カテゴリ名の配列）と`series`（`{"name","values"}`の配列、
  1件以上、各`values`の要素数は`categories`と一致必須）でデータを直接JSONに
  書く（pptxにはExcelのようなセル参照が無いため）。`show_data_labels`は
  既定`true`（`pie`は%、それ以外は値を表示）。
- `theme`を指定すると系列色が付く（`pie`は対象外）。**この系列色は`theme`の
  `primary`/`secondary`/`accent`ではなく、色覚多様性(CVD)に配慮した固定8色
  パレットを使う**（`primary`等3色循環だと4系列目以降で色が重複するため）。
  `theme`は主にheading等の他opとの記法統一のために受け付けているだけで、
  どのテーマ名を指定しても系列色そのものは変わらない。
- 追加した画像・グラフは`slide.shapes`に入るだけなので、`shape_index`の
  確認は既存どおり`pptx-inspect`を再実行すればよい（`add_picture`/`add_chart`
  専用の確認手順は無い）。
- `add_chart`で追加したチャートを含むスライドは`duplicate_slide`で複製
  できない（`UNSUPPORTED_DUPLICATE_TYPES`が`CHART`を含むため、他のチャート
  同様にエラーになる）。

## エッジケース

- `template_path` と `output_path` が同じで `--overwrite` 未指定の場合はエラー終了します。
- `--data` と `--data-file` を両方指定、または両方省略した場合はエラー終了します。
- 存在しない `slide` / `shape_index`、このバッチ内で既に`delete_slide`された`slide`を
  参照、shape種別が合わない操作（例: 表でないshapeへの`set_table_cell`）、`set_table`
  の行列数不一致、`reorder_slides` の順列が現在の全スライド数と不一致、
  `replace_picture` の `image_path` 不在、未対応の `theme`/`role`/`align`、
  `set_shape_style` でスタイルキーが1つも無い、表shapeへの`set_shape_style`で
  対象行未指定、表shapeへの`set_shape_style`で`border_color`指定、
  `set_shape_position` で位置/サイズキーが1つも無い、`add_picture` の
  `image_path` 不在、`add_chart` の未対応`type`（bar/line/pie以外）や
  `series`の`values`要素数と`categories`要素数の不一致、は、いずれもその
  操作番号を添えてエラー終了します。
- `delete_slide`で歯抜けができた後に`duplicate_slide`を実行しても、内部でスライドの
  パート名衝突（python-pptx側の既知の制限）を自動回避するため、安全に組み合わせられます。
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
