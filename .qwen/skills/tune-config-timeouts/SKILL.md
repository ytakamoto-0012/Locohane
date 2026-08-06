---
name: tune-config-timeouts
description: Locohane の config.ini の timeout系設定（[llm].request_timeout_seconds / [llm].stream_chunk_timeout_seconds / [scripts].timeout）を、実行環境のハードウェアスペック（GPU/CPU/メモリ、モデルサイズ）に応じて実測ベンチマークして自動チューニングする。evals/cases/config_timeouts を使う。「タイムアウトをチューニングして」「環境に合わせてtimeoutを調整して」「/tune-config-timeouts」等で使う。tune-prompt（プロンプト資産のテキスト品質チューニング）とは別物で、こちらは実測タイムスタンプに基づく数値パラメータが対象。
---

# tune-config-timeouts: config.ini timeout系設定の自動チューニング

`Locohane` は llama-server（llama.cpp）をローカルLLMバックエンドとして
使う。`config.ini` の timeout系設定は実行環境のハードウェアスペックに強く
依存するため、実測ベンチマークに基づき環境ごとの適正値を算出し、必要であれば
`config.ini` を更新するループを回す。

## 対象スコープ

チューニング対象は以下3項目のみ（すべて「LLM推論速度」または「スクリプト
実行速度」に依存する = 環境スペックが変われば適正値も変わるもの）。

| セクション | キー | 対応する計測指標（`evals/timing_callbacks.py`） |
|---|---|---|
| `[llm]` | `request_timeout_seconds` | `max_llm_total_seconds`（LLM呼び出し1回あたりの総所要時間の最大値） |
| `[llm]` | `stream_chunk_timeout_seconds` | `max_stream_chunk_gap_seconds`（ストリーミング中のチャンク間隔の最大値） |
| `[scripts]` | `timeout` | `max_script_seconds`（`run_script`/`execute_python_code` 実行時間の最大値） |

**対象外**: `[user_response_timeouts]` セクション（`approval_seconds`/`ask_user_question_seconds`/
`ask_user_choice_seconds`）は「人間の応答待ち」
であり、ハードウェアスペックとは無関係なので**絶対に触らない**。

## 前提条件の確認

1. llama.cpp server が起動しているか確認する
   （`config.ini` の `[llm].base_url`、既定 `http://localhost:8080/v1`）。
   起動していない場合、評価結果は `error: llm_unreachable` になる。
   このエラーが出た場合はループを進めず、ユーザーに server 起動を促して終了する。
2. `evals/README.md` を一読し、ケース形式・実行方法を把握する。
3. 既知の制約: `config.ini` の `[graph].implementation` が既定の `prebuilt`
   （`create_react_agent`）である前提で計測が効く。`handwritten` の場合、
   `_build_handwritten_graph` の `call_model` ノードが `config`（callbacks）を
   LLM呼び出しに渡していないため計測できない。本番既定値が `prebuilt` のため
   このスキルでは対応しない（本番グラフコードも変更しない）。

## ループ手順

**イテレーション上限は3回。** 上限に達したら、どこまで調整できたか・
残っている課題を報告してループを終了する（無限ループにしない）。

### 0. ベースライン退避（初回のみ）

`evals/history/config_timeouts/` に何も無ければ、現在の `config.ini` を
`evals/history/config_timeouts/iter00_baseline.ini` としてそのままコピーする
（対象3キーだけでなくファイル全体を退避してよい。差分は git 管理下にあるので
必要なら `git diff` でも追跡できるが、このスキル自体はコミットしない）。

### 1. 評価を実行する

```
python evals/run_all.py config_timeouts
```

標準出力のサマリと、`evals/results/config_timeouts/<最新timestamp>/results.json`
（各ケースの `turn_timings`）を確認する。`error` が出ているケースがあれば、
まずその原因（`llm_unreachable`・`timeout`・`mid_turn_exception` 等）を解消する
ことを優先し、実測値の議論はエラー解消後に行う。

