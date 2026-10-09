# skill-creator 補助資料: 各スクリプトの入出力形式

共通:
- 正常時は終了コード0で標準出力に1行のJSON、異常時は終了コード1で標準エラーに `エラー: 理由`。
- `--name` はドラフト名。自分のドラフトはスキル名だけ（例 `my-skill`）、他の人のドラフトは `ユーザー名/スキル名`。
- 他の人のドラフトは、config.ini `[skill_creator].other_users_drafts` が許す範囲でしか扱えない（`full` のときだけ書き込み・評価できる）。
- ドラフトの場所: `<ドラフト置き場>/<ユーザー名>/<スキル名>/`。評価の結果は `<ドラフト置き場>/<ユーザー名>/_workspace/<スキル名>/`。

## 非同期実行（start / status）

`run_isolated_eval.py` `run_trigger_eval.py` `propose_description.py` は実際にローカルLLMを動かすため、
`start` で `job_id` を受け取り、`status` で結果を受け取る。`status` が `running` の間は1分ほど待ってから再度呼ぶ。

---

## scaffold_skill.py

```
python scaffold_skill.py --name my-skill --description "..." [--with-script]
```

自分のドラフト置き場に SKILL.md・references/・evals/（`--with-script` なら scripts/run.py も）を作る。
正式スキルと同じ名前、自分の既存ドラフトと同じ名前はエラー。

出力: `{"draft", "skill_dir", "note"}`

## fork_skill.py

```
python fork_skill.py --name <正式スキル名>
```

正式スキルを自分のドラフトへ複製する（改善案。正式スキルは変わらない）。`evals/cases/<名前>/` のケースも evals/ へ写す。

出力: `{"draft", "skill_dir", "base_dir", "copied_cases", "note"}`

## write_draft_file.py

```
python write_draft_file.py --name my-skill --path SKILL.md --content "全文"
python write_draft_file.py --name my-skill --path scripts/run.py --content-file <ファイル>
python write_draft_file.py --name my-skill --path SKILL.md --old "古い文" --new "新しい文"
```

`--content` / `--content-file` / `--old`+`--new` のどれか1つ。`--old` はファイル内で1か所だけ一致する必要がある。
`--path` はスキルフォルダからの相対パス（`..`・絶対パス・`_draft_meta.json` は不可）。
SKILL.md の frontmatter が不正になる書き込みは取り消される。

出力: `{"draft", "path", "chars"}`

## delete_draft.py

```
python delete_draft.py --name my-skill [--path references/old.md]
```

`--path` 無しならドラフトごと（評価結果も）削除。SKILL.md・`_draft_meta.json` は単体では消せない。

出力: `{"deleted"}` または `{"draft", "deleted_file"}`

## list_drafts.py

```
python list_drafts.py
```

出力: `{"user", "drafts": [{"draft", "own", "kind", "base_skill", "status", "description", "updated_at", "eval_cases", "last_tryout", "returned_reason"}]}`

- `kind`: `new`（新規）/ `improve`（既存スキルの改善案）
- `status`: `draft` / `promoted`（正式化済み）/ `returned`（差し戻し。理由は `returned_reason`）/ `rejected`

## validate_skill.py

```
python validate_skill.py --name my-skill
```

出力: `{"draft", "valid", "error", "name", "description", "description_length", "script_files", "eval_cases"}`

## make_eval_case.py

```
python make_eval_case.py --name my-skill --case-id 001_basic \
    --turns "[\"発話\"]" \
    [--expect "{...}"] [--judge "判定観点"] \
    [--work-dir "./evals/fixtures/xxx"] [--timeout-seconds 600] [--notes "..."]
```

`--expect` と `--judge` はどちらか必須（両方でもよい）。ケースはドラフトの `evals/<case-id>.yaml` に保存される。
評価中のスキル名はユーザー名の付かないスキル名になる（expect にもスキル名だけを書く）。

`--expect` の主なキー:
- `tool_called_any`: list[str] — いずれかのツールが呼ばれれば合格
- `tool_not_called`: list[str] — 指定ツールが一度も呼ばれなければ合格
- `tool_call_args_contains`: dict[str, dict] — 例 `{"read_skill": {"skill_name": "my-skill"}}`
- `response_contains` / `response_not_contains`: list[str] — 最終回答の文字列の部分一致

出力: `{"draft", "case_path", "case_id"}`

## run_isolated_eval.py

```
python run_isolated_eval.py start --name my-skill [--mode with_skill|without_skill] [--repeat N] [--case <ID> ...] [--instance <名前>]
python run_isolated_eval.py status --name my-skill --job-id <job_id>
```

- `with_skill`（既定）: 本番のスキル構成の上にドラフトを重ねて評価する（改善案は同名の正式スキルを上書きした状態）。
- `without_skill`: ドラフト無し（新規ならスキル無し、改善案なら正式スキルのまま）。比較用。
- `--repeat N`: 各ケースを N 回繰り返す（スキル安定化トライアウト）。全回合格で合格。

`start` の出力: `{"job_id", "pid", "log_path", "status": "started", "runs"}`
`status` の出力（実行中）: `{"job_id", "status": "running", "started_runs", "total_runs"}`
`status` の出力（完了）:
```json
{
  "status": "finished", "mode": "with_skill", "repeat": 10,
  "verdict": "pass | fail | needs_judge",
  "cases": {"001_basic": {"pass": 10, "fail": 0, "judge": 0, "error": 0}},
  "runs": [{"case_id", "repeat_index", "outcome", "failed_rules", "judge", "error", "final_answer"}],
  "results_path": "...\\results.json"
}
```

- `needs_judge`: ルール上は不合格が無いが judge 付きのケースがある。`runs` の `judge` と `final_answer`（詳しくは `results_path` の `transcript`）を読んで判定する。
- 完了結果はドラフトの `_draft_meta.json` の `tryouts` にも記録される。

## aggregate_results.py

```
python aggregate_results.py --name my-skill --input with_skill=<results.json> --input without_skill=<results.json>
```

ケースごとの合格回数・平均トークンの比較表を作る。出力: `{"output_path", "cases", "markdown"}`

## run_trigger_eval.py

eval-set の形式:
```json
[
  {"query": "ユーザーが実際に打ちそうな発話", "should_trigger": true},
  {"query": "紛らわしいが本来は使うべきでない発話", "should_trigger": false}
]
```

```
python run_trigger_eval.py start --name my-skill --eval-set <JSONファイル> [--repeats 3]
python run_trigger_eval.py status --name my-skill --job-id <job_id>
```

`status` の出力（完了）: `{"status": "finished", "accuracy", "per_query": [{"query", "should_trigger", "trigger_rate", "matched"}], "results_path"}`

`matched: false` の項目だけ抜き出して `propose_description.py` の `--failed-queries` に渡す。

## propose_description.py

```
python propose_description.py start --name my-skill --failed-queries <JSONファイル>
python propose_description.py status --name my-skill --job-id <job_id>
```

現在の description はドラフトの SKILL.md から読む。`status` の出力（完了）: `{"status": "finished", "proposed_description"}`。
提案は自動では反映されない（`write_draft_file.py` で書く）。
