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

![Locohane](public/settings/icon.png)

名前の由来：**Lo**cal（ローカル環境）+ 小羽（**cohane** / 軽量さ・和名っぽさ）

## 中核使命

**このプロジェクトは、高性能な大規模パラメータモデルではなく、低パラメータモデルでも
安定して Agent がタスクをこなせるようにすることを目指す。** 特に低パラメータモデルで
起こりやすい、思考ループ・パス生成不具合・途中停止（無言のまま応答を終える等）の制御に
全力を尽くす。この中核使命を支える技術は次の7点:

- **パスメモリー機能**（`src/path_memory.py`、`system_prompt.md` の
  Tool Usage Guidelines）: `Glob`/`Grep`/`Read` の結果に短い参照番号 `@N`
  を自動付与し、LLM が長い絶対パスを自分で組み立て直して失敗を繰り返すことを防ぐ。
- **ループ検知・ガード・リトライ**（`config.ini` の `[thinking_loop_guard]`、
  `src/llm/chat_model.py` の `ChatLlamaCpp`、`src/llm/loop_guard.py` の
  `ThinkingLoopDetected`）: 応答が反復ループに
  陥ったことを検知してナッジメッセージを注入・自動リトライする。上限を超えても
  フリーズしないよう、`app.py` 側で打ち切りメッセージを出して終了する。
- **glob 機能の拡張**（`src/tools/glob_tool.py` の `Glob`、共通ロジックは
  `src/tools/_file_tools_common.py`）:
  通常の glob 結果に加え、一致件数・ディレクトリ一覧・パスメモリー参照を返し、
  LLM が探索範囲を自己修正しやすくする。
- **read-only 権限境界と explore サブエージェントへの強制委譲**（`src/tools/registry.py`
  の `_SUBAGENT_TOOLS`、`agents/explore.md`、`system_prompt.md` の
  Task Delegation）: 探索作業を読み取り専用のサブエージェントへ切り出し、
  リクエスト1回あたりのトークン使用量を分散させて安定化する。加えて
  `config.ini` の `[main_agent_tool_guard]` が、メインエージェント自身による
  `Read`/`Grep`/`analyze_image` 等トークン消費の大きい重量系ツール・スキル
  スクリプトの直接呼び出しをホワイトリストで制限し、委譲を事実上強制する。
- **メインエージェントのトークン量上限設計**（`agents/worker.md`、
  `src/main_token_guard.py`、`src/context_trim.py`、`config.ini` の
  `[graph] token_guard_*`）: メインエージェントは司令塔であり、不安定化すると
  タスク全体が破綻するため「1リクエストあたりのトークン数を目安64,000未満に
  保つ」ことを設計原則としている。大量ファイル処理でサブエージェントの読み取り
  結果をメインへ全文返させると、トークン量が単調増加し無応答のまま停止する
  （実測: 297枚の画像処理で1リクエストあたり24,833→128,000トークンまで増加し
  停止した事例あり）。対策として、読み取りから書き出しまでを内部で完結させ
  メインへは処理件数・失敗分のみ返す書き込み可能サブエージェント `worker` を
  用意している。`worker`が正常終了する前提の対策のため、途中でトークン上限に
  達して打ち切られた場合の保険として `write_scratch_note` があり、サブエー
  ジェントが調査の節目ごとに分かった内容をスクラッチファイルへ追記しておく
  ことで、打ち切り時も委譲元へファイルのパスだけ引き継げば続きを判断できる。
  加えて `context_trim` による切り詰めと `main_token_guard` による引継ぎ
  プロンプト自動生成で多重に補強している。
- **bash/npm 相当のツールを持たせない設計**（`src/tools/` の `run_script`
  系ツール、`create_plan`/`approve_plan`、`_GUARD_BLOCKED_CMDS`）: 低パラメータ
  モデルに任意コマンド実行を許すと暴走・誤操作のリスクが高いため、汎用シェル・
  bash・npm に相当するツールは一切公開しない。コマンド実行はスキル配下に
  事前定義された `run_script`（ホワイトリスト方式）のみに限定し、書き込み系
  ツールはすべて `create_plan`→`approve_plan` によるユーザー承認を得るまで
  実行をブロックする。任意コード実行が可能な `execute_python_code` でも
  git/npm/pip の起動とプロジェクト外への書き込みをガードで遮断し、最低限の
  破壊的操作を防いでいる。
- **文書生成スキルの「ops JSON DSL」設計**（`skills/pptx-create`・`skills/excel-edit`・
  `skills/docx-create` の `scripts/_*.py`）: 上記「bash/npm相当のツールを持たせない
  設計」を xlsx/pptx/docx 生成にも一貫して適用したもの。`python-pptx`/`openpyxl`/
  `python-docx` の生コードを LLM に書かせるのではなく、有限個の `op`（`set_range`/
  `add_chart`/`format_table` 等）と JSON スキーマへ閉じ込めることで、低パラメータ
  モデルでも構文エラーやファイル破損なく確実に動く操作だけに絞っている（Anthropic
  公式の `skills/pptx`・`skills/xlsx` 等は逆に、Claude自身の事前知識に頼って
  `python-pptx`/`openpyxl` の生コードを直接書かせる設計で、SKILL.md はライブラリ
  API そのものではなく「落とし穴」だけを補足する差分ドキュメントになっている）。
  見た目の一貫性のため、`pptx-create`/`excel-edit`/`docx-create`/`pdf-tools` は
  同じ8色の配色テーマ（`charcoal`/`navy` 等、Anthropic公式pptxスキルのDesign
  Ideas準拠）を共有している。`pdf-tools`（`create_pdf.py`）だけは例外的に
  セマンティックHTML（自前パーサーで`reportlab`のFlowableへ変換、JS実行なし）を LLM に直接書かせている —
  HTML は xlsx/pptx/docx の XMLベース形式と違い多少崩れても致命的にファイルが
  壊れないため、この原則の範囲内で安全に表現力を確保できる。

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
       │ ChatOpenAI              │   │ src/tools/ (39ツール)               │
       │  → llama-server /v1     │   │  read_skill / read_skill_file /    │
       │  (OpenAI 互換)          │   │  run_script / execute_python_code /│
       └─────────────────────────┘   │  get_tool_source / check_work_dir_status /│
                                      │  Read / Glob / Grep / json_query / │
                                      │  list_path_memory / analyze_image /│
                                      │  dispatch_agent / create_plan /    │
                                      │  approve_plan / update_task_progress/│
                                      │  get_plan_status / lock_plan_mode /│
                                      │  AskUserQuestion / ask_user_choice /│
                                      │  provide_download /                │
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
| 2. Read | LLM がスキルを選び、`read_skill` で本文全体を読む | `src/tools/read_skill.py` |
| 3. Execute | 本文の指示に従い `read_skill_file`/`run_script` で必要時のみ読む・実行 | `src/tools/read_skill_file.py`/`src/tools/run_script.py` |

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

1. 各ツールは `src/tools/` パッケージ配下（ツール1つにつき原則1ファイル）で LangChain の
   `@tool` デコレータ付き関数として定義する。関数の docstring が `description`、
   型ヒント付き引数が JSON Schema の `parameters` に自動変換される
   （`langchain_core.tools.tool` の標準機能。本プロジェクト側に独自の変換コードはない）。
2. `get_all_tools()`（`src/tools/registry.py`）がビルトインツールと MCP 由来の動的ツールを
   合流させたリストを返す。
3. `src/graph.py` の `build_model(config).bind_tools(get_all_tools())`（サブエージェント側は
   `src/subagent.py`）が、このリストを `ChatOpenAI` 系クラス（`src/llm/chat_model.py` の
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

上記3段階に加えて、スキルの読み込みとは独立したツールが以下の39個ある（いずれも `src/tools/` パッケージ配下、ツール1つにつき原則1ファイル）。

