# トークン上限超過エラー（160,468トークン、507,527トークン）→ コンテキスト圧縮失敗

- **区分**: バグ
- **検知日時**: 2026-08-08 20:50:00
- **対象ログファイル**: data/logs/app_20260808_172838.log

## 経緯

レシピ画像変換バッチタスク（IMG_2299〜IMG_2358のMDファイル作成）において、メインエージェントのコンテキストが128,000トークンの上限を大幅に超えるまで膨張した。最初の失敗では160,468トークンでコンテキストサイズ超過エラーが発生し、その後コンテキスト圧縮（context_compaction）を試みたが、これも507,527トークンで再び上限超過エラーが発生して失敗した。

## ログ引用

```
2026-08-08 17:31:17,612 ERROR src.tools: dispatch_agent_background 失敗 (run_id=92cd1f48fdb54eaf87ff025ed53828a9)
2026-08-08 17:31:17,619 WARNING src.tools: tool_result: name=dispatch_agent_background content="エラー: サブエージェントの実行に失敗しました: Error code: 400 - {'error': {'code': 400, 'message': 'request (160468 tokens) exceeds the available context size (128000 tokens), try increasing it', 'type': 'exceed_context_size_error', 'n_prompt_tokens': 160468, 'n_ctx': 128000}}"
2026-08-08 17:31:21,320 ERROR src.context_compaction: 会話履歴の自動要約に失敗しました。今回は圧縮をスキップします
2026-08-08 17:31:21,320 openai.BadRequestError: Error code: 400 - {'error': {'code': 400, 'message': 'request (507527 tokens) exceeds the available context size (128000 tokens), try increasing it', 'type': 'exceed_context_size_error', 'n_prompt_tokens': 507527, 'n_ctx': 128000}}
```

## エラー原文

```
openai.BadRequestError: Error code: 400 - {'error': {'code': 400, 'message': 'request (160468 tokens) exceeds the available context size (128000 tokens), try increasing it', 'type': 'exceed_context_size_error', 'n_prompt_tokens': 160468, 'n_ctx': 128000}}

openai.BadRequestError: Error code: 400 - {'error': {'code': 400, 'message': 'request (507527 tokens) exceeds the available context size (128000 tokens), try increasing it', 'type': 'exceed_context_size_error', 'n_prompt_tokens': 507527, 'n_ctx': 128000}}
```

## 推定原因

1. **大規模バッチタスクの負荷**: 20件×2グループ（計40件）の画像からMDファイルを作成する大規模バッチタスクにおいて、画像認識結果、栄養情報、Pythonコード、ツール呼び出しの結果などが会話履歴に累積し、コンテキストが爆発的に膨張した。
2. **コンテキスト圧縮のタイミング遅れ**: 160,468トークンに達した時点でコンテキスト圧縮が触发されなかった、または圧縮処理自体が会話履歴の大きさを十分に把握できていなかった可能性がある。
3. **圧縮時の再失敗**: コンテキスト圧縮処理自体がLLM呼び出しを含むが、その際にも507,527トークンという巨大なコンテキストが指定され、圧縮処理自体が上限超過エラーで失敗した。

## 追記（2026-08-08 20:50）

初回検知。トークン上限超過が2回連続で発生（160,468トークン、507,527トークン）。両方とも128,000トークンの上限を大幅に超えている。

## ユーザー回答
