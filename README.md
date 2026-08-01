# Locohane（ローカルAIエージェント基盤 / SKILL.md 標準準拠）

> **README.md更新時の注意**: 見出し（`##`/`###`）を追加・変更・削除した場合は、
> `CLAUDE.md` の「ClaudeCode実行時ルール」に記載している README.md 見出し一覧も
> 必ず合わせて更新すること。

完全オフラインで動作する AI エージェント基盤。スキル定義は Anthropic 公開の
**Agent Skills 標準（SKILL.md 形式）** に準拠する。
仕様: <https://agentskills.io/specification>

設計方針は「薄く・透明に」。賢い仕掛け（動的 import・ホットリロード・メタクラス）は使わない。
起動時に `skills/` を走査して読む、それだけ。ファイルの保存・削除のパスとタイミングが
コードから明確に追える状態を最優先する。

![Locohane](public\settings\icon.png)

名前の由来：**Lo**cal（ローカル環境）+ 小羽（**cohane** / 軽量さ・和名っぽさ）

## 中核使命

**このプロジェクトは、高性能な大規模パラメータモデルではなく、低パラメータモデルでも
安定して Agent がタスクをこなせるようにすることを目指す。** 特に低パラメータモデルで
起こりやすい、思考ループ・パス生成不具合・途中停止（無言のまま応答を終える等）の制御に
全力を尽くす。この中核使命を支える技術は次の5点:

- **パスメモリー機能**（`src/path_memory.py`、`system_prompt.md`
  の Tool Usage Guidelines）: `Glob`/`Grep`/`Read` の結果に短い参照番号
  `@N` を自動付与し、LLM が長い絶対パスを自分で組み立て直して失敗を繰り返すことを防ぐ。
- **ループ検知＆ループガード＆リトライ機能**（`config.ini` の `[thinking_loop_guard]`、
  `src/llm.py` の `ChatLlamaCpp`/`ThinkingLoopDetected`）: ストリーミング中の応答が
  反復ループに陥ったことを検知し、ナッジメッセージを注入して自動リトライする。
  上限を超えても処理がフリーズしないよう、`app.py` 側で打ち切りメッセージを出して終了する。
- **一般的な glob 機能の拡張機能**（`src/tools.py` の `Glob`、ロジックは
  `src/file_tools.py`）: 通常の glob 結果に加え、一致件数・ディレクトリ一覧・
  パスメモリー参照を返し、LLM が探索範囲を自己修正しやすくする。
- **read-only 権限境界と explore サブエージェントの強制委譲によるリクエスト１回
  当たりのトークン使用量の分散による安定化**（`src/tools.py` の
  `_SUBAGENT_TOOLS`、`agents/explore.md`、`system_prompt.md` の Task Delegation）:
  低パラメータモデルは委譲先のサブエージェントでも状態変更を伴う操作（ファイル書き込み・
  任意コード実行）を暴走的に行うリスクがあるため、プロンプトの指示だけに頼らず、
  調査専用の `explore` サブエージェントには最初から `run_script`/`execute_python_code`
  を持たせない。その代わり、状態を変更しない `Read`/`Glob`/`Grep`/`json_query`を
  直接持たせ、調査に必要なファイルアクセスは確保しつつ書き込み・実行系の操作は
  構造的に不可能にする。
  この安全なサブエージェントを積極的に使わせるため、`system_prompt.md` の
  Task Delegation 節では「複数ファイル・複数画像・複数スキル本文を読む調査は、
  必ず `dispatch_agent`（`agent_type="explore"`）へ委譲すること」と、判断をLLMの
  自己判断に委ねず強く指示している（低パラメータモデルは「使えるなら使う」という
  曖昧な表現では委譲判断がぶれるため、条件を数値の目安（確認対象2件以上）まで
  具体化した上で必須化している）。委譲を促すことでコンテキストを節約しつつ、
  read-only境界と組み合わせて安全性・安定性の両方を高めている。
- **メインエージェントの1リクエストあたりトークン量を上限内に抑える設計**
  （`agents/worker.md`、`src/main_token_guard.py`、`src/context_trim.py`、
  `config.ini` の `[graph] token_guard_*`）: メインエージェントはプログラムで
  言うメインルーチン（司令塔）であり、ここが不安定になるとタスク全体が破綻する。
  そのため「1リクエストあたりのトークン数をできるだけ低く（目安64,000未満）保つ」
  ことをメインエージェントの安定運用における設計原則としている。大量ファイル
  処理では、委譲先（サブエージェント）が読み取った内容をメインへ全文返させると、
  メインがそれを書き写す・保持するだけでリクエストごとのトークン量が単調に
  増加し、最終的にコンテキスト上限に張り付いて処理が続けられなくなる
  （実測: 297枚の画像処理タスクで1リクエストあたり24,833→128,000トークンまで
  増加し無応答のまま停止した事例がある）。この対策として、読み取りから書き出し
  までを委譲先の中で完結させ、メインへは「処理件数・失敗分」のみを返す
  書き込み可能サブエージェント `worker` を用意し、可能な限り処理そのものを
  サブエージェント側へ寄せることで、1リクエストあたりのトークン量がメイン
  エージェントに乗らないようにしている。加えて、`context_trim` による
  AIMessage/ToolMessageの切り詰め、`main_token_guard` による閾値到達時の
  自動引継ぎプロンプト生成（新しいチャットでの再開を促す）が、この設計を
  多重に補強している。

---

## アーキテクチャ

```
                 ┌──────────────────────────────────────────────┐
   ブラウザ ───▶ │  app.py (Chainlit UI)                        │
                 │   - @cl.on_message でユーザー入力を受ける      │
                 │   - astream_events でトークンをストリーム表示  │
                 │   - ツールコールを cl.Step で可視化            │
                 │   - approve_plan の承認を                      │
                 │     cl.AskActionMessage で確認（承認/拒否）    │
                 └───────────────┬──────────────────────────────┘
                                 │ 実行
                 ┌───────────────▼──────────────────────────────┐
                 │  src/graph.py  ReAct ループ（実装2種を切替）   │
                 │                                               │
                 │  handwritten: START→[agent]⇄[tools]→END       │
                 │    （手書き StateGraph。挙動を自前配線）        │
                 │  prebuilt   : create_react_agent に委譲        │
                 │    （config.ini [graph] implementation で選択）│
                 └───┬───────────────────────────┬──────────────┘
                     │ モデル呼び出し             │ ツール実行
       ┌─────────────▼───────────┐   ┌───────────▼──────────────────────┐
       │ ChatOpenAI              │   │ src/tools.py (29ツール)             │
       │  → llama-server /v1     │   │  read_skill / read_skill_file /    │
       │  (OpenAI 互換)          │   │  run_script / execute_python_code /│
       └─────────────────────────┘   │  get_tool_source / check_work_dir_status /│
                                      │  Read / Glob / Grep / json_query / │
                                      │  list_path_memory / analyze_image /│
                                      │  dispatch_agent / create_plan /    │
                                      │  approve_plan / update_task_progress/│
                                      │  get_plan_status / lock_plan_mode /│
                                      │  AskUserQuestion / ask_user_choice /│
                                      │  provide_download / show_image /   │
                                      │  create_memory / update_memory /   │
                                      │  delete_memory / read_memory /     │
                                      │  search_memory / list_memories /   │
                                      │  help                              │
                                      │  （skills/ ファイル操作は配下限定、 │
                                      │   Read/Glob/Grep は任意の絶対パス） │
                                      └───────┬───────────────┬──────────┘
                                              │ 走査/読込/実行  │ 読み書き
       起動時 ┌─────────────────────────────┐  │   ┌────────────▼───────────┐
   Discovery │ src/skills.py                │◀─┘   │ src/memory.py           │
    (第1段階) │  skills/ を走査し             │      │  data/memory/ 配下に    │
             │  name+description を          │      │  User/Feedback/Project/│
             │  システムプロンプトへ注入      │      │  Reference の4種を保存 │
             └─────────────────────────────┘      │  MEMORY.md 索引を       │
                                                    │  {{memory}} へ差し込み │
   会話状態 ┌──────────────────────────────┐      └────────────────────────┘
           │ AsyncSqliteSaver              │   ┌────────────────────┐
           │  → data/checkpoints.sqlite   │   │ skills/            │
           └──────────────────────────────┘   │  <name>/SKILL.md   │
                                                │  <name>/scripts/   │
                                                │  <name>/references/│
                                                │  <name>/assets/    │
                                                └────────────────────┘
```

