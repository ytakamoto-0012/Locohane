# evals — プロンプト資産の自動ループテスト・チューニング

`system_prompt/system_prompt.md`・`skills/*/SKILL.md`・`src/tools.py` の各ツール
docstring といった「LLM に渡すプロンプト資産」を、実際のローカル LLM
（llama.cpp server）を動かして評価し、失敗があれば ClaudeCode が修正して
再評価する、というループを回すための仕組み。

## 前提

- llama.cpp server が `config.ini` の `[llm].main_url` に設定された接続先で
  起動していること（複数接続先・時間帯切替の設定もあり得るため、実際に
  使われる `base_url` は既定値を仮定せず `config.ini` を直接確認する）。
  起動していない場合、各ケースは `error: llm_unreachable` を返す。
- Chainlit サーバーは起動しない。`run_case.py` が `evals/headless_chainlit.py`
  で `chainlit` の UI 呼び出し（`cl.user_session` / `cl.Message` /
  `cl.AskActionMessage` / `cl.AskUserMessage`）をスタブに差し替え、
  グラフ（`src/graph.py`）を直接 `ainvoke` する。
- `recursion_limit`（`config.ini` の `[graph].recursion_limit`、既定50）・
  checkpointer（`AsyncSqliteSaver`、`:memory:` でファイルI/Oなし）は
  本番 `app.py` と同じ設定・実装を使う。`GraphRecursionError` /
  `ThinkingLoopDetected` が発生した場合も、本番同様そのターンだけ打ち切って
  会話を継続する（会話全体を `mid_turn_exception` として中断させない）。
  どのターンで打ち切りが起きたかは結果 JSON の `turn_cutoffs`
  （`[{"turn_index": ..., "reason": "recursion_limit"|"thinking_loop"}]`）に
  記録される。

## 実行方法

```
python evals/run_all.py system_prompt
```

`evals/cases/<target>/*.yaml` を全件、直列にサブプロセス実行する
（ローカル1台の llama.cpp server に同時多重リクエストをかけないため）。
結果は `evals/results/<target>/<timestamp>/results.json` /
`summary.md` に保存され、標準出力にも同じサマリが表示される
（`evals/results/` は再生成可能なデータのため `.gitignore` 対象）。

1ケースだけ試したい場合:

```
python -m evals.run_case evals/cases/system_prompt/001_skill_routing_pdf.yaml
```

## ケースの書き方（`evals/cases/<target>/*.yaml`）

```yaml
id: skill_routing_pdf              # 一意な識別子
target: system_prompt              # チューニング対象カテゴリ（ディレクトリ名と一致させる）
turns:                              # 1スレッドの中でユーザーが順に送るメッセージ
  - "この請求書PDFからテーブルを抜き出してExcelにして"
expect:                              # ルールベース判定（省略可、以下はすべて省略可）
  tool_called_any: [read_skill]      # このいずれかが1回以上呼ばれていれば合格
  tool_not_called: [execute_python_code]   # これらが1回も呼ばれていなければ合格
  tool_call_args_contains:           # 該当ツールの呼び出しに指定引数が含まれるか
    read_skill: {skill_name: "pdf-tools"}
  response_contains: ["Excel"]       # 最終回答に含まれるべき文字列
  response_not_contains: ["申し訳ありません"]  # 含まれてはいけない文字列
judge: |                             # 自由記述の判定基準（省略可）。
  ClaudeCode が transcript を読んで合否判定する。expect と併用可、
  どちらか一方でもよいが両方無いケースは無効。
auto_approve: true                   # run_script/execute_python_code/approve_plan の
                                      # 承認ダイアログを自動承認(true)/拒否(false)するか
scripted_text_answers: []            # AskUserQuestion が labels 省略で呼ばれるたびに1件ずつ消費して返す回答
work_dir: "evals/fixtures/xxx"       # run_script/execute_python_code/view_image の既定
                                      # 作業ディレクトリをこのケース専用に固定したい場合の
                                      # プロジェクトルート相対パス（省略可、既定は config.ini
                                      # の [default_workdir].dir）
timeout_seconds: 3600                # run_all.py がサブプロセス実行する際のタイムアウト秒数
                                      # （省略可、既定は run_all.py の CASE_TIMEOUT_SECONDS=900。
                                      # 大量ファイルを扱う重量級ケースの上書き用）
notes: "人間向けの補足メモ（判定には使わない）"
```

