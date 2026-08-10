---
name: excel-render
description: xlsx/xlsm/xlsのシートをOLE→PDF→PNG変換で画像化し、罫線・書式・グラフ・レイアウトなどセル値だけでは分からない見た目をLLMに見せるスキル。ローカルにMicrosoft Excelが導入されている必要がある。画像化しただけではLLMは中身を見られないため、続けてanalyze_imageで確認する2段階手順が必須。ユーザーがExcelのレイアウトや罫線を確認したいとき、excel-editでの装飾作業の前後に見た目を検証したいときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-render

Excelシートを画像化してLLMに見せるスキル。`render_excel.py` を `run_script` で実行する。

excel-readスキルは数値・テキストは取れても罫線・書式・グラフ・レイアウトは読み取れないため、シートの意図をより正確に把握したいときに画像で確認する。

PDF化前に各シートの印刷設定を「横1ページ×縦1ページ」フィット印刷（`Zoom=False`+`FitToPagesWide=1`+`FitToPagesTall=1`）へ自動強制し、使用範囲が複数ページに分割される（画像が細切れになる）ことを防ぐ。PDF→画像化は600DPIの高解像度で行い余白除去の精度を確保後、目標DPI（既定300）までダウンスケールする。

## 呼び出し

```json
{"skill_name": "excel-render", "script_filename": "render_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx", "--start-page", "1", "--max-pages", "3"]}
```
`--start-page`/`--max-pages`(既定3、最大5)/`--dpi`(既定600、72〜600)/`--no-crop`(余白除去オフ)は省略可。

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。生成した画像は`path_memory`に自動登録される。

## 出力

`{"total_pages":5,"start_page":1,"end_page":3,"dpi":600,"target_dpi":300,"crop_applied":true,"images":[{"page":1,"image_path":"...","cropped":true}, ...]}`。

## 重要（2段階手順、必須ルール）

このスクリプトはPNGを保存しパスをJSONで返すだけで、LLMへ見せる処理は別。各`images[].image_path`（絶対パス）を続けて`analyze_image`（共通ツール）の`relative_path`引数に渡す。1回の`analyze_image`で1ページ分見えるので複数ページは呼び出しを分ける。`render_excel.py`が返す`image_path`は必ず続けてこの手順を行うこと（画像化だけでは中身を確認したことにならない）。

デザイン・レイアウト（配色・罫線・列幅・グラフ配置・印刷時の見え方等）の調整を行う前後には、必ずこのスキル＋`analyze_image`でシートを画像として確認する。excel-readのセル値・style情報だけで見た目を判断して完了報告しない。

余白除去は既定オン（白黒境界判定。Excelは余白が大きいことが多く、除去でコンテンツ解像度が上がり読みやすくなる）。`--no-crop`でオフにできる。

## エッジケース

ファイル不在／ディレクトリ指定／壊れたファイル／Excel未インストールはエラー終了。`start_page`が総ページ数超過は`images: []`を終了コード0で返す（エラーにならない）。`max_pages`は5にクランプ（総ページ数が多い場合は`--start-page`を変えて複数回呼ぶ）。生成PNGは`_tmp_<thread_id>/rendered/`に保存され同一ファイル再実行時は上書き、会話終了時に自動削除。`pywin32`/`pypdfium2`/`pillow`未導入は`ImportError`（該当する`pip install <パッケージ名>`をユーザーに促す）。
