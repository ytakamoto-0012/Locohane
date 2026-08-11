"""設定ローダー。

役割:
- config.ini を読み込み、LLM 接続情報と全保存先パスを 1 つの frozen dataclass に集約する。
- 環境変数が設定されていればそれで上書きする（config.ini の値より優先）。
- data 配下（checkpoints / uploads / logs / memory）のディレクトリを起動時に作成する。

このファイル自体は Agent Skills 仕様には対応しない（純粋なアプリ設定）。
「何がどこに溜まるか」をコードから追えるようにするための土台。
"""

from __future__ import annotations

import ast
import configparser
import json
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path

from . import memory

# プロジェクトルート（このファイルは <root>/src/config.py なので 2 つ上がルート）。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.ini"

# main_routing_strategy / sub_routing_strategy が取りうる値。
# src/llm.py の _select_endpoint() がこの文字列で分岐する。
LLM_ROUTING_STRATEGIES = frozenset({"round_robin", "random", "priority_failover", "sticky"})

# reasoning_format が取りうる値（llama-server の --reasoning-format と同じ）。
LLM_REASONING_FORMATS = frozenset({"none", "deepseek", "deepseek-legacy"})


@dataclass(frozen=True)
class LLMEndpoint:
    """LLM接続先1件分（[llm].main_url / sub_url の各要素）。

    Attributes:
        base_url: llama.cpp server（OpenAI 互換）のベース URL。
        api_key: LLM API キー。llama.cpp は認証不要のためダミー値でよい。
        model: 使用するモデル名（llama-server の --model / --alias と一致させる）。
    """

    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class Config:
    """アプリ全体の設定。すべて絶対パスに解決済みで保持する。

    load_config() によってのみ構築される frozen dataclass。

    Attributes:
        main_endpoints: メインエージェント用のLLM接続先リスト（[llm].main_url）。
            要素数1なら常にそれを使い、複数なら main_routing_strategy に
            従って呼び出しごとに選ぶ（src/llm.py の build_model/_select_endpoint
            参照）。
        main_routing_strategy: main_endpoints が複数ある場合の選び方
            （"round_robin"/"random"/"priority_failover"/"sticky" のいずれか）。
        sub_endpoints: サブエージェント（dispatch_agent）用のLLM接続先リスト
            （[llm].sub_url）。形式は main_endpoints と同じ。
        sub_routing_strategy: sub_endpoints が複数ある場合の選び方。
            形式は main_routing_strategy と同じ。
        temperature: 生成時のtemperature。
        top_p: 累積確率上位のみサンプリングする閾値。None なら未指定
            （llama-server既定に委ねる）。
        top_k: 上位k候補のみサンプリングする（llama.cpp拡張、OpenAI標準API
            には無いため extra_body 経由で渡す）。None なら未指定。
        repeat_penalty: 直近生成トークンの再出現を抑制する係数（llama.cpp
            拡張、extra_body 経由）。None なら未指定。
        frequency_penalty: 出現済みトークン全体への一律ペナルティ（OpenAI
            標準API）。None なら未指定。
        presence_penalty: 一度でも出現したトークンへの一律ペナルティ
            （OpenAI標準API）。None なら未指定。
        max_tokens: 1リクエストあたりの最大生成トークン数。None なら
            無制限（llama-server既定に委ねる）。
        dry_multiplier: DRY (Don't Repeat Yourself) サンプラーの強度
            （llama.cpp拡張、extra_body 経由）。repeat_penalty より長い
            フレーズ単位の反復に効く。None または 0.0 で無効。
        dry_base: DRY サンプラーの反復長に対するペナルティ指数増加率。
            None なら未指定（llama-server既定に委ねる）。
        dry_allowed_length: DRY サンプラーがこの文字数以下の反復を許容する
            閾値。None なら未指定。
        dry_penalty_last_n: DRY サンプラーが反復検出に遡って見るトークン数
            （-1 でコンテキスト全体）。None なら未指定。
        dry_sequence_breakers: DRY サンプラーが反復検出をリセットする区切り
            文字列のリスト。None なら未指定（llama-server既定に委ねる）。
        enable_thinking: Qwen3系モデルの thinking（reasoning、<think>ブロック）
            モードのON/OFF（llama.cpp拡張、extra_body の chat_template_kwargs
            経由）。None なら未指定でモデル・llama-server既定に委ねる。
            False にすると reasoning をオフにする。
        reasoning_format: thinkingタグの扱い方（llama.cpp拡張、extra_body
            経由。llama-server起動時の --reasoning-format に相当）。
            "none"/"deepseek"/"deepseek-legacy" のいずれか。None なら未指定で
            llama-server既定（auto）に委ねる。
        reasoning_budget: 思考に使えるトークン数の上限（llama.cpp拡張、
            extra_body 経由。llama-server起動時の --reasoning-budget に相当）。
            -1=無制限、0=即座に終了、N>0=上限トークン数。None なら未指定で
            llama-server既定（-1）に委ねる。
        reasoning_budget_message: reasoning_budget を使い切った際に思考終了
            タグの直前へ挿入するメッセージ（llama.cpp拡張、extra_body 経由。
            llama-server起動時の --reasoning-budget-message に相当）。None
            なら未指定（挿入しない）。
        track_token_usage: LLM応答のトークン使用量（入力/出力/合計）を
            取得するかどうか。True の場合 build_model（src/llm.py）が
            ChatOpenAI の stream_usage=True を有効化し、app.py・eval側で
            使用量を集計・表示できるようにする。llama-server が
            stream_options.include_usage 拡張に対応していない場合のみ
            False にする（その場合トークン数は表示されない）。
        request_timeout_seconds: LLMサーバーへのHTTPリクエストのタイムアウト
            秒数（httpx.Timeoutのread/write/poolに適用。connectは別途固定値を
            使う）。build_model（src/llm.py）がhttpx.AsyncClient/httpx.Client
            の生成時に渡す。ストリーミング中はチャンク到達のたびにタイマーが
            リセットされるため、正常な長時間生成は妨げない。ThinkingLoopDetected
            発生後のaclose失敗でクライアントが壊れたまま次のリクエストが応答
            ヘッダー待ちで無期限にハングした本番incidentへの対策
            （詳細はsrc/llm.pyのChatLlamaCpp._astream / build_model参照）。
        stream_chunk_timeout_seconds: ストリーミング中にチャンクが一定時間
            届かない場合のタイムアウト秒数（langchain_openaiのstream_chunk_
            timeout。request_timeout_secondsとは別物で「チャンクとチャンクの
            間隔」の上限）。build_model（src/llm.py）がChatLlamaCppの
            コンストラクタに渡す。大きなコンテキストのプロンプト処理(prefill)
            に時間がかかる環境ほどこの秒数に到達しやすい。
        skills_dir: スキル群を格納するディレクトリの絶対パス。
        agents_dir: エージェント種別定義（dispatch_agent の agent_type、
            ClaudeCode の .claude/agents/*.md 相当）を格納するディレクトリの
            絶対パス。*.md 1ファイル = 1種別、frontmatterで name/
            description/tools（省略時は既定ツール一式を継承）を指定する。
        project_locohane_dirs: プロジェクト固有の拡張ディレクトリ（ClaudeCode の
            .claude/ 相当）の絶対パスのリスト。既定 [.locohane]。複数パスは
            nudge_messages と同じJSON/Python風リスト形式（角カッコ＋改行複数行
            OK）で指定できる。この配下の skills/・agents/・LOCOHANE.md から
            locohane_skills_dirs/locohane_agents_dirs/project_instructions_paths
            を導出する（load_config() 参照）。
        locohane_skills_dirs: skills_dir に追加でマージ走査するディレクトリの
            絶対パスのリスト（project_locohane_dirs の各要素 / "skills"）。
            同名スキルが両方に存在する場合は後方（.locohane側）が優先される
            （scan_skills() 参照）。
        locohane_agents_dirs: agents_dir に追加でマージ走査するディレクトリの
            絶対パスのリスト（project_locohane_dirs の各要素 / "agents"）。
            同名定義が両方に存在する場合は後方（.locohane側）が優先される
            （scan_agent_types() 参照）。
        bin_path: run_script/execute_python_code のサブプロセスへ渡す PATH の
            先頭に追加するディレクトリの絶対パスのリスト（既定は空）。
            コマンド名を素の状態で叩く前提の外部バイナリをOS側のPATH登録なしで
            呼び出せるようにする（src/tools.py の _subprocess_env() 参照）。
            存在しないディレクトリは無視される。
        system_prompt_path: システムプロンプトのテンプレートファイル
            （{{skills}} にスキル一覧を差し込む）の絶対パス。
        project_instructions_paths: プロジェクト固有の追加指示ファイル
            （ClaudeCode の CLAUDE.md 相当）の絶対パスのリスト
            （project_locohane_dirs の各要素 / "LOCOHANE.md"）。1つも
            存在しなくてもエラーにはならず、システムプロンプトの
            {{project_instructions}} には「（プロジェクト固有の指示は
            ありません）」が差し込まれる（render_project_instructions_block()
            参照）。
        checkpoint_db: LangGraph の会話状態を永続化する SQLite ファイルの絶対パス。
        upload_dir: ユーザーがアップロードしたファイルの保存先絶対パス。
        log_dir: アプリケーションログの出力先絶対パス。
        log_level: ログの詳細度（"info"/"debug"/"none"のいずれか、大文字小文字は
            区別しない）。"info" は現行仕様（各ツール呼び出しの概要のみ）、
            "debug" はツール呼び出しの全引数・全結果・LLM応答本文・thinking
            （reasoning_content）まで記録、"none" はログを一切生成しない。
            app.py の _setup() がこの値に応じてロギングを設定する。
        log_clear_on_startup: 起動のたびに新しい app_*.log ファイルを作成する
            か、直近の既存ファイルに追記を試みるか。False（既定）なら直近の
            app_*.log への追記を試みる（既に log_max_lines を超えていれば
            その場で新しいファイルにローテーションする）。True なら起動の
            たびに必ず新しい日時つきファイルを作成する。log_level="none" の
            ときは意味を持たない。
        log_max_lines: 1つのログファイル（app_YYYYMMDD_HHMMSS[_N].log）が保持する
            最大行数。この行数を超えたら新しいファイルへローテーションする
            （src/log_rotation.py の LineCountRotatingFileHandler が使う）。
            0以下でローテーション無効化。log_level="none" のときは意味を
            持たない。
        log_retention_days: ローテーションで増え続ける古い app_*.log の
            保持日数。この日数を過ぎた（更新日時が古い）ファイルは自動削除
            する。0以下で無効化。同じ log_dir に evals/run_case.py が書く
            evals.log は対象外（cleanup 呼び出し側で pattern="app_*.log"
            を指定するため）。
        log_cleanup_interval_hours: 上記の自動削除チェックの実行間隔（時間）。
        chat_log_enabled: 会話ログ（ユーザー発言とAIの最終応答）をテキスト
            ファイルへ記録する機能の有効/無効（[chat_log].enabled）。
        chat_log_dir: 会話ログの保存先ルートディレクトリの絶対パス。
            実際には <chat_log_dir>/<ユーザー名>/<日付>_<thread_id>.log の
            構成で書き出す（src/chat_log.py 参照）。
        chat_starter_prompts: チャット開始時に表示する定型文ボタンの候補
            リスト（[chat_starters].prompts）。クリックするとそのまま
            メッセージとして送信される。空リストならボタンを表示しない。
        default_workdir: エージェントの既定の作業ディレクトリ（run_script の
            cwd、スクリプトが生成するファイルの出力先の基準）。Chainlit の
            ChatSettings でセッション単位の作業ディレクトリが指定されな
            かった場合に使われる。run_script 専用ではなくエージェント全体の
            作業拠点という位置づけのため、他の run_script 実行設定とは
            分けて保存先パス群に含める。
        memory_dir: 永続メモリー（User/Feedback/Project/Reference）の
            保存先ルートディレクトリの絶対パス。配下に4種のtype
            サブディレクトリと索引ファイル MEMORY.md を持つ
            （src/memory.py 参照）。
        help_path: help ツールが読み込んで返すヘルプ本文（ユーザー向け、
            Markdown）ファイルの絶対パス。
        upload_retention_days: アップロードファイルの保持日数。この日数を
            過ぎた（更新日時が古い）ファイルは自動削除する。0以下で無効化。
        upload_cleanup_interval_hours: アップロードファイル自動削除の
            チェック間隔（時間）。起動時にも1回チェックする。
        chainlit_files_retention_days: Chainlit自身のセッションファイル
            ディレクトリ（`.files/<セッションID>/`。show_image・回答本文への
            画像埋め込みが使う）の保持日数。ディレクトリ単位で削除する
            （src/cleanup.py の cleanup_old_dirs 参照）。0以下で無効化。
        chainlit_files_cleanup_interval_hours: 上記の自動削除チェック間隔
            （時間）。起動時にも1回チェックする。
        image_max_long_side_pixels: 画像をLLMへ渡す前に縮小する際の、長辺の
            ピクセル数の上限。0以下、または画像の長辺が既にこの値以下の
            場合は縮小しない（src/images.py の to_data_url 参照）。
        image_jpeg_quality: 上記の縮小時に再エンコードするJPEG品質（1-95）。
        image_inline_preview_max_long_side_pixels: LLMの回答本文（Markdown
            テーブルのセル等）へ直接埋め込む画像プレビューの、長辺ピクセル数の
            上限。image_max_long_side_pixels（Vision向け）とは別の、意図的に
            小さい値（表示用サムネイルの帯域・容量を抑える目的。
            app.py の _embed_local_images_as_session_urls 参照）。
        image_inline_preview_jpeg_quality: 上記プレビューの再エンコード品質
            （1-95）。
        default_workdir_retention_days: default_workdir 直下に溜まり続ける
            ファイルの保持日数。この日数を過ぎた（更新日時が古い）ファイルは
            自動削除する。0以下で無効化。ユーザーが ChatSettings で指定した
            セッション単位の work_dir は対象外。
        default_workdir_cleanup_interval_hours: default_workdir 自動削除の
            チェック間隔（時間）。起動時にも1回チェックする。
        path_memory_dir: パスメモリー（src/path_memory.py）が
            会話ごとのレジストリファイル（<thread_id>.json）を保存する
            ルートディレクトリの絶対パス。
        path_memory_retention_days: パスメモリーの保持日数。この日数を
            過ぎたレジストリファイルは自動削除する。0以下で無効化。
        path_memory_cleanup_interval_hours: パスメモリー自動削除のチェック
            間隔（時間）。起動時にも1回チェックする。
        path_memory_max_entries: パスメモリー1会話あたりの登録上限件数。
        script_timeout: run_script の実行タイムアウト秒数。
        script_python: run_script が .py スクリプトを起動する際に使う
            Python 実行ファイル。
        code_exec_enabled: execute_python_code ツール（LLMが生成した
            Pythonコードをその場で実行する）の有効/無効。False の場合、
            ツールは呼び出されてもエラー文字列を返すのみでコードは
            実行されない。
        script_background_max_runtime_seconds: run_script_background で
            起動したプロセスを強制終了するまでの上限秒数（script_timeout とは
            別軸。同期版 run_script より長時間の実行を想定した上限）。
        script_background_job_retention_seconds: run_script_background の
            ジョブが完了・失敗・タイムアウト等で終了した後、check_script_job で
            一度も取得されないまま registry に残ってよい秒数。超過分は次回の
            run_script_background 呼び出し時に破棄される。
        script_background_min_poll_interval_seconds: check_script_job が
            「実行中」ステータスを返した直後、同じジョブへの次の
            check_script_job 呼び出しを許可するまでの最短間隔秒数
            （src/tools.py 参照）。0以下で無効化（強制なし）。
        script_background_min_poll_message: 上記の最短間隔未満で
            check_script_job が呼ばれた際にLLMへ返すメッセージのテンプレート。
            .format() で {wait_remaining}/{job_id}/{min_interval} を
            埋め込む。空欄なら DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE
            を使う。
        script_plan_approval_exempt_scripts: run_script/run_script_background
            の計画承認（Plan Mode）を免除する、副作用のない読み取り専用
            スクリプトのホワイトリスト（{(スキル名, スクリプトファイル名), ...}）。
        file_tools_duplicate_guard_enabled: 読み取り専用の Read/Glob/Grep/
            json_query ツールを同一引数で繰り返し呼び出すのを防ぐガード機能の
            有効/無効。
        file_tools_duplicate_guard_max_calls: 同一シグネチャ（ツール名+引数）
            の呼び出しを何回まで許可するか。この回数に達した以降の呼び出しは
            エラーで拒否する。
        file_tools_duplicate_guard_carry_over_to_main: サブエージェント
            （dispatch_agent）内での呼び出し履歴を、メインエージェントの重複
            判定へ持ち越すかどうか。True なら両者で呼び出し集合を共有し、
            False なら別々に管理する（src/tools.py の _IN_SUBAGENT 参照）。
        main_agent_tool_guard_enabled: メインエージェント自身が
            main_agent_tool_guard_entries に登録済みのツールを直接呼び出せる
            回数を制限するガード機能の有効/無効。plan_approval_exempt_scripts
            は計画承認を免除するだけで直接呼び出し自体は妨げないため、
            render_pdf_pages.py→analyze_image のような「1件の重い調査」を
            メインエージェントが委譲せず自分で最後まで実行し続けてトークン
            上限に達する事例（src/tools.py の _guard_main_agent_tool_limit
            参照）を防ぐための汎用ガード。ビルトインツール名（Glob・
            analyze_image 等）も run_script 配下のスキルスクリプトも同じ
            リストへ登録できる（旧・Glob専用ガード main_agent_glob_guard は
            本ガードへ統合済み。既定で ["Glob", 1] を登録し、従来と同じ挙動を
            引き継ぐ）。
        main_agent_tool_guard_entries: 本ガードの対象エントリの集合
            （frozenset[tuple[str | tuple[str, str], int]]）。各要素は
            (対象, max_calls) のペア。対象が文字列1件（例: "Glob",
            "analyze_image"）ならビルトインツール名そのもの、2要素タプル
            （例: ("pdf-tools", "render_pdf_pages.py")）なら
            run_script/run_script_background 経由で呼ばれる
            (スキル名, スクリプトファイル名) の組を表す。max_calls は
            エントリごとに個別指定でき、メインエージェントが1ターンあたり
            そのエントリを何回まで直接呼び出せるかを表す（0以下はそのエントリを
            完全ブロックする＝1回も呼び出せない。本ガードはホワイトリスト方式の
            ため「登録した上で無制限」は意味を成さず、他の呼び出し回数ガード
            （_record_and_check_duplicate）とは0の意味が逆になる点に注意）。
            plan_approval_exempt_scripts とは独立したリストで、
            ここに登録されていないツール・スクリプトはガード対象外。空集合
            なら本ガード自体が事実上無効（何も登録されていないため）。
        graph_impl: ReAct ループの実装切替。"handwritten"（手書き
            StateGraph）または "prebuilt"（LangGraph の
            create_react_agent）。build_graph() が参照する。
        graph_recursion_limit: メインの ReAct ループ（agent→tools 遷移）の
            最大反復回数。LangGraph の recursion_limit にそのまま渡す
            （単位はノード遷移数で、subagent_max_iterations とは数え方が
            異なる）。
        graph_tool_max_parallel: メインエージェントのツール呼び出し
            （ImageAwareToolNode）を、1セッションあたり同時に何件まで
            並列実行してよいか。ToolNode は同一AIMessage内の複数tool_calls
            を asyncio.gather() で完全並列実行するため、共有リソース
            （llama-server・DB等）への同時アクセスや呼び出し順の乱れが
            起きうる（subagent_max_parallel と同じ理由づけのメイン
            エージェント版）。セッション（thread_id）ごとに独立した
            asyncio.Semaphore を持つ（src/tools.py の _TOOL_CALL_SEMAPHORES
            参照）ため、この値は「1セッションが同時に使える枠」の上限であり、
            複数セッション間の並列自体は妨げない。1以上はその値までに
            ガードし（既定1＝完全直列化）、0以下はガードを無効化して
            並列呼び出しをそのまま許可する。
        graph_token_guard_enabled: メインエージェントの1リクエストあたりの
            トークン量を監視し、上限が近づいたら引継ぎプロンプトの生成を
            促す機能の有効/無効（src.main_token_guard 参照）。
        graph_token_guard_soft_threshold: 直近1回のLLM応答の total_tokens が
            この値以上になったら、次のモデル呼び出しの入力へ
            graph_handoff_prompt_path の文言を差し込む。
        graph_handoff_prompt_path: 上記で差し込む文言（新しいチャットへの
            引継ぎ手順）のMarkdownファイルの絶対パス。
        subagent_max_iterations: dispatch_agent が内部で回す ReAct
            ループの最大反復回数（agent→tools 遷移の回数）。
        subagent_max_parallel: dispatch_agent ツールの実LLM呼び出しを、
            1セッションあたり同時に何件まで許可するか。単一インスタンスの
            llama-serverへdispatch_agentの並列リクエストが飛ぶとチェック
            ポイント破損（ToolMessage欠落によるValueError）が本番で発生した
            ための保険措置。セッション（thread_id）ごとに独立した
            asyncio.Semaphore を持つ（src/tools.py の
            _DISPATCH_AGENT_SEMAPHORES 参照）ため、この値は「1セッションが
            同時に使える枠」の上限であり、複数セッション間の並列自体は
            妨げない。1以上はその値までにガードし（既定1＝完全直列化）、
            0以下はガードを無効化して並列呼び出しをそのまま許可する（検証用）。
        subagent_token_guard_enabled: dispatch_agent 内のLLM応答の
            usage_metadata.total_tokens を監視し、閾値超過時に注意喚起
            （ソフト）→打ち切り（ハード）を行う機能の有効/無効。
            track_token_usage=False の場合は usage_metadata が取得できず
            実質発火しない（run_subagent 側で無条件に無効化される）。
        subagent_token_guard_soft_threshold: 直近1回のLLM呼び出しの
            total_tokens がこの値以上になったら、そのiterationのtool_calls
            は通常通り実行した上で、次のモデル呼び出し前に
            subagent_token_guard_soft_warning_text の注意メッセージを
            1回だけ注入する。
        subagent_token_guard_soft_warning_text: 上記ソフト閾値到達時に
            注入する注意メッセージの文言。
        subagent_token_guard_hard_threshold: ソフト警告後もなお
            total_tokens がこの値以上の応答が続いた場合、そのtool_calls
            は実行せず、それ以上model.ainvoke()を呼ばずに打ち切る
            （subagent_max_iterations到達時と同じ要約フォーマットで返す）。
            subagent_token_guard_soft_threshold以上の値を設定すること。
        subagent_empty_response_max_retries: dispatch_agent内のLLM応答が
            tool_callsも本文も空（LLMサーバー側の異常応答。本番ログ
            2026-07-23で確認）だった場合に再試行する最大回数。この回数を
            使い切ってもなお空の応答が続いた場合は、正常終了として空文字列を
            返さず、それまでに集めたツール実行結果を要約して打ち切る
            （subagent_max_iterations到達時と同じ要約フォーマット）。
        approval_timeout_seconds: create_plan/approve_plan の計画承認で
            ユーザーの応答を待つ秒数。未応答は安全側に倒してタイムアウト
            扱いにする。0以下は無期限待ち（タイムアウトしない）。
        ask_user_question_timeout_seconds: AskUserQuestion（自由記述の
            質問。labels省略時は単発質問、labels指定時は複数項目フォーム）
            でユーザーの応答を待つ秒数。0以下は無期限待ち。
        ask_user_choice_timeout_seconds: ask_user_choice（選択肢形式の
            質問）でユーザーの応答を待つ秒数。0以下は無期限待ち。
        plan_badge_allow_unlock: 送信ボタン付近の Plan Mode / Edit Automatically
            バッジをクリックした際、Plan Mode → Edit Automatically 方向への
            切り替え（ロック解除）も許可するか。False にすると Edit
            Automatically → Plan Mode 方向（ロック）のクリックのみ有効になり、
            ロック解除は approve_plan（ユーザー承認フロー）経由に限定される
            （config.ini の [plan].allow_badge_unlock 由来）。
        plan_reset_approval_on_recreate: 既に Edit Automatically（計画承認済み）の
            状態で create_plan が再度呼ばれた際、plan_approved を強制的に
            False へ戻す（Plan Mode へ戻す）か。True（既定）なら常に戻し
            approve_plan による再承認を必須にする。False なら承認済み状態を
            維持したまま steps だけ差し替える（未承認状態からの呼び出しは
            この設定に関わらず常に Plan Mode のまま）。
            （config.ini の [plan].reset_approval_on_recreate 由来）。
        thinking_loop_guard_enabled: LLM応答（thinking/本文）のストリーミング中に
            反復ループを検知したら生成を打ち切って再試行する機能の有効/無効。
        thinking_loop_guard_window_chars: ループ検知の判定対象に使う
            直近テキストのウィンドウ文字数。
        thinking_loop_guard_check_interval_chars: このバイト数増えるごとに
            再チェックする。
        thinking_loop_guard_confirm_count: 反復判定条件が連続で何回成立したら
            確定でループと判定するか（誤検知防止）。
        thinking_loop_guard_max_history_chars: 直近ウィンドウとの最長一致
            部分文字列を探す際に比較対象とする、過去履歴の上限文字数
            （MAX_K）。大きいほど長い周期の反復も検知できるが計算コストが
            増える。
        thinking_loop_guard_match_ratio_threshold: 直近ウィンドウと過去履歴の
            最長一致長をwindow_charsで割った値（match_ratio）がこの値を
            上回った場合にループ確定とする（真の反復ループと正当なJSON生成等を
            区別するため）。
        thinking_loop_guard_max_retries: ループ検知後、注意メッセージを注入して
            再試行する最大回数。
        thinking_loop_guard_nudge_messages: ループ検知後に注入する注意メッセージの
            候補リスト。リトライ回数に応じて順番に使い、使い切ったらランダムに
            選ぶ（src.llm.pick_loop_nudge_message 参照）。空リストなら組み込みの
            既定文言を使う。
        thinking_loop_guard_empty_response_max_retries: 無言終了（tool_calls も
            本文も空のAIMessage）を検知した場合、最終回答を促して再試行する
            最大回数。thinking_loop_guard_max_retries と合算予算
            （total_retries）を共有する。
        context_trim_enabled: 会話履歴中の古い ToolMessage を切り詰めて
            LLMへの入力を抑える機能の有効/無効（src.context_trim 参照）。
        context_trim_keep_recent_tool_messages: 全文保持する直近 ToolMessage
            の件数。これより古い ToolMessage のみ切り詰め対象にする。
        context_trim_truncated_max_chars: 切り詰め対象 ToolMessage /
            AIMessage の content を、先頭何文字まで残すか
            （超過分はマーカー文言に置換）。context_trim_duplicate_guard_tool_max_chars
            の対象ツール（Read/Glob/Grep/json_query/analyze_image）には適用されない。
        context_trim_duplicate_guard_tool_max_chars: Read/Glob/Grep/
            json_query/analyze_image（src.tools の _check_file_tools_duplicate
            等、同一引数での再呼び出しに上限回数があるツール）の ToolMessage
            にだけ適用する切り詰め文字数。これらのツールは上限到達時
            「会話履歴にある前回の実行結果を参照してください」と案内するが、
            前回結果が context_trim_truncated_max_chars（既定は小さめの値）で
            切り詰められていると、実際にはモデルへ渡っていない分を参照させる
            ことになり案内が機能しない。そのため通常より大きめの値を
            別枠で持たせる。
        context_trim_ai_messages: ToolMessage だけでなく AIMessage
            （モデル自身の思考本文と tool_calls の引数）も切り詰めるか。
        context_trim_keep_recent_ai_messages: 全文保持する直近 AIMessage
            の件数。これより古い AIMessage のみ切り詰め対象にする。
        context_compaction_enabled: メインエージェントの累積トークン数、または
            直近1回のLLM呼び出しのトークン数が閾値を超えたら会話履歴を要約して
            圧縮する機能の有効/無効（src.context_compaction 参照）。context_trim
            と異なり永続履歴（checkpointer上のメッセージ）自体を書き換える。
        context_compaction_token_threshold: 圧縮を発火させる、メインエージェントの
            累積 total_tokens（token_usage_cumulative_main、圧縮発火のたびに
            リセットされる）の閾値。直近1回のLLM呼び出し分だけで判定すると、
            context_trim による送信ペイロード削減の影響で閾値未満に収まり
            続け、圧縮が発火しないまま永続履歴だけが肥大化しうるため、
            累積値でも判定する（context_compaction_single_request_token_threshold
            とのOR判定）。track_token_usage=False の場合は判定材料が無いため
            この条件は実質発火しない。
        context_compaction_single_request_token_threshold: 圧縮を発火させる、
            直近1回のLLM呼び出しの total_tokens の閾値。会話全体の累積は
            低くても、1ターンで巨大なツール結果やファイル内容を一気に積む
            などして単発のリクエストがモデルのcontext window上限に迫る
            ケースを検知するためのもの。低パラメータモデルでは1リクエスト
            あたりcontext window未満に収める必要があるため、ツール結果1往復分
            の余裕を見てそれより低い値にすること。track_token_usage=False の
            場合は判定材料が無いためこの条件は実質発火しない。
        context_compaction_keep_recent_turns: 圧縮時に丸ごと保持する直近の
            ユーザーターン数（HumanMessage単位）。tool_calls とそれに
            対応する ToolMessage の対応関係を壊さないよう、この境界
            （直近N個目のHumanMessage直前）でのみ古い側を切り離す。
        context_compaction_min_messages_to_compact: 会話全体のメッセージ数が
            この件数未満なら、閾値を超えていても圧縮しない安全弁。
        context_compaction_prompt_path: 要約を指示するプロンプト本文
            （Markdown）の絶対パス。
        context_compaction_summary_source_max_chars: 要約対象の古い
            ToolMessage を要約LLMへ渡す前に切り詰める文字数。context_trim
            （プリフィル短縮が目的で、直近以外の全ツール結果に一律適用
            される小さめの値）とは別枠。要約は永続履歴を置き換える
            恒久的な操作のため、context_trim と同じ値を使うと要約対象
            ツール結果の情報がまとめて失われ、要約が内容の薄いものに
            なりうる（大量ファイル処理タスクでファイル名の列挙しか
            残らない等）。
        auth_enabled: ログイン認証機能のON/OFF（[auth].enabled）。True の場合、
            app.py がモジュール読み込み時に @cl.password_auth_callback を
            登録し、未ログインユーザーはチャット画面にアクセスできなくなる。
        auth_require_password: auth_enabled=True のとき、ユーザー名だけで
            なくパスワードの一致まで要求するかどうか（[auth].require_password）。
            False の場合、auth_users に登録済みのユーザー名であればパスワードの
            内容は問わない。
        auth_users: ログイン可能なユーザー名→パスワードの対応表。config.ini
            には対応するキーが存在せず、環境変数 AUTH_USERS（.env 推奨）
            のみから読む機密情報専用フィールド（他フィールドと異なり
            config.ini 側フォールバックを持たない）。
    """

    # --- LLM (llama.cpp server / OpenAI 互換) ---
    main_endpoints: tuple[LLMEndpoint, ...]
    main_routing_strategy: str
    sub_endpoints: tuple[LLMEndpoint, ...]
    sub_routing_strategy: str
    temperature: float
    top_p: float | None
    top_k: int | None
    repeat_penalty: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    max_tokens: int | None
    dry_multiplier: float | None
    dry_base: float | None
    dry_allowed_length: int | None
    dry_penalty_last_n: int | None
    dry_sequence_breakers: list[str] | None
    enable_thinking: bool | None
    reasoning_format: str | None
    reasoning_budget: int | None
    reasoning_budget_message: str | None
    track_token_usage: bool
    request_timeout_seconds: float
    stream_chunk_timeout_seconds: float
    llm_max_concurrent_requests: int

    # --- 保存先パス（すべて絶対パス） ---
    skills_dir: Path
    agents_dir: Path
    project_locohane_dirs: list[Path]
    locohane_skills_dirs: list[Path]
    locohane_agents_dirs: list[Path]
    bin_path: list[Path]
    system_prompt_path: Path
    project_instructions_paths: list[Path]
    checkpoint_db: Path
    upload_dir: Path
    log_dir: Path
    log_level: str
    log_clear_on_startup: bool
    default_workdir: Path
    memory_dir: Path
    plans_dir: Path
    help_path: Path

    # --- アップロードファイルの自動削除 ---
    upload_retention_days: int
    upload_cleanup_interval_hours: float

    # --- Chainlitセッションファイルディレクトリ（.files/）の自動削除 ---
    chainlit_files_retention_days: int
    chainlit_files_cleanup_interval_hours: float

    # --- 画像をLLMへ渡す前の縮小 ---
    image_max_long_side_pixels: int
    image_jpeg_quality: int

    # --- 回答本文へ埋め込む画像プレビューの縮小 ---
    image_inline_preview_max_long_side_pixels: int
    image_inline_preview_jpeg_quality: int

    # --- default_workdir 直下のファイルの自動削除 ---
    default_workdir_retention_days: int
    default_workdir_cleanup_interval_hours: float

    # --- パスメモリー（src/path_memory.py）の保存・自動削除 ---
    path_memory_dir: Path
    path_memory_retention_days: int
    path_memory_cleanup_interval_hours: float
    path_memory_max_entries: int

    # --- ログファイル（app_*.log）の行数ベースローテーション・自動削除 ---
    log_max_lines: int
    log_retention_days: int
    log_cleanup_interval_hours: float

    # --- 会話ログ（[chat_log]） ---
    chat_log_enabled: bool
    chat_log_dir: Path

    # --- チャット開始時の定型文ボタン（[chat_starters]） ---
    chat_starter_prompts: list[str]

    # --- run_script / execute_python_code 共通実行設定 ---
    script_timeout: int
    script_python: str
    code_exec_enabled: bool

    # --- run_script_background 用設定 ---
    script_background_max_runtime_seconds: int
    script_background_job_retention_seconds: int
    script_background_min_poll_interval_seconds: int
    script_background_min_poll_message: str

    # --- run_script/run_script_background の計画承認免除ホワイトリスト ---
    script_plan_approval_exempt_scripts: frozenset[tuple[str, str]]

    # --- Read/Glob/Grep/json_query 重複呼び出しガード（src/tools.py の _check_file_tools_duplicate） ---
    file_tools_duplicate_guard_enabled: bool
    file_tools_duplicate_guard_max_calls: int
    file_tools_duplicate_guard_carry_over_to_main: bool

    # --- メインエージェント自身の任意ツール直接呼び出し回数ガード（ビルトインツール名・
    #     run_script配下のスキルスクリプトの両方を、エントリごとの max_calls 付きで
    #     登録できる。Glob専用だった旧ガードもここへ統合済み。src/tools.py の
    #     _guard_main_agent_tool_limit） ---
    main_agent_tool_guard_enabled: bool
    main_agent_tool_guard_entries: frozenset[tuple[str | tuple[str, str], int]]

    # --- グラフ実装切替 ---
    graph_impl: str
    graph_recursion_limit: int
    graph_tool_max_parallel: int
    graph_token_guard_enabled: bool
    graph_token_guard_soft_threshold: int
    graph_handoff_prompt_path: Path

    # --- サブエージェント（dispatch_agent）設定 ---
    subagent_max_iterations: int
    subagent_max_parallel: int
    subagent_token_guard_enabled: bool
    subagent_token_guard_soft_threshold: int
    subagent_token_guard_soft_warning_text: str
    subagent_token_guard_hard_threshold: int
    subagent_empty_response_max_retries: int
    subagent_background_job_retention_seconds: int
    subagent_background_min_poll_interval_seconds: int
    subagent_background_min_poll_message: str
    subagent_background_inline_wait_max_seconds: int
    subagent_background_progress_push_interval_seconds: int
    subagent_background_llm_timeout_max_retries: int

    # --- ユーザー応答待ちタイムアウト（Chainlit の Ask*Message） ---
    approval_timeout_seconds: int
    ask_user_question_timeout_seconds: int
    ask_user_choice_timeout_seconds: int

    # --- Plan Mode / Edit Automatically バッジ（送信ボタン付近のUI） ---
    plan_badge_allow_unlock: bool
    plan_reset_approval_on_recreate: bool

    # --- LLM応答の反復ループ検知（src/llm.py の ChatLlamaCpp） ---
    thinking_loop_guard_enabled: bool
    thinking_loop_guard_window_chars: int
    thinking_loop_guard_check_interval_chars: int
    thinking_loop_guard_confirm_count: int
    thinking_loop_guard_max_history_chars: int
    thinking_loop_guard_match_ratio_threshold: float
    thinking_loop_guard_max_retries: int
    thinking_loop_guard_nudge_messages: list[str]
    thinking_loop_guard_empty_response_max_retries: int

    # --- 会話履歴トリミング（src/context_trim.py） ---
    context_trim_enabled: bool
    context_trim_keep_recent_tool_messages: int
    context_trim_truncated_max_chars: int
    context_trim_duplicate_guard_tool_max_chars: int
    context_trim_ai_messages: bool
    context_trim_keep_recent_ai_messages: int

    # --- 会話履歴の自動要約・圧縮（src/context_compaction.py） ---
    context_compaction_enabled: bool
    context_compaction_token_threshold: int
    context_compaction_single_request_token_threshold: int
    context_compaction_keep_recent_turns: int
    context_compaction_min_messages_to_compact: int
    context_compaction_prompt_path: Path
    context_compaction_summary_source_max_chars: int

    # --- ログイン認証（[auth]、機密情報は .env 側） ---
    auth_enabled: bool
    auth_require_password: bool
    auth_users: dict[str, str]

    # --- MCP（Model Context Protocol）サーバー接続（[mcp]、src/mcp_client.py） ---
    # .locohane/settings.json（Claude Code/Qwen Code の mcpServers 形式を参考にした
    # 設定ファイル、git管理対象）の "mcp" ブロックがあれば、ここに列挙する4値は
    # load_config() の末尾でその内容により上書きされる（config.ini/環境変数はデフォルト値）。
    mcp_enabled: bool
    mcp_settings_path: Path
    mcp_connect_timeout_seconds: float
    mcp_call_timeout_seconds: float

    # --- checkpointer（LangGraphの会話状態を永続化するSQLite接続）タイムアウト ---
    checkpointer_op_timeout_seconds: float
    checkpointer_close_timeout_seconds: float
    checkpointer_shutdown_drain_timeout_seconds: float

    # --- UI（フロントエンド表示、[ui]） ---
    # チャット画面メインスレッドの描画件数上限（0 = 無制限）。あくまで表示専用の
    # 間引きであり、LLMへ渡す会話コンテキストや会話ログには影響しない
    # （frontend/src/App.tsx / messageTree.ts 参照）。
    ui_max_display_messages: int
    # サイドパネル（右側、ツール呼び出し等のStep一覧）の描画件数上限（0 = 無制限）。
    # 上記と同様、表示専用の間引きでありLLMへ渡す会話コンテキストや会話ログには
    # 影響しない（frontend/src/App.tsx / messageTree.ts 参照）。
    ui_max_display_side_steps: int
    # トークン使用量カード（TokenUsageCard）の「リクエスト1回あたり」行で、
    # 直近1回のLLM呼び出しの合計トークン数（total）がこの値以上になったら、
    # 該当行をオレンジ太字で強調表示する（0以下で無効。frontend/src/components/
    # TokenUsageCard.tsx 参照）。
    ui_token_usage_warn_threshold: int
    # 上記と同じ判定対象で、この値以上になったら赤太字で強調表示する
    # （warn_threshold より優先。0以下で無効）。
    ui_token_usage_alert_threshold: int