| ツール | 役割 |
|--------|------|
| `read_skill` | スキルの SKILL.md 本文全体を読み込む（progressive disclosure 第2段階） |
| `read_skill_file` | skills ディレクトリ配下のファイルを読み込む（references/assets 等。progressive disclosure 第3段階） |
| `run_script` | スキルの scripts/ 配下のスクリプトを実行する（要承認。`config.ini` で承認不要に切替可）。完了までブロックするため、タイムアウトに近い長時間実行が見込まれる場合は `run_script_background` を使う |
| `run_script_background` | `run_script` と同じスクリプトを起動する（要承認は同様）。完了までの間、進捗（経過秒数・途中出力）をチャットへ直接通知しながら待つため、LLM自身がポーリングする必要は無い。設定した安全上限（`[scripts].background_inline_wait_max_seconds`）を超えてもなお完了しない場合のみ `job_id` を返してターンを終える |
| `check_script_job` / `stop_script_job` | 上記の安全上限超過フォールバック時のみ使う、ジョブの状況確認・強制終了。実行中ジョブへの連続 `check_script_job` 呼び出しは `[scripts].background_min_poll_interval_seconds` 未満の間隔だとサーバー側で拒否される |
| `execute_python_code` | LLMが生成したPythonコードをその場で実行（要承認。`config.ini` で無効化可）。完了までブロックするため、タイムアウトに近い長時間実行が見込まれる場合は `execute_python_code_background` を使う。code内で `@N`（パスメモリ）を参照する場合は `AGENT_SRC_DIR` 環境変数経由で `path_memory.resolve()` を呼んで実パスへ展開する必要がある。実行前ガードにより `src/`・`app.py`・`config.ini`・`skills/` 等プロジェクトフォルダ配下への書き込み・削除・改名はブロックされる。書き込み先は、ユーザーが作業フォルダ（後述「作業ディレクトリの切り替え」参照）を指定していればそのフォルダ、未指定なら `default_workdir` 配下でも自セッション専用の一時領域 `_tmp_<name>` に限られる（`default_workdir` 直下への書き込みは、別セッションの誤動作を防ぐため許可しない） |
| `execute_python_code_background` | `execute_python_code` と同じコードを起動する（要承認・`config.ini` での無効化・パスメモリ展開・プロジェクトフォルダ保護ガードは同様）。`run_script_background` と同じく完了まで進捗通知しながら待ち、状況確認・停止（安全上限超過時のみ）は共通の `check_script_job`/`stop_script_job` を使う |
| `execute_python_code_readonly` | `execute_python_code` の読み取り専用版（ファイル書き込み不可、承認不要）。`explore`/`planner` 等の読み取り専用サブエージェント種別（`agents/*.md` の `tools:`）にのみ付与され、メインエージェントは持たない |
| `write_scratch_note` | 調査中に分かった内容をスクラッチファイルへ追記する。計画未承認でも常に呼べ、書き込み先はツール自身が決めるため任意パスには書けない。トークン上限打ち切り時の引き継ぎ用途 |
| `write_thread_note` / `list_thread_notes` / `read_thread_note` | 同一スレッド内に閉じたスレッド共有ノート（後述「スレッド共有ノートとの違い」参照）。委譲元・委譲先間で調査結果の詳細を引き継ぐための、`write_scratch_note`とは別系統のメモ機構。主エージェント・全サブエージェントが読み書き可能 |
| `get_tool_source` | `run_script` がエラーになった際、原因調査用にスクリプトの絶対パスを返す（中身は返さない） |
| `check_work_dir_status` | 現在の作業ディレクトリの実際のアクセス状況を確認する |
| `Read` / `Glob` / `Grep` | ローカルファイルシステム上の任意の絶対パスに対する読込・ファイル名検索・全文検索（ClaudeCode の同名ツールに合わせた名前。読み取り専用のため計画未承認でも常に呼べる。ロジックは `src/tools/read_tool.py`/`glob_tool.py`/`grep_tool.py`、共通処理は `_file_tools_common.py`） |
| `json_query` | JSON/dict に対する JMESPath クエリ（読み取り専用） |
| `list_path_memory` | 現在の会話のパスメモリー（`@N`）登録内容を一覧表示する（読み取り専用） |
| `provide_download` | 既存のファイルをチャット画面にダウンロードボタンとして提示する |
| `analyze_image` | 画像ファイルをLLMへ視覚情報として見せ、LLM自身が内容を解析・説明・判断する（Vision対応モデル向け）。`show_in_chat=True` を指定すると、解析と同時にチャット画面へもプレビュー表示する（「表示して」「見せて」にはこちらを使う。表示だけして中身を見ない、という呼び方はできない）。回答本文（Markdownテーブルのセル等）の中に画像を組み込みたい場合は、ツールを使わず回答テキストへ直接 `![説明](絶対パス)` と書けばよい（送信直前に自動でブラウザから取得可能なURLへ変換される。`app.py` の `_embed_local_images_as_session_urls`） |
| `dispatch_agent` | タスクをサブエージェント（`src/subagent.py`）へ委譲し最終回答のみ受け取る。`agent_type` 引数でサブエージェントの種別を必ず指定する（暗黙の既定値は無い）。種別定義は `agents/*.md`（ClaudeCode の `.claude/agents/*.md` 相当）。`.locohane/agents/*.md` ともマージ走査され、同名は `.locohane/agents` 側が優先される。完了までの間、進捗（経過時間・反復回数）をチャットへ直接通知しながら待つため、LLM自身がポーリングする必要は無い。設定した安全上限（`[subagent].background_inline_wait_max_seconds`）を超えてもなお完了しない場合のみ `job_id` を返してターンを終える |
| `check_dispatch_agent_job` / `stop_dispatch_agent_job` | 上記の安全上限超過フォールバック時のみ使う、ジョブの状況確認・強制終了 |
| `create_plan` / `approve_plan` / `update_task_progress` | 複数ステップの実行計画を作成・承認・進捗更新（承認後は`run_script`の個別確認をスキップ）。各ステップは `content`（内容）と `activeForm`（実行中表示用の現在進行形）を持つ。既定（`config.ini` の `[plan] require_planner_dispatch`）では、同一ターンで `dispatch_agent(agent_type="planner")` が完了していないと `create_plan` はエラーを返してブロックする（記憶・推測だけで steps を作らせず、専用サブエージェントに草案を作らせるため） |
| `get_plan_status` / `lock_plan_mode` | 現在 Plan Mode（書き込み系ツールがブロックされたロック状態）か Edit Automatically（承認済み計画を実行できる状態）かを確認し、後者から前者へユーザー承認なしに手動で戻す |
| `AskUserQuestion` / `ask_user_choice` | 会話継続に必要な追加情報をユーザーへ質問（`AskUserQuestion` は自由記述。`labels` 省略時は単一入力、指定時は複数項目をまとめて提示。`ask_user_choice` は選択肢形式で、表示される選択肢には常に「✏️ その他（自由入力）」「❌ キャンセル」が自動で追加される） |
| `create_memory` / `update_memory` / `delete_memory` / `read_memory` / `search_memory` / `list_memories` | スレッドをまたぐ永続メモリー（`src/memory.py`）の保存・更新・削除・全文読込・検索・一覧。主エージェントは全6ツールを持つ。`dispatch_agent` のサブエージェントには種別ごとに絞って委譲し、`explore` は読み込み系（`read_memory`/`search_memory`/`list_memories`）のみ、`worker` は全6ツール（フルアクセス）を持つ（`agents/*.md` の `tools:` 参照） |
| `help` | ユーザー向けヘルプ本文（`system_prompt/help.md`）をそのまま返す |

`run_script`（`run_script_background` 含む）と `execute_python_code`（`execute_python_code_background`
含む）は書き込み系ツールのため、`create_plan`/`approve_plan` で計画がユーザー承認済み
（`cl.user_session["plan_approved"]` が True）でない限り実行できず、
未承認の場合はエラーを返す（Plan Mode）。承認自体は `cl.AskActionMessage` による
✅承認/🚫拒否ボタンで行い、タイムアウト時は安全側（拒否）に倒す。`execute_python_code`
（`execute_python_code_background` 含む）は `config.ini` の `[scripts] code_execution_enabled`
でツール自体を無効化できる。

上記の39ツール・スキルすべてがメインエージェントから直接呼べるわけではない。
`config.ini` の `[main_agent_tool_guard]`（既定 `mode = all`）が、メインエージェント
自身によるビルトインツール・`run_script` 配下スキルスクリプトの直接呼び出しを
`allow_entries` のホワイトリストで制限する（`dispatch_agent` 配下のサブエージェントは
対象外）。`Read`/`Grep`/`json_query`/`analyze_image`/`execute_python_code` 系や
大半のスキルスクリプトはこのリストに含めておらず、未登録＝呼び出し不可という
形で事実上 `dispatch_agent` への委譲を強制する（トークン消費の大きい重量系ツールの
連打によるトークン上限到達を防ぐ狙い。詳細は `config.ini` 内コメント参照）。

---

## ファイル構成

