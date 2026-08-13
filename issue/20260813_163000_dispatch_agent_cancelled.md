# dispatch_agent 失敗（CancelledError）

- **区分**: 問題点
- **検知日時**: 2026-08-13 16:30:00
- **対象ログファイル**: data/logs/app_20260812_203024.log, app_20260812_234641.log

## 経緯

`dispatch_agent` 呼び出し中にユーザーがキャンセル（またはタイムアウト）し、`CancelledError` が発生。チェックポイントの修復のために孤立した tool_calls にプレースホルダの ToolMessage が補完された。

## ログ引用

```
2026-08-12 20:35:16,810 WARNING app.py: on_message: CancelledErrorを検知 [name='Task-2162' id=2533461042384 cancelling=1 cancelled=False must_cancel=False elapsed_ms=0, cause='None', context='None']
2026-08-12 20:35:16,810 ERROR src.tools: dispatch_agent 失敗 (run_id=b02f1f26e2ee4929bd39f5765447424a)
2026-08-12 20:35:16,835 WARNING app.py: on_message: CancelledErrorを検知し、孤立したtool_calls(1件)にプレースホルダのToolMessageを補完してチェックポイントを修復しました

2026-08-12 20:46:31,735 ERROR src.tools: dispatch_agent 失敗 (run_id=86f3232ab03d4372936d8b7f7439b90d)
2026-08-12 20:46:31,747 WARNING app.py: on_message: CancelledErrorを検知 [name='Task-19152' id=2533468974288 cancelling=1 cancelled=False must_cancel=False elapsed_ms=0, cause='None', context='None']
2026-08-12 20:46:31,754 WARNING app.py: on_message: CancelledErrorを検知し、孤立したtool_calls(1件)にプレースホルダのToolMessageを補完してチェックポイントを修復しました

2026-08-13 01:11:32,367 WARNING app.py: on_message: CancelledErrorを検知 [name='Task-170587' id=2167896177168 cancelling=1 cancelled=False must_cancel=False elapsed_ms=0, cause='None', context='None']
2026-08-13 01:11:32,367 ERROR src.tools: dispatch_agent 失敗 (run_id=9b8042f699d343419efd86ec831af8cb)
2026-08-13 01:11:32,410 WARNING app.py: on_message: CancelledErrorを検知し、孤立したtool_calls(1件)にプレースホルダのToolMessageを補完してチェックポイントを修復しました
```

## 推定原因

3件の発火があり、すべてユーザーによる明示的キャンセルまたは長時間実行時のタイムアウトが原因。`dispatch_agent` の実行時間が `[scripts].background_max_runtime_seconds`（3600秒）に近づいた場合、またはユーザーが手動でキャンセルした場合に発火する。チェックポイント修復の仕組みは正常に動作している。

## 追記（2026-08-13 16:30）

- 初回検知。3回発生。

## ユーザー回答
