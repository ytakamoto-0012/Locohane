# edit_excel.pyの基本契約（ops必須・--newの挙動）

`excel-edit`（`edit_excel.py`）の呼び出しでハマった事例の多くは、opsベースの
APIであること自体を見失い、単純なファイル操作ツールとして誤用したことが原因。

## `--ops-json`/`--ops-file`はどんな用途でも必須

`--ops-json`と`--ops-file`は`argparse`の`mutually_exclusive_group(required=True)`
で定義されており、**どちらか一方を必ず渡さないと即エラーになる**
（`--new`だけを指定して空ブックを作る、既存ファイルを別名にコピーする、
といった「opsを使わない」呼び出し方は存在しない）。

実際に、バックアップファイルを本番ファイルへ復元しようとして

```
run_script(excel-edit, edit_excel.py, ["収支計算表.bak_....xlsm", "--output", "収支計算表.xlsm"])
```

と`--ops-json`/`--ops-file`を省略して呼び出し、

```
one of the arguments --ops-json --ops-file is required
```

で失敗した事例がある。さらに深刻なのは、この直後に**`--overwrite`を
追加しただけの同じコマンドをもう一度実行し、まったく同じエラーで再度失敗**
したこと。`--overwrite`は「`--new`時の上書き許可」フラグであり、
「ops必須」エラーとは無関係なため当然直らない
（再試行前にエラー原因と変更点が対応しているか確認する一般原則は
[[error-message-first-retry]]参照）。

**ファイルのコピー・バックアップ復元自体は`execute_python_code`の
`shutil.copy2`等で行い、その後に`excel-edit`で`--ops-json`のopsを適用する、
という2段階に分けること。** `edit_excel.py`はファイルコピーツールではない。

## `--new`は0シートの空ブックから始まる

`--new`指定時の実装は次の通り：

```python
wb = openpyxl.Workbook()
wb.remove(wb.active)
```

つまり**既定の「Sheet1」は最初から存在しない**（openpyxlの一般知識として
「新規ブックには既定シートがある」と思い込みがちだが、`edit_excel.py`の
`--new`は生成直後にそれを削除している）。この思い込みから、新規作成タスクの
最初のopに

```json
{"op": "delete_sheet", "name": "Sheet1"}
```

を含めて

```
ops[0]（op='delete_sheet'）の適用に失敗しました: シートが見つかりません: Sheet1（存在するシート: []）
```

に失敗した事例がある。**`--new`直後の最初のopは必ず`add_sheet`にする**
（delete/rename等をいきなり最初のopに置かない）。

## `--query`と`--query-json`は別物（`excel-read`側の話だが混同しやすい）

`excel-read`のフラグは`--query-json`であって`--query`ではない。フラグ名自体の
打ち間違いはエラーメッセージから気づきにくく（`unrecognized arguments`にしか
ならない）、SKILL.mdを再読して初めて気づく形になりやすいので、`--ops-json`
（excel-edit）と`--query-json`（excel-read）のどちらを呼んでいるかを含め、
フラグ名は都度SKILL.mdの表記をそのままコピーする意識を持つこと。
