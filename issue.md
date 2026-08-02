# Issues - ローカルエージェントシステムの課題一覧

---

## ISSUE-004: 画像レシピ抽出バッチ（297枚）がThinkingLoopGuard発火で途中停止（未完遂）

- **状態**: 調査中
- **報告日**: 2026-08-03
- **優先度**: 高

### 現象

`E:\akiyo\レシピ\images` 内の画像297枚からレシピ情報を抽出しmdファイル化するバッチタスクにおいて、全体約30ステップ中ステップ11（画像111〜120件目）で処理が停止。「生成がループし、3回リトライしましたが改善しなかったため停止しました。」という自動停止メッセージで終了し、タスクは未完遂（進捗は全体の3分の1程度）。

関連ログ: `data/logs/app_20260803_00_1.log`, `app_20260803_00_2.log`, `app_20260803_01.log`, `app_20260803_01_1.log`, `app_20260803_01_2.log`

関連する自動起票issue:
- `issue/20260803_004600_thinking_loop_dispatch_agent_recipes.md`
- `issue/20260803_004600_cancellederror_and_slot_congestion.md`
- `issue/20260803_004600_execute_python_code_plan_approval_required.md`

### 原因（調査済み・複合要因）

1. **一次原因（モデルの指示追従失敗・既知パターンへの回帰）**
   `system_prompt/system_prompt.md:274-336` には「1回のdispatch_agentには`${subagent_max_iterations}`(=10)件を目安にする」「回数が数十回になっても非現実的と判断してはいけない」という、まさにこの障害を狙い撃ちした明文ルールが既に存在する（2026-07-31の同一失敗パターンへの対策として追記済み、`evals/tuning_log.md:2565-2577`参照）。
   にもかかわらず、画像111〜120件目という深い段階でモデルがこのルールを適用できず、1件=1委譲換算で「258回」を算出し、「258回も呼ぶのは非現実的」と自己否定→別案検討→同じ結論、という段落単位の反復を生成した（`src/llm.py:282-372` の `_ThinkingLoopDetector` が1回の生成内の文字列反復として検知、`match_ratio_threshold=0.4`超過が2回連続で確定）。

2. **二次原因（構造的な誘因）**
   `analyze_image`（`src/tools.py:2687-2754`）の結果は `ToolMessage.content` には入らず、次のAIMessageにしか現れない設計。かつメインエージェント自身は `analyze_image` を持てない設計（`system_prompt.md:334-335`）。このためモデルは「execute_python_codeからanalyze_imageを呼べない」という制約に繰り返し直面し、そのたびにジレンマを再言語化してループの燃料にしていた。

3. **増幅要因（計画承認フローの実害）**
   `create_plan` を誤って再度呼ぶと `plan_approved` がリセットされる（`src/tools.py:2406`）。混乱したモデルが計画を作り直そうとするたびに `execute_python_code`/`Glob` がブロックされ続け、同一夜間に7回連続でエラーが発生（`issue/20260803_004600_execute_python_code_plan_approval_required.md` 参照）。

4. **リトライ機構の限界**
   `ThinkingLoopDetector`（`src/llm.py:282-372`）は1回の生成内の文字列反復しか検知できず、複数ターンにまたがる「同じ結論への収束」は検知対象外。リトライ経路（`src/graph.py` の `ainvoke_ensuring_final_text`、`src/subagent.py` の `_invoke_with_loop_retry`）は汎用nudge注入とLLMグラフ再構築のみを行い、誤った計画方針そのものは訂正しないため、同日中に4回検知されるも根治しなかった。

5. **並行して観測された別系統の不具合（cancel scope RuntimeError）**
   `langchain_openai` の `_astream_with_chunk_timeout`（`_client_utils.py:650`）が `asyncio.wait_for()` でストリーム取得を別タスク化しており、httpx/httpcore/anyioが開く`CancelScope`の「開いたタスクでしか閉じられない」制約に抵触して `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in` が発生（`Exception ignored in`ログとして観測）。`config.ini` に既にこの現象への仮説と実験計画（`stream_chunk_timeout_seconds=0`にすると再現しなくなるか検証する）が記載されているが未実施。この事象自体はタスク停止の直接原因ではなく、並行して発生していたllama-serverスロット競合（初回チャンク遅延最大242秒）の周辺症状。

### 関連ファイル

- `system_prompt/system_prompt.md:274-336` — グループ化ルールの明文規定
- `src/llm.py:282-372` — `_ThinkingLoopDetector`実装
- `src/llm.py:600-705` — `_astream_guarded`（ストリーミング・cancel処理）
- `src/graph.py:249-354` — `ainvoke_ensuring_final_text`（リトライ経路）
- `src/subagent.py:117-208` — `_invoke_with_loop_retry`
- `src/tools.py:2406` — `create_plan`再呼び出し時の`plan_approved`リセット
- `src/tools.py:2687-2754` — `analyze_image`実装
- `config.ini` [llm] `stream_chunk_timeout_seconds`（63-78行目付近）
- `config.ini` [llm] `max_concurrent_requests`（未コミットで追加実装中、`src/llm.py`の`_LLM_REQUEST_SEMAPHORE`）

### 修正案

1. `create_plan`の再呼び出しガード — 既に承認済みで内容が実質同一の場合は`plan_approved`をリセットしない、または再呼び出し前に警告を返す（`src/tools.py:2406`周辺）
2. グループ分割をLLMの暗算に委ねない — `${subagent_max_iterations}`件ごとの分割案（開始/終了番号のリスト）をツール側で機械的に提示する
3. `ThinkingLoopDetector`に軽量な意味的パターン検知を追加 — 「現実的ではない」等の停滞フレーズを検知し、より具体的なnudge（例:「create_planは既に承認済みです」「1件ずつではなく${subagent_max_iterations}件ずつグループ化してください」）を注入する
4. リトライ経路で直近のツール結果（計画未承認エラーの連続など）を検査し、単なるnudgeでは解決しない構造的な行き詰まりには強い介入を入れる
5. `stream_chunk_timeout_seconds=0`の実験を本番反映し、cancel scope RuntimeErrorが再現しなくなるか確認する（config.ini記載の未実施実験）

### 検証方法

1. 297枚規模の画像バッチ処理を再実行し、ステップ11相当（100件超）まで到達してもThinkingLoopGuardが発火しないことを確認
2. `create_plan`を意図的に複数回呼び出しても`execute_python_code`がブロックされないことを確認
3. `stream_chunk_timeout_seconds=0`設定下で長時間実行し、`Attempted to exit cancel scope`エラーが再発しないことを確認

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