def _resolve(base: Path, value: str) -> Path:
    """config.ini 内の相対パスをプロジェクトルート基準の絶対パスへ解決する。

    Args:
        base: 相対パスの基準ディレクトリ（通常は PROJECT_ROOT）。
        value: config.ini または環境変数から得た生のパス文字列。
            絶対パスであればそのまま使う。

    Returns:
        value が絶対パスならそれをそのまま Path 化したもの、相対パスなら
        base 基準で resolve() した絶対パス。
    """
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _as_bool(value: bool | str) -> bool:
    """config.ini のbool値、または環境変数由来の文字列をboolへ変換する。

    Args:
        value: config.ini から得た bool、または環境変数から得た文字列。

    Returns:
        value が bool ならそのまま。文字列なら "0"/"false"/"no"（大文字小文字
        を問わない）を False、それ以外を True として扱う。
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no")


def _as_optional_float(value: float | str | None) -> float | None:
    """config.ini の空欄、または環境変数の空文字列を None（未指定）として扱う。

    Args:
        value: config.ini から得た値、または環境変数から得た文字列。

    Returns:
        空欄・None なら None、それ以外は float に変換した値。
    """
    if value is None:
        return None
    if isinstance(value, float):
        return value
    text = str(value).strip()
    return float(text) if text else None


def _as_optional_int(value: int | str | None) -> int | None:
    """config.ini の空欄、または環境変数の空文字列を None（未指定）として扱う。

    Args:
        value: config.ini から得た値、または環境変数から得た文字列。

    Returns:
        空欄・None なら None、それ以外は int に変換した値。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text else None


