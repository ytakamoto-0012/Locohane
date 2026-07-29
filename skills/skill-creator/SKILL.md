---
name: skill-creator
description: Locohaneの .locohane/skills/ 配下に新しいスキルを作成する、既存スキルを編集・改善する、evalsハーネスで実際にローカルLLMを動かしてスキルの効果を検証する、SKILL.mdのdescriptionのトリガー精度を最適化するためのメタスキル。「新しいスキルを作りたい」「スキルを作って」「このスキルを直して」「スキルがちゃんと動くか試したい」「read_skillされやすいようにdescriptionを直したい」「スキルのトリガー精度を上げたい」など、スキル自体の作成・改善・検証に関する依頼があれば、たとえユーザーが「skill-creator」という名前を出さなくても必ず使うこと。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# skill-creator

新しいスキルを作り、実際にローカルLLM（自分自身と同じ llama.cpp server）を
動かしてテストし、フィードバックを踏まえて改善する——このサイクルを回す
ためのスキル。既存スキルの改善や、description のトリガー精度最適化にも使う。

## 全体の流れ

1. 意図を把握する（何をするスキルか、いつトリガーすべきか）
2. `scaffold_skill.py` で雛形を作る、または既存スキルを編集する
3. `validate_skill.py` で frontmatter を検証する
4. `make_eval_case.py` でテストケースを作る
5. `run_isolated_eval.py` で with_skill / baseline を実機テストする（直列）
6. ルールベースは自動判定、judge指定分は自分がtranscriptを読んで判定する
7. フィードバックを踏まえて SKILL.md / scripts を修正し、4〜6を繰り返す
8. （任意）`run_trigger_eval.py` + `propose_description.py` で description のトリガー精度を最適化する

ユーザーが「evalは要らない、雛形だけ作って」と言えばステップ4以降は
スキップしてよい。逆に「ちゃんと動くか確かめたい」という要望なら
必ずステップ5まで進める。

## Locohane固有の制約（必ず守ること）

- **ローカルのllama.cpp serverは1インスタンスのみ**。評価用のテスト実行は
  常に直列（1件ずつ）で行う。複数の `run_isolated_eval.py start` や
  `run_trigger_eval.py start` を同時に走らせて多重リクエストを送らないこと。
  1件startしたら、statusで`finished`になるまで待ってから次を始める。
- `run_script` は **1本のテキスト（stdout/stderrと終了コード）** しかLLMに
  渡さない。スクリプトはすべて正常系は終了コード0＋stdoutに1行JSON、
  異常系は非0＋stderrという規約に統一している。
- 実際にLLMを動かす評価系スクリプト（`run_isolated_eval.py`
  `run_trigger_eval.py` `propose_description.py`）は `run_script` の同期
  タイムアウトを超えうるため、**start でジョブを開始し、status でポーリング
  する**非同期パターンになっている。詳細は `references/schemas.md` 参照。
  status が `running` を返している間は、一度に数十秒〜1分程度待ってから
  再度呼び出すこと（間隔を空けずに連打しない）。
- 新しいスキルを追加・変更しても **Locohaneアプリはホットリロードしない**。
  本番での動作確認にはアプリの再起動が必要になる旨をユーザーに伝えること
  （evalによる実機テスト自体はアプリ再起動なしで行える）。
- **新規スキルは必ず `.locohane/skills/<name>/` に作成する。** プロジェクト
  ルート直下の `skills/`（本スキルや word-counter 等の置き場）には書き込ま
  ない。`scaffold_skill.py` は常に `.locohane/skills/` へ生成する。既存の
  `skills/` 側スキルを評価・改善する場合のみ、`run_isolated_eval.py` の
  `--skill-root skills` で対象を切り替える。

## ステップ1: 意図を把握する

ユーザーに（会話に既に手がかりがあれば推測してから確認する）:

1. このスキルは何をするためのものか
2. どんなユーザー発話・状況でトリガーされるべきか
3. 期待する出力の形式は何か
4. 実機テスト（eval）は必要か。ファイル変換・データ抽出・固定手順のような
   「客観的に正解が決まる」スキルは eval が有効。文章のトーンやデザインの
   ような主観的な出力は、ルールベースでは測れないため `judge` 判定か
   ユーザー自身の目視確認に頼ることになる、と伝える。

## ステップ2: SKILL.mdを書く

```json
{"skill_name": "skill-creator", "script_filename": "scaffold_skill.py",
 "script_args": ["--name", "my-new-skill", "--description", "...",
                  "--with-script"]}
```

`--with-script` を付けると `scripts/run.py` のサンプルも生成される。
スクリプト不要（知識のみ）のスキルなら付けない（`skills/git-commit-style`
のような形）。

雛形ができたら、以下を踏まえて SKILL.md 本文を書き直す:

- **description が唯一のトリガー手がかり**。「何をするか」だけでなく
  「どんな発話のときに使うべきか」を具体的に書く。Locohaneはトリガー判断を
  LLM自身の推論に委ねており、`allowed-tools` のような自動承認機構はない
  ため、description の質が最終的なUXを決める。
- SKILL.md本文は500行を目安に収める。長くなりそうなら `references/` に
  分割し、本文からポインタを張る。
- `scripts/` を持つ場合、本文に**呼び出しJSON例・出力キーの意味・
  エッジケース**を明記する（`skills/word-counter/SKILL.md` を参考にする）。
  スクリプトの出力は構造化JSON（1行、`print(json.dumps(result,
  ensure_ascii=False))`）を推奨。ファイルを生成するスキルなら
  `output_path`（絶対パス文字列）キーを必ず含める。
- name/description の検証ルールに違反すると起動時に**黙ってスキップ**
  される（例外にはならない）。`validate_skill.py` で必ず事前確認する。