```
Locohane/
├── config.ini              # 全設定（LLM接続・保存先パス・スクリプト実行・グラフ実装・サブエージェント）
├── requirements.txt        # pip 依存（バージョン固定）
├── app.py                  # Chainlit エントリ
├── app.bat                 # Windows用起動バッチ
├── Locohane.lnk            # 起動用ショートカット
├── chainlit.md             # Chainlit ウェルカム画面
├── CLAUDE.md               # プロジェクト固有の追加指示（Claude Code 形式）
├── QWEN.md                 # プロジェクト固有の追加指示（Qwen Code 形式、内容はCLAUDE.md参照の1行）
├── LICENSE                 # 本プロジェクトのライセンス（MIT）
├── THIRD_PARTY_LICENSES.md # 依存OSSライセンス一覧（tools/gen_licenses.pyで再生成）
├── memo.md                 # 開発者向けメモ
├── issue.md                # 既知の課題メモ（手動管理）
├── issue/                  # monitor-app-log スキルが app_*.log の異常検知時に自動起票するケース別Markdown
├── pytest.ini              # pytest設定
├── .env.example            # 環境変数サンプル
├── .gitignore              # Git除外設定
├── .chainlit/
│   ├── config.toml         # Chainlit設定
│   └── translations/       # 多言語翻訳ファイル
├── .claude/
│   └── skills/             # Claude Code用スキル（開発支援。アプリ実行時には使わない）
│       ├── setup-basic-config/    # 環境依存パス（config.ini等）の対話設定
│       ├── tune-config-timeouts/  # timeout系設定の実測自動チューニング
│       ├── tune-prompt/           # system_prompt.md等のプロンプト資産自動チューニング
│       └── monitor-app-log/       # app_*.log を定期監視し issue/ へ自動起票
├── .qwen/                  # Qwen Code用の `.claude/` 相当ディレクトリ（settings.json・skills/等）
├── .locohane/                # project_locohane_dir（既定）。配下を起動時に自動検知
│   ├── LOCOHANE.md.example  # LOCOHANE.md のサンプル（配置するとプロジェクト固有指示になる）
│   ├── settings.json        # MCPサーバー接続設定
│   ├── skills/              # skills_dir にマージ走査される追加スキル置き場（同名は優先。現状は README.md のみで中身は空）
│   └── agents/              # agents_dir にマージ走査される追加エージェント種別置き場（同名は優先。現状は README.txt のみで中身は空）
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
│   ├── subagent_common.md   # サブエージェント共通プロンプト
│   ├── handoff_prompt.md    # トークン上限接近時にLLMへ注入する打ち切り・引き継ぎ指示
│   └── compressing_test/    # プロンプト圧縮の効果検証用ワークスペース（開発時の実験、tune-prompt関連）
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
│   ├── analyze_investigation_order.py # チューニング検証用の一時的な集計スクリプト
│   ├── tuning_log.md        # チューニング履歴
│   ├── handoff_prompt.md    # tune-prompt再開時の引き継ぎメモ
│   ├── handoff_config_timeouts_skill.md # config_timeoutsターゲット開発時の引き継ぎメモ
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
│   ├── tools/                # LangGraph ツール39種（第2・第3段階＋独立ツール）。ツール1つにつき原則1ファイル
│   │   ├── registry.py       # get_all_tools()・MCPツール登録・_BASE_TOOLS/_SUBAGENT_TOOLS一覧
│   │   ├── tool_node.py      # ImageAwareToolNode・メインエージェント向けツールのフィルタリング
│   │   ├── _state.py         # モジュール共有状態（config値・セマフォ・レジストリ等）の一元管理
│   │   ├── _file_tools_common.py # Read/Glob/Grep の共通ロジック
│   │   └── （read_skill.py/run_script.py/dispatch_agent.py 等、ツールごとに1ファイル）
│   ├── path_memory.py       # パスメモリー（@N）レジストリの読み書き
│   ├── memory.py            # 永続メモリーの読み書き・索引再構築
│   ├── project_instructions.py # .locohane/LOCOHANE.md の読込
│   ├── graph.py             # ReAct ループ（handwritten / prebuilt を切替）
│   ├── llm/                  # ChatOpenAI（llama-server接続）の構築
│   │   ├── chat_model.py     # ChatLlamaCpp・build_model()
│   │   ├── loop_guard.py     # 反復ループ検知（ThinkingLoopDetected）
│   │   ├── routing.py        # 接続先ルーティング（round_robin/random/priority_failover）
│   │   └── diagnostics.py    # 診断用ロギング
│   ├── context_trim.py      # 古い ToolMessage の切り詰め
│   ├── context_compaction.py # 会話履歴の自動要約・圧縮
│   ├── main_token_guard.py  # メインエージェントのトークン量ガード・引継ぎプロンプト自動生成
│   ├── subagent.py          # dispatch_agent の内部ReActループ
│   ├── mcp_client.py        # MCPサーバー接続（stdio）・ツール変換
│   ├── chat_log.py          # 会話ログのテキストファイル記録
│   ├── thread_store.py      # 会話スレッド一覧（左サイドバー）・再開用の軽量ストア
│   ├── plan_persist.py      # plan/plan_approved変更をターン完了を待たず即座にthread_storeへ永続化
│   ├── ask_relay.py         # approve_plan/AskUserQuestion/ask_user_choice応答待ちをスレッド切替後の別セッションへ引き継ぐ状態
│   ├── instance_lock.py     # 同一データディレクトリへの多重起動を防ぐプロセス排他ロック
│   ├── cleanup.py           # 不要ファイルの自動削除
│   ├── files.py             # ファイルアップロード処理
│   ├── images.py            # 画像処理・Data URL変換
│   ├── log_rotation.py      # app.log の日時ローテーション
│   └── uploads.py           # アップロードファイル管理
├── agents/
│   ├── README/AGENTS_README.md # エージェント種別定義（frontmatter）の書き方ガイド（*.mdスキャン時の警告を避けるためREADME/配下に退避）
│   ├── explore.md           # 読み取り専用の汎用調査・オフィス文書/PDF調査エージェント種別
│   ├── planner.md           # create_planの前段で計画草案（steps候補＋detail_markdown）を作る設計専用の読み取り専用エージェント種別
│   ├── worker.md            # 承認済み計画に沿って読取り→書込みを内部完結させる書き込み可能エージェント種別
│   └── verifier.md          # 成果物検証用エージェント種別
├── skills/
│   ├── SKILLS_README.md    # スキル開発者向けガイド
│   ├── skill-creator/       # 新しいスキルの作成・既存スキルの改善・eval検証を行うメタスキル
│   ├── docx-read/           # docx読込専用（段落・表・文書プロパティ・Track Changes有無）
│   ├── docx-create/         # docx新規生成（見出し/段落/表/画像/ページ設定等）
│   ├── docx-edit/           # 既存docxの編集（検索置換・Track Changes・段落追加削除）
│   ├── docx-render/         # Word文書ページの画像化
│   ├── excel-read/          # xlsx/xls/xlsm読込専用（シート一覧・セルデータ）
│   ├── excel-edit/          # xlsx/xlsm新規作成・編集（セル/書式/行列/グラフ等）
│   ├── excel-recalc/        # xlsx/xlsm/xls数式再計算・エラーセル検出
│   ├── excel-vba-read/      # xlsm/xls VBAマクロコード読込専用
│   ├── excel-vba-edit/      # xlsm VBAマクロコード追加/上書き/削除・実行
│   ├── excel-render/        # Excelシートの画像化
│   ├── excel-knowledge/     # excel-edit/read/render/recalc利用時のコーディング作法・落とし穴のローカル知識ベース
│   ├── excel-vba-knowledge/ # Excel VBAのコーディング作法・落とし穴のローカル知識ベース
│   ├── pdf-tools/           # PDF読込・ページ画像化・PDF生成
│   ├── pptx-read/           # pptx読込専用（テキスト・表・発表者ノート抽出）
│   ├── pptx-create/         # pptx新規生成（16:9テンプレート方式）
│   ├── pptx-inspect/        # 既存pptxテンプレートの構造読取（shape_index把握）
│   ├── pptx-edit/           # 既存pptxテンプレートの部分編集（デザイン保持）
│   ├── pptx-render/         # PowerPointスライドの画像化
│   ├── office_shared/       # docx/excel/pptx各スキルのscripts/が共有するPython共通処理（SKILL.mdを持たずLLMには公開されない）
│   └── web-search/          # Tavily APIによるWeb検索（要APIキー設定）
│       # Read/Glob/Grep/json_query/list_path_memory はネイティブツール化済み
│       # （src/tools/read_tool.py 等、src/path_memory.py）。
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
    ├── plans/               # create_plan が detail_markdown を渡した際の詳細計画Markdown
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
- **SKILL.md本文の呼び出し例はコマンドライン形式で統一**: `scripts/` 配下の
  スクリプトを呼ぶ例は、Locohane固有の `run_script` JSON引数形式
  （`{"skill_name":..., "script_filename":..., "script_args":[...]}`）ではなく、
  他のAgent Skills対応環境（Claude Code等）と共通の `python <script>.py <args...>`
  形式で書く（`skills/SKILLS_README.md` 4-0節参照）。これにより `skills/<name>/`
  フォルダは他環境へそのまま持ち出せるだけでなく、逆にGitHub等で公開されている
  `python <script>.py <args...>` 形式のサードパーティ製Agent Skills（他環境向けに
  書かれたSKILL.md）も、呼び出し記法の差異という障壁なくLocohaneへ導入できる
  （依存ライブラリの導入等、環境固有の準備は別途必要）。この形式からLocohane
  固有の `run_script` 呼び出しへの変換は、SKILL.md側にもコード側にも実装を持たず、
  LLM側の共通プロンプト指示（`system_prompt/system_prompt.md`・
  `system_prompt/subagent_common.md` の「SKILL.md呼び出し例の変換ルール」節）が
  ツール呼び出し時に行う。
- 仕様違反の SKILL.md は **スキップし警告ログ** を出す（全体は落とさない）。

### 範囲外（実装していない）

- **`allowed-tools`**: フィールドは読み取れるが、それに基づく自動承認は未実装（仕様上も実験的）。
- **公式バリデータ（skills-ref）非統合**: 自前の最小検証のみ。厳密検証が必要なら
  `skills-ref validate ./skills/<name>` を別途利用のこと。
- **深いネスト参照**: reference 参照は SKILL.md から 1 階層を想定。

---

## データの保存場所と手動削除の手順

すべて `data/` 配下（`config.ini` の `[paths]`/`[uploads]`/`[elements]`/`[log]`/`[default_workdir]` 等の `dir` 系キーで変更可）。`data/` は `.gitignore` 済み。保存先ルートをまとめて変更したい場合は、各キーを個別に書き換える代わりに `[paths] common_data_dir`（既定 `./data`）だけを変更すればよい（対応するキーの値は `${common_data_dir}` を参照する形で書かれている）。

| パス | 中身 | 削除してよいタイミング | 削除方法 |
|------|------|------------------------|----------|
| `data/checkpoints.sqlite` | LangGraph の会話状態（全スレッドの履歴） | 過去の会話履歴が不要になったとき | ファイルを削除（アプリ停止中に） |
| `data/chat_threads.sqlite` | 会話スレッド一覧（画面左サイドバー）・再開用のメタデータ（スレッド名・所有者・Chainlitの Step 履歴）。`checkpoints.sqlite` とは別ファイル（`[thread_store]`参照） | 一覧・再開機能が不要になったとき | ファイルを削除（アプリ停止中に。`checkpoints.sqlite` 自体は消えないため会話状態は失われない） |
| `data/uploads/` | Chainlit にアップロードされたファイル | アップロード資料が不要になったとき | フォルダ内を削除 |
| `data/logs/app.log` | アプリの動作ログ | いつでも | ファイルを削除 |
| `data/memory/` | 永続メモリー（`user`/`feedback`/`project`/`reference` サブフォルダ＋`MEMORY.md`索引） | 蓄積した記憶が不要になったとき | フォルダ内を削除（`MEMORY.md`は次回保存時に再生成される） |
| `data/plans/` | `create_plan` が `detail_markdown` 引数を渡した場合の詳細計画Markdown（`[paths] plans_dir`） | 古い計画が不要になったとき | フォルダ内を削除 |
| `data/temp/_tmp_<作成時刻>_<thread_id>/` | `execute_python_code`/`run_script` の中間生成物・`write_scratch_note`/`write_thread_note` の書き出し先。自セッション専用（`[default_workdir]` 参照）。フォルダ名先頭の作成時刻（ミリ秒まで）はファイラー上で作成順に並べるためのもの | セッション終了後、不要になったとき | フォルダ内を削除（`[default_workdir] retention_days`/`cleanup_interval_hours` により自動削除もされる） |
| `data/elements/<thread_id>/` | 添付ファイル（`provide_download`/`analyze_image`の`show_in_chat=True`等）・回答本文への画像埋め込みの永続化先。スレッド再開・プロセス再起動後も表示できるようここへ実体をコピー保存する（`[elements]`参照、`src/thread_store.py`） | 添付ファイルが不要になったとき | フォルダ内を削除 |
| `.files/` | Chainlit自身のセッションファイル配信ディレクトリ（送信直後のライブ表示にのみ使う一時配信。プロジェクト直下、`data/`配下ではない） | いつでも | フォルダ内を削除 |
| `data/app.lock` | 同一データディレクトリへの多重起動を防ぐプロセス排他ロック（`src/instance_lock.py`）。空ファイルにOSのファイルロックをかけるだけで中身は使わない | アプリ停止中、削除しても実害はない | ファイルを削除（アプリ起動中は削除不可） |

`data/uploads/` は `config.ini` の `[uploads] retention_days`（既定7日）を過ぎたファイルを
`cleanup_interval_hours`（既定1時間）おきに自動削除する。`retention_days` を0以下にすると
自動削除は無効化される。`data/elements/` も `[elements] retention_days`（既定7日）・
`cleanup_interval_hours`（既定1時間）で同様に自動削除される。`.files/` も `[chainlit_files]
retention_days`（既定7日）・`cleanup_interval_hours`（既定1時間）で同様に自動削除される
（Chainlit自身のパス管理下にあるため `dir` キーは無い。ディレクトリ単位＝セッションIDごとに
削除する点が他と異なる）。

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

- `--alias` はモデル名。`config.ini` の `[llm] main_url` 内の `model` と揃える。
- 接続先・モデル名は `config.ini`（または環境変数 `LLM_MAIN_URL` / `LLM_SUB_URL`）で切り替え可能。
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
| `[llm]` | `main_url` | メインエージェント用のLLM接続先（1件のみ変更対象。`base_url`＝手順2で起動した llama-server の OpenAI 互換エンドポイント例: `http://localhost:8080/v1`、`api_key`＝llama.cpp は認証不要のため通常はダミー値のまま、`model`＝手順2の `--alias` と一致させるモデル名） |
| `[llm]` | `sub_url` | サブエージェント（`dispatch_agent`）用のLLM接続先。形式は `main_url` と同じ。通常は `main_url` と同じ値にする |
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
6項目の現在値を提示した上で対話形式で新しい値を確認し、まとめて
更新してくれる（`.claude/skills/setup-basic-config/SKILL.md`）。

