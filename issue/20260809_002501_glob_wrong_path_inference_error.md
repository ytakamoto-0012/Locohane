# Glob ツールで LLM が間違ったパスを推測しエラー

- **区分**: 問題点
- **検知日時**: 2026-08-09 00:25:01
- **対象ログファイル**: data/logs/app_20260809_000102.log

## 経緯

ユーザーが「imagesフォルダ内の画像ファイル（料理本のレシピの写真）を読み取り、mdフォルダにmdファイルでレシピ内容を書き出す」というタスクを依頼。メインエージェントが `Glob` ツールで画像ファイルを確認しようとした際、LLMが間違った絶対パス `C:\Users\akira\Desktop\cook-book\images` を推測して指定した。実際のディレクトリは `C:\Users\akiyo\レシピ` 付近にあるはずだが、ユーザー名 `akira` は完全な推測誤り。

ツールは「検索起点ディレクトリが見つかりません」とエラーを返し、LLMは `AskUserQuestion` で正しいパスを問い合わせる結果となった。

## ログ引用

```
2026-08-09 00:01:38,593 DEBUG src.tools: tool_call: name=Glob args={'pattern': '**/*', 'path': 'C:\\Users\\akira\\Desktop\\cook-book\\images', 'head_limit': 1} id=TSP39zPMMkA5VXqZELuSLgbNsMd1uVZG
2026-08-09 00:01:38,602 WARNING src.tools: tool_result: name=Glob content='エラー: 検索起点ディレクトリが見つかりません: C:\\Users\\akira\\Desktop\\cook-book\\images もしかして C:\\Users\\akiyo ではありませんか？ パスは記憶や推測で再構築せず、直前のツール結果に含まれる文字列や path_memory の @N をそのままコピーして使ってください。'
```

## 推定原因

LLMが会話履歴やpath_memoryから正しいパスを抽出できず、ユーザー名 `akira` を推測してしまった。system_prompt.md で「パスは記憶や推測で再構築せず、直前のツール結果に含まれる文字列や path_memory の @N をそのままコピーして使ってください」と指示されているものの、小型ローカルモデルがこの指示を厳密に守れていない可能性がある。

## 追記

（なし）

## ユーザー回答

ここにはユーザーの回答が記述される
