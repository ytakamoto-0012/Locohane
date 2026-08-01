# Issues - ローカルエージェントシステムの課題一覧

---

## [2026-08-01] Plan Mode の承認判断でエージェントがreasoning loopに陥る

**症状**

`run_script_background` の動作テスト中（一時追加した `time-counter-test` スキルを
`run_script_background` で実行するよう依頼）に、`run_script_background` 自体は
正常に呼び出されて完了しているにもかかわらず、その後のモデルの思考ステップで
「`create_plan` → `approve_plan` を呼ぶべきか」「`approve_plan` はユーザーへの
承認ダイアログを内包しているのでそのまま呼んでよいはずだが、ユーザーに手間を
かけるのは避けたい」という同じ内容の自問自答をほぼ一字一句そのまま何度も
繰り返し、ツール呼び出しに進まないまま「思考中」ステップが止まらなくなった。
UI上は「思考中」が停止可能な状態のまま長時間継続する。

**発生条件**

- モデル: `QWEN3.6_35B-A3B`（config.ini `[llm].model`）
- `run_script_background` など書き込み系ツールの実行前に必要な
  `plan_approved` 承認ゲート（`src\tools.py` `_prepare_script_execution`）に
  最初弾かれ（計画未承認エラー）、その後の再判断で発生

**推定原因（未検証）**

`create_plan`/`approve_plan` ツールの説明文（system_prompt.md 等）で、
「`approve_plan` はユーザー確認ダイアログを内包するので自分の判断で続けて
呼んでよい」という指示と、「ユーザーに手間をかけるべきでない」という
一般的な配慮のどちらを優先すべきかの記述が曖昧／競合しており、モデルが
判断を確定できず同じ結論の再検証を繰り返している可能性がある。

**副次的に見つかった問題**

Chainlitサーバーのコンソールログで以下の `UnicodeEncodeError` が発生：

```
UnicodeEncodeError: 'cp932' codec can't encode character '—' in position ...: illegal multibyte sequence
```

モデル出力に含まれる em-dash（`—`）等の文字を、cp932コンソールへログ出力
しようとして例外（`--- Logging error ---`）になる。処理自体は継続するが、
該当ログ行は欠落する。ロガーのハンドラでUTF-8出力や `errors="backslashreplace"`
等の指定がされていない可能性がある。

**影響**

単純な1ステップのタスクでも、書き込み系ツールを伴うと計画承認フローで
思考ループに陥り、実質的にタスクが完了しなくなるケースがある。

**追記（同日、再現テストで判明）**

Locohane側には既に `ThinkingLoopDetected`（`app.py`、思考ループを検知して
LLMクライアント接続をクローズしグラフを再構築し自動リトライする機構）が
実装されており、今回のケースでもログ上は作動していた
（`20:28:31 - WARNING - app.py - ThinkingLoopDetected: ...リトライ2回目開始`）。
しかしこの自動リトライでは思考ループが解消されず、最終的にユーザーが
手動でセッション停止（`on_stop`）し、新規チャットで最初からやり直すことで
回避した。同一スレッド内での自動リトライだけでは不十分な可能性がある。

なお再試行後は `create_plan` → `approve_plan` → `run_script_background`
→（約5分37秒、LLMへのリクエストなしで待機）→ `update_task_progress: completed`
と正常に完了しており、`run_script_background` 自体（[scripts].timeoutの
300秒を超えるジョブでもタイムアウトしない挙動）は問題なく動作することを確認済み。

**追記2（同日、execute_python_code_background の動作テストで再発）**

`execute_python_code_background`（新規実装）の動作テスト中にも同じ
`ThinkingLoopDetected` が再発した。今回は `21:01:28` に3回目のリトライが
開始した後、`21:05:11` にユーザーが手動でセッション停止（`on_stop`）する
まで**3分半以上応答なしでフリーズ**しており、`run_script_background` の
時より深刻だった。自動リトライ機構が働いても複数回（2回・3回）失敗し続け、
最終的に人間の介入なしには回復しないケースがあることを再確認した。

このテストでは思考ループとは別に、モデルの判断そのものに起因する
2種類の失敗パターンも観測された（`execute_python_code_background` 自体の
バグではなく、エージェントの振る舞いの問題）:

1. ジョブ起動後、`check_script_job` を1〜7秒間隔で連投し、19秒経過時点で
   「330秒は長いから」と自発的に `stop_script_job` で打ち切ってしまった
   （ユーザーは完走を求めていたが、モデルが勝手に判断して打ち切った）
2. `create_plan` を「ジョブ起動」のみの1ステップで作成し、起動直後に
   `update_task_progress` で該当ステップを `completed` にしてしまった結果、
   ジョブ完了を待たずに `plan_approved` がリセットされて Plan Mode に
   戻ってしまった（バックグラウンドジョブの「起動」と「完了待ち・結果確認」
   を別ステップに分けるという設計判断をモデルがしなかった）

**推定原因2（未検証）**

