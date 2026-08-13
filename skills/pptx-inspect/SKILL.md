---
name: pptx-inspect
description: PowerPoint（.pptx）の編集対象を特定するための構造読み取り専用スキル。スライド単位のshape構造（shape_index・種別・プレースホルダ種別・表/画像の有無等）をJSONで取得する。既存pptxテンプレートを`pptx-edit`で部分編集する前に、必ずこのスキルでshape_indexを把握するために使う。内容の要約・テキスト抽出は`pptx-read`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pptx-inspect

既存テンプレートを編集する前に**必ず**このスキルでスライド構造を確認し、
`shape_index` を把握してから `pptx-edit` を呼んでください。`inspect_pptx.py` を
`run_script` ツールで実行して結果を得ます。`pptx-read` の `read_pptx.py` は
人間向けの要約（title/texts/tables/notes）を返すのに対し、こちらは編集に
必要なshape単位の構造情報を返します。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

スライドごとの本文データは直接標準出力へは返さず、一時JSONファイルへ書き出して
そのパス（`result_path`）を返します。中身を確認するには `Read` ツールで
`result_path`（または `path_memory` の `@N`）を読んでください。

## inspect_pptx.py — 編集対象を特定するための構造読み取り

呼び出し例:
```json
{
    "skill_name": "pptx-inspect",
    "script_filename": "inspect_pptx.py",
    "script_args": ["C:\\Users\\me\\template.pptx", "--start-slide", "1", "--max-slides", "20"]
}
```
`--start-slide`（整数、既定`1`）/`--max-slides`（整数、既定`20`）は省略可。
どちらも1未満を指定すると1にクランプされる（例`--max-slides 0`は`1`扱い）。

出力例:
```json
{"path": "C:\\foo\\template.pptx", "total_slides": 4, "start_slide": 1, "end_slide": 4,
 "slides_count": 4, "slide_width_cm": 25.4, "slide_height_cm": 19.05,
 "result_path": "C:\\...\\_tmp_<thread_id>\\pptx_inspect\\1a2b3c4d_20260805_153012_123456.json"}
```
スライド単位のshape構造（`slides`、各要素は
`{"index", "layout_name", "layout_index", "shapes", "notes_present"}`。
`shapes` の各要素は `{"shape_index", "name", "shape_type", "is_placeholder",
"placeholder_idx", "placeholder_type", "has_text_frame", "text_preview",
"has_table", "table_dims", "has_picture", "left_cm", "top_cm", "width_cm",
"height_cm"}`）は標準出力からは省かれ、`result_path` が指すJSONファイルにのみ
含まれます。`Read` ツールで読んで `shape_index` を確認してから `pptx-edit` を
呼んでください。

トップレベルに以下のフィールドも追加されます：
- `slide_width_cm`/`slide_height_cm`: スライドのサイズ（cm単位）
- `warnings`: 構造的な不備の警告配列（該当なしならキー省略）。shapeがスライド境界をはみ出している場合に指摘。

- `shape_index` は `pptx-edit` の各操作で指定する `shape_index` と完全に一致します
  （このスライド内での0始まり連番）。
- `text_preview` は先頭50文字までの切り詰め表示です（編集対象を見分けるための参考情報で、
  全文取得には `pptx-read` を使ってください）。
- `left_cm`/`top_cm`/`width_cm`/`height_cm` はshapeの現在位置・サイズ（cm単位）。
  `pptx-edit`の`set_shape_position`が受け取る単位と完全に一致するため、
  ここで読んだ値をそのまま計算の基準に使える（例:「shape_index 2の右に10cm離して
  配置したい」→このshapeの`left_cm + width_cm + 10`を新しい`left_cm`にする）。
  プレースホルダ等でレイアウト側から座標を継承していて実座標が取得できない
  shapeは`null`になる。
- ページングは `pptx-read` と同じ設計です（`total_slides` が `max_slides` を超える場合は
  `--start-slide` を `end_slide + 1` にして再度呼び出す）。

## エッジケース

- ファイル不在・ディレクトリ指定・壊れたファイルはエラー終了します
  （`read_pptx.py` と同じ挙動）。
- 依存パッケージ `python-pptx` が実行環境に入っていないと `ModuleNotFoundError` で
  終了コード非0になります。その場合は導入者へ `pip install python-pptx` の実施を促してください。

## パスメモリー（`@N`）

`inspect_pptx.py` が書き出す結果JSON（`result_path`）は、出力JSONに
`path_memory`（例: `{"@7": "C:\\...\\1a2b3c4d.json"}`）として自動登録されます。
続けて `run_script`/`Read` を呼ぶ場合、絶対パスの代わりにその `@N` を
そのまま渡せます（自動的に実パスへ解決されます）。
