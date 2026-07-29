---
name: word-counter
description: テキストファイルの行数・単語数・文字数を数える。ユーザーがファイルの行数/単語数/文字数を知りたいとき、テキストの分量を測りたいとき、アップロードしたテキストの統計が欲しいときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# word-counter

テキストファイルの行数・単語数・文字数をカウントするスキルです。
`count.py` を `run_script` ツールで実行して結果を得ます。

## 手順

1. 対象のテキストファイルのパスを確認する（ユーザーがアップロードした場合は
   保存先パスがメッセージに示されている）。
2. `run_script` ツールを次の形式で呼び出す:
   ```json
   {
       "skill_name": "word-counter",
       "script_filename": "count.py",
       "script_args": ["C:\\Users\\me\\sample.txt"]
   }
   ```
3. スクリプトは JSON を標準出力へ返す。その `lines` / `words` / `chars` を
   ユーザーへ日本語で分かりやすく報告する。

## 出力例

```json
{"path": "sample.txt", "lines": 12, "words": 84, "chars": 512}
```

## エッジケース

- ファイルが存在しない場合、スクリプトはエラーメッセージを stderr に出して
  終了コード 1 を返す。その旨をユーザーに伝えること。
- 単語数は空白区切りで数える（日本語のように空白で区切らない言語では
  「単語数」は目安である点を補足するとよい）。
