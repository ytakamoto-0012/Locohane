# リトライ後の初回チャンク受信が126秒遅延（llama-serverスロット詰まりの疑い）

- **区分**: 問題点
- **検知日時**: 2026-08-02 00:58:12
- **対象ログファイル**: data/logs/app_20260802_00_1.log

## 経緯

`Task-111`が00:54:13に初回リクエストを開始し、その後何らかの理由で
00:56:06に「リトライ2回目開始」となった。このリトライ後、最初の
チャンク（thinkingまたは本文）を受信するまでに126秒を要し、
`app.py`側の遅延検知（閾値10秒）がWARNINGとして
「llama-server スロット詰まりの疑い」を出力した。ログ上、HTTPレスポンス
ヘッダー自体は00:58:10に200 OKで返っており、ボディの最初のチャンクが
届くまでの`receive_response_body`区間で待たされている
（`response_closed`が00:58:15に`GeneratorExit`で終了しており、この
リクエストも最終的には打ち切られたとみられる）。

## ログ引用

```
2026-08-02 00:54:13,074 DEBUG app.py: on_message: 初回リクエスト開始 [name='Task-111' id=1279064425040 cancelling=0 cancelled=False must_cancel=False elapsed_ms=0]
2026-08-02 00:56:06,741 WARNING app.py: on_message: リトライ2回目開始 [name='Task-111' id=1279064425040 cancelling=0 cancelled=False must_cancel=False elapsed_ms=0, cancel_scope_breakage_last_60s=0]
2026-08-02 00:58:10,015 DEBUG httpcore.http11: receive_response_headers.complete return_value=(b'HTTP/1.1', 200, b'OK', ...)
2026-08-02 00:58:12,936 WARNING app.py: リトライ後の初回チャンク受信まで126秒（異常遅延） [name='Task-111' id=1279064425040 cancelling=0 cancelled=False must_cancel=False elapsed_ms=0] — llama-server スロット詰まりの疑い
2026-08-02 00:58:15,091 DEBUG httpcore.http11: receive_response_body.failed exception=GeneratorExit()
```

## 推定原因

未検証。`app.py`のログメッセージ自体が「llama-server スロット詰まりの
疑い」と推測しており、`llama-server`の`--parallel`スロット数を超えて
複数リクエストが同時に飛んだ場合や、長いコンテキストのプロンプト
プリフィルに時間がかかっている可能性が考えられる（`config.ini`の
`[llm].stream_chunk_timeout_seconds`コメント参照）。前段の
リトライ2回目自体がなぜ発生したか（何が1回目を失敗させたか）は
このログ範囲からは特定できていない。

## 追記（2026-08-02 09:32）

リトライ後の初回チャンク受信まで24秒の遅延が再発。前回の126秒より大幅に改善したが、閾値（10秒）を超えている。

```
2026-08-02 09:31:51,168 WARNING app.py: on_message: リトライ2回目開始 [name='Task-1892' id=1773157096208 cancelling=0 cancelled=False must_cancel=False elapsed_ms=0, cancel_scope_breakage_last_60s=0]
2026-08-02 09:32:14,788 WARNING app.py: リトライ後の初回チャンク受信まで24秒（異常遅延） [name='Task-1892' id=1773157096208 cancelling=0 cancelled=False must_cancel=False elapsed_ms=0] — llama-server スロット詰まりの疑い
```

## 追記（YYYY-MM-DD HH:MM）

（同一原因の問題が再検知されるたびに、ここに追記を積み重ねていく）

## ユーザー回答

2026-08-02 修正実施済み。`ThinkingLoopDetected` 発生時は常に
`aclose_active_llm_clients()` で旧接続を強制クローズし、
`_rebuild_graph()` でグラフ再構築するフローに統一した。
動作テスト中。