### 2. 推奨値を算出する

```
python evals/analyze_timing.py config_timeouts
```

現在値・実測最大値・推奨値・差分（%）・変更要否のテーブルが標準出力され、
`evals/results/config_timeouts/<timestamp>/recommendations.json` にも書き出される。
推奨値の算出式（`evals/analyze_timing.py` にハードコード）:
- `request_timeout_seconds` 推奨値 = `ceil(max_llm_total_seconds × 1.5)`（下限60秒）
- `stream_chunk_timeout_seconds` 推奨値 = `ceil(max_stream_chunk_gap_seconds × 2.0)`（下限30秒）
- `timeout`（scripts） 推奨値 = `ceil(max_script_seconds × 1.5)`（下限60秒）
- 現在値との差が **±20%未満** なら「変更不要」（測定誤差による無意味な
  チャーン・振動を避けるため）。

### 3. 判定する

- 3項目すべて「変更不要」→ 手順5（完了報告）へ。
- いずれかが「変更推奨」→ 手順4（適用）へ。

### 4. 変更を適用する

1. 変更前の `config.ini` を `evals/history/config_timeouts/iterNN_before.ini`
   （NN は今回のイテレーション番号）としてコピーする。
2. 「変更推奨」となったキー**のみ**を Edit ツールで更新する
   （コメント・他キー・`[user_response_timeouts]` セクションは一切変更しない）。
3. `evals/tuning_log.md` に「### config_timeouts iterNN」の見出しで、
   対象ケース・実測値・推奨根拠・変更差分（旧値→新値）を簡潔に追記する
   （既存の `## iterNN: ...`（tune-prompt由来）と混同しないよう、
   見出しに `config_timeouts` を含める）。
4. 手順1に戻り、新しい値のもとで再評価する。

### 5. 再評価・完了判定

再度 `run_all.py config_timeouts` → `analyze_timing.py config_timeouts` を実行し、
新しい timeout 値に対して実測最大値が十分な余裕（マージン以内、目安として
新しい timeout の70%以下）に収まっているか、`rules_pass`/`judge` が引き続き
健全かを確認する。

- **振動検知**: 同じキーが2イテレーション連続で逆方向（増加→減少→増加、等）
  に振れ続けている場合、フィクスチャや測定条件が不安定である可能性が高い。
  これ以上機械的に繰り返さず、状況（何を試して何が起きたか）をユーザーに
  報告してループを止める。
- 全項目「変更不要」、またはイテレーション上限（3回）に達したら終了する。

### 6. 完了報告

- 最終的な推奨値・実際に適用した変更（適用していれば）をユーザーに報告する。
- **`config.ini` は現状すでに `[scripts].python` のような絶対パス等
  マシン固有の値を含む形でリポジトリにコミットされているファイルであるため、
  本スキルも tune-prompt と同じく「git コミットは一切行わない」方針とする。
  このチューニング結果を実際にコミットするかどうかはユーザー判断に委ねる旨を
  明記して報告すること。**

## 安全策

- git へのコミット・ステージングは一切行わない
  （スナップショット退避（`evals/history/config_timeouts/`）と
  `evals/tuning_log.md` のみで変更履歴を追える）。
- 対象3キー（`[llm].request_timeout_seconds` / `[llm].stream_chunk_timeout_seconds`
  / `[scripts].timeout`）以外は編集しない。特に `[user_response_timeouts]` セクションは
  対象外なので絶対に触らない。
- 1イテレーションで複数キーをまとめて大きく変更しない
  （原因の切り分けが難しくなり、振動検知も効かなくなるため）。
- `evals/analyze_timing.py` は推奨値の提示のみ行い、`config.ini` を直接
  書き換えない。実際の適用判断・編集は必ずこのSKILL.mdの手順に従い
  ClaudeCode自身がEditツールで行う。
