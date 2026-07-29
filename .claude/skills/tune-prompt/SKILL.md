---
name: tune-prompt
description: Locohane の system_prompt.md・SKILL.md・tool docstring 等のプロンプト資産を、実際のローカルLLM（llama.cpp）を動かして自動評価し、失敗があれば ClaudeCode 自身が修正して再評価するループを回す。「system_promptをチューニングして」「evalを回して」「プロンプトをループテストして」「/tune-prompt」等で使う。evals/ 配下の評価ハーネス（evals/run_all.py, evals/cases/）とセットで使う。
---

# tune-prompt: プロンプト資産の自動ループテスト・チューニング

`Locohane` の `system_prompt/system_prompt.md`・`skills/*/SKILL.md`・
`src/tools.py` のツール docstring を、実際のローカル LLM を動かして評価し、
失敗があれば最小限の修正を加えて再評価する、というループを完全自動で回す。

引数（`args`）でチューニング対象カテゴリを指定する。省略時は `system_prompt`。
対象カテゴリは `evals/cases/<target>/*.yaml` に対応する
（例: `system_prompt` → `evals/cases/system_prompt/`）。

対象ファイルの対応表:
- `system_prompt` → `system_prompt/system_prompt.md`
- `system_prompt_scale` → `system_prompt/system_prompt.md`（`system_prompt`と
  同じファイルが対象だが、実データ規模のフィクスチャを使う重量級ケース専用。
  このループの自動対象には**含めない**。ユーザーから明示指定された場合、
  または`system_prompt`ループ完了後の最終確認として使う）
- （将来）`skill:<skill名>` → `skills/<skill名>/SKILL.md`
- （将来）`tool_docstring` → `src/tools.py`

## 前提条件の確認

1. llama.cpp server が起動しているか確認する
   （`config.ini` の `[llm].base_url`、既定 `http://localhost:8080/v1`）。
   起動していない場合、評価結果は `error: llm_unreachable` になる。
   このエラーが出た場合はループを進めず、ユーザーに server 起動を促して終了する。
2. `evals/README.md` を一読し、ケース形式・実行方法を把握する。

## ループ手順

**イテレーション上限は10回。** 上限に達したら、どこまで直せたか・
残っている失敗は何かを報告してループを終了する（無限ループにしない）。

### 0. ベースライン退避（初回のみ）

`evals/history/<target>/` に何も無ければ、対象ファイルの現在の内容を
`evals/history/<target>/iter00_baseline.md` としてそのままコピーする。

### 1. 評価を実行する

```
python evals/run_all.py <target>
```

標準出力のサマリと、`evals/results/<target>/<最新timestamp>/results.json` を確認する。

### 2. 結果を判定する

- ルールベースで `rules_pass: false` のケース → 不合格。
- `judge` 指示があるケース → `results.json` の `transcript` と `judge` 指示文を
  自分で読み、合格/不合格と根拠を判断する。捏造・幻覚呼び出し・的外れな
  委譲判断などは厳しめに見る。
- `turn_cutoffs`（`recursion_limit`/`thinking_loop` によるターン単位打ち切り）
  が記録されているケースは、打ち切り自体を即不合格にはしないが、何が起きた
  かをtuning_log.mdの判定根拠に一言含める（本番`app.py`でも起こりうる正常な
  打ち切り動作であり、evalハーネス固有の異常ではないため）。
- 判断結果（ケースID・合否・根拠1〜2行）を `evals/tuning_log.md` に追記する
  （イテレーション番号の見出しの下にまとめる）。

### 3. 全ケース合格なら終了

- 最終的な対象ファイルの内容を `evals/history/<target>/iterNN_final.md`
  として退避する。
- `evals/tuning_log.md` に完了サマリ（何イテレーションかかったか、
  最終的にどこを直したか）を追記する。
- ユーザーへ完了報告して終了する。

### 4. 不合格があれば修正する

1. 不合格ケースの `transcript`（どのツールをどう呼んだか、最終回答）と、
   対象ファイルの現在の記述を突き合わせ、**根本原因**を特定する
   （記述が曖昧、指示が矛盾している、例が無い、等）。
2. 編集前に対象ファイルを `evals/history/<target>/iterNN_before.md`
   （NN は今回のイテレーション番号）としてコピーする。
3. 特定した原因に対して**最小限**の修正を加える（Edit ツール）。
   合格しているケースの挙動を壊さないよう、変更範囲を絞ること。
4. `evals/tuning_log.md` に「### iterNN」の見出しで、対象ケース・失敗内容・
   原因・変更箇所・変更理由を簡潔に追記する。
5. **振動検知**: 直近2〜3イテレーションで同じケースが同じ理由で
   失敗と合格を繰り返している場合、修正方針が誤っている可能性が高い。
   これ以上機械的に繰り返さず、状況（何を試して何が起きたか）を
   ユーザーに報告してループを止める。
6. 手順1に戻る。

## 安全策

- git へのコミット・ステージングは一切行わない
  （スナップショット退避と `tuning_log.md` のみで変更履歴を追える）。
- 対象カテゴリに対応するファイル以外は編集しない
  （`system_prompt` 実行中に `skills/*/SKILL.md` や `src/tools.py` を触らない）。
- 1イテレーションで複数箇所を一度に書き換えない
  （原因の切り分けが難しくなり、振動検知も効かなくなるため）。
