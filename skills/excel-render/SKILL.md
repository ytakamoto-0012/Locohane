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

PDF化前に各シートの印刷設定を「横1ページ×縦1ページ」フィット印刷（`Zoom=False`+`FitToPagesWide=1`+`FitToPagesTall=1`）へ自動強制し、使用範囲が複数ページに分割される（画像が細切れになる）ことを防ぐ。PDF→画像化は既定300DPIで行い、シートの縮尺が小さいほどキャプチャDPIを自動的に引き上げる（上限900DPI）。全シート（PDF化後の全ページ）を一度に画像化する。

## 呼び出し

```json
{"skill_name": "excel-render", "script_filename": "render_excel.py",
 "script_args": ["C:\\Users\\me\\book.xlsx"]}
```
## 引数一覧

| 引数 | 必須/任意 | 値の型 | 既定値 | 説明 |
|---|---|---|---|---|
| `excel_path`（位置引数） | 必須 | 文字列（絶対パス） | - | 画像化対象の`.xlsx`/`.xlsm`/`.xls`ファイルパス。他拡張子はエラー |

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。生成した画像は`path_memory`に自動登録される。

## 出力

```json
{"path": "C:\\foo\\book.xlsx", "tool": "excel", "total_pages": 5, "start_page": 1, "end_page": 5,
 "dpi": 300, "target_dpi": 300, "crop_applied": true,
 "images": [{"page": 1, "image_path": "C:\\...\\_tmp_<name>\\rendered\\1a2b3c4d_p1.png", "original_dpi": 300, "cropped": true}, ...]}
```
`images`には全ページ（=全シート、通常は`total_pages`件）が含まれる。
`images`の各要素は常に`original_dpi`（クロップ前の実解像度）を持つ。`cropped`（bbox検出に
成功しクロップできたら`true`、検出できず元画像のままなら`false`）は既定動作で各画像に付与される。

**DPIの動的ブースト**: シート内容が用紙に収まりにくいほど、キャプチャDPI・`target_dpi`は自動的に引き上げられる場合がある（上限900DPI）。

## 重要（2段階手順、必須ルール）

このスクリプトはPNGを保存しパスをJSONで返すだけで、LLMへ見せる処理は別。各`images[].image_path`（絶対パス）を続けて`analyze_image`（共通ツール）の`relative_path`引数に渡す。1回の`analyze_image`で1ページ分見えるので複数ページは呼び出しを分ける。`render_excel.py`が返す`image_path`は必ず続けてこの手順を行うこと（画像化だけでは中身を確認したことにならない）。

デザイン・レイアウト（配色・罫線・列幅・グラフ配置・印刷時の見え方等）の調整を行う前後には、必ずこのスキル＋`analyze_image`でシートを画像として確認する。excel-readのセル値・style情報だけで見た目を判断して完了報告しない。

余白除去は既定オン（白黒境界判定）。

## エッジケース

ファイル不在／ディレクトリ指定／拡張子が`.xlsx`・`.xlsm`・`.xls`以外／壊れたファイル／Excel未インストールはエラー終了。シートが1枚も無い等でPDFが0ページになった場合は終了コード0で返るが、通常時とJSONの形が異なる点に注意（`images: []`に加え`start_page`/`end_page`が`null`になり、**`target_dpi`キー自体が無くなる**）。生成PNGは`_tmp_<name>/rendered/`に保存され同一ファイル再実行時は上書き、会話終了時に自動削除。`pywin32`/`pypdfium2`/`pillow`未導入は`ImportError`（該当する`pip install <パッケージ名>`をユーザーに促す）。

内部でEXCEL.EXEを一時起動する。処理完了時は必ず終了させるが、`run_script`のタイムアウト等でスクリプトが強制終了された場合は残留する可能性がある。この場合はタスクマネージャーでの手動終了ではなく、`excel-recalc`スキルの`recalc_excel.py`または`excel-vba-edit`スキルの`edit_vba.py`を`script_args`を`["--recover-locks"]`だけにして実行する（自セッションがexcel-render等で起動して残留したCOMプロセスのうち、まだ生存しているものだけを内部で終了する）。

シートの列幅・行高さの合計が用紙1ページに収めるための下限スケール（10%）を下回ると、複数ページに分割された上で出力JSONに`warnings`配列（stderrにも同内容を出力）が付与される。終了コードは0のまま。`excel-edit`の`set_column_width`（単位は文字幅、目安1〜60）で列幅を見直してください。
