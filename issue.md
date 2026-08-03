# Issues - ローカルエージェントシステムの課題一覧

---

## ISSUE-003: approve_planでブラウザ再フォーカス時に承認ボタンが消える + 次回整合性エラー

- **状態**: 調査中
- **報告日**: 2026-08-02
- **優先度**: 中

### 現象

1. `approve_plan`ツールでユーザーに計画の承認/拒否を促す承認ボタンが表示される
2. ユーザーがブラウザのタブを再フォーカス（WindowsのIME切り替え等）すると、**ボタンが一瞬で消える**
3. 次回メッセージ送信時に以下のエラーが発生する:
   ```
   ValueError: Found AIMessages with tool_calls that do not have a
   corresponding ToolMessage. Here are the first few of those tool calls:
   [{'name': 'approve_plan', 'args': {}, ...}]
   ```

### 原因

1. `approve_plan`は`cl.AskActionMessage.send()`でユーザー応答待ちの**ブロッキング状態**にある
2. ブラウザ再フォーカス（WindowsのIME切り替え等）でWebSocketが切断 → `send()`が`CancelledError`
3. `app.py`の`CancelledError`ハンドリング（行1473-1521）でToolMessage補完を試みるが、`graph.aupdate_state()`が`_CheckpointerTimeout`で失敗
4. 補完がスキップされたままチェックポイントが更新される
5. 次回メッセージ送信時にチェックポイント履歴読み込み段階で「tool_callsに対応するToolMessageが無い」エラー

### 関連ファイル

- `app.py`:
  - 行2441: `approve_plan`の`AskActionMessage.send()`呼び出し
  - 行1473-1521: `CancelledError`ハンドリングとToolMessage補完処理
  - 行1503-1517: `graph.aupdate_state()`による補完コミット
  - 行1093-1107: `_find_orphaned_tool_calls()`関数
  - 行660-670: `_rebuild_graph()`関数
- `src/tools.py` 行2416-2467: `approve_plan`ツール実装

### 修正案

**案1: `_rebuild_graph()`の冒頭に整合性修復処理を追加**

`_rebuild_graph()`でチェックポイント再構築後、`_find_orphaned_tool_calls()`で孤立したtool_callsを検出し、`aput_writes()`で補完ToolMessageを書き込む。既存の`app.py`行1503-1517のロジックを流用。

**案2: `approve_plan`に`asyncio.wait_for`によるタイムアウトラップ**

`cl.AskActionMessage.send()`を`asyncio.wait_for`で囲み、ブラウザの再フォーカスによる一時的WebSocket切断をタイムアウトで許容する。タイムアウト発生時は既存のタイムアウト処理経路（`res is None`）へ流す。

### 検証方法

1. `approve_plan`を実行 → 承認ボタンを表示
2. ブラウザのタブを再フォーカス（WindowsのIME切り替え等でWebSocketが切断される操作）
3. ボタンが消えない、または消えても次回メッセージ送信時に整合性エラーが出ないことを確認

---

## ISSUE-004: 会話履歴自動要約(context_compaction)失敗時にメインエージェント累積トークンがリセットされず積み上がり続ける

- **状態**: 調査中
- **報告日**: 2026-08-04
- **優先度**: 中

### 現象

トークン使用量パネルの「メインエージェント累計」が、`config.ini`の
`[context_compaction].token_threshold`（81920）を大幅に超えた値
（例: 990,403）のまま表示され続ける。本来は閾値超過で圧縮が発火し、
発火のたびに0へリセットされるはずの値。

### 原因

1. パネル表示の「メインエージェント累計」は `cl.user_session["token_usage_cumulative_main"]` そのもの（`app.py`の`_format_token_usage`が表示、`should_compact`が閾値判定に使うのも同じ変数）
2. 各ターン終了時、`app.py`の`on_message`終盤（行1708-1720）で`should_compact()`が閾値超過を検知すると`maybe_compact()`で要約を試みる
3. 要約用LLM呼び出し自体が失敗すると`maybe_compact()`が`None`を返す
4. `if new_messages is not None:`の分岐に入らないため、`graph.aupdate_state()`によるメッセージ差し替えも、続く`token_usage_cumulative_main`のリセット（行1720）も実行されない
5. 結果、圧縮が失敗するたびに累積トークンだけが際限なく積み上がり続ける

既知の実例として `issue/20260802_104012_context_compaction_summary_failed.md`（要約処理失敗のログ）があり、これと同一原因と推定される。

### 関連ファイル

- `app.py`:
  - 行1708-1720: `should_compact`判定〜`maybe_compact`呼び出し〜リセット処理
  - 行359-371: `_format_token_usage()`（パネル表示のフォーマット）
- `src/context_compaction.py`:
  - 行28-68: `should_compact()`（閾値判定ロジック）
  - `maybe_compact()`: 要約LLM呼び出し失敗時に`None`を返す箇所
- `issue/20260802_104012_context_compaction_summary_failed.md`: 関連する既知の要約失敗ログ

### 修正案

- `maybe_compact()`が`None`を返した（要約失敗）場合でも、無限にリトライして累積が肥大化し続けないよう、失敗時のフォールバック（例: 古いToolMessageの単純truncateへのフォールバック、または失敗時も一定回数ごとに強制リセットして次ターンで再試行）を検討する
- 要約失敗の根本原因（プロンプトがcontext window超過している/LLM側タイムアウト等）を`data/logs/`のERRORログから特定する

### 検証方法

1. 要約が失敗するよう意図的に誘導する（例: 要約用LLM呼び出しがエラーになる状況を再現）、または過去に要約失敗ログが出た会話を継続する
2. `token_usage_cumulative_main`が閾値を超えた状態で複数ターン経過してもリセットされず積み上がることを確認
3. 修正後は、要約失敗時も累積が無限に肥大化しないことを確認
