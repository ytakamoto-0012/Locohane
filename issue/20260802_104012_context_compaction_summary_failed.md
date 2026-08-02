# 会話履歴の自動要約に失敗（context_compaction）

- **区分**: バグ
- **検知日時**: 2026-08-02 10:40:12
- **対象ログファイル**: data/logs/app_20260802_10.log

## 経緯

トークン上限到達によりメインエージェントが引継ぎプロンプトを生成した直後、
`src.context_compaction` による会話履歴の自動要約処理が失敗した。
圧縮がスキップされたため、チェックポイント上のメッセージ数が
そのまま保持され続ける。

## ログ引用

```
2026-08-02 10:40:12,816 ERROR src.context_compaction: 会話履歴の自動要約に失敗しました。今回は圧縮をスキップします
```

## エラー原文

```
ERROR src.context_compaction: 会話履歴の自動要約に失敗しました。今回は圧縮をスキップします
```

## 推定原因

未検証。`context_compaction.py` 内の要約処理（LLMへの要約依頼）が
何らかの理由で失敗したものと推測される。具体的には:

- 直前のトークンガード発動でLLM応答が途切れた影響
- 要約プロンプトのコンテキストが超限界に達していた
- チェックポイントのメッセージ数が `min_messages_to_compact` を超えていない

`config.ini` の `[context_compaction]` セクションの設定:
- `token_threshold`: 49152
- `min_messages_to_compact`: 10
- `keep_recent_turns`: 2

## 追記（YYYY-MM-DD HH:MM）

（同一原因の問題が再検知されるたびに、ここに追記を積み重ねていく）

## ユーザー回答

様子見。