**方法B: 各ファイルを直接編集する**

エディタで `config.ini` を開き、`[llm]` セクションの
`main_url`/`sub_url`、`[scripts]` セクションの
`python` を直接書き換える。`app.bat` の `PYTHON_DIR` と、プロジェクト
`CLAUDE.md` の「Python実行環境」「Node.jsパス」も同様に書き換える。
各項目の意味は後述の「設定リファレンス（config.ini）」も参照。

### 4. アプリ起動

```bash
cd C:\DT_Python\Locohane
C:/DT_Python/Python311/env_claudecode/Scripts/chainlit run app.py -w
```

ブラウザで開き、例えば「この Excel ファイルの中身を要約して」と送ると、
`read_skill`（excel-read の本文読込）→ `run_script`（`read_excel.py` 実行）が
**ステップとして可視化** され、結果がストリーミング表示される。

---

## 開発環境

本プロジェクトの開発・動作検証に実際に使用している環境の一例。

### コーディングAgentツール

| ツール | 内容 |
|--------|------|
| ClaudeCode | Claude Pro Plan |
| QwenCode | llama-server（ローカル）+ Qwen3.6-35B-A3B |

### OS / ハードウェア

- OS: Windows 11
- CPU: Intel(R) Core(TM) Ultra 5 225（10コア / 10論理プロセッサ、ベース3.30GHz）
- メモリ: 32GB
- GPU: NVIDIA GeForce RTX 5060 Ti（専用GPUメモリ 16.0GB）

### llama-server 起動コマンド例

上記ハードウェアに合わせて以下の引数で `llama-server` を起動している。

```
  --model .\models\unsloth\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
  --mmproj .\models\unsloth\Qwen3.6-35B-A3B-GGUF\mmproj-F16.gguf
  --n-cpu-moe 25
  --n-gpu-layers 99
  --threads 10
  --threads-batch 10
  --threads-http 4
  --models-max 1
  --parallel 2
  --batch-size 4096
  --ubatch-size 2048
  --ctx-size 256000
  --n-predict 32000
  --temp 0.6
  --top-p 0.95
  --top-k 20
  --min-p 0.0
  --presence-penalty 0
  --repeat-penalty 1.0
  --cache-reuse 256
  --cache-ram -1
  --swa-full
  --cache-type-k q8_0
  --cache-type-v q8_0
  --flash-attn on
  --jinja
  --no-mmap
  --mlock
  --reasoning-format deepseek
  --reasoning-budget 8192
  --reasoning-budget-message "**Wait, I am overthinking this. I should answer now.**"
```

- `n-cpu-moe`/`n-gpu-layers` はGPUの専用メモリ容量（16GB）に収まるよう調整したMoEオフロード設定。
- `ctx-size`/`cache-type-k`/`cache-type-v` はコンテキスト長とKVキャッシュ量子化のバランスをとった値。

---

## 同梱スキル

| スキル | 配置場所 | 種別 | 内容 |
|--------|----------|------|------|
| `skill-creator` | `skills/` | スクリプト実行を伴う | 新しいスキルの作成・既存スキルの改善・description のトリガー精度最適化・evalハーネスによる検証を行うメタスキル。 |
| `pdf-tools` | `skills/` | スクリプト実行を伴う | PDFのテキスト抽出・ページ画像化（レイアウト/図表/スキャン内容の視覚把握）・PDF生成（日本語対応）。 |
| `docx-read` | `skills/` | スクリプト実行を伴う | docxの読込専用（段落・表・文書プロパティ・Track Changes有無・画像有無の取得）。 |
| `docx-create` | `skills/` | スクリプト実行を伴う | docxの新規生成（見出し/段落/箇条書き/表/画像/ページ設定/ヘッダーフッター等）。 |
| `docx-edit` | `skills/` | スクリプト実行を伴う | 既存docxの編集（検索置換、画像挿入・サイズ変更、Track Changes/変更履歴の付与・確定・却下を含む。画像トリミングは非対応）。 |
| `docx-render` | `skills/` | スクリプト実行を伴う | Word文書ページの画像化（レイアウト・表・画像配置・強調表現の視覚把握）。 |
| `excel-read` | `skills/` | スクリプト実行を伴う | xlsx/xls/xlsmの読込専用（シート一覧・セルデータ・画像有無）。 |
| `excel-edit` | `skills/` | スクリプト実行を伴う | xlsx/xlsmの新規作成・編集（セル/書式/行列/グラフ・画像追加と位置調整・条件付き書式・データ検証を含む。画像トリミングは非対応）。 |
| `excel-recalc` | `skills/` | スクリプト実行を伴う | xlsx/xlsm/xlsの数式再計算・エラーセル検出。 |
| `excel-vba-read` | `skills/` | スクリプト実行を伴う | xlsm/xlsのVBAマクロコードの読み込み専用。 |
| `excel-vba-edit` | `skills/` | スクリプト実行を伴う | xlsmのVBAマクロコードの追加/上書き/削除・実行。 |
| `excel-render` | `skills/` | スクリプト実行を伴う | Excelシートの画像化（罫線・書式・グラフ・レイアウトの視覚把握）。 |
| `excel-knowledge` | `skills/` | 参照のみ | excel-edit/excel-read/excel-render/excel-recalc利用時のコーディング作法・定石・落とし穴を蓄積したローカル知識ベース（`references/`参照）。 |
| `excel-vba-knowledge` | `skills/` | 参照のみ | Excel VBAのコーディング作法・定石・落とし穴を蓄積したローカル知識ベース（`references/`参照）。 |
| `pptx-read` | `skills/` | スクリプト実行を伴う | pptxの読込専用（スライドのタイトル・本文・表・発表者ノートの抽出）。 |
| `pptx-create` | `skills/` | スクリプト実行を伴う | pptxの新規生成（16:9テンプレート方式）。 |
| `pptx-inspect` | `skills/` | スクリプト実行を伴う | 既存pptxテンプレートの構造読取専用（`pptx-edit`前の`shape_index`把握）。 |
| `pptx-edit` | `skills/` | スクリプト実行を伴う | 既存pptxテンプレートの部分編集（デザインを保った差し替え・複製・削除・並び替え・画像/グラフの新規追加・画像トリミング）。 |
| `pptx-render` | `skills/` | スクリプト実行を伴う | PowerPointスライドの画像化（レイアウト・図表・画像配置・強調表現の視覚把握）。 |
| `web-search` | `skills/` | スクリプト実行を伴う | Tavily APIによるWeb検索。スキル専用の`scripts/.env`にTAVILY_API_KEY設定時のみ動作（既定では通信なし）。 |

`.locohane/skills/` はユーザー独自スキルの置き場（`skills/` とマージ走査、同名は優先）で、現状は使い方を示す `README.md` のみを含む。

スキル開発の詳細な手順・規約は [`skills/SKILLS_README.md`](skills/SKILLS_README.md) を参照。

処理時間が `[scripts].timeout`（既定300秒）に近づく、または超えうるスクリプトを持つスキルは、
SKILL.md 側にその旨（実行に時間がかかりうる）を明記する（`run_script`/`run_script_background`
といったLocohane固有のツール名はSKILL.md本文には書かない。4-0節参照）。長時間化しうる
スクリプトかどうかを踏まえて `run_script` と `run_script_background` のどちらを使うかは、
LLM側の共通変換ルール（`system_prompt/system_prompt.md`・`system_prompt/subagent_common.md`
の「SKILL.md呼び出し例の変換ルール」節）が判断する。`run_script_background` は
（`[scripts].background_inline_wait_max_seconds` の安全上限に達しない限り）完了まで
自動的に待ち、`run_script` と同じ最終結果を直接返す（進捗は人間向けにチャットへ自動で
通知されるため、SKILL.md 側でポーリング手順を指示する必要は無い）。安全上限を超える
ごく長時間のスクリプトに限り `job_id` を含む案内が返るので、その場合のみ
`check_script_job`/`stop_script_job` の使い方をSKILL.md に明記すればよい。

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

`scripts/` 配下のスクリプトを呼び出す例を本文に書く場合は、Locohane固有の
`run_script` JSON引数形式ではなく `python <script>.py <args...>` というコマンド
ライン形式で書く（詳細は [`skills/SKILLS_README.md`](skills/SKILLS_README.md) 4-0節参照）。
実際の`run_script`呼び出しへの変換はLLM側の共通指示が行う。

アプリを再起動すると起動時走査で自動的に発見される（動的リロードはしない）。

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
- **`[main_agent_tool_guard]`との関係**: MCP動的ツールは名前が実行時にしか
  分からず`allow_entries`へ事前登録できないため、既定の`mode=all`のままだと
  メインエージェントから一切呼び出せない。事前登録なしで常に許可したい場合は
  `mode=tools_skills_only`を使う（詳細は`config.ini`内コメント参照）。

**MCPサーバー接続は、実装のみで動作テストしていないのでまともに動くか保証できません**

---

## 永続メモリー（スレッドをまたぐ記憶）

ClaudeCode のメモリー機能相当。会話（スレッド）が変わっても引き継ぎたい事実を、
`data/memory/`（`config.ini` の `[paths] memory_dir` で変更可）配下に
YAML frontmatter 付き Markdown ファイルとして保存する。ロジックは `src/memory.py`
に集約し、`src/tools/memory_tools.py` の6ツール（`create_memory`/`update_memory`/`delete_memory`/
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

### スレッド共有ノート（thread notes）との違い

上記の永続メモリーはスレッドをまたぐ長期記憶用だが、これとは別に
**同一スレッド内に閉じた一時的な共有メモ**として `write_thread_note`/
`list_thread_notes`/`read_thread_note` の3ツールがある（主エージェント・
サブエージェント（`dispatch_agent`の全 agent_type）の両方が読み書き可能）。