`dispatch_agent` のみ、`src/subagent.py` 内で独立した ReAct ループを回す特殊なツールで、
その内部のツール呼び出し（`read_skill`/`read_skill_file`/`run_script`/`execute_python_code` 等、
`agent_type` で選んだ種別が持つツールに限る）は親の会話履歴・グラフトレースには乗らない
（コンテキスト節約のため意図的な設計）。種別定義は `agents/*.md`（`name`/`description`/`tools`
の frontmatter、ClaudeCode の `.claude/agents/*.md` 相当）を起動時に走査して読み込む。

起動時に `[paths].project_locohane_dir`（既定 `.locohane`）配下の `LOCOHANE.md`
（存在すれば）も読み込まれ、システムプロンプトの `{{project_instructions}}` へ
差し込まれる（ClaudeCode の CLAUDE.md 相当、`src/project_instructions.py` 参照）。

### progressive disclosure（3 段階）の実装場所

| 段階 | 内容 | 実装 |
|------|------|------|
| 1. Discovery | 起動時に各 `SKILL.md` の frontmatter（name/description）のみ注入 | `src/skills.py` |
| 2. Read | LLM がスキルを選び、`read_skill` で本文全体を読む | `src/tools.py` の `read_skill` |
| 3. Execute | 本文の指示に従い `read_skill_file`/`run_script` で必要時のみ読む・実行 | `src/tools.py` の `read_skill_file`/`run_script` |

スキル読み込みは **すべて LangGraph のツールコール** として実装しており、グラフのトレースに乗り
Chainlit 側で「今このスキルを読んでいます」等のステップとして可視化される。

### ビルトインツール一覧とLLMへの公開方法

「ビルトインツール」とは、スキル本文とは独立して常時 LLM に公開される、状態を持たない
関数群を指す（対して SKILL.md は「知識・手順書」であり、それ自体はツールではない。
LLM は `read_skill`/`read_skill_file`/`run_script` という**ビルトインツール**を経由して
間接的にスキル本文やスクリプトへアクセスする）。

**LLM への認識のさせ方**: system_prompt.md 等のテキストへツールの説明を埋め込む
方式（プロンプトベース）では**ない**。OpenAI 互換 API の `tools` パラメータとして
リクエストのたびに構造化データで送信する方式を取る。

1. 各ツールは `src/tools.py` で LangChain の `@tool` デコレータ付き関数として定義する。
   関数の docstring が `description`、型ヒント付き引数が JSON Schema の `parameters` に
   自動変換される（`langchain_core.tools.tool` の標準機能。本プロジェクト側に
   独自の変換コードはない）。
2. `get_all_tools()`（`src/tools.py`）がビルトインツールと MCP 由来の動的ツールを
   合流させたリストを返す。
3. `src/graph.py` の `build_model(config).bind_tools(get_all_tools())`（サブエージェント側は
   `src/subagent.py`）が、このリストを `ChatOpenAI` 系クラス（`src/llm.py` の
   `ChatLlamaCpp`）に紐付ける。
4. 実際の HTTP リクエスト送信時、`bind_tools` で渡した関数群が OpenAI Function Calling
   形式の `tools` 配列へ変換され、`base_url`（llama-server の `/v1` エンドポイント）
   宛のリクエストボディに含まれる。LLM 側の応答に含まれる `tool_calls` を
   LangGraph が受け取り、対応する Python 関数を実行する。

つまり LLM が「どんなツールが使えるか」を知る手がかりは、システムプロンプトの文章では
なく、**リクエストごとに送られる `tools` フィールドそのもの**である
（`name`/`description`/`parameters` 込みで、モデルの Function Calling 機能が解釈する）。
スキルの frontmatter（name/description）だけはこれとは別に、`src/skills.py` が
起動時にシステムプロンプトへテキスト注入する（Discovery 段階、こちらはプロンプトベース）。

上記3段階に加えて、スキルの読み込みとは独立したツールが以下の32個ある（いずれも `src/tools.py`）。

| ツール | 役割 |
|--------|------|
| `read_skill` | スキルの SKILL.md 本文全体を読み込む（progressive disclosure 第2段階） |
| `read_skill_file` | skills ディレクトリ配下のファイルを読み込む（references/assets 等。progressive disclosure 第3段階） |
| `run_script` | スキルの scripts/ 配下のスクリプトを実行する（要承認。`config.ini` で承認不要に切替可）。完了までブロックするため、タイムアウトに近い長時間実行が見込まれる場合は `run_script_background` を使う |
| `run_script_background` | `run_script` と同じスクリプトをバックグラウンドで起動し、即座に `job_id` を返す（要承認は同様）。完了を待たずエージェントのターンを解放する |
| `check_script_job` | `run_script_background` のジョブの状況（実行中の経過秒数・途中出力）または最終結果を取得する |
| `stop_script_job` | `run_script_background` のジョブを強制終了する |
| `execute_python_code` | LLMが生成したPythonコードをその場で実行（要承認。`config.ini` で無効化可） |
| `get_tool_source` | `run_script` がエラーになった際、原因調査用にスクリプトの絶対パスを返す（中身は返さない） |
| `check_work_dir_status` | 現在の作業ディレクトリの実際のアクセス状況を確認する |
| `Read` / `Glob` / `Grep` | ローカルファイルシステム上の任意の絶対パスに対する読込・ファイル名検索・全文検索（ClaudeCode の同名ツールに合わせた名前。読み取り専用のため計画未承認でも常に呼べる。ロジックは `src/file_tools.py`） |
| `json_query` | JSON/dict に対する JMESPath クエリ（読み取り専用） |
| `list_path_memory` | 現在の会話のパスメモリー（`@N`）登録内容を一覧表示する（読み取り専用） |
| `provide_download` | 既存のファイルをチャット画面にダウンロードボタンとして提示する |
| `show_image` | 既存の画像ファイルをチャット画面にプレビュー表示する（LLM自身は内容を見ない。「表示して」「見せて」に） |
| `analyze_image` | 画像ファイルをLLMへ視覚情報として見せ、LLM自身が内容を解析・説明・判断する（Vision対応モデル向け） |
| `dispatch_agent` | タスクをサブエージェント（`src/subagent.py`）へ委譲し最終回答のみ受け取る。`agent_type` 引数でサブエージェントの種別を必ず指定する（暗黙の既定値は無い）。種別定義は `agents/*.md`（ClaudeCode の `.claude/agents/*.md` 相当）。`.locohane/agents/*.md` ともマージ走査され、同名は `.locohane/agents` 側が優先される |
| `create_plan` / `approve_plan` / `update_task_progress` | 複数ステップの実行計画を作成・承認・進捗更新（承認後は`run_script`の個別確認をスキップ）。各ステップは `content`（内容）と `activeForm`（実行中表示用の現在進行形）を持つ |
| `get_plan_status` / `lock_plan_mode` | 現在 Plan Mode（書き込み系ツールがブロックされたロック状態）か Edit Automatically（承認済み計画を実行できる状態）かを確認し、後者から前者へユーザー承認なしに手動で戻す |
| `AskUserQuestion` / `ask_user_choice` | 会話継続に必要な追加情報をユーザーへ質問（`AskUserQuestion` は自由記述。`labels` 省略時は単一入力、指定時は複数項目をまとめて提示。`ask_user_choice` は選択肢形式） |
| `create_memory` / `update_memory` / `delete_memory` / `read_memory` / `search_memory` / `list_memories` | スレッドをまたぐ永続メモリー（`src/memory.py`）の保存・更新・削除・全文読込・検索・一覧。主エージェントのみに公開し `dispatch_agent` のサブエージェントには渡さない |
| `help` | ユーザー向けヘルプ本文（`system_prompt/help.md`）をそのまま返す |

`run_script`（`run_script_background` 含む）と `execute_python_code` は書き込み系ツールのため、
`create_plan`/`approve_plan` で計画がユーザー承認済み（`cl.user_session["plan_approved"]` が True）でない限り実行できず、
未承認の場合はエラーを返す（Plan Mode）。承認自体は `cl.AskActionMessage` による
✅承認/🚫拒否ボタンで行い、タイムアウト時は安全側（拒否）に倒す。`execute_python_code` は
`config.ini` の `[scripts] code_execution_enabled` でツール自体を無効化できる。

---

## ファイル構成