def _as_optional_bool(value: bool | str | None) -> bool | None:
    """config.ini の空欄、または環境変数の空文字列を None（未指定）として扱う。

    Args:
        value: config.ini から得た値、または環境変数から得た文字列。

    Returns:
        空欄・None なら None、bool ならそのまま、それ以外は _as_bool と同じ
        規則（"0"/"false"/"no" を False、それ以外を True）で変換した値。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    return _as_bool(text) if text else None


def _as_optional_str_list(value: str | None) -> list[str] | None:
    """config.ini のカンマ区切り文字列を list[str] に変換する。空欄は None。

    Args:
        value: config.ini から得たカンマ区切り文字列、または環境変数由来の文字列。

    Returns:
        空欄・None なら None、それ以外はカンマ区切りで分割し前後の空白を
        取り除いた文字列のリスト。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return [item.strip() for item in text.split(",")]


def _as_optional_str(value: str | None) -> str | None:
    """config.ini の空欄、または環境変数の空文字列を None（未指定）として扱う。

    Args:
        value: config.ini から得た値、または環境変数から得た文字列。

    Returns:
        前後の空白を除いた文字列。空欄・None なら None。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_optional_reasoning_format(value: str | None) -> str | None:
    """[llm].reasoning_format の値を検証する。

    Args:
        value: config.ini から得た値、または環境変数から得た文字列。

    Returns:
        前後の空白を除いた文字列（LLM_REASONING_FORMATS のいずれか）。
        空欄・None なら None（未指定、llama-server既定の auto に委ねる）。

    Raises:
        ValueError: LLM_REASONING_FORMATS に無い値が指定された場合。
    """
    text = _as_optional_str(value)
    if text is None:
        return None
    if text not in LLM_REASONING_FORMATS:
        choices = "/".join(sorted(LLM_REASONING_FORMATS))
        raise ValueError(f"[llm].reasoning_format は {choices} のいずれかを指定してください（現在値: {text!r}）")
    return text


def _as_message_list(value: str | None) -> list[str]:
    """config.ini のJSON/Python風リスト値を list[str] に変換する。

    例: '["a", "b"]' や、末尾カンマを含む複数行の配列リテラル。
    json.loads ではなく ast.literal_eval を使うのは、末尾カンマ等の
    Python的な緩い記法（コピペしやすい）も許容するため。

    Args:
        value: config.ini から得たリスト形式の文字列、または環境変数由来の文字列。
            空欄・None なら空リストとして扱う。

    Returns:
        パースした文字列のリスト（空要素は除外）。

    Raises:
        ValueError: 値がリスト（配列）として解釈できない場合
            （構文エラー、またはリスト以外の型だった場合）。
    """
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"config.ini の値はJSON/Pythonのリスト（配列）形式で指定してください: {text!r}") from e
    if not isinstance(parsed, list):
        raise ValueError(f"config.ini の値はリスト（配列）形式で指定してください: {text!r}")
    return [str(item) for item in parsed if str(item).strip()]


# [llm].main_url / sub_url が config.ini に無い場合の既定値（1件のみ）。
_DEFAULT_LLM_URL = '[{"base_url": "http://localhost:8080/v1", "api_key": "dummy-not-used", "model": "local-model"}]'

# [scripts].background_min_poll_message が空の場合に使う既定メッセージ。
# check_script_job() が最短確認間隔未満での再呼び出しを検知した際にLLMへ
# 返す文字列のテンプレート。.format() で {wait_remaining}/{job_id}/
# {min_interval} を埋め込む（src/tools.py 参照）。
DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE = (
    "まだ確認間隔が短すぎます。あと約{wait_remaining}秒待ってから、"
    "改めて check_script_job(job_id={job_id!r}) を呼び直してください"
    "（最短確認間隔: {min_interval}秒）。"
)

# [subagent].background_min_poll_message が空の場合に使う既定メッセージ。
# check_dispatch_agent_job() が最短確認間隔未満での再呼び出しを検知した際に
# LLMへ返す文字列のテンプレート。プレースホルダーは
# DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE と同じ
# （.format() で {wait_remaining}/{job_id}/{min_interval} を埋め込む）。
DEFAULT_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE = (
    "まだ確認間隔が短すぎます。あと約{wait_remaining}秒待ってから、"
    "改めて check_dispatch_agent_job(job_id={job_id!r}) を呼び直してください"
    "（最短確認間隔: {min_interval}秒）。"
)


def _validate_poll_message_template(text: str) -> str:
    """[scripts].background_min_poll_message のテンプレート文字列を検証する。

    {wait_remaining}/{job_id}/{min_interval} 以外のプレースホルダーや
    書式指定の誤りを起動時に検出するため、ダミー値で実際に .format() を
    試してみる。

    Args:
        text: config.ini または環境変数から得たテンプレート文字列。

    Returns:
        検証済みの文字列（そのまま）。

    Raises:
        ValueError: .format(wait_remaining=.., job_id=.., min_interval=..)
            が失敗する場合（未知のプレースホルダー等）。
    """
    try:
        text.format(wait_remaining=1, job_id="dummy", min_interval=1)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(
            f"[scripts].background_min_poll_message のプレースホルダーが不正です: {text!r} ({e})"
        ) from e
    return text


def _as_llm_endpoints(value: str | None, key_name: str) -> tuple[LLMEndpoint, ...]:
    """[llm].main_url / sub_url のJSON/Python風リスト値を LLMEndpoint のタプルに変換する。

    例: '[{"base_url": "http://localhost:8080/v1", "api_key": "dummy-not-used",
    "model": "local-model"}]'。_as_message_list() と同じく ast.literal_eval を
    使い、末尾カンマ等の緩い記法を許容する。

    Args:
        value: config.ini から得たリスト形式の文字列、または環境変数由来の文字列。
        key_name: エラーメッセージに使う設定キー名（例: "main_url"）。

    Returns:
        パースした LLMEndpoint のタプル（1件以上）。

    Raises:
        ValueError: リスト（配列）として解釈できない、空リスト、要素が
            dict でない、または必須キー（base_url/model）が欠けている場合。
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"[llm].{key_name} は最低1件の接続先を指定してください: {value!r}")
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"[llm].{key_name} はJSON/Pythonのリスト（配列）形式で指定してください: {text!r}") from e
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"[llm].{key_name} は最低1件を含むリスト（配列）形式で指定してください: {text!r}")
    endpoints: list[LLMEndpoint] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError(f"[llm].{key_name} の各要素は " '{"base_url":..., "api_key":..., "model":...} の' f"dict にしてください: {item!r}")
        if not item.get("base_url") or not item.get("model"):
            raise ValueError(f"[llm].{key_name} の各要素には base_url と model が必須です: {item!r}")
        endpoints.append(
            LLMEndpoint(
                base_url=str(item["base_url"]),
                api_key=str(item.get("api_key") or "dummy-not-used"),
                model=str(item["model"]),
            )
        )
    return tuple(endpoints)


