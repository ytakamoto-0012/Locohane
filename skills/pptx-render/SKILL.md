---
name: pptx-render
description: PowerPoint（.pptx）のスライドを画像化し、analyze_imageでLLMに見せるためのスキル（OLE→PDF→PNG変換）。ローカルのMicrosoft PowerPoint本体が必要。テキスト抽出だけでは分からないレイアウト・図表・画像配置・強調表現を確認したいとき、生成・編集したpptxの見た目を確認したいときに使う。テキストの抽出は`pptx-read`、新規生成は`pptx-create`、既存テンプレートの部分編集は`pptx-edit`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# pptx-render

PPTXスライドを画像化してLLMに見せるスキルです。`render_pptx.py` を `run_script`
ツールで実行して結果を得ます。`pptx-read` の `read_pptx.py` でテキストは取得
できても、レイアウト・図表・画像の配置・強調表現などテキストだけでは読み取れない
情報があるため、資料の意図をより高精度に汲み取りたいときはこちらを使って
画像として内容を確認します。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "pptx-render",
    "script_filename": "render_pptx.py",
    "script_args": ["C:\\Users\\me\\presentation.pptx", "--start-page", "1", "--max-pages", "3"]
}
```
## 引数一覧

| 引数 | 必須/任意 | 値の型 | 既定値 | 説明 |
|---|---|---|---|---|
| `pptx_path`（位置引数） | 必須 | 文字列（絶対パス） | - | 画像化対象の`.pptx`ファイルパス。他拡張子はエラー |
| `--start-page` | 任意 | 整数 | `1` | 開始スライド番号（1始まり）。クランプなし。総スライド数超過時は下記エッジケース参照 |
| `--max-pages` | 任意 | 整数 | `3` | 一度に画像化する最大スライド数。**1〜5にクランプされる**（6以上を指定しても5枚まで） |

出力例:
```json
{"path": "C:\\foo\\presentation.pptx", "tool": "pptx", "total_pages": 12, "start_page": 1, "end_page": 3, "dpi": 300, "target_dpi": 150, "crop_applied": false,
 "images": [
   {"page": 1, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p1.png", "original_dpi": 300},
   {"page": 2, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p2.png", "original_dpi": 300},
   {"page": 3, "image_path": "C:\\...\\_tmp_<thread_id>\\rendered\\1a2b3c4d_p3.png", "original_dpi": 300}
 ]}
```

**重要（2段階手順）**: このスクリプト自体はPNGファイルを保存してパスをJSONで
返すだけで、LLMへ画像を見せるところまでは行いません。`images` の各要素の
`image_path`（絶対パス）を、続けて `analyze_image` ツール（このスキル専用ではなく
共通ツール）の `relative_path` 引数にそのまま渡して呼び出してください。
1回の `analyze_image` 呼び出しで1スライド分が見えるので、複数スライドある場合は
スライド数分 `analyze_image` を呼びます。

## エッジケース

- ファイル不在・ディレクトリ指定・拡張子が`.pptx`以外・壊れたファイル/PowerPoint未インストールはエラー終了します。
- `start_page` が総スライド数を超える場合はエラーにはならず終了コード0で返りますが、
  返るJSONの形が通常時と異なる点に注意（`images: []`に加えて`start_page`/`end_page`が
  `null`になり、**`target_dpi`キー自体が無くなる**）。
- `max_pages` は5にクランプされます。総スライド数が多いプレゼンを広く画像化
  したい場合は `--start-page` を変えて複数回に分けて呼び出してください。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/rendered/`）に保存されます。同一ファイルの再実行時は
  上書きされ、会話終了時に自動的に削除されます。
- 依存パッケージ `pywin32`・`pypdfium2`・`pillow` が実行環境に無い場合は
  `ImportError` で終了コード非0になります。
