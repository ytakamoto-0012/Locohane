# skill-creator 補助資料: 各スクリプトの入出力形式

`scripts/` 配下の各スクリプトが受け取る引数・返す JSON の形式をまとめる。
共通契約: 正常終了時は標準出力の最終行に1行の JSON、異常系は終了コード
非0＋標準エラーにメッセージ（`SKILLS_README.md` の規約どおり）。

## 非同期実行（start / status）について

実際にローカルの llama.cpp server へ問い合わせる処理（`run_isolated_eval.py`
`run_trigger_eval.py` `propose_description.py`）は、`run_script` の同期実行
タイムアウト（`config.ini` の `[scripts].timeout`）を超えうるため、
`start` でバックグラウンド起動して `job_id` を受け取り、`status` で
ポーリングする2段構成になっている。`status` は `{"status": "running", ...}`
を返している間は数十秒待ってから再度呼び出すこと。

---

## scaffold_skill.py

新しいスキルの雛形（SKILL.md + references/ +（任意で）scripts/）を生成する。

```
python scaffold_skill.py --name my-new-skill --description "..." [--with-script]
```

生成先は常に `.locohane/skills/<name>/`（プロジェクトルート直下の `skills/`
には書き込まない）。

出力: `{"skill_dir", "skill_md_path", "created": [...], "note"}`

## validate_skill.py

SKILL.md の frontmatter を `src/skills.py` の `_validate()` と同一ルールで検証する。

```
python validate_skill.py --skill-dir "C:\...\skills\my-new-skill"
```

出力: `{"valid", "error", "name", "description", "description_length", "dir_name", "has_scripts_dir", "script_files"}`

## make_eval_case.py

`evals/case_schema.py` 互換のテストケース（yaml、中身はJSON）を1件生成する。
生成先は `evals/cases/<target>/<case-id>.yaml`。

```
python make_eval_case.py --target my-new-skill --case-id 001_basic_usage \
    --turns '["ユーザーの発話"]' \
    --expect '{"tool_call_args_contains": {"read_skill": {"skill_name": "my-new-skill"}}}' \
    [--judge "判定してほしい観点の自由記述"] \
    [--work-dir "./evals/fixtures/xxx"] [--timeout-seconds 600] [--notes "..."]
```

`--expect` と `--judge` はどちらか必須（両方でもよい）。`Expect` の主なキー:
- `tool_called_any`: list[str] — いずれかのツールが呼ばれれば pass
- `tool_not_called`: list[str] — 指定ツールが一度も呼ばれなければ pass
- `tool_call_args_contains`: dict[str, dict] — 例 `{"read_skill": {"skill_name": "..."}}`。指定ツールの呼び出し引数のいずれかが部分一致すれば pass
- `response_contains` / `response_not_contains`: list[str] — 最終応答文字列に対する部分一致判定

出力: `{"case_path", "target", "case_id"}`

## run_isolated_eval.py

対象スキルの有無/新旧を切り替えたうえで `evals.run_case` を1件バックグラウンド実行する。

```
python run_isolated_eval.py start --case <case.yaml> --skill-name my-new-skill \
    [--skill-root skills|locohane] [--mode with_skill|without_skill|old_skill] \
    [--replacement-dir <旧バージョン一式のパス>] [--workspace <path>] [--python-exe <path>]

python run_isolated_eval.py status --job-id <job_id> --skill-name my-new-skill \
    [--skill-root skills|locohane] [--workspace <path>]
```

`--skill-root` の既定値は `locohane`（`.locohane/skills/`、skill-creator が
新規作成するスキルの置き場）。プロジェクトルート直下の `skills/` にある
既存スキル（excel-edit等）を対象にする場合のみ `--skill-root skills` を
明示する。

- `with_skill`: 本番の skills_dir をそのまま使う。
- `without_skill`: 対象スキルだけ除外した一時ディレクトリで実行する（baseline）。
- `old_skill`: `--replacement-dir` の内容で対象スキルを差し替えて実行する（改善前後比較）。

`start` の出力: `{"job_id", "pid", "log_path", "status": "started", "mode", "workspace"}`
`status` の出力（実行中）: `{"job_id", "status": "running", "pid"}`
`status` の出力（完了）: `{"job_id", "status": "finished", "result": {...evals.run_case の出力...}}`

`result` の主なキー（`evals/run_case.py` 準拠）:
- `case_id`, `target`, `notes`, `final_answer`
- `transcript`: 会話全体のシリアライズ（各要素に `tool_calls` があれば `{"name","args"}` を含む）
- `rule_results`, `rules_pass`（expect 未指定なら null）
- `judge`: 判定指示文（合否はこのスキル＝呼び出し元のLLM自身がtranscriptを読んで判断する）
- `token_usage_total`: `{"input_tokens","output_tokens","total_tokens"}`
- `turn_timings`, `turn_cutoffs`（あれば）
- `error`（`llm_unreachable` / `mid_turn_exception` / `runtime_exception` 等）

## aggregate_results.py

複数の `run_isolated_eval.py status` 出力（または `evals.run_case` の生JSON）を
比較する Markdown レポートを生成する。

```
python aggregate_results.py --input with_skill=result_a.json --input baseline=result_b.json \
    --output "<workspace>/iteration-1/benchmark.md"
```

出力: `{"output_path", "cases"}`

## run_trigger_eval.py

description のトリガー精度を評価する。`eval-set` の各クエリを `--repeats`
回実行し、`read_skill` が対象スキル名で呼ばれた比率を集計する。

eval-set の形式:
```json
[
  {"query": "ユーザーが実際に打ちそうな発話", "should_trigger": true},
  {"query": "紛らわしいが本来は使うべきでない発話", "should_trigger": false}
]
```

```
python run_trigger_eval.py start --eval-set trigger_eval.json --skill-name my-new-skill \
    --workspace <path> [--repeats 3] [--python-exe <path>]

python run_trigger_eval.py status --job-id <job_id> --workspace <path>
```

`status` の出力（完了時）:
```json
{
  "status": "finished",
  "target": "trigger_my-new-skill_xxxxxxxx",
  "accuracy": 0.85,
  "per_query": [
    {"query": "...", "should_trigger": true, "trigger_rate": 1.0, "matched": true}
  ],
  "results_path": "C:\\...\\evals\\results\\trigger_my-new-skill_xxxxxxxx\\20260101_120000\\results.json"
}
```

`matched: false` の項目だけ抽出して `propose_description.py` の `--failed-queries` に渡す。

## propose_description.py

現在の description と `run_trigger_eval.py` の失敗例から改善案をLLMに提案させる。

```
python propose_description.py start --skill-name my-new-skill \
    --current-description "現在のdescription全文" \
    --failed-queries failed.json --workspace <path> [--python-exe <path>]

python propose_description.py status --job-id <job_id> --workspace <path>
```

`--failed-queries` は `run_trigger_eval.py` の `per_query` から `matched: false` を
抽出したJSON配列をそのまま渡す。

`status` の出力（完了時）: `{"job_id", "status": "finished", "result": {"text": "改善後のdescription案"}}`