def _as_routing_strategy(value: str | None, key_name: str) -> str:
    """[llm].main_routing_strategy / sub_routing_strategy の値を検証する。

    Args:
        value: config.ini から得た文字列、または環境変数由来の文字列。
        key_name: エラーメッセージに使う設定キー名。

    Returns:
        前後の空白を除いた文字列（LLM_ROUTING_STRATEGIES のいずれか）。

    Raises:
        ValueError: LLM_ROUTING_STRATEGIES に無い値が指定された場合。
    """
    text = str(value or "").strip()
    if text not in LLM_ROUTING_STRATEGIES:
        choices = "/".join(sorted(LLM_ROUTING_STRATEGIES))
        raise ValueError(f"[llm].{key_name} は {choices} のいずれかにしてください: {value!r}")
    return text


def _as_path_list(value: str | None, base: Path) -> list[Path]:
    """config.ini のパス指定を list[Path] に変換する。

    従来通りの単一パス（角カッコなしのプレーンな文字列）と、
    nudge_messages と同じJSON/Python風リスト形式（角カッコ＋改行複数行OK、
    例: '[\n    "./a.md",\n    "./b.md",\n    ]'）の両方を許容する
    （後方互換のため、既存の config.ini を書き換えなくても動く）。

    Args:
        value: config.ini または環境変数から得た生の文字列。
        base: 相対パスの解決基準ディレクトリ（通常は PROJECT_ROOT）。

    Returns:
        _resolve() で絶対パス化した Path のリスト（空欄なら空リスト）。
    """
    text = str(value).strip() if value is not None else ""
    if not text:
        return []
    items = _as_message_list(text) if text.startswith("[") else [text]
    return [_resolve(base, item) for item in items]


