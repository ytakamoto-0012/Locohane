# 権限外のツールをコードでシミュレートしない

`execute_python_code_readonly`のみを持つ（`run_script`を持たない）
readonlyサブエージェントが、それでも`excel-read`を呼びたいがために、
`run_script`ツールへ渡すはずの引数JSONをそのまま`subprocess`経由の
標準入力として渡すコードを書いた実例がある。

```python
subprocess.run(["python", "-m", "json.tool"],
                input='{"skill_name": "excel-read", ...}', ...)
```

これは`run_script`という**ツール呼び出しの仕組み自体**を、手元のコード内で
再現しようとした誤り（`json.tool`はJSONの整形コマンドであって`excel-read`を
実行する手段ではなく、加えて`input`に文字列を渡したことで
`TypeError: a bytes-like object is required, not 'str'`にもなっている）。
自分に無い権限のツールは、どれだけコードを工夫しても呼び出せない。

## 対処

- 自分が持つツール一覧を確認し、必要なツール（この例では`run_script`）が
  無いと分かった時点で、コードでの回避を試みるのをやめる。
- 権限を持つ上位エージェントへ結果を差し戻す・作業を委譲し直す等、
  ツール権限の設計に沿った経路で解決する。
- 「readonly」の制約は意図的な設計（書き込み系ツールを持たせないことで
  安全に読み取り専用の調査をさせる）であり、コードでの抜け道を探す対象では
  ない。