- `expect` はルールベースで `run_case.py` がその場で pass/fail を出す。
- `judge` は ClaudeCode（人間の代わりに読む側）が transcript を読んで判断する
  自由記述の指示。ツール呼び出しの機械的な有無では判定しづらい「捏造していないか」
  「委譲判断が妥当か」といった観点に使う。

## 対象カテゴリ（`target`）を増やす場合

`evals/cases/<新しいtarget名>/` にケースを追加すれば、`run_case.py` /
`run_all.py` は変更なしでそのまま動く（`system_prompt` に限定した実装は無い）。
ただし現状 `run_case.py` は `system_prompt.md` を毎回ディスクから読み直す
前提の設計なので、`skill` や `tool_docstring` を対象にする場合も同様に
「チューニング対象ファイルは常にディスク上の現在の内容を読む」という設計を保つこと。

`system_prompt_scale`（`evals/cases/system_prompt_scale/`）は
`system_prompt/system_prompt.md` を対象にする点は `system_prompt` と同じだが、
`evals/fixtures/annual_schedule_large`（実データ規模を再現した大量ファイル
フィクスチャ、`python evals/fixtures/generate_annual_schedule_fixture.py
--preset large` で生成）を使う重量級ケース専用のカテゴリで、
`/tune-prompt system_prompt` の自動ループ（毎イテレーション全件実行）には
含めない。`python evals/run_all.py system_prompt_scale` で手動実行する。

## チューニングループ本体

`.claude/skills/tune-prompt/SKILL.md` が、評価の実行・失敗分析・
対象ファイルの修正・スナップショット退避・再評価というループの手順書。
ClaudeCode で `/tune-prompt system_prompt` のように実行する。

- 編集前のスナップショットは `evals/history/<target>/` に退避される。
- 何を・なぜ変えたかは `evals/tuning_log.md` に追記される。
- イテレーション上限（既定10回）に達したら、途中経過を報告して停止する。
- git へのコミットは行わない（スナップショットとログのみで変更履歴を追える）。

## `config_timeouts` ターゲット（timeout系設定のチューニング）

`system_prompt` 等がプロンプト資産の**テキスト品質**を対象にするのに対し、
`config_timeouts`（`evals/cases/config_timeouts/`）は `config.ini` の
`[llm].request_timeout_seconds` / `[llm].stream_chunk_timeout_seconds` /
`[scripts].timeout` という**実測タイムスタンプに基づく数値パラメータ**を
対象にする、別系統のチューニングターゲット。

- `run_config`（`RunnableConfig`）に `evals/timing_callbacks.py` の
  `LatencyCallbackHandler` を `callbacks` として渡し、LLM呼び出し・
  `run_script`/`execute_python_code` の所要時間をターンごとに実測する
  （本番コード `src/` は変更しない）。
- 結果 JSON の各ケースに `turn_timings`（`token_usage_by_turn` と並列の構造、
  ターンごとの `max_llm_total_seconds` / `max_stream_chunk_gap_seconds` /
  `max_script_seconds` / 生データ）が追加される。
- 推奨値算出:
  ```
  python evals/analyze_timing.py config_timeouts
  ```
  最新の `evals/results/config_timeouts/<timestamp>/results.json` を集計し、
  現在値・実測最大値・推奨値・差分のテーブルを表示、
  同ディレクトリに `recommendations.json` を書き出す。**config.ini は
  直接書き換えない**（推奨値の提示のみ）。
- 実際のチューニングループは `.claude/skills/tune-config-timeouts/SKILL.md`
  を参照（イテレーション上限3回、`[user_response_timeouts]` セクションは対象外）。
