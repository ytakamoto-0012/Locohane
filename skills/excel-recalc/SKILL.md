---
name: excel-recalc
description: xlsx/xlsm/xlsファイルの数式を実際にMicrosoft Excelで再計算し、計算結果とエラーセル（#DIV/0!等）を検出するスキル。ローカルにMicrosoft Excelが導入され対話セッションから呼ばれている必要がある（EXCEL.EXEを一時起動するCOM経由の処理）。excel-editスキルで数式を書き込んだ直後に計算結果を確認したいとき、数式エラーが無いか検査したいときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-recalc

xlsx/xlsm/xls の数式を実際に再計算し、エラーセルを検出するスキル。
`recalc_excel.py` を実行する。

## 呼び出し

```bash
python recalc_excel.py "C:\Users\me\book.xlsx"
```
excel-editスキルの`edit_excel.py`で書いた数式はopenpyxlでは評価されないため、計算結果を確認したい場合はこのスクリプトを実行後、excel-readスキルの`read_excel.py --data-only`で読み直す。Excel本体をバックグラウンド起動し実際に計算・上書き保存する。

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。

## 出力

`{"path":..., "recalculated": true, "errors": [{"sheet":"Sheet1","cell":"B5","value":"#DIV/0!"}]}`。`errors`が空なら数式エラーなし。1件でもあればどのシート・セルかをユーザーへ報告し、必要ならexcel-editスキルの`set_cell`で修正する。

## 制約（実行前にユーザーへ伝えるべき情報）

- **ローカルにMicrosoft Excelが導入され、対話セッションから呼ばれている必要がある**（サーバーサービス実行では動かない可能性が高い。Windowsネイティブ環境前提のこのプロジェクトでは通常問題ないが、Excel未導入環境では使えない）。
- 内部でEXCEL.EXEを一時起動する。処理完了時は必ず終了させるが、**スクリプトが強制終了された場合は残留する可能性あり**。この場合はタスクマネージャーでの手動終了ではなく、`python recalc_excel.py --recover-locks`（`path`は不要）を実行する（自セッションが`recalc_excel.py`/`edit_vba.py`/excel-render等で起動して残留したCOMプロセスのうち、まだ生存しているものだけを内部で終了する。無関係なExcelプロセスを巻き込む余地はない）。実行後`{"recovered": [...]}`に終了したPID一覧が返る。
- 大きいワークブックは実行タイムアウト（既定60秒）を超える可能性あり。超過時はタイムアウト増加を提案する。
- インターネット由来（Mark of the Web付き）ファイルは保護ビューで開かれ正しく再計算できない場合がある（このスキルで生成・編集したファイルなら通常問題ない）。

## エッジケース

ファイル不在／拡張子がxlsx・xlsm・xls以外はエラー＋終了コード1。`pywin32`未導入は`ImportError`（該当する`pip install pywin32`をユーザーに促す）。ExcelのCOMエラー（未インストール／未認証／他プロセスで開かれている等）は「Excelでの再計算に失敗しました: ...」＋終了コード1（そのままユーザーへ伝える）。
