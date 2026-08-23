# execute_python_codeでのpip installがガードで拒否され1手ロス（oletoolsインストール指示）

- **区分**: 問題点
- **検知日時**: 2026-08-23 12:06:21
- **対象ログファイル**: data/logs/app_20260823_104959.log

## 経緯

VBAマクロ修正タスクの冒頭、ユーザー指示文に「oletoolsをインストールし」と
あったため、workerが`execute_python_code`で`pip install oletools`を実行
しようとし、`execute_python_codeガード`によって`PermissionError`で拒否された。
6秒後、workerは「pip installは実行環境で禁止されていますが、まずは既存VBA
コードを確認します」と判断し、`read_vba.py`をそのまま実行して成功（oletoolsは
既に導入済みだったため実害なし）。

## ログ引用

```
2026-08-23 12:06:21,135 WARNING src.subagent: subagent tool=execute_python_code args={'code': "import subprocess\nresult = subprocess.run(['pip', 'install', 'oletools'], capture_output=True, text=True)\nprint(result.stdout)\nprint(result.stderr)"} -> [終了コード] 1
2026-08-23 12:06:21,135 DEBUG src.subagent: subagent tool=execute_python_code args=... -> '[終了コード] 1\n[標準エラー]\n...\nPermissionError: [execute_python_codeガード] git/npm/pipコマンドの実行は禁止されています: [\'pip\', \'install\', \'oletools\']'
2026-08-23 12:06:27,636 INFO src.subagent: subagent iter=2 ai='pip installは実行環境で禁止されていますが、まずは既存VBAコードを確認します。\n\n'
2026-08-23 12:06:27,849 INFO src.subagent: subagent tool=run_script args={'skill_name': 'excel-vba-read', 'script_filename': 'read_vba.py', ...} -> [終了コード] 0
```

## 推定原因

`execute_python_codeガード`はCLAUDE.mdの「新規pipライブラリが必要な時は
requirements.txtを更新する」運用（依存追加は開発者側が管理し、LLMが実行時に
勝手にインストールしない設計）を守るための意図した制限であり、バグではない。
今回はユーザー指示文に「oletoolsをインストールし」という一文が含まれていた
ためLLMが素直に実行を試みたが、oletoolsは`excel-vba-read`スキル用に既に
導入済みで、実際には不要な手順だった。6秒で自己回復しており実害は無い。

## 追記（YYYY-MM-DD HH:MM）

（再発時にここへ追記）

## ユーザー回答

ここにはユーザーの回答が記述される