```
Locohane/
├── config.ini              # 全設定（LLM接続・保存先パス・スクリプト実行・グラフ実装・サブエージェント）
├── requirements.txt        # pip 依存（バージョン固定）
├── app.py                  # Chainlit エントリ
├── app.bat                 # Windows用起動バッチ
├── chainlit.md             # Chainlit ウェルカム画面
├── CLAUDE.md               # プロジェクト固有の追加指示（Claude Code 形式）
├── QWEN.md                 # プロジェクト固有の追加指示（Qwen Code 形式、内容はCLAUDE.md参照の1行）
├── LICENSE                 # 本プロジェクトのライセンス（MIT）
├── THIRD_PARTY_LICENSES.md # 依存OSSライセンス一覧（tools/gen_licenses.pyで再生成）
├── memo.md                 # 開発者向けメモ
├── issue.md                # 既知の課題メモ
├── pytest.ini              # pytest設定
├── .env.example            # 環境変数サンプル
├── .gitignore              # Git除外設定
├── .chainlit/
│   ├── config.toml         # Chainlit設定
│   └── translations/       # 多言語翻訳ファイル
├── .claude/
│   └── skills/             # Claude Code用スキル（tune-prompt等）
│       ├── setup-basic-config/
│       ├── tune-config-timeouts/
│       └── tune-prompt/
├── .locohane/                # project_locohane_dir（既定）。配下を起動時に自動検知
│   ├── LOCOHANE.md.example  # LOCOHANE.md のサンプル（配置するとプロジェクト固有指示になる）
│   ├── settings.json        # MCPサーバー接続設定
│   ├── skills/              # skills_dir にマージ走査される追加スキル（同名は優先）
│   │   ├── README.md
│   │   ├── docx-tools/       # Word文書の読込・生成・編集（Track Changes対応）
│   │   ├── excel-tools/      # xlsx/xls/xlsm読込・編集・数式再計算
│   │   ├── pdf-tools/        # PDF読込・ページ画像化・PDF生成
│   │   └── pptx-tools/       # PowerPoint読込・生成・テンプレート部分編集
│   └── agents/              # agents_dir にマージ走査される追加エージェント種別（同名は優先）
├── .officecli/                # このディレクトリに配置すると利用可能。（config.ini の project_locohane_dir にディレクトリ登録必要）
│   │                           # OfficeCLI（外部OSS、Apache 2.0、要別途インストール）の配置先。
│   │                           # .gitignore 対象のためリポジトリには同梱されない
│   ├── bin/officecli.exe     # 単一バイナリ本体（.NET runtime内蔵、Office製品のインストール不要）
│   ├── skills/                # skills_dir にマージ走査される、OfficeCLI公式配布のSKILL.md群
│   │   ├── morph-ppt/         # 既存pptxを指定デザインスタイルへ再構成するスキル
│   │   └── morph-ppt-3d/      # 3Dモデル(.glb)を含むpptx編集スキル
│   ├── LICENSE                # Apache License 2.0
│   ├── NOTICE
│   ├── THIRD-PARTY-NOTICES.txt # OfficeCLIが内包する第三者コンポーネント一覧
│   ├── SECURITY.md
│   └── README_ja.md           # OfficeCLI公式README（日本語版）
├── frontend/                # カスタムReactフロントエンド
│   ├── src/
│   │   ├── components/      # UIコンポーネント（AskFormBar, PlanCard, SidePanel等）
│   │   └── utils/           # ユーティリティ
│   └── public/
├── public/                  # Chainlit公開ファイル
│   ├── build/               # フロントエンドビルド成果物
│   ├── settings/            # ヘッダー・アイコン・ウェルカム文言の設定
│   ├── icons/                # UIアイコン画像
│   ├── custom.css           # カスタムCSS
│   └── UI変更ガイド.md      # UIカスタマイズガイド
├── system_prompt/
│   ├── system_prompt.md     # メインエージェント用システムプロンプト
│   ├── help.md              # help ツールが返すユーザー向けヘルプ
│   ├── compaction_prompt.md # 会話履歴の自動要約指示
│   └── subagent_common.md   # サブエージェント共通プロンプト
├── tools/
│   └── gen_licenses.py      # THIRD_PARTY_LICENSES.md 再生成スクリプト
├── evals/                   # プロンプト資産の自動評価・チューニングループ
│   ├── README.md            # 実行方法・ケースの書き方
│   ├── run_all.py           # 全ケース一括実行
│   ├── run_case.py          # 1ケース実行（Chainlit UI不要）
│   ├── case_schema.py       # ケースYAMLのスキーマ
│   ├── headless_chainlit.py # Chainlitスタブ
│   ├── timing_callbacks.py  # config_timeouts用の実測タイミング計測コールバック
│   ├── analyze_timing.py    # 実測タイミング結果の集計・分析
│   ├── tuning_log.md        # チューニング履歴
│   ├── cases/               # 評価ケース（YAML）
│   │   ├── system_prompt/   # システムプロンプト用ケース
│   │   ├── system_prompt_scale/ # スケーリング用ケース
│   │   └── config_timeouts/ # timeout系設定チューニング用ケース
│   ├── fixtures/            # 評価用フィクスチャデータ
│   ├── history/             # チューニング前スナップショット
│   └── results/             # 実行結果（再生成可能、.gitignore済み）
├── src/
│   ├── config.py            # config.ini ローダー（frozen dataclass）
│   ├── skills.py            # スキル発見ミドルウェア（第1段階 Discovery）
│   ├── agent_types.py       # エージェント種別発見（agents/*.md）
│   ├── tools.py             # LangGraph ツール29種（第2・第3段階＋独立ツール）
│   ├── file_tools.py        # Read/Glob/Grep/json_query のロジック層
│   ├── path_memory.py       # パスメモリー（@N）レジストリの読み書き
│   ├── memory.py            # 永続メモリーの読み書き・索引再構築
│   ├── project_instructions.py # .locohane/LOCOHANE.md の読込
│   ├── graph.py             # ReAct ループ（handwritten / prebuilt を切替）
│   ├── llm.py               # ChatOpenAI（llama-server接続）の構築
│   ├── context_trim.py      # 古い ToolMessage の切り詰め
│   ├── context_compaction.py # 会話履歴の自動要約・圧縮
│   ├── subagent.py          # dispatch_agent の内部ReActループ
│   ├── mcp_client.py        # MCPサーバー接続（stdio）・ツール変換
│   ├── chat_log.py          # 会話ログのテキストファイル記録
│   ├── cleanup.py           # 不要ファイルの自動削除
│   ├── files.py             # ファイルアップロード処理
│   ├── images.py            # 画像処理・Data URL変換
│   ├── log_rotation.py      # app.log の日時ローテーション
│   └── uploads.py           # アップロードファイル管理
├── agents/
│   ├── explore.md           # 読み取り専用エージェント種別
│   └── verifier.md          # 成果物検証用エージェント種別
├── skills/
│   ├── SKILLS_README.md    # スキル開発者向けガイド
│   ├── word-counter/        # テキストの行数/単語数/文字数を数える（サンプル）
│   ├── git-commit-style/    # コミットメッセージ規約（知識のみ）
│   └── skill-creator/       # 新しいスキルの作成・既存スキルの改善・eval検証を行うメタスキル
│       # Read/Glob/Grep/json_query/list_path_memory はネイティブツール化済み
│       # （src/file_tools.py、src/path_memory.py）。
│       # office系（docx/excel/pptx）・pdf-tools は .locohane/skills/ 配下
├── tests/                   # pytestテストケース
│   ├── conftest.py
│   ├── fixtures/
│   └── test_*.py            # 各モジュールのテスト
└── data/                    # 実行時生成（.gitignore 済み）
    ├── checkpoints.sqlite   # LangGraph の会話状態
    ├── uploads/             # Chainlit にアップロードされたファイル
    ├── logs/                # アプリ動作ログ（日時ローテーション）
    ├── logs_chat/           # 会話ログ（ユーザー発言＋AI応答）
    ├── memory/              # 永続メモリー（type別サブフォルダ＋MEMORY.md）
    ├── path_memory/         # パスメモリーレジストリ（.json）
    └── temp/                # 一時ファイル
```

各モジュール冒頭に「そのファイルが仕様のどの段階を実装するか」をコメントで記載している。

---

## Agent Skills 仕様への準拠範囲

仕様: <https://agentskills.io/specification>

### 準拠している範囲

- **SKILL.md frontmatter**: `name`・`description`（必須）を検証。
  `license`・`compatibility`・`metadata`・`allowed-tools`（任意）は読み取り可能。
- **`name` 検証ルール**: 1〜64 文字 / 小文字英数字・ハイフン・アンダースコアのみ /
  先頭末尾は区切り文字不可 / 区切り文字の連続不可 / **親ディレクトリ名と一致**。