- 保存先は `data/memory/` ではなく、作業ディレクトリ配下の
  `_tmp_<name>/_thread_notes.md`（`execute_python_code`/`run_script`の
  中間生成物と同じ一時領域。`write_scratch_note` が使う
  `_scratch_notes_<run_id>.md` の隣に置かれる）。
- 用途は「委譲先サブエージェントの調査結果を、最終回答を肥大化させずに
  他のエージェント（委譲元や以降の別のサブエージェント）へ引き継ぐ」こと。
  永続メモリーのように次回以降のスレッドへは引き継がれない。
- スレッド共有ノートは自動索引化されず、システムプロンプトにも差し込まれない
  （`list_thread_notes`で都度トピック一覧・文字数を確認してから読む設計。
  詳細は `system_prompt/subagent_common.md`・`system_prompt/system_prompt.md`
  参照）。
- ライフサイクルは `_tmp_<name>` 全体と同じで、`[default_workdir]`の
  `retention_days`/`cleanup_interval_hours`（本ドキュメント「設定リファレンス」
  参照）による自動削除の対象、または手動削除も可能。
- コンテキスト圧縮（要約）が発生すると、要約LLMが書き出し済みのトピックを
  要約に残すとは限らないため、`src/context_compaction.py` の `maybe_compact`
  がトピック一覧（`list_thread_notes`相当）を要約結果とは無関係に機械的に
  再注入する。これにより圧縮後もモデルはthread noteの存在・トピック名を
  見失わない（承認済み実行計画の再注入と同じ仕組み）。

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
| `[llm]` | `main_url` | メインエージェント用のLLM接続先リスト（`[{"base_url":...,"api_key":...,"model":...}]` のJSON/Python風リスト形式、複数指定可。各要素に任意で `start`/`end`（使用可能時間帯、単位は時間、分は小数、必ずセットで指定）、`provider`（`openai_compatible`既定/`llama_cpp`。`round_robin`戦略が選ぶ前にGET /slotsで空き確認する対象を指定する）を追加でき、リスト全体で最低1件は`start`/`end`両方省略した常時使用可能な接続先が必要） | `LLM_MAIN_URL` |
| `[llm]` | `main_routing_strategy` | `main_url` が複数件のときの選び方（`round_robin`/`random`/`priority_failover`。`round_robin`はprovider="llama_cpp"の接続先を選ぶ前にGET /slotsで空きを確認し、無ければスキップ、全滅なら待機する） | `LLM_MAIN_ROUTING_STRATEGY` |
| `[llm]` | `sub_url` | サブエージェント（`dispatch_agent`）用のLLM接続先リスト。形式は `main_url` と同じ | `LLM_SUB_URL` |
| `[llm]` | `sub_routing_strategy` | `sub_url` が複数件のときの選び方。形式は `main_routing_strategy` と同じ | `LLM_SUB_ROUTING_STRATEGY` |
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
| `[llm]` | `enable_thinking` | Qwen3系モデルのthinking（reasoning、`<think>`ブロック）モードのON/OFF（llama.cpp拡張、空欄なら未指定でモデル・llama-server既定に委ねる） | `LLM_ENABLE_THINKING` |
| `[llm]` | `reasoning_format` | thinkingブロックの出力形式（`none`/`deepseek`/`deepseek-legacy`。llama.cpp拡張。空欄なら未指定でllama-server既定の`auto`に委ねる） | `LLM_REASONING_FORMAT` |
| `[llm]` | `reasoning_budget` | thinkingに使えるトークン数上限（llama.cpp拡張、`-1`=無制限・`0`=即座に終了・`N>0`=上限。空欄なら未指定でllama-server既定の`-1`に委ねる） | `LLM_REASONING_BUDGET` |
| `[llm]` | `reasoning_budget_message` | 上記`reasoning_budget`を使い切った際にthinking終了タグ直前へ挿入するメッセージ（空欄なら挿入しない） | `LLM_REASONING_BUDGET_MESSAGE` |
| `[llm]` | `track_token_usage` | トークン使用量の取得を有効にする（Chainlit UI表示・eval結果に反映） | `LLM_TRACK_TOKEN_USAGE` |
| `[llm]` | `request_timeout_seconds` | LLMサーバーへの応答待ちタイムアウト秒数（read/write/pool） | `LLM_REQUEST_TIMEOUT_SECONDS` |
| `[llm]` | `stream_chunk_timeout_seconds` | ストリーミング中にチャンクが届かない場合のタイムアウト秒数 | `LLM_STREAM_CHUNK_TIMEOUT_SECONDS` |
| `[llm]` | `max_concurrent_requests` | llama-serverへの同時リクエスト数上限。1以上でSemaphore(N)ガード（既定1＝完全直列化）、0以下で無制限 | `LLM_MAX_CONCURRENT_REQUESTS` |
| `[llm]` | `round_robin_slots_probe_timeout_seconds` | `round_robin`戦略がprovider="llama_cpp"の接続先を選ぶ前に送るGET /slots問い合わせ自体のタイムアウト秒数（既定3、確認できなければ空きありとみなすfail-safe） | `LLM_ROUND_ROBIN_SLOTS_PROBE_TIMEOUT_SECONDS` |
| `[llm]` | `round_robin_busy_poll_interval_seconds` | `round_robin`戦略で候補の全接続先に空きスロットが無かった場合、再確認までに待機する秒数（既定2） | `LLM_ROUND_ROBIN_BUSY_POLL_INTERVAL_SECONDS` |
| `[paths]` | `common_data_dir` | 各種データ保存先パスの共通ベースディレクトリ（既定 `./data`）。本セクションの`checkpoint_db`/`memory_dir`/`plans_dir`、および`[uploads]`/`[log]`/`[default_workdir]`/`[path_memory]`/`[chat_log]`の`dir`系キーの値に`${common_data_dir}`と書くとここで指定した値に置換される（configparser標準の補間ではなくconfig.py側の独自置換） | `COMMON_DATA_DIR` |
| `[paths]` | `skills_dir` | スキルフォルダ | `SKILLS_DIR` |
| `[paths]` | `agents_dir` | エージェント種別定義フォルダ（`dispatch_agent` の `agent_type`） | `AGENTS_DIR` |
| `[paths]` | `project_locohane_dir` | プロジェクト固有の拡張ディレクトリ（ClaudeCode の `.claude/` 相当）。配下の `skills/`（`skills_dir` にマージ走査、同名は優先）・`agents/`（`agents_dir` にマージ走査、同名は優先）・`LOCOHANE.md`（プロジェクト固有指示、存在しなくてもエラーにならない）を自動検知する。`nudge_messages` と同じリスト形式で複数ディレクトリ指定可 | `PROJECT_LOCOHANE_DIR` |
| `[paths]` | `system_prompt_path` | メインエージェント用システムプロンプトのテンプレート | `SYSTEM_PROMPT_PATH` |
| `[paths]` | `bin_path` | 外部バイナリ実行ファイルの配置先ディレクトリ一覧（`project_locohane_dir`と同じリスト形式）。コマンド名を素の状態で叩くスキルがOS側PATH未登録でも呼び出せるようにする（`src/tools/_subprocess_env.py`参照） | `BIN_PATH` |
| `[paths]` | `checkpoint_db` | 会話状態 SQLite | `CHECKPOINT_DB` |
| `[paths]` | `memory_dir` | 永続メモリーの保存先ルート | `MEMORY_DIR` |
| `[paths]` | `plans_dir` | `create_plan` が `detail_markdown` を渡した際の詳細計画Markdownの保存先 | `PLANS_DIR` |
| `[paths]` | `help_path` | `help` ツールが返すヘルプ本文Markdownのパス | `HELP_PATH` |
| `[uploads]` | `dir` | アップロード保存先 | `UPLOAD_DIR` |
| `[uploads]` | `retention_days` | アップロードファイルの保持日数（0以下で自動削除無効） | `UPLOAD_RETENTION_DAYS` |
| `[uploads]` | `cleanup_interval_hours` | 自動削除チェックの実行間隔（時間） | `UPLOAD_CLEANUP_INTERVAL_HOURS` |
| `[chainlit_files]` | `retention_days` | Chainlit自身のセッションファイルディレクトリ（`.files/<セッションID>/`）の保持日数（0以下で自動削除無効）。ディレクトリ単位で削除 | `CHAINLIT_FILES_RETENTION_DAYS` |
| `[chainlit_files]` | `cleanup_interval_hours` | 自動削除チェックの実行間隔（時間） | `CHAINLIT_FILES_CLEANUP_INTERVAL_HOURS` |
| `[elements]` | `dir` | 添付ファイル・回答本文への画像埋め込みの永続化先（スレッド再開・プロセス再起動後も表示するため） | `ELEMENTS_DIR` |
| `[elements]` | `retention_days` | 上記の保持日数（0以下で自動削除無効） | `ELEMENTS_RETENTION_DAYS` |
| `[elements]` | `cleanup_interval_hours` | 自動削除チェックの実行間隔（時間） | `ELEMENTS_CLEANUP_INTERVAL_HOURS` |
| `[images]` | `max_long_side_pixels` | LLMへ渡す前に画像を縮小する長辺ピクセル数の上限（`0`で縮小なし） | `IMAGE_MAX_LONG_SIDE_PIXELS` |
| `[images]` | `jpeg_quality` | 縮小後に再エンコードするJPEG品質（1-95） | `IMAGE_JPEG_QUALITY` |
| `[images]` | `inline_preview_max_long_side_pixels` | 回答本文（Markdownテーブルのセル等）へ直接埋め込む画像プレビューの長辺ピクセル数の上限。`analyze_image`の`show_in_chat=True`と同じセッションファイル配信経路で渡すためVision向け設定より小さくても差し支えなく、表示帯域・ディスク使用量を抑える目的で別に小さい値を使う | `IMAGE_INLINE_PREVIEW_MAX_LONG_SIDE_PIXELS` |
| `[images]` | `inline_preview_jpeg_quality` | 上記プレビューの再エンコード品質（1-95） | `IMAGE_INLINE_PREVIEW_JPEG_QUALITY` |
| `[images]` | `inline_preview_min_long_side_pixels` | 上記プレビューの長辺ピクセル数の下限。これより小さい画像はこの値まで拡大してから埋め込む（`0`で拡大なし） | `IMAGE_INLINE_PREVIEW_MIN_LONG_SIDE_PIXELS` |
| `[scripts]` | `timeout` | `run_script`/`execute_python_code` 共通のタイムアウト秒 | `SCRIPT_TIMEOUT` |
| `[scripts]` | `python` | `.py` 実行に使う Python | `SCRIPT_PYTHON` |
| `[scripts]` | `code_execution_enabled` | `execute_python_code` ツール自体の有効/無効 | `CODE_EXECUTION_ENABLED` |
| `[scripts]` | `background_max_runtime_seconds` | `run_script_background` のジョブを強制終了するまでの上限秒 | `SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS` |
| `[scripts]` | `background_job_retention_seconds` | `run_script_background` の完了済みジョブが `check_script_job` で未回収のまま残ってよい秒数 | `SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS` |
| `[scripts]` | `background_min_poll_interval_seconds` | `check_script_job` を同一ジョブへ再度呼べるまでの最短間隔秒（0以下で無効化） | `SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS` |
| `[scripts]` | `background_min_poll_message` | 上記間隔未満で呼ばれた際にLLMへ返すメッセージのテンプレート（`{wait_remaining}`/`{job_id}`/`{min_interval}` を埋め込み可）。空欄なら既定文言 | `SCRIPT_BACKGROUND_MIN_POLL_MESSAGE` |
| `[scripts]` | `background_inline_wait_max_seconds` | `run_script_background`/`execute_python_code_background` がジョブ完了をLLMを介さずコード側で待つ上限秒数。超過時のみ `job_id` を返してLLMへ制御を戻す | `SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS` |
| `[scripts]` | `background_progress_push_interval_seconds` | 待機中、人間向けに経過秒数・標準出力/標準エラー末尾をチャットへ直接送る間隔（秒） | `SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS` |
| `[scripts]` | `background_job_output_tail_chars` | 進捗表示・`check_script_job`/`stop_script_job`/`read_thread_note`が末尾のみ表示する際の標準出力/標準エラー/進捗メモの最大文字数（`[subagent]`配下の同種表示も共有） | `SCRIPT_BACKGROUND_JOB_OUTPUT_TAIL_CHARS` |
| `[scripts]` | `plan_approval_exempt_scripts` | `run_script`/`run_script_background`の計画承認（Plan Mode）を免除する読み取り専用スクリプトのホワイトリスト（`[["スキル名","スクリプトファイル名"], ...]`形式、空欄なら既定7件）。免除は承認のみで、`[main_agent_tool_guard]`によるメインエージェントからの直接呼び出し制限は別枠 | `SCRIPT_PLAN_APPROVAL_EXEMPT_SCRIPTS` |
| `[scripts]` | `agent_type_run_script_allowlist` | `dispatch_agent`の`agent_type`ごとに`run_script`で呼べるスキル/スクリプトを絞り込むホワイトリスト（`[[agent_type, 対象], ...]`形式、対象は`"スキル名"`または`["スキル名","スクリプトファイル名"]`、空欄なら既定19件） | `SCRIPT_AGENT_TYPE_RUN_SCRIPT_ALLOWLIST` |
| `[file_tools_duplicate_guard]` | `enabled` | Read/Glob/Grep/json_query ツールの同一引数繰り返し呼び出しを防止するガードの有効/無効 | `FILE_TOOLS_DUPLICATE_GUARD_ENABLED` |
| `[file_tools_duplicate_guard]` | `max_calls` | 同一シグネチャの呼び出しを許可する回数（既定1回） | `FILE_TOOLS_DUPLICATE_GUARD_MAX_CALLS` |
| `[file_tools_duplicate_guard]` | `carry_over_to_main` | サブエージェント内の呼び出し履歴をメイン判定へ持ち越すかどうか | `FILE_TOOLS_DUPLICATE_GUARD_CARRY_OVER` |
| `[main_agent_tool_guard]` | `mode` | メインエージェント自身がビルトインツール・`run_script`配下のスキルスクリプトを直接呼び出せる回数を制限するガードのモード（`dispatch_agent`配下のサブエージェントは対象外）。`false`=無効、`tools_skills_only`=MCP動的ツール（`mcp__server__tool`形式）のみ常に許可しそれ以外は制限、`all`（既定）=MCP動的ツールも含め全て制限。トークン消費の大きい重量系ツールの連打によるトークン上限到達を防ぐ | `MAIN_AGENT_TOOL_GUARD_MODE` |
| `[main_agent_tool_guard]` | `visibility_mode` | `mode!=false`時の可視化モード。対象はスキル一覧とビルトインツールの呼べない名前案内（`mode=all`時はMCP動的ツール名も混ざりうる）。`strict`（既定）は呼び出せないツール・スキルを一覧から完全除外、`hint`は一覧に出しつつ直接実行不可のものへ`dispatch_agentへ委譲`という注記を付ける | `MAIN_AGENT_TOOL_GUARD_VISIBILITY_MODE` |
| `[main_agent_tool_guard]` | `allow_entries` | 許可リスト（ホワイトリスト）。`["ツール名", max_calls]`または`[["スキル名","スクリプトファイル名"], max_calls]`の要素からなるリスト形式で、未登録のツール・スキルスクリプトは`mode=all`（または`tools_skills_only`時のMCP以外）の間メインエージェントから一切呼び出せない。`max_calls`は`0`=登録のみで完全ブロック、`-1`=無制限、`1`以上=その回数まで許可（他のガードと0/-1の意味が逆なので注意） | `MAIN_AGENT_TOOL_GUARD_ALLOW_ENTRIES` |
| `[graph]` | `implementation` | ReAct ループの実装（`handwritten` または `prebuilt`） | `GRAPH_IMPL` |
| `[graph]` | `recursion_limit` | メインReActループ（agent→tools遷移）の最大反復回数。超過時は打ち切りメッセージを表示 | `GRAPH_RECURSION_LIMIT` |
| `[graph]` | `connection_error_max_retries` | LLMサーバーとの通信エラー（接続失敗・5xx等）検知時、直近接続先を一時クールダウンした上でグラフを再構築し同じ反復を自動リトライする回数（メインエージェント用。`0`でリトライせず通信エラーを通知して中断） | `GRAPH_CONNECTION_ERROR_MAX_RETRIES` |
| `[graph]` | `max_parallel` | メインエージェントのツール呼び出し（ImageAwareToolNode）の同時実行数上限。1以上でSemaphore(N)ガード、0以下でガード無効化 | `GRAPH_TOOL_MAX_PARALLEL` |
| `[graph]` | `token_guard_enabled` | メインエージェントのトークン量ガード（直近1回の応答が閾値到達で引継ぎプロンプトへ差し替え）の有効/無効 | `GRAPH_TOKEN_GUARD_ENABLED` |
| `[graph]` | `token_guard_soft_threshold` | 上記ガードが発動するトークン数閾値。実測: この仕組みが無い状態では1リクエストあたり24,833→128,000まで単調増加しコンテキスト上限で停止した事例あり | `GRAPH_TOKEN_GUARD_SOFT_THRESHOLD` |
| `[graph]` | `handoff_prompt_path` | 上記ガード発動時に差し込む、新しいチャットへの引継ぎ手順を指示するMarkdownファイルのパス | `GRAPH_HANDOFF_PROMPT_PATH` |
| `[subagent]` | `max_iterations` | `dispatch_agent` の内部ReActループの最大反復回数 | `SUBAGENT_MAX_ITERATIONS` |
| `[subagent]` | `max_parallel` | `dispatch_agent` の実LLM呼び出しの同時実行数上限。1以上でSemaphore(N)ガード、0以下でガード無効化 | `SUBAGENT_MAX_PARALLEL` |
| `[subagent]` | `token_guard_enabled` | サブエージェントのトークン使用量ガードの有効/無効 | `SUBAGENT_TOKEN_GUARD_ENABLED` |
| `[subagent]` | `token_guard_soft_threshold` | ソフト警告（注意メッセージ注入）のトークン閾値 | `SUBAGENT_TOKEN_GUARD_SOFT_THRESHOLD` |
| `[subagent]` | `token_guard_hard_threshold` | ハード打ち切りのトークン閾値 | `SUBAGENT_TOKEN_GUARD_HARD_THRESHOLD` |
| `[subagent]` | `token_guard_soft_warning_text` | ソフト警告到達時に注入する注意メッセージの文言（`write_thread_note`への書き出しを促しつつ、ツール呼び出し自体は禁止しない） | `SUBAGENT_TOKEN_GUARD_SOFT_WARNING_TEXT` |
| `[subagent]` | `empty_response_max_retries` | 空応答の再試行回数 | `SUBAGENT_EMPTY_RESPONSE_MAX_RETRIES` |
| `[subagent]` | `background_job_retention_seconds` | `dispatch_agent` のジョブが終了後、`check_dispatch_agent_job` で一度も取得されないまま残ってよい秒数 | `SUBAGENT_BACKGROUND_JOB_RETENTION_SECONDS` |
| `[subagent]` | `background_min_poll_interval_seconds` | `check_dispatch_agent_job` を連続で呼べる最短間隔秒数。0以下で無効化 | `SUBAGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS` |
| `[subagent]` | `background_min_poll_message` | 上記間隔未満で呼ばれた際にLLMへ返すメッセージのテンプレート（`{wait_remaining}`/`{job_id}`/`{min_interval}` を埋め込み可）。空欄なら既定文言 | `SUBAGENT_BACKGROUND_MIN_POLL_MESSAGE` |
| `[subagent]` | `background_inline_wait_max_seconds` | `dispatch_agent` がジョブ完了をLLMを介さずコード側で待つ上限秒数。超過時のみ `job_id` を返してLLMへ制御を戻す | `SUBAGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS` |
| `[subagent]` | `background_progress_push_interval_seconds` | 待機中、人間向けに経過秒数・反復回数・進捗メモをチャットへ直接送る間隔（秒） | `SUBAGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS` |
| `[subagent]` | `background_llm_timeout_max_retries` | 実行中のLLM呼び出しがタイムアウトした場合、モデルを再構築して同じ反復を再試行する最大回数 | `SUBAGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES` |
| `[user_response_timeouts]` | `approval_seconds` | `approve_plan`／`run_script`・`execute_python_code`の個別実行確認でユーザー応答を待つ秒数。`0`で無期限待ち | `APPROVAL_TIMEOUT_SECONDS` |
| `[user_response_timeouts]` | `ask_user_question_seconds` | `AskUserQuestion`（自由記述質問。`labels`省略時は単一入力、指定時は複数項目フォーム）でユーザー応答を待つ秒数。`0`で無期限待ち | `ASK_USER_QUESTION_TIMEOUT_SECONDS` |
| `[user_response_timeouts]` | `ask_user_choice_seconds` | `ask_user_choice`（選択肢質問）でユーザー応答を待つ秒数。`0`で無期限待ち | `ASK_USER_CHOICE_TIMEOUT_SECONDS` |
| `[plan]` | `allow_badge_unlock` | Plan Mode バッジの双方向切り替えを許可するか | `PLAN_ALLOW_BADGE_UNLOCK` |
| `[plan]` | `require_planner_dispatch` | `create_plan` を呼ぶ前に、同一ターンで `dispatch_agent(agent_type="planner")` が完了していることを必須にするか（`true`が既定。`planner`サブエージェントに計画草案を作らせてから`create_plan`を呼ぶ運用を強制する） | `PLAN_REQUIRE_PLANNER_DISPATCH` |
| `[plan]` | `reset_approval_on_recreate` | 承認済み（Edit Automatically）状態で`create_plan`を再度呼んだ際、`plan_approved`を無条件でリセットしてPlan Modeへ戻すか。`false`なら承認状態を維持したままstepsだけ差し替える | `PLAN_RESET_APPROVAL_ON_RECREATE` |
| `[plan]` | `reset_approval_on_new_message` | 新しいユーザーメッセージを受け取るたびに`plan_approved`を無条件でリセットしてPlan Modeへ戻すか。`false`なら承認状態をメッセージをまたいで維持する（`thinking_loop_guard`のリトライ上限到達後、ユーザーが続行メッセージを送っても再承認が不要になる） | `PLAN_RESET_APPROVAL_ON_NEW_MESSAGE` |
| `[plan]` | `auto_approve` | `approve_plan`呼び出し時にユーザーへの承認/却下確認を一切行わず自動承認するか（`false`が既定。書き込み系ツールが人の確認なしに実行されるため、無人自動化用途以外での使用は推奨しない） | `PLAN_AUTO_APPROVE` |
| `[default_workdir]` | `dir` | エージェントの既定の作業ディレクトリのベース。実際の書き込み・`run_script`のcwdはこの配下の自セッション専用サブディレクトリ`_tmp_<name>`に限定される（`dir`直下への書き込みは許可されない） | `DEFAULT_WORKDIR` |
| `[default_workdir]` | `allow_sandbox_dir` | セッション分離の外側で常時書き込みを許可する追加ディレクトリのリスト（`[{"dir": "パス", "allow_entries": [["スキル名","スクリプトファイル名"], ...]}, ...]`形式）。各要素の`allow_entries`を空リストにすると、対象を問わずそのディレクトリへ無制限に書き込み可能になる。登録したディレクトリはスレッドを問わず常時書き込み可能になる点に注意（既定は空リストで無効） | `ALLOW_SANDBOX_DIR` |
| `[default_workdir]` | `retention_days` | 上記 `dir` 配下のファイル保持日数（0以下で自動削除無効） | `DEFAULT_WORKDIR_RETENTION_DAYS` |
| `[default_workdir]` | `cleanup_interval_hours` | default_workdir 自動削除チェック間隔（時間） | `DEFAULT_WORKDIR_CLEANUP_INTERVAL_HOURS` |
| `[log]` | `dir` | ログ出力先 | `LOG_DIR` |
| `[log]` | `level` | ログの詳細度。`info`（現行仕様、ツール呼び出しの概要のみ）／`debug`（ツール呼び出しの全引数・全結果・LLM応答本文・thinkingまで記録）／`none`（ログを一切生成しない） | `LOG_LEVEL` |
| `[log]` | `clear_on_startup` | 起動のたびに `app.log` を空にしてから書き始めるか（`false`＝従来通り追記） | `LOG_CLEAR_ON_STARTUP` |
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
| `[thinking_loop_guard]` | `empty_response_max_retries` | メインエージェントの空応答（tool_callsもcontentも無い応答）を検知した際の再試行最大回数 | `THINKING_LOOP_GUARD_EMPTY_RESPONSE_MAX_RETRIES` |
| `[thinking_loop_guard]` | `nudge_messages` | ループ検知後に注入する注意メッセージ（複数指定可） | `THINKING_LOOP_GUARD_NUDGE_MESSAGES` |
| `[context_trim]` | `enabled` | 古い `ToolMessage` を切り詰めてプリフィル遅延を抑える機能の有効/無効 | `CONTEXT_TRIM_ENABLED` |
| `[context_trim]` | `trigger_total_tokens` | トリムを発動させる閾値（Claude APIのcontext editing、`clear_tool_uses_20250919`のtrigger.value相当）。直近1回のLLM呼び出しのtotal_tokensがこの値未満のうちは発動しない。0以下なら常に発動 | `CONTEXT_TRIM_TRIGGER_TOTAL_TOKENS` |
| `[context_trim]` | `keep_recent_tool_messages` | 全文保持する直近 `ToolMessage` の件数 | `CONTEXT_TRIM_KEEP_RECENT_TOOL_MESSAGES` |
| `[context_trim]` | `trim_ai_messages` | `ToolMessage`だけでなく`AIMessage`（モデル自身の思考本文・tool_calls引数）も切り詰め対象にするか。`execute_python_code`のcode引数へファイル本文を書き写す使い方をすると`ToolMessage`側だけの切り詰めでは1リクエスト入力が膨らみ続けるため既定で有効 | `CONTEXT_TRIM_AI_MESSAGES` |
| `[context_trim]` | `keep_recent_ai_messages` | 全文保持する直近 `AIMessage` の件数（`trim_ai_messages=true`の場合のみ意味を持つ） | `CONTEXT_TRIM_KEEP_RECENT_AI_MESSAGES` |
| `[context_trim]` | `truncated_max_chars` | 切り詰め対象 `ToolMessage` の残す最大文字数 | `CONTEXT_TRIM_TRUNCATED_MAX_CHARS` |
| `[context_trim]` | `duplicate_guard_tool_max_chars` | Read/Glob/Grep/json_query/analyze_image（`[file_tools_duplicate_guard]`の対象ツール）の `ToolMessage` にだけ適用する切り詰め文字数（`truncated_max_chars`の代わりに使う） | `CONTEXT_TRIM_DUPLICATE_GUARD_TOOL_MAX_CHARS` |
| `[context_compaction]` | `enabled` | 会話履歴の自動要約・圧縮機能（ClaudeCodeのcompact相当）の有効/無効 | `CONTEXT_COMPACTION_ENABLED` |
| `[context_compaction]` | `token_threshold` | メインエージェントの累積トークン数（圧縮発火のたびに0へリセット）がこの値を超えたら圧縮する条件（1リクエストあたりの上限ではない） | `CONTEXT_COMPACTION_TOKEN_THRESHOLD` |
| `[context_compaction]` | `single_request_token_threshold` | 直近1回のLLM呼び出しのtotal_tokensがこの値を超えたら圧縮する条件（累積条件とのOR判定） | `CONTEXT_COMPACTION_SINGLE_REQUEST_TOKEN_THRESHOLD` |
| `[context_compaction]` | `keep_recent_turns` | 圧縮時に丸ごと保持する直近のユーザーターン数 | `CONTEXT_COMPACTION_KEEP_RECENT_TURNS` |
| `[context_compaction]` | `min_messages_to_compact` | 会話全体のメッセージ数がこの件数未満なら圧縮しない安全弁 | `CONTEXT_COMPACTION_MIN_MESSAGES_TO_COMPACT` |
| `[context_compaction]` | `compaction_prompt_path` | 要約を指示するプロンプト本文（Markdown）のパス | `CONTEXT_COMPACTION_PROMPT_PATH` |
| `[context_compaction]` | `summary_source_max_chars` | 要約対象の古い`ToolMessage`を要約LLMへ渡す前に切り詰める文字数（`[context_trim]`とは別枠） | `CONTEXT_COMPACTION_SUMMARY_SOURCE_MAX_CHARS` |
| `[context_compaction]` | `pre_note_threshold` | 圧縮発火前に`write_thread_note`への書き出しを促す注意メッセージを注入する、直近1回のLLM呼び出しのtotal_tokens閾値（0以下で無効化） | `CONTEXT_COMPACTION_PRE_NOTE_THRESHOLD` |
| `[context_compaction]` | `pre_note_warning_text` | 上記閾値到達時に注入する注意メッセージの文言 | `CONTEXT_COMPACTION_PRE_NOTE_WARNING_TEXT` |
| `[path_memory]` | `dir` | パスメモリー機能のレジストリファイル（`<thread_id>.json`）保存先 | `PATH_MEMORY_DIR` |
| `[path_memory]` | `retention_days` | パスメモリーのレジストリファイル保持日数 | `PATH_MEMORY_RETENTION_DAYS` |
| `[path_memory]` | `cleanup_interval_hours` | パスメモリーの自動削除チェック間隔（時間） | `PATH_MEMORY_CLEANUP_INTERVAL_HOURS` |
| `[path_memory]` | `max_entries` | 1会話あたりのパスメモリー登録上限件数 | `PATH_MEMORY_MAX_ENTRIES` |
| `[auth]` | `enabled` | ログイン認証機能のON/OFF（`false`＝現状通りログイン不要） | `AUTH_ENABLED` |
| `[auth]` | `require_password` | 認証ON時、パスワード一致を必須にするか（`false`＝ユーザー名のみで通す） | `AUTH_REQUIRE_PASSWORD` |
| `[chat_log]` | `enabled` | 会話ログ（ユーザー発言・AI最終応答）のテキストファイル記録の有効/無効 | `CHAT_LOG_ENABLED` |
| `[chat_log]` | `dir` | 会話ログの保存先ルートディレクトリ | `CHAT_LOG_DIR` |
| `[thread_store]` | `enabled` | 会話スレッド一覧（画面左サイドバー）・再開機能の有効/無効 | `THREAD_STORE_ENABLED` |
| `[thread_store]` | `db` | スレッド一覧・Step履歴の保存先SQLiteファイル（`data/checkpoints.sqlite`とは別ファイル） | `THREAD_STORE_DB` |
| `[thread_store]` | `retention_days` | スレッド一覧の保持日数（0以下で無期限保持、既定）。削除してもLangGraph側の会話状態自体は消えない | `THREAD_STORE_RETENTION_DAYS` |
| `[thread_store]` | `cleanup_interval_hours` | 上記の自動削除チェックの実行間隔（時間） | `THREAD_STORE_CLEANUP_INTERVAL_HOURS` |
| `[chat_starters]` | `prompts` | チャット開始時に表示する定型文ボタン（クリックでそのまま送信、複数指定可） | `CHAT_STARTER_PROMPTS` |
| `[checkpointer]` | `op_timeout_seconds` | LangGraphの会話状態SQLite（`[paths].checkpoint_db`）に対する1回あたりの操作（aget_tuple/aput_writes等）タイムアウト秒数。超過時はcheckpointer再構築へフォールバック | `CHECKPOINTER_OP_TIMEOUT_SECONDS` |
| `[checkpointer]` | `close_timeout_seconds` | checkpointer再構築時に旧DB接続をクローズする際のタイムアウト秒数 | `CHECKPOINTER_CLOSE_TIMEOUT_SECONDS` |
| `[checkpointer]` | `shutdown_drain_timeout_seconds` | アプリシャットダウン時、保留中の非同期タスクの完了を待つ最大秒数 | `CHECKPOINTER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` |
| `[mcp]` | `enabled` | MCPサーバー自動接続機能の有効/無効（既定値。`.locohane/settings.json` の `"mcp"."enabled"` があればそちらが優先） | `MCP_ENABLED` |
| `[mcp]` | `settings_path` | `.locohane/settings.json` のパス | `MCP_SETTINGS_PATH` |
| `[mcp]` | `connect_timeout_seconds` | 1サーバーあたりの起動（プロセス起動+initialize+tools/list）のタイムアウト秒数 | `MCP_CONNECT_TIMEOUT_SECONDS` |
| `[mcp]` | `call_timeout_seconds` | MCPツール（tools/call）1回あたりのタイムアウト秒数 | `MCP_CALL_TIMEOUT_SECONDS` |
| `[ui]` | `max_display_messages` | チャット画面に描画するメッセージの最大件数（表示専用の間引き、`0`で無制限） | `UI_MAX_DISPLAY_MESSAGES` |
| `[ui]` | `max_display_side_steps` | サイドパネルに描画するツール呼び出し等のStepの最大件数（表示専用の間引き、`0`で無制限） | `UI_MAX_DISPLAY_SIDE_STEPS` |
| `[ui]` | `token_usage_warn_threshold` | トークン使用量カードの「リクエスト1回あたり」行の合計トークン数がこの値以上でオレンジ太字表示（`0`以下で無効） | `UI_TOKEN_USAGE_WARN_THRESHOLD` |
| `[ui]` | `token_usage_alert_threshold` | 同上、この値以上で赤太字表示（`token_usage_warn_threshold`より優先、`0`以下で無効） | `UI_TOKEN_USAGE_ALERT_THRESHOLD` |
| `[websocket]` | `ping_interval_seconds` | ブラウザ⇔サーバー間WebSocket（Socket.IO）の生存確認ping送信間隔秒数。`[llm].stream_chunk_timeout_seconds`（LLMサーバーとの通信）とは別レイヤー | `WEBSOCKET_PING_INTERVAL_SECONDS` |
| `[websocket]` | `ping_timeout_seconds` | 直近pingへの応答をこの秒数待っても受信できない場合に切断とみなす。LLM応答待ちでイベントループがブロッキング気味の時間帯（`dispatch_agent`の長時間実行中等）に短すぎると誤切断しやすい | `WEBSOCKET_PING_TIMEOUT_SECONDS` |

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