`run_script_background`/`execute_python_code_background` の戻り値
（`src\tools.py` 該当ツール末尾）に含まれる
「途中で打ち切る場合は `stop_script_job` を使ってください」という一文が、
「長時間かかる処理は打ち切るべきもの」という誤読を誘発している可能性がある。
また `check_script_job` の docstring には「短い間隔で連投しない」
「処理時間の長さ自体は打ち切る理由にならない」という振る舞い面のガイダンスが
無く、`create_plan` にも「バックグラウンドジョブは起動ステップと完了確認
ステップを分けること」という誘導が無い。これらのdocstring改善で
成功率が上がる可能性があるが、`run_script_background` は同じ文言でも
1回目失敗・2回目成功だったため、モデルのサンプリングの非決定性による
振れ幅の影響も否定できず、docstring修正だけで再現しなくなるとは限らない。

**追記3（同日、原因調査・部分対応）**

サブエージェントによる調査と、実際の思考ループログ（スクリーンショット）の
精読の結果、当初の「推定原因（未検証）」（承認ダイアログ許可と手間への配慮の
競合）は誤りと判明した。実際のログでは、モデルは毎回「`create_plan` →
`approve_plan` を呼ぶ」という結論に到達し、計画の中身（ステップ・activeForm）
まで具体的に生成していたにもかかわらず、そこから実際のツール呼び出しに移行
せず「いや、待てよ」「よく考えたら」で振り出しに戻る、という**結論後も同じ
検証を再生成し続ける自己回帰的な反復**だった。

原因は `system_prompt.md` の「Plan & Progress」節（ステップ1）にある
「`create_plan` を呼ぶ直前の自己チェック」（自問すること）および「NG例」
（自分の判断を疑うこと）という、行動直前に必ず自己懐疑させる指示が、
本来ステップ1（調査十分性の確認）限定であるにもかかわらず、モデルの
振る舞いパターンとして汎化し、明示的な自問指示の無いステップ3
（`approve_plan` を呼ぶ）にまで漏れ出していたと推定される。ステップ3
自体は「自分の判断でそのまま呼んでよい」と既に明記されていたが、この
許可文言を読んでは疑い、また読んでは疑う、を繰り返す状態に陥っていた。

なお config.ini 側のサンプリングパラメータ（`repeat_penalty`/`dry_*`等）は
過去に検証済みでこれ以上の調整余地は無いことを確認済み。

**対応**

- `system_prompt.md` のステップ3冒頭に「ここでは自問・再検証は不要」
  「ステップ1の自問指示はステップ3には適用されない」「再検討し始めたら
  直ちに中断してそのまま approve_plan を呼ぶ」旨を明記する一文を追加済み。
  効果は再現テストで要確認。
- `UnicodeEncodeError`（cp932）については、`evals/run_case.py`・
  `evals/run_all.py` には `sys.stdout.reconfigure(encoding="utf-8")` による
  対策が既にあるが、`app.py`（Chainlitサーバー本体）には同様の対策が
  無いことを確認した。リポジトリ内に `StreamHandler` は存在せず、コンソール
  出力は `chainlit run` がパッケージ内部で独自に `logging.basicConfig()` する
  ため cp932依存になっている。`app.py` 起動時にも同様の `reconfigure` を
  追加すれば解消する見込みだが、未対応。
- `ThinkingLoopDetected` の自動リトライが複数回失敗する件について、
  `_rebuild_graph`/`on_stop` はいずれも LangGraph の checkpointer を
  使い回す実装であり、**ループを引き起こした直前のAIMessage・ツール呼び出し
  履歴はクリアされず、nudgeメッセージ1件が追記されるだけで同じ文脈のまま
  再送される**ことを確認した。これが「リトライしても解消しない」ことの
  構造的原因と考えられる。ユーザーが「新規チャットで回避できた」のは
  `on_stop` 自体ではなく、新しい `thread_id`（新しいチェックポイント系列）を
  発行する `on_chat_start` によるものだった。リトライ時にループ原因となった
  直近メッセージをcheckpointerから除去する等の対応は未実施。
- `check_script_job`/`stop_script_job`/`run_script_background`/
  `execute_python_code_background` の戻り値・docstring改善は対応済み
  （`src\tools.py`）。
  - 起動直後の案内文を `_background_job_started_message()` に共通化し、
    「途中で打ち切る場合は stop_script_job を使ってください」という
    誤読を招く表現をやめ、「処理に時間がかかっていること自体は打ち切る
    理由にならない。ユーザーから明示的に中断を指示された場合にのみ使う」
    と明記。
  - `check_script_job` に「実行中が返ってきても数秒間隔で連投せず、経過を
    伝えたらターンを終えるか十分な間隔を空けて呼び直す」旨を追記。
  - `stop_script_job` に「ユーザーの明示的な指示がある場合のみ使う」旨を追記。
  - `create_plan`/`update_task_progress` に「run_script_background/
    execute_python_code_background は『起動』と『完了確認』を別ステップに
    分け、起動しただけの時点では completed にしない」旨を追記（1ステップの
    計画を作って起動直後に completed → plan_approved リセット →
    不要な計画やり直しループ、という今回の失敗パターンへの対策）。
  - `approve_plan`/`create_plan` の「書き込み系ツール」列挙に
    `run_script_background`/`execute_python_code_background` が抜けていた
    点も追記して補完。
  - 再現テストによる効果検証は未実施。

---