- **`description` 検証**: 非空・1024 文字以内。
- **ディレクトリ構造**: `scripts/`・`references/`・`assets/` を想定した読み込み・実行。
- **progressive disclosure の 3 段階**（Discovery / Read / Execute）。
- 仕様違反の SKILL.md は **スキップし警告ログ** を出す（全体は落とさない）。

### 範囲外（実装していない）

- **`allowed-tools`**: フィールドは読み取れるが、それに基づく自動承認は未実装（仕様上も実験的）。
- **公式バリデータ（skills-ref）非統合**: 自前の最小検証のみ。厳密検証が必要なら
  `skills-ref validate ./skills/<name>` を別途利用のこと。
- **深いネスト参照**: reference 参照は SKILL.md から 1 階層を想定。

---

## データの保存場所と手動削除の手順

すべて `data/` 配下（`config.ini` の `[paths]` で変更可）。`data/` は `.gitignore` 済み。

| パス | 中身 | 削除してよいタイミング | 削除方法 |
|------|------|------------------------|----------|
| `data/checkpoints.sqlite` | LangGraph の会話状態（全スレッドの履歴） | 過去の会話履歴が不要になったとき | ファイルを削除（アプリ停止中に） |
| `data/uploads/` | Chainlit にアップロードされたファイル | アップロード資料が不要になったとき | フォルダ内を削除 |
| `data/logs/app.log` | アプリの動作ログ | いつでも | ファイルを削除 |
| `data/memory/` | 永続メモリー（`user`/`feedback`/`project`/`reference` サブフォルダ＋`MEMORY.md`索引） | 蓄積した記憶が不要になったとき | フォルダ内を削除（`MEMORY.md`は次回保存時に再生成される） |

`data/uploads/` は `config.ini` の `[uploads] retention_days`（既定7日）を過ぎたファイルを
`cleanup_interval_hours`（既定1時間）おきに自動削除する。`retention_days` を0以下にすると
自動削除は無効化される。

手動全削除（PowerShell / cmd、アプリ停止中に実行）:

```powershell
Remove-Item -Recurse -Force .\data\*
```

「孤児ファイル」を作らない設計: 保存は `app.py` の `_save_uploads()`（`data/uploads/` へ）、
会話履歴は `AsyncSqliteSaver`（`data/checkpoints.sqlite`）に限定される。
どこに何が書かれるかはコード上で一意に追える。

---

## セットアップと起動

### 1. 依存インストール

```bash
pip install -r requirements.txt
```

### 2. llama-server（llama.cpp）の起動例

OpenAI 互換エンドポイントを `http://localhost:8080/v1` で公開する:

```bash
llama-server --model C:\path\to\model.gguf --alias local-model --host 127.0.0.1 --port 8080 -c 8192
```

- `--alias` はモデル名。`config.ini` の `[llm] model` と揃える。
- 接続先・モデル名は `config.ini`（または環境変数 `LLM_BASE_URL` / `LLM_MODEL`）で切り替え可能。
- サンプリング（`top_p`/`top_k`/`repeat_penalty`/`frequency_penalty`/`presence_penalty`/`max_tokens`）は
  `--repeat-penalty` 等の起動時 CLI オプションでも既定値を指定できるが、`config.ini` の
  `[llm]` 側で値を指定した場合はリクエストごとにその値が優先される（未指定＝空欄の項目のみ
  llama-server 起動時の既定値が使われる）。

> **⚠️ 商用利用時のモデルライセンス**
> llama.cpp 自体は MIT ですが、**動かす GGUF モデルのライセンスはモデルごとに異なります**
> （基盤コードには含まれません）。商用利用時は必ず確認してください。
> - Qwen2.5 系 / Mistral 系 → Apache 2.0（商用可・推奨）
> - Llama 系 → Meta Llama Community License（商用可だが月間7億MAU条項など独自制約あり）
> - Gemma 系 → 独自の Gemma Terms of Use
> 迷ったら Apache 2.0 のモデル（Qwen2.5 等）を選ぶのが安全です。

### 3. 環境依存パスの最低限の設定

llama-server を起動したら、環境依存で必ず実際の値に合わせる必要がある
パス設定を行う。これらは `config.ini` だけでなく `app.bat` と
プロジェクト直下の `CLAUDE.md` にも分散しているので注意する。

**`config.ini`（LLM接続先・エージェントが実行時に使うPython）**

| セクション | キー | 設定する値 |
|---|---|---|
| `[llm]` | `base_url` | 手順2で起動した llama-server の OpenAI 互換エンドポイント（例: `http://localhost:8080/v1`） |
| `[llm]` | `api_key` | llama.cpp は認証不要のため通常はダミー値のままでよい |
| `[llm]` | `model` | 手順2の `--alias` と一致させるモデル名 |
| `[scripts]` | `python` | `run_script`/`execute_python_code` ツール（LLMが実行時に呼び出す）が使う Python 実行ファイルの絶対パス |

**`app.bat`（アプリ本体＝chainlitサーバーを起動する仮想環境）**

| 変数 | 設定する値 |
|---|---|
| `PYTHON_DIR` | `chainlit run app.py` を実行する Python 仮想環境のディレクトリ（`Scripts` はこの変数からの相対で解決される） |

**プロジェクト `CLAUDE.md`（Claude Code がこのプロジェクトを開発・テストする際に使う実行環境）**

| 見出し | 設定する値 |
|---|---|
| `Python実行環境` | Claude Code がスクリプト実行・動作確認に使う Python 実行ファイルの絶対パス（通常は `app.bat` の `PYTHON_DIR` と同じ仮想環境） |
| `Node.jsパス` | `frontend/`（package.json あり）のビルド・テストに Claude Code が使う Node.js のディレクトリ |

これら3ファイルは用途が異なるため、同じ値を指すこともあれば異なることもある。
`config.ini` の `[scripts].python` はアプリ実行中にLLMが呼び出すスクリプト用、
`app.bat` の `PYTHON_DIR` はアプリ本体の起動用、プロジェクト `CLAUDE.md` の
2項目は開発時に Claude Code が使う用、という違いを意識して設定する。

**方法A: `setup-basic-config` スキルを使う（推奨）**

Claude Code 上で `/setup-basic-config` を実行すると、上記3ファイル・
7項目の現在値を提示した上で対話形式で新しい値を確認し、まとめて
更新してくれる（`.claude/skills/setup-basic-config/SKILL.md`）。

**方法B: 各ファイルを直接編集する**

エディタで `config.ini` を開き、`[llm]` セクションの
`base_url`/`api_key`/`model`、`[scripts]` セクションの
`python` を直接書き換える。`app.bat` の `PYTHON_DIR` と、プロジェクト
`CLAUDE.md` の「Python実行環境」「Node.jsパス」も同様に書き換える。
各項目の意味は後述の「設定リファレンス（config.ini）」も参照。

### 4. アプリ起動

```bash
cd C:\DT_Python\Locohane
C:/DT_Python/Python311/env_claudecode/Scripts/chainlit run app.py -w
```

ブラウザで開き、例えば「このテキストの単語数を数えて」と送ると、
`read_skill`（word-counter の本文読込）→ `run_script`（`count.py` 実行）が
**ステップとして可視化** され、結果がストリーミング表示される。

---

## 同梱スキル

| スキル | 配置場所 | 種別 | 内容 |
|--------|----------|------|------|
| `word-counter` | `skills/` | スクリプト実行を伴う | `scripts/count.py` でテキストの行数/単語数/文字数を数える。`run_script` の実演。 |
| `git-commit-style` | `skills/` | 知識のみ | このプロジェクトのコミットメッセージ規約。スクリプトなし、本文の知識のみで回答。 |
| `skill-creator` | `skills/` | スクリプト実行を伴う | 新しいスキルの作成・既存スキルの改善・description のトリガー精度最適化・evalハーネスによる検証を行うメタスキル。 |
| `pdf-tools` | `.locohane/skills/` | スクリプト実行を伴う | PDFのテキスト抽出・ページ画像化（レイアウト/図表/スキャン内容の視覚把握）・PDF生成（日本語対応）。 |
| `docx-tools` | `.locohane/skills/` | スクリプト実行を伴う | Word文書の読込・生成・編集（検索置換、Track Changes/変更履歴の付与・確定・却下を含む）。 |
| `excel-tools` | `.locohane/skills/` | スクリプト実行を伴う | xlsx/xls/xlsmの読込・編集（グラフ・条件付き書式・データ検証を含む）・数式再計算・VBAマクロコードの読み込み/追加/上書き/削除・実行。 |
| `pptx-tools` | `.locohane/skills/` | スクリプト実行を伴う | PowerPointの読込・生成（16:9テンプレート方式）・既存テンプレートの部分編集（デザインを保った差し替え・複製・削除・並び替え）。 |
| `web-search` | `.locohane/skills/` | スクリプト実行を伴う | Tavily APIによるWeb検索。スキル専用の`scripts/.env`にTAVILY_API_KEY設定時のみ動作（既定では通信なし）。 |

