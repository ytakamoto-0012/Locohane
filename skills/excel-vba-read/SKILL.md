---
name: excel-vba-read
description: xlsm/xlsファイルのVBAマクロコード（標準モジュール・クラスモジュール・Sub/Function）を読み込み専用で取得するスキル。Excel本体は不要（oletoolsでバイト列から直接抽出するため、edit_vba.pyより前提条件が緩い）。ユーザーがVBAマクロのコードを見たいとき、excel-vba-editスキルでコードを書き換える前に既存コードを確認したいときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-vba-read

xlsm/xls のVBAマクロコードを読み込み専用で取得するスキル。
`read_vba.py` を `run_script` で実行する。

## 呼び出し

```json
{"skill_name": "excel-vba-read", "script_filename": "read_vba.py", "script_args": ["C:\\Users\\me\\book.xlsm", "--module", "Module1"]}
```
`--module`省略でモジュール一覧モード。`oletools`（`olevba`）でバイト列から直接抽出するため**Excel本体・COM不要**（excel-vba-editスキルとは対照的に前提条件が緩い）。

## 入出力の型

成功=終了コード0＋JSON1行を標準出力。失敗=終了コード非0＋エラーメッセージを標準エラー。エラー文はそのままユーザーへ伝える。未導入ライブラリは`ImportError`終了コード1（原因: `oletools`）→該当する`pip install oletools`をユーザーに促す。

このスクリプトは本文（modules一覧/コード全文）を標準出力に出さず、一時JSONへ書き出して`result_path`を返す。`Read`ツールで`result_path`（または`path_memory`の`@N`）を読む。一覧モードは`modules_count`と`result_path`（中身は`modules`＝各要素`{"name","type","line_count"}`）。`--module`指定時は`code_length`と`result_path`（中身`code`＝ソース全文）。

## 手順

1. `--module`なしで一覧確認。
2. 目的のモジュールを`--module`指定で全文取得。
3. excel-vba-editスキルで書き換える前に必ずこれで既存コードを確認する（`set_code`は全文置換のため）。

## エッジケース

対象はxlsm/xls（`.xlsx`は即エラー）。VBAプロジェクト無しのファイルはエラーにせず`{"has_vba": false, "modules": []}`で正常終了。モジュール種別（`standard`/`class`/`document`/`form`）は名前パターン・コード内容からの**簡易推測**であり保証はない（正確な種別が要る場合はExcelのVBE(Alt+F11)確認をユーザーに案内）。存在しないモジュール名指定はエラー（存在する一覧付き）。UserFormは`type:"form"`として一覧表示され読込は可能だがexcel-vba-editスキルの編集対象外。