## 会話スレッド一覧・再開（左サイドバー）

`[thread_store] enabled = true`（既定）にすると、画面左に過去の会話一覧が
サイドバー表示され、選ぶとLangGraphの会話状態ごとその続きを再開できる
（ChatGPT/Claude 同様）。実装は `src/thread_store.py`（Chainlit の
`BaseDataLayer` を実装した軽量な独自ストア、`data/chat_threads.sqlite`）と
`frontend/src/components/Sidebar.tsx`。

- **所有者の分け方**: `[auth] enabled = false`（既定）では全会話を
  `anonymous` という1つのバケットにまとめ、単一の一覧として表示する
  （`[chat_log]` と同じ挙動）。`[auth] enabled = true` ではログインユーザー
  ごとに自分のスレッドのみが見える。
- **一覧・リネーム・削除**: 独自の `GET/PUT/DELETE /locohane/threads*`
  エンドポイント（`app.py`）を使う。Chainlit公式の `/project/threads*` は
  匿名モードで常に401になり使えないため採用していない。
- **再開の仕組み**: Chainlit自身のスレッドID（`cl.context.session.thread_id`）
  をそのままLangGraphのチェックポイント分離キーとして使う（`on_chat_start`）。
  再開時は同じIDが渡されるため、LangGraph側の会話状態が自動的に引き継がれる。
  `@cl.on_chat_resume` は `on_chat_start` の軽量版で、ウェルカムメッセージ等は
  再送しない（過去のメッセージは resume 時に自動再生されるため）。