スキル開発の詳細な手順・規約は [`skills/SKILLS_README.md`](skills/SKILLS_README.md) を参照。

上記に加え、[OfficeCLI](https://github.com/iOfficeAI/OfficeCLI)（外部OSS、後述）を
`.officecli/` に導入すると、同梱の `morph-ppt`（既存pptxを指定デザインスタイルへ
再構成）・`morph-ppt-3d`（3Dモデル(.glb)を含むpptx編集）スキルも `project_locohane_dir`
経由で自動検知される。

処理時間が `[scripts].timeout`（既定300秒）に近づく、または超えうるスクリプトを持つスキルは、
SKILL.md 側で `run_script` ではなく `run_script_background` を使うよう指示し、起動後は
ユーザーに実行中である旨を伝えた上で、後続のやり取りで `check_script_job` を呼んで状況を
確認するパターンを明記する（完了通知はポーリング方式。エージェントが自発的にターン内で
待ち続けるのではなく、ユーザーとの次のやり取りで確認する運用を想定）。

### 新しいスキルの追加方法

`skills/<name>/SKILL.md` を作るだけ（`<name>` はフォルダ名 = frontmatter の `name`）。
ビルトイン一式を汚したくない場合は `.locohane/skills/<name>/SKILL.md` に置いてもよい
（`skills/` とマージ走査され、同名スキルがあれば `.locohane/skills` 側が優先される）。
最小の SKILL.md:

```markdown
---
name: my-skill
description: 何をするスキルか、いつ使うかを具体的に書く。
---

# my-skill

ここに手順を書く。必要なら scripts/ references/ assets/ を追加する。
```

アプリを再起動すると起動時走査で自動的に発見される（動的リロードはしない）。

### OfficeCLI の導入（任意）

[OfficeCLI](https://github.com/iOfficeAI/OfficeCLI)（Apache License 2.0）は、Word/Excel/
PowerPointをOfficeのインストールなしで読み書きできる、AIエージェント向けの単一バイナリCLI
（`.NET runtime`内蔵）。本プロジェクトへは直接組み込まず、公式配布物一式（バイナリ・付属
スキル・ライセンス文書）をリポジトリ直下 `.officecli/` にそのまま展開し、`config.ini` の
`project_locohane_dir` に `"./.officecli"` を追加することで、同梱の `skills/`（`morph-ppt`・
`morph-ppt-3d`）を Locohane のスキル発見機構（`skills_dir` へのマージ走査）に乗せる形で
利用する。

- **導入手順**: [公式リリース](https://github.com/iOfficeAI/OfficeCLI/releases)から
  Windows用バイナリ（`officecli-win-x64.exe` 等）を取得し、`.officecli/bin/officecli.exe`
  として配置する（付属の `LICENSE`・`NOTICE`・`THIRD-PARTY-NOTICES.txt`・`skills/` も
  公式配布のまま `.officecli/` 配下に置く）。この配置規約に従えば、OS側のPATH環境変数へ
  手動登録しなくても `config.ini` `[paths].bin_path`（既定 `./.officecli/bin`）経由で
  run_script/execute_python_code のサブプロセスから `officecli` コマンドを呼び出せる
  （`src/tools.py` の `_subprocess_env()` 参照）。別の場所に配置した場合は `bin_path` を
  書き換えること。
- **`.gitignore` 対象**: `.officecli/` はリポジトリにコミットされない
  （外部OSSバイナリのため、利用者ごとに個別導入する想定）。導入しない場合、
  `project_locohane_dir` に指定していても該当ディレクトリが存在しないだけでエラーには
  ならない。
- **完全オフライン運用時の注意**: OfficeCLIはバックグラウンドで更新の自動チェックを行う
  （既定で有効）。オフライン環境で使う場合は `officecli config autoUpdate false` で
  恒久的に無効化するか、実行のたびに環境変数 `OFFICECLI_SKIP_UPDATE=1` を設定してスキップ
  する（詳細は後述の「外部通信について（完全オフライン保証）」を参照）。

---

## MCPサーバー接続

Anthropic公式のModel Context Protocol（[仕様](https://modelcontextprotocol.io/specification)）に
準拠し、外部のMCPサーバー（stdioトランスポートのみ対応）をLLMのツールとして動的に
利用できる。実装は `src/mcp_client.py`（公式Python SDK、PyPI: `mcp` を直接使用）。

- **設定ファイル**: プロジェクト直下 `.locohane/settings.json`（git管理対象）。
  Claude Code / Qwen Code の `mcpServers` 設定形式を参考にしている。
  ```jsonc
  {
    "mcpServers": {
      "my-server": {
        "command": "npx",
        "args": ["-y", "@some/mcp-server"],
        "env": { "API_KEY": "${MY_SERVER_API_KEY}" },
        "cwd": null,
        "disabled": false
      }
    },
    "mcp": {
      "enabled": true,
      "connectTimeoutSeconds": 15,
      "callTimeoutSeconds": 60
    }
  }
  ```
  トップレベルの `"mcp"` ブロックは `config.ini` の `[mcp]` セクション（既定値）を
  上書きする任意項目。`env` の値に `${ENV_VAR_NAME}` と書くと、接続時に
  `os.environ` から展開される。**`.locohane/settings.json` はgit管理対象のため、
  APIキー等の機密情報を直接書かず、必ずこの `${ENV_VAR_NAME}` 形式で参照すること。**
  未解決の環境変数がある場合、そのサーバーのみ接続をスキップする（警告ログ）。
- **接続方式**: アプリ起動時（`@cl.on_app_startup`）に `mcpServers` の全件へ自動
  接続する。Chainlit UIから都度追加する機能は提供しない。
- **ツール名の命名規則**: 衝突を避けるため `mcp__<サーバー名>__<ツール名>`
  （64文字以内に正規化）としてLLMに公開する。
- **エラー時の挙動**: 個別サーバーへの接続失敗・タイムアウトはそのサーバーのみ
  警告ログを出してスキップし、アプリ全体の起動は継続する。ツール呼び出し自体の
  失敗も例外を送出せず `"エラー: ..."` 形式の文字列として返す。
- **既知の限界**: 接続後にサーバープロセスがクラッシュした場合の自動再接続は
  行わない。応答はテキストブロックのみ抽出する（画像等のリソースは未対応）。

---

## 永続メモリー（スレッドをまたぐ記憶）

ClaudeCode のメモリー機能相当。会話（スレッド）が変わっても引き継ぎたい事実を、
`data/memory/`（`config.ini` の `[paths] memory_dir` で変更可）配下に
YAML frontmatter 付き Markdown ファイルとして保存する。ロジックは `src/memory.py`
に集約し、`src/tools.py` の6ツール（`create_memory`/`update_memory`/`delete_memory`/
`read_memory`/`search_memory`/`list_memories`）が薄いラッパーとして公開する。

- **4種類の type**: `user`（ユーザーの役割・選好）／`feedback`（訂正・確認済みの
  アプローチ）／`project`（進行中の作業の背景）／`reference`（外部リソースの所在）。
  それぞれ `data/memory/<type>/` 配下に `<name>.md` として1ファイル保存される。
- **索引 `MEMORY.md`**: 保存されている全メモリーの `name`+`description` 一覧。
  create/update/delete のたびに全再構築される（差分更新はしない設計）。
  この索引はシステムプロンプトの `{{memory}}` に常時差し込まれ、LLM は起動時から
  全メモリーの一覧を把握できる（本文は `read_memory`/`search_memory` で都度取得）。
- 200行を超える場合はシステムプロンプトへの差込時に切り詰められる（`src/memory.py`
  の `render_memory_block`）。
- `dispatch_agent` のサブエージェントにはメモリー系ツールを渡さない（主エージェント
  専用）。

保存・削除の判断基準（何を保存すべきで何を保存すべきでないか）は
`system_prompt/system_prompt.md` の「Memory System」セクションに記載している。

---

## プロンプト資産の自動チューニングループ（evals/）

`system_prompt.md`・`SKILL.md`・ツール docstring などの「LLM に渡すプロンプト資産」を、
実際のローカル LLM（llama.cpp server）を動かして自動評価し、失敗があれば修正して
再評価する仕組み。`.claude/skills/tune-prompt/SKILL.md` の手順書に沿って
Claude Code から `/tune-prompt system_prompt` のように実行する。

- 評価ケースは `evals/cases/<target>/*.yaml`（現状 `system_prompt` に1件、`system_prompt_scale` に1件）。
  ルールベース判定（`expect`: 特定ツールの呼び出し有無・応答文字列の含有等）と、
  自由記述の `judge`（transcript を読んで合否判断させる）を併用できる。
- `python evals/run_all.py system_prompt` で全ケースを直列実行し、結果は
  `evals/results/<target>/<timestamp>/`（`.gitignore` 対象、再生成可能なデータ）へ出力。
- `evals/run_case.py` は Chainlit の UI 呼び出しを `evals/headless_chainlit.py` で
  スタブに差し替え、`src/graph.py` のグラフを直接 `ainvoke` する（Chainlit サーバー起動不要）。
- チューニング時は編集前スナップショットを `evals/history/<target>/` に退避し、
  変更内容と理由を `evals/tuning_log.md` に追記する。git へのコミットは行わない。
- 同じ仕組みを流用し、`config.ini` の timeout系設定（`request_timeout_seconds`
  等）を実行環境のスペックに応じて実測チューニングする `config_timeouts`
  ターゲットもある（`.claude/skills/tune-config-timeouts/SKILL.md`）。

詳細は [`evals/README.md`](evals/README.md) を参照。

---

## 設定リファレンス（config.ini）

| セクション | キー | 意味 | 対応する環境変数 |
|-----------|------|------|------------------|
| `[llm]` | `base_url` | llama-server の OpenAI 互換 URL | `LLM_BASE_URL` |
| `[llm]` | `api_key` | ダミー値（llama.cpp は認証不要） | `LLM_API_KEY` |
| `[llm]` | `model` | モデル名（`--alias` と一致させる） | `LLM_MODEL` |
| `[llm]` | `temperature` | 生成のばらつき | `LLM_TEMPERATURE` |
| `[llm]` | `top_p` | 累積確率上位のみサンプリング（空欄で未指定） | `LLM_TOP_P` |
| `[llm]` | `top_k` | 上位k候補のみサンプリング（llama.cpp拡張、空欄で未指定） | `LLM_TOP_K` |
| `[llm]` | `repeat_penalty` | 直近トークンの再出現抑制（llama.cpp拡張、空欄で未指定）。thinking内の同一文言ループ抑制に有効 | `LLM_REPEAT_PENALTY` |
| `[llm]` | `frequency_penalty` | 出現済みトークン全体への一律ペナルティ（空欄で未指定） | `LLM_FREQUENCY_PENALTY` |
| `[llm]` | `presence_penalty` | 一度でも出現したトークンへの一律ペナルティ（空欄で未指定） | `LLM_PRESENCE_PENALTY` |
| `[llm]` | `max_tokens` | 1リクエストあたりの最大生成トークン数（空欄で無制限） | `LLM_MAX_TOKENS` |
| `[llm]` | `dry_multiplier` | DRYサンプラーの強度（llama.cpp拡張、空欄で無効）。フレーズ単位の反復に効く | `LLM_DRY_MULTIPLIER` |
| `[llm]` | `dry_base` | DRYサンプラーの反復長に対するペナルティ指数増加率（空欄で未指定） | `LLM_DRY_BASE` |
| `[llm]` | `dry_allowed_length` | DRYサンプラーがこの文字数以下の反復を許容する閾値（空欄で未指定） | `LLM_DRY_ALLOWED_LENGTH` |
| `[llm]` | `dry_penalty_last_n` | DRYサンプラーが反復検出に遡って見るトークン数（空欄で未指定） | `LLM_DRY_PENALTY_LAST_N` |
| `[llm]` | `dry_sequence_breakers` | DRYサンプラーの反復検出リセット区切り文字（カンマ区切り、空欄で既定値） | `LLM_DRY_SEQUENCE_BREAKERS` |
| `[llm]` | `track_token_usage` | トークン使用量の取得を有効にする（Chainlit UI表示・eval結果に反映） | `LLM_TRACK_TOKEN_USAGE` |
| `[llm]` | `request_timeout_seconds` | LLMサーバーへの応答待ちタイムアウト秒数（read/write/pool） | `LLM_REQUEST_TIMEOUT_SECONDS` |
| `[llm]` | `stream_chunk_timeout_seconds` | ストリーミング中にチャンクが届かない場合のタイムアウト秒数 | `LLM_STREAM_CHUNK_TIMEOUT_SECONDS` |
| `[paths]` | `skills_dir` | スキルフォルダ | `SKILLS_DIR` |
| `[paths]` | `agents_dir` | エージェント種別定義フォルダ（`dispatch_agent` の `agent_type`） | `AGENTS_DIR` |
| `[paths]` | `project_locohane_dir` | プロジェクト固有の拡張ディレクトリ（ClaudeCode の `.claude/` 相当）。配下の `skills/`（`skills_dir` にマージ走査、同名は優先）・`agents/`（`agents_dir` にマージ走査、同名は優先）・`LOCOHANE.md`（プロジェクト固有指示、存在しなくてもエラーにならない）を自動検知する。`nudge_messages` と同じリスト形式で複数ディレクトリ指定可 | `PROJECT_LOCOHANE_DIR` |
| `[paths]` | `system_prompt_path` | メインエージェント用システムプロンプトのテンプレート | `SYSTEM_PROMPT_PATH` |
| `[paths]` | `checkpoint_db` | 会話状態 SQLite | `CHECKPOINT_DB` |
| `[paths]` | `upload_dir` | アップロード保存先 | `UPLOAD_DIR` |
| `[paths]` | `log_dir` | ログ出力先 | `LOG_DIR` |
| `[paths]` | `log_level` | ログの詳細度。`info`（現行仕様、ツール呼び出しの概要のみ）／`debug`（ツール呼び出しの全引数・全結果・LLM応答本文・thinkingまで記録）／`none`（ログを一切生成しない） | `LOG_LEVEL` |
| `[paths]` | `log_clear_on_startup` | 起動のたびに `app.log` を空にしてから書き始めるか（`false`＝従来通り追記） | `LOG_CLEAR_ON_STARTUP` |
| `[paths]` | `default_workdir` | エージェントの既定の作業ディレクトリ（`run_script` の cwd 等） | `DEFAULT_WORKDIR` |
| `[paths]` | `memory_dir` | 永続メモリーの保存先ルート | `MEMORY_DIR` |
| `[paths]` | `help_path` | `help` ツールが返すヘルプ本文Markdownのパス | `HELP_PATH` |
| `[uploads]` | `retention_days` | アップロードファイルの保持日数（0以下で自動削除無効） | `UPLOAD_RETENTION_DAYS` |
| `[uploads]` | `cleanup_interval_hours` | 自動削除チェックの実行間隔（時間） | `UPLOAD_CLEANUP_INTERVAL_HOURS` |
| `[scripts]` | `timeout` | `run_script`/`execute_python_code` 共通のタイムアウト秒 | `SCRIPT_TIMEOUT` |
| `[scripts]` | `python` | `.py` 実行に使う Python | `SCRIPT_PYTHON` |
| `[scripts]` | `code_execution_enabled` | `execute_python_code` ツール自体の有効/無効 | `CODE_EXECUTION_ENABLED` |
| `[scripts]` | `background_max_runtime_seconds` | `run_script_background` のジョブを強制終了するまでの上限秒 | `SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS` |
| `[scripts]` | `background_job_retention_seconds` | `run_script_background` の完了済みジョブが `check_script_job` で未回収のまま残ってよい秒数 | `SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS` |
| `[file_tools_duplicate_guard]` | `enabled` | Read/Glob/Grep/json_query ツールの同一引数繰り返し呼び出しを防止するガードの有効/無効 | `FILE_TOOLS_DUPLICATE_GUARD_ENABLED` |
| `[file_tools_duplicate_guard]` | `max_calls` | 同一シグネチャの呼び出しを許可する回数（既定1回） | `FILE_TOOLS_DUPLICATE_GUARD_MAX_CALLS` |
| `[file_tools_duplicate_guard]` | `carry_over_to_main` | サブエージェント内の呼び出し履歴をメイン判定へ持ち越すかどうか | `FILE_TOOLS_DUPLICATE_GUARD_CARRY_OVER` |
| `[graph]` | `implementation` | ReAct ループの実装（`handwritten` または `prebuilt`） | `GRAPH_IMPL` |
| `[graph]` | `recursion_limit` | メインReActループ（agent→tools遷移）の最大反復回数。超過時は打ち切りメッセージを表示 | `GRAPH_RECURSION_LIMIT` |
| `[graph]` | `max_parallel` | メインエージェントのツール呼び出し（ImageAwareToolNode）の同時実行数上限。1以上でSemaphore(N)ガード、0以下でガード無効化 | `GRAPH_TOOL_MAX_PARALLEL` |
| `[subagent]` | `max_iterations` | `dispatch_agent` の内部ReActループの最大反復回数 | `SUBAGENT_MAX_ITERATIONS` |
| `[subagent]` | `max_parallel` | `dispatch_agent` の実LLM呼び出しの同時実行数上限。1以上でSemaphore(N)ガード、0以下でガード無効化 | `SUBAGENT_MAX_PARALLEL` |
| `[subagent]` | `token_guard_enabled` | サブエージェントのトークン使用量ガードの有効/無効 | `SUBAGENT_TOKEN_GUARD_ENABLED` |
| `[subagent]` | `token_guard_soft_threshold` | ソフト警告（注意メッセージ注入）のトークン閾値 | `SUBAGENT_TOKEN_GUARD_SOFT_THRESHOLD` |
| `[subagent]` | `token_guard_hard_threshold` | ハード打ち切りのトークン閾値 | `SUBAGENT_TOKEN_GUARD_HARD_THRESHOLD` |
| `[subagent]` | `empty_response_max_retries` | 空応答の再試行回数 | `SUBAGENT_EMPTY_RESPONSE_MAX_RETRIES` |
| `[timeouts]` | `approval_seconds` | `approve_plan`／`run_script`・`execute_python_code`の個別実行確認でユーザー応答を待つ秒数。`0`で無期限待ち | `APPROVAL_TIMEOUT_SECONDS` |
| `[timeouts]` | `ask_user_question_seconds` | `AskUserQuestion`（自由記述質問。`labels`省略時は単一入力、指定時は複数項目フォーム）でユーザー応答を待つ秒数。`0`で無期限待ち | `ASK_USER_QUESTION_TIMEOUT_SECONDS` |
| `[timeouts]` | `ask_user_choice_seconds` | `ask_user_choice`（選択肢質問）でユーザー応答を待つ秒数。`0`で無期限待ち | `ASK_USER_CHOICE_TIMEOUT_SECONDS` |
| `[plan]` | `allow_badge_unlock` | Plan Mode バッジの双方向切り替えを許可するか | `PLAN_ALLOW_BADGE_UNLOCK` |
| `[default_workdir]` | `retention_days` | default_workdir 配下のファイル保持日数（0以下で自動削除無効） | `DEFAULT_WORKDIR_RETENTION_DAYS` |
| `[default_workdir]` | `cleanup_interval_hours` | default_workdir 自動削除チェック間隔（時間） | `DEFAULT_WORKDIR_CLEANUP_INTERVAL_HOURS` |
| `[log]` | `max_lines` | app_*.log の最大行数（超過時にローテーション） | `LOG_MAX_LINES` |
| `[log]` | `retention_days` | ローテーション済み app_*.log の保持日数 | `LOG_RETENTION_DAYS` |
| `[log]` | `cleanup_interval_hours` | app_*.log 自動削除チェック間隔（時間） | `LOG_CLEANUP_INTERVAL_HOURS` |
| `[thinking_loop_guard]` | `enabled` | ループ検知＆ループガード＆リトライ機能の有効/無効 | `THINKING_LOOP_GUARD_ENABLED` |
| `[thinking_loop_guard]` | `window_chars` | ループ判定対象とする直近テキストのウィンドウ文字数 | `THINKING_LOOP_GUARD_WINDOW_CHARS` |
| `[thinking_loop_guard]` | `check_interval_chars` | 何文字増えるごとに再チェックするか | `THINKING_LOOP_GUARD_CHECK_INTERVAL_CHARS` |
| `[thinking_loop_guard]` | `confirm_count` | ループ確定と判定するまでの連続成立回数（誤検知防止） | `THINKING_LOOP_GUARD_CONFIRM_COUNT` |
| `[thinking_loop_guard]` | `max_history_chars` | 反復検知で比較対象とする過去履歴の上限文字数 | `THINKING_LOOP_GUARD_MAX_HISTORY_CHARS` |
| `[thinking_loop_guard]` | `match_ratio_threshold` | 直近ウィンドウとの最長一致率がこの値を超えたらループ候補とみなす閾値 | `THINKING_LOOP_GUARD_MATCH_RATIO_THRESHOLD` |
| `[thinking_loop_guard]` | `max_retries` | ループ検知後、注意メッセージを注入して再試行する最大回数 | `THINKING_LOOP_GUARD_MAX_RETRIES` |
| `[thinking_loop_guard]` | `nudge_messages` | ループ検知後に注入する注意メッセージ（複数指定可） | `THINKING_LOOP_GUARD_NUDGE_MESSAGES` |
| `[context_trim]` | `enabled` | 古い `ToolMessage` を切り詰めてプリフィル遅延を抑える機能の有効/無効 | `CONTEXT_TRIM_ENABLED` |
| `[context_trim]` | `keep_recent_tool_messages` | 全文保持する直近 `ToolMessage` の件数 | `CONTEXT_TRIM_KEEP_RECENT_TOOL_MESSAGES` |
| `[context_trim]` | `truncated_max_chars` | 切り詰め対象 `ToolMessage` の残す最大文字数 | `CONTEXT_TRIM_TRUNCATED_MAX_CHARS` |
| `[context_compaction]` | `enabled` | 会話履歴の自動要約・圧縮機能（ClaudeCodeのcompact相当）の有効/無効 | `CONTEXT_COMPACTION_ENABLED` |
| `[context_compaction]` | `token_threshold` | 直近1回のLLM呼び出しの合計トークン数がこの値を超えたら圧縮を検討する閾値 | `CONTEXT_COMPACTION_TOKEN_THRESHOLD` |
| `[context_compaction]` | `keep_recent_turns` | 圧縮時に丸ごと保持する直近のユーザーターン数 | `CONTEXT_COMPACTION_KEEP_RECENT_TURNS` |
| `[context_compaction]` | `min_messages_to_compact` | 会話全体のメッセージ数がこの件数未満なら圧縮しない安全弁 | `CONTEXT_COMPACTION_MIN_MESSAGES_TO_COMPACT` |
| `[context_compaction]` | `compaction_prompt_path` | 要約を指示するプロンプト本文（Markdown）のパス | `CONTEXT_COMPACTION_PROMPT_PATH` |
| `[path_memory]` | `dir` | パスメモリー機能のレジストリファイル（`<thread_id>.json`）保存先 | `PATH_MEMORY_DIR` |
| `[path_memory]` | `retention_days` | パスメモリーのレジストリファイル保持日数 | `PATH_MEMORY_RETENTION_DAYS` |
| `[path_memory]` | `cleanup_interval_hours` | パスメモリーの自動削除チェック間隔（時間） | `PATH_MEMORY_CLEANUP_INTERVAL_HOURS` |
| `[path_memory]` | `max_entries` | 1会話あたりのパスメモリー登録上限件数 | `PATH_MEMORY_MAX_ENTRIES` |
| `[auth]` | `enabled` | ログイン認証機能のON/OFF（`false`＝現状通りログイン不要） | `AUTH_ENABLED` |
| `[auth]` | `require_password` | 認証ON時、パスワード一致を必須にするか（`false`＝ユーザー名のみで通す） | `AUTH_REQUIRE_PASSWORD` |
| `[chat_log]` | `enabled` | 会話ログ（ユーザー発言・AI最終応答）のテキストファイル記録の有効/無効 | `CHAT_LOG_ENABLED` |
| `[chat_log]` | `dir` | 会話ログの保存先ルートディレクトリ | `CHAT_LOG_DIR` |
| `[checkpointer]` | `op_timeout_seconds` | LangGraphの会話状態SQLite（`[paths].checkpoint_db`）に対する1回あたりの操作（aget_tuple/aput_writes等）タイムアウト秒数。超過時はcheckpointer再構築へフォールバック | `CHECKPOINTER_OP_TIMEOUT_SECONDS` |
| `[checkpointer]` | `close_timeout_seconds` | checkpointer再構築時に旧DB接続をクローズする際のタイムアウト秒数 | `CHECKPOINTER_CLOSE_TIMEOUT_SECONDS` |
| `[checkpointer]` | `shutdown_drain_timeout_seconds` | アプリシャットダウン時、保留中の非同期タスクの完了を待つ最大秒数 | `CHECKPOINTER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` |
| `[mcp]` | `enabled` | MCPサーバー自動接続機能の有効/無効（既定値。`.locohane/settings.json` の `"mcp"."enabled"` があればそちらが優先） | `MCP_ENABLED` |
| `[mcp]` | `settings_path` | `.locohane/settings.json` のパス | `MCP_SETTINGS_PATH` |
| `[mcp]` | `connect_timeout_seconds` | 1サーバーあたりの起動（プロセス起動+initialize+tools/list）のタイムアウト秒数 | `MCP_CONNECT_TIMEOUT_SECONDS` |
| `[mcp]` | `call_timeout_seconds` | MCPツール（tools/call）1回あたりのタイムアウト秒数 | `MCP_CALL_TIMEOUT_SECONDS` |

環境変数が設定されていれば `config.ini` の値より優先される（詳細は `src/config.py` を参照）。

## ログイン認証の設定

`[auth] enabled = true` にするとログイン画面が表示され、ユーザー名/パスワードでの
ログインが必須になる。ユーザー名・パスワードの実体、および JWT 署名鍵は
**機密情報のため config.ini には置かず、`.env`（環境変数）で管理する**。

1. `chainlit create-secret` を実行し、出力された値を `.env` の
   `CHAINLIT_AUTH_SECRET` に設定する（`[auth] enabled = true` の場合は必須。
   未設定だと起動時にエラーで止まる）。
2. `.env` の `AUTH_USERS` にログイン可能なユーザー名/パスワードの組を
   `[["ユーザー名","パスワード"], ...]` の Python リスト形式で登録する
   （書式・記入例は `.env.example` を参照）。複数行に分けて見やすく書きたい
   場合は、値全体をシングルクォート(`'`)で囲むこと（python-dotenv の仕様上、
   クォート無しの値は複数行にできない）。例:
   ```
   AUTH_USERS='[
       ["alice", "password1"],
       ["bob", "password2"]
   ]'
   ```
3. `config.ini` の `[auth]` で `enabled` / `require_password` を設定する。
   `require_password = false` の場合、`AUTH_USERS` に登録済みのユーザー名で
   あればパスワードの内容は問われない。
4. 独自フロントエンド（`frontend/`）にログインフォームを実装しているため、
   フロントエンドを変更した場合は `cd frontend && npm run build` で
   再ビルドし `public/build` へ反映する。

**`.env` は絶対にコミットしないこと**（`.gitignore` 済みだが、値の取り扱いに注意）。

## 会話ログの記録

`[chat_log] enabled = true` にすると、ユーザー発言とAIの最終応答（ツール実行の
詳細・thinkingは含まない）を、データベースを使わずテキストファイルへ記録する。

- 保存先: `[chat_log].dir`（既定 `./data/logs_chat`）配下に
  `<ユーザー名>/<日時>_<thread_id>.log`（日時は秒単位まで含む）の構成で書き出す。
- ユーザー名はログイン中のユーザー識別子（`[auth]` 参照）。`[auth] enabled = false`
  で未ログインの場合は `anonymous` にまとめる。
- 1つのチャットセッション（タブ）につき1ファイルへ追記し続ける（日をまたいでも
  ファイルは変わらない）。

---

## ライセンスと商用利用

### 本プロジェクトのライセンス

本ソフトウェアは **MIT License** です。詳細は [LICENSE](LICENSE) を参照。

### 依存パッケージ（商用利用可）

`requirements.txt`（pip）の直接依存から到達する実行時の推移的依存、全 160 パッケージの
ライセンス内訳:

| ライセンス種別 | 数 | 商用利用 |
|---|---:|---|
| Apache 2.0 | 74 | ✅ |
| MIT | 55 | ✅ |
| BSD | 26 | ✅ |
| PSF | 3 | ✅ |
| MPL 2.0 | 2 | ✅（改変せず利用する限り） |

**GPL / AGPL / LGPL は含まれません。** すべて寛容ライセンス、またはファイル単位の
弱いコピーレフト（MPL 2.0）であり、改変せず依存として利用する限り本ソフトウェアへの
組み込み・商用配布が可能です。全パッケージの完全な一覧と帰属表示は
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) に記載しています（依存追加時は
`tools/gen_licenses.py` で再生成）。

上記は pip 依存のみが対象です。`frontend/` の npm 依存（react、recoil、
`@chainlit/react-client`、vite 等、いずれも MIT / Apache 2.0）は別途手動確認済みで
GPL / AGPL / LGPL 系は含まれませんが、`tools/gen_licenses.py` の自動集計・
THIRD_PARTY_LICENSES.md には含まれていません。npm 依存を追加・更新した場合は
`frontend/package-lock.json` のライセンスも別途確認してください。

なお `frontend/public/favicon.svg`（`public/build/favicon.svg` に配布）は
[Chainlit](https://github.com/Chainlit/chainlit)（Apache 2.0）が同梱するデフォルトの
favicon をそのまま使用しています。

### OfficeCLI（外部バイナリツール、任意導入）

上記の pip / npm 依存とは別に、`.officecli/` に導入する
[OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) は **Apache License 2.0** の
外部OSSで、本プロジェクトのソースコードには組み込まれていません（`.gitignore`
対象、詳細は上記「OfficeCLI の導入（任意）」参照）。単一バイナリ内に
`DocumentFormat.OpenXml`・`System.CommandLine`・`.NET Runtime`（いずれもMIT
License）を同梱しており、帰属表示は `.officecli/THIRD-PARTY-NOTICES.txt` に
含まれています。GPL / AGPL / LGPL は含まれず、商用利用可能です。詳細は
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) の「pip依存以外の同梱物（手動記載）」
セクションを参照。

### 外部通信について（完全オフライン保証）

本スタックの外部送信はすべて **opt-in**（API キー設定時のみ）で、既定では発生しません。
`app.py` 冒頭で以下を明示的に無効化しています:

- Chainlit の Literal AI データ層（`LITERAL_API_KEY` 未設定なら送信なし）
- LangChain の LangSmith トレーシング（`LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` を false 化）

唯一の外部通信は `config.ini` の `base_url`（既定 `http://localhost:8080` = ローカルの
llama-server）への LLM 呼び出しのみです。

MCPサーバー機能（`.locohane/settings.json`、上記「MCPサーバー接続」参照）を有効化した
場合、そこに定義したコマンド（例: `npx` 経由のサードパーティ製MCPサーバー）が
ローカルサブプロセスとして起動されます。既定状態（`.locohane/settings.json` が
空の `mcpServers` のみ、または `[mcp] enabled = false`）では何も起動されず、
オフライン保証は変わりません。ただし、ユーザー自身が追加したMCPサーバーが内部で
ネットワーク通信を行うかどうかは各MCPサーバーの実装次第であり、本プロジェクトの
管理範囲外です。

`.officecli/` に [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) を導入した場合、
`officecli` バイナリ自身が**既定でバックグラウンド更新チェックの外部通信を行います**
（本プロジェクトのコードが行うものではありません）。完全オフライン運用したい場合は、
`officecli config autoUpdate false` で恒久的に無効化するか、実行のたびに環境変数
`OFFICECLI_SKIP_UPDATE=1` を設定してください。導入しない場合はこの通信も発生しません。

`web-search` スキル（Tavily APIによるWeb検索）は、スキル専用の
`skills/web-search/scripts/.env` に `TAVILY_API_KEY` を設定した場合のみ、
ユーザーがこのスキルを実行した時に限り `https://api.tavily.com` へ通信します。
未設定（`.env` 自体が無い、または空）の場合は一切通信せず、設定手順を示す
エラーメッセージを返すだけです。

### 商用利用時のチェックリスト

- [ ] 使用する **GGUF モデルのライセンス** を確認する（→ 上記「llama-server 起動例」の注記）
- [ ] 依存を追加・更新したら `tools/gen_licenses.py` で告知ファイルを再生成する
- [ ] `.officecli/` を導入した場合、完全オフライン運用が必要なら
      `officecli config autoUpdate false` で自動更新チェックを無効化する

---

## 免責事項

本ソフトウェアは「現状のまま」（as-is）で提供され、明示的または黙示的な保証はありません。
著作者または著作権者は、本ソフトウェアの使用またはそれ以外の行為について、本ソフトウェアに
起因するかぎり一切の責任を負いません。いかなる状況下においても、本ソフトウェアの使用により
直接・間接的に生じたいかなる損害（データ消失、システム障害、ビジネス損失、機会損失を含む）
について、著作者または著作権者は責任を負いません。

上記は本ソフトウェアに適用される [LICENSE](LICENSE)（MIT License）の無保証・免責条項の
要約です。詳細な法的文言は LICENSE 本文が優先します。
