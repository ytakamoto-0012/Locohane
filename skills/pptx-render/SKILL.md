---
name: pptx-render
description: PowerPoint（.pptx）のスライドを画像化し、analyze_imageでLLMに見せるためのスキル（OLE→PDF→PNG変換、余白自動除去）。ローカルのMicrosoft PowerPoint本体が必要。テキスト抽出だけでは分からないレイアウト・図表・画像配置・強調表現を確認したいとき、生成・編集したpptxの見た目を確認したいときに使う。テキストの抽出は`pptx-read`、新規生成は`pptx-create`、既存テンプレートの部分編集は`pptx-edit`を使う。
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
`--start-page`/`--max-pages`（既定3、最大5にクランプ）/`--dpi`（既定300、
72〜600にクランプ）/`--crop`（後述の余白除去をオンにする。既定はオフ）は省略可。

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

**余白除去について**: 既定はクロップなし（スライドの実際の端がそのまま見える）。
スライドのサイズ・余白はpptxファイル自体が定義済みで推測の必要が無いうえ、
レイアウトQA（余白の均等さ・中央揃え・スライドが薄すぎないか等の確認）には
実際の端が見えている方が有用なため。図形の配置確認だけが目的で余白を詰めたい
場合のみ `--crop` を付けてください。

## エッジケース

- ファイル不在・ディレクトリ指定・壊れたファイル/PowerPoint未インストールはエラー終了します。
- `start_slide` が総スライド数を超える場合はエラーにはならず、`images: []` を
  終了コード0で返します。
- `max_pages` は5にクランプされます。総スライド数が多いプレゼンを広く画像化
  したい場合は `--start-page` を変えて複数回に分けて呼び出してください。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/rendered/`）に保存されます。同一ファイルの再実行時は
  上書きされ、会話終了時に自動的に削除されます。
- 依存パッケージ `pywin32`・`pypdfium2`・`pillow` が実行環境に無い場合は
  `ImportError` で終了コード非0になります。