- **匿名モードでの再開有効化**: Chainlit本体の `resume_thread()`
  （`chainlit/socket.py`）は `session.user` が `None` だと無条件で
  早期returnする設計のため、匿名モードのままではスレッド再開が発火しない。
  `app.py` の `_patch_chainlit_anonymous_resume()` が、匿名モード時のみ
  `chainlit.socket.resume_thread` をモジュール関数単位で差し替え、再開時に
  一時的な `anonymous` ユーザーを補ってから元の実装を呼ぶ。
- **添付ファイル・埋め込み画像の永続化**: `provide_download`/`analyze_image`の
  `show_in_chat=True` 等の添付ファイル、および回答本文へ直接埋め込まれた画像は、
  `data/elements/`（`[elements]`参照）へ実体をコピー保存し、再開したスレッドや
  プロセス再起動後も表示できる（`src/thread_store.py` の `create_element`/
  `get_element`/`delete_element`、`app.py` の
  `_embed_local_images_as_session_urls`）。ただし `analyze_image` を
  `show_in_chat=False`（既定）で呼んだ場合はチャットUIへ表示しないため、
  この永続化とは無関係。

---

## 作業ディレクトリの切り替え（ツールバー）

`run_script`/`execute_python_code` が読み書きする作業ディレクトリは、既定では
サンドボックス化された `[default_workdir]` 配下の `_tmp_<name>`
（前述「データの保存場所」参照）だが、チャット入力欄ツールバーの
フォルダアイコン（`frontend/src/components/WorkDirButton.tsx`）から、
会話（スレッド）単位でユーザー自身の実プロジェクトフォルダ等へ絶対パスで
切り替えられる。

