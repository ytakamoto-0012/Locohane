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

## 追記（2026-08-12 12:00）

同種の「パスを推測で構築してGlobエラー」が別セッションで再発。今回はメインエージェントが`explore-docs`サブエージェントへの委譲タスク文中で`E:\yukinori\テスト\2024\ocr_md`・`E:\yukinori\テスト\2025\ocr_md`という、実際には途中に`Datas`フォルダが必要なパス（正: `E:\yukinori\テスト\Datas\2024\ocr_md`）を誤って指示した。

```
2026-08-12 11:56:41,327 DEBUG src.tools: tool_call: name=dispatch_agent args={'task': 'E:\\yukinori\\テスト\\2024\\ocr_md\\ と E:\\yukinori\\テスト\\2025\\ocr_md\\ の markdown ファイルを読み込み...', 'agent_type': 'explore-docs'}
2026-08-12 11:56:48,126 WARNING src.subagent: subagent tool=Glob args={'pattern': '**/*.md', 'path': 'E:\\yukinori\\テスト\\2024\\ocr_md'} -> エラー: 検索起点ディレクトリが見つかりません: E:\yukinori\テスト\2024\ocr_md
2026-08-12 11:56:48,126 WARNING src.subagent: subagent tool=Glob args={'pattern': '**/*.md', 'path': 'E:\\yukinori\\テスト\\2025\\ocr_md'} -> エラー: 検索起点ディレクトリが見つかりません: E:\yukinori\テスト\2025\ocr_md
```

今回は前回と異なり、サブエージェント自身が`E:\yukinori\テスト`のルートを`Glob`で確認し直し（11:56:51）、11:57:09に正しい`Datas`付きパスで再試行して自己回復した（ユーザーへの`AskUserQuestion`には至らなかった）。同一セッション内で同じ4年分バッチ委譲（[issue/20260812_115000_explore_docs_batch_oversize_hard_token_cutoff.md](20260812_115000_explore_docs_batch_oversize_hard_token_cutoff.md)参照）の直後の残り2年分（2024・2025）を委譲する際に発生しており、**メインエージェントは11:42の1回目の委譲では正しいパスの一部（`Datas`無し表記だが実データはサブエージェント側のGlobで解決できていた）を使い、2回目でも同じ`Datas`無し表記を再度使っている**——つまり1回目の委譲で`Datas`付きの正しいパスがツール結果として返ってきていたにもかかわらず、2回目のタスク文生成時にそれを踏まえずまた推測ベースのパスを書いてしまっている。「直前のツール結果のパスをそのままコピーする」という指示が、委譲タスク文の生成（dispatch_agentへの`task`引数の作文）という文脈では効いていない可能性がある。

## 追記（2026-08-12 18:30）

同種の「パスを推測で構築してGlobエラー」が再発。18:25のセッションで、LLMが`E:\yukinori\テスト（読み書き可能）`という存在しないパスを推測してGlobを呼び出した。

```
2026-08-12 18:25:27,368 WARNING src.tools: tool_result: name=Glob content='エラー: 検索起点ディレクトリが見つかりません: E:\\yukinori\\テスト（読み書き可能）'
```

`テスト（読み書き可能）`というフォルダは実在せず、正しいパスは`E:\yukinori\テスト`。LLMが何らかの文脈（ユーザーの入力メッセージ等）から間違ったパスを推測したと見られる。

なお、このセッションではその後メインエージェントがGlob呼び出し上限にも達している（[issue/20260812_154200_main_agent_glob_call_limit_reached.md](20260812_154200_main_agent_glob_call_limit_reached.md)参照）。

## ユーザー回答

ここにはユーザーの回答が記述される
