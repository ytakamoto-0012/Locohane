# メインエージェントがGlobを複数回直接呼び出しし呼び出し上限に到達

- **区分**: 改善点
- **検知日時**: 2026-08-12 16:00:00
- **対象ログファイル**: data/logs/app_20260812_154052.log

## 経緯

メインエージェントが年間行事予定表の作成タスクを処理中、作業ディレクトリ構造の確認のためにGlobツールを1回呼び出した後、さらに4回のGlobを同一ターンで並列呼び出ししようとした。しかし、`[main_agent_tool_guard]` の設定によりGlobの呼び出し上限が1回に制限されていたため、初回呼び出し以降の4回がすべてエラーで拒否された。

エージェントはGlobによる直接調査を諦め、dispatch_agent（exploreエージェント）へ委譲して調査を続行した。

## ログ引用

```
2026-08-12 15:42:04,340 WARNING src.tools: tool_result: name=Glob content='エラー: Glob はメインエージェントとして既に呼び出し上限（1回）に達しています。これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: explore, explore-docs, verifier, worker）へ委譲してください。'
2026-08-12 15:42:04,340 WARNING src.tools: tool_result: name=Glob content='エラー: Glob はメインエージェントとして既に呼び出し上限（1回）に達しています。これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: explore, explore-docs, verifier, worker）へ委譲してください。'
2026-08-12 15:42:04,340 WARNING src.tools: tool_result: name=Glob content='エラー: Glob はメインエージェントとして既に呼び出し上限（1回）に達しています。これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: explore, explore-docs, verifier, worker）へ委譲してください。'
2026-08-12 15:42:04,340 WARNING src.tools: tool_result: name=Glob content='エラー: Glob はメインエージェントとして既に呼び出し上限（1回）に達しています。これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: explore, explore-docs, verifier, worker）へ委譲してください。'
```

## 推定原因

`[main_agent_tool_guard]` セクションの `entries` で `["Glob", 1]` が設定されており、メインエージェントが1ターン内でGlobを呼び出せる回数が1回に制限されている。これは小型ローカルモデルが進捗の無いまま同じ呼び出しを繰り返し行う事例への対策だが、今回のように並列呼び出しが必要なケースでは制限が厳しすぎる可能性がある。

エージェントは制限に達した後、適切にdispatch_agentへ委譲しているので、バグではなく「並列呼び出しが必要なケースの扱い」が改善点として残る。