- **入力方式**: OSネイティブのフォルダ選択ダイアログではなく、絶対パス文字列を
  直接入力するポップオーバー方式（サーバーとブラウザが別マシンの構成だと
  ネイティブダイアログがサーバー側にしか表示されずクライアントが固まって
  見える問題があったため）。「既定値に戻す」で `config.ini` の
  `[default_workdir].dir` ベースの設定へ戻せる。
- **変更可能なタイミング**: 新規チャットで一度もメッセージを送信していない間、
  または未送信のまま再開したスレッドに限る。送信済みのチャットではアイコンが
  無効化される（送信後に作業フォルダを変えると挙動が分かりにくくなるため）。
- **検証とフォールバック**: 設定時にサーバー側で実際にディレクトリ一覧
  取得・一時ファイルの作成/書き込み/削除を試みて読み書き可否を判定する
  （`src/tools/check_work_dir_status.py` の `probe_workdir_access`）。存在しない・
  読み取り不可・（書き込みが必要な場面では）書き込み不可と判定された場合は、LLMが確認を
  怠っても安全側に倒れるよう自動的に `default_workdir` へフォールバックする。
  判定結果は `check_work_dir_status` ツールでも確認できる。
- **スコープ**: `cl.user_session`（会話単位）に保存されるだけで、
  `config.ini` 自体は変更されない。他の会話・他ユーザーには影響しない。

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

`web-search` スキル（Tavily APIによるWeb検索）は、スキル専用の
`skills/web-search/scripts/.env` に `TAVILY_API_KEY` を設定した場合のみ、
ユーザーがこのスキルを実行した時に限り `https://api.tavily.com` へ通信します。
未設定（`.env` 自体が無い、または空）の場合は一切通信せず、設定手順を示す
エラーメッセージを返すだけです。

### サードパーティ製Agent Skillsのライセンス

`skills/`・`.locohane/skills/` 配下にGitHub等で公開されているサードパーティ製の
Agent Skills（SKILL.md一式）を追加導入した場合、そのスキル自体のライセンス・
著作権表示は本プロジェクトのMIT Licenseとは別に、そのスキルの配布元が定める
条件に従います。ユーザーが任意に追加する外部コンテンツであり、本プロジェクトの
同梱物ではないため、`THIRD_PARTY_LICENSES.md`の自動集計・本プロジェクトの
ライセンス保証（上記「依存パッケージ」節）の対象外です。導入前に配布元の
LICENSEファイル・SKILL.mdのfrontmatter（`license`キー）を確認し、商用利用の
可否・帰属表示義務の有無を個別にチェックしてください。スキルが追加で外部
パッケージに依存する場合、そのパッケージのライセンスも別途確認が必要です
（`requirements.txt`への追加は本リポジトリ直下 `CLAUDE.md`「修正・実装時
ルール」に従うこと）。

### 商用利用時のチェックリスト

- [ ] 使用する **GGUF モデルのライセンス** を確認する（→ 上記「llama-server 起動例」の注記）
- [ ] 依存を追加・更新したら `tools/gen_licenses.py` で告知ファイルを再生成する
- [ ] サードパーティ製Agent Skillsを追加した場合、そのスキル自体と追加依存
      パッケージのライセンスを個別に確認する（上記「サードパーティ製Agent
      Skillsのライセンス」参照）

---

## 免責事項

本ソフトウェアは「現状のまま」（as-is）で提供され、明示的または黙示的な保証はありません。
著作者または著作権者は、本ソフトウェアの使用またはそれ以外の行為について、本ソフトウェアに
起因するかぎり一切の責任を負いません。いかなる状況下においても、本ソフトウェアの使用により
直接・間接的に生じたいかなる損害（データ消失、システム障害、ビジネス損失、機会損失を含む）
について、著作者または著作権者は責任を負いません。

上記は本ソフトウェアに適用される [LICENSE](LICENSE)（MIT License）の無保証・免責条項の
要約です。詳細な法的文言は LICENSE 本文が優先します。
