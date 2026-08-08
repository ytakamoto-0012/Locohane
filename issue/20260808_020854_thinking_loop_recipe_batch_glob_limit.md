# ThinkingLoopDetected: レシピ画像変換バッチ（Glob呼び出し上限がトリガー）

- **区分**: 問題点
- **検知日時**: 2026-08-08 02:25:00
- **対象ログファイル**: data/logs/app_20260808_020533.log

## 経緯

ユーザーが `images` フォルダ（340枚の料理写真）の画像を読み取り、`md` フォルダへレシピ内容のMarkdownファイルを生成するタスクを依頼。メインエージェントが Glob ツールを1回呼び出した後、「既に呼び出し上限に達している」旨のWARNINGを受け、explore/workerへの委譲を試みるも、LLM応答のループ（ThinkingLoopDetected）を検知して4回までのリトライを繰り返した。最終的に dispatch_agent_background が失敗し、check_dispatch_agent_job もエラーを返した。

## ログ引用

```
2026-08-08 02:08:56,877 WARNING src.tools: tool_result: name=Glob content='エラー: Glob はメインエージェントとして既に呼び出し上限（1回）に達しています（対象ルート直下の確認用の例外のみ）。これ以上フォルダを深掘りせず、残りの調査は dispatch_agent（explore/explore-docs/worker）へ委譲してください。'
2026-08-08 02:09:36,374 WARNING src.llm: LLM応答のループを検知したため生成を打ち切ります（直近テキスト: '像ファイル（JPG/PNG/HEIC等）がどのくらいあるかを確認する必要がある。\n\n計画を作成する前に、まずはimagesフォルダの画像ファイルの数を正確に把握し...' ）
2026-08-08 02:09:36,377 WARNING app.py: LLM応答のループを検知（1回目の再試行）: 直近テキスト='像ファイル（JPG/PNG/HEIC等）がどのくらいあるかを確認する必要がある。\n\n計画を作成する前に...'
2026-08-08 02:09:36,853 WARNING app.py: ThinkingLoopDetected: リトライ前にLLMグラフを再構築しました
2026-08-08 02:09:52,242 WARNING app.py: リトライ後の初回チャンク受信まで15秒（異常遅延） [name='Task-98' ...] — llama-server スロット詰まりの疑い
2026-08-08 02:17:56,841 WARNING app.py: ThinkingLoopDetected: リトライ前にLLMグラフを再構築しました
2026-08-08 02:17:56,841 WARNING app.py: on_message: リトライ3回目開始
2026-08-08 02:24:38,145 ERROR src.tools: dispatch_agent_background 失敗 (run_id=56e798f5921c4264880bddd5bc6f45a2)
2026-08-08 02:24:38,177 WARNING app.py: on_message: リトライ4回目開始
2026-08-08 02:25:30,094 WARNING src.tools: tool_result: name=check_dispatch_agent_job content='エラー: サブエージェントの実行に失敗しました: '
```

## 推定原因

1. **Glob呼び出し上限（1回）の制約**: メインエージェントは1ターンあたりのGlob呼び出しが1回に制限されている（`[main_agent_glob_guard] max_calls = 1`）。今回のタスクでは `images/**/*` と `md/**/*` の2回を一度に呼び出したく、1回目が上限に達した。
2. **LLMの委譲ループ**: 上限到達後、explore/workerへの委譲を試みるも、同じ内容の思考（thinking）を繰り返すループに陥り、ThinkingLoopDetectedが4回発火。
3. **llama-serverスロット詰まり**: リトライ後の初回チャンク受信まで15秒〜27秒を要しており、llama-serverのスロットが詰まっていた疑い。
4. **dispatch_agent_backgroundの失敗**: 最終的にバックグラウンドサブエージェントの実行に失敗し、結果も空文字列だったため、全体タスクが未完成で終了。

## 追記（2026-08-08 02:25）

初回検知。340枚の画像を一度に処理する大規模バッチタスクでは、Glob上限の制約とLLMの委譲ループが組み合わさって失敗しやすい傾向がある。

## ユーザー回答