def _parse_auth_users(value: str | None) -> dict[str, str]:
    """AUTH_USERS環境変数（Python風の [["user","pass"], ...] リテラル）を
    ユーザー名→パスワードの辞書へ変換する。

    _as_message_list と同様 ast.literal_eval を使う（末尾カンマ等の緩い
    記法も許容し、.env へのコピペを容易にするため）。

    Args:
        value: 環境変数 AUTH_USERS の生文字列。空欄・None なら空辞書。

    Returns:
        {ユーザー名: パスワード} の辞書（重複ユーザー名は後勝ち）。

    Raises:
        ValueError: リストとして解釈できない場合、または各要素が
            [ユーザー名, パスワード] の2要素配列でない場合。
    """
    if not value or not value.strip():
        return {}
    text = value.strip()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"AUTH_USERS はPythonのリスト形式で指定してください: {text!r}") from e
    if not isinstance(parsed, list):
        raise ValueError(f"AUTH_USERS はリスト（配列）形式で指定してください: {text!r}")
    users: dict[str, str] = {}
    for item in parsed:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(f"AUTH_USERS の各要素は [ユーザー名, パスワード] の2要素にしてください: {item!r}")
        users[str(item[0])] = str(item[1])
    return users


def _parse_plan_approval_exempt_scripts(value: str | None) -> frozenset[tuple[str, str]]:
    """config.ini の [scripts].plan_approval_exempt_scripts をパースする。

    run_script/run_script_background の計画承認（Plan Mode）を免除する、
    副作用のない読み取り専用スクリプトのホワイトリスト。Python風の
    [["スキル名","スクリプトファイル名"], ...] リテラルを _parse_auth_users
    と同様 ast.literal_eval で読む（末尾カンマ等の緩い記法も許容するため）。

    Args:
        value: config.ini から得たリスト形式の文字列、または環境変数由来の文字列。
            空欄・None なら空集合を返す。

    Returns:
        {(スキル名, スクリプトファイル名), ...} の frozenset。

    Raises:
        ValueError: リストとして解釈できない場合、または各要素が
            [スキル名, スクリプトファイル名] の2要素配列でない場合。
    """
    if not value or not value.strip():
        return frozenset()
    text = value.strip()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"plan_approval_exempt_scripts はPythonのリスト形式で指定してください: {text!r}") from e
    if not isinstance(parsed, list):
        raise ValueError(f"plan_approval_exempt_scripts はリスト（配列）形式で指定してください: {text!r}")
    entries: set[tuple[str, str]] = set()
    for item in parsed:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(f"plan_approval_exempt_scripts の各要素は [スキル名, スクリプトファイル名] の" f"2要素にしてください: {item!r}")
        entries.add((str(item[0]), str(item[1])))
    return frozenset(entries)


def _parse_main_agent_tool_guard_entries(value: str | None) -> frozenset[tuple[str | tuple[str, str], int]]:
    """config.ini の [main_agent_tool_guard].entries をパースする。

    各要素は [対象, max_calls] の2要素配列。max_calls をエントリごとに
    個別指定できるようにするため、plan_approval_exempt_scripts のような
    「対象だけの集合」ではなく「対象→上限回数」のペアの集合として持つ。
    対象（1つ目の要素）はさらに次の2種類のいずれかを許容する
    （メインエージェント自身の直接呼び出しを制限したい対象が、ビルトイン
    ツール名単体の場合と run_script 配下のスキルスクリプトの場合の
    両方があるため）:
      - 文字列1件（例: "Glob", "analyze_image"）: ビルトインツール名そのもの。
      - [スキル名, スクリプトファイル名] の2要素配列（例:
        ["pdf-tools","render_pdf_pages.py"]）: run_script/
        run_script_background 経由で呼ばれるスキルスクリプト。
    例: entries = [["Glob", 1], ["analyze_image", 2], [["pdf-tools","render_pdf_pages.py"], 1]]
    Python風のリストリテラルを ast.literal_eval で読む（末尾カンマ等の
    緩い記法も許容するため）。

    Args:
        value: config.ini から得たリスト形式の文字列、または環境変数由来の文字列。
            空欄・None なら空集合を返す。

    Returns:
        {(対象, max_calls), ...} の frozenset。対象はツール名の文字列、または
        (スキル名, スクリプトファイル名) のタプル。

    Raises:
        ValueError: リストとして解釈できない場合、各要素が [対象, max_calls] の
            2要素でない場合、対象が文字列でも2要素配列でもない場合、または
            max_calls が整数でない場合。
    """
    if not value or not value.strip():
        return frozenset()
    text = value.strip()
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"main_agent_tool_guard.entries はPythonのリスト形式で指定してください: {text!r}") from e
    if not isinstance(parsed, list):
        raise ValueError(f"main_agent_tool_guard.entries はリスト（配列）形式で指定してください: {text!r}")
    entries: set[tuple[str | tuple[str, str], int]] = set()
    for item in parsed:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(f"main_agent_tool_guard.entries の各要素は [対象, max_calls] の2要素にしてください: {item!r}")
        target, max_calls = item
        key: str | tuple[str, str]
        if isinstance(target, str):
            key = target
        elif isinstance(target, (list, tuple)) and len(target) == 2:
            key = (str(target[0]), str(target[1]))
        else:
            raise ValueError(
                "main_agent_tool_guard.entries の対象はツール名の文字列、または"
                f"[スキル名, スクリプトファイル名] の2要素にしてください: {target!r}"
            )
        if not isinstance(max_calls, int) or isinstance(max_calls, bool):
            raise ValueError(f"main_agent_tool_guard.entries の max_calls は整数にしてください: {max_calls!r}")
        entries.add((key, max_calls))
    return frozenset(entries)


def render_plan_approval_exempt_scripts_block(entries: frozenset[tuple[str, str]]) -> str:
    """system_prompt.md の {{plan_approval_exempt_scripts}} へ差し込むテキストを組み立てる。

    config.ini の [scripts].plan_approval_exempt_scripts（frozenset）は集合の
    ため反復順序が不定。プロンプトへ差し込む表示を安定させるため、
    スキル名→スクリプトファイル名の順にソートしてから箇条書きへ整形する。

    Args:
        entries: {(スキル名, スクリプトファイル名), ...}（config.script_plan_approval_exempt_scripts）。

    Returns:
        差し込み用テキスト。空集合の場合は「（登録なし）」を返す。
    """
    if not entries:
        return "（登録なし）"
    return "\n".join(f"- `{skill}` / `{script}`" for skill, script in sorted(entries))


