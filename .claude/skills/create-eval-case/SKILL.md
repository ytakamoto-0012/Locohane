---
name: create-eval-case
description: Locohane の evals/cases/<target>/*.yaml に新しい eval ケースを追加する。既存ケース（evals/cases/system_prompt, system_prompt_scale, config_timeouts）の書き方・命名規則・judge指示文の型に沿わせつつ、turns（ユーザー発話文）は実行対象がローカルの低パラメータモデルであることを踏まえて端的に書く。「evalケースを追加して」「このバグの回帰テストを作って」「〜のシナリオをevalsに足して」「/create-eval-case」等で使う。ケース実行・チューニングループ自体は tune-prompt / tune-config-timeouts が担当し、このスキルはケースの新規作成専用。
---

# create-eval-case: evals ケースの新規作成

`evals/cases/<target>/*.yaml` に新しい eval ケースを1件追加する。
チューニングループ本体（`tune-prompt`/`tune-config-timeouts`）とは別物で、
このスキルは「ケースを1件正しく書いて動作確認する」ところまでを担当する。

## 前提条件の確認

1. `evals/README.md` と `evals/case_schema.py` を読み、yaml のフィールド・
   `expect`/`judge` の役割分担を確認する。
2. llama.cpp server が起動しているか確認する（`config.ini` の
   `[llm].main_url`に設定された接続先。既定値を仮定せず、実際に使われる
   `base_url` は都度 `config.ini` を直接見て確認する）。手順6の動作確認で
   実際に1件実行するため、未起動ならユーザーに起動を促す。

## 最重要1: turns は端的に書く。詳しく書くのは評価にならない

`turns` はそのまま Locohane 本番のメインエージェント（ローカルで動く
低パラメータモデル）への指示として渡る。**本番のユーザーは体裁・条件を
何行にもわたって詳しく指示したりしない。** 詳しい指示文でテストすると
「丁寧に書けば成功する」ことしか証明できず、本番で実際に届く短く曖昧な
指示に対する挙動（曖昧さをどう埋めるか、何を確認しに行くか）を検証した
ことにならない。詳しく書くほど「良いケース」になるという発想を捨てる。

- 1メッセージ1〜2文、条件は多くて1〜2個までを基本にする。
- 足りない情報（対象ファイル名、体裁の細部など）はあえて書かない。
  低パラメータモデルがそれをどう埋めるか（勝手に決め打ちするか、
  `ask_user_choice`で確認するか、探索して判断するか）自体が検証対象。
- **例外**: 本番ログ（`data/logs/app_*.log`）で実際に観測された失敗を
  再現する回帰ケースは、ログ中の実際のユーザー発話をそのまま引用する
  （言い換えて丁寧にしない。実際に本番ユーザーが書いた文なので、
  長くても「詳しく書きすぎ」には当たらない）。

良い例（`system_prompt/006_annual_schedule_week_fix_ambiguous_calendar.yaml`）:
```yaml
turns:
  - >-
    annual_schedule.xlsx の「週間予定表」シートを確認してください。月と週の
    組み合わせがおかしいので直してください。
```
2文・条件1つ。「どこがどうおかしいか」の詳細は書かず、モデル自身に調査させる。

避けるべき書き方（ClaudeCodeが書きがちな失敗例。実際には使わない）:
```yaml
turns:
  - >-
    annual_schedule.xlsxの週間予定表シートについて、A列は月を表す結合セルで
    行数が4,4,4,6,6,8,6,6,7,7,7,9のパターンになっており、B列は週を表しますが
    「第X週Y月」という形式で本来のカレンダーと一致していません。A列の結合
    セル範囲は変更せずに保持したまま、B列の週番号だけを実カレンダーに基づいて
    正しく修正してください。
```
これは「答えを指示文に書いてしまっている」状態で、モデルの調査力・判断力を
一切検証できていない（本番ユーザーもこんな詳細を把握して指示しない）。

## 最重要2: expect / judge はパターン別の具体例からコピーして書く

「機械的に判定できるものはexpectに、意図判断はjudgeに」という原則だけでは
実際には書けない。以下5パターンから自分のケースに最も近いものを選び、
該当する既存ファイルを実際に `Read` してから、その構成を書き換える形で
作る（ゼロから書き起こさない）。

### パターンA: 特定ツール・委譲の有無を検証する（`tool_called_any`/`tool_not_called`）

「〜のときは委譲すべき」「〜を直接やってはいけない」を機械的に検証する、
一番シンプルなパターン。judge無しでも成立する。

```yaml
# 参考: evals/cases/system_prompt_scale/007_recipe_nutrition_web_search.yaml
expect:
  tool_called_any: [dispatch_agent, execute_python_code]
  tool_not_called: [analyze_image]
  response_not_contains: ["申し訳ありません", "できません", "機能がありません"]
```
- `tool_called_any` は複数書くと「いずれか1つ呼ばれればOK」（AND条件ではない）。
- `tool_not_called` は「これを直接呼んだら退行」というツールを列挙する
  （委譲すべき処理をメインエージェントが自分でやってしまうパターンの検出）。

### パターンB: 委譲時の引数の妥当性を検証する（`tool_call_args_contains`）

ツールが呼ばれるだけでなく「正しい相手・正しいスクリプトに」委譲したかを
見る。委譲先の取り違え（`worker`と`verifier`の混同、スクリプトパスの
間違いなど）を検出したいときに使う。

```yaml
# 参考: evals/cases/config_timeouts/002_script_heavy_excel_generation.yaml
expect:
  tool_called_any: [run_script]
  tool_call_args_contains:
    run_script: {script: "scripts/edit_excel.py"}
```
```yaml
# 参考: evals/cases/system_prompt_scale/007_recipe_nutrition_web_search.yaml
expect:
  tool_call_args_contains:
    dispatch_agent: {agent_type: "worker"}
```
該当ツールの**いずれか1回の呼び出し**が指定した引数キー全てに一致すれば
OK（全呼び出し一致は要求しない）。

### パターンC: 委譲判断・思考プロセスの妥当性を検証する（judgeのみ、複雑パターン）

ツール呼び出しの有無だけでは判定できない「調査を省略していないか」
「同じ思考を堂々巡りしていないか」「捏造していないか」を見るパターン。
judgeは必ず「背景→番号付き確認事項→どの番号が主根拠か」の3ブロックで書く。

```yaml
# 参考: evals/cases/system_prompt/006_annual_schedule_week_fix_ambiguous_calendar.yaml
# （実際のjudgeは長いため構成のみ抜粋。フルは元ファイルを読むこと）
judge: |
  本番ログ（app_20260814_001030.log）で実際に観測された失敗パターンの
  再発防止を検証する回帰ケース。<fixtureの何が矛盾を含むか、本番で何回・
  何分・何トークン浪費したかを具体的に書く>

  以下を確認せよ:
  1. transcript中のモデルの思考に、同一内容の計算をほぼそのまま3回以上
     繰り返している箇所がないか。
  2. 前提矛盾に気づいた時点で、ask_user_choice等で確認したか、もしくは
     明確な方針を自ら決めて実際の書き込みまで進んだか。どちらにも進まず
     状況説明だけを繰り返して終わっている場合は不合格。
  3. 結果JSONのturn_cutoffsを確認し、thinking_loop起因の打ち切りが
     何回発生したか報告せよ（打ち切り自体は不合格の直接理由にしない）。

  1・2のいずれかに明確に反する場合は不合格とせよ。
```
- 「〜が含まれているか」ではなく「〜を3回以上繰り返している箇所がないか」
  のように、transcriptの中で**具体的に何を数える／探すか**まで書く。
  抽象的な「思考が堂々巡りしていないか」だけでは判定者ごとに基準が揺れる。
- 判断が難しい項目は不合格にせず「要確認として報告せよ」と逃げ道を作って
  よい（`system_prompt_scale/007_*.yaml` の4・5番を参照）。
- 末尾で必ず「どの番号が不合格の主根拠か」を明示する。

### パターンD: 実測が主目的で、合否判定は最小限でよい（`config_timeouts`）

`config_timeouts` ターゲットは「会話が正常に完走したか」だけを軽く見れば
十分で、`turn_timings`（実測値）の取得自体が主目的。judgeを厚く書かない。

```yaml
# 参考: evals/cases/config_timeouts/001_llm_heavy_investigation.yaml
judge: |
  以下を満たしていれば合格。数値タイムアウトの妥当性そのものの判定では
  なく、turn_timings を実測するための会話が正常に完走したかどうかを
  見ればよい:
  - エラーで会話が完全に中断していない。
  - 複数年度分のファイルを実際に調査した形跡がある。
  - 最終回答が年度ごとの活動内容に言及している。
```

### パターンE: 単純な回答内容チェック（`response_contains`）

ツール呼び出しに関わらず、最終回答の中身だけを機械的に見る一番軽いパターン。
```yaml
expect:
  response_contains: ["Excel", "annual_schedule.xlsx"]
```

### 迷ったときの選び方

- 「委譲したか/しなかったか」だけで判定できる → パターンA（+ 必要ならB）。
- 「委譲判断・思考の妥当性」など機械的に測れない → パターンC（judge）。
  A/BのexpectとCのjudgeは併用してよい（`007_recipe_nutrition_web_search.yaml`
  はA+B+Cを全部使っている）。
- `config_timeouts`ターゲット → パターンD。
- 上記どれにも当てはまらないシンプルな回答内容確認のみ → パターンE。

## 手順

### 1. 目的とtargetを決める

- 何を検証したいケースか（新機能の確認／本番で観測した失敗の回帰防止／
  性能特性の実測）をユーザーに確認する。
- 対応する `target`（`evals/cases/<target>/`）を決める。
  - `system_prompt`: 通常規模のプロンプト品質検証。`/tune-prompt` の自動
    ループ対象。
  - `system_prompt_scale`: 実データ規模の重量級ケース（自動ループ対象外、
    手動実行）。
  - `config_timeouts`: 数値タイムアウトの実測用（パターンD、合否判定は
    簡略でよい）。
  - 上記に当てはまらない新カテゴリなら `evals/cases/<新target>/` を新規
    作成してよい（`run_case.py`/`run_all.py` は変更不要、README.md参照）。

### 2. ファイル名・idを決める

- 既存の `evals/cases/<target>/*.yaml` を確認し、`NNN_説明的スラッグ.yaml`
  の連番を対象targetディレクトリ内で1つ進める（他targetの連番とは独立）。
- `id` はファイル名から拡張子と番号プレフィックスを除いたスラッグにする。

### 3. fixture（`work_dir`）の要否を確認する

- `evals/fixtures/` に流用できる既存フィクスチャがあれば、`work_dir` で
  指定するだけで済ませる。
- 新規フィクスチャが必要な場合、`evals/fixtures/generate_*.py` の既存例
  （`generate_annual_schedule_fixture.py` 等）に倣い、**決定論的（seed固定）**
  な生成スクリプトを作る。手作業でファイルを置くのではなく、再生成コマンドを
  `notes` に書けるようにする。
- `work_dir` を指定しないケースは `config.ini` の `[default_workdir].dir`
  がそのまま使われる点に注意する。

### 4. turnsを書く

「最重要1」の方針（端的に、答えを書かない、例外は本番ログの実発話のみ）
に従う。書いたら「これは本番のLocohaneユーザーが実際に打ち込みそうな
文か？」を自問し、詳しすぎたら削る。

### 5. expect / judgeを書く

「最重要2」のパターンA〜Eから最も近いものを選び、対応する既存ファイルを
`Read` で実際に開いてから、その構成に沿って書く。

### 6. その他フィールドを設定する

- `auto_approve`: `run_script`/`execute_python_code`/`approve_plan` の
  承認ダイアログを自動承認するか。却下時の挙動を検証したいケースのみ
  `false` にする。
- `timeout_seconds`: 既定（`run_all.py` の `CASE_TIMEOUT_SECONDS`）で
  足りない重量級ケースのみ上書きする。
- `scripted_text_answers`: `ask_user_choice` が単一質問で呼ばれるたびに
  消費される回答。想定シナリオに沿って必要な件数だけ用意する。
- `notes`: 生成の経緯、fixture再生成コマンド、参照した本番ログのファイル名・
  日時など、人間向けの補足を書く（判定には使われない）。

### 7. 動作確認する

```
python -m evals.run_case evals/cases/<target>/<ファイル名>.yaml
```

- yaml の形式エラー（`case_schema.py` の `ValueError`）が出ないか確認する。
- `error: llm_unreachable` が出た場合はサーバー未起動が原因なので、
  ケース自体の問題ではない。
- 実行が完走し、`expect` があれば `rules_pass` の内容が意図通りか、
  `judge` があれば `transcript` が判定に必要な情報を含むかを確認する。
  ここではケースの**合否そのもの**は問わない（対象プロンプト資産の品質は
  `tune-prompt`/`tune-config-timeouts` 側の仕事）。ケースが正しく動作する
  ことだけを確認する。

### 8. 完了報告

- 追加したケースのパス・target・検証したい内容を1〜2行で要約して報告する。
- git へのステージング・コミットは行わない（ユーザーの判断に委ねる）。

## 安全策

- git へのコミット・ステージングは行わない。
- 既存ケースの yaml を無断で書き換えない（既存ケースの修正依頼と、新規
  ケース追加は別作業）。
- fixture を新規生成する場合、既存フィクスチャを上書きしない
  （別ディレクトリ名にする）。