```json
{"skill_name": "skill-creator", "script_filename": "validate_skill.py",
 "script_args": ["--skill-dir", "C:\\...\\skills\\my-new-skill"]}
```

`valid: false` なら `error` に理由が入るので直して再検証する。

## ステップ3: テストケースを作る

現実的なテストプロンプトを2〜3個、ユーザーと一緒に決める。判定方法は
2種類:

- **ルールベース (`expect`)**: 特定ツールが呼ばれたか、応答に特定文字列が
  含まれるか、といった客観的に機械判定できる項目。
- **judge**: 自由記述の判定指示。実行結果の `judge` フィールドと
  `transcript` を読んで、**自分（このスキルを使っているLLM自身）が
  合否を判断する**。これは `.claude/skills/tune-prompt` と同じ考え方で、
  Locohaneには人間の代わりに自動採点するグレーダー機構は無い。

```json
{"skill_name": "skill-creator", "script_filename": "make_eval_case.py",
 "script_args": ["--target", "my-new-skill", "--case-id", "001_basic_usage",
                  "--turns", "[\"ユーザーが実際に打ちそうな発話\"]",
                  "--expect", "{\"tool_call_args_contains\": {\"read_skill\": {\"skill_name\": \"my-new-skill\"}}}"]}
```

生成先は `evals/cases/<target>/<case-id>.yaml`。既存の `python evals/run_all.py
<target>` でも直接実行できる標準フォーマットなので、独自の仕組みは使わない。

## ステップ4: 実機テスト（with_skill / baseline）

新規スキルなら baseline は「スキルなし」、既存スキルの改善なら baseline は
「編集前のスキル」にする（改善前に `skill_dir` を丸ごと別フォルダへ
コピーしておき `--replacement-dir` に渡す）。

```json
{"skill_name": "skill-creator", "script_filename": "run_isolated_eval.py",
 "script_args": ["start", "--case", "C:\\...\\evals\\cases\\my-new-skill\\001_basic_usage.yaml",
                  "--skill-name", "my-new-skill", "--mode", "with_skill",
                  "--workspace", "C:\\...\\skills\\my-new-skill-workspace"]}
```

`job_id` が返るので、少し待ってから status で確認する:

```json
{"skill_name": "skill-creator", "script_filename": "run_isolated_eval.py",
 "script_args": ["status", "--job-id", "<job_id>", "--skill-name", "my-new-skill",
                  "--workspace", "C:\\...\\skills\\my-new-skill-workspace"]}
```

`status: running` の間はしばらく待って再確認する。`finished` になったら、
同じケースを `--mode without_skill`（新規スキルの場合）または
`--mode old_skill --replacement-dir <退避先>`（既存スキル改善の場合）で
**逐次**（同時にstartしない）実行し、比較材料を揃える。

## ステップ5: 判定と比較

`result.rules_pass` があればそれに従う。`result.judge` に指示文があれば、
`result.transcript` を実際に読んで自分で合否を判断する。判断根拠は
ユーザーへの報告に含めること。

複数件たまったら比較レポートを作る:

```json
{"skill_name": "skill-creator", "script_filename": "aggregate_results.py",
 "script_args": ["--input", "with_skill=<with_skillのstatus出力を保存したjson>",
                  "--input", "baseline=<baselineのstatus出力を保存したjson>",
                  "--output", "C:\\...\\skills\\my-new-skill-workspace\\iteration-1\\benchmark.md"]}
```

`status` コマンドの出力（JSONテキスト）は、Write ツール等で一旦ファイルに
保存してから `--input` に渡す。

## ステップ6: 改善のしかた

- ユーザーのフィードバックを**一般化**する。目の前の2〜3例だけに効く
  対症療法的な `MUST` を並べるより、なぜそれが必要かを説明する文に
  書き直す方が、未知の入力にも効く。
- SKILL.mdは無駄を削る。読んでいて手順が冗長・過剰に厳格だと感じたら
  削ってよい。
- 複数のテストケースで同じような補助スクリプトが必要になっているなら、
  それを `scripts/` に1つ書いて共通化する。
- 修正後は同じテストケースで再実行し、`iteration-2/` のように分けて
  比較する。ユーザーが満足するか、フィードバックが出尽くすまで繰り返す。

## ステップ7（任意）: description のトリガー精度最適化

「このスキル、狙った発話でちゃんと使われているか不安」「もっと的確に
トリガーされてほしい」といった要望があれば行う。

1. should_trigger true/false 合わせて10〜20件程度、現実的な発話例を
   ユーザーと一緒に用意する（`references/schemas.md` の eval-set 形式）。
   should_trigger=false の例は「キーワードは似ているが本来は別の対応が
   要る」ような紛らわしいものを混ぜること。明らかに無関係な例ばかりでは
   何も測れない。
2. `run_trigger_eval.py start` → `status` をポーリングし、`accuracy` と
   `per_query` を確認する。
3. `matched: false` の項目を抽出し `propose_description.py start` で
   改善案を生成、`status` で結果を受け取る。
4. 改善案を SKILL.md の description に反映し、`validate_skill.py` で
   1024文字以内であることを再確認したうえで、もう一度
   `run_trigger_eval.py` を回して改善したか確認する。
5. 改善が頭打ちになったら、その時点の accuracy と描写内容をユーザーに
   報告して終える。

## 参考資料

- `references/schemas.md`: 各スクリプトの引数・出力JSONの詳細スキーマ。
- `skills/SKILLS_README.md`: SKILL.mdのフォーマット仕様・run_scriptの
  値渡し規約（このスキル自体もこの仕様に従っている）。
- `skills/word-counter/SKILL.md` / `skills/git-commit-style/SKILL.md`:
  シンプルなスキルの実例。