def load_config(config_path: Path | None = None) -> Config:
    """config.ini を読み、環境変数で上書きした Config を返す。

    環境変数（設定されていれば config.ini より優先）:
      LLM_MAIN_URL / LLM_MAIN_ROUTING_STRATEGY / LLM_SUB_URL / LLM_SUB_ROUTING_STRATEGY
      LLM_TEMPERATURE
      LLM_TOP_P / LLM_TOP_K / LLM_REPEAT_PENALTY / LLM_FREQUENCY_PENALTY / LLM_PRESENCE_PENALTY / LLM_MAX_TOKENS
      LLM_DRY_MULTIPLIER / LLM_DRY_BASE / LLM_DRY_ALLOWED_LENGTH / LLM_DRY_PENALTY_LAST_N / LLM_DRY_SEQUENCE_BREAKERS
      LLM_ENABLE_THINKING / LLM_TRACK_TOKEN_USAGE
      SKILLS_DIR / AGENTS_DIR / PROJECT_LOCOHANE_DIR / SYSTEM_PROMPT_PATH / CHECKPOINT_DB / UPLOAD_DIR / LOG_DIR / LOG_LEVEL / LOG_CLEAR_ON_STARTUP / DEFAULT_WORKDIR / MEMORY_DIR / PLANS_DIR / HELP_PATH
      UPLOAD_RETENTION_DAYS / UPLOAD_CLEANUP_INTERVAL_HOURS
      PATH_MEMORY_DIR / PATH_MEMORY_RETENTION_DAYS / PATH_MEMORY_CLEANUP_INTERVAL_HOURS / PATH_MEMORY_MAX_ENTRIES
      SCRIPT_TIMEOUT / SCRIPT_PYTHON / SCRIPT_REQUIRE_APPROVAL
      CODE_EXECUTION_ENABLED / CODE_EXECUTION_REQUIRE_APPROVAL
      FILE_TOOLS_DUPLICATE_GUARD_ENABLED / FILE_TOOLS_DUPLICATE_GUARD_MAX_CALLS /
      FILE_TOOLS_DUPLICATE_GUARD_CARRY_OVER_TO_MAIN
      GRAPH_IMPL / GRAPH_RECURSION_LIMIT
      SUBAGENT_MAX_ITERATIONS / SUBAGENT_SYSTEM_PROMPT_PATH / SUBAGENT_MAX_PARALLEL
      SUBAGENT_TOKEN_GUARD_ENABLED / SUBAGENT_TOKEN_GUARD_SOFT_THRESHOLD /
      SUBAGENT_TOKEN_GUARD_HARD_THRESHOLD
      APPROVAL_TIMEOUT_SECONDS / ASK_USER_TEXT_TIMEOUT_SECONDS / ASK_USER_CHOICE_TIMEOUT_SECONDS
      PLAN_BADGE_ALLOW_UNLOCK
      THINKING_LOOP_GUARD_ENABLED / THINKING_LOOP_GUARD_WINDOW_CHARS /
      THINKING_LOOP_GUARD_CHECK_INTERVAL_CHARS / THINKING_LOOP_GUARD_COMPRESSION_RATIO_THRESHOLD /
      THINKING_LOOP_GUARD_CONFIRM_COUNT / THINKING_LOOP_GUARD_MAX_RETRIES /
      THINKING_LOOP_GUARD_NUDGE_MESSAGES
      CONTEXT_TRIM_ENABLED / CONTEXT_TRIM_KEEP_RECENT_TOOL_MESSAGES / CONTEXT_TRIM_TRUNCATED_MAX_CHARS
      CONTEXT_COMPACTION_ENABLED / CONTEXT_COMPACTION_TOKEN_THRESHOLD /
      CONTEXT_COMPACTION_KEEP_RECENT_TURNS / CONTEXT_COMPACTION_MIN_MESSAGES_TO_COMPACT /
      CONTEXT_COMPACTION_PROMPT_PATH
      AUTH_ENABLED / AUTH_REQUIRE_PASSWORD / AUTH_USERS（AUTH_USERS は .env 専用、
      config.ini 側フォールバックを持たない）
      CHAT_LOG_ENABLED / CHAT_LOG_DIR
      CHAT_STARTER_PROMPTS
      MCP_ENABLED / MCP_SETTINGS_PATH / MCP_CONNECT_TIMEOUT_SECONDS / MCP_CALL_TIMEOUT_SECONDS
      （これら4値は .locohane/settings.json の "mcp" ブロックがあればさらに
      上書きされる。config.ini/環境変数はその既定値という位置づけ）

    パス系の値はすべて _resolve() でプロジェクトルート基準の絶対パスへ
    解決し、checkpoint_db の親ディレクトリ・upload_dir・log_dir は
    存在しなければここで作成する（アプリ起動時に呼ぶことを想定）。

    Args:
        config_path: 読み込む config.ini のパス。省略時は
            <プロジェクトルート>/config.ini（DEFAULT_CONFIG_PATH）を使う。

    Returns:
        LLM 接続情報・各種保存先パス・run_script 実行設定を集約した
        frozen な Config インスタンス。

    Raises:
        FileNotFoundError: config_path（または既定の config.ini）が
            存在しない場合。
        configparser.Error: config.ini の構文が不正な場合。
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(path)
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    llm = parser["llm"] if parser.has_section("llm") else {}
    paths = parser["paths"] if parser.has_section("paths") else {}
    uploads = parser["uploads"] if parser.has_section("uploads") else {}
    chainlit_files = parser["chainlit_files"] if parser.has_section("chainlit_files") else {}
    images_section = parser["images"] if parser.has_section("images") else {}
    default_workdir_section = parser["default_workdir"] if parser.has_section("default_workdir") else {}
    path_memory = parser["path_memory"] if parser.has_section("path_memory") else {}
    log_section = parser["log"] if parser.has_section("log") else {}
    chat_log = parser["chat_log"] if parser.has_section("chat_log") else {}
    chat_starters = parser["chat_starters"] if parser.has_section("chat_starters") else {}
    scripts = parser["scripts"] if parser.has_section("scripts") else {}
    file_tools_duplicate_guard = parser["file_tools_duplicate_guard"] if parser.has_section("file_tools_duplicate_guard") else {}
    main_agent_tool_guard = parser["main_agent_tool_guard"] if parser.has_section("main_agent_tool_guard") else {}
    graph = parser["graph"] if parser.has_section("graph") else {}
    subagent = parser["subagent"] if parser.has_section("subagent") else {}
    timeouts = parser["user_response_timeouts"] if parser.has_section("user_response_timeouts") else {}
    plan_section = parser["plan"] if parser.has_section("plan") else {}
    thinking_loop_guard = parser["thinking_loop_guard"] if parser.has_section("thinking_loop_guard") else {}
    context_trim = parser["context_trim"] if parser.has_section("context_trim") else {}
    context_compaction = parser["context_compaction"] if parser.has_section("context_compaction") else {}
    auth = parser["auth"] if parser.has_section("auth") else {}
    mcp = parser["mcp"] if parser.has_section("mcp") else {}
    checkpointer = parser["checkpointer"] if parser.has_section("checkpointer") else {}
    ui = parser["ui"] if parser.has_section("ui") else {}

    project_locohane_dirs = _as_path_list(
        os.getenv("PROJECT_LOCOHANE_DIR", paths.get("project_locohane_dir", "./.locohane")),
        PROJECT_ROOT,
    )
    bin_path = _as_path_list(
        os.getenv("BIN_PATH", paths.get("bin_path", "")),
        PROJECT_ROOT,
    )

    cfg = Config(
        main_endpoints=_as_llm_endpoints(
            os.getenv("LLM_MAIN_URL", llm.get("main_url", _DEFAULT_LLM_URL)),
            "main_url",
        ),
        main_routing_strategy=_as_routing_strategy(
            os.getenv("LLM_MAIN_ROUTING_STRATEGY", llm.get("main_routing_strategy", "round_robin")),
            "main_routing_strategy",
        ),
        sub_endpoints=_as_llm_endpoints(
            os.getenv("LLM_SUB_URL", llm.get("sub_url", _DEFAULT_LLM_URL)),
            "sub_url",
        ),
        sub_routing_strategy=_as_routing_strategy(
            os.getenv("LLM_SUB_ROUTING_STRATEGY", llm.get("sub_routing_strategy", "round_robin")),
            "sub_routing_strategy",
        ),
        temperature=float(os.getenv("LLM_TEMPERATURE", llm.get("temperature", 0.3))),
        top_p=_as_optional_float(os.getenv("LLM_TOP_P", llm.get("top_p", ""))),
        top_k=_as_optional_int(os.getenv("LLM_TOP_K", llm.get("top_k", ""))),
        repeat_penalty=_as_optional_float(os.getenv("LLM_REPEAT_PENALTY", llm.get("repeat_penalty", ""))),
        frequency_penalty=_as_optional_float(os.getenv("LLM_FREQUENCY_PENALTY", llm.get("frequency_penalty", ""))),
        presence_penalty=_as_optional_float(os.getenv("LLM_PRESENCE_PENALTY", llm.get("presence_penalty", ""))),
        max_tokens=_as_optional_int(os.getenv("LLM_MAX_TOKENS", llm.get("max_tokens", ""))),
        dry_multiplier=_as_optional_float(os.getenv("LLM_DRY_MULTIPLIER", llm.get("dry_multiplier", ""))),
        dry_base=_as_optional_float(os.getenv("LLM_DRY_BASE", llm.get("dry_base", ""))),
        dry_allowed_length=_as_optional_int(os.getenv("LLM_DRY_ALLOWED_LENGTH", llm.get("dry_allowed_length", ""))),
        dry_penalty_last_n=_as_optional_int(os.getenv("LLM_DRY_PENALTY_LAST_N", llm.get("dry_penalty_last_n", ""))),
        dry_sequence_breakers=_as_optional_str_list(os.getenv("LLM_DRY_SEQUENCE_BREAKERS", llm.get("dry_sequence_breakers", ""))),
        enable_thinking=_as_optional_bool(os.getenv("LLM_ENABLE_THINKING", llm.get("enable_thinking", ""))),
        reasoning_format=_as_optional_reasoning_format(os.getenv("LLM_REASONING_FORMAT", llm.get("reasoning_format", ""))),
        reasoning_budget=_as_optional_int(os.getenv("LLM_REASONING_BUDGET", llm.get("reasoning_budget", ""))),
        reasoning_budget_message=_as_optional_str(
            os.getenv("LLM_REASONING_BUDGET_MESSAGE", llm.get("reasoning_budget_message", ""))
        ),
        track_token_usage=_as_bool(os.getenv("LLM_TRACK_TOKEN_USAGE", llm.get("track_token_usage", True))),
        request_timeout_seconds=float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", llm.get("request_timeout_seconds", 300))),
        stream_chunk_timeout_seconds=float(
            os.getenv(
                "LLM_STREAM_CHUNK_TIMEOUT_SECONDS",
                llm.get("stream_chunk_timeout_seconds", 120),
            )
        ),
        llm_max_concurrent_requests=int(
            os.getenv(
                "LLM_MAX_CONCURRENT_REQUESTS",
                llm.get("max_concurrent_requests", 1),
            )
        ),
        skills_dir=_resolve(PROJECT_ROOT, os.getenv("SKILLS_DIR", paths.get("skills_dir", "./skills"))),
        agents_dir=_resolve(PROJECT_ROOT, os.getenv("AGENTS_DIR", paths.get("agents_dir", "./agents"))),
        project_locohane_dirs=project_locohane_dirs,
        locohane_skills_dirs=[d / "skills" for d in project_locohane_dirs],
        locohane_agents_dirs=[d / "agents" for d in project_locohane_dirs],
        bin_path=bin_path,
        system_prompt_path=_resolve(
            PROJECT_ROOT, os.getenv("SYSTEM_PROMPT_PATH", paths.get("system_prompt_path", "./system_prompt/system_prompt.md"))
        ),
        project_instructions_paths=[d / "LOCOHANE.md" for d in project_locohane_dirs],
        checkpoint_db=_resolve(PROJECT_ROOT, os.getenv("CHECKPOINT_DB", paths.get("checkpoint_db", "./data/checkpoints.sqlite"))),
        upload_dir=_resolve(PROJECT_ROOT, os.getenv("UPLOAD_DIR", uploads.get("dir", "./data/uploads"))),
        log_dir=_resolve(PROJECT_ROOT, os.getenv("LOG_DIR", log_section.get("dir", "./data/logs"))),
        log_level=os.getenv("LOG_LEVEL", log_section.get("level", "info")).strip().lower(),
        log_clear_on_startup=_as_bool(os.getenv("LOG_CLEAR_ON_STARTUP", log_section.get("clear_on_startup", False))),
        default_workdir=_resolve(PROJECT_ROOT, os.getenv("DEFAULT_WORKDIR", default_workdir_section.get("dir", "./"))),
        memory_dir=_resolve(PROJECT_ROOT, os.getenv("MEMORY_DIR", paths.get("memory_dir", "./data/memory"))),
        plans_dir=_resolve(PROJECT_ROOT, os.getenv("PLANS_DIR", paths.get("plans_dir", "./data/plans"))),
        help_path=_resolve(PROJECT_ROOT, os.getenv("HELP_PATH", paths.get("help_path", "./system_prompt/help.md"))),
        upload_retention_days=int(os.getenv("UPLOAD_RETENTION_DAYS", uploads.get("retention_days", 7))),
        upload_cleanup_interval_hours=float(os.getenv("UPLOAD_CLEANUP_INTERVAL_HOURS", uploads.get("cleanup_interval_hours", 1))),
        chainlit_files_retention_days=int(os.getenv("CHAINLIT_FILES_RETENTION_DAYS", chainlit_files.get("retention_days", 7))),
        chainlit_files_cleanup_interval_hours=float(
            os.getenv("CHAINLIT_FILES_CLEANUP_INTERVAL_HOURS", chainlit_files.get("cleanup_interval_hours", 1))
        ),
        image_max_long_side_pixels=int(os.getenv("IMAGE_MAX_LONG_SIDE_PIXELS", images_section.get("max_long_side_pixels", 0))),
        image_jpeg_quality=int(os.getenv("IMAGE_JPEG_QUALITY", images_section.get("jpeg_quality", 85))),
        image_inline_preview_max_long_side_pixels=int(
            os.getenv("IMAGE_INLINE_PREVIEW_MAX_LONG_SIDE_PIXELS", images_section.get("inline_preview_max_long_side_pixels", 320))
        ),
        image_inline_preview_jpeg_quality=int(
            os.getenv("IMAGE_INLINE_PREVIEW_JPEG_QUALITY", images_section.get("inline_preview_jpeg_quality", 70))
        ),
        default_workdir_retention_days=int(os.getenv("DEFAULT_WORKDIR_RETENTION_DAYS", default_workdir_section.get("retention_days", 7))),
        default_workdir_cleanup_interval_hours=float(
            os.getenv("DEFAULT_WORKDIR_CLEANUP_INTERVAL_HOURS", default_workdir_section.get("cleanup_interval_hours", 1))
        ),
        path_memory_dir=_resolve(PROJECT_ROOT, os.getenv("PATH_MEMORY_DIR", path_memory.get("dir", "./data/path_memory"))),
        path_memory_retention_days=int(os.getenv("PATH_MEMORY_RETENTION_DAYS", path_memory.get("retention_days", 1))),
        path_memory_cleanup_interval_hours=float(os.getenv("PATH_MEMORY_CLEANUP_INTERVAL_HOURS", path_memory.get("cleanup_interval_hours", 1))),
        path_memory_max_entries=int(os.getenv("PATH_MEMORY_MAX_ENTRIES", path_memory.get("max_entries", 500))),
        log_max_lines=int(os.getenv("LOG_MAX_LINES", log_section.get("max_lines", 5000))),
        log_retention_days=int(os.getenv("LOG_RETENTION_DAYS", log_section.get("retention_days", 7))),
        log_cleanup_interval_hours=float(os.getenv("LOG_CLEANUP_INTERVAL_HOURS", log_section.get("cleanup_interval_hours", 1))),
        chat_log_enabled=_as_bool(os.getenv("CHAT_LOG_ENABLED", chat_log.get("enabled", False))),
        chat_log_dir=_resolve(PROJECT_ROOT, os.getenv("CHAT_LOG_DIR", chat_log.get("dir", "./data/logs_chat"))),
        chat_starter_prompts=_as_message_list(os.getenv("CHAT_STARTER_PROMPTS", chat_starters.get("prompts", ""))),
        script_timeout=int(os.getenv("SCRIPT_TIMEOUT", scripts.get("timeout", 60))),
        script_python=os.getenv("SCRIPT_PYTHON", scripts.get("python", "python")),
        code_exec_enabled=_as_bool(os.getenv("CODE_EXECUTION_ENABLED", scripts.get("code_execution_enabled", True))),
        script_background_max_runtime_seconds=int(
            os.getenv(
                "SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS",
                scripts.get("background_max_runtime_seconds", 3600),
            )
        ),
        script_background_job_retention_seconds=int(
            os.getenv(
                "SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS",
                scripts.get("background_job_retention_seconds", 1800),
            )
        ),
        script_background_min_poll_interval_seconds=int(
            os.getenv(
                "SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS",
                scripts.get("background_min_poll_interval_seconds", 20),
            )
        ),
        script_background_min_poll_message=_validate_poll_message_template(
            os.getenv(
                "SCRIPT_BACKGROUND_MIN_POLL_MESSAGE",
                scripts.get("background_min_poll_message", "") or DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE,
            )
        ),
        script_plan_approval_exempt_scripts=_parse_plan_approval_exempt_scripts(
            os.getenv(
                "SCRIPT_PLAN_APPROVAL_EXEMPT_SCRIPTS",
                scripts.get(
                    "plan_approval_exempt_scripts",
                    '[["excel-vba-read","read_vba.py"],["excel-read","read_excel.py"],'
                    '["excel-render","render_excel.py"],'
                    '["docx-read","read_docx.py"],["docx-render","render_docx.py"],'
                    '["pdf-tools","read_pdf.py"],["pdf-tools","render_pdf_pages.py"],'
                    '["pptx-read","read_pptx.py"],["pptx-inspect","inspect_pptx.py"],'
                    '["pptx-render","render_pptx.py"],'
                    '["web-search","search_web.py"]]',
                ),
            )
        ),
        file_tools_duplicate_guard_enabled=_as_bool(
            os.getenv(
                "FILE_TOOLS_DUPLICATE_GUARD_ENABLED",
                file_tools_duplicate_guard.get("enabled", True),
            )
        ),
        file_tools_duplicate_guard_max_calls=int(
            os.getenv(
                "FILE_TOOLS_DUPLICATE_GUARD_MAX_CALLS",
                file_tools_duplicate_guard.get("max_calls", 1),
            )
        ),
        file_tools_duplicate_guard_carry_over_to_main=_as_bool(
            os.getenv(
                "FILE_TOOLS_DUPLICATE_GUARD_CARRY_OVER_TO_MAIN",
                file_tools_duplicate_guard.get("carry_over_to_main", True),
            )
        ),
        main_agent_tool_guard_enabled=_as_bool(
            os.getenv(
                "MAIN_AGENT_TOOL_GUARD_ENABLED",
                main_agent_tool_guard.get("enabled", True),
            )
        ),
        main_agent_tool_guard_entries=_parse_main_agent_tool_guard_entries(
            os.getenv(
                "MAIN_AGENT_TOOL_GUARD_ENTRIES",
                main_agent_tool_guard.get("entries", '[["Glob", 1]]'),
            )
        ),
        graph_impl=os.getenv("GRAPH_IMPL", graph.get("implementation", "handwritten")),
        graph_recursion_limit=int(os.getenv("GRAPH_RECURSION_LIMIT", graph.get("recursion_limit", 50))),
        graph_tool_max_parallel=int(os.getenv("GRAPH_TOOL_MAX_PARALLEL", graph.get("max_parallel", 1))),
        graph_token_guard_enabled=_as_bool(os.getenv("GRAPH_TOKEN_GUARD_ENABLED", graph.get("token_guard_enabled", True))),
        graph_token_guard_soft_threshold=int(
            os.getenv(
                "GRAPH_TOKEN_GUARD_SOFT_THRESHOLD",
                graph.get("token_guard_soft_threshold", 49152),
            )
        ),
        graph_handoff_prompt_path=_resolve(
            PROJECT_ROOT,
            os.getenv(
                "GRAPH_HANDOFF_PROMPT_PATH",
                graph.get("handoff_prompt_path", "./system_prompt/handoff_prompt.md"),
            ),
        ),
        subagent_max_iterations=int(os.getenv("SUBAGENT_MAX_ITERATIONS", subagent.get("max_iterations", 6))),
        subagent_max_parallel=int(os.getenv("SUBAGENT_MAX_PARALLEL", subagent.get("max_parallel", 1))),
        subagent_token_guard_enabled=_as_bool(os.getenv("SUBAGENT_TOKEN_GUARD_ENABLED", subagent.get("token_guard_enabled", True))),
        subagent_token_guard_soft_threshold=int(
            os.getenv(
                "SUBAGENT_TOKEN_GUARD_SOFT_THRESHOLD",
                subagent.get("token_guard_soft_threshold", 40000),
            )
        ),
        subagent_token_guard_soft_warning_text=os.getenv(
            "SUBAGENT_TOKEN_GUARD_SOFT_WARNING_TEXT",
            subagent.get(
                "token_guard_soft_warning_text",
                "[システム通知: このタスクのトークン使用量が上限に近づいています。"
                "これ以上ツール呼び出しを追加せず、次の応答でこれまでに分かったことと"
                "未処理の残り（あれば）をまとめて回答してください]",
            ),
        ),
        subagent_token_guard_hard_threshold=int(
            os.getenv(
                "SUBAGENT_TOKEN_GUARD_HARD_THRESHOLD",
                subagent.get("token_guard_hard_threshold", 55000),
            )
        ),
        subagent_empty_response_max_retries=int(
            os.getenv(
                "SUBAGENT_EMPTY_RESPONSE_MAX_RETRIES",
                subagent.get("empty_response_max_retries", 2),
            )
        ),
        subagent_background_job_retention_seconds=int(
            os.getenv(
                "SUBAGENT_BACKGROUND_JOB_RETENTION_SECONDS",
                subagent.get("background_job_retention_seconds", 1800),
            )
        ),
        subagent_background_min_poll_interval_seconds=int(
            os.getenv(
                "SUBAGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS",
                subagent.get("background_min_poll_interval_seconds", 60),
            )
        ),
        subagent_background_min_poll_message=_validate_poll_message_template(
            os.getenv(
                "SUBAGENT_BACKGROUND_MIN_POLL_MESSAGE",
                subagent.get("background_min_poll_message", "") or DEFAULT_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE,
            )
        ),
        subagent_background_inline_wait_max_seconds=int(
            os.getenv(
                "SUBAGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS",
                subagent.get("background_inline_wait_max_seconds", 1800),
            )
        ),
        subagent_background_progress_push_interval_seconds=int(
            os.getenv(
                "SUBAGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS",
                subagent.get("background_progress_push_interval_seconds", 20),
            )
        ),
        subagent_background_llm_timeout_max_retries=int(
            os.getenv(
                "SUBAGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES",
                subagent.get("background_llm_timeout_max_retries", 3),
            )
        ),
        approval_timeout_seconds=int(os.getenv("APPROVAL_TIMEOUT_SECONDS", timeouts.get("approval_seconds", 300))),
        ask_user_question_timeout_seconds=int(os.getenv("ASK_USER_QUESTION_TIMEOUT_SECONDS", timeouts.get("ask_user_question_seconds", 60))),
        ask_user_choice_timeout_seconds=int(os.getenv("ASK_USER_CHOICE_TIMEOUT_SECONDS", timeouts.get("ask_user_choice_seconds", 90))),
        plan_badge_allow_unlock=_as_bool(os.getenv("PLAN_BADGE_ALLOW_UNLOCK", plan_section.get("allow_badge_unlock", True))),
        plan_reset_approval_on_recreate=_as_bool(
            os.getenv("PLAN_RESET_APPROVAL_ON_RECREATE", plan_section.get("reset_approval_on_recreate", True))
        ),
        thinking_loop_guard_enabled=_as_bool(os.getenv("THINKING_LOOP_GUARD_ENABLED", thinking_loop_guard.get("enabled", True))),
        thinking_loop_guard_window_chars=int(os.getenv("THINKING_LOOP_GUARD_WINDOW_CHARS", thinking_loop_guard.get("window_chars", 600))),
        thinking_loop_guard_check_interval_chars=int(
            os.getenv(
                "THINKING_LOOP_GUARD_CHECK_INTERVAL_CHARS",
                thinking_loop_guard.get("check_interval_chars", 150),
            )
        ),
        thinking_loop_guard_confirm_count=int(os.getenv("THINKING_LOOP_GUARD_CONFIRM_COUNT", thinking_loop_guard.get("confirm_count", 2))),
        thinking_loop_guard_max_history_chars=int(
            os.getenv(
                "THINKING_LOOP_GUARD_MAX_HISTORY_CHARS",
                thinking_loop_guard.get("max_history_chars", 4000),
            )
        ),
        thinking_loop_guard_match_ratio_threshold=float(
            os.getenv(
                "THINKING_LOOP_GUARD_MATCH_RATIO_THRESHOLD",
                thinking_loop_guard.get("match_ratio_threshold", 0.2),
            )
        ),
        thinking_loop_guard_max_retries=int(os.getenv("THINKING_LOOP_GUARD_MAX_RETRIES", thinking_loop_guard.get("max_retries", 2))),
        thinking_loop_guard_nudge_messages=_as_message_list(
            os.getenv("THINKING_LOOP_GUARD_NUDGE_MESSAGES", thinking_loop_guard.get("nudge_messages", ""))
        ),
        thinking_loop_guard_empty_response_max_retries=int(
            os.getenv(
                "THINKING_LOOP_GUARD_EMPTY_RESPONSE_MAX_RETRIES",
                thinking_loop_guard.get("empty_response_max_retries", 2),
            )
        ),
        context_trim_enabled=_as_bool(os.getenv("CONTEXT_TRIM_ENABLED", context_trim.get("enabled", True))),
        context_trim_keep_recent_tool_messages=int(
            os.getenv(
                "CONTEXT_TRIM_KEEP_RECENT_TOOL_MESSAGES",
                context_trim.get("keep_recent_tool_messages", 5),
            )
        ),
        context_trim_truncated_max_chars=int(
            os.getenv(
                "CONTEXT_TRIM_TRUNCATED_MAX_CHARS",
                context_trim.get("truncated_max_chars", 2000),
            )
        ),
        context_trim_duplicate_guard_tool_max_chars=int(
            os.getenv(
                "CONTEXT_TRIM_DUPLICATE_GUARD_TOOL_MAX_CHARS",
                context_trim.get("duplicate_guard_tool_max_chars", 2000),
            )
        ),
        context_trim_ai_messages=_as_bool(os.getenv("CONTEXT_TRIM_AI_MESSAGES", context_trim.get("trim_ai_messages", True))),
        context_trim_keep_recent_ai_messages=int(
            os.getenv(
                "CONTEXT_TRIM_KEEP_RECENT_AI_MESSAGES",
                context_trim.get("keep_recent_ai_messages", 3),
            )
        ),
        context_compaction_enabled=_as_bool(os.getenv("CONTEXT_COMPACTION_ENABLED", context_compaction.get("enabled", True))),
        context_compaction_token_threshold=int(
            os.getenv(
                "CONTEXT_COMPACTION_TOKEN_THRESHOLD",
                context_compaction.get("token_threshold", 60000),
            )
        ),
        context_compaction_single_request_token_threshold=int(
            os.getenv(
                "CONTEXT_COMPACTION_SINGLE_REQUEST_TOKEN_THRESHOLD",
                context_compaction.get("single_request_token_threshold", 60000),
            )
        ),
        context_compaction_keep_recent_turns=int(
            os.getenv(
                "CONTEXT_COMPACTION_KEEP_RECENT_TURNS",
                context_compaction.get("keep_recent_turns", 2),
            )
        ),
        context_compaction_min_messages_to_compact=int(
            os.getenv(
                "CONTEXT_COMPACTION_MIN_MESSAGES_TO_COMPACT",
                context_compaction.get("min_messages_to_compact", 10),
            )
        ),
        context_compaction_prompt_path=_resolve(
            PROJECT_ROOT,
            os.getenv(
                "CONTEXT_COMPACTION_PROMPT_PATH",
                context_compaction.get("compaction_prompt_path", "./system_prompt/compaction_prompt.md"),
            ),
        ),
        context_compaction_summary_source_max_chars=int(
            os.getenv(
                "CONTEXT_COMPACTION_SUMMARY_SOURCE_MAX_CHARS",
                context_compaction.get("summary_source_max_chars", 2000),
            )
        ),
        auth_enabled=_as_bool(os.getenv("AUTH_ENABLED", auth.get("enabled", False))),
        auth_require_password=_as_bool(os.getenv("AUTH_REQUIRE_PASSWORD", auth.get("require_password", True))),
        auth_users=_parse_auth_users(os.getenv("AUTH_USERS", "")),
        mcp_enabled=_as_bool(os.getenv("MCP_ENABLED", mcp.get("enabled", True))),
        mcp_settings_path=_resolve(
            PROJECT_ROOT,
            os.getenv("MCP_SETTINGS_PATH", mcp.get("settings_path", "./.locohane/settings.json")),
        ),
        mcp_connect_timeout_seconds=float(os.getenv("MCP_CONNECT_TIMEOUT_SECONDS", mcp.get("connect_timeout_seconds", 15))),
        mcp_call_timeout_seconds=float(os.getenv("MCP_CALL_TIMEOUT_SECONDS", mcp.get("call_timeout_seconds", 60))),
        checkpointer_op_timeout_seconds=float(
            os.getenv(
                "CHECKPOINTER_OP_TIMEOUT_SECONDS",
                checkpointer.get("op_timeout_seconds", 15),
            )
        ),
        checkpointer_close_timeout_seconds=float(
            os.getenv(
                "CHECKPOINTER_CLOSE_TIMEOUT_SECONDS",
                checkpointer.get("close_timeout_seconds", 3),
            )
        ),
        checkpointer_shutdown_drain_timeout_seconds=float(
            os.getenv(
                "CHECKPOINTER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS",
                checkpointer.get("shutdown_drain_timeout_seconds", 5),
            )
        ),
        ui_max_display_messages=int(os.getenv("UI_MAX_DISPLAY_MESSAGES", ui.get("max_display_messages", 50))),
        ui_max_display_side_steps=int(
            os.getenv("UI_MAX_DISPLAY_SIDE_STEPS", ui.get("max_display_side_steps", 50))
        ),
        ui_token_usage_warn_threshold=int(
            os.getenv("UI_TOKEN_USAGE_WARN_THRESHOLD", ui.get("token_usage_warn_threshold", 48000))
        ),
        ui_token_usage_alert_threshold=int(
            os.getenv("UI_TOKEN_USAGE_ALERT_THRESHOLD", ui.get("token_usage_alert_threshold", 64000))
        ),
    )

    # .locohane/settings.json の "mcp" ブロックがあれば、config.ini/環境変数由来の
    # 既定値をさらに上書きする（settings.json が最優先）。settings.json の
    # "mcpServers" 本体の読み込みは src/mcp_client.py 側で独立して行うため、
    # ここでは config.py が mcp_client.py に依存する循環importを避けるため、
    # このブロックだけを自前で読む（構文エラーは他のconfig読み込みと同様に伝播させる）。
    mcp_overrides = _load_mcp_global_overrides(cfg.mcp_settings_path)
    if mcp_overrides:
        cfg = replace(
            cfg,
            mcp_enabled=_as_bool(mcp_overrides.get("enabled", cfg.mcp_enabled)),
            mcp_connect_timeout_seconds=float(mcp_overrides.get("connectTimeoutSeconds", cfg.mcp_connect_timeout_seconds)),
            mcp_call_timeout_seconds=float(mcp_overrides.get("callTimeoutSeconds", cfg.mcp_call_timeout_seconds)),
        )

    # data 配下のディレクトリを確実に用意する（checkpoint_db は親ディレクトリを作る）。
    cfg.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    cfg.path_memory_dir.mkdir(parents=True, exist_ok=True)
    cfg.default_workdir.mkdir(parents=True, exist_ok=True)
    cfg.plans_dir.mkdir(parents=True, exist_ok=True)
    memory.ensure_dirs(cfg.memory_dir)

    return cfg


def _load_mcp_global_overrides(path: Path) -> dict:
    """.locohane/settings.json の "mcp" ブロック（全体挙動の上書き設定）を読む。

    src/mcp_client.py には依存しない自己完結の実装（循環import回避）。
    "mcpServers"（個々のサーバー定義）はここでは読まない
    （src/mcp_client.py が起動時に独立して読み込む）。

    Args:
        path: cfg.mcp_settings_path（既定 <root>/.locohane/settings.json）。

    Returns:
        "mcp" ブロックの内容（dict）。ファイル不在、または "mcp" キー
        自体が無ければ空の dict。

    Raises:
        json.JSONDecodeError: settings.json の構文が不正な場合
            （config.ini 同様、設定ミスを起動時に検出するためfail fastする）。
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("mcp", {}) if isinstance(data, dict) else {}


_CONFIG_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def expand_config_vars(template: str, config: Config) -> str:
    """テンプレート文字列内の ``${フィールド名}`` を Config の実際の値へ展開する。

    システムプロンプトやツールのdocstringに config.ini の値（例: 反復回数の
    上限）をハードコードせず参照させるための汎用機構。変数名は Config
    dataclass の属性名をそのまま使う（例: ``${subagent_max_iterations}``）。
    属性名は「セクション名_キー名」で一意になるよう命名されているため、
    セクション修飾なしのフラットな名前で衝突しない。

    Args:
        template: ``${変数名}`` を含む文字列。
        config: 値の取得元となる Config インスタンス。

    Returns:
        ``${変数名}`` を実際の値の文字列表現に置き換えた文字列。

    Raises:
        ValueError: テンプレート中に Config に存在しないフィールド名が
            参照されている場合。設定ミス（typo等）を起動時に検出できるよう
            fail-fast する。
    """
    values = {f.name: getattr(config, f.name) for f in fields(config)}

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"未定義のconfig変数が参照されています: ${{{name}}}")
        return str(values[name])

    return _CONFIG_VAR_PATTERN.sub(_replace, template)
