"""Chainlit UI（LangGraph 実行のストリーミング表示 + ステップ可視化）。

役割:
- 起動時にスキルを走査（第1段階 Discovery）→ システムプロンプトへ注入 → グラフを構築。
- @cl.on_message で LangGraph を astream_events で回し、トークンをストリーミング表示。
- スキル読み込み(read_skill)・ファイル読み込み(read_skill_file)・スクリプト実行(run_script)・
  画像読み込み(view_image)・サブエージェント委譲(dispatch_agent)・ユーザーへの追加質問
  (AskUserQuestion/ask_user_choice)の各ツールコールを cl.Step として可視化
  （「今このスキルを読んでいます」が見える状態）。
  dispatch_agent は内部で独立した ReAct ループを回すが、その内部の思考過程・
  ツール呼び出しはグラフのトレースに乗らないため Step としては表示されない
  （最終回答のみが1つの Step として見える。コンテキスト節約が目的）。
- アップロードファイルは config.upload_dir に保存する。画像は data URL 化して
  LLMへ視覚情報として渡し、それ以外はパスをメッセージへ明示。
- ツール結果に生成ファイルの output_path（src/files.py 参照）が含まれる場合、
  画像なら cl.Image でインラインプレビュー、それ以外は cl.File でダウンロード
  可能な添付を自動送信する（pdf-tools/pptx-tools 等のファイル生成スキル、
  provide_download・show_image ツールに共通で効く）。

会話状態は AsyncSqliteSaver（config.checkpoint_db）で永続化。thread_id はセッション毎。
チェックポインタ（DB接続）・システムプロンプト・ツールはアプリ全体で 1 つを共有するが、
グラフ（LLMモデル・httpx.AsyncClientを含む）はセッション（タブ）ごとに個別に構築し
cl.user_session に保持する。停止ボタンは自セッションのLLMクライアントのみを強制
クローズし、他タブの処理には影響しない（src/llm.py の set_current_session /
aclose_active_llm_clients 参照）。
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

# 完全オフライン運用を保証するための防御的な無効化（import より前に設定）。
# 実態: このスタックの外部送信はすべて opt-in。
#   - Chainlit(2.x) は LITERAL_API_KEY を設定した時のみ Literal AI へ送信する。
#   - LangChain は LANGSMITH_API_KEY + トレーシング有効時のみ LangSmith へ送信する。
# いずれのキーも設定しなければ外部通信は発生しないが、事故防止のため明示的に切る。
# （setdefault なので、意図的に有効化したい場合は環境変数で上書きできる。）
os.environ.setdefault("CHAINLIT_TELEMETRY_ENABLED", "false")  # 1.x 互換。2.x では no-op
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

import uuid

import aiosqlite
import chainlit as cl
from chainlit.input_widget import TextInput
from chainlit.utils import utc_now
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from src.agent_types import render_agent_types_block, scan_agent_types
from src.chat_log import append_turn, build_log_path, resolve_log_username
from src.cleanup import cleanup_old_dirs, cleanup_old_files
from src.cleanup import run_cleanup_loop as cleanup_run_cleanup_loop
from src.config import expand_config_vars, load_config
from src.context_compaction import maybe_compact, should_compact
from src.files import extract_generated_file
from src.graph import EMPTY_RESPONSE_NUDGE, build_graph, is_empty_final_message
from src.images import is_image_file, to_data_url
from src.llm import (
    LLM_CONNECTION_ERRORS,
    ThinkingLoopDetected,
    _register_cancel_scope_watcher,
    aclose_active_llm_clients,
    build_model,
    describe_current_task,
    forget_session,
    pick_loop_nudge_message,
    recent_cancel_scope_breakage,
    set_current_session,
)
from src.log_rotation import LineCountRotatingFileHandler
from src.mcp_client import init_mcp_tools, shutdown_mcp_tools
from src.memory import render_memory_block
from src.project_instructions import render_project_instructions_block
from src.skills import build_system_prompt, render_skills_block, scan_skills
from src.subagent import is_truncated_result
from src.tools import (
    WorkDirAccessStatus,
    init_tools,
    probe_workdir_access,
    register_raw_unc_paths_in_text,
    toggle_plan_mode_from_ui,
)
from src.uploads import cleanup_old_uploads, run_cleanup_loop

# ツール名 → UI 表示ラベル（ステップ名として表示）。
_TOOL_LABELS = {
    "read_skill": "スキル読み込み",
    "read_skill_file": "ファイル読み込み",
    "run_script": "スクリプト実行",
    "analyze_image": "画像解析",
    "show_image": "画像表示",
    "dispatch_agent": "サブエージェント実行",
    "AskUserQuestion": "ユーザーへの質問（自由記述）",
    "ask_user_choice": "ユーザーへの質問（選択式）",
    "create_plan": "実行計画作成",
    "approve_plan": "計画承認確認",
    "update_task_progress": "進捗更新",
    "get_plan_status": "計画状態確認",
    "lock_plan_mode": "Plan Modeへ復帰",
    "create_memory": "メモリー保存",
    "update_memory": "メモリー更新",
    "delete_memory": "メモリー削除",
    "read_memory": "メモリー読み込み",
    "search_memory": "メモリー検索",
    "list_memories": "メモリー一覧",
    "help": "ヘルプ表示",
}

# アプリ全体で共有する状態（起動時に一度だけ構築）。
# グラフ（LLMモデル・httpxクライアントを含む）はここには含まない。
# セッション（タブ）ごとに個別構築し cl.user_session["graph"] に保持する
# （理由は本ファイル冒頭のモジュールdocstring参照）。
# chainlit run はモジュール読み込み直後に ensure_jwt_secret() を呼び、
# password_auth_callback が登録済みなら CHAINLIT_AUTH_SECRET の有無を検査する。
# そのため config 読み込みと認証コールバックの登録はモジュールのトップレベル
# （_setup() 内の遅延初期化より前）で行う必要がある。
_config = load_config()
# セッションごとのグラフ構築（on_chat_start）・再構築（_rebuild_graph）に
# 使い回すため保持しておく。
_system_prompt = None
_checkpointer = None


class _CheckpointerTimeout(Exception):
    """checkpointer操作のタイムアウト検知用例外。"""


class _CheckpointerConnectionClosed(_CheckpointerTimeout):
    """checkpointer操作の実行中にDB接続が閉じられていたことを示す例外。

    aiosqliteはバックグラウンドスレッドのキューに操作を積んだ後、実際に
    その操作が処理される前に別タスクが接続をclose()すると
    ValueError("no active connection" / "Connection closed") を送出する
    （アプリシャットダウン処理やcheckpointer再構築との競合。issue.md
    「DB接続切れエラー（no active connection）」参照）。on_message側は
    _CheckpointerTimeout を捕捉してcheckpointer再構築へ倒す設計のため、
    そのサブクラスとして扱い同じ復旧経路（turn_broken_exc +
    checkpointer_needs_rebuild）に合流させる。
    """


class _TimeoutGuardedSqliteSaver(AsyncSqliteSaver):
    """AsyncSqliteSaverをラップし、全操作にタイムアウトを付与する。

    langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver の self.lock は
    asyncio.Lock のインスタンスでタイムアウト機構がない。aiosqliteの
    バックグラウンドスレッドが固着するとロックが永久に解放されず、
    以降の全操作がハングする。本ラッパーは asyncio.timeout() で
    操作全体を包み、超過時に _CheckpointerTimeout を送出する。
    """

    async def _guarded(self, coro):
        """コルーチンを [checkpointer].op_timeout_seconds でタイムアウト包む。

        DB接続がクローズ済みの状態で操作が実行された場合の ValueError
        （_CheckpointerConnectionClosed のdocstring参照）もここで検知し、
        未処理のまま伝播させて on_message をクラッシュさせる代わりに
        _CheckpointerTimeout系の復旧経路へ変換する。
        """
        try:
            async with asyncio.timeout(_config.checkpointer_op_timeout_seconds):
                return await coro
        except ValueError as exc:
            if "no active connection" in str(exc) or "Connection closed" in str(exc):
                raise _CheckpointerConnectionClosed(str(exc)) from exc
            raise

    async def aget_tuple(self, config):
        return await self._guarded(super().aget_tuple(config))

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self._guarded(super().aput(config, checkpoint, metadata, new_versions))

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await self._guarded(super().aput_writes(config, writes, task_id, task_path))

    async def adelete_thread(self, thread_id):
        return await self._guarded(super().adelete_thread(thread_id))

    async def aget_delta_channel_history(self, *, config, channels):
        return await self._guarded(super().aget_delta_channel_history(config=config, channels=channels))


async def _build_checkpointer():
    """新しいチェックポインタを構築して返す（共通処理）。"""
    conn = await aiosqlite.connect(str(_config.checkpoint_db))
    saver = _TimeoutGuardedSqliteSaver(conn)
    await saver.setup()
    return saver


async def _rebuild_checkpointer(thread_id: str):
    """チェックポインタを新しい接続に差し替える（旧接続はベストエフォートclose）。"""
    global _checkpointer
    new_saver = await _build_checkpointer()
    old_saver = _checkpointer
    _checkpointer = new_saver
    if old_saver is not None:
        try:
            async with asyncio.timeout(_config.checkpointer_close_timeout_seconds):
                await old_saver.conn.close()
        except Exception:  # noqa: BLE001 - リークを許容してログのみ
            logging.getLogger(__name__).debug(
                "旧checkpointer接続のクローズに失敗しました（リークを許容）",
                exc_info=True,
            )


if _config.auth_enabled:
    if not os.environ.get("CHAINLIT_AUTH_SECRET"):
        raise RuntimeError(
            "config.ini の [auth] enabled=true ですが、.env に CHAINLIT_AUTH_SECRET が"
            "設定されていません。`chainlit create-secret` の出力を .env に追記してください。"
        )
    if not _config.auth_users:
        raise RuntimeError(
            "config.ini の [auth] enabled=true ですが、.env の AUTH_USERS が"
            "未設定または空です。少なくとも1組のユーザー名/パスワードを登録してください。"
        )

    @cl.password_auth_callback
    async def auth_callback(username: str, password: str) -> cl.User | None:
        """[auth] enabled=true のときのみ登録されるログイン検証コールバック。

        require_password=true: ユーザー名・パスワードともに一致必須
            （hmac.compare_digest でタイミング攻撃を軽減した定数時間比較）。
        require_password=false: AUTH_USERS に登録済みのユーザー名であれば
            パスワードの内容を問わず通す。
        """
        stored_password = _config.auth_users.get(username)
        if stored_password is None:
            return None
        if _config.auth_require_password and not hmac.compare_digest(password, stored_password):
            return None
        return cl.User(identifier=username)


@cl.on_app_startup
async def _on_app_startup() -> None:
    """プロセス起動時（最初のセッション接続より前）に一度だけ呼ばれる。

    .locohane/settings.json の mcpServers に定義された全MCPサーバーへ自動接続する
    （[mcp] enabled=false なら何もしない）。@cl.on_chat_start（セッションごとに
    複数回発火しうる）とは独立した、プロセス全体で1回のフック。
    """
    if not _config.mcp_enabled:
        logging.getLogger(__name__).info("MCP機能は無効化されています（[mcp].enabled=false）")
        return
    await init_mcp_tools(_config)


@cl.on_app_shutdown
async def _on_app_shutdown() -> None:
    """プロセス終了時に一度だけ呼ばれる。接続済みMCPサーバーのstdioサブプロセスを
    正常終了させ（src.mcp_client.shutdown_mcp_tools 参照）、checkpointerの
    DB接続を保留中タスクの後始末を待ってから閉じる
    （_close_checkpointer_gracefully 参照）。
    """
    await shutdown_mcp_tools()
    await _close_checkpointer_gracefully()


async def _close_checkpointer_gracefully() -> None:
    """保留中の非同期タスクを完了（またはキャンセル）させてから、checkpointerの
    DB接続を閉じる。

    先に接続を閉じてしまうと、その時点でまだ実行中だったcheckpointer操作
    （_TimeoutGuardedSqliteSaver.aget_tuple/aput_writes等。on_messageの
    ストリーム処理等が発行する）がaiosqliteのバックグラウンドスレッド上で
    ValueError: no active connection を送出してクラッシュする
    （issue.md「DB接続切れエラー（no active connection）」の原因1〜3）。
    このシャットダウンフック自身を除く全タスクを
    [checkpointer].shutdown_drain_timeout_seconds まで待ち、なお残って
    いるものはキャンセルして後始末の完了を待ってから接続を閉じる。
    """
    if _checkpointer is None:
        return
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        _done, still_pending = await asyncio.wait(pending, timeout=_config.checkpointer_shutdown_drain_timeout_seconds)
        if still_pending:
            logging.getLogger(__name__).warning(
                "シャットダウン: %d件の保留タスクが規定時間内に終わらなかったためキャンセルします",
                len(still_pending),
            )
            for task in still_pending:
                task.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)
    try:
        async with asyncio.timeout(_config.checkpointer_close_timeout_seconds):
            await _checkpointer.conn.close()
    except Exception:  # noqa: BLE001 - シャットダウン時はベストエフォート、ログのみ
        logging.getLogger(__name__).debug(
            "シャットダウン時のcheckpointer接続クローズに失敗しました（リークを許容）",
            exc_info=True,
        )


# public/settings/ 配下は Chainlit の /public/{filename:path} ルートで静的配信される
# （custom_build の SPA とは別経路）ため、ここに置いたテキストファイルを編集すれば
# フロントエンド/バックエンドの再ビルド無しで Welcome メッセージ・ヘッダー文言を変更できる。
SETTINGS_DIR = Path(__file__).resolve().parent / "public" / "settings"


def _load_settings_text(filename: str, default: str) -> str:
    """public/settings/ 配下のユーザー編集用テキストファイルを読み込む。

    ファイルが存在しない、または読み込めない場合は default を返す
    （ユーザーが直接編集する外部ファイルという境界を越える入力のため、
    欠落・破損時にアプリ起動を落とさないための最小限のフォールバック）。
    """
    try:
        return (SETTINGS_DIR / filename).read_text(encoding="utf-8").strip()
    except OSError:
        return default


# usage_metadata（langchain-openai の UsageMetadata dict）のキー →
# 累計辞書のキーの対応。on_chat_model_end のトークン集計で使う。
_USAGE_KEYS = (("input", "input_tokens"), ("output", "output_tokens"), ("total", "total_tokens"))


def _new_usage_totals() -> dict:
    """トークン使用量累計辞書の初期値（{"input","output","total"}を0で初期化）。"""
    return {"input": 0, "output": 0, "total": 0}


def _accumulate_usage(totals: dict, usage: dict) -> None:
    """usage_metadata の値を totals（{"input","output","total"}）へ加算する。

    Args:
        totals: 加算先の集計辞書（in-placeで更新する）。
        usage: AIMessage.usage_metadata（"input_tokens"/"output_tokens"/
            "total_tokens" を持つ dict）。
    """
    for key, src_key in _USAGE_KEYS:
        totals[key] += usage.get(src_key, 0) or 0


def _format_token_usage(call: dict, cumulative_main: dict, cumulative: dict) -> str:
    """直近のリクエスト1回分・メインエージェント累計・会話累計（メイン+サブ合算）を、
    サイドパネルで読みやすい複数行にまとめる。

    cumulative_main はサブエージェント（dispatch_agent）内部の呼び出しを含まない、
    メインエージェント自身のLLM呼び出しのみの累計。委譲がどれだけ会話コンテキストの
    節約に寄与しているかを、cumulative（合算値）との差でユーザーが確認できる。
    """
    return (
        f"🔢 トークン使用量\n\n"
        f"リクエスト1回あたり\n"
        f"入力: {call['input']:,} / 出力: {call['output']:,} / 合計: {call['total']:,}\n\n"
        f"メインエージェント累計\n"
        f"入力: {cumulative_main['input']:,} / 出力: {cumulative_main['output']:,} / "
        f"合計: {cumulative_main['total']:,}\n\n"
        f"会話累計（サブエージェント含む）\n"
        f"入力: {cumulative['input']:,} / 出力: {cumulative['output']:,} / 合計: {cumulative['total']:,}"
    )


# トークン使用量表示（🔢 プレフィックス）と同じ仕組みで、作業ディレクトリの状態を
# 通常のチャット欄ではなく右サイドバー専用領域へ表示するためのマーカー。
# フロントエンド側（frontend/src/utils/messageTree.ts）でこのプレフィックスを見て
# メインスレッドから除外し、サイドパネルへ抽出する。
WORK_DIR_PREFIX = "📁 作業ディレクトリ"


# public/settings/welcome.md が存在しない場合のフォールバック（{skills} はスキル一覧に置換される）。
_WELCOME_TEMPLATE_DEFAULT = (
    "Locohaneを起動しました。利用可能なスキル: {skills}\n\n" "スクリプト実行時の作業ディレクトリは、入力欄の📁アイコンから指定できます。"
)


def _format_work_dir_status(resolved: str | None, status: WorkDirAccessStatus | None = None) -> str:
    """作業ディレクトリの現在値・アクセス状況をサイドパネル表示用にまとめる。

    Args:
        resolved: ユーザー指定の作業ディレクトリ（絶対パス文字列）。None なら未設定。
        status: probe_workdir_access() の実測結果。None（未設定時、または
            プローブ未実行）の場合はパスのみを表示する。
    """
    if resolved is None:
        return f"{WORK_DIR_PREFIX}: 未設定（既定値 {_config.default_workdir} を使用）"
    if status is None or (status.exists and status.readable and status.writable):
        return f"{WORK_DIR_PREFIX}: {resolved}"
    if not status.exists:
        label = "存在しません（このPCから直接アクセスできません）"
    elif not status.readable:
        label = "アクセスできません（読み取り不可）"
    else:
        label = "読み取り専用（書き込み不可）"
    return (
        f"{WORK_DIR_PREFIX}: {resolved}\n"
        f"状態: {label}\n"
        f"※書き込みが必要な処理は既定フォルダ（{_config.default_workdir}）を自動的に使用します。"
    )


async def _setup() -> None:
    """スキル走査・ツール初期化など、全セッション共有資源の構築を一度だけ行う（冪等）。

    モジュール globals の _system_prompt が None でなければ即座に return する
    ため、複数回の @cl.on_chat_start 発火（複数セッション）でも
    重い初期化（DB接続・スキル走査）は1回しか実行されない。
    グラフ（LLMモデル・httpxクライアント）はここでは構築しない。
    セッションごとに @cl.on_chat_start が個別に build_graph() を呼ぶ
    （本ファイル冒頭のモジュールdocstring参照）。

    _config はモジュール読み込み時（トップレベル）で既に読み込み済み
    （password_auth_callback の登録判定に必要なため。詳細はモジュール
    globals 定義箇所のコメント参照）。ここでは流用するのみで再読み込みしない。

    実行内容:
    1. ログ出力先を設定する（_config はモジュール読み込み時に読み込み済み）。
    2. scan_skills() でスキルを走査し、build_system_prompt() で
       システムプロンプトを組み立てる（第1段階 Discovery）。
    3. init_tools() でツール（read_skill/read_skill_file/run_script/
       execute_python_code/dispatch_agent/メモリー系ツール）に skills ルート・
       サブエージェント設定・メモリールート等を注入する。
    4. AsyncSqliteSaver を構築し、checkpoint_db への接続をアプリ
       寿命で保持する。
    5. アップロードフォルダの期限切れファイルを1回削除し、以降は
       cleanup_interval_hours 間隔で削除を続けるバックグラウンドタスクを起動する
       （upload_retention_days が0以下なら無効化）。

    Args:
        なし。

    Returns:
        None。副作用としてモジュール globals（_system_prompt, _checkpointer）
        を設定する。
    """
    global _system_prompt, _checkpointer
    if _system_prompt is not None:
        return

    # ログをファイルへ（何がどこに溜まるかを追えるように log_dir に出す）。
    # 注意: chainlit run はモジュール読み込み前に自前で logging.basicConfig() を
    # 呼び root logger にハンドラを付けてしまうため、basicConfig() は仕様上
    # no-op になり FileHandler が root に付かない。そのため root logger へ
    # 明示的に addHandler する。
    #
    # config.log_level（config.ini の [paths].log_level）で詳細度を切り替える:
    #   "none"  - logging.disable() でプロセス全体のログ出力を無効化する
    #             （ハンドラ自体を作らない。data/logs/app_*.log は生成されない）。
    #   "info"  - 従来通り root logger を INFO でハンドラへ出す。
    #   "debug" - root logger を DEBUG にする。DEBUG レベルのログ（ツール呼び出しの
    #             全引数・全結果、LLM応答本文・thinkingを含む、tools.py/subagent.py/
    #             llm.py 側で発行）も同じハンドラに記録されるようになる。
    # ログファイルは data/logs/app_YYYYMMDD_HH.log のように日時つきの名前で
    # 出力され、config.log_max_lines（[log].max_lines）行を超えると新しい
    # 日時つきファイルへ自動的にローテーションする（LineCountRotatingFileHandler、
    # src/log_rotation.py）。config.log_clear_on_startup（[paths].log_clear_on_startup）
    # が True なら、起動のたびに必ず新しいファイルを作成する。False（既定）なら
    # 直近の既存ファイルへの追記を試みる（行数超過ならその場でローテーション）。
    # ローテーションで増え続ける古い app_*.log は config.log_retention_days
    # （[log].retention_days）で自動削除する。
    log_level = _config.log_level
    if log_level not in ("info", "debug", "none"):
        raise ValueError(
            f"unknown log_level: {_config.log_level!r}（config.ini の " "[paths].log_level は info/debug/none のいずれかで指定してください）"
        )
    if log_level == "none":
        logging.disable(logging.CRITICAL)
    else:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG if log_level == "debug" else logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = LineCountRotatingFileHandler(
            _config.log_dir,
            _config.log_max_lines,
            clear_on_startup=_config.log_clear_on_startup,
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 第1段階 Discovery: スキルを走査して name+description をシステムプロンプトへ。
    # skills_dir と locohane_skills_dirs をマージ（同名は locohane 側優先）。
    skills = scan_skills([_config.skills_dir, *_config.locohane_skills_dirs])
    system_prompt = build_system_prompt(skills, _config.system_prompt_path)
    # エージェント種別（agents/*.md、ClaudeCode の .claude/agents/*.md 相当）を走査し、
    # 各種別のシステムプロンプトにも {{skills}} を差し込む。
    # agents_dir と locohane_agents_dirs をマージ（同名は locohane 側優先）。
    agent_type_defs = scan_agent_types([_config.agents_dir, *_config.locohane_agents_dirs])
    skills_block = render_skills_block(skills)
    agent_type_defs = [replace(a, system_prompt=a.system_prompt.replace("{{skills}}", skills_block)) for a in agent_type_defs]
    # 永続メモリーの索引（MEMORY.md）を {{memory}} へ差し込む（常に読み込まれる仕様）。
    # subagent 側にはメモリーツールを渡さないため差し込まない。
    system_prompt = system_prompt.replace("{{memory}}", render_memory_block(_config.memory_dir))
    # dispatch_agent が選べるエージェント種別一覧を {{agent_types}} へ差し込む。
    system_prompt = system_prompt.replace("{{agent_types}}", render_agent_types_block(agent_type_defs))
    # config.ini の値を ${変数名} として埋め込めるよう展開する（{{...}}置換完了後に行う）。
    system_prompt = expand_config_vars(system_prompt, _config)
    # .locohane/LOCOHANE.md はユーザー自由記述のため ${...} を偶然含んでいても
    # expand_config_vars() の未定義変数エラーで起動が落ちないよう、config変数展開が
    # 完了した後の最後の差し込みとして扱う。
    system_prompt = system_prompt.replace(
        "{{project_instructions}}",
        render_project_instructions_block(_config.project_instructions_paths),
    )

    # サブエージェント共通の注意事項（反復回数の定義・フォールバック方針）を、
    # agents/*.md ファイル自体には書き込まず、各 agent_type の system_prompt
    # （メモリ上の文字列）の末尾に自動連結する。
    subagent_common = expand_config_vars(
        (_config.system_prompt_path.parent / "subagent_common.md").read_text(encoding="utf-8"),
        _config,
    )
    agent_type_defs = [replace(a, system_prompt=f"{a.system_prompt}\n\n{subagent_common}") for a in agent_type_defs]

    # ツールに skills ルート等を注入（dispatch_agent 用にエージェント種別定義も渡す）。
    # locohane_skills_dirs を先に置くことで、read_skill/run_script/analyze_image 等の
    # 実体解決も scan_skills() と同じ「.locohane 側優先」のマージ挙動になる。
    init_tools(
        [*_config.locohane_skills_dirs, _config.skills_dir],
        _config.script_python,
        _config.script_timeout,
        _config,
        agent_type_defs,
        _config.subagent_max_iterations,
        _config.default_workdir,
        _config.memory_dir,
        _config.help_path,
        _config.path_memory_dir,
        _config.path_memory_max_entries,
        _config.code_exec_enabled,
        _config.approval_timeout_seconds,
        _config.ask_user_question_timeout_seconds,
        _config.ask_user_choice_timeout_seconds,
        _config.plan_badge_allow_unlock,
        _config.subagent_max_parallel,
        _config.graph_tool_max_parallel,
        script_background_max_runtime_seconds=_config.script_background_max_runtime_seconds,
        script_background_job_retention_seconds=_config.script_background_job_retention_seconds,
    )

    # チェックポインタ（会話状態の永続化）。接続はアプリ寿命で保持する。
    _checkpointer = await _build_checkpointer()

    # on_stop でのグラフ再構築（_rebuild_graph）に使い回すため保持する。
    _system_prompt = system_prompt

    logging.getLogger(__name__).info("共有リソース初期化完了。スキル数=%d", len(skills))

    # アップロードフォルダの期限切れファイルを起動時に1回削除し、以降は定期的に削除を続ける。
    cleanup_old_uploads(_config.upload_dir, _config.upload_retention_days)
    if _config.upload_retention_days > 0:
        asyncio.create_task(
            run_cleanup_loop(
                _config.upload_dir,
                _config.upload_retention_days,
                _config.upload_cleanup_interval_hours,
            )
        )

    # default_workdir（ユーザーが ChatSettings で work_dir を指定しなかった場合の
    # 既定作業ディレクトリ）直下に溜まり続けるファイルも同様に自動削除する。
    # ユーザー指定の work_dir はここでは触らない。
    cleanup_old_files(_config.default_workdir, _config.default_workdir_retention_days)
    if _config.default_workdir_retention_days > 0:
        asyncio.create_task(
            cleanup_run_cleanup_loop(
                _config.default_workdir,
                _config.default_workdir_retention_days,
                _config.default_workdir_cleanup_interval_hours,
            )
        )

    # execute_python_code が default_workdir 配下に作る `_tmp_<thread_id>`
    # （src/tools.py の _resolve_exec_workdir() 参照）は本来 on_chat_end で
    # セッション終了時に即時削除されるが、異常終了でそのフックが発火しな
    # かった場合の取りこぼしに備え、起動時に一度だけ日数ベースで掃除する。
    cleanup_old_dirs(_config.default_workdir, _config.default_workdir_retention_days, pattern="_tmp_*")

    # パスメモリー（src/path_memory.py）のレジストリファイルも同様に日数ベースで
    # 自動削除する（「会話終了」を検知する手段が無いための代替措置）。
    cleanup_old_files(_config.path_memory_dir, _config.path_memory_retention_days)
    if _config.path_memory_retention_days > 0:
        asyncio.create_task(
            cleanup_run_cleanup_loop(
                _config.path_memory_dir,
                _config.path_memory_retention_days,
                _config.path_memory_cleanup_interval_hours,
            )
        )

    # ローテーションで増え続ける古い app_*.log も同様に日数ベースで自動削除する。
    # 同じ log_dir に evals/run_case.py が書く evals.log は対象外にする
    # （pattern="app_*.log" で絞り込む）。
    cleanup_old_files(_config.log_dir, _config.log_retention_days, pattern="app_*.log")
    if _config.log_retention_days > 0:
        asyncio.create_task(
            cleanup_run_cleanup_loop(
                _config.log_dir,
                _config.log_retention_days,
                _config.log_cleanup_interval_hours,
                pattern="app_*.log",
            )
        )

    # P0-3: httpcore の cancel scope breakage 検知フィルタを登録する。
    # _setup() は一度だけ呼ばれるため、ここで登録すれば十分（冪等処理あり）。
    _register_cancel_scope_watcher()


def _rebuild_graph(thread_id: str):
    """既存の system_prompt・checkpointer を使い回し、指定セッションのグラフだけを
    （再）構築して cl.user_session へ保存する。

    on_chat_start（初回構築）・on_stop（LLM クライアント強制クローズ後）・
    on_message の ThinkingLoopDetected リトライ経路（クライアント再構築後）の
    いずれからも呼ぶ。build_model() は呼ばれるたびに新しい httpx.AsyncClient を
    生成するため、build_graph() の直前に set_current_session(thread_id) を
    呼び、新しいクライアントが正しくこのセッションへ紐づけて登録されるように
    する。checkpointer の接続（_checkpointer）はそのまま使い回すため、
    thread_id ごとの会話履歴は失われない。

    グラフはセッション（タブ）ごとに個別のインスタンスであり、他タブの
    グラフ・httpxクライアントには一切影響しない（モジュール globals では
    なく cl.user_session に保存する理由）。

    Args:
        thread_id: このセッションの thread_id（cl.user_session の
            "thread_id"）。

    Returns:
        新しく構築されたコンパイル済みグラフ。呼び出し元はこれをローカル
        変数として使い続けること。
    """
    set_current_session(thread_id)
    graph = build_graph(_config, _system_prompt, _checkpointer)
    cl.user_session.set("graph", graph)
    return graph


@cl.on_stop
async def on_stop() -> None:
    """停止ボタン押下時、【このセッション自身の】LLMサーバーへの根底のHTTP接続を
    強制的に切断する。

    Chainlit本体（session.current_task.cancel()）は実行中タスクへ
    asyncio.CancelledError を投げ込むのみで、LLMサーバーへのHTTP接続を
    切断する処理は持たない。ChatLlamaCpp._astream の finally 節にある
    agen.aclose() はキャンセル済みコンテキストでは正しく完了しない
    可能性が高く、接続が生きたままだと停止ボタンを押してもCPU/GPU使用率が
    下がらない事象につながる（tune-prompt iter27でユーザー報告・調査）。

    ここはキャンセルされていない別の task コンテキストで呼ばれるため、
    aclose_active_llm_clients(thread_id) で確実に接続を強制クローズできる。
    その直後に自セッションのグラフを再構築し、新しいLLMクライアントに
    差し替える。

    以前はプロセス全体で共有される _active_async_clients を一括クローズ
    しており、別タブで実行中の処理まで巻き添えで
    "Cannot send a request, as the client has been closed" 等のエラーで
    止まってしまう不具合があった。グラフ（＝LLMモデル・httpxクライアント）を
    セッションごとに分離したことで、thread_id を指定して自セッション分
    だけを閉じられるようになっている。
    """
    thread_id = cl.user_session.get("thread_id")
    if thread_id is None:
        # _setup()/on_chat_start 完了前に停止が押された等の異常系。
        # 紐づくセッションのグラフがまだ無いため何もしない。
        logging.getLogger(__name__).warning("on_stop: thread_id が未設定のため何もしません")
        return
    # aclose_active_llm_clients 内でCancelledErrorが発生しても、後始末
    # （グラフ再構築・ログ出力）は最後まで実行してから呼び出し元へ伝播する。
    cancelled: asyncio.CancelledError | None = None
    try:
        await aclose_active_llm_clients(thread_id)
    except asyncio.CancelledError as exc:
        cancelled = exc
    # _rebuild_graph が例外を送出しても検知できないまま処理が終わるのを防ぐ。
    # 最大2回まで試行する。
    rebuild_ok = False
    for _retry in range(2):
        try:
            _rebuild_graph(thread_id)
            rebuild_ok = True
            break
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).error(
                "on_stop: グラフ再構築に失敗しました [%s]",
                describe_current_task(),
                exc_info=True,
            )
            await cl.Message(content="セッションの再初期化に失敗しました。反応がなければ" "ページを再読み込みしてください。").send()
    logging.getLogger(__name__).info(
        "on_stop: セッション thread_id=%s のLLMクライアントを強制クローズし、" "グラフを再構築しました%s%s",
        thread_id,
        "（クローズ処理中にタスクがキャンセルされましたが後始末は完了しました）" if cancelled else "",
        "（グラフ再構築: 成功）" if rebuild_ok else "（グラフ再構築: 失敗）",
    )
    if cancelled is not None:
        raise cancelled


@cl.on_chat_start
async def on_chat_start() -> None:
    """Chainlit のチャットセッション開始時に呼ばれるフック。

    アプリ全体で共有する資源の初期化（_setup、未実施なら）を行った上で、
    このセッション専用の thread_id を発行して LangGraph の
    チェックポイント分離キーとして user_session に保存する。
    続けてこのセッション専用のグラフ（LLMモデル・httpxクライアントを含む）を
    _rebuild_graph() で構築し、cl.user_session["graph"] に保存する
    （他タブとはLLMクライアントを共有しない。本ファイル冒頭の
    モジュールdocstring参照）。最後に利用可能なスキル一覧を挨拶メッセージ
    として送信する。

    Args:
        なし（Chainlit のフック規約により引数なし）。

    Returns:
        None。副作用として cl.user_session への書き込みと
        ウェルカムメッセージの送信を行う。
    """
    await _setup()
    # 会話ごとに thread_id を発行（チェックポイントの分離キー、かつ
    # LLMクライアントのセッションスコープ分離キーにも流用する）。
    thread_id = str(uuid.uuid4())
    cl.user_session.set("thread_id", thread_id)
    # 会話ログ（[chat_log].enabled=true の場合のみ）の書き出し先を
    # セッション開始時に1回だけ確定する（日をまたいでも同じファイルに
    # 追記し続ける。src/chat_log.py 参照）。
    if _config.chat_log_enabled:
        user = cl.user_session.get("user")
        username = resolve_log_username(user.identifier if user else None)
        cl.user_session.set("chat_log_path", build_log_path(_config.chat_log_dir, username, thread_id))
    # このセッション専用のグラフを構築する（他タブとはLLMクライアントを共有しない）。
    _rebuild_graph(thread_id)
    cl.user_session.set("work_dir", None)
    cl.user_session.set("work_dir_access", None)
    # サイドパネル上部の「作業ディレクトリ」表示を初期状態（未設定）から出す。
    await cl.Message(content=_format_work_dir_status(None)).send()
    # Task Management ツール（create_plan/approve_plan/update_task_progress）の
    # セッション状態。新しいセッションでは未作成・未承認から開始する。
    cl.user_session.set("plan", None)
    cl.user_session.set("plan_message", None)
    cl.user_session.set("plan_approved", False)
    # トークン使用量の会話全体累計（on_message の on_chat_model_end で加算）。
    # メインエージェント自身のLLM呼び出しとサブエージェント（dispatch_agent）
    # 内部のLLM呼び出しの両方を合算した値。
    cl.user_session.set("token_usage_cumulative", _new_usage_totals())
    # 上記のうちメインエージェント自身の呼び出し分のみの累計
    # （サブエージェントへ委譲した分は含まない。委譲がどれだけ会話コンテキストの
    # 節約に寄与しているかをユーザーが確認できるように分けて集計する）。
    cl.user_session.set("token_usage_cumulative_main", _new_usage_totals())

    # run_script の作業ディレクトリを歯車アイコンから指定できるようにする。
    # 未入力（初期値）なら config.ini の [paths].default_workdir が使われる
    # （tools.py の _resolve_workdir）。
    await cl.ChatSettings(
        [
            TextInput(
                id="work_dir",
                label="作業ディレクトリ",
                initial="",
                placeholder=rf"例: C:\Users\me\project（未入力なら既定値 {_config.default_workdir} を使用）",
            )
        ]
    ).send()

    skills = scan_skills([_config.skills_dir, *_config.locohane_skills_dirs])
    names = "、".join(s.name for s in skills) or "（なし）"
    welcome_template = _load_settings_text("welcome.md", _WELCOME_TEMPLATE_DEFAULT)
    await cl.Message(content=welcome_template.replace("{skills}", names)).send()


@cl.on_chat_end
async def on_chat_end() -> None:
    """タブを閉じる・切断された際に呼ばれるフック。

    src/llm.py の _active_async_clients（セッションID→WeakSetの辞書）は、
    以前は「セッション終了」を検知する手段が無かったため、キー自体は
    プロセス寿命中ずっと残り続けていた（値のWeakSetはクライアントの
    参照が無くなり次第GCで自然に空になるが、辞書エントリそのものは
    消えない）。実害はごく軽微（文字列+空WeakSetオブジェクト程度）だが、
    forget_session() で明示的に片付ける。

    注意: ここでは強制クローズ（aclose_active_llm_clients）は呼ばない。
    このフックは session.current_task をキャンセルしないため、停止
    ボタンを押さずにタブを閉じた場合はバックグラウンドで処理が続いて
    いる可能性があり、その最中にクライアントを強制closeすると新たな
    エラーを誘発しかねない。辞書キーの掃除のみに留める。

    あわせて、execute_python_code が作業ディレクトリ配下に作る
    `_tmp_<thread_id>`（src/tools.py の _resolve_exec_workdir() 参照）も
    ここで削除する。ディレクトリ名にthread_idを含むため、同じ作業
    ディレクトリを複数セッションが共有していても他セッション分には
    触れない。ユーザー指定の work_dir はこの日数ベースの自動削除の対象外
    （config.ini [default_workdir] 参照）のため、セッション終了時の
    即時削除が唯一の後片付け機会になる。work_dir が書き込み不可と判定され
    ていた場合、実体は _resolve_exec_workdir() の機械的ガードにより
    default_workdir 配下に作られているため、両方を削除対象にする
    （存在しない側は ignore_errors=True で無害）。
    """
    thread_id = cl.user_session.get("thread_id")
    if thread_id is not None:
        forget_session(thread_id)
        work_dir = cl.user_session.get("work_dir")
        shutil.rmtree(_config.default_workdir / f"_tmp_{thread_id}", ignore_errors=True)
        if work_dir:
            shutil.rmtree(Path(work_dir) / f"_tmp_{thread_id}", ignore_errors=True)


async def _apply_work_dir(raw: str) -> None:
    """作業ディレクトリの入力値を実際にプローブして cl.user_session に反映する。

    入力値が空文字なら work_dir を None に戻す（既定動作＝config.ini の
    default_workdir に復帰）。値がある場合は常に cl.user_session に絶対パス
    文字列として保存する（表示用）。以前はここで is_dir() に失敗すると保存
    自体を拒否していたが、サーバー駆動でローカルネットワーク上の別PCから
    利用される場合、ユーザーのPCからは見える/書き込めるパスがサーバー側から
    は見えない・書き込めないことがあり得るため拒否はしない。代わりに
    probe_workdir_access() で実際の読み書き可否を確認し、結果を
    cl.user_session["work_dir_access"] にキャッシュする（src/tools.py の
    _resolve_workdir() がこれを見て機械的に既定フォルダへフォールバックする）。

    ChatSettings（歯車アイコン）と、独自フロントエンドの「作業フォルダ」
    アイコン（pick_work_dir action_callback）の両方から共通で呼ばれる。

    Args:
        raw: ユーザーが指定したパス文字列（未加工）。

    Returns:
        None。副作用として cl.user_session の更新と状態メッセージの送信を行う。
    """
    raw = raw.strip()
    if not raw:
        cl.user_session.set("work_dir", None)
        cl.user_session.set("work_dir_access", None)
        await cl.Message(content=_format_work_dir_status(None)).send()
        return

    resolved = str(Path(raw).resolve())
    status = probe_workdir_access(Path(resolved))
    cl.user_session.set("work_dir", resolved)
    cl.user_session.set("work_dir_access", status)
    await cl.Message(content=_format_work_dir_status(resolved, status)).send()


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    """ChatSettings（歯車アイコン）で作業ディレクトリが変更されたときに呼ばれるフック。

    Args:
        settings: ChatSettings の全入力値（{"work_dir": "..."}）。

    Returns:
        None。
    """
    await _apply_work_dir(settings.get("work_dir") or "")


@cl.action_callback("toggle_plan_mode")
async def on_toggle_plan_mode(action: cl.Action) -> None:
    """送信ボタン付近の Plan Mode / Edit Automatically バッジをクリックした際の
    action_callback。実体は src/tools.py の toggle_plan_mode_from_ui（config.ini の
    [plan].allow_badge_unlock によるロック解除方向のガードもそちら側で行う）。

    Args:
        action: フロントエンドから callAction で渡された cl.Action（未使用）。

    Returns:
        None。
    """
    del action
    await toggle_plan_mode_from_ui()


@cl.action_callback("pick_work_dir")
async def on_pick_work_dir(action: cl.Action) -> None:
    """独自フロントエンドの「作業フォルダ」アイコンから呼ばれる action_callback。

    tkinter の OS ネイティブなフォルダ選択ダイアログを表示する。バックエンドと
    ブラウザが同一マシン上で動くローカルデスクトップ用途を前提とした機能
    （リモート環境でブラウザだけ別マシンという構成では機能しない）。
    filedialog.askdirectory() はブロッキング呼び出しのため、他セッションの
    応答性を落とさないよう asyncio.to_thread でイベントループから退避させる。

    Args:
        action: フロントエンドから callAction で渡された cl.Action（未使用）。

    Returns:
        None。
    """
    del action

    def _ask_directory() -> str:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return filedialog.askdirectory()
        finally:
            root.destroy()

    raw = await asyncio.to_thread(_ask_directory)
    await _apply_work_dir(raw)


def _save_uploads(message: cl.Message) -> list[str]:
    """アップロードファイルを upload_dir に保存し、保存先パスの一覧を返す。

    message.elements に含まれる各添付要素について、element.path
    （Chainlit が保持する一時保存先）から config.upload_dir へコピーする。
    path を持たない要素（テキストのみの要素など）はスキップする。

    Args:
        message: on_message で受け取った Chainlit のメッセージオブジェクト。
            elements にアップロードファイルの情報が含まれる。

    Returns:
        保存先の絶対パス文字列のリスト（保存した順）。
        アップロードが無ければ空リスト。
    """
    saved: list[str] = []
    for element in message.elements or []:
        src = getattr(element, "path", None)
        if not src:
            continue
        dest = _config.upload_dir / (element.name or Path(src).name)
        shutil.copyfile(src, dest)
        saved.append(str(dest))
        logging.getLogger(__name__).info("アップロード保存: %s", dest)
    return saved


def _build_human_message(user_text: str, saved_paths: list[str]) -> HumanMessage:
    """アップロードファイルを踏まえて HumanMessage を組み立てる。

    画像ファイルは data URL 化して content のマルチモーダル要素として積み、
    Vision対応モデルへ実際の視覚情報として渡す。それ以外のファイルは従来通り
    パスをテキストへ追記するのみ（run_script 等のツールにパスとして渡させるため）。

    Args:
        user_text: ユーザーが入力したメッセージ本文。
        saved_paths: _save_uploads() が返した保存先パスの一覧。

    Returns:
        画像添付が無ければ文字列 content の HumanMessage、あれば
        text + image_url のマルチモーダル content を持つ HumanMessage。
    """
    image_paths = [p for p in saved_paths if is_image_file(p)]
    other_paths = [p for p in saved_paths if not is_image_file(p)]

    text = user_text
    if other_paths:
        paths = "\n".join(f"- {p}" for p in other_paths)
        text += f"\n\n[アップロードされたファイルの保存先]\n{paths}"

    if not image_paths:
        return HumanMessage(content=text)

    content: list[dict] = [{"type": "text", "text": text}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": to_data_url(p)}})
    return HumanMessage(content=content)


def _resolve_parent_id(event: dict, steps: dict[str, cl.Step]) -> str | None:
    """astream_events の event から、UI Step の親として使う parent_id を決める。

    dispatch_agent は内部で独立したサブエージェント（run_subagent）を回すが、
    LangChain の astream_events は contextvar 経由でその内部呼び出しの
    イベントも同じストリームへ伝播させる（当初の設計意図「内部は表示しない」
    とは異なる実装挙動）。何も対策しないと、サブエージェント内部のツール・
    思考Stepが親の dispatch_agent Step と見分けの付かない兄弟として並び、
    「サブエージェントが実行中のまま止まって見えるのに下に完了済みStepが
    並ぶ」というUI上の混乱を招く。

    event["parent_ids"]（LangChain が管理する実際の呼び出し階層。dispatch_agent
    のツール実行がRunnable Bを呼べば、B のイベントの parent_ids に
    dispatch_agent 側の run_id が含まれる）を見て、現在 steps 辞書に
    残っている（＝まだ完了していない）祖先Stepがあれば、その Step の下へ
    ネストさせる。該当が無ければ従来通り cl.context.current_step を使う。
    """
    for parent_run_id in event.get("parent_ids", []):
        parent_step = steps.get(parent_run_id)
        if parent_step is not None:
            return parent_step.id
    parent = cl.context.current_step
    return parent.id if parent else None


def _is_subagent_call(event: dict, steps: dict[str, cl.Step]) -> bool:
    """この astream_events イベントが dispatch_agent 内部（サブエージェント）由来かを判定する。

    _resolve_parent_id() と同じ event["parent_ids"] を使う。祖先に steps
    （現在開いているツールStep）が含まれていれば、そのツール実行中に
    発生したイベント＝サブエージェント内部（dispatch_agentの中）由来と
    判定できる（LLM呼び出しを内部で行うツールは dispatch_agent のみ）。
    """
    return any(pid in steps for pid in event.get("parent_ids", []))


def _is_dispatch_agent_error(tool_name: str, content: object) -> bool:
    """dispatch_agent が失敗時に返す「エラー: ...」文字列かどうかを判定する。

    src/tools.py の dispatch_agent() は失敗時も例外を投げず、この形式の
    文字列を正常な戻り値として返す。on_tool_end はそのままだと常に正常
    終了として扱ってしまうため、ここで検知して呼び出し元が is_error を
    立てられるようにする。
    """
    return tool_name == "dispatch_agent" and isinstance(content, str) and content.startswith("エラー:")


def _is_dispatch_agent_truncated(tool_name: str, content: object) -> bool:
    """dispatch_agent がサブエージェント内部の打ち切り（max_iterations到達・
    空応答連続・トークン閾値超過・LLMタイムアウト）で返した文字列かどうかを判定する。

    src/subagent.py の run_subagent() はこれらの打ち切りを例外にせず、
    is_truncated_result() で判定できるプレフィックス付き文字列を正常な
    戻り値として返す。_is_dispatch_agent_error と異なりエラーではない
    （収集済みの結果を伴う「未完了終了」）ため、is_error ではなく
    stopped_reason バッジで区別する。
    """
    return tool_name == "dispatch_agent" and is_truncated_result(content)


async def _finalize_orphaned_steps(steps: dict[str, cl.Step], reason: str) -> None:
    """ThinkingLoopDetected/GraphRecursionError で打ち切られた際、on_tool_end が
    届かないまま steps に残っている進行中Step（dispatch_agent等）を確定させる。

    リトライ時は steps がループ先頭で新しい空dictに差し替わるため、ここで
    finalize しないと該当Stepがフロントエンド上で「実行中」のまま孤立し続ける
    （UI側は step.end が来るまで恒久的に「実行中」表示のため）。thinking の
    ループ検知時と同じ metadata.stopped_reason を使い、「停止」バッジで
    正常完了と区別できるようにする。
    """
    for step in steps.values():
        step.metadata = {"stopped_reason": reason}
        step.end = utc_now()
        await step.update()
    steps.clear()


class _PlanDeniedInterrupt(Exception):
    """approve_plan が明示的に却下された直後、このターンを打ち切るための内部例外。

    ToolNode が生成した ToolMessage はこの時点ではまだチェックポイントへ
    コミットされていない。async for ループの外側（except節、finallyの
    aclose()より前）まで運んでから graph.aupdate_state() で明示コミットする
    ことで、次回のグラフ実行時に「tool_calls に対応する ToolMessage が無い」
    という不整合（_validate_chat_history エラー）を防ぐ。
    """

    def __init__(self, tool_message: ToolMessage) -> None:
        self.tool_message = tool_message


def _find_orphaned_tool_calls(messages: list) -> list[dict]:
    """直近のAIMessageのtool_callsのうち、対応するToolMessageがまだ無いものを返す。

    停止ボタン等によるCancelledErrorでターンが中断された場合、チェックポイントに
    コミット済みのAIMessage(tool_calls)に対し、ToolNodeが生成するはずだった
    ToolMessageが記録されないまま終わることがある（_PlanDeniedInterruptの
    docstring参照）。次回グラフ実行前にこの孤立を検出するために使う。
    """
    if not messages:
        return []
    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None)
    if not tool_calls:
        return []
    return list(tool_calls)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Chainlit がユーザーからのメッセージを受け取るたびに呼ばれるフック。

    セッションの thread_id を使って LangGraph のグラフを astream_events
    で実行し、以下をリアルタイムに UI へ反映する:
    - on_chat_model_stream: モデルのトークンを最終回答へストリーム表示。
    - on_tool_start / on_tool_end: ツール呼び出し（read_skill/read_skill_file/
      run_script/view_image）を cl.Step として可視化し、入出力を表示する。

    アップロードファイルがあれば _save_uploads() で保存し、_build_human_message()
    で HumanMessage を組み立ててグラフへ渡す。画像は data URL 化してLLMへ
    視覚情報として渡し、それ以外は保存先パスを本文に追記する（LLM が
    run_script 等にパスを渡せるようにするため）。本文中に生のUNCパスが
    含まれる場合は register_raw_unc_paths_in_text() で path_memory へ
    事前登録し `@N` へ置換してからグラフへ渡す（ISSUE-002対策）。

    Args:
        message: ユーザーが送信した Chainlit のメッセージ
            （本文 content と添付ファイル elements を含む）。

    Returns:
        None。副作用として cl.Message / cl.Step の送信・更新を行う。
    """
    thread_id = cl.user_session.get("thread_id")
    # 計画承認は「このユーザーメッセージへの応答で作られた計画の実行」に
    # 限定されたスコープであるべきなので、新しいメッセージを受け取るたびに
    # 前回（放置されて完了しなかった計画など）の承認状態を持ち越さない。
    cl.user_session.set("plan_approved", False)
    # dispatch_agent 経由でこのタスクから派生するサブエージェントの
    # build_model() 呼び出しも、このセッションへ紐づけて登録されるようにする
    # （src/llm.py の set_current_session / _CURRENT_SESSION_ID 参照）。
    set_current_session(thread_id)
    graph = cl.user_session.get("graph")
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": _config.graph_recursion_limit,
    }

    # アップロードがあれば保存する。画像は data URL 化してLLMに見せ、それ以外は
    # 保存先パスを本文に明示する（LLM が run_script に渡せるように）。
    saved = _save_uploads(message)
    processed_text = register_raw_unc_paths_in_text(message.content)
    human_message = _build_human_message(processed_text, saved)

    inputs = {"messages": [human_message]}
    turn_totals = {"input": 0, "output": 0, "total": 0}  # このターンのトークン使用量
    # 無言終了（tool_calls も回答テキストも無いまま終わる）を検知した場合、
    # 最終回答を促して自動的にリトライする（下のループ末尾を参照。
    # [thinking_loop_guard].empty_response_max_retries 由来）。
    max_empty_retries = _config.thinking_loop_guard_empty_response_max_retries
    # LLM応答の反復ループ（src/llm.py の ChatLlamaCpp が検知して
    # ThinkingLoopDetected を送出する）を検知した場合の再試行回数と、
    # 注入した注意メッセージ（機械的なもの）のid（成功後に履歴から取り除くため）。
    loop_max_retries = _config.thinking_loop_guard_max_retries
    loop_attempt = 0
    loop_nudge_ids: list[str] = []
    # 無言終了リマインダーのid（成功後に履歴から取り除くため。thinking_loopの
    # nudgeと同様、機械的な注入を会話履歴に残さないようにする）。
    empty_nudge_ids: list[str] = []
    # 2つのリトライ要因（無言終了・反復ループ）で試行回数の予算を共有する。
    total_retries = max_empty_retries + loop_max_retries
    # 直近1回分のLLM呼び出しの usage_metadata。ターン終了後、コンテキスト
    # 圧縮（src/context_compaction.py）の閾値判定に使う。
    last_usage: dict | None = None

    loop_exc: ThinkingLoopDetected | None = None
    turn_broken_exc: Exception | None = None
    checkpointer_needs_rebuild: bool = False
    for attempt in range(total_retries + 1):
        answer: cl.Message | None = None  # ツール呼び出しごとに区切って新規発行する
        thinking: cl.Step | None = None  # <think> ブロック（reasoning_content）を表示するStep
        steps: dict[str, cl.Step] = {}  # run_id -> Step（ツール開始/終了を対応付け）

        event_stream = graph.astream_events(inputs, config=config, version="v2")
        # P1: リトライ経路（attempt>0）でのみ、どのタスクで新リクエストが始まったか、
        # cancel scope breakage の結果をログする。
        if attempt > 0:
            csb = recent_cancel_scope_breakage()
            logging.getLogger(__name__).warning(
                "on_message: リトライ%d回目開始 [%s, cancel_scope_breakage_last_60s=%d]",
                attempt + 1,
                describe_current_task(),
                csb,
            )
        else:
            logging.getLogger(__name__).debug(
                "on_message: 初回リクエスト開始 [%s]",
                describe_current_task(),
            )
        try:
            async for event in event_stream:
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    # モデルのトークンをストリーム表示。stream_token() は初回呼び出し時に
                    # UI上の表示位置を確定させるため、ツール呼び出しをまたいで使い回すと
                    # 後続のStepより上に固定表示されてしまう。ツール呼び出し区切りで
                    # 新規 Message を発行し、時系列順に表示されるようにする。
                    chunk = event["data"]["chunk"]

                    # llama-server が reasoning_content（<think>ブロック）を返す場合、
                    # ChatLlamaCpp（src/llm.py）が additional_kwargs へ拾い上げている。
                    # 本回答とは別の折りたたみStepとして「思考中」を可視化する。
                    reasoning = chunk.additional_kwargs.get("reasoning_content")
                    if reasoning:
                        if thinking is None:
                            thinking = cl.Step(
                                name="思考中",
                                type="llm",
                                parent_id=_resolve_parent_id(event, steps),
                            )
                            thinking.start = utc_now()
                            await thinking.send()
                        await thinking.stream_token(reasoning)

                    if chunk.content:
                        if thinking is not None:
                            # 思考が終わり本回答が始まったのでStepを確定させる。
                            thinking.end = utc_now()
                            await thinking.update()
                            thinking = None
                        if answer is None:
                            answer = cl.Message(content="")
                        await answer.stream_token(chunk.content)

                elif kind == "on_tool_start":
                    # ここまでの思考/回答があれば確定送信し、次のテキストは新しい Message に分ける。
                    if thinking is not None:
                        thinking.end = utc_now()
                        await thinking.update()
                        thinking = None
                    if answer is not None:
                        await answer.send()
                        answer = None
                    # ツール実行を Step として可視化（どのスキル/ツールかが見える）。
                    # cl.Step はコンストラクタで local_steps（@cl.on_message が積む
                    # 実行ラン）を自動継承しない（cl.Message は継承する）ため、
                    # _resolve_parent_id() で明示的に親を決める。
                    label = _TOOL_LABELS.get(event["name"], event["name"])
                    step = cl.Step(name=label, type="tool", parent_id=_resolve_parent_id(event, steps))
                    step.start = utc_now()
                    step.input = event["data"].get("input")
                    await step.send()
                    steps[event["run_id"]] = step

                elif kind == "on_tool_end":
                    step = steps.pop(event["run_id"], None)
                    if step is not None:
                        output = event["data"].get("output")
                        content = getattr(output, "content", output)
                        step.output = content
                        step.end = utc_now()
                        if _is_dispatch_agent_error(event["name"], content):
                            step.is_error = True
                        elif _is_dispatch_agent_truncated(event["name"], content):
                            # ISSUE-001: max_iterations到達等によるサブエージェント
                            # 内部の打ち切りは例外にならず正常なToolMessageとして
                            # 返るため、放置すると「正常完了」に見えてしまう。
                            # ループ検知等と同じstopped_reasonバッジで区別した上で、
                            # 親LLMの言及に頼らず打ち切りをユーザーへ明示する。
                            step.metadata = {"stopped_reason": "subagent_truncated"}
                        await step.update()
                        if _is_dispatch_agent_truncated(event["name"], content):
                            await cl.Message(
                                content=(
                                    "サブエージェントのタスクが打ち切られました"
                                    "（反復回数や応答内容の上限に到達したため）。"
                                    "ここまでに収集できた結果を踏まえて続行します。"
                                )
                            ).send()
                        if isinstance(content, str):
                            generated = extract_generated_file(content)
                            if generated is not None:
                                if is_image_file(generated):
                                    element = cl.Image(name=generated.name, path=str(generated), display="inline")
                                else:
                                    element = cl.File(name=generated.name, path=str(generated))
                                await cl.Message(
                                    content=f"生成ファイル: {generated.name}",
                                    elements=[element],
                                ).send()
                    # 実行計画がユーザーに却下された直後。ツール結果の文言・
                    # system_prompt.mdの指示だけに頼ると、LLMが計画を微修正して
                    # 勝手に続行してしまう恐れがあるため、ここでコード側から
                    # 確実にこのターンを打ち切る（approve_planがフラグを立てる。
                    # src/tools.py参照）。ここで即returnせず _PlanDeniedInterrupt を
                    # raise するのは、ToolNode が生成したこの ToolMessage がまだ
                    # チェックポイントへコミットされていないため（下の except節参照）。
                    if event["name"] == "approve_plan" and cl.user_session.get("plan_denied_just_now"):
                        cl.user_session.set("plan_denied_just_now", False)
                        if thinking is not None:
                            thinking.end = utc_now()
                            await thinking.update()
                            thinking = None
                        if answer is not None:
                            await answer.send()
                            answer = None
                        await cl.Message(content="実行計画が却下されたため、処理を終了しました。ご指示をお待ちしています。").send()
                        raise _PlanDeniedInterrupt(tool_message=event["data"].get("output"))

                elif kind == "on_chat_model_end":
                    # config.ini [llm].track_token_usage=true の場合のみ usage_metadata が乗る
                    # （src/llm.py の build_model が stream_usage を有効化している場合）。
                    output = event["data"].get("output")
                    usage = getattr(output, "usage_metadata", None)
                    if usage:
                        last_usage = usage
                        _accumulate_usage(turn_totals, usage)
                        # 長時間の承認待ち等でセッションデータが失われている場合に
                        # 備え、Noneなら0から再初期化する（totals[key]でのクラッシュ防止）。
                        cumulative = cl.user_session.get("token_usage_cumulative") or _new_usage_totals()
                        _accumulate_usage(cumulative, usage)
                        cl.user_session.set("token_usage_cumulative", cumulative)
                        cumulative_main = cl.user_session.get("token_usage_cumulative_main") or _new_usage_totals()
                        if not _is_subagent_call(event, steps):
                            _accumulate_usage(cumulative_main, usage)
                            cl.user_session.set("token_usage_cumulative_main", cumulative_main)
                        # UI表示（cl.Message）はセッション終了とともに追えなくなる
                        # ため、事後にapp.logだけでトークン使用量の推移（LLM呼び出し
                        # 単位の値・このターンの累積・会話全体の累積）を追跡できる
                        # よう、on_chat_model_endのたびにINFOで記録しておく。
                        logging.getLogger(__name__).info(
                            "トークン使用量 thread_id=%s call(in=%d,out=%d,total=%d) "
                            "turn(in=%d,out=%d,total=%d) cumulative(in=%d,out=%d,total=%d) "
                            "cumulative_main(in=%d,out=%d,total=%d)",
                            thread_id,
                            usage.get("input_tokens", 0) or 0,
                            usage.get("output_tokens", 0) or 0,
                            usage.get("total_tokens", 0) or 0,
                            turn_totals["input"],
                            turn_totals["output"],
                            turn_totals["total"],
                            cumulative["input"],
                            cumulative["output"],
                            cumulative["total"],
                            cumulative_main["input"],
                            cumulative_main["output"],
                            cumulative_main["total"],
                        )
                        # ツール呼び出しを挟んで長く動くターンでも、LLM呼び出しの
                        # たびにサイドパネルの表示を更新する（ターン完了まで待たないと
                        # 見えない、という問題を避けるため）。表示は「このターン」の
                        # 累積ではなく、直近のリクエスト（LLM呼び出し）1回分の値を使う。
                        call_totals = {"input": 0, "output": 0, "total": 0}
                        _accumulate_usage(call_totals, usage)
                        await cl.Message(content=_format_token_usage(call_totals, cumulative_main, cumulative)).send()
        except ThinkingLoopDetected as exc:
            # LLM応答（thinking/本文）が反復ループに陥り打ち切られた場合。
            # ここまでの思考/回答があれば確定送信してから、注意メッセージを
            # 注入して再試行する（id付きHumanMessageとして注入し、成功後に
            # 履歴から取り除けるようidを記録しておく）。
            #
            # P2: グラフ再構築は finally 後の共通処理へ延期する。
            # これにより「aclose→rebuild→新リクエスト」の順序を保証できる
            # （従来は except で rebuild → continue した後 finally が実行
            # される順序だった）。
            loop_exc = exc
            logging.getLogger(__name__).warning(
                "LLM応答のループを検知（%d回目の再試行）: 直近テキスト=%r [%s]",
                loop_attempt + 1,
                exc.snippet,
                describe_current_task(),
            )
            if thinking is not None:
                # ループ検知による打ち切りである旨をフロントへ伝える。StepItem.tsx
                # 側はこの metadata を見て「完了」ではなく「停止」バッジを表示する
                # （デフォルトでは end のみ設定された Step は他の正常完了Stepと
                # 見分けが付かず「完了」と誤表示されていたため）。
                thinking.metadata = {"stopped_reason": "loop_detected"}
                thinking.end = utc_now()
                await thinking.update()
                thinking = None
            if answer is not None:
                await answer.send()
                answer = None
            await _finalize_orphaned_steps(steps, "loop_detected")
        except LLM_CONNECTION_ERRORS as exc:
            # LLMサーバーとの通信エラー（接続失敗・5xx・httpx系）。
            # thinking/answerを確定送信 → チェックポイントの orphan steps
            # を片付ける → turn_broken_exc をセットして finally 後のリビルド
            # 分岐へ合流する。GraphRecursionError ブロックと同型のパターン。
            # 自動リトライはせず、ユーザーへメッセージを送って中断する。
            turn_broken_exc = exc
            logging.getLogger(__name__).warning(
                "LLMサーバーとの通信エラーを検知しました: %s [%s]",
                exc,
                describe_current_task(),
            )
            if thinking is not None:
                thinking.end = utc_now()
                await thinking.update()
                thinking = None
            if answer is not None:
                await answer.send()
                answer = None
            await _finalize_orphaned_steps(steps, "connection_error")
            checkpointer_needs_rebuild = True
        except _CheckpointerTimeout as exc:
            # checkpointer操作がタイムアウトした（ロック取得の固着）、または
            # 操作の実行中にDB接続が閉じられていた（_CheckpointerConnectionClosed。
            # issue.md「DB接続切れエラー」参照）。いずれも共有のSQLite接続が
            # 不健全な状態にある可能性が高い。turn_broken_exc +
            # checkpointer_needs_rebuild をセットし、対応1の後処理へ合流させる。
            turn_broken_exc = exc
            checkpointer_needs_rebuild = True
            logging.getLogger(__name__).warning(
                "チェックポインタ操作が失敗しました（%s）: %s [%s]",
                type(exc).__name__,
                exc,
                describe_current_task(),
            )
            if thinking is not None:
                thinking.end = utc_now()
                await thinking.update()
                thinking = None
            if answer is not None:
                await answer.send()
                answer = None
            await _finalize_orphaned_steps(steps, "checkpointer_timeout")
        except GraphRecursionError:
            # メインの ReAct ループが recursion_limit（config.ini [graph]）に達した場合。
            # ここまでの思考/回答があれば確定送信してから、打ち切りを明示する。
            if thinking is not None:
                thinking.end = utc_now()
                await thinking.update()
                thinking = None
            if answer is not None:
                await answer.send()
                answer = None
            await _finalize_orphaned_steps(steps, "recursion_limit")
            await cl.Message(
                content=(
                    f"反復上限（recursion_limit={_config.graph_recursion_limit}）に達したため、"
                    "処理を打ち切りました。タスクを分割するか、別のアプローチを試してください。"
                )
            ).send()
            if loop_nudge_ids:
                await graph.aupdate_state(config, {"messages": [RemoveMessage(id=i) for i in loop_nudge_ids]})
            return
        except _PlanDeniedInterrupt as e:
            # 却下メッセージの送信・thinking/answerの確定は on_tool_end 内で
            # 既に行っている（raise直前）。ここでは、その時点ではまだ
            # チェックポイントへコミットされていない ToolMessage を、async for
            # ループを抜けた今この場（finallyのaclose()より前）で明示的に
            # コミットする。これにより次回のグラフ実行時に「tool_calls に
            # 対応する ToolMessage が無い」という不整合を防ぐ。
            await _finalize_orphaned_steps(steps, "interrupted")
            await graph.aupdate_state(config, {"messages": [e.tool_message]}, as_node="tools")
            return
        except asyncio.CancelledError as exc:
            # 停止ボタン・切断・リロード等でこのターンがキャンセルされた場合、
            # 実行中だったtool_call（approve_planのAskActionMessage待機中など）
            # に対応するToolMessageが生成されないままチェックポイントが
            # コミット済みのAIMessage(tool_calls)だけを残し、次回グラフ実行時に
            # 「tool_callsに対応するToolMessageが無い」不整合を起こす
            # （langchain-ai/langgraph#6726と同種の問題）。_PlanDeniedInterrupt
            # と同じ手法で、プレースホルダのToolMessageを補完コミットしてから
            # キャンセルを再送出する。
            #
            # P1: 無条件で1行WARNINGを出す（孤立tool_callの有無に関わらず）。
            # 例外チェーン（__cause__/__context__）を記録し、なぜキャンセル
            # されたかの診断に使う。
            logging.getLogger(__name__).warning(
                "on_message: CancelledErrorを検知 [%s, cause=%r, context=%r]",
                describe_current_task(),
                repr(exc.__cause__),
                repr(exc.__context__),
            )
            try:
                state = await graph.aget_state(config)
                messages = state.values.get("messages", []) if state else []
                orphaned = _find_orphaned_tool_calls(messages)
                if orphaned:
                    await graph.aupdate_state(
                        config,
                        {
                            "messages": [
                                ToolMessage(
                                    content=("エラー: ユーザーの停止操作等により、" "このツール呼び出しの実行が中断されました。"),
                                    tool_call_id=tc["id"],
                                    name=tc.get("name", ""),
                                )
                                for tc in orphaned
                            ]
                        },
                        as_node="tools",
                    )
                    logging.getLogger(__name__).warning(
                        "on_message: CancelledErrorを検知し、孤立したtool_calls(%d件)に"
                        "プレースホルダのToolMessageを補完してチェックポイントを修復しました",
                        len(orphaned),
                    )
            except _CheckpointerTimeout:
                # checkpointerのロックが固着している可能性が高いが、
                # この例外を伝播させるとCancelledErrorが消える。
                # ログのみに留めて元のCancelledErrorの伝播を妨げない
                # （次回メッセージ送信時のaget_tupleで同じタイムアウトが
                # 再検知され、その時のturn_broken_exc処理で自己修復する）。
                logging.getLogger(__name__).debug(
                    "CancelledError処理中のcheckpointer操作がタイムアウトしました",
                    exc_info=True,
                )
            raise
        except Exception as exc:  # noqa: BLE001
            # 上のどの except 節にも一致しない未分類の例外に対する保険。
            # 個別に列挙した例外型（LLM_CONNECTION_ERRORS等）から1つ漏れて
            # いた場合、従来はここで捕捉されずChainlit最上位ハンドラまで
            # 伝播し、turn_broken_exc が一切セットされないままターンが
            # 終わってしまっていた（_rebuild_checkpointer/_rebuild_graphが
            # 一度も呼ばれず、壊れたLLMクライアント/checkpointerを抱えた
            # セッションが永久に復旧しない。本番incident・2026-07-31:
            # httpx.ReadErrorがLLM_CONNECTION_ERRORSに含まれておらず発生。
            # 根本対応はsrc.llm.LLM_CONNECTION_ERRORSをhttpx.TransportError
            # 基底クラスへ広げたことだが、今後また未知の例外型が漏れても
            # セッションを自己修復可能な状態に保つため、ここでも
            # except LLM_CONNECTION_ERRORS と同じ回復処理に倒す）。
            turn_broken_exc = exc
            checkpointer_needs_rebuild = True
            logging.getLogger(__name__).error(
                "on_message: 未分類の例外を検知しました（%s）: %s [%s]",
                type(exc).__name__,
                exc,
                describe_current_task(),
                exc_info=True,
            )
            if thinking is not None:
                thinking.end = utc_now()
                await thinking.update()
                thinking = None
            if answer is not None:
                await answer.send()
                answer = None
            await _finalize_orphaned_steps(steps, "unclassified_error")
        finally:
            # astream_events() の非同期ジェネレータは、async for が
            # ThinkingLoopDetected/GraphRecursionError で中断された場合、
            # 明示的に aclose() しないとLangGraph内部のバックグラウンド
            # タスクやcheckpointerのロックが残留しうる（GC任せだと即座に
            # aclose()される保証がない）。次のイテレーションで同じ
            # thread_id に対し astream_events() を呼び直す前に必ず閉じる。
            #
            # ChatLlamaCpp._astream（src/llm.py）と同じ設計方針:
            # asyncio.wait_for() は渡されたコルーチンを ensure_future で
            # 別タスクにラップするため、asyncio.CancelScope の「開いたの
            # 同じタスクで閉じる」制約を破りうる（2026-07-28 incident の
            # 直接原因）。asyncio.timeout() は現在のタスクに直接タイム
            # アウトを注入し新規タスクを生成しないため、同一タスク制約を
            # 破らずにタイムアウトを維持できる（両箇所とも asyncio.timeout
            # を使い、相互参照コメントを付けた）。
            aclose_failed = False
            try:
                async with asyncio.timeout(5.0):
                    await event_stream.aclose()
            except TimeoutError:
                aclose_failed = True
                logging.getLogger(__name__).warning(
                    "astream_events のクローズ(aclose)が5秒でタイムアウトしました",
                    exc_info=True,
                )
            except Exception:
                aclose_failed = True
                logging.getLogger(__name__).debug("astream_events のクローズ中に例外が発生しました", exc_info=True)
            # ThinkingLoopDetected/GraphRecursionError以外（停止ボタンによる
            # CancelledError等、上のexcept節が捕捉しない中断）でも steps が
            # 未finalizeのまま残りうる。安全網として finally で必ず片付ける
            # （空dictなら _finalize_orphaned_steps は何もしない）。
            await _finalize_orphaned_steps(steps, "interrupted")

        # P2: ThinkingLoopDetected のグラフ再構築・nudge注入を finally 後へ延期。
        # これにより「aclose→rebuild→新リクエスト」の順序が保証される。
        # 却下する案: finally の後始末を別タスクに切り離す案は、今回の障害の
        # 直接原因（後始末が別タスクに漏れたこと）を悪化させるだけなので採用しない。
        if loop_exc is not None:
            if loop_attempt < loop_max_retries:
                if loop_exc.client_broken:
                    # ストリームの後始末（_astreamのfinally節でのagen.aclose()）
                    # が失敗し、httpx.AsyncClientの内部状態が壊れている
                    # 可能性が高い（ThinkingLoopDetected.client_brokenの
                    # docstring参照）。_rebuild_graphは新しいクライアントを
                    # 用意するだけで、壊れた旧クライアントの根底のTCP接続を
                    # 明示的には切断しないため、そのままだとllama-server側に
                    # 旧ストリームが残り続け、次のリトライが応答ヘッダー
                    # 待ちで恒久的にハングしうる（本番incident・2026-07-20:
                    # 7分11秒間ハング。2026-07-31: 同型の事象が再発し、
                    # ユーザーが手動キャンセルするまで復帰しなかった）。
                    # on_stopのaclose_active_llm_clientsと同じ処理を、
                    # ここでもリトライ前に呼んで旧接続を強制クローズする。
                    await aclose_active_llm_clients(thread_id)
                graph = _rebuild_graph(thread_id)
                logging.getLogger(__name__).warning(
                    "ThinkingLoopDetected: リトライ前にLLMグラフを再構築しました " "(client_broken=%s) [%s]",
                    loop_exc.client_broken,
                    describe_current_task(),
                )
                nudge_id = str(uuid.uuid4())
                loop_nudge_ids.append(nudge_id)
                text = pick_loop_nudge_message(_config.thinking_loop_guard_nudge_messages, loop_attempt)
                loop_attempt += 1
                inputs = {"messages": [HumanMessage(content=text, id=nudge_id)]}
                continue
            await cl.Message(content=(f"生成がループし、{loop_max_retries}回リトライしましたが" "改善しなかったため停止しました。")).send()
            if loop_nudge_ids:
                await graph.aupdate_state(config, {"messages": [RemoveMessage(id=i) for i in loop_nudge_ids]})
            return

        if turn_broken_exc is not None:
            if checkpointer_needs_rebuild:
                await _rebuild_checkpointer(thread_id)
                logging.getLogger(__name__).warning(
                    "チェックポインタを再構築しました [%s]",
                    describe_current_task(),
                )
            _rebuild_graph(thread_id)
            logging.getLogger(__name__).warning(
                "エラーのためグラフを再構築しました: %s [%s]",
                turn_broken_exc,
                describe_current_task(),
            )
            await cl.Message(content="通信エラーのため中断しました。Llamaサーバーの再起動後、" "少し待って「続けて」と送信してください。").send()
            return

        if thinking is not None:
            thinking.end = utc_now()
            await thinking.update()
        if answer is not None:
            await answer.send()

        # 無言終了（tool_calls も回答テキストも無いまま終わる）を検知した場合、
        # 最終回答を促す短いメッセージを注入して自動的に1回だけリトライする
        # （thinkingの長引き・コンテキスト逼迫等でこの状態に陥ることがある
        # 小型ローカルモデル対策。evals/run_case.py の
        # ainvoke_ensuring_final_text と同じ考え方）。
        if attempt < total_retries:
            state = await graph.aget_state(config)
            messages = state.values.get("messages", []) if state else []
            if is_empty_final_message(messages):
                nudge_id = str(uuid.uuid4())
                empty_nudge_ids.append(nudge_id)
                inputs = {"messages": [HumanMessage(content=EMPTY_RESPONSE_NUDGE, id=nudge_id)]}
                continue
        break

    # ループ検知・無言終了いずれの注意メッセージ（機械的な注入）も、成功して
    # 会話が完了した後は履歴に残さず取り除く（残すと長い会話ほど全量再送で
    # コンテキストを圧迫し、過去の失敗の痕跡がモデル自身の目に触れ続けることに
    # なる）。
    remove_ids = loop_nudge_ids + empty_nudge_ids
    if remove_ids:
        await graph.aupdate_state(config, {"messages": [RemoveMessage(id=i) for i in remove_ids]})

    # コンテキスト圧縮（ClaudeCodeのcompact相当）。進行中のグラフ実行が
    # 完全に終わった後のこのタイミングでのみ判定・実行する（ストリーミング
    # 中に aupdate_state すると競合するため）。
    state = await graph.aget_state(config)
    messages = state.values.get("messages", []) if state else []

    # 会話ログ（[chat_log].enabled=true の場合のみ、on_chat_start で
    # cl.user_session["chat_log_path"] が設定済み）。ユーザー発言と
    # このターンのAIの最終応答（thinking・ツール呼び出し詳細は含めない）
    # のみをテキストファイルへ追記する。
    chat_log_path = cl.user_session.get("chat_log_path")
    if chat_log_path is not None:
        final_ai_message = messages[-1] if messages else None
        ai_text = final_ai_message.content if isinstance(final_ai_message, AIMessage) else ""
        append_turn(
            chat_log_path,
            message.content,
            ai_text,
            token_usage_cumulative=cl.user_session.get("token_usage_cumulative"),
        )

    if should_compact(last_usage, len(messages), _config):
        summary_model = build_model(_config)
        new_messages = await maybe_compact(messages, summary_model, _config)
        if new_messages is not None:
            await graph.aupdate_state(
                config,
                {"messages": [RemoveMessage(id=m.id) for m in messages] + new_messages},
            )
