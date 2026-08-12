---
name: docx-render
description: Word文書（.docx）のページを画像化し、analyze_imageでLLMに見せるためのスキル（OLE→PDF→PNG変換、余白自動除去）。ローカルのMicrosoft Word本体が必要。テキスト抽出だけでは分からないレイアウト・表・画像配置・強調表現を確認したいとき、生成・編集したdocxの見た目を確認したいときに使う。テキストの抽出・検索には`docx-read`、生成には`docx-create`、編集には`docx-edit`を使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# docx-render

DOCXページを画像化してLLMに見せるスキルです。`render_docx.py` を `run_script`
ツールで実行して結果を得ます。`docx-read` の `read_docx.py` でテキストは取得
できても、レイアウト・表・画像の配置・強調表現などテキストだけでは読み取れない
情報があるため、文書の意図をより高精度に汲み取りたいときはこちらを使って
画像として内容を確認します。

正常系なら終了コード0でJSON1行を標準出力へ、異常系なら終了コード非0で
エラーメッセージを標準エラーへ出力します。

呼び出し例:
```json
{
    "skill_name": "docx-render",
    "script_filename": "render_docx.py",
    "script_args": ["C:\\Users\\me\\report.docx", "--start-page", "1", "--max-pages", "3"]
}
```
`--start-page`/`--max-pages`（既定3、最大5にクランプ）/`--dpi`（既定300、
72〜600にクランプ）/`--crop`（後述の余白除去をオンにする。既定はオフ）は省略可。

出力例:
```json
{"path": "C:\\foo\\report.docx", "tool": "docx", "total_pages": 5, "start_page": 1, "end_page": 3, "dpi": 300, "target_dpi": 150, "crop_applied": false,
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
1回の `analyze_image` 呼び出しで1ページ分が見えるので、複数ページある場合は
ページ数分 `analyze_image` を呼びます。

**余白除去について**: 既定はクロップなし（ページの実際の端がそのまま見える）。
用紙サイズ・余白はdocxファイル自体が定義済みで推測の必要が無いうえ、レイアウト
QA（余白の均等さ・中央揃え等の確認）には実際の端が見えている方が有用なため。
文字を大きく見せたいだけの場合のみ `--crop` を付けてください。

## エッジケース

- ファイル不在・ディレクトリ指定・壊れたファイル/Word未インストールはエラー終了します。
- `start_page` が総ページ数を超える場合はエラーにはならず、`images: []` を
  終了コード0で返します。
- `max_pages` は5にクランプされます。総ページ数が多い文書を広く画像化したい
  場合は `--start-page` を変えて複数回に分けて呼び出してください。
- 生成されるPNGは作業ディレクトリ配下のセッション専用一時フォルダ
  （`_tmp_<thread_id>/rendered/`）に保存されます。同一ファイルの再実行時は
  上書きされ、会話終了時に自動的に削除されます。
- 依存パッケージ `pywin32`・`pypdfium2`・`pillow` が実行環境に無い場合は
  `ImportError` で終了コード非0になります。
