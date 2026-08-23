"""LangGraph ツール（progressive disclosure 第2・第3段階）。

仕様: https://agentskills.io/specification

LangChain の @tool として定義する。read_skill/read_skill_file/run_script/analyze_image は
すべて LangGraph のツールコールとして実行され、グラフのトレースに乗る
（Chainlit 側で可視化するため）。dispatch_agent のみ内部で独立した ReAct
ループ（subagent.py）を回す特殊なツールで、その内部の呼び出しはグラフの
トレースに乗らない（意図的、コンテキスト節約のため）。

- read_skill      … 第2段階(Read):    SKILL.md 本文全体を読む
- read_skill_file       … 第3段階(Execute): references/assets 等を必要時に読む
- run_script      … 第3段階(Execute): scripts/ 配下のスクリプトを実行する
- execute_python_code … 第3段階(Execute): LLMが生成したPythonコードをその場で実行する
- get_tool_source … 第3段階(Execute): run_script が失敗した際、原因調査用にスクリプトの絶対パスを返す
- read_tool(Read)/glob_tool(Glob)/grep_tool(Grep) … ローカルファイルシステム上の
  任意の絶対パスに対する読込・ファイル名検索・全文検索（src/file_tools.py に
  ロジック本体、ClaudeCode の同名ツールに合わせた名前）
- json_query      … JSON/dictへのJMESPathクエリ（src/file_tools.py にロジック本体）
- list_path_memory … 現在の会話のパスメモリー（@N）登録内容を一覧表示する
- analyze_image   … 第3段階(Execute): 画像ファイルをVision対応モデルへ見せ、LLM自身が内容を解析する
- show_image      … 第3段階(Execute): 画像ファイルをチャットUIにプレビュー表示するだけ
  （LLM自身は内容を見ない。provide_download の画像版。ユーザーへの「表示して」「見せて」はこちら）
- dispatch_agent  … タスクをサブエージェントへ委譲し、最終回答のみを受け取る。完了までの間、
  進捗（経過時間・反復回数）を人間向けにチャットへ直接通知する。設定した安全上限を超えても
  なお完了しない場合のみ job_id を返してターンを終える（フォールバック）
- check_dispatch_agent_job / stop_dispatch_agent_job … 上記フォールバック時のみ使う、
  ジョブの状況確認・強制終了
- ask_user_question(AskUserQuestion) … ユーザーへ自由記述で追加質問する。labels省略時は
  単発質問（Chainlit AskUserMessage）、labels指定時は複数項目をまとめて提示する
  フォーム（Chainlit AskElementMessage + CustomElement）
- ask_user_choice … ユーザーへ選択肢形式で追加質問する。multi_select省略/False時は
  単一選択（Chainlit AskActionMessage）、multi_select=True時は複数選択可能な
  チェックボックスフォーム（Chainlit AskElementMessage + CustomElement）
- create_memory / update_memory / delete_memory / read_memory / search_memory /
  list_memories … スレッドをまたぐ永続メモリー（src/memory.py）の読み書き。
  主エージェントのみに公開し、dispatch_agent のサブエージェントには渡さない。

セキュリティ: read_skill_file / run_script / get_tool_source は必ず skills
ディレクトリ配下に限定する（_safe_path でディレクトリトラバーサルを拒否）。保存・実行の
パスがコードから追える事。analyze_image / read_tool(Read) / glob_tool(Glob) /
grep_tool(Grep) は読み込み系ツールのため、パスの制限は行わない
（_resolve_analyze_image_path / _resolve_file_tools_path）。ただし
`_tmp_<thread_id>`（他セッションの一時ディレクトリ）だけは例外で、
自セッション以外は読み取り不可（_foreign_tmp_dir_error）。
メモリー系ツールも同様に memory.py 側の _safe_memory_path で memory ルート配下に限定する。

設定（skills ルート・Python 実行ファイル・タイムアウト・サブエージェント設定・
メモリールート）はモジュール globals に init_tools() で一度だけ注入する。動的 import
やメタクラス等の仕掛けは使わない。
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import chainlit as cl
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolNode

from . import file_tools, memory, path_memory
from .agent_types import AgentType
from .config import (
    DEFAULT_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE,
    DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE,
    Config,
    expand_config_vars,
)
from .images import image_followup_message, is_image_file, to_data_url
from .llm import get_current_session
from .ask_relay import clear_pending_ask, dispatch_to_live_viewers, register_pending_ask
from .subagent import is_truncated_result, run_subagent

logger = logging.getLogger(__name__)

# 現在の実行がサブエージェント（dispatch_agent 経由）の内部かどうかを示す
# コンテキスト変数。dispatch_agent がサブエージェント起動前に True を設定する。
# contextvars は asyncio.gather() が生成する子タスクにも起動時点の値が
# コピーされるため、run_subagent 内から並列実行される各ツール呼び出しからも
# 参照できる（_check_file_tools_duplicate の重複ガードが、
# [file_tools_duplicate_guard].carry_over_to_main=false のときに
# メインエージェント/サブエージェントの呼び出し履歴を分けるために使う）。
_IN_SUBAGENT: contextvars.ContextVar[bool] = contextvars.ContextVar("_in_subagent", default=False)

# 現在の dispatch_agent 呼び出しを一意に識別するID（サブエージェント外では None）。
# 重複ガードの集合をサブエージェント実行ごとに分けるために使う。サブエージェントの
# 会話履歴は委譲元にも他のサブエージェントにも共有されず、返るのは最終回答テキスト
# だけなので、あるサブエージェントが読んだ画像・ファイルを別のサブエージェントや
# メインが読み直すのは重複ではなく正当な再取得である（同一集合で数えると、
# 1件目のサブエージェントが読んで返しきれなかった画像を誰も読み直せなくなる）。
_SUBAGENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("_subagent_run_id", default=None)

# 現在実行中のサブエージェントの agent_type 名（サブエージェント外では None）。
# _AGENT_TYPE_RUN_SCRIPT_SKILL_ALLOWLIST によるスキル制限のチェックに使う。
_SUBAGENT_AGENT_TYPE: contextvars.ContextVar[str | None] = contextvars.ContextVar("_subagent_agent_type", default=None)

# agent_type ごとに run_script で呼んでよいスキル名を制限する（未登録の
# agent_type は制限なし）。agents/*.md のプロンプト文面だけでこの制約を
# 課しているagent_typeは、低パラメータモデルでは指示を無視して他スキルの
# 読み込み専用スクリプトまで呼んでしまうことがある（本番同等のeval
# ケースで実際に発生: explore が本来analyze-docs専用のread_excel.pyを
# 呼んでxlsxを調査し、analyze-docs向けの誤診断防止ルールが適用されない
# まま処理が進んでしまった）。プロンプトの記述と実際の許可を一致させる
# ため、コード側でも強制する。
_AGENT_TYPE_RUN_SCRIPT_SKILL_ALLOWLIST: dict[str, frozenset[str]] = {
    "explore": frozenset({"web-search"}),
}


def _duplicate_guard_session_key(base_key: str) -> str:
    """重複ガードの記録先キーを、実行コンテキスト（メイン／各サブエージェント）ごとに分ける。

    [file_tools_duplicate_guard].carry_over_to_main が false のとき、
    サブエージェント内の呼び出しはメインエージェント側の判定に影響させない。
    さらにサブエージェント同士も互いに影響しないよう、dispatch_agent の実行ID
    ごとに別キーにする。

    Args:
        base_key: メインエージェント用の記録先キー
            （例: "file_tools_call_signatures"）。

    Returns:
        実際に cl.user_session へ保存するキー。
    """
    cfg = _LLM_CONFIG
    guard_carry_over = cfg.file_tools_duplicate_guard_carry_over_to_main if cfg else True
    if guard_carry_over:
        return base_key
    run_id = _SUBAGENT_RUN_ID.get()
    if run_id is None:
        return base_key
    return f"{base_key}_subagent_{run_id}"


# dispatch_agent の実際のLLM呼び出しの同時実行数をガードするセマフォ。
# ToolNode は同一AIMessage内の複数tool_callsを並列実行するため、モデルが
# dispatch_agent を1ターンで複数回呼ぶと、単一インスタンスのllama-server
# へ複数リクエストが同時に飛ぶ。system_prompt.md で「並列発行しない」と
# 指示するだけでは低パラメータモデルの遵守が不確実な上、llama-serverの
# --parallel スロット数を超えて並列実行するとチェックポイントの破損
# （AIMessageのtool_callsに対応するToolMessageが欠落し、次のモデル呼び出し
# でLangGraphがValueErrorを送出する）が本番で確認されたため、モデルの
# 挙動に関係なく確実に効くコード側のガードとして同時実行数を制限する
# （並列呼び出し自体は許可し、超過分は待ち順に処理される）。
# init_tools() が config.ini の [subagent].max_parallel に応じて
# _DISPATCH_AGENT_MAX_PARALLEL を再設定する。0以下はガード無効（無制限）を表す。
# セッション（llm.get_current_session() が返す thread_id）ごとに独立した
# Semaphore を遅延生成する辞書で保持する（グローバル単一 Semaphore だと、
# 無関係な複数セッションのdispatch_agent呼び出しが互いに待ち合ってしまうため。
# 全体のHTTPリクエスト総数は [llm].max_concurrent_requests が別途一元管理する）。
# 既定値1は init_tools() 未実行時（テスト等）の安全側フォールバック。
_DISPATCH_AGENT_MAX_PARALLEL: int = 1
_DISPATCH_AGENT_SEMAPHORES: "dict[str | None, asyncio.Semaphore]" = {}

# メインエージェントの全ツール呼び出し（ImageAwareToolNode 経由）の同時実行数を
# ガードするセマフォ。init_tools() が config.ini の [graph].max_parallel に
# 応じて _TOOL_CALL_MAX_PARALLEL を再設定する。0以下はガード無効を表す。
# 上記 _DISPATCH_AGENT_SEMAPHORES と同じ理由・同じ辞書方式でセッション毎に
# 独立させる。既定値1は init_tools() 未実行時（テスト等）の安全側フォールバック。
_TOOL_CALL_MAX_PARALLEL: int = 1
_TOOL_CALL_SEMAPHORES: "dict[str | None, asyncio.Semaphore]" = {}


def _get_session_semaphore(registry: "dict[str | None, asyncio.Semaphore]", max_parallel: int) -> "asyncio.Semaphore | None":
    """現在のセッション（llm.get_current_session()）専用の Semaphore を
    registry から取得する。無ければ max_parallel で新規生成して登録する
    （評価ハーネス等、Chainlitセッションを持たない呼び出し元は session_id が
    None になり、その呼び出し元同士で1つの Semaphore を共有する）。
    max_parallel が0以下ならガード無効として None を返す。
    """
    if max_parallel <= 0:
        return None
    session_id = get_current_session()
    sem = registry.get(session_id)
    if sem is None:
        sem = asyncio.Semaphore(max_parallel)
        registry[session_id] = sem
    return sem


def forget_session_tool_semaphores(session_id: str) -> None:
    """セッション終了時（@cl.on_chat_end）に、そのセッション専用の Semaphore
    エントリを辞書から削除する。Semaphore 自体は参照が無くなり次第GCされるが、
    辞書キー（session_id文字列）がプロセス寿命中ずっと残り続けるのを防ぐのが
    目的（src/llm.py の forget_session() と同じ理由）。
    """
    _TOOL_CALL_SEMAPHORES.pop(session_id, None)
    _DISPATCH_AGENT_SEMAPHORES.pop(session_id, None)


async def _tool_call_semaphore_wrap(request, execute):
    """ToolNode(awrap_tool_call=...) 用インターセプタ。

    全ツール呼び出し（同期/非同期問わず _execute_tool_async 経由で正しく
    振り分けられた実行）を、現在のセッション専用の Semaphore（_TOOL_CALL_SEMAPHORES
    参照）で待ち合わせる。dispatch_agent は専用の _DISPATCH_AGENT_SEMAPHORES でも
    重ねてガードされる形になるが、単に入れ子になるだけで問題ない。
    """
    sem = _get_session_semaphore(_TOOL_CALL_SEMAPHORES, _TOOL_CALL_MAX_PARALLEL)
    if sem is None:
        return await execute(request)
    if sem.locked():
        tool_name = request.tool_call.get("name")
        logger.info("tool_call: 空きスロットが無いため待機します tool=%r", tool_name)
    async with sem:
        return await execute(request)


@dataclass(frozen=True)
class ResolvedAgentType:
    """AgentType のツール名を実際の BaseTool へ解決した実行時表現。

    init_tools() が agent_type_defs（frontmatterの生値）から組み立て、
    _AGENT_TYPES に保持する。dispatch_agent はこれを引いて run_subagent()
    に渡す。
    """

    description: str
    system_prompt: str
    tools: list[BaseTool]


# init_tools() で注入されるモジュール設定（起動時に一度だけ設定）。
# 複数ディレクトリ対応（scan_skills()/scan_agent_types() と同じマージ設計。
# 例: [*locohane_skills_dirs, skills_dir]。前方のディレクトリが優先される）。
_SKILLS_ROOTS: list[Path] | None = None
_SCRIPT_PYTHON: str = "python"
_SCRIPT_TIMEOUT: int = 60
_CODE_EXEC_ENABLED: bool = False
_SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS: int = 3600
_SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS: int = 1800
_SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS: int = 20
_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE: str = DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE
_SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS: int = 0
_SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS: int = 20
_DEFAULT_WORKDIR: Path | None = None
_LLM_CONFIG: Config | None = None
_AGENT_TYPES: dict[str, ResolvedAgentType] = {}
_SUBAGENT_MAX_ITERATIONS: int = 6
_DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS: int = 1800
_DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS: int = 20
_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE: str = DEFAULT_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE
_DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS: int = 1800
_DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS: int = 20
_DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES: int = 3
_MEMORY_ROOT: Path | None = None
_PLANS_DIR: Path | None = None
_HELP_PATH: Path | None = None
_PATH_MEMORY_DIR: Path | None = None
_PATH_MEMORY_MAX_ENTRIES: int = 500
_SRC_DIR: Path = Path(__file__).parent  # src/ディレクトリ（path_memory.py 等がある）
_APPROVAL_TIMEOUT_SECONDS: int = 300
_ASK_USER_QUESTION_TIMEOUT_SECONDS: int = 60
_ASK_USER_CHOICE_TIMEOUT_SECONDS: int = 90
_PLAN_BADGE_ALLOW_UNLOCK: bool = True
_PLAN_RESET_APPROVAL_ON_RECREATE: bool = True
_PLAN_REQUIRE_PLANNER_DISPATCH: bool = True
_PLAN_AUTO_APPROVE: bool = False

# run_script は本来「書き込み系ツール」として一律に計画承認を要求するが、
# 副作用のない純粋な読み取り専用スクリプトはここに (skill_name, script_filename)
# を明示的に登録することで承認チェックを免除できる。init_tools() が config.ini の
# [scripts].plan_approval_exempt_scripts から注入するため、ここでは空集合で初期化する。
_PLAN_APPROVAL_EXEMPT_SCRIPTS: set[tuple[str, str]] = set()


# 「無期限待ち」の代替として使う実質無期限の秒数（2^31-1 ≈ 68年）。
# chainlit.types.AskSpec は pydantic dataclass で timeout: int（Optional化されて
# いない）を要求するため、timeout=None を渡すと ValidationError
# 「timeout: Input should be a valid integer」がツール実行時の例外として
# LLMに見えてしまう（本番ログ 2026-07-25 で確認）。None は使わず、この定数を渡す。
_UNLIMITED_TIMEOUT_SECONDS = 2**31 - 1


def _resolve_ask_timeout(seconds: int) -> int:
    """config.ini のタイムアウト秒数を cl.Ask*Message(timeout=...) 用の値へ変換する。

    0以下（無期限待ち指定）は _UNLIMITED_TIMEOUT_SECONDS（実質無期限）に変換する。
    """
    return seconds if seconds > 0 else _UNLIMITED_TIMEOUT_SECONDS


def init_tools(
    skills_root: Path | str | Sequence[Path | str],
    script_python: str,
    script_timeout: int,
    llm_config: Config,
    agent_type_defs: list[AgentType],
    subagent_max_iterations: int,
    default_workdir: Path,
    memory_root: Path,
    help_path: Path,
    path_memory_dir: Path,
    path_memory_max_entries: int,
    code_exec_enabled: bool = False,
    approval_timeout_seconds: int = 300,
    ask_user_question_timeout_seconds: int = 60,
    ask_user_choice_timeout_seconds: int = 90,
    plan_badge_allow_unlock: bool = True,
    dispatch_agent_max_parallel: int = 1,
    graph_tool_max_parallel: int = 1,
    script_background_max_runtime_seconds: int = 3600,
    script_background_job_retention_seconds: int = 1800,
    script_background_min_poll_interval_seconds: int = 20,
    script_background_min_poll_message: str = DEFAULT_SCRIPT_BACKGROUND_MIN_POLL_MESSAGE,
    script_background_inline_wait_max_seconds: int = 0,
    script_background_progress_push_interval_seconds: int = 20,
    dispatch_agent_background_job_retention_seconds: int = 1800,
    dispatch_agent_background_min_poll_interval_seconds: int = 20,
    dispatch_agent_background_min_poll_message: str = DEFAULT_DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE,
    dispatch_agent_background_inline_wait_max_seconds: int = 1800,
    dispatch_agent_background_progress_push_interval_seconds: int = 20,
    dispatch_agent_background_llm_timeout_max_retries: int = 3,
    plan_approval_exempt_scripts: Iterable[tuple[str, str]] = (),
    plans_dir: Path | None = None,
    plan_reset_approval_on_recreate: bool = True,
    plan_require_planner_dispatch: bool = True,
    plan_auto_approve: bool = False,
) -> None:
    """ツールが使う設定を注入する（app 起動時に一度だけ呼ぶ）。

    read_skill / read_skill_file / run_script / dispatch_agent / メモリー系
    ツールはいずれもモジュール globals を参照するため、グラフ構築より前に
    必ずこの関数を呼んでおく必要がある。

    Args:
        skills_root: skills ディレクトリのルートパス、またはその並び。
            複数渡した場合は渡した順に探索され、前方のディレクトリが優先
            される（例: [*locohane_skills_dirs, skills_dir]）。各要素は
            resolve() により絶対パスへ正規化した上でモジュール変数に保持する。
        script_python: run_script が .py スクリプトを起動する際に使う
            Python 実行ファイル（例: "python", "C:\\path\\to\\python.exe"）。
        script_timeout: run_script のタイムアウト秒数。
        llm_config: dispatch_agent がサブエージェント用モデルを構築する
            際に使うアプリ設定（build_model に渡す）。
        agent_type_defs: scan_agent_types() が返したエージェント種別定義
            （system_prompt は {{skills}} 差し込み済みであること）。
            dispatch_agent の agent_type 引数で選べる種別一覧として、
            ツール名を実際の BaseTool に解決した上で _AGENT_TYPES に
            保持する。
        subagent_max_iterations: dispatch_agent の ReAct ループの
            最大反復回数。
        default_workdir: run_script の既定の作業ディレクトリ（cwd）。
            Chainlit の ChatSettings でセッション単位の作業ディレクトリが
            指定されなかった場合に使われる（config.ini の
            [default_workdir].dir 由来）。
        memory_root: 永続メモリーストアのルートディレクトリ（config.ini の
            [paths].memory_dir 由来）。create_memory 等のメモリー系
            ツールが参照する。
        help_path: help ツールが読み込んで返すヘルプ本文ファイルの絶対パス
            （config.ini の [paths].help_path 由来）。
        path_memory_dir: パスメモリー（src/path_memory.py）のレジストリ
            ファイル保存先ディレクトリ（config.ini の [path_memory].dir 由来）。
            run_script/execute_python_code のサブプロセスへ環境変数
            AGENT_PATH_MEMORY_DIR として渡すほか、Read/Glob/Grep/analyze_image/
            run_script の @N 解決でも使う。
        path_memory_max_entries: パスメモリー1会話あたりの登録上限件数
            （config.ini の [path_memory].max_entries 由来）。
        code_exec_enabled: execute_python_code ツール（LLMが生成した
            Pythonコードをその場で実行する）の有効/無効。False の場合、
            ツールは呼び出されてもエラー文字列を返すのみでコードは
            実行されない（config.ini の [scripts].code_execution_enabled 由来）。
        approval_timeout_seconds: create_plan/approve_plan の計画承認で
            ユーザーの応答を待つ秒数（config.ini の
            [user_response_timeouts].approval_seconds 由来）。0以下は無期限待ちを意味する。
        ask_user_question_timeout_seconds: AskUserQuestion（自由記述の
            質問。labels省略時は単発質問、labels指定時は複数項目フォーム）
            がユーザーの応答を待つ秒数（config.ini の
            [user_response_timeouts].ask_user_question_seconds 由来）。0以下は無期限待ちを意味する。
        ask_user_choice_timeout_seconds: ask_user_choice がユーザーの
            応答を待つ秒数（config.ini の
            [user_response_timeouts].ask_user_choice_seconds 由来）。0以下は無期限待ちを意味する。
        plan_badge_allow_unlock: 送信ボタン付近の Plan Mode / Edit Automatically
            バッジをクリックした際、Plan Mode → Edit Automatically 方向
            （ロック解除）も許可するか。False の場合はロック方向のクリックのみ
            有効になる（config.ini の [plan].allow_badge_unlock 由来）。
        dispatch_agent_max_parallel: dispatch_agent ツールの実LLM呼び出しを、
            1セッションあたり _DISPATCH_AGENT_SEMAPHORES で同時に何件まで
            許可するか。1以上はその値までにガードし（既定1＝完全直列化）、
            0以下はガードを無効化して並列呼び出しをそのまま許可する
            （config.ini の [subagent].max_parallel 由来）。
        graph_tool_max_parallel: メインエージェントの全ツール呼び出し
            （ImageAwareToolNode）を、1セッションあたり _TOOL_CALL_SEMAPHORES
            で同時に何件まで許可するか。1以上はその値までにガードし
            （既定1＝完全直列化）、0以下はガードを無効化して並列呼び出しを
            そのまま許可する（config.ini の [graph].max_parallel 由来）。
        script_background_max_runtime_seconds: run_script_background で
            起動したプロセスを強制終了するまでの上限秒数（config.ini の
            [scripts].background_max_runtime_seconds 由来）。
        script_background_job_retention_seconds: run_script_background の
            ジョブが終了後、check_script_job で一度も取得されないまま
            registry に残ってよい秒数（config.ini の
            [scripts].background_job_retention_seconds 由来）。
        script_background_min_poll_interval_seconds: check_script_job が
            「実行中」ステータスを返した直後、同じジョブへの次の
            check_script_job 呼び出しを許可するまでの最短間隔秒数。
            SKILL.md やツールのdocstringでLLMに「数秒おきに呼び直さない」
            よう指示しても、指示に従わないローカルLLMでは無視され得るため、
            サーバー側で強制する（config.ini の
            [scripts].background_min_poll_interval_seconds 由来）。
            0以下を指定すると強制を無効化する。
        script_background_min_poll_message: 上記の最短間隔未満で
            check_script_job が呼ばれた際にLLMへ返すメッセージのテンプレート
            （config.ini の [scripts].background_min_poll_message 由来）。
            .format() で {wait_remaining}（あと何秒待つべきか）/
            {job_id}（対象ジョブID、repr形式）/{min_interval}（設定値
            そのもの）を埋め込める。
        script_background_inline_wait_max_seconds: run_script_background/
            execute_python_code_background がジョブ完了をLLMを介さず
            コード側で待つ上限秒数（config.ini の
            [scripts].background_inline_wait_max_seconds 由来）。この秒数を
            超えてもまだ実行中の場合のみ job_id を返してLLMへ制御を戻す。
            ジョブ自体はこの上限に達してもキャンセルされず動き続ける。
            0以下を指定すると無期限に待つ（フォールバック経路が事実上無効に
            なる）。
        script_background_progress_push_interval_seconds: 上記の待機中、
            人間向けに経過秒数・標準出力/標準エラー末尾をチャットへ直接送る
            間隔（秒）。cl.Message送信のみでLLM呼び出しを伴わずトークンを
            消費しない（config.ini の
            [scripts].background_progress_push_interval_seconds 由来）。
        dispatch_agent_background_job_retention_seconds: dispatch_agent の
            ジョブが終了後、check_dispatch_agent_job で一度も取得されないまま
            registry に残ってよい秒数（config.ini の
            [subagent].background_job_retention_seconds 由来）。
        dispatch_agent_background_min_poll_interval_seconds: check_dispatch_agent_job が
            「実行中」ステータスを返した直後、同じジョブへの次の
            check_dispatch_agent_job 呼び出しを許可するまでの最短間隔秒数
            （config.ini の [subagent].background_min_poll_interval_seconds 由来。
            script_background_min_poll_interval_seconds と同じ理由でサーバー側強制）。
            0以下を指定すると強制を無効化する。
        dispatch_agent_background_min_poll_message: 上記の最短間隔未満で
            check_dispatch_agent_job が呼ばれた際にLLMへ返すメッセージの
            テンプレート（config.ini の [subagent].background_min_poll_message 由来）。
            プレースホルダーは script_background_min_poll_message と同じ。
        dispatch_agent_background_inline_wait_max_seconds: dispatch_agent
            がジョブ完了をLLMを介さずコード側で待つ上限秒数（config.ini の
            [subagent].background_inline_wait_max_seconds 由来）。この秒数を
            超えてもまだ実行中の場合のみ job_id を返してLLMへ制御を戻す。
            ジョブ自体はこの上限に達してもキャンセルされず動き続ける。
            0以下を指定すると無期限に待つ（フォールバック経路が事実上無効になる）。
        dispatch_agent_background_progress_push_interval_seconds: 上記の待機中、
            人間向けに経過秒数・反復回数・進捗メモ末尾をチャットへ直接送る間隔
            （秒）。cl.Message送信のみでLLM呼び出しを伴わずトークンを消費しない
            （config.ini の [subagent].background_progress_push_interval_seconds 由来）。
        dispatch_agent_background_llm_timeout_max_retries: dispatch_agent
            実行中のLLM呼び出しがタイムアウトした場合、モデルを再構築してから
            同じ反復を再試行する最大回数（config.ini の
            [subagent].background_llm_timeout_max_retries 由来）。dispatch_agent は
            常にこの設定を使う（旧・同期版が使っていた即時打ち切りは廃止済み）。
        plan_approval_exempt_scripts: run_script/run_script_background の
            計画承認（Plan Mode）を免除する、副作用のない読み取り専用
            スクリプトのホワイトリスト。(skill_name, script_filename) の
            並び（config.ini の [scripts].plan_approval_exempt_scripts 由来）。
        plans_dir: create_plan が detail_markdown 引数を渡した際に詳細計画
            Markdownを書き出す保存先ディレクトリ（config.ini の
            [paths].plans_dir 由来）。None の場合は detail_markdown を
            渡してもファイル保存をスキップする。
        plan_reset_approval_on_recreate: 既に Edit Automatically（計画承認済み）
            の状態で create_plan が再度呼ばれた際、plan_approved を強制的に
            False へ戻す（Plan Mode へ戻す）か。True（既定）なら常に戻し
            approve_plan による再承認を必須にする。False なら承認済み状態を
            維持したまま steps だけ差し替える（未承認状態からの呼び出しは
            この設定に関わらず常に Plan Mode のまま）。
            （config.ini の [plan].reset_approval_on_recreate 由来）。
        plan_require_planner_dispatch: create_plan を呼ぶ前に、同一ターンで
            dispatch_agent(agent_type="planner") が完了していることを必須に
            するか。True（既定）なら未実施の場合 create_plan はエラーを返す。
            create_plan が成功するたびにフラグは消費される。
            （config.ini の [plan].require_planner_dispatch 由来）。
        plan_auto_approve: True の場合、approve_plan 呼び出し時にユーザーへの
            確認（承認/却下ボタンの表示・応答待ち）を一切行わず、その場で
            自動的に承認済み扱いにする。False（既定）なら従来通りユーザーの
            明示的な承認を必須にする（config.ini の [plan].auto_approve 由来）。

    Returns:
        None。副作用としてモジュール globals を更新するのみ。
    """
    global _SKILLS_ROOTS, _SCRIPT_PYTHON, _SCRIPT_TIMEOUT
    global _CODE_EXEC_ENABLED
    global _SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS, _SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS
    global _SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS, _SCRIPT_BACKGROUND_MIN_POLL_MESSAGE
    global _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS, _SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS
    global _DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS, _DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS
    global _DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE
    global _DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS, _DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS
    global _DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES
    global _DEFAULT_WORKDIR, _LLM_CONFIG, _AGENT_TYPES, _SUBAGENT_MAX_ITERATIONS
    global _MEMORY_ROOT
    global _PLANS_DIR
    global _HELP_PATH
    global _PATH_MEMORY_DIR, _PATH_MEMORY_MAX_ENTRIES
    global _APPROVAL_TIMEOUT_SECONDS, _ASK_USER_QUESTION_TIMEOUT_SECONDS
    global _ASK_USER_CHOICE_TIMEOUT_SECONDS
    global _PLAN_BADGE_ALLOW_UNLOCK
    global _PLAN_RESET_APPROVAL_ON_RECREATE
    global _PLAN_REQUIRE_PLANNER_DISPATCH
    global _PLAN_AUTO_APPROVE
    global _DISPATCH_AGENT_MAX_PARALLEL
    global _TOOL_CALL_MAX_PARALLEL
    global _PLAN_APPROVAL_EXEMPT_SCRIPTS
    _skills_root_list = [skills_root] if isinstance(skills_root, (str, Path)) else list(skills_root)
    _SKILLS_ROOTS = [Path(p).resolve() for p in _skills_root_list]
    _SCRIPT_PYTHON = script_python
    _SCRIPT_TIMEOUT = script_timeout
    _CODE_EXEC_ENABLED = code_exec_enabled
    _SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS = script_background_max_runtime_seconds
    _SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS = script_background_job_retention_seconds
    _SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS = script_background_min_poll_interval_seconds
    _SCRIPT_BACKGROUND_MIN_POLL_MESSAGE = script_background_min_poll_message
    _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS = script_background_inline_wait_max_seconds
    _SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS = script_background_progress_push_interval_seconds
    _DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS = dispatch_agent_background_job_retention_seconds
    _DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS = dispatch_agent_background_min_poll_interval_seconds
    _DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE = dispatch_agent_background_min_poll_message
    _DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS = dispatch_agent_background_inline_wait_max_seconds
    _DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS = dispatch_agent_background_progress_push_interval_seconds
    _DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES = dispatch_agent_background_llm_timeout_max_retries
    _PLAN_APPROVAL_EXEMPT_SCRIPTS = set(plan_approval_exempt_scripts)
    _DEFAULT_WORKDIR = Path(default_workdir).resolve()
    _LLM_CONFIG = llm_config
    _AGENT_TYPES = _resolve_agent_types(agent_type_defs)
    _SUBAGENT_MAX_ITERATIONS = subagent_max_iterations
    _MEMORY_ROOT = Path(memory_root).resolve()
    memory.ensure_dirs(_MEMORY_ROOT)
    _PLANS_DIR = Path(plans_dir).resolve() if plans_dir else None
    if _PLANS_DIR is not None:
        _PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _HELP_PATH = Path(help_path).resolve()
    _PATH_MEMORY_DIR = Path(path_memory_dir).resolve()
    _PATH_MEMORY_MAX_ENTRIES = path_memory_max_entries
    _APPROVAL_TIMEOUT_SECONDS = approval_timeout_seconds
    _ASK_USER_QUESTION_TIMEOUT_SECONDS = ask_user_question_timeout_seconds
    _ASK_USER_CHOICE_TIMEOUT_SECONDS = ask_user_choice_timeout_seconds
    _PLAN_BADGE_ALLOW_UNLOCK = plan_badge_allow_unlock
    _PLAN_RESET_APPROVAL_ON_RECREATE = plan_reset_approval_on_recreate
    _PLAN_REQUIRE_PLANNER_DISPATCH = plan_require_planner_dispatch
    _PLAN_AUTO_APPROVE = plan_auto_approve
    _DISPATCH_AGENT_MAX_PARALLEL = dispatch_agent_max_parallel
    _TOOL_CALL_MAX_PARALLEL = graph_tool_max_parallel
    # 設定値の変更（再初期化）に追従できるよう、以前の値で生成済みの
    # セッション毎 Semaphore を破棄する。既に待機/取得中のタスクがあっても、
    # 参照を持つ限りその Semaphore オブジェクト自体は生きたまま使われ続ける
    # ため、ここでの clear() が実行中の待ち合わせを壊すことはない。
    _DISPATCH_AGENT_SEMAPHORES.clear()
    _TOOL_CALL_SEMAPHORES.clear()
    # 各ツールの description（LLMに見えるツールスキーマ説明）内の ${変数名} を
    # config.ini の実値へ展開する。@tool デコレータが docstring から description を
    # 設定するのは import 時の一度きりなので、ここで書き換えないと LLM には
    # プレースホルダーがそのまま見えてしまう。BaseTool（pydanticモデル）は
    # hashable とは限らないため、set化ではなく id() で重複を除いて回す。
    # MCPサーバー由来のツール（_MCP_TOOLS）はここでは対象外にする。description が
    # 外部サーバー由来のため、無関係な ${...} パターンを誤って展開しようとして
    # ValueError を送出するリスクがあるため（register_mcp_tools/get_all_tools 参照）。
    seen_tool_ids: set[int] = set()
    for tool_obj in [*_BASE_TOOLS, *_SUBAGENT_TOOLS]:
        if id(tool_obj) in seen_tool_ids:
            continue
        seen_tool_ids.add(id(tool_obj))
        tool_obj.description = expand_config_vars(tool_obj.description, llm_config)


def _resolve_agent_types(agent_type_defs: list[AgentType]) -> dict[str, ResolvedAgentType]:
    """AgentType のツール名一覧を実際の BaseTool へ解決する。

    tool_names が None（frontmatterで tools: 省略）の場合は、サブエージェント
    に割当可能な既定ツール一式（_SUBAGENT_TOOLS）をそのまま継承させる
    （Anthropic公式のサブエージェント仕様における「tools省略時は全ツール
    継承」を、本プロジェクトのセキュリティ境界内で読み替えたもの）。
    未知のツール名は例外を投げず警告してスキップする（scan_agent_types()
    と同じフェイルオープンしない方針）。

    Args:
        agent_type_defs: scan_agent_types() が返した定義のリスト
            （system_prompt は {{skills}} 差し込み済みであること）。

    Returns:
        エージェント種別名 -> ResolvedAgentType の辞書。
    """
    tool_lookup = {t.name: t for t in _SUBAGENT_TOOLS}
    resolved: dict[str, ResolvedAgentType] = {}
    for agent_def in agent_type_defs:
        if agent_def.tool_names is None:
            tools = list(_SUBAGENT_TOOLS)
        else:
            tools = []
            for tool_name in agent_def.tool_names:
                tool_obj = tool_lookup.get(tool_name)
                if tool_obj is None:
                    logger.warning(
                        "エージェント種別 '%s': 未知のツール '%s' をスキップ",
                        agent_def.name,
                        tool_name,
                    )
                    continue
                tools.append(tool_obj)
        resolved[agent_def.name] = ResolvedAgentType(
            description=agent_def.description,
            system_prompt=agent_def.system_prompt,
            tools=tools,
        )
    return resolved


_PATH_MEMORY_TOKEN_RE = re.compile(r"^@(\d+)$")


def _resolve_path_memory_token(value: str) -> tuple[str, str | None]:
    """value が `@N` 形式のパスメモリー参照なら実パスへ解決する。

    Args:
        value: analyze_image の relative_path や run_script の args の
            要素として渡された文字列。

    Returns:
        (解決後の値, エラーメッセージ) のタプル。
        - `@N` 形式でなければ (value, None)（従来通りそのまま使う）。
        - `@N` 形式で解決できれば (実パス, None)。
        - `@N` 形式だがパスメモリーが利用できない・未登録の場合は
          (value, "パスメモリー ... は登録されていません。..." というエラー文)。
          呼び出し側はこのエラー文をそのまま「エラー: ...」として返すこと。
    """
    if not _PATH_MEMORY_TOKEN_RE.match(value):
        return value, None
    if _PATH_MEMORY_DIR is None:
        return value, f"パスメモリー機能が利用できません: {value}"
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    resolved = path_memory.resolve(thread_id, value, _PATH_MEMORY_DIR)
    if resolved is None:
        return value, (f"パスメモリー {value} は登録されていません。" "list_path_memory ツールで現在の登録内容を確認してください。")
    return resolved, None


_PATH_MEMORY_TEXT_TOKEN_RE = re.compile(r"(?<![\w@])@(\d+)\b")


def _resolve_path_memory_tokens_in_text(text: str) -> str:
    """自由記述テキスト中に埋め込まれた `@N` パスメモリー参照を実パスへ置換する。

    dispatch_agent の task 引数のように、モデルが自然文の中でパスに触れる場面では
    文字列全体が `@N` のみであることを前提とする _resolve_path_memory_token は使えない。
    このヘルパーは文中に複数含まれうる `@N` を正規表現で検出し、解決できたものだけを
    実パスへ置き換える。未登録・パスメモリー機能が使えない等で解決できないトークンは
    エラーにせず元の `@N` 文字列のまま残す（自由文の一部が理由で呼び出し自体を
    失敗させないため）。

    Args:
        text: `@N` を含みうる自由記述テキスト（dispatch_agent の task 等）。

    Returns:
        解決できた `@N` を実パスへ置換したテキスト。`@N` を含まない、または
        どれも解決できなかった場合は text をそのまま返す。
    """

    def _replace(match: re.Match) -> str:
        token = match.group(0)
        resolved, error = _resolve_path_memory_token(token)
        return token if error else resolved

    return _PATH_MEMORY_TEXT_TOKEN_RE.sub(_replace, text)


def _task_with_work_dir_hint(task: str) -> str:
    """dispatch_agent の task 文の先頭に、実際の作業ディレクトリを事実として付与する。

    委譲元（メインエージェント）が task 文中に書いたパスは、モデルの記憶からの
    書き起こしで誤っていたり未検証だったりしうる（本番実例: 存在しない
    "E:\\akiyo\\レシピ\\md" を渡され、サブエージェントが自力でGlobして偶然
    見つけた無関係な "E:\\akiyo\\md" を答えとして扱ってしまった）。サブエージェントは
    メインエージェントの会話文脈を一切持たないため、この事実を照合する独自の
    手掛かりを持たない。_resolve_workdir() が返す確認済みの作業ディレクトリを
    task 本文の先頭へ機械的に付与することで、サブエージェント自身のツール呼び出しの
    挙動やプロンプト内指示への追従性に頼らず、常に正しいground truthを与える。

    _resolve_workdir() は init_tools() 未実行時のみ RuntimeError を送出するが、
    ヒント注入1つの失敗で dispatch_agent 全体を失敗させないよう、ここで
    握りつぶして task をそのまま返す。
    """
    try:
        work_dir = _resolve_workdir()
    except RuntimeError:
        return task
    return f"作業ディレクトリ: {work_dir}\n\n{task}"


def current_plan_status_text() -> str:
    """現在の実行計画の状態を、プロース（要約LLMの再構成・委譲元の手書き）に頼らず
    cl.user_session から直接読んで機械的に整形する。

    context_compaction.py（圧縮再注入）と _task_with_plan_hint（dispatch_agent
    委譲時の注入）の両方が同じ整形結果を共有するための single source of truth。
    計画が未作成、またはchainlitセッション文脈外（テスト・evals等で
    cl.user_session.get() が ChainlitContextException を送出する場合）は
    空文字列を返す。
    """
    try:
        plan = cl.user_session.get("plan")
    except Exception:
        return ""
    if not plan:
        return ""
    return _render_plan(plan)


def _task_with_plan_hint(task: str) -> str:
    """dispatch_agent の task 文の先頭に、現在の実行計画を事実として付与する。

    本番インシデント: create_plan の detail_markdown（プロース）は1ファイル
    成果物を約束していたが、実際の steps は月間・週間版を別々の
    dispatch_agent 呼び出しへ分割委譲しており、委譲されたworkerは
    「月間版を作って」としか伝えられず計画全体の約束を知る術が無かった。
    _task_with_work_dir_hint と同じ「委譲元の書き起こしを信用せず、
    cl.user_session の ground truth を機械的に注入する」パターンをここにも
    適用し、委譲元がtask文に計画全体を書き忘れてもサブエージェントが
    常に計画全体・現在位置を認識できるようにする。
    """
    status = current_plan_status_text()
    if not status:
        return task
    return f"[実行計画（進行中・最優先タスク）]\n{status}\n\n{task}"


_RAW_UNC_PATH_RE = re.compile(r"\\\\[A-Za-z0-9_.\-]+(?:\\[A-Za-z0-9_.\-]+)+")


def register_raw_unc_paths_in_text(text: str) -> str:
    """ユーザー入力テキスト中の生UNCパスを path_memory へ事前登録し `@N` へ置換する。

    低パラメータモデルはツール呼び出しのJSON argsにUNCパス（`\\\\server\\share\\...`）
    を書き起こす際にバックスラッシュのエスケープを誤りやすい（ISSUE-002）。
    ユーザーがチャット本文に生のUNCパスを直接書いた場合、そのままではLLMが
    このパスを手で再構築する必要が生じるため、on_message でLLMに渡す前に
    本文中のUNCパスを検出して path_memory へ登録し、本文中の当該箇所を
    `@N` に置き換える。これによりLLMは以後そのターンから `@N` を使えばよく、
    生のUNCパス文字列を手で書き起こす場面自体を無くす。

    検出対象はUNCパスの正常形のみ（`\\\\server\\share` 以上の深さを要求し、
    セグメントはASCII英数字・`_`・`.`・`-` に限定して地の文の巻き込みを防ぐ）。
    ローカル絶対パス（`C:\\...`）や、既にLLMの誤変換で崩れた二重バックスラッシュ
    形状の検出は今回のスコープ外（将来必要になれば別途拡張する）。

    Args:
        text: ユーザーのメッセージ本文（message.content）。

    Returns:
        検出したUNCパスを `@N` に置換したテキスト。path_memory機能が
        利用できない場合やUNCパスを含まない場合は text をそのまま返す。
    """
    if _PATH_MEMORY_DIR is None:
        return text
    thread_id = cl.user_session.get("thread_id") or "_no_session"

    def _replace(match: re.Match) -> str:
        path = match.group(0)
        index = path_memory.register(
            thread_id,
            path,
            _PATH_MEMORY_DIR,
            _PATH_MEMORY_MAX_ENTRIES,
            description="ユーザー入力",
        )
        return f"@{index}" if index is not None else path

    return _RAW_UNC_PATH_RE.sub(_replace, text)


def _register_path_memory(paths: list[str], description: str | None = None) -> dict[str, str]:
    """パスの一覧を path_memory レジストリへ登録し、{"@N": path, ...} を返す。

    旧 skills/file-tools/scripts/_common.py の register_paths()（run_script
    サブプロセス経由・環境変数依存）を、同一プロセス内の直接呼び出しに
    置き換えたもの（Read/Glob/Grep/json_query が使う）。

    Args:
        paths: 登録したい絶対パス文字列のリスト。
        description: 各パスに添える短い説明（省略可）。

    Returns:
        {"@N": path, ...} の辞書。path_memory が利用できない環境でも
        例外を投げず空辞書を返す。
    """
    if _PATH_MEMORY_DIR is None:
        return {}
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    result: dict[str, str] = {}
    for path in paths:
        index = path_memory.register(thread_id, path, _PATH_MEMORY_DIR, _PATH_MEMORY_MAX_ENTRIES, description=description)
        if index is not None:
            result[f"@{index}"] = path
    return result


def _dedupe_paths_with_path_memory(result: dict, path_memory_map: dict[str, str]) -> None:
    """Glob 結果に含まれるフルパスの重複を `@N` 参照へ置き換える（in-place）。

    `glob_search()` の戻り値は同じ絶対パスを `files`・`file_details[].path`・
    `directories[].path` に持ち、さらに `path_memory` にも同じパスが載るため、
    1件あたり同じ長い文字列が最大4回会話履歴へ積まれていた。297件のフォルダを
    Glob した実測で1回の ToolMessage が約10万文字に達し、数回の呼び出しだけで
    コンテキスト上限（128k）を使い切って処理が中断する事象が eval
    （レシピ画像297枚ケース）で観測された。

    `@N` はそのまま各ツールの絶対パス引数へ渡せる（`_resolve_path_memory_token`
    が解決する）ため、`path_memory` に対応表がある限り情報は失われない。

    Args:
        result: `glob_search()` の戻り値（この関数が直接書き換える）。
        path_memory_map: `{"@N": 絶対パス}`。空なら何もしない。
    """
    if not path_memory_map:
        return
    token_by_path = {path: token for token, path in path_memory_map.items()}
    result["files"] = [token_by_path.get(p, p) for p in result.get("files", [])]
    for detail in result.get("file_details", []):
        detail["path"] = token_by_path.get(detail["path"], detail["path"])
    for directory in result.get("directories", []):
        directory["path"] = token_by_path.get(directory["path"], directory["path"])


def _resolve_file_tools_path(raw: str) -> tuple[Path | None, str | None]:
    """Read/Glob/Grep/json_query の path 系引数を解決する。

    `@N` 形式のパスメモリー参照を解決したのち、絶対パスはそのまま、
    相対パス（空文字含む）は作業ディレクトリ基準（_resolve_workdir()）で
    解決する。旧 run_script はサブプロセスの cwd=workdir により暗黙に
    作業ディレクトリ基準になっていたため、ネイティブツール化後もこの挙動を
    明示的に再現する（Path.cwd() 等プロセス自身のcwdは使わない）。

    Args:
        raw: file_path/path 引数の生値（空文字・相対パス・絶対パス・`@N`）。

    Returns:
        (解決済み絶対パス, エラーメッセージ) のタプル。`@N` が未登録等で
        解決できない場合は (None, エラー文字列)。
    """
    resolved, error = _resolve_path_memory_token(raw) if raw else (raw, None)
    if error:
        return None, error
    p = Path(resolved) if resolved else _resolve_workdir()
    if not p.is_absolute():
        p = _resolve_workdir() / p
    tmp_error = _foreign_tmp_dir_error(p)
    if tmp_error:
        return None, tmp_error
    return p, None


def _foreign_tmp_dir_error(path: Path) -> str | None:
    """path が自セッション以外の `_tmp_<thread_id>` 配下なら拒否メッセージを返す。

    `_tmp_<thread_id>`（execute_python_code系の中間生成物置き場、
    `_resolve_exec_workdir()`）は作業ディレクトリ・default_workdir直下に
    全セッション共通の兄弟フォルダとして並ぶため、素朴にパスを許可すると
    他セッションの一時ファイルまで読めてしまう（LLMが他セッションの
    残留ファイルを自セッションの生成物と誤認する事故の原因）。

    誤検知を避けるため、「作業ディレクトリ/default_workdir 直下の
    最初の階層が `_tmp_` で始まる名前かどうか」だけを見る（パスの
    どこかに `_tmp_` を含む文字列があるかではない）。無関係な場所に
    たまたま `_tmp_` で始まる名前のフォルダがあっても影響しない。

    さらに、この階層が実際にディレクトリである場合のみ拒否する
    （`_foreign_tmp_dir_names()` と同じ条件）。`_resolve_exec_workdir()`
    が作るセッション作業フォルダは常にディレクトリであり、ファイルには
    なりえないため、LLMが作業ディレクトリ直下に `_tmp_ops.json` のような
    `_tmp_` で始まる名前の**ファイル**を自分で作成した場合まで誤って
    「他セッションの一時ディレクトリ」として読み取り拒否してしまう回帰が
    あった（2026-08-22 発見・修正）。

    Args:
        path: 解決済みの絶対パス（未解決でも内部で resolve() する）。

    Returns:
        他セッションの一時ディレクトリ配下と判定できればエラー文字列、
        そうでなければ None。
    """
    own_name = f"_tmp_{cl.user_session.get('thread_id') or '_no_session'}"
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in _tmp_dir_parents(_resolve_workdir()):
        try:
            rel = resolved.relative_to(parent)
        except ValueError:
            continue
        if not rel.parts:
            continue
        first = rel.parts[0]
        if first.startswith("_tmp_") and first != own_name:
            try:
                is_dir = (parent / first).is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                return f"エラー: 他セッションの一時ディレクトリ（{first}）は読み取れません。"
        return None
    return None


def _foreign_tmp_dir_names() -> frozenset[str]:
    """自セッション以外の `_tmp_<thread_id>` ディレクトリ名の集合を返す。

    Glob/Grep が作業ディレクトリ本体などの祖先から再帰検索する際、
    他セッションの `_tmp_<X>` サブツリーだけを走査・結果から除外するために
    `file_tools.glob_search`/`grep_search` の `exclude_names` へ渡す。
    """
    own_name = f"_tmp_{cl.user_session.get('thread_id') or '_no_session'}"
    names: set[str] = set()
    for parent in _tmp_dir_parents(_resolve_workdir()):
        try:
            entries = list(parent.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("_tmp_") and entry.name != own_name and entry.is_dir():
                names.add(entry.name)
    return frozenset(names)


def _check_file_tools_duplicate(tool_label: str, signature: str) -> str | None:
    """Read/Glob/Grep/json_query/read_skill/read_skill_file/get_tool_source 共通の重複呼び出しガード。

    旧 _run_script_impl の file-tools 分岐（読み取り専用スキルのため、
    同一引数での再実行は結果が変わらないと保証できる）を汎用化したもの。
    config.ini [file_tools_duplicate_guard] の enabled/max_calls/
    carry_over_to_main をそのまま参照する。

    read_skill/read_skill_file はコンテキスト圧縮でメッセージ履歴から
    過去の実行結果が消えた後もLLMが同じ大きいSKILL.md等を何度も読み直す
    thrashingを防ぐ目的でも使う（2026-08-10 issue参照）。この記録は
    cl.user_session（LLMへ送るメッセージ履歴とは別のサーバー側状態）に
    残るため、圧縮でメッセージ履歴が消えてもガードの記憶自体は消えない。

    Args:
        tool_label: エラー文言に出すツール名（例: "Read"）。
        signature: 呼び出し引数から組み立てた、その呼び出しを一意に表す文字列。

    Returns:
        重複と判定されればエラー文字列、そうでなければ None
        （None の場合、呼び出し元は通常通り処理を続けてよい）。
    """
    cfg = _LLM_CONFIG
    guard_enabled = cfg.file_tools_duplicate_guard_enabled if cfg else True
    if not guard_enabled:
        return None
    guard_max_calls = cfg.file_tools_duplicate_guard_max_calls if cfg else 1
    session_key = _duplicate_guard_session_key("file_tools_call_signatures")
    if _record_and_check_duplicate(session_key, signature, guard_max_calls):
        return (
            f"エラー: {tool_label} を同じ引数で既に上限（{guard_max_calls}回）まで"
            f"呼び出し済みです。{tool_label} は読み取り専用のため再実行しても"
            "結果は変わりません。会話履歴にある前回の実行結果を参照するか、"
            "別の引数・別の手段に切り替えてください。"
        )
    return None


def _subprocess_env() -> dict[str, str]:
    """run_script/execute_python_code の子プロセスへ渡す環境変数を組み立てる。

    既存の PYTHONIOENCODING（日本語文字化け対策）に加え、パスメモリー
    （src/path_memory.py）用の AGENT_THREAD_ID/AGENT_PATH_MEMORY_DIR/
    AGENT_PATH_MEMORY_MAX_ENTRIES を注入する。run_script 経由で実行される
    スキルのスクリプトは、これらを `path_memory.env_params()` で読み、
    自分が出力するパスをレジストリへ登録できる。
    AGENT_SRC_DIR は execute_python_code のサブプロセスが
    `src/path_memory.py` をインポートするために使う。
    AGENT_DEFAULT_WORKDIR は `_resolve_exec_workdir()`/`path_memory.exec_tmp_dir()`
    が中間生成物置き場 `_tmp_<thread_id>/` の基準ディレクトリを決めるために使う
    （run_script の cwd が指すユーザー指定 work_dir は保持日数ベースの自動削除
    対象外のため、cwd 基準にすると中間生成物が消えずに溜まり続ける。常に
    default_workdir 基準に固定することで自動削除の対象に含める。
    `_restrict_default_workdir` 参照）。

    config.ini `[paths].bin_path`（既定は空）に列挙されたディレクトリを PATH の
    先頭へ追加する。コマンド名を素の状態で叩く外部バイナリのスキルは、事前に
    ユーザーがOS側のPATH環境変数へ手動登録していないと「コマンドが見つからない」
    で失敗する。config.ini に配置先を明示しておけば、evals・app.py実行時の
    どちらでも追加の手動設定なしで呼び出せる。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env["AGENT_THREAD_ID"] = cl.user_session.get("thread_id") or "_no_session"
    if _PATH_MEMORY_DIR is not None:
        env["AGENT_PATH_MEMORY_DIR"] = str(_PATH_MEMORY_DIR)
    env["AGENT_PATH_MEMORY_MAX_ENTRIES"] = str(_PATH_MEMORY_MAX_ENTRIES)
    env["AGENT_SRC_DIR"] = str(_SRC_DIR)
    if _DEFAULT_WORKDIR is not None:
        env["AGENT_DEFAULT_WORKDIR"] = str(_DEFAULT_WORKDIR)
    cfg = _LLM_CONFIG
    if cfg is not None:
        bin_dirs = [d for d in cfg.bin_path if d.is_dir()]
        if bin_dirs:
            env["PATH"] = os.pathsep.join([*(str(d) for d in bin_dirs), env.get("PATH", "")])
    return env


def _run_script_guard_env(workdir: Path) -> tuple[dict[str, str], Path | None]:
    """run_script/run_script_background が起動するサブプロセスへ、書き込み
    サンドボックスガードを注入した環境変数を組み立てる。

    run_script はスキル作者が書いた既存の scripts/ 配下のファイルをそのまま
    実行するため、execute_python_code のようにコード文字列の先頭へガードの
    ソースを連結する方法が使えない（対象ファイルを書き換えるのは事故の元）。
    代わりに Python がサブプロセス起動時に sys.path 上の sitecustomize
    モジュールを自動 import する仕組みを利用し、_python_fs_guard_preamble が
    生成するのと同じモンキーパッチコードを一時ディレクトリへ sitecustomize.py
    として書き出し、PYTHONPATH の先頭に追加することで対象スクリプトのソースを
    一切変更せずに書き込み・削除系呼び出しへガードを差し込む。

    許可されるのは workdir（run_script の cwd = 作業ディレクトリ。呼び出し元の
    _prepare_script_execution が _restrict_default_workdir() 済みのため、
    work_dir未設定等でdefault_workdirへフォールバックした場合はここで
    既に `_tmp_<thread_id>` になっている）・`_tmp_<thread_id>`（念のため
    ここでも明示的に加える。default_workdirはサーバー側の共有ディレクトリの
    ため、直下やその他のサブディレクトリへの書き込みは許可せず、常に
    自セッション専用の`_tmp_<thread_id>`のみに限定する。他セッションが
    生成物を誤参照する事故の防止）・path_memory_dir（register_output_path() が
    ロックファイルを書き込むLocohane内部の状態ディレクトリ。ユーザー成果物の
    保存先ではないためexecute_python_code側のallowed_rootsとは異なりここにのみ追加）
    配下のみ。それ以外の場所（他ドライブ・Locohaneプロジェクト本体・
    default_workdir直下の他ディレクトリを含む）への書き込み・削除は
    PermissionError でブロックされる。読み取りは対象外だが、
    `_tmp_<thread_id>`（`workdir`直下に全セッション共通で並ぶ一時フォルダ）に
    限っては、自セッション以外への読み取り・書き込み・削除を allowed_roots の
    内外を問わず追加でブロックする（`_python_fs_guard_preamble` の
    `tmp_dir_roots` 参照）。

    Args:
        workdir: このスクリプト実行の cwd（_resolve_workdir の解決結果）。

    Returns:
        (env, guard_dir) のタプル。guard_dir は呼び出し側がサブプロセス
        終了後に削除する一時ディレクトリ。一時ファイル作成に失敗した場合は
        guard_dir が None になり、env にはガード無しの _subprocess_env() の
        結果のみが入る（ガード注入の失敗自体でスクリプト実行を止めない。
        書き込み制限が効かなくなるだけで、通常の権限エラー等は従来通り
        OS側で発生する）。
    """
    env = _subprocess_env()
    allowed_roots = [workdir]
    if _DEFAULT_WORKDIR is not None:
        allowed_roots.append(_resolve_exec_workdir())
    if _PATH_MEMORY_DIR is not None:
        allowed_roots.append(_PATH_MEMORY_DIR)
    try:
        guard_dir = Path(tempfile.mkdtemp(prefix="agent_fs_guard_"))
        guard_src = _python_fs_guard_preamble(allowed_roots, tmp_dir_roots=_tmp_dir_parents(workdir))
        (guard_dir / "sitecustomize.py").write_text(guard_src, encoding="utf-8")
    except OSError:
        logger.warning("run_script: 書き込みガード用の一時ファイル作成に失敗したため、ガード無しで実行します。")
        return env, None
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(guard_dir) + (os.pathsep + existing_path if existing_path else "")
    return env, guard_dir


def _safe_path(relative: str) -> Path:
    """skills ルート配下に限定した絶対パスを返す。境界外なら ValueError。

    ディレクトリトラバーサル対策の中核。relative に ".." やシンボリック
    リンク経由の脱出が含まれていても、resolve() で正規化した上で
    is_relative_to() により境界を検証するため、skills ルート外への
    アクセスは常に拒否される。

    _SKILLS_ROOTS は複数ディレクトリを保持しうる（例: [*locohane_skills_dirs,
    skills_dir]）。前方から順に候補を解決し、実在する最初の候補を返す。
    どのルートにも実在しない場合は先頭ルート基準の候補を返す（呼び出し側の
    「見つかりません」エラーへ自然に流すため）。

    Args:
        relative: skills ルートからの相対パス（例: "word-counter/SKILL.md"）。

    Returns:
        skills ルート配下に解決された絶対パス（Path）。

    Raises:
        RuntimeError: init_tools() が未実行で _SKILLS_ROOTS が空の場合。
        ValueError: 解決後のパスがいずれかの skills ルート配下に収まらない
            場合（ディレクトリトラバーサルの試行とみなす）。
    """
    if not _SKILLS_ROOTS:
        raise RuntimeError("init_tools() が未実行です")
    candidates: list[Path] = []
    for root in _SKILLS_ROOTS:
        # resolve() でシンボリックリンクや .. を正規化した上で境界を検証する。
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"skills ディレクトリ外へのアクセスは許可されません: {relative}")
        candidates.append(candidate)
        if candidate.exists():
            return candidate
    return candidates[0]


def _missing_skill_prefix_hint(relative_path: str) -> str:
    """read_skill_file が見つからなかった際、スキル名プレフィックス漏れを疑うヒントを返す。

    read_skill(skill_name) を呼んだ直後のサブエージェントが、以後のパスを
    「そのスキルフォルダの中にいる」前提で書いてしまい、relative_path の
    先頭にスキルフォルダ名を付け忘れる（例: "word-counter/references/notes.md"
    のつもりで "references/notes.md" とだけ渡す）誤りが疑われるため追加した
    ヒント。relative_path の先頭セグメントがどの skills ルートにも
    ディレクトリとして存在しない場合にのみヒントを返す。

    Args:
        relative_path: read_skill_file に渡された相対パス。

    Returns:
        スキル名プレフィックス漏れが疑われる場合のヒント文字列。
        該当しない場合は空文字列。
    """
    first_segment = relative_path.replace("\\", "/").split("/", 1)[0]
    if not first_segment:
        return ""
    for root in _SKILLS_ROOTS or []:
        if (root / first_segment).is_dir():
            return ""
    return (
        f"（先頭 '{first_segment}' がスキルフォルダ名になっていない可能性があります。"
        "read_skill_file の relative_path は常にスキルルートからの相対パスで、"
        "スキルフォルダ名自体を先頭に含める必要があります。"
        "例: word-counter/references/notes.md）"
    )


def _resolve_script_filename(skill_name: str, script_filename: str) -> Path:
    """スキルの scripts/ 配下からファイル名でスクリプトを探し、絶対パスを返す。

    get_tool_source / run_script が共有する解決ロジック。
    呼び出し側にディレクトリ構成（scripts/ 配下という規約）を書かせず、
    ファイル名のみで指定できるようにする。低パラメータモデルが「scripts/」
    という文字列と引数名を混同して壊れた値（例: "scripts=read_file.py"）を
    生成する誤動作を避けるための設計（ドキュメント側の引数名も script_filename
    にリネーム済み）。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 探したいファイル名（例: "read_file.py"）。ディレクトリ
            区切りを含む値（旧形式の "scripts/read_file.py" 等）が渡されても
            os.path.basename() でファイル名部分のみを取り出すため動作する。

    Returns:
        解決済みの絶対パス（Path）。

    Raises:
        ValueError: skill_name に scripts/ ディレクトリが無い場合、
            該当ファイルが見つからない場合。
    """
    basename = os.path.basename(script_filename)
    if not basename:
        raise ValueError(f"スクリプトのファイル名を指定してください: {script_filename!r}")
    scripts_root = _safe_path(f"{skill_name}/scripts")
    if not scripts_root.is_dir():
        raise ValueError(f"スキル '{skill_name}' に scripts/ ディレクトリがありません")
    matches = [p for p in scripts_root.rglob(basename) if p.is_file()]
    if not matches:
        raise ValueError(f"スクリプトが見つかりません: {basename}（skill={skill_name}）")
    matches.sort(key=lambda p: (len(p.relative_to(scripts_root).parts), str(p)))
    return matches[0]


@tool
def read_skill(skill_name: str) -> str:
    """スキルの SKILL.md 本文全体を読み込んで返す。

    ユーザーの要求に合致するスキルを選んだら、まずこのツールで本文（手順）を読むこと。
    Agent Skills 標準の progressive disclosure における第2段階（Read）に相当する。

    Args:
        skill_name: 読み込むスキルのフォルダ名（= SKILL.md の name）。

    Returns:
        SKILL.md の本文全体（UTF-8 テキスト）。skill_name が skills ルート外を
        指す場合や SKILL.md が存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す（LLM がそのまま読める形にするため）。
    """
    try:
        skill_md = _safe_path(f"{skill_name}/SKILL.md")
    except ValueError as e:
        return f"エラー: {e}"
    if not skill_md.is_file():
        return f"エラー: スキル '{skill_name}' の SKILL.md が見つかりません。"
    dup_error = _check_file_tools_duplicate("read_skill", f"read_skill\x00{skill_name}")
    if dup_error:
        return dup_error
    logger.info("read_skill: %s", skill_name)
    return skill_md.read_text(encoding="utf-8")


@tool
def read_skill_file(relative_path: str) -> str:
    """skills ディレクトリ配下のファイルを読み込んで返す。

    SKILL.md 本文が references/assets を参照している場合など、必要時のみ使う。
    Agent Skills 標準の progressive disclosure における第3段階（Execute）の一部。

    Args:
        relative_path: skills ルートからの相対パス。read_skill(skill_name) で
            そのスキルを読んだ後でも、先頭に必ずスキルフォルダ名を含めること
            （例: "references/notes.md" ではなく
            "word-counter/references/notes.md"）。

    Returns:
        ファイル内容（UTF-8、デコード不能なバイト列は errors="replace" で置換）。
        skills ルート外を指す場合やファイルが存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    try:
        path = _safe_path(relative_path)
    except ValueError as e:
        return f"エラー: {e}"
    if not path.is_file():
        hint = _missing_skill_prefix_hint(relative_path)
        if hint:
            return f"エラー: ファイルが見つかりません: {relative_path}{hint}"
        return (
            f"エラー: ファイルが見つかりません: {relative_path}"
            "（read_skill_file は skills ディレクトリ配下限定です。作業ディレクトリ配下の"
            "ファイルを読みたい場合は Read ツールを使ってください）"
        )
    dup_error = _check_file_tools_duplicate("read_skill_file", f"read_skill_file\x00{path}")
    if dup_error:
        return dup_error
    logger.info("read_skill_file: %s", relative_path)
    return path.read_text(encoding="utf-8", errors="replace")


@tool
def provide_download(file_paths: list[str]) -> str:
    """既存のファイルをチャット画面にダウンロードボタンとして提示する。

    アップロード済みファイルや、Read/Glob 等で見つけた既存ファイル、以前の
    作業で生成済みのファイルなどを、あらためてユーザーがダウンロードできる
    ようにしたいときに使う。Read 等と同様にパスの制限は行わない
    （ローカルファイルシステム上の任意の絶対パスを指定できる。ただし
    `_tmp_<thread_id>` の他セッション分だけは例外で提供できない）。複数指定
    した場合、1つのメッセージにダウンロードボタンがまとめて並んで表示
    される（1件だけの場合はリストに1件だけ入れて渡す）。

    Args:
        file_paths: ダウンロードさせたいファイルの絶対パスのリスト
            （相対パスの場合はセッションの作業ディレクトリ基準で解決する）。

    Returns:
        成功時: {"output_paths": ["...", ...]} 形式のJSON文字列
            （自動的にチャットへダウンロードボタンがまとめて表示される）。
        1件でも見つからないファイルがあれば「エラー: ...」形式の文字列を
            返す（部分的な成功はしない）。
    """
    resolved: list[Path] = []
    for file_path in file_paths:
        path = Path(file_path)
        if not path.is_absolute():
            path = _resolve_workdir() / path
        path = path.resolve()
        tmp_error = _foreign_tmp_dir_error(path)
        if tmp_error:
            return tmp_error
        if not path.is_file():
            return f"エラー: ファイルが見つかりません: {file_path}"
        resolved.append(path)
    logger.info("provide_download: %s", resolved)
    return json.dumps({"output_paths": [str(p) for p in resolved]}, ensure_ascii=False)


@tool
def show_image(file_path: str) -> str:
    """既存の画像ファイルをチャット画面にプレビュー表示する（LLM自身は内容を見ない）。

    ユーザーが「表示して」「見せて」「プレビューして」のように画像そのものを
    見たいだけの依頼をしてきた場合は、まずこのツールを使う。画像の内容について
    自分（LLM）が説明・分析・判断してから答える必要がある場合にのみ、代わりに
    `analyze_image` を使うこと（`analyze_image` は画像をVision対応モデルへ実際に
    見せてLLM自身に解析させるツール。`show_image` は画像データをLLMへは渡さず、
    チャットUI上に表示するだけ）。迷ったら、ユーザーが求めているのが「画像そのものを
    見ること」か「画像についての説明」かで判断する。

    生成済みの画像（グラフ・スクリーンショット等）や、アップロード済み・
    Glob で見つけた既存の画像をユーザーへ見せたいときに使う。
    provide_download と同様にパスの制限は行わない（ローカルファイルシステム上の
    任意の絶対パスを指定できる。ただし `_tmp_<thread_id>` の他セッション分だけは
    例外で表示できない）。

    Args:
        file_path: 表示したい画像ファイルの絶対パス（相対パスの場合は
            セッションの作業ディレクトリ基準で解決する）。

    Returns:
        成功時: {"output_path": "..."} 形式のJSON文字列
            （自動的にチャットへ画像がプレビュー表示される）。
        失敗時: 「エラー: ...」形式の文字列。
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = _resolve_workdir() / path
    path = path.resolve()
    tmp_error = _foreign_tmp_dir_error(path)
    if tmp_error:
        return tmp_error
    if not path.is_file():
        return f"エラー: ファイルが見つかりません: {file_path}"
    if not is_image_file(path):
        return f"エラー: 画像ファイルではありません（対応形式: png/jpg/jpeg/gif/webp/bmp）: {file_path}"
    logger.info("show_image: %s", path)
    return json.dumps({"output_path": str(path)}, ensure_ascii=False)


@tool
def get_tool_source(skill_name: str, script_filename: str) -> str:
    """run_script で実行したスクリプトの絶対パスを返す（中身は返さない）。

    run_script がエラー（非0終了コード・スタックトレース）を返した場合の原因調査に使う。
    このツールでソースファイルの絶対パスを取得し、必要なら read_skill_file で中身を
    確認するか、execute_python_code 内で `sys.path.insert(0, "<このパスの親ディレクトリ>")`
    のようにして _common.py 等の同スキル内ヘルパーモジュールを直接 import して調査・
    代替コードの実行に使う。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 探したいファイル名（例: "read_file.py"）。パスや scripts/
            プレフィックスは不要 — スキルフォルダの scripts/ 配下から自動検索される。

    Returns:
        スクリプトの絶対パス文字列。skill_name に scripts/ ディレクトリが無い場合、
        スクリプトが見つからない場合は、例外を送出せず「エラー: ...」形式の
        文字列を返す。
    """
    try:
        script_path = _resolve_script_filename(skill_name, script_filename)
    except ValueError as e:
        return f"エラー: {e}"
    dup_error = _check_file_tools_duplicate("get_tool_source", f"get_tool_source\x00{skill_name}\x00{script_filename}")
    if dup_error:
        return dup_error
    logger.info("get_tool_source: %s/%s", skill_name, script_filename)
    return str(script_path)


@dataclass
class WorkDirAccessStatus:
    """作業ディレクトリへの実際のアクセス可否（probe_workdir_access の戻り値）。

    サーバー駆動でローカルネットワーク上の別PCから利用される場合、ユーザーの
    PCからは見える/書き込めるパスでも、サーバープロセス側からは見えない、
    または見えても書き込み権限が無い（読み取り専用共有など）ことがある。
    os.access() はWindowsのネットワーク共有・ACL構成では実態と食い違う
    ことがあるため、実際のI/Oで判定する（probe_workdir_access 参照）。
    """

    path: str
    exists: bool
    readable: bool
    writable: bool
    error: str | None = None


def probe_workdir_access(path: Path) -> WorkDirAccessStatus:
    """作業ディレクトリの読み取り/書き込み可否を実際のI/Oで検証する。

    Args:
        path: 検証対象のディレクトリパス。

    Returns:
        存在確認・読み取り確認（os.listdir）・書き込み確認（一時ファイルの
        作成/削除）の結果をまとめた WorkDirAccessStatus。
    """
    if not path.is_dir():
        return WorkDirAccessStatus(str(path), exists=False, readable=False, writable=False)
    try:
        os.listdir(path)
    except OSError as e:
        return WorkDirAccessStatus(str(path), exists=True, readable=False, writable=False, error=str(e))
    probe = path / f".agent_write_test_{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return WorkDirAccessStatus(str(path), exists=True, readable=True, writable=False, error=str(e))
    return WorkDirAccessStatus(str(path), exists=True, readable=True, writable=True)


def _resolve_workdir(need_write: bool = False) -> Path:
    """run_script が subprocess.run に渡す cwd を決定する。

    Chainlit の ChatSettings（独自フロントエンドには表示されないが
    on_settings_update の経路自体は残っている）でユーザーがセッションに
    作業ディレクトリを設定していればそれを使い（app.py の
    on_settings_update が cl.user_session["work_dir"] に絶対パス文字列を
    保存する）、未設定なら config.ini の [default_workdir].dir
    （init_tools() で注入された _DEFAULT_WORKDIR）にフォールバックする。

    サーバー/クライアントでファイルシステムが分離している環境（別PCから
    利用する場合）では、ユーザー指定の work_dir がサーバー側から見て
    アクセス不可・書き込み不可なことがある。app.py の _apply_work_dir が
    設定時に cl.user_session["work_dir_access"]（WorkDirAccessStatus）へ
    実測結果をキャッシュしており、ここではそれを参照して機械的に
    default_workdir へフォールバックする（LLMが確認を怠っても安全側に
    倒れる）。読み取り専用共有から既存ファイルを読ませたいだけのケースを
    妨げないよう、need_write=False（既定）では読み取り可否のみを見る。

    read_skill / read_skill_file / スクリプト本体の場所解決には影響しない
    （それらは常に _safe_path 経由で skills ルート配下に固定される）。

    Args:
        need_write: True の場合、書き込み可否（status.writable）も見て
            フォールバック判定する。既存ファイルの読み取りのみが目的の
            呼び出し元は False のままでよい。

    Returns:
        呼び出し元が使う絶対パス。work_dir が未設定、またはアクセス不可・
        （need_write時は）書き込み不可と判定されていれば default_workdir。

    Raises:
        RuntimeError: init_tools() が未実行で _DEFAULT_WORKDIR が None の場合。
    """
    if _DEFAULT_WORKDIR is None:
        raise RuntimeError("init_tools() が未実行です")
    work_dir = cl.user_session.get("work_dir")
    if not work_dir:
        return _DEFAULT_WORKDIR
    status: WorkDirAccessStatus | None = cl.user_session.get("work_dir_access")
    if status is not None:
        if not status.exists or not status.readable:
            return _DEFAULT_WORKDIR
        if need_write and not status.writable:
            return _DEFAULT_WORKDIR
    return Path(work_dir)


@tool
def check_work_dir_status() -> str:
    """現在の作業ディレクトリの実際のアクセス状況を確認する。

    work_dir を作業フォルダアイコンで変更した
    直後は自動的にこの確認が行われ、結果がサイドパネルに表示されるため、通常は
    明示的に呼び出す必要はない。ただし run_script や execute_python_code が
    ファイルの読み書きで原因不明のエラー（ファイルが見つからない、書き込め
    ない等）を返した場合、それが「作業ディレクトリ自体へのアクセス問題
    （サーバー側から見えない・読み取り専用共有である等）」なのか「スクリプト
    側の問題」なのかを切り分けたいときに使う。

    os.access() のような簡易判定ではなく、実際にディレクトリ一覧の取得と、
    一時ファイルの作成・書き込み・削除を試みることで正確に判定する
    （ローカルネットワーク越しの共有フォルダ等では簡易判定が実態と食い違う
    ことがあるため）。

    Returns:
        作業ディレクトリのパス、状態（読み書き可能 / 読み取り専用（書き込み
        不可）/ アクセス不可（読み取りも不可）/ 存在しない）、アクセス不可の
        場合にどこへ自動フォールバックされるかをまとめた説明文字列。
        init_tools() が未実行の場合は例外を送出せず「エラー: ...」形式の
        文字列を返す。
    """
    if _DEFAULT_WORKDIR is None:
        return "エラー: init_tools() が未実行です"
    work_dir = cl.user_session.get("work_dir")
    if not work_dir:
        return (
            f"作業ディレクトリ: 未設定（既定フォルダ {_DEFAULT_WORKDIR} を使用）\n"
            "状態: 読み書き可能（既定フォルダはサーバー側の設定のため通常アクセス可能）"
        )
    status = probe_workdir_access(Path(work_dir))
    cl.user_session.set("work_dir_access", status)
    if not status.exists:
        label = "存在しません（このPCから直接アクセスできません）"
    elif not status.readable:
        label = "アクセスできません（読み取り不可）"
    elif not status.writable:
        label = "読み取り専用（書き込み不可）"
    else:
        label = "読み書き可能"
    lines = [f"作業ディレクトリ: {work_dir}", f"状態: {label}"]
    if status.error:
        lines.append(f"詳細: {status.error}")
    if not status.exists or not status.readable:
        lines.append(f"影響: 読み取り・書き込みとも既定フォルダ（{_DEFAULT_WORKDIR}）を自動的に使用します。")
    elif not status.writable:
        lines.append(
            f"影響: 書き込みが必要な処理（execute_python_code, run_script の出力生成）は"
            f"既定フォルダ（{_DEFAULT_WORKDIR}）を自動的に使用します。既存ファイルの読み取りは"
            "引き続きこの作業ディレクトリを使用します。"
        )
    return "\n".join(lines)


def _resolve_exec_workdir() -> Path:
    """execute_python_code / run_script が中間生成物を書く実行用ディレクトリ。

    常に default_workdir 配下に `_tmp_<thread_id>` を作って返す（無ければ
    作成する）。LLMがコード内で相対パスで書き出すファイル（ops.json 等の
    中間生成物）が作業ディレクトリ直下に散らからないようにするため。
    `_tmp/<thread_id>` のような親子階層ではなく `_tmp_<thread_id>` という
    単一のディレクトリ名にしているのは、日数ベースの自動削除（cleanup_old_dirs、
    app.py）が丸ごと rmtree した際、親ディレクトリ（`_tmp`）が空のまま
    残り続ける問題を避けるため。

    以前はユーザー指定の work_dir 直下に作っていたが、生成中に別スレッドへ
    切り替えるとソケット切断（on_chat_end）で即座に rmtree され、裏で
    継続中の処理と競合する問題があった。default_workdir 固定にすることで
    on_chat_end での即時削除自体を廃止し、default_workdir_retention_days
    による日数ベースの自動削除（app.py）に一本化した（work_dir は元々
    サーバー/クライアントでファイルシステムが分離しうるため書き込み不可の
    こともあり得るが、default_workdir はサーバー側の設定のため常に
    書き込み可能という前提が置ける）。

    provide_download / show_image / _resolve_analyze_image_path は
    ユーザーへの成果物提供に使う関数のため、意図的にこの関数を使わず
    _resolve_workdir() のまま据え置く（最終成果物はユーザー指定の work_dir
    直下に置かれる想定のため。execute_python_code の書き込みガード
    （_exec_guard_roots）は _tmp_<thread_id> の実体とは独立に、実際の
    work_dir も allowed_roots へ含めている）。

    Returns:
        `_tmp_<thread_id>` ディレクトリの絶対パス。
    """
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    d = _DEFAULT_WORKDIR / f"_tmp_{thread_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _restrict_default_workdir(path: Path) -> Path:
    """書き込み先/cwd候補が default_workdir そのものなら `_tmp_<thread_id>` へ縮小する。

    default_workdir はサーバー側の共有ディレクトリで全セッションから同じ
    パスが見えるため、直下やその他のサブディレクトリへ書き込みを許すと、
    別セッションがそのファイルを参照して誤動作する事故につながる
    （work_dir 未設定時は run_script/execute_python_code の書き込み先が
    素朴には default_workdir 直下になっていたため発生しえた）。
    自セッション専用の `_tmp_<thread_id>`（_resolve_exec_workdir() と同じ
    ディレクトリ）だけに縮小することで、成果物は常にセッション専用領域へ
    閉じ込め、ユーザーへの提示は provide_download/show_image 経由に統一する。

    work_dir がユーザー指定で default_workdir と無関係な場所を指している
    場合はそのまま通す（制限対象は default_workdir そのものが渡された
    ケースのみ）。

    Args:
        path: 書き込み許可ルート、または cwd の候補（_resolve_workdir() の
            戻り値など）。

    Returns:
        path が default_workdir と同一なら `_tmp_<thread_id>`、それ以外は
        path をそのまま返す。
    """
    if _DEFAULT_WORKDIR is not None and path.resolve() == _DEFAULT_WORKDIR:
        return _resolve_exec_workdir()
    return path


def _tmp_dir_parents(primary: Path) -> list[Path]:
    """`_tmp_<thread_id>` が実際に作られうる親ディレクトリの一覧を返す。

    `_resolve_exec_workdir()` は通常 `primary`（呼び出し元が渡す実行用
    ディレクトリの親、または run_script の cwd そのもの）配下に
    `_tmp_<thread_id>` を作るが、mkdir失敗時は `_DEFAULT_WORKDIR` 配下へ
    フォールバックする（1402-1408行目）。他セッションの `_tmp_<X>` 検出
    （`_foreign_tmp_dir_error`/`_foreign_tmp_dir_names`/ガードプリアンブルの
    `tmp_dir_roots`）は、このフォールバック先も含めて両方を見る必要がある。

    Args:
        primary: 実行用ディレクトリの親（execute_python_code系）、または
            run_script の cwd（run_script系。この場合それ自体が親）。

    Returns:
        重複・非存在を除いた既存ディレクトリのリスト。
    """
    candidates = [primary]
    if _DEFAULT_WORKDIR is not None:
        candidates.append(_DEFAULT_WORKDIR)
    seen: set[Path] = set()
    result: list[Path] = []
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _scratch_notes_path_for_run(run_id: str) -> Path:
    """write_scratch_note が書き込むスクラッチファイルの絶対パスを、
    指定した run_id から直接決める（_SUBAGENT_RUN_ID を読まない版）。

    dispatch_agent のジョブを起動したツール呼び出しとは別の
    非同期コンテキスト（check_dispatch_agent_job / stop_dispatch_agent_job）
    から、同じジョブのスクラッチノートを参照するために使う。呼び出し元は
    事前にジョブの thread_id が現在のセッションと一致することを確認して
    いる前提（_resolve_exec_workdir() は cl.user_session から thread_id を
    読むため、一致していなければ別セッションのディレクトリを指してしまう）。
    """
    workdir = _resolve_exec_workdir()
    return workdir / f"_scratch_notes_{run_id}.md"


def _scratch_notes_path() -> Path:
    """write_scratch_note が書き込むスクラッチファイルの絶対パスを決める。

    _resolve_exec_workdir() と同じ `_tmp_<thread_id>` 配下に、現在の
    dispatch_agent 実行（_SUBAGENT_RUN_ID）ごとの専用ファイルを1つ割り当てる。
    サブエージェント外（run_id が無い状態）で呼ばれた場合は "_main" を使う。
    ファイル名はこの関数が決め打ちするため、呼び出し側が任意パスを
    指定することはできない。
    """
    return _scratch_notes_path_for_run(_SUBAGENT_RUN_ID.get() or "_main")


@tool
def write_scratch_note(content: str) -> str:
    """調査中に分かった内容を、その場でスクラッチファイルへ追記する。

    大量のファイルを読み進めながら1つの成果物（要約・抽出データ等）に
    まとめていくような調査タスクで使う。ある程度読み進めるたびにこの
    ツールで分かったことを書き残しておくと、万一このサブエージェント自身が
    トークン上限に達して打ち切られても、ここまでの内容は消えずに残る
    （打ち切り時、委譲元にはこのファイルのパスが案内され、そこから
    続きを判断できる）。

    execute_python_code/run_script と異なり、計画（create_plan/approve_plan）が
    未承認でも常に呼べる（調査は通常 create_plan より前に行うため）。書き込み先の
    ファイル名はこのツール自身が決めるため任意パスへは書き込めず、ユーザーの
    作業ディレクトリや出力先には一切触れない（execute_python_code の中間生成物と
    同じスクラッチ領域を使う）。

    Args:
        content: 追記する内容（Markdown・JSON文字列など自由形式）。
            これまでの内容は消さず、末尾に追記される。

    Returns:
        書き込み先の絶対パスと、追記後の累計文字数。content が空、または
        書き込みに失敗した場合は例外を送出せず「エラー: ...」形式で返す。
    """
    if not content.strip():
        return "エラー: content が空です。"
    path = _scratch_notes_path()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        total_chars = len(path.read_text(encoding="utf-8"))
    except OSError as e:
        return f"エラー: スクラッチファイルへの書き込みに失敗しました: {e}"
    return f"書き込みました: {path}（累計 {total_chars} 文字）"


def _thread_notes_path() -> Path:
    """write_thread_note/list_thread_notes/read_thread_note が使う、
    スレッド全体で共有されるノートファイルの絶対パスを決める。

    _scratch_notes_path() と異なり run_id ではなく thread_id のみで決まるため、
    同一スレッド内の全 dispatch_agent 実行（サブエージェントごとに変わる
    run_id）とメインエージェント自身が同じファイルを共有できる。
    """
    workdir = _resolve_exec_workdir()
    return workdir / "_thread_notes.md"


_THREAD_NOTE_BLOCK_RE = re.compile(r"^## (.+)\n<!-- (.+?) by (.+?) -->\n", re.MULTILINE)


def _thread_note_author() -> str:
    """現在の呼び出し元を表す短い文字列（メインエージェントなら"main"、
    サブエージェントなら agent_type）。write_thread_note が書き込むメタ情報、
    list_thread_notes の一覧表示に使う。
    """
    if _IN_SUBAGENT.get():
        return _SUBAGENT_AGENT_TYPE.get() or "subagent"
    return "main"


@dataclass
class _ThreadNoteBlock:
    topic: str
    timestamp: str
    author: str
    content: str


def _parse_thread_notes(text: str) -> list["_ThreadNoteBlock"]:
    """_thread_notes_path() の内容を `## topic` 単位のブロックへ分割する。

    write_thread_note は常に追記のみで既存ブロックを書き換えないため、
    同じ topic 名のブロックが複数存在しうる（list_thread_notes /
    read_thread_note 側で合算・連結して1つの topic として扱う）。
    """
    blocks: list[_ThreadNoteBlock] = []
    matches = list(_THREAD_NOTE_BLOCK_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(
            _ThreadNoteBlock(
                topic=m.group(1).strip(),
                timestamp=m.group(2).strip(),
                author=m.group(3).strip(),
                content=text[start:end].rstrip("\n"),
            )
        )
    return blocks


@tool
def write_thread_note(topic: str, content: str) -> str:
    """調査で分かった詳細を、スレッド全体で共有されるノートへ書き込む。

    write_scratch_note と違い、このファイルは同一スレッド内であれば
    メインエージェント・どのサブエージェント（agent_type）からも
    list_thread_notes / read_thread_note で読める（run_id には縛られない）。
    委譲元への最終回答を要約に留めたい場合、根拠となる具体的な事実
    （値・件数・該当箇所等）はここに書き、最終回答では topic 名を参照する形にする。

    生データの丸写しではなく、抽出済みの事実を凝縮して書くこと
    （このノート自体が肥大化すると、後で読む側のコンテキストを圧迫する）。
    同じ topic で複数回呼べば追記されていく（既存内容は上書きされない）。

    Args:
        topic: この書き込みの見出し。以後 list_thread_notes / read_thread_note
            から同じ文字列で参照する（表記ゆれがあると別トピック扱いになるので、
            既存トピックへ追記したい場合は list_thread_notes で正確な表記を確認する）。
        content: 書き込む内容（Markdown等の自由形式）。

    Returns:
        書き込み先の絶対パスと、この topic の累計文字数・ファイル全体の文字数。
        topic/content が空、または書き込みに失敗した場合は「エラー: ...」形式で返す。
    """
    topic_clean = topic.strip()
    if not topic_clean:
        return "エラー: topic が空です。"
    if not content.strip():
        return "エラー: content が空です。"
    path = _thread_notes_path()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    author = _thread_note_author()
    block = f"## {topic_clean}\n<!-- {timestamp} by {author} -->\n{content.rstrip()}\n\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        full_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"エラー: thread note への書き込みに失敗しました: {e}"
    topic_chars = sum(b.content.__len__() for b in _parse_thread_notes(full_text) if b.topic == topic_clean)
    return (
        f'書き込みました: topic="{topic_clean}"（このtopic累計 {topic_chars} 文字、'
        f"ファイル全体 {len(full_text)} 文字）。パス: {path}"
    )


@tool
def list_thread_notes() -> str:
    """スレッド全体で共有されるノート（write_thread_note の書き込み先）の
    トピック一覧を、本文抜きで返す。

    本文を読む前に必ずこれを呼び、対象トピックの文字数を確認すること。
    ノートはスレッドが進むほど肥大化するため、read_thread_note でいきなり
    本文を読むとコンテキストを圧迫しうる。文字数が大きいトピックは、
    read_thread_note が案内する通り dispatch_agent での分析委譲を検討する。

    Returns:
        トピックごとの「文字数・書き込み件数・最終更新日時と書き込み者
        （main または agent_type）」の一覧。ノートがまだ無ければその旨を返す。
    """
    path = _thread_notes_path()
    if not path.is_file():
        return "thread note はまだありません。write_thread_note で最初のトピックを書き込めます。"
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_thread_notes(text)
    if not blocks:
        return "thread note ファイルは存在しますが、有効なトピックがまだありません。"
    grouped: dict[str, list[_ThreadNoteBlock]] = {}
    for b in blocks:
        grouped.setdefault(b.topic, []).append(b)
    lines = [f"thread note のトピック一覧（{len(grouped)}件、ファイル全体 {len(text)} 文字）:"]
    for topic, items in grouped.items():
        total_chars = sum(len(b.content) for b in items)
        last = items[-1]
        lines.append(
            f'- "{topic}": {total_chars}文字（{len(items)}件の書き込み、最終更新 {last.timestamp} by {last.author}）'
        )
    return "\n".join(lines)


@tool
def read_thread_note(topic: str) -> str:
    """スレッド全体で共有されるノートから、指定した topic の内容だけを読む。

    呼ぶ前に必ず list_thread_notes で対象トピックの文字数を確認すること。
    このtopicの合計文字数が大きい場合、この関数は先頭・末尾のみを返し
    中略する（このツール自身の出力でコンテキストを圧迫しないため）。
    全量の精読・分析が必要な場合は、切り詰め時に返る案内の通り
    dispatch_agent で worker または explore へ、このファイルパスと
    topic名を指定して分析を委譲すること（サブエージェントの Read は
    メインエージェントのようなツール呼び出し回数ガードの対象外のため、
    このファイルを直接 Read して全文を精読できる）。

    Args:
        topic: list_thread_notes に表示された、正確なトピック名。

    Returns:
        topicの内容（大きい場合は先頭・末尾のみ＋案内）。ノートが無い、
        またはtopicが見つからない場合は「エラー: ...」形式で返す。
    """
    path = _thread_notes_path()
    if not path.is_file():
        return "エラー: thread note はまだありません。"
    text = path.read_text(encoding="utf-8", errors="replace")
    topic_clean = topic.strip()
    blocks = [b for b in _parse_thread_notes(text) if b.topic == topic_clean]
    if not blocks:
        return f'エラー: topic "{topic_clean}" は見つかりません。list_thread_notes で確認してください。'
    merged = "\n\n".join(f"[{b.timestamp} by {b.author}]\n{b.content}" for b in blocks)
    if len(merged) <= _JOB_OUTPUT_TAIL_CHARS:
        return merged
    half = _JOB_OUTPUT_TAIL_CHARS // 2
    head = merged[:half]
    tail = merged[-half:]
    return (
        f"{head}\n\n"
        f'...(中略: topic "{topic_clean}" は合計 {len(merged)} 文字あり、先頭・末尾のみ表示)...\n\n'
        f"{tail}\n\n"
        f'[全量の精読・分析が必要な場合は dispatch_agent で worker または explore へ、'
        f'「{path} の "{topic_clean}" セクションをReadして分析・要約せよ」という形で委譲してください。]'
    )


def _resolve_analyze_image_path(raw: str) -> Path:
    """analyze_image 専用のパス解決。読み込み系のためパスの制限は行わない。

    相対パスは従来通り skills ルート基準で解決する（SKILL.md の
    references/assets からの参照や既存の呼び出し規約との後方互換のため）。
    絶対パスはそのまま解決する（Read/Glob/Grep と同じ方針で、ローカル
    ファイルシステム上の任意パスを読めることを優先する）。_SKILLS_ROOTS が
    複数ある場合は _safe_path() と同じく前方から順に実在確認し、最初に
    見つかった候補を返す。

    Args:
        raw: analyze_image に渡された relative_path（相対パスまたは絶対パス）。

    Returns:
        解決済みの絶対パス（Path）。

    Raises:
        RuntimeError: init_tools() が未実行の場合。
    """
    if not _SKILLS_ROOTS:
        raise RuntimeError("init_tools() が未実行です")
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    for root in _SKILLS_ROOTS:
        candidate = (root / p).resolve()
        if candidate.exists():
            return candidate
    return (_SKILLS_ROOTS[0] / p).resolve()


def _record_and_check_duplicate(session_key: str, signature: str, max_calls: int = 1) -> bool:
    """同一シグネチャでの呼び出しが会話（セッション）中に上限回数まで既にあったかを判定し、記録する。

    小型ローカルモデルは、進捗が無いまま同一引数のツール呼び出しを会話全体を
    通じて散発的に繰り返すことがある（tune-prompt調査で glob_file.py の同一引数
    2回連続呼び出し、analyze_image で同じ画像を14回重複して呼ぶ実例あり）。直前1回
    との比較だけでは検知できないため、会話（thread）単位で見たシグネチャごとの
    呼び出し回数を cl.user_session に保持し、上限に達しているかどうかで判定する。

    Args:
        session_key: cl.user_session に保存する呼び出し回数辞書のキー
            （呼び出し元ごとに別集合にするため、対象を含める）。
        signature: 呼び出し引数から組み立てた、その呼び出しを一意に表す文字列。
        max_calls: 同一シグネチャを何回まで許可するか（既定1回。
            [file_tools_duplicate_guard].max_calls 等、呼び出し元が設定値を
            渡す場合がある）。

    Returns:
        True であれば、このシグネチャは今回のセッション内で既に上限回数まで
        記録済み（＝今回は重複呼び出し、拒否すべき）。False であれば
        まだ上限に達していない（呼び出し元はその旨をこの関数が記録済みなので、
        通常通り処理を続けてよい）。
    """
    if max_calls <= 0:
        # 0以下は「無制限」を表す（config.ini側の運用上の逃げ道。ガードが
        # モデルの挙動と噛み合わずループ等を起こす場合にここで無効化できる）。
        return False
    counts = cl.user_session.get(session_key)
    if counts is None:
        counts = {}
    count = counts.get(signature, 0)
    is_duplicate = count >= max_calls
    counts[signature] = count + 1
    cl.user_session.set(session_key, counts)
    return is_duplicate


def reset_call_history_guards_after_compaction() -> None:
    """コンテキスト圧縮（要約）で古い会話履歴が消えた際、Read/Glob等の
    重複呼び出しガードが記録しているシグネチャ履歴も合わせてリセットする。

    _check_file_tools_duplicate / analyze_image の重複ガードは
    cl.user_session に会話（thread）全体を通じた呼び出し履歴を保持し続ける
    仕組みだが、要約後のモデルは要約に含まれなかった個々の呼び出し
    （どのファイルをどの引数で読んだか等）を覚えていない。記憶が無いのに
    ガードだけが「既に呼び出し済み」として拒否し続けると、モデルは
    エラーの理由を理解できないまま同じような呼び出しを繰り返し、
    抜け出せないループに陥る。要約が確定した直後に呼ばれる想定
    （app.py の圧縮成功パス。token_usage_cumulative_main のリセットと同様の
    位置づけ）。
    """
    cl.user_session.set("file_tools_call_signatures", None)
    cl.user_session.set("analyze_image_call_signatures", None)


_FAILURE_STREAK_THRESHOLD = 4


def _track_failure_streak(session_key: str, failed: bool, tool_label: str) -> str:
    """同一ツールの連続失敗回数を cl.user_session に記録し、閾値超過時は警告文を返す。

    小型ローカルモデルは、同じコード・引数の微修正を繰り返すだけで根本的な
    アプローチを変えないまま失敗を重ねることがある（tune-prompt調査で
    execute_python_code が13回連続で構文エラーを繰り返した実例あり）。
    system_prompt.md の「連続失敗3回で切り替える」という自己申告ルールだけ
    では守られないことがあるため、ツール自体が連続失敗回数を数え、閾値を
    超えたら結果メッセージに強制的な警告を追記する（会話（thread）単位で
    独立させるため cl.user_session を使う）。

    Args:
        session_key: cl.user_session に保存するカウンタのキー
            （ツールごとに別カウンタにするため、ツール名を含める）。
        failed: 今回の実行が失敗（非0終了コード）だったか。
        tool_label: 警告文に出すツール名（例: "execute_python_code"）。

    Returns:
        閾値に達していれば先頭に改行を含む警告文、そうでなければ空文字列
        （空文字列は呼び出し側で無視してよい）。
    """
    if not failed:
        cl.user_session.set(session_key, 0)
        return ""
    streak = cl.user_session.get(session_key, 0) + 1
    cl.user_session.set(session_key, streak)
    if streak < _FAILURE_STREAK_THRESHOLD:
        return ""
    return (
        f"\n\n【システム警告】{tool_label} が直近{streak}回連続で失敗しています。"
        "同じコード・引数を少しずつ書き直す対症療法をやめ、根本的に別の書き方・"
        "別の手段に切り替えるか、この手段にこだわらず代替アプローチを検討してください。"
    )


@tool
async def run_script(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """スキルの scripts/ 配下のスクリプトを実行し、標準出力/標準エラーを返す。

    作業ディレクトリは、作業フォルダアイコンでユーザーが
    セッションに設定していればそのディレクトリを使う。未設定・アクセス不可・
    書き込み不可の場合は既定の作業フォルダ（default_workdir）配下の
    自セッション専用一時フォルダ（`_tmp_<thread_id>`）を使う
    （default_workdirはサーバー側の共有フォルダのため、直下に成果物を
    書くと他セッションから見えてしまう事故を避けるため。この場合の
    成果物はユーザーへ直接見えないため、provide_download/show_image で
    改めて提示すること）。
    タイムアウトは設定値（既定 60 秒）。完了までこのツール呼び出し自体が
    ブロックされるため、タイムアウトに近い長時間の実行が見込まれるスクリプトは
    このツールではなく run_script_background を使うこと。
    .py スクリプトは設定された Python 実行ファイルで起動する。
    Agent Skills 標準の progressive disclosure における第3段階（Execute）に相当する。
    書き込み系ツールのため、create_plan/approve_plan で計画が承認済みでない
    限り実行できない（未承認の場合はエラーを返す）。ただし副作用のない
    読み取り専用スクリプト（一部は事前に例外登録されている。例: excel-vba-read の
    read_vba.py）はこの承認チェックを免除される。

    **重要: 書き込みは作業ディレクトリ配下限定（サンドボックス）**
    起動するサブプロセスへ書き込みガードを注入しており、open()の書き込み
    モードや os.remove/os.rename/shutil.move 等の呼び出し先が作業
    ディレクトリ・`_tmp_<thread_id>` 配下以外（他ドライブ・Locohaneプロジェクト
    本体、default_workdir直下の他ディレクトリを含む）の場合は
    PermissionError で失敗する。出力先パス（output_path/--output 等）は
    必ず作業ディレクトリ配下を指定すること。default_workdirへ絶対パスで
    直接書き込もうとしても（`_tmp_<thread_id>`以外は）ブロックされる。
    既存ファイルの読み取りはこのガードの対象外で制限されない。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 実行したいスクリプトのファイル名（例: "count.py"）。
            パスや scripts/ プレフィックスは不要 — スキルフォルダの scripts/
            配下から自動検索される。同名ファイルが複数階層にある場合は
            最も浅い階層のものが使われる。
        script_args: スクリプトへ渡す追加引数のリスト（省略可）。書き込み先の
            パスは作業ディレクトリ配下を指定すること（上記サンドボックス参照）。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラーがあれば
        それぞれ「[標準出力]」「[標準エラー]」の見出し付きで連結した文字列。
        スキルに scripts/ が無い場合、スクリプトが見つからない場合、
        計画が未承認の場合、タイムアウトした場合、起動自体に失敗した場合は
        いずれも例外を送出せず「エラー: ...」形式で返す。
    """
    return await _run_script_impl(skill_name, script_filename, script_args)


@tool("Read")
def read_tool(file_path: str, offset: int = 0, limit: int = 10) -> str:
    """ローカルファイルシステム上の任意のテキストファイルを行番号付きで読み込む。

    skills ディレクトリ配下限定の read_skill_file とは異なり、パスの制限は
    行わない（ユーザーが指定した任意の絶対パスを読めることが目的。ただし
    `_tmp_<thread_id>` の他セッション分だけは例外で読み取れない）。
    スキル本文・補助資料を読むなら read_skill_file、ユーザーが指定した
    ファイルを読むならこちらを使う。読み取り専用のため、計画の有無に
    関わらずいつでも呼んでよい。

    Args:
        file_path: 読み込む絶対パス（`@N` のパスメモリー参照も指定可）。
            相対パスを指定した場合は作業ディレクトリ基準で解決する。
        offset: 読み飛ばす先頭行数（0始まり、既定0）。
        limit: 読み込む最大行数（既定10）。大きいファイルの続きを読みたい
            場合は offset を前回の end_line に合わせて指定すること。

    Returns:
        `{"path", "total_lines", "start_line", "end_line", "content", "path_memory"}`
        を持つJSON文字列。`content` は "行番号\\t内容" を改行結合した文字列。
        ファイル不在・ディレクトリ指定・バイナリファイル・同一引数での
        再呼び出し（読み取り専用のため上限回数まで）は例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    path, error = _resolve_file_tools_path(file_path)
    if error:
        return f"エラー: {error}"
    dup_error = _check_file_tools_duplicate("Read", f"Read\x00{path}\x00{offset}\x00{limit}")
    if dup_error:
        return dup_error
    try:
        result = file_tools.read_file(path, offset=offset, limit=limit)
    except ValueError as e:
        return f"エラー: {e}"
    path_memory = _register_path_memory([result["path"]])
    if path_memory:
        result["path_memory"] = path_memory
    logger.info("Read: %s", path)
    return json.dumps(result, ensure_ascii=False)


@tool("Glob")
def glob_tool(pattern: str, path: str = "", head_limit: int = 200) -> str:
    """指定ディレクトリ配下でglobパターンに一致するファイル・ディレクトリを検索する。

    ファイル名検索だけでなく、ディレクトリ階層そのものの調査（対象直下に
    ファイルが1件も無くサブディレクトリしか無いかもしれない場合等）にも
    `"**/*"`/`"*"` 等で使う。読み取り専用のため、計画の有無に関わらず
    いつでも呼んでよい。ただしメインエージェント自身が呼べるのは1ターンに
    つき既定1回のみ（対象ルート直下の確認用の例外）。2回目以降はエラーを
    返すので、それ以降の調査は `dispatch_agent` へ委譲すること。

    Args:
        pattern: globパターン（例: 配下の全Pythonファイルなら "**/*.py"）。
        path: 検索起点ディレクトリの絶対パス（`@N` 可）。省略時は作業ディレクトリ。
        head_limit: ファイル・ディレクトリそれぞれに独立に適用する上限件数（既定200）。

    Returns:
        `{"base", "base_contents", "total_matches", "returned", "truncated",
        "files", "file_details", "total_directories", "returned_directories",
        "directories_truncated", "directories", "path_memory"}` を持つJSON文字列。
        `files`/`directories` は更新日時降順で `head_limit` 件までそれぞれ独立に
        打ち切られる（`truncated`/`directories_truncated` で判別）。
        `file_details` は `files` と同順・同数で、バイナリ判定と総行数
        （`Read` の `limit` を決める前に確認すること）を持つ。
        `files`・`file_details[].path`・`directories[].path` は、パスメモリーに
        登録できた場合は絶対パスそのものではなく `@N` 参照で返る（実体は
        `path_memory` の対応表を見る）。`@N` はそのまま他ツールの絶対パス引数へ
        渡せる。
        起点ディレクトリが存在しない・ディレクトリでない場合は例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    base, error = _resolve_file_tools_path(path)
    if error:
        return f"エラー: {error}"
    dup_error = _check_file_tools_duplicate("Glob", f"Glob\x00{pattern}\x00{base}\x00{head_limit}")
    if dup_error:
        return dup_error
    try:
        result = file_tools.glob_search(base, pattern, head_limit=head_limit, exclude_names=_foreign_tmp_dir_names())
    except ValueError as e:
        return f"エラー: {e}"
    path_memory = _register_path_memory([*result["files"], *[d["path"] for d in result["directories"]]])
    if path_memory:
        # フルパスの重複を `@N` へ畳んでから result に載せる（大量ファイル時に
        # 同じパスが3〜4回積まれてコンテキストを食い潰すのを防ぐ）。
        _dedupe_paths_with_path_memory(result, path_memory)
        result["path_memory"] = path_memory
    logger.info("Glob: pattern=%s base=%s", pattern, base)
    return json.dumps(result, ensure_ascii=False)


@tool("Grep")
def grep_tool(
    pattern: str,
    path: str = "",
    glob: str = "",
    case_insensitive: bool = False,
    context: int = 0,
    head_limit: int = 50,
) -> str:
    """指定ファイル・ディレクトリ配下のテキストから正規表現で検索し、マッチした行番号・内容を返す。

    読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。
    ファイル名ではなくファイルの中身（テキスト）を検索する（ファイル名検索は `Glob` を使うこと）。

    Args:
        pattern: 検索する正規表現。
        path: 検索対象の絶対パス（ファイルまたはディレクトリ、`@N` 可）。
            省略時は作業ディレクトリ配下を検索する。
        glob: 検索前にファイル名で対象を絞り込むglobパターン（省略可、例: "*.py"）。
            ディレクトリ検索時のみ有効。マッチ結果には現れない事前フィルタ。
        case_insensitive: True で大文字小文字を無視する。
        context: マッチ行の前後何行を含めるか（既定0）。
        head_limit: 返却するマッチ件数の上限（既定50）。

    Returns:
        `{"matched", "total_matches", "returned", "truncated",
        "matches": [{"path", "line", "text"}, ...], "path_memory"}` 形状のJSON文字列。
        マッチが1件も無い場合は `{"matched": false, "files": [], "matches": [], "counts": []}`。
        正規表現が不正・対象パスが存在しない場合は例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    base, error = _resolve_file_tools_path(path)
    if error:
        return f"エラー: {error}"
    signature = f"Grep\x00{pattern}\x00{base}\x00{glob}\x00" f"{case_insensitive}\x00{context}\x00{head_limit}"
    dup_error = _check_file_tools_duplicate("Grep", signature)
    if dup_error:
        return dup_error
    try:
        result = file_tools.grep_search(
            base,
            pattern,
            glob=glob,
            output_mode="content",
            case_insensitive=case_insensitive,
            context=context,
            head_limit=head_limit,
            exclude_names=_foreign_tmp_dir_names(),
        )
    except ValueError as e:
        return f"エラー: {e}"
    if result["matched"]:
        paths = list(dict.fromkeys(m["path"] for m in result["matches"]))
        path_memory = _register_path_memory(paths)
        if path_memory:
            result["path_memory"] = path_memory
    logger.info("Grep: pattern=%s base=%s", pattern, base)
    return json.dumps(result, ensure_ascii=False)


@tool
def json_query(query: str, file_path: str = "", json_text: str = "") -> str:
    """JSON/dictデータにJMESPathクエリを実行する。

    JMESPath は jq とは構文が異なる点に注意（`.a.b` ではなく `a.b`、
    `items[?age > \\`30\\`].name` のように書く）。読み取り専用のため、
    計画の有無に関わらずいつでも呼んでよい。

    Args:
        query: JMESPathクエリ文字列。
        file_path: クエリ対象のJSONファイルの絶対パス（`@N` 可）。
            file_path/json_text のどちらか一方を必ず指定すること。
        json_text: クエリ対象のJSON文字列を直接渡す場合に使う
            （execute_python_code の出力等をその場でクエリしたい場合）。
            file_path と同時指定・両方省略はエラー。

    Returns:
        `{"result": ...}` のJSON文字列（該当データが無ければ `{"result": null}`）。
        file_path/json_text の指定不備・JSON解析失敗・クエリ不正の場合は
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    resolved_path: Path | None = None
    if file_path:
        resolved_path, error = _resolve_file_tools_path(file_path)
        if error:
            return f"エラー: {error}"
    signature_source = json_text if not file_path else ""
    signature = (
        f"json_query\x00{query}\x00{resolved_path or ''}\x00"
        f"{hashlib.sha256(signature_source.encode('utf-8')).hexdigest() if signature_source else ''}"
    )
    dup_error = _check_file_tools_duplicate("json_query", signature)
    if dup_error:
        return dup_error
    try:
        result = file_tools.query_json(query, file_path=resolved_path, json_text=json_text or None)
    except ValueError as e:
        return f"エラー: {e}"
    logger.info("json_query: %s", query)
    return json.dumps(result, ensure_ascii=False)


@tool
def list_path_memory() -> str:
    """現在の会話のパスメモリー（`@N`）登録内容を一覧表示する。

    `@N` が何のファイルを指していたか思い出せなくなったとき、または
    Read/Glob/Grep/analyze_image が「パスメモリー @N は登録されていません」と
    返したときに使う。読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。

    Returns:
        `{"entries": [{"index", "path", "valid", "description"}, ...]}` の
        JSON文字列（登録順）。`valid` が false の場合、登録時点では存在したが
        その後削除・移動された可能性がある。path_memory機能が利用できない
        環境では `{"entries": []}` を返す。
    """
    if _PATH_MEMORY_DIR is None:
        return json.dumps({"entries": []}, ensure_ascii=False)
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    entries = path_memory.list_entries(thread_id, _PATH_MEMORY_DIR)
    return json.dumps({"entries": entries}, ensure_ascii=False)


def _prepare_script_execution(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> tuple[list[str], Path] | str:
    """run_script / run_script_background 共通の前処理。

    引数のパスメモリー解決 → スクリプトパス解決 → 作業ディレクトリ解決 →
    計画承認チェック → 実行コマンド組み立て、までを行う。
    (skill_name, script_filename) が config.ini の
    [scripts].plan_approval_exempt_scripts に登録されている場合は
    計画未承認でも実行できる。

    メインエージェント自身の直接呼び出し回数ガード（[main_agent_tool_guard]）は
    ここではなく ImageAwareToolNode.invoke/ainvoke（_guard_main_agent_tool_limit）
    側でツール実行前にかかるため、ここには現れない。

    Returns:
        検証に成功すれば (cmd, workdir) のタプル。失敗すれば
        「エラー: ...」形式の文字列（呼び出し側はそのまま返せばよい）。
    """
    current_agent_type = _SUBAGENT_AGENT_TYPE.get()
    allowed_skills = _AGENT_TYPE_RUN_SCRIPT_SKILL_ALLOWLIST.get(current_agent_type) if current_agent_type else None
    if allowed_skills is not None and skill_name not in allowed_skills:
        return (
            f"エラー: agent_type=\"{current_agent_type}\" から呼び出せる run_script のスキルは "
            f"{sorted(allowed_skills)} に限定されています（skill={skill_name} は対象外）。"
            "ファイルの内容確認が必要な場合は、委譲元に対応するサブエージェント"
            "（office文書/PDFなら analyze-docs、書き込みが要るなら worker）へ"
            "改めて委譲するよう伝えてください。"
        )

    args = script_args or []
    # args 内の各要素で `@N`（パスメモリー参照）を実パスへ解決する。
    # 対象外の文字列（トークン形式でない）はそのまま通す。
    resolved_args = []
    for a in args:
        resolved, error = _resolve_path_memory_token(a)
        if error:
            return f"エラー: {error}"
        resolved_args.append(resolved)
    args = resolved_args
    try:
        script_path = _resolve_script_filename(skill_name, script_filename)
    except ValueError as e:
        return f"エラー: {e}"
    # work_dir未設定・書き込み不可等でdefault_workdirへフォールバックする
    # 場合、cwdをdefault_workdir直下ではなく`_tmp_<thread_id>`にする
    # （_restrict_default_workdir参照。サーバー側共有ディレクトリである
    # default_workdir直下に、相対パス書き込みの既定の置き場として
    # 全セッション共通で成果物が積まれるのを防ぐ）。
    workdir = _restrict_default_workdir(_resolve_workdir(need_write=True))

    is_plan_exempt = (skill_name, script_filename) in _PLAN_APPROVAL_EXEMPT_SCRIPTS
    if not is_plan_exempt and not cl.user_session.get("plan_approved"):
        logger.info("run_script: 計画未承認のためブロック skill=%s script=%s", skill_name, script_filename)
        return (
            "エラー: 計画が未承認のため実行できません"
            f"（skill={skill_name}, script={script_filename}）。"
            "create_plan で計画を作成し、approve_plan でユーザーの承認を得てから"
            "実行してください。"
        )

    # .py は設定の Python で、それ以外はそのまま実行を試みる。
    if script_path.suffix == ".py":
        cmd = [_SCRIPT_PYTHON, str(script_path), *args]
    else:
        cmd = [str(script_path), *args]
    return cmd, workdir


async def _run_script_impl(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """run_script の実行本体。

    公開ツールの引数名は "args" ではなく "script_args"（"args"/"kwargs" は
    pydantic の ValidatedFunction が *args/**kwargs 用プレースホルダとして
    予約している名前と衝突し、生成されるスキーマのフィールド名が
    "v__args" に化けて run_script() 呼び出しが TypeError になるため使えない）。
    """
    prepared = _prepare_script_execution(skill_name, script_filename, script_args)
    if isinstance(prepared, str):
        return prepared
    cmd, workdir = prepared
    env, guard_dir = _run_script_guard_env(workdir)

    logger.info("run_script: %s %s cwd=%s", skill_name, script_filename, workdir)
    try:
        # 承認待ちの await 済みで別スレッドの必要はないが、subprocess.run 自体は
        # ブロッキング呼び出しのため、to_thread でイベントループのブロックを避ける。
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"エラー: スクリプトが {_SCRIPT_TIMEOUT} 秒でタイムアウトしました。"
    except OSError as e:
        return f"エラー: スクリプトを実行できませんでした: {e}"
    finally:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)

    # stdout / stderr / 終了コードをまとめて返す（LLM が結果を解釈できるように）。
    parts = [f"[終了コード] {proc.returncode}"]
    if proc.stdout:
        parts.append(f"[標準出力]\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"[標準エラー]\n{proc.stderr.rstrip()}")
    warning = _track_failure_streak("run_script_failure_streak", proc.returncode != 0, "run_script")
    if warning:
        parts.append(warning)
    return "\n".join(parts)


@dataclass
class _BackgroundJob:
    """run_script_background で起動したジョブの状態。

    モジュールレベルの _BACKGROUND_JOBS に job_id をキーとして保持する。
    """

    process: asyncio.subprocess.Process
    thread_id: str
    skill_name: str
    script_filename: str
    started_at: float
    stdout_chunks: list[str]
    stderr_chunks: list[str]
    status: str  # "running" | "completed" | "failed" | "timeout" | "killed" | "error"
    returncode: int | None
    error_message: str | None
    runner_task: "asyncio.Task | None" = None
    # execute_python_code_background 由来のジョブのみ設定される
    # （run_script_background 由来のジョブでは None のまま）。
    tmp_path: "Path | None" = None
    workdir: "Path | None" = None
    before_snapshot: "dict[Path, float] | None" = None
    # run_script_background / execute_python_code_background 由来のジョブの
    # 書き込みガード用一時ディレクトリ（_run_script_guard_env が作成）。
    # ジョブ終了後に _run_background_job の finally で削除する。ガード注入に
    # 失敗した場合や execute_python_code_background 由来の場合は None のまま
    # （execute_python_code_background 側はコード先頭連結方式のためこの
    # フィールドを使わない）。
    guard_dir: "Path | None" = None
    # check_script_job が直前に「実行中」ステータスを返した時刻
    # （time.monotonic()）。None は未確認（起動直後でまだ一度も
    # check_script_job が呼ばれていない）ことを表す。連続呼び出しの
    # 最短間隔（_SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS）を
    # サーバー側で強制するために使う。
    last_polled_at: float | None = None


# run_script_background のジョブレジストリ。プロセス内メモリのみで永続化は
# しない（アプリ再起動でジョブは失われるが、そもそも実行中プロセスも
# 再起動で失われるため実害はない）。
_BACKGROUND_JOBS: dict[str, _BackgroundJob] = {}

# check_script_job が「実行中」ステータスで返す標準出力/標準エラーの末尾の
# 最大文字数（全量を返すとコンテキストを圧迫するため切り詰める）。
_JOB_OUTPUT_TAIL_CHARS = 4000


async def _read_stream_into(stream: "asyncio.StreamReader | None", chunks: list[str]) -> None:
    """サブプロセスの stdout/stderr を EOF まで読み、行単位で chunks に追記する。"""
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        chunks.append(line.decode("utf-8", errors="replace"))


def _format_background_job_progress(job: "_BackgroundJob", job_id: str) -> str:
    """run_script_background/execute_python_code_background ジョブの実行中の
    状況を表す文字列を組み立てる。

    _push_background_job_progress（人間向けのUI直接push）と check_script_job
    の running 分岐（フォールバック経路でのLLM向け応答）の両方から呼ぶ、
    表示フォーマット共通化のためのヘルパー（dispatch_agent の
    _format_dispatch_agent_progress と同じ役割）。
    """
    elapsed = int(time.monotonic() - job.started_at)
    parts = [f"実行中です（経過 {elapsed} 秒・job_id={job_id}）。"]
    stdout_tail = "".join(job.stdout_chunks)[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
    stderr_tail = "".join(job.stderr_chunks)[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
    if stdout_tail:
        parts.append(f"[標準出力（末尾）]\n{stdout_tail}")
    if stderr_tail:
        parts.append(f"[標準エラー（末尾）]\n{stderr_tail}")
    return "\n".join(parts)


async def _push_background_job_progress(job: "_BackgroundJob", job_id: str) -> None:
    """run_script_background/execute_python_code_background の実行中、人間向けに
    進捗をチャットへ直接pushする。

    cl.Message送信のみでLLM呼び出しを一切伴わないためトークンを消費しない
    （dispatch_agent の _push_dispatch_agent_progress と同じ設計）。
    author=_SUBAGENT_MESSAGE_AUTHOR は使わない（サブエージェントの最終回答
    専用の識別子のため）。代わりに type="system_message" を使う
    （app.py がコンテキスト圧縮の通知等、既存の一時的なシステム通知に
    使っている慣習と同じ）。

    _run_background_job の finally で確実にキャンセルされる想定
    （_push_dispatch_agent_progress と同じ理由）。

    metadata={"ephemeral_progress": True} を付ける（_push_dispatch_agent_progress
    と同じ）。ChatThreadDataLayer.create_step（src/thread_store.py）がこの
    フラグを見て永続化をスキップするため、スレッド再開時に「経過N秒・
    job_id=xxx」という実行時点でしか意味を持たない古い進捗表示が復元されない
    （2026-08-21 ユーザー報告）。ライブ表示（emitter.send_step）には影響しない。
    """
    while job.status == "running":
        await asyncio.sleep(_SCRIPT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS)
        if job.status != "running":
            break
        await cl.Message(
            content=_format_background_job_progress(job, job_id),
            type="system_message",
            metadata={"ephemeral_progress": True},
        ).send()


async def _run_background_job(job: "_BackgroundJob", job_id: str) -> None:
    """バックグラウンドジョブのランナータスク本体。

    stdout/stderr の読み取りと終了コード取得を並行して行い、
    background_max_runtime_seconds を超えたら強制終了する。
    stop_script_job が先に status を "killed" にしていた場合はそれを
    上書きしない。

    進捗push タスク（_push_background_job_progress）をここで生成・管理する。
    run_script_background/execute_python_code_background 自身が安全上限
    フォールバックでターンを終えた後も、このタスク（＝ジョブ本体）が生きて
    いる限り進捗pushは動き続ける（dispatch_agent の _run_dispatch_agent_job
    と同じ設計）。
    """
    progress_task = asyncio.create_task(_push_background_job_progress(job, job_id))
    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream_into(job.process.stdout, job.stdout_chunks),
                    _read_stream_into(job.process.stderr, job.stderr_chunks),
                    job.process.wait(),
                ),
                timeout=_SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS,
            )
        except asyncio.TimeoutError:
            job.process.kill()
            await job.process.wait()
            if job.status != "killed":
                job.status = "timeout"
            job.returncode = job.process.returncode
            return
        except Exception as e:  # noqa: BLE001 - ストリーム読み取り自体の異常はエラー扱いで返す
            if job.status != "killed":
                job.status = "error"
                job.error_message = f"{e}\n{traceback.format_exc()}"
            return

        job.returncode = job.process.returncode
        if job.status == "killed":
            return
        job.status = "completed" if job.returncode == 0 else "failed"
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        # execute_python_code_background が書き出した一時 .py ファイルの後始末。
        # run_script_background 由来のジョブでは tmp_path が None のため何もしない。
        if job.tmp_path is not None:
            job.tmp_path.unlink(missing_ok=True)
        # run_script_background 由来のジョブの書き込みガード用一時ディレクトリの後始末
        # （_run_script_guard_env が作成。ガード注入に失敗した場合は None のため何もしない）。
        if job.guard_dir is not None:
            shutil.rmtree(job.guard_dir, ignore_errors=True)


def _purge_stale_background_jobs() -> None:
    """完了済みのまま check_script_job で回収されなかったジョブを掃除する。

    専用のクリーンアップループは持たず、run_script_background の呼び出しの
    度に opportunistic に走らせる。
    """
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _BACKGROUND_JOBS.items()
        if job.status != "running" and now - job.started_at > _SCRIPT_BACKGROUND_JOB_RETENTION_SECONDS
    ]
    for job_id in stale:
        _BACKGROUND_JOBS.pop(job_id, None)


def _format_job_result(job: "_BackgroundJob") -> str:
    """終了済みジョブの結果を run_script と同じ表示形式に整形する。"""
    returncode_label = job.returncode if job.returncode is not None else "不明"
    parts = [f"[終了コード] {returncode_label}"]
    stdout = "".join(job.stdout_chunks).rstrip()
    stderr = "".join(job.stderr_chunks).rstrip()
    if stdout:
        parts.append(f"[標準出力]\n{stdout}")
    if stderr:
        parts.append(f"[標準エラー]\n{stderr}")
    # execute_python_code_background 由来のジョブのみ workdir/before_snapshot が
    # 設定されている（run_script_background 由来では None のためスキップされる）。
    if job.workdir is not None and job.before_snapshot is not None:
        path_memory_note = _register_exec_output_files(job.workdir, job.before_snapshot, job.thread_id)
        if path_memory_note:
            parts.append(path_memory_note)
    return "\n".join(parts)


def _background_job_started_message(job_id: str) -> str:
    """run_script_background/execute_python_code_background が安全上限
    （[scripts].background_inline_wait_max_seconds）に達してもなおジョブが
    終わっていない場合に、フォールバックとしてLLMへ返す案内文。

    通常はこの文言に到達しない（run_script_background/
    execute_python_code_background 自身がジョブ完了までツール呼び出し内で
    待ち続け、最終結果を直接返すため）。到達するのは、設定された安全上限を
    超えるほど長時間のジョブだけである（dispatch_agent の
    _dispatch_agent_job_started_message と同じ設計）。

    以前は「途中で打ち切る場合は stop_script_job を使ってください」という
    表現だけだったが、これが「長時間かかる処理は打ち切るべきもの」という
    誤読を誘発し、ユーザーが完走を求めているのにモデルが自発的に
    stop_script_job を呼んで途中終了させてしまう事例が確認された。
    処理時間の長さ自体は打ち切る理由にならないことを明記する。
    """
    return (
        f"バックグラウンドで実行中です（job_id={job_id}）。長時間かかっているため、"
        "いったんこのターンを終えて制御を返します（ジョブ自体は裏側で動き続けます）。\n"
        "完了確認・結果取得には check_script_job（job_id指定）を使うこと。"
        "処理に時間がかかっていること自体は打ち切る理由にはならない。"
        "ユーザーから明示的に中断・キャンセルを指示された場合にのみ"
        "stop_script_job（job_id指定）を使うこと。"
    )


def _resolve_job(job_id: str) -> "_BackgroundJob | str":
    """job_id を現在のセッション所有のジョブへ解決する（他セッションは拒否）。"""
    job = _BACKGROUND_JOBS.get(job_id)
    if job is None:
        return f"エラー: job_id '{job_id}' は見つかりません（既に取得済みか、無効なIDです）。"
    thread_id = cl.user_session.get("thread_id") or ""
    if job.thread_id != thread_id:
        return f"エラー: job_id '{job_id}' は現在のセッションのものではありません。"
    return job


def _finalize_script_job_result(job: "_BackgroundJob", job_id: str) -> str:
    """終端状態（completed/failed/timeout/killed/error）のジョブを最終結果
    文字列へ整形し、レジストリから取り除く。

    run_script_background/execute_python_code_background（安全上限内に完了
    した場合）と check_script_job（終端状態を取得した場合）の両方から呼ぶ、
    ワンショット取得（一度取得したら同じ job_id は再利用できない）の共通処理
    （dispatch_agent の _finalize_dispatch_agent_job_result と同じ役割）。
    stop_script_job は自身がジョブを終端させた直後に独自の「強制終了しました。」
    接頭辞で結果を返すため、こちらは使わない。
    """
    result = _format_job_result(job)
    if job.status == "timeout":
        result = f"エラー: バックグラウンド実行が {_SCRIPT_BACKGROUND_MAX_RUNTIME_SECONDS} " f"秒の上限に達したため強制終了しました。\n{result}"
    elif job.status == "killed":
        result = f"stop_script_job により強制終了されました。\n{result}"
    elif job.status == "error":
        result = f"エラー: バックグラウンド実行中に問題が発生しました: {job.error_message}"
    _BACKGROUND_JOBS.pop(job_id, None)
    return result


@tool
async def run_script_background(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """スキルの scripts/ 配下のスクリプトをバックグラウンドで起動する。

    処理時間が長くなることが見込まれるスクリプト向け。run_script と異なり、
    このターン自体は（下記の安全上限に達しない限り）完了までブロックされる
    （Chainlit UI上は「実行中」表示が続く）。待っている間、人間向けに
    チャットへ直接進捗（経過秒数・標準出力/標準エラー末尾）が自動で通知
    されるため、自分から check_script_job を繰り返し呼んでポーリングする
    必要は無い（進捗はコード側が直接チャットへ送るため、LLMの呼び出し回数・
    トークン消費は増えない）。1回の呼び出しで run_script と同じ形式の
    最終結果がそのまま返る。

    設定した安全上限（[scripts].background_inline_wait_max_seconds）を
    超えてもなお完了しない場合に限り、job_id を含む案内文を返してこのターンを
    終える（ジョブ自体は裏側で動き続ける）。この場合のみ、後続ターンで
    check_script_job（結果取得）・stop_script_job（明示的な中断指示があった
    場合のみ）を使う。

    引数解決・作業ディレクトリ解決・計画承認チェック（例外の扱いも含む）、
    および書き込みサンドボックスガード（出力先は作業ディレクトリ/
    default_workdir配下限定、詳細は run_script のdocstring参照）は
    run_script と同じ。バックグラウンドジョブを強制終了するまでの上限は
    既定3600秒。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 実行したいスクリプトのファイル名（例: "count.py"）。
        script_args: スクリプトへ渡す追加引数のリスト（省略可）。

    Returns:
        通常は run_script と同じ形式の最終結果文字列。安全上限に達した
        場合のみ job_id を含む案内文字列。引数不正・スクリプトが見つからない・
        計画未承認・起動自体に失敗した場合は run_script 同様「エラー: ...」
        形式の文字列を返す（この場合 job は作られない）。
    """
    prepared = _prepare_script_execution(skill_name, script_filename, script_args)
    if isinstance(prepared, str):
        return prepared
    cmd, workdir = prepared
    env, guard_dir = _run_script_guard_env(workdir)

    _purge_stale_background_jobs()

    logger.info("run_script_background: %s %s cwd=%s", skill_name, script_filename, workdir)
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as e:
        if guard_dir is not None:
            shutil.rmtree(guard_dir, ignore_errors=True)
        return f"エラー: スクリプトを起動できませんでした: {e}"

    job_id = uuid.uuid4().hex[:12]
    job = _BackgroundJob(
        process=process,
        thread_id=cl.user_session.get("thread_id") or "",
        skill_name=skill_name,
        script_filename=script_filename,
        started_at=time.monotonic(),
        stdout_chunks=[],
        stderr_chunks=[],
        status="running",
        returncode=None,
        error_message=None,
        guard_dir=guard_dir,
    )
    job.runner_task = asyncio.create_task(_run_background_job(job, job_id))
    _BACKGROUND_JOBS[job_id] = job

    # asyncio.shield: このwait_forがタイムアウトして例外を送出しても、
    # job.runner_task 自体はキャンセルされず裏側で動き続ける
    # （安全上限超過時のフォールバック挙動の要。dispatch_agent と同じ設計）。
    # 0以下は無期限待ち。
    wait_timeout = _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS if _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS > 0 else None
    try:
        await asyncio.wait_for(asyncio.shield(job.runner_task), timeout=wait_timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "run_script_background: 安全上限(%s秒)に達したため job_id を返してターンを終えます: job_id=%s",
            wait_timeout,
            job_id,
        )
        return _background_job_started_message(job_id)

    return _finalize_script_job_result(job, job_id)


@tool
async def check_script_job(job_id: str) -> str:
    """run_script_background/execute_python_code_background で起動したジョブの
    状況・結果を確認する。

    通常は run_script_background/execute_python_code_background 自身が
    ジョブ完了まで待ってから最終結果を直接返すため、このツールを呼ぶ必要は
    無い。呼ぶのは、それらが安全上限に達して job_id を返した場合
    （＝よほど長時間のジョブ）のみ。

    実行中であれば経過秒数と、現時点までの標準出力・標準エラーの末尾
    （最大4000文字）を返す。完了・失敗・タイムアウト・強制終了のいずれかで
    終わっていれば、run_script と同じ形式
    （"[終了コード] N" に続けて "[標準出力]"/"[標準エラー]"）で最終結果を返し、
    以降は同じ job_id を指定できなくなる（登録から削除される）。
    他セッションが起動した job_id は参照できない。

    実行中（"実行中です（経過 N 秒）。"）が返ってきた場合、数秒間隔で連続
    して呼び直さないこと。経過をユーザーへ一言伝えたらそのターンを終えて
    次のユーザー発言を待つか、十分な間隔（数十秒〜）を空けてから改めて
    呼ぶこと。処理に時間がかかっていること自体は異常でも打ち切る理由でもない。
    なお、この指示に反して短い間隔で呼び直した場合はサーバー側で拒否され、
    「まだ確認間隔が短すぎます」のような拒否メッセージが返る（既定の最小
    間隔は20秒）。

    Args:
        job_id: run_script_background/execute_python_code_background の
            戻り値に含まれるID。

    Returns:
        状況または最終結果を表す文字列。job_id が不明・他セッションのもので
        ある場合、または直前の「実行中」応答から
        background_min_poll_interval_seconds 未満しか経っていない場合は
        「エラー: ...」/拒否メッセージ（[scripts].background_min_poll_message、
        既定は「まだ確認間隔が短すぎます...」）形式の文字列。
    """
    resolved = _resolve_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status == "running":
        now = time.monotonic()
        if (
            _SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS > 0
            and job.last_polled_at is not None
            and (now - job.last_polled_at) < _SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS
        ):
            wait_remaining = int(_SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS - (now - job.last_polled_at)) + 1
            return _SCRIPT_BACKGROUND_MIN_POLL_MESSAGE.format(
                wait_remaining=wait_remaining,
                job_id=job_id,
                min_interval=_SCRIPT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS,
            )
        job.last_polled_at = now
        return _format_background_job_progress(job, job_id)

    return _finalize_script_job_result(job, job_id)


@tool
async def stop_script_job(job_id: str) -> str:
    """run_script_background で起動したジョブを強制終了する。

    ユーザーから明示的に中断・キャンセル・停止を指示された場合にのみ使う
    こと。処理に時間がかかっていること自体（check_script_job が実行中を
    返し続けること）は、自分の判断で打ち切ってよい理由にはならない。
    強制終了時点までの標準出力・標準エラーを添えて結果を返し、登録から
    削除する。他セッションが起動した job_id は操作できない。

    Args:
        job_id: run_script_background の戻り値に含まれるID。

    Returns:
        強制終了結果を表す文字列。job_id が不明・他セッションのものである、
        または既に終了済みの場合は「エラー: ...」形式の文字列。
    """
    resolved = _resolve_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status != "running":
        return f"エラー: job_id '{job_id}' は既に終了しています（status={job.status}）。" "check_script_job で結果を取得してください。"

    job.status = "killed"
    try:
        job.process.kill()
    except ProcessLookupError:
        pass
    if job.runner_task is not None:
        await job.runner_task

    result = _format_job_result(job)
    _BACKGROUND_JOBS.pop(job_id, None)
    return f"強制終了しました。\n{result}"


def _register_exec_output_files(workdir: Path, before_snapshot: dict[Path, float], thread_id: str) -> str:
    """execute_python_code の実行前後で workdir 直下のファイル差分を検知し、
    新規作成/更新されたファイルを path_memory へ自動登録する。

    LLMが execute_python_code のコード内で相対パス書き込みしたファイル
    （中間生成物）を、後続の run_script 等へ渡す際にLLMが絶対パスを手で
    組み立て直す必要が無いようにするため（cwdが `_tmp_<thread_id>` に
    切り替わったことをLLMが意識しなくて済む）。

    Args:
        workdir: execute_python_code が使った実行用ディレクトリ
            （_resolve_exec_workdir() の戻り値）。
        before_snapshot: 実行前に取得した {ファイルパス: mtime} のスナップショット。
        thread_id: path_memory への登録に使うセッションID。
            execute_python_code_background 経由の呼び出しでは
            check_script_job 呼び出し時の cl.user_session とジョブ起動時の
            セッションが一致する保証に頼らず、job.thread_id を明示的に渡す。

    Returns:
        新規作成/更新ファイルがあれば「[生成/更新ファイル]」見出し付きの
        文字列（1行1ファイル、`@N ファイル名（新規作成|更新）` 形式）。
        対象ファイルが無い場合は空文字列。パスメモリーへ登録できなかった
        ファイルは絶対パスをそのまま表示する。
    """
    try:
        after_files = [p for p in workdir.iterdir() if p.is_file()]
    except OSError:
        return ""

    changed: list[tuple[Path, str]] = []
    for p in after_files:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        prev = before_snapshot.get(p)
        if prev is None:
            changed.append((p, "新規作成"))
        elif mtime != prev:
            changed.append((p, "更新"))
    if not changed:
        return ""

    lines = []
    for p, kind in changed:
        index = None
        if _PATH_MEMORY_DIR is not None:
            index = path_memory.register(
                thread_id,
                str(p),
                _PATH_MEMORY_DIR,
                _PATH_MEMORY_MAX_ENTRIES,
                description=f"execute_python_codeが{kind}",
            )
        if index is not None:
            lines.append(f"@{index} {p.name}（{kind}）")
        else:
            lines.append(f"{p}（{kind}、パスメモリー登録失敗のため絶対パスをそのまま使用）")
    return "[生成/更新ファイル]\n" + "\n".join(lines)


def _exec_guard_roots() -> list[Path]:
    """execute_python_code系（execute_python_code/execute_python_code_background）
    のガードに渡す allowed_roots を組み立てる。

    実際の作業ディレクトリ（_resolve_workdir(need_write=True)。書き込み
    不可と判定されていれば default_workdir へ自動的に倒れる。倒れた場合は
    _restrict_default_workdir() により `_tmp_<thread_id>` へさらに縮小
    される）・`_tmp_<thread_id>`（_resolve_exec_workdir()。work_dir が
    default_workdir以外を指している場合でも常に念のため加える）に加え、
    path_memory_dir（LLM生成コードが path_memory.register()/resolve() を
    直接呼ぶ場合にロックファイル書き込みが必要になるLocohane内部の
    状態ディレクトリ）を含める。default_workdir自体（`_tmp_<thread_id>`
    以外のサブディレクトリや直下）は意図的に含めない
    （default_workdirはサーバー側の共有ディレクトリのため、他セッションが
    誤参照する事故を避けるため常に自セッション専用の`_tmp_<thread_id>`
    だけに書き込みを限定する。_restrict_default_workdir参照）。
    4箇所の呼び出し元での重複を避けるための共通ヘルパー。

    以前は実行用ディレクトリ（_tmp_<thread_id>）の親から作業ディレクトリを
    逆算していたが、_tmp_<thread_id> が常に default_workdir 配下に固定
    されたため（_resolve_exec_workdir() 参照）、ここで独立に解決する
    必要がある（そうしないと実際の work_dir が allowed_roots から漏れ、
    execute_python_code がユーザー指定の作業ディレクトリへ書き込めなく
    なってしまう）。
    """
    roots = [_restrict_default_workdir(_resolve_workdir(need_write=True)), _resolve_exec_workdir()]
    if _PATH_MEMORY_DIR is not None:
        roots.append(_PATH_MEMORY_DIR)
    return roots


def _python_fs_guard_preamble(allowed_roots: Sequence[Path], tmp_dir_roots: Sequence[Path] = ()) -> str:
    """execute_python_code / run_script が実行するコードの先頭（または
    サブプロセスの sitecustomize.py）に連結する、書き込みサンドボックス用の
    ガードコードを生成する。

    LLMが生成したコードや、スキルの scripts/ 配下のスクリプトは絶対パスや
    `..` で任意の場所へ書き込めてしまい、cwd を作業用ディレクトリに絞る
    だけでは他ドライブやプロジェクト本体を誤って書き換える事故を防げない。
    ここで生成するコードは、サブプロセス内で `open`/`os`/`shutil` の
    書き込み・削除・改名系関数をモンキーパッチし、allowed_roots（作業
    ディレクトリと default_workdir）配下以外への操作を場所を問わず
    ブロックする（原則「書き込みは常にサンドボックス配下限定」。以前は
    Locohaneのプロジェクトフォルダだけを保護し、それ以外のドライブ・
    フォルダへの書き込みは無制限に許可していたが、それでは
    「作業ディレクトリの外に書き込まれる」事故を防げないため、常に
    allowed_roots 配下限定へ強化した）。悪意ある回避（ctypes直叩き等）
    までは防げないベストエフォートのガードであり、あくまで「LLMが
    悪気なく意図しない場所に書き込んでしまう」事故防止が目的。

    加えて、`subprocess.Popen`（`run`/`call`/`check_call`/`check_output`
    もこれを経由する）・`os.system`・`os.popen` をモンキーパッチし、
    コマンド名（basename、拡張子は無視）が git / npm / pip / pip3 、
    および copy / xcopy / move / robocopy / del / erase / ren / rename /
    rd / rmdir 等のファイル操作コマンド、cmd / cmd.exe / powershell / pwsh
    等のシェルラッパーのいずれかに一致する場合は場所を問わず
    PermissionError にする。生成・実行させたコードが誤ってリポジトリ操作や
    パッケージインストールを行う事故を防ぐことに加え、`open`/`os`/`shutil`
    への書き込みガードを `os.system("copy ...")` 等のシェル経由で回避
    されるのを防ぐ（tune-prompt iter1でsystem_prompt_scale/002実行中、
    LLMが実際にこの手口で allowed_roots 外へファイルをコピーすることに
    成功した事例を確認したため追加。`cmd /c copy ...` のような多段の
    シェルラッパー経由でのコマンド名偽装までは防げないベストエフォートの
    対策）。こちらは allowed_roots による除外はない（常に全面禁止）。

    さらに tmp_dir_roots 配下（`_tmp_<thread_id>`。execute_python_code系の
    中間生成物置き場、`_resolve_exec_workdir()`）については、自セッション
    以外の `_tmp_<X>` への読み取り（`open()`）・書き込み・削除・改名を
    allowed_roots の内外を問わず一律ブロックする（他セッションの一時
    ファイルをLLM生成コードが誤って読む・書き換える事故の防止。
    ディレクトリ一覧取得 `os.listdir`/`os.scandir`/`Path.iterdir`/`os.walk`
    はv1では対象外）。自セッション自身の `_tmp_<own>` はこれまで通り
    無制限に読み書きできる。

    Args:
        allowed_roots: 書き込み・削除を許可するディレクトリの一覧
            （実行用ディレクトリと `_tmp_<thread_id>`。呼び出し元
            （_exec_guard_roots/_run_script_guard_env）が
            _restrict_default_workdir() によって default_workdir自体を
            渡さないよう既に縮小済み）。
        tmp_dir_roots: `_tmp_<thread_id>` が実際に作られうる親ディレクトリの
            一覧（`_tmp_dir_parents()` の戻り値）。他セッション判定にのみ
            使い、allowed_roots とは独立（execute_python_code_readonly は
            allowed_roots=[] で全面書き込み禁止だが、他セッションtmp判定
            自体はここに渡す tmp_dir_roots で別途機能する）。

    Returns:
        コード文字列の先頭に連結する、あるいは sitecustomize.py として
        そのまま配置できるモンキーパッチ処理のPythonソース。呼び出し元の
        コード自体には何も変更を加えない。
    """
    # tuple の repr をそのまま埋め込む（旧実装は ", ".join(...) を "(...,)" で
    # 囲んでいたため、allowed_roots が空リストのとき ("",) という「空文字列
    # 1件を含むタプル」になってしまい、os.path.realpath("") がカレント
    # ディレクトリを指す結果、書き込みガードが実質無効化される不具合が
    # あった。repr(tuple(...)) なら空リストは正しく "()" になる。
    allowed_repr = repr(tuple(str(p) for p in allowed_roots))
    tmp_roots_repr = repr(tuple(str(p) for p in tmp_dir_roots))
    return f'''\
import builtins as _guard_builtins
import io as _guard_io
import os as _guard_os
import shutil as _guard_shutil

_GUARD_ALLOWED = [_guard_os.path.realpath(_p) for _p in {allowed_repr}]
_GUARD_TMP_ROOTS = [_guard_os.path.realpath(_p) for _p in {tmp_roots_repr}]
_GUARD_OWN_TMP_NAME = "_tmp_" + _guard_os.environ.get("AGENT_THREAD_ID", "_no_session")


def _guard_check_foreign_tmp(_path):
    try:
        _target = _guard_os.path.realpath(_guard_os.fspath(_path))
    except TypeError:
        return
    for _root in _GUARD_TMP_ROOTS:
        if _target == _root:
            return
        if _target.startswith(_root + _guard_os.sep):
            _first = _target[len(_root) + 1 :].split(_guard_os.sep, 1)[0]
            if _first.startswith("_tmp_") and _first != _GUARD_OWN_TMP_NAME:
                raise PermissionError(
                    f"[一時ディレクトリガード] 他セッションの一時ディレクトリへは"
                    f"アクセスできません: {{_path}}"
                )
            return


def _guard_check(_path, _op):
    _guard_check_foreign_tmp(_path)
    try:
        _target = _guard_os.path.realpath(_guard_os.fspath(_path))
    except TypeError:
        return
    for _root in _GUARD_ALLOWED:
        if _target == _root or _target.startswith(_root + _guard_os.sep):
            return
    raise PermissionError(
        f"[書き込みサンドボックスガード] 作業ディレクトリ配下以外は{{_op}}できません: {{_path}}\\n"
        "作業ディレクトリ（またはセッション専用の一時フォルダ _tmp_<thread_id>）配下のみ"
        "書き込み・削除可能です。default_workdir直下など共有フォルダへは"
        "直接書き込めません。"
    )


_guard_orig_open = _guard_builtins.open


def _guard_open(_file, _mode="r", *_args, **_kwargs):
    if any(_c in _mode for _c in ("w", "a", "x", "+")):
        _guard_check(_file, "書き込み")
    else:
        _guard_check_foreign_tmp(_file)
    return _guard_orig_open(_file, _mode, *_args, **_kwargs)


_guard_builtins.open = _guard_open
_guard_io.open = _guard_open

for _guard_name in ("remove", "unlink", "rename", "replace", "rmdir", "removedirs", "mkdir", "makedirs", "truncate"):
    def _guard_make_os(_orig, _name):
        def _fn(_path, *_args, **_kwargs):
            _guard_check(_path, _name)
            if _name in ("rename", "replace") and _args:
                _guard_check(_args[0], _name)
            return _orig(_path, *_args, **_kwargs)

        return _fn

    _guard_orig = getattr(_guard_os, _guard_name, None)
    if _guard_orig is not None:
        setattr(_guard_os, _guard_name, _guard_make_os(_guard_orig, _guard_name))

for _guard_name in ("rmtree", "move", "copy", "copy2", "copyfile", "copytree"):
    def _guard_make_shutil(_orig, _name):
        def _fn(_src, *_args, **_kwargs):
            if _name == "rmtree":
                _guard_check(_src, _name)
            else:
                _guard_check(_src, _name)
                if _args:
                    _guard_check(_args[0], _name)
            return _orig(_src, *_args, **_kwargs)

        return _fn

    _guard_orig = getattr(_guard_shutil, _guard_name, None)
    if _guard_orig is not None:
        setattr(_guard_shutil, _guard_name, _guard_make_shutil(_guard_orig, _guard_name))

del _guard_name, _guard_orig

import subprocess as _guard_subprocess

_GUARD_BLOCKED_CMDS = {{
    "git", "npm", "pip", "pip3",
    "copy", "xcopy", "move", "robocopy", "del", "erase",
    "ren", "rename", "rd", "rmdir",
    "cmd", "cmd.exe", "powershell", "pwsh",
}}


def _guard_cmd_basename(_arg):
    try:
        _s = _guard_os.fspath(_arg)
    except TypeError:
        _s = _arg
    _base = _guard_os.path.basename(str(_s)).lower()
    for _ext in (".exe", ".cmd", ".bat"):
        if _base.endswith(_ext):
            _base = _base[: -len(_ext)]
            break
    return _base


def _guard_check_cmd(_args):
    if isinstance(_args, (str, bytes)):
        _tokens = str(_args).strip().split()
        _first = _tokens[0] if _tokens else ""
    elif isinstance(_args, _guard_os.PathLike):
        _first = _args
    elif _args:
        _first = _args[0]
    else:
        _first = ""
    if _guard_cmd_basename(_first) in _GUARD_BLOCKED_CMDS:
        raise PermissionError(
            f"[execute_python_codeガード] git/npm/pipコマンドの実行は禁止されています: {{_args}}"
        )


_guard_orig_popen_init = _guard_subprocess.Popen.__init__


def _guard_popen_init(self, args, *_a, **_kw):
    _guard_check_cmd(args)
    _guard_orig_popen_init(self, args, *_a, **_kw)


_guard_subprocess.Popen.__init__ = _guard_popen_init

_guard_orig_system = _guard_os.system


def _guard_os_system(_cmd):
    _guard_check_cmd(_cmd)
    return _guard_orig_system(_cmd)


_guard_os.system = _guard_os_system

_guard_orig_os_popen = _guard_os.popen


def _guard_os_popen(_cmd, *_a, **_kw):
    _guard_check_cmd(_cmd)
    return _guard_orig_os_popen(_cmd, *_a, **_kw)


_guard_os.popen = _guard_os_popen
'''


@tool
async def execute_python_code(code: str) -> str:
    """LLMが生成したPythonコードをその場で実行し、標準出力/標準エラーを返す。

    run_script が skills/*/scripts/ 配下の既存ファイルしか実行できないのに対し、
    このツールはコード文字列を一時ファイルへ書き出してその場で実行する。任意コード
    実行はリスクが高いため、サーバー設定で無効化されている場合は実行せずエラーを
    返す。書き込み系ツールのため、create_plan/approve_plan で計画が承認済みで
    ない限り実行できない（未承認の場合はエラーを返す）。

    **cwd（作業ディレクトリ）は run_script とは異なる**。run_script の cwd は
    ユーザーが設定した作業ディレクトリだが、このツールの cwd は常に
    default_workdir 配下のこのセッション専用の一時フォルダ
    （`_tmp_<thread_id>`）になる（ユーザーの作業ディレクトリが何であっても
    変わらない）。このコードが相対パスで書き出すファイル（中間生成物）は
    そちらへ溜まり、LLMがユーザーの作業ディレクトリを直接汚さないように
    している。ユーザーの作業ディレクトリを狙う必要はなく、単純に相対パス
    （例: `open("ops.json", "w", ...)`）で書けばよい。生成・更新された
    ファイルは実行後に自動検知して path_memory（`@N`）へ登録し、戻り値に
    含める（後続の run_script 等へは `@N` かこのツールが返す絶対パスを
    そのまま渡せる。run_script 側もこの一時フォルダを読み取れるため、
    cwd が違うことは問題にならない）。タイムアウトや Python 実行ファイルは
    run_script と共通の設定を流用する。

    **重要: パスメモリ(@N)の活用**
    Globや他のツールで取得したファイルパス（@0, @1, @2…）を、このツールの
    code引数で使う場合、code内で `path_memory.resolve()` を呼び出して
    実パスへ展開する必要があります。環境変数 `AGENT_SRC_DIR` で `src/`
    ディレクトリが利用可能なので、以下のようにインポート・展開できます:

      import os, sys
      sys.path.insert(0, os.environ.get("AGENT_SRC_DIR", ""))
      import path_memory
      thread_id = os.environ.get("AGENT_THREAD_ID", "_no_session")
      pm_dir = os.environ.get("AGENT_PATH_MEMORY_DIR", "")
      if pm_dir:
          resolved = path_memory.resolve(thread_id, "@0", Path(pm_dir))
          print(open(resolved).read()[:500])

    ファイル一覧をcode内にリテラルリストとして書き写す必要は絶対にない。

    **重要: ファイル数上限**
    code引数内へファイル名をリテラルとしてリスト化する場合、**30件を超えると**
    トークン爆発（会話履歴の肥大化）を引き起こす。ファイルが30件を超す場合は
    code内にリスト化せず、globやpathlibでディレクトリ探索を行うか、
    run_script で既存スクリプトを呼び出す方式を優先すること。

    **重要: 委譲の原則**
    ファイル調査・比較・集計等の処理は、可能な限り既存のスキルや
    run_script で実装されたスクリプトへ委譲すること。execute_python_codeは
    簡易なスクリプト実行やプロトタイピングに限定し、複雑なデータ処理や
    大規模なファイル操作は避ける。

    **重要: 書き込みは作業ディレクトリ配下限定（サンドボックス）**
    このコードは実行前ガードにより、書き込み・削除・改名の類が作業
    ディレクトリ・自セッション専用の一時フォルダ（`_tmp_<thread_id>`。
    このコードのcwdそのもの）以外では自動的にブロックされる
    （PermissionErrorで失敗する。Locohaneのプロジェクトフォルダ
    〔src/・app.py・config.ini・skills/ 等〕、それ以外の任意のドライブ・
    フォルダに加え、default_workdir直下の`_tmp_<thread_id>`以外の場所
    （サーバー側の共有フォルダのため他セッションから見えてしまう）も
    含めて、書き込みは一切できない）。プロジェクト自体の設定やソース
    コードを変更する必要がある場合はこのツールを使わず、ユーザーへ
    直接の編集を依頼すること。
    読み取りはこのガードの対象外で従来通り制限されない。

    Args:
        code: 実行する Python コード全文。path_memory の @N トークン
            （例: @0, @1）を含める場合、code内で `path_memory.resolve()`
            を呼び出して実パスへ展開する必要がある（上記「パスメモリの活用」
            参照）。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラー、生成/更新
        ファイルの path_memory 参照（あれば）を、それぞれ見出し付きで
        連結した文字列。code が空の場合、実行が無効化されている場合、
        計画が未承認の場合、タイムアウトした場合、起動自体に失敗した
        場合はいずれも例外を送出せず「エラー: ...」形式で返す。
    """
    if not code.strip():
        return "エラー: code が空です。"
    if not _CODE_EXEC_ENABLED:
        return "エラー: LLMが生成したPythonコードの実行はconfig.iniで無効化されています" "（[scripts] code_execution_enabled=false）。"
    workdir = _resolve_exec_workdir()

    if not cl.user_session.get("plan_approved"):
        logger.info("execute_python_code: 計画未承認のためブロック")
        return "エラー: 計画が未承認のため実行できません。" "create_plan で計画を作成し、approve_plan でユーザーの承認を得てから" "実行してください。"

    try:
        before_snapshot = {p: p.stat().st_mtime for p in workdir.iterdir() if p.is_file()}
    except OSError:
        before_snapshot = {}

    try:
        _fs_guard = _python_fs_guard_preamble(_exec_guard_roots(), tmp_dir_roots=_tmp_dir_parents(workdir.parent))
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(workdir), delete=False, encoding="utf-8")
        tmp.write(_fs_guard + code)
        tmp.close()
        tmp_path = Path(tmp.name)
    except OSError as e:
        return f"エラー: 一時ファイルを作成できませんでした: {e}"

    logger.info("execute_python_code: cwd=%s", workdir)
    try:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [_SCRIPT_PYTHON, str(tmp_path)],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT,
                encoding="utf-8",
                errors="replace",
                env=_subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return f"エラー: コードが {_SCRIPT_TIMEOUT} 秒でタイムアウトしました。"
        except OSError as e:
            return f"エラー: コードを実行できませんでした: {e}"
    finally:
        tmp_path.unlink(missing_ok=True)

    parts = [f"[終了コード] {proc.returncode}"]
    if proc.stdout:
        parts.append(f"[標準出力]\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"[標準エラー]\n{proc.stderr.rstrip()}")

    path_memory_note = _register_exec_output_files(workdir, before_snapshot, cl.user_session.get("thread_id") or "_no_session")
    if path_memory_note:
        parts.append(path_memory_note)

    warning = _track_failure_streak("execute_python_code_failure_streak", proc.returncode != 0, "execute_python_code")
    if warning:
        parts.append(warning)
    return "\n".join(parts)


@tool
async def execute_python_code_readonly(code: str) -> str:
    """LLMが生成したPythonコードをその場で実行し、標準出力/標準エラーを返す
    （読み取り専用・書き込み不可版）。

    execute_python_code と同じ方式（コード文字列を一時ファイルへ書き出し
    その場で実行）だが、書き込み・削除・改名を場所を問わず一切許可しない。
    規則から「正しい値」を計算する・既存データを読み込んで検算するといった、
    ファイルを一切書き換えない計算・検証専用に使う。成果物ファイルの生成・
    編集にはこのツールではなく execute_python_code、または対応するスキルの
    専用スクリプトを使うこと（このツールでファイルを書こうとしても
    PermissionError になり失敗する）。

    execute_python_code と異なり計画承認（Plan Mode）を必要としない
    （書き込みが一切できないため、計画未承認でも安全に実行できる）。

    Args:
        code: 実行する Python コード全文。path_memory の @N トークンを使う
           場合は execute_python_code と同じ方法（path_memory.resolve()）
            で実パスへ展開する。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラーを連結した文字列。
        code が空の場合、実行が無効化されている場合、タイムアウトした場合、
        起動自体に失敗した場合はいずれも例外を送出せず「エラー: ...」形式で
        返す。コードが書き込み・削除・改名を試みた場合は PermissionError と
        なり、その内容が標準エラーに含まれる。
    """
    if not code.strip():
        return "エラー: code が空です。"
    if not _CODE_EXEC_ENABLED:
        return "エラー: LLMが生成したPythonコードの実行はconfig.iniで無効化されています" "（[scripts] code_execution_enabled=false）。"
    workdir = _resolve_exec_workdir()

    try:
        _fs_guard = _python_fs_guard_preamble([], tmp_dir_roots=_tmp_dir_parents(workdir.parent))
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(workdir), delete=False, encoding="utf-8")
        tmp.write(_fs_guard + code)
        tmp.close()
        tmp_path = Path(tmp.name)
    except OSError as e:
        return f"エラー: 一時ファイルを作成できませんでした: {e}"

    logger.info("execute_python_code_readonly: cwd=%s", workdir)
    try:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [_SCRIPT_PYTHON, str(tmp_path)],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT,
                encoding="utf-8",
                errors="replace",
                env=_subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return f"エラー: コードが {_SCRIPT_TIMEOUT} 秒でタイムアウトしました。"
        except OSError as e:
            return f"エラー: コードを実行できませんでした: {e}"
    finally:
        tmp_path.unlink(missing_ok=True)

    parts = [f"[終了コード] {proc.returncode}"]
    if proc.stdout:
        parts.append(f"[標準出力]\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"[標準エラー]\n{proc.stderr.rstrip()}")
    return "\n".join(parts)


@tool
async def execute_python_code_background(code: str) -> str:
    """LLMが生成したPythonコードをバックグラウンドで実行する。

    処理時間が長くなることが見込まれるコード向け。execute_python_code と
    異なり、このターン自体は（下記の安全上限に達しない限り）完了まで
    ブロックされる（Chainlit UI上は「実行中」表示が続く）。待っている間、
    人間向けにチャットへ直接進捗（経過秒数・標準出力/標準エラー末尾）が
    自動で通知されるため、自分から check_script_job を繰り返し呼んで
    ポーリングする必要は無い（進捗はコード側が直接チャットへ送るため、
    LLMの呼び出し回数・トークン消費は増えない）。1回の呼び出しで
    execute_python_code と同じ形式の最終結果がそのまま返る
    （run_script_background のジョブと共通のレジストリ・ツールで扱われる）。

    設定した安全上限（[scripts].background_inline_wait_max_seconds）を
    超えてもなお完了しない場合に限り、job_id を含む案内文を返してこのターンを
    終える（ジョブ自体は裏側で動き続ける）。この場合のみ、後続ターンで
    check_script_job（結果取得）・stop_script_job（明示的な中断指示があった
    場合のみ）を使う。

    引数チェック・作業ディレクトリ解決・計画承認チェック（免除なし、常に
    create_plan/approve_plan による承認が必要）・実行可否チェックは
    execute_python_code と同じ。生成・更新されたファイルは完了時に自動検知して
    path_memory（`@N`）へ登録し、結果に含める。
    バックグラウンドジョブを強制終了するまでの上限は既定3600秒。

    **重要: パスメモリ(@N)の活用**
    Globや他のツールで取得したファイルパス（@0, @1, @2…）を、このツールの
    code引数で使う場合、code内で `path_memory.resolve()` を呼び出して
    実パスへ展開する必要があります。環境変数 `AGENT_SRC_DIR` で `src/`
    ディレクトリが利用可能なので、以下のようにインポート・展開できます:

      import os, sys
      sys.path.insert(0, os.environ.get("AGENT_SRC_DIR", ""))
      import path_memory
      thread_id = os.environ.get("AGENT_THREAD_ID", "_no_session")
      pm_dir = os.environ.get("AGENT_PATH_MEMORY_DIR", "")
      if pm_dir:
          resolved = path_memory.resolve(thread_id, "@0", Path(pm_dir))
          print(open(resolved).read()[:500])

    ファイル一覧をcode内にリテラルリストとして書き写す必要は絶対にない。

    **重要: ファイル数上限**
    code引数内へファイル名をリテラルとしてリスト化する場合、**30件を超えると**
    トークン爆発（会話履歴の肥大化）を引き起こす。ファイルが30件を超す場合は
    code内にリスト化せず、globやpathlibでディレクトリ探索を行うか、
    run_script で既存スクリプトを呼び出す方式を優先すること。

    **重要: 委譲の原則**
    ファイル調査・比較・集計等の処理は、可能な限り既存のスキルや
    run_script で実装されたスクリプトへ委譲すること。execute_python_codeは
    簡易なスクリプト実行やプロトタイピングに限定し、複雑なデータ処理や
    大規模なファイル操作は避ける。

    **重要: 書き込みは作業ディレクトリ配下限定（サンドボックス）**
    このコードは実行前ガードにより、書き込み・削除・改名の類が作業
    ディレクトリ・自セッション専用の一時フォルダ（`_tmp_<thread_id>`。
    このコードのcwdそのもの）以外では自動的にブロックされる
    （PermissionErrorで失敗する。Locohaneのプロジェクトフォルダ
    〔src/・app.py・config.ini・skills/ 等〕、それ以外の任意のドライブ・
    フォルダに加え、default_workdir直下の`_tmp_<thread_id>`以外の場所
    （サーバー側の共有フォルダのため他セッションから見えてしまう）も
    含めて、書き込みは一切できない）。プロジェクト自体の設定やソース
    コードを変更する必要がある場合はこのツールを使わず、ユーザーへ
    直接の編集を依頼すること。
    読み取りはこのガードの対象外で従来通り制限されない。

    Args:
        code: 実行する Python コード全文。

    Returns:
        通常は execute_python_code と同じ形式の最終結果文字列。安全上限に
        達した場合のみ job_id を含む案内文字列。code が空・実行が
        無効化されている・計画未承認・一時ファイル作成や起動自体に
        失敗した場合は execute_python_code 同様「エラー: ...」形式の
        文字列を返す（この場合 job は作られない）。
    """
    if not code.strip():
        return "エラー: code が空です。"
    if not _CODE_EXEC_ENABLED:
        return "エラー: LLMが生成したPythonコードの実行はconfig.iniで無効化されています" "（[scripts] code_execution_enabled=false）。"
    workdir = _resolve_exec_workdir()

    if not cl.user_session.get("plan_approved"):
        logger.info("execute_python_code_background: 計画未承認のためブロック")
        return "エラー: 計画が未承認のため実行できません。" "create_plan で計画を作成し、approve_plan でユーザーの承認を得てから" "実行してください。"

    try:
        before_snapshot = {p: p.stat().st_mtime for p in workdir.iterdir() if p.is_file()}
    except OSError:
        before_snapshot = {}

    try:
        _fs_guard = _python_fs_guard_preamble(_exec_guard_roots(), tmp_dir_roots=_tmp_dir_parents(workdir.parent))
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(workdir), delete=False, encoding="utf-8")
        tmp.write(_fs_guard + code)
        tmp.close()
        tmp_path = Path(tmp.name)
    except OSError as e:
        return f"エラー: 一時ファイルを作成できませんでした: {e}"

    _purge_stale_background_jobs()

    logger.info("execute_python_code_background: cwd=%s", workdir)
    try:
        process = await asyncio.create_subprocess_exec(
            _SCRIPT_PYTHON,
            str(tmp_path),
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subprocess_env(),
        )
    except OSError as e:
        tmp_path.unlink(missing_ok=True)
        return f"エラー: コードを起動できませんでした: {e}"

    job_id = uuid.uuid4().hex[:12]
    job = _BackgroundJob(
        process=process,
        thread_id=cl.user_session.get("thread_id") or "",
        skill_name="",
        script_filename=tmp_path.name,
        started_at=time.monotonic(),
        stdout_chunks=[],
        stderr_chunks=[],
        status="running",
        returncode=None,
        error_message=None,
        tmp_path=tmp_path,
        workdir=workdir,
        before_snapshot=before_snapshot,
    )
    job.runner_task = asyncio.create_task(_run_background_job(job, job_id))
    _BACKGROUND_JOBS[job_id] = job

    wait_timeout = _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS if _SCRIPT_BACKGROUND_INLINE_WAIT_MAX_SECONDS > 0 else None
    try:
        await asyncio.wait_for(asyncio.shield(job.runner_task), timeout=wait_timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "execute_python_code_background: 安全上限(%s秒)に達したため job_id を返してターンを終えます: job_id=%s",
            wait_timeout,
            job_id,
        )
        return _background_job_started_message(job_id)

    return _finalize_script_job_result(job, job_id)


def _append_scratch_note_hint(result: str) -> str:
    """打ち切られたサブエージェントの結果に、スクラッチノートの案内を追記する。

    write_scratch_note で途中経過が書き残されていれば、そのパスを案内する。
    委譲元はそちらを Read すれば、打ち切りにより未整理のまま返ってくる
    ツール結果の生データより、サブエージェント自身が構造化して書き残した
    内容を優先して参照できる。呼び出し時点で _SUBAGENT_RUN_ID がまだ
    現在の実行を指している必要があるため、dispatch_agent の finally で
    リセットする前に呼ぶこと。
    """
    if not is_truncated_result(result):
        return result
    path = _scratch_notes_path()
    if not path.is_file():
        return result
    return (
        f"{result}\n\n[このサブエージェントは write_scratch_note で途中経過を"
        f"書き残しています。Read で {path} を確認すると、打ち切り前に整理された"
        "内容が得られます。]"
    )


@dataclass
class _DispatchAgentJob:
    """dispatch_agent で起動したジョブの状態。

    モジュールレベルの _DISPATCH_AGENT_JOBS に job_id をキーとして保持する。
    _BackgroundJob（run_script_background 用）とは別のデータクラスにする。
    サブプロセスを持たず（process/stdout_chunks 等が不要）、代わりに
    run_subagent 専用の run_id（_SUBAGENT_RUN_ID に相当）を持つ点が異なる。
    """

    thread_id: str
    run_id: str  # このジョブ専用の _SUBAGENT_RUN_ID・_IN_SUBAGENT の値
    agent_type: str
    task_preview: str  # ログ・状況確認表示専用（task 先頭一部。全文は保持しない）
    started_at: float  # time.monotonic()
    status: str  # "running" | "completed" | "killed" | "error"
    result: str | None
    error_message: str | None
    max_iterations: int  # run_subagent に渡した反復上限（進捗表示用に保持）
    runner_task: "asyncio.Task | None" = None
    # run_subagent の on_iteration コールバックが更新する、現在の反復回数
    # （進捗push・check_dispatch_agent_job の running 分岐の表示に使う）。
    current_iteration: int = 0
    # check_dispatch_agent_job が直前に「実行中」ステータスを返した時刻
    # （time.monotonic()）。_BackgroundJob.last_polled_at と同じ理由づけ
    # （連続呼び出しの最短間隔をサーバー側で強制するため）。
    last_polled_at: float | None = None
    # dispatch_agent() がこのジョブの完了をまだ（安全上限内で）待っているとみなせる
    # 間は True。安全上限超過・停止ボタン等でジョブの完了を待たずに呼び出し元の
    # ターンが先に終わった場合、dispatch_agent() 側がジョブ完了より前に
    # （wait_forの例外捕捉時に同期的に）False へ変える。
    # _run_dispatch_agent_job の finally 節が job.runner_task の完了処理の
    # 一部として実行されるのは asyncio.wait_for が制御を戻すより必ず前になる
    # （wait_forは対象タスクの完了＝finally込みの完了を待って初めて返るため）。
    # そのため「dispatch_agent()側で完了後にTrueにする」という実装は間に合わず、
    # 逆に「初期値Trueで、フォールバック検知時にのみ即座にFalseへ落とす」という
    # 向きでなければ正しく機能しない。True のときのみ、_run_dispatch_agent_job の
    # finally節でmain_agent_tool_guard_call_countのリセットを行ってよい（結果を
    # 受け取るのがまだ同じターンだと保証できるため）。False の場合にリセットすると、
    # その間に同一セッションで始まった別の新しいターンのツールガードカウンタを
    # 横から無効化してしまう。
    turn_still_waiting: bool = True


# dispatch_agent のジョブレジストリ。_BACKGROUND_JOBS と同じ理由で
# プロセス内メモリのみで永続化はしない（アプリ再起動でジョブは失われるが、
# その内部で走っていた run_subagent 自体も再起動で失われるため実害はない）。
_DISPATCH_AGENT_JOBS: dict[str, _DispatchAgentJob] = {}

# app.py の SUBAGENT_MESSAGE_AUTHOR、frontend/src/utils/messageTree.ts の
# SUBAGENT_MESSAGE_AUTHOR と一致させる（UI側でメインエージェントの回答と
# 区別して表示するための cl.Message author 識別子）。
_SUBAGENT_MESSAGE_AUTHOR = "サブエージェント"


def _format_dispatch_agent_progress(job: "_DispatchAgentJob", job_id: str) -> str:
    """dispatch_agent ジョブの実行中の状況を表す文字列を組み立てる。

    _push_dispatch_agent_progress（人間向けのUI直接push）と
    check_dispatch_agent_job の running 分岐（フォールバック経路でのLLM向け
    応答）の両方から呼ぶ、表示フォーマット共通化のためのヘルパー。
    """
    elapsed = int(time.monotonic() - job.started_at)
    parts = [f"実行中です（経過 {elapsed} 秒・反復 {job.current_iteration}/{job.max_iterations} 回・job_id={job_id}）。"]
    note_path = _scratch_notes_path_for_run(job.run_id)
    if note_path.is_file():
        note_tail = note_path.read_text(encoding="utf-8", errors="replace")[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
        if note_tail:
            parts.append(f"[進捗メモ（末尾）]\n{note_tail}")
    return "\n".join(parts)


async def _push_dispatch_agent_progress(job: "_DispatchAgentJob", job_id: str) -> None:
    """dispatch_agent の実行中、人間向けに進捗をチャットへ直接pushする。

    cl.Message送信のみでLLM呼び出しを一切伴わないため、トークンを消費しない。
    「LLM自身がcheck_dispatch_agent_jobを繰り返し呼ぶとその都度LLM推論コストが
    かかる」というフィードバックを受け、dispatch_agent 自体は
    ジョブ完了までLLMに戻らずブロックする設計にしている（_run_dispatch_agent_job
    参照）。その間も人間が「動いているか」を確認できるよう、この関数が
    代わりに進捗を伝える。

    contextvarベースのChainlitセッションコンテキストは asyncio.create_task
    生成時点でコピーされる。このタスクは dispatch_agent の
    ツール呼び出しがまだ生きている（＝元のセッションコンテキストの中にいる）
    間に生成されるため、cl.Message(...).send() が正しいセッションへ届く。

    _run_dispatch_agent_job の finally で確実にキャンセルされる想定
    （ループ条件（job.status=="running"）だけに頼ると、ジョブ完了後も
    次の asyncio.sleep が明けるまでタスクが残留してしまうため）。

    metadata={"ephemeral_progress": True} を付ける（_push_background_job_progress
    と同じ）。ChatThreadDataLayer.create_step（src/thread_store.py）がこの
    フラグを見て永続化をスキップするため、スレッド再開時に「経過N秒・
    反復N/N回・job_id=xxx」という実行時点でしか意味を持たない古い進捗表示が
    復元されない（2026-08-21 ユーザー報告）。ライブ表示（emitter.send_step）
    には影響しない。サブエージェントの実際の発言（write_scratch_note等が
    生む本来のステップ）にはこのフラグを付けないため、それらは通常通り
    履歴に残る。
    """
    while job.status == "running":
        await asyncio.sleep(_DISPATCH_AGENT_BACKGROUND_PROGRESS_PUSH_INTERVAL_SECONDS)
        if job.status != "running":
            break
        await cl.Message(
            content=_format_dispatch_agent_progress(job, job_id),
            author=_SUBAGENT_MESSAGE_AUTHOR,
            metadata={"ephemeral_progress": True},
        ).send()


async def _run_dispatch_agent_job(job: "_DispatchAgentJob", job_id: str, task: str, resolved: "ResolvedAgentType") -> None:
    """dispatch_agent のランナータスク本体。

    asyncio.create_task() はタスク生成時点のコンテキストをコピーし、以後
    タスク内部の contextvar.set()/reset() は呼び出し元（dispatch_agent）へは
    伝播しない。そのため _IN_SUBAGENT / _SUBAGENT_RUN_ID の設定・セマフォ確保・
    run_subagent() 呼び出し・_append_scratch_note_hint・
    main_agent_tool_guard_call_count のリセットは、このタスクの中で行う必要がある
    （呼び出し元側で行っても、値が呼び出し元のコンテキストにしか残らず
    このタスクの中からは見えない）。

    dispatch_agent は起動後、このタスクの完了を（安全上限まで）同じツール
    呼び出し内で待ち続ける設計だが、それでも run_subagent 自体はこのタスク
    として独立させておく。理由: (1) 安全上限を超えた場合に dispatch_agent
    側だけ制御をLLMへ返しつつ、このタスクは裏側で動き続けさせる必要がある
    （asyncio.shield で保護）。(2) 進捗push タスク（_push_dispatch_agent_progress）
    と並行させる必要がある。

    例外はここで確定的に捕捉し job.status="error" へ変換する。fire-and-forget
    の asyncio.Task 内で未捕捉の例外は「Task exception was never retrieved」
    という警告だけを残して静かに失われ、回収経路が無くなるため
    （_run_background_job と同じ理由）。asyncio.CancelledError
    （stop_dispatch_agent_job による task.cancel()）はここで握りつぶさず
    そのまま伝播させる。stop_dispatch_agent_job が先に status="killed" を
    設定済みのため、下記の各分岐は「killed を上書きしない」ガードを持つ
    （_run_background_job の status!="killed" ガードと同じレース対策）。
    """
    token = _IN_SUBAGENT.set(True)
    run_id_token = _SUBAGENT_RUN_ID.set(job.run_id)
    agent_type_token = _SUBAGENT_AGENT_TYPE.set(job.agent_type)
    progress_task = asyncio.create_task(_push_dispatch_agent_progress(job, job_id))

    def _on_iteration(iteration: int, max_iterations: int) -> None:
        job.current_iteration = iteration

    try:
        sem = _get_session_semaphore(_DISPATCH_AGENT_SEMAPHORES, _DISPATCH_AGENT_MAX_PARALLEL)
        if sem is not None:
            async with sem:
                result = await run_subagent(
                    task,
                    resolved.tools,
                    resolved.system_prompt,
                    _LLM_CONFIG,
                    job.max_iterations,
                    on_iteration=_on_iteration,
                    llm_timeout_max_retries=_DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES,
                )
        else:
            result = await run_subagent(
                task,
                resolved.tools,
                resolved.system_prompt,
                _LLM_CONFIG,
                job.max_iterations,
                on_iteration=_on_iteration,
                llm_timeout_max_retries=_DISPATCH_AGENT_BACKGROUND_LLM_TIMEOUT_MAX_RETRIES,
            )
        result = _append_scratch_note_hint(result)
        if job.status != "killed":
            job.result = result
            job.status = "completed"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - fire-and-forgetタスクの例外を消さず確定的に捕捉する
        logger.exception("dispatch_agent 失敗 (run_id=%s)", job.run_id)
        if job.status != "killed":
            job.status = "error"
            # str(e) が空文字列になる例外がある（例: asyncio.TimeoutError()）。
            # 「エラー: サブエージェントの実行に失敗しました: 」のように原因が
            # 一切分からない文言になっていた実例が本番ログで確認された
            # （issue/20260808_022438_dispatch_agent_background_failure.md）。
            # 空の場合は例外の型名で補い、必ず何らかの手がかりを残す。
            job.error_message = f"{str(e) or type(e).__name__}\n{traceback.format_exc()}"
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        _IN_SUBAGENT.reset(token)
        _SUBAGENT_RUN_ID.reset(run_id_token)
        _SUBAGENT_AGENT_TYPE.reset(agent_type_token)
        # 委譲が完了するたびに main_agent_tool_guard のカウンタをリセットする。
        # 1ターン内で複数回「調査→delegate」を繰り返す正当なケースを妨げないため。
        # ただし、安全上限フォールバックや停止ボタン等で呼び出し元のターンが
        # 既に終わっている場合（turn_still_waiting=False）はリセットしない。
        # ターンが終わっている＝同一セッションで別の新しいターンが既に進行中の
        # 可能性があり、そちらのガードカウンタを横から無効化してしまうため。
        if job.turn_still_waiting:
            cl.user_session.set("main_agent_tool_guard_call_count", None)


def _finalize_dispatch_agent_job_result(job: "_DispatchAgentJob", job_id: str) -> str:
    """終端状態（completed/killed/error）のジョブを最終結果文字列へ整形し、レジストリから取り除く。

    dispatch_agent（安全上限内に完了した場合）と
    check_dispatch_agent_job（終端状態を取得した場合）の両方から呼ぶ、
    ワンショット取得（一度取得したら同じ job_id は再利用できない）の共通処理。
    """
    if job.status == "completed":
        result = job.result or ""
        if job.agent_type == "planner":
            # create_plan の直前チェック（_PLAN_REQUIRE_PLANNER_DISPATCH）が
            # 消費するフラグ。ここで完了を記録しておくことで、dispatch_agent
            # が安全上限内に即応した場合・check_dispatch_agent_job経由で
            # 後続ターンに完了を取得した場合の両方をカバーする。
            cl.user_session.set("planner_dispatched_since_plan", True)
    elif job.status == "killed":
        result = f"stop_dispatch_agent_job により強制終了されました。\n{job.result or '（強制終了時点で最終回答は未生成でした）'}"
    else:  # "error"
        result = f"エラー: サブエージェントの実行に失敗しました: {job.error_message}"
    _DISPATCH_AGENT_JOBS.pop(job_id, None)
    return result


def _purge_stale_dispatch_agent_jobs() -> None:
    """完了済みのまま check_dispatch_agent_job で回収されなかったジョブを掃除する。

    専用のクリーンアップループは持たず、dispatch_agent の呼び出しの
    度に opportunistic に走らせる（_purge_stale_background_jobs と同じ方針）。
    """
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _DISPATCH_AGENT_JOBS.items()
        if job.status != "running" and now - job.started_at > _DISPATCH_AGENT_BACKGROUND_JOB_RETENTION_SECONDS
    ]
    for job_id in stale:
        _DISPATCH_AGENT_JOBS.pop(job_id, None)


def _dispatch_agent_job_started_message(job_id: str) -> str:
    """dispatch_agent が安全上限（background_inline_wait_max_seconds）に
    達してもなおジョブが終わっていない場合に、フォールバックとしてLLMへ返す案内文。

    通常はこの文言に到達しない（dispatch_agent 自身がジョブ完了まで
    ツール呼び出し内で待ち続け、最終結果を直接返すため）。到達するのは、
    設定された安全上限を超えるほど長時間のジョブだけである。
    _background_job_started_message と同じ理由づけ（処理時間の長さ自体は
    打ち切る理由にならないことを明記し、モデルの自発的な早期キャンセルを防ぐ）。
    """
    return (
        f"バックグラウンドで実行中です（job_id={job_id}）。長時間かかっているため、"
        "いったんこのターンを終えて制御を返します（ジョブ自体は裏側で動き続けます）。\n"
        "完了確認・結果取得には check_dispatch_agent_job（job_id指定）を使うこと。"
        "処理に時間がかかっていること自体は打ち切る理由にはならない。"
        "ユーザーから明示的に中断・キャンセルを指示された場合にのみ"
        "stop_dispatch_agent_job（job_id指定）を使うこと。"
    )


def _resolve_dispatch_agent_job(job_id: str) -> "_DispatchAgentJob | str":
    """job_id を現在のセッション所有のジョブへ解決する（他セッションは拒否）。

    _resolve_job と同じ方針。
    """
    job = _DISPATCH_AGENT_JOBS.get(job_id)
    if job is None:
        return f"エラー: job_id '{job_id}' は見つかりません（既に取得済みか、無効なIDです）。"
    thread_id = cl.user_session.get("thread_id") or ""
    if job.thread_id != thread_id:
        return f"エラー: job_id '{job_id}' は現在のセッションのものではありません。"
    return job


@tool
async def dispatch_agent(task: str, agent_type: str) -> str:
    """タスクを独立したサブエージェントへ委譲し、最終回答のみを受け取る。

    調査や複数ステップの下調べなど、詳細な思考過程やツール呼び出しの
    経緯までは自分の会話履歴に残す必要が無い作業に使う。サブエージェントは
    agent_type で選んだ種別のツールセットを使って自律的に作業するが、
    その内部の思考過程・ツール呼び出しはあなたの会話履歴には一切残らず、最終回答の
    テキストのみが返る（ログファイルには内部の記録が残る）。run_script が
    呼ばれた場合、その承認確認はサブエージェントの実行中にそのまま
    ユーザーへ表示される。数十〜数百件規模のファイル（画像等）を扱う調査は、
    年・サブフォルダ等の単位でこのツールへ分割委任すると効率的。
    サブエージェントはさらに別のサブエージェントへ委譲することはできない。

    このターン自体は（下記の安全上限に達しない限り）完了までブロックされる
    （Chainlit UI上は「実行中」表示が続く）。待っている間、人間向けに
    チャットへ直接進捗（経過時間・反復回数・進捗メモ）が自動で通知されるため、
    自分から何度も呼び直してポーリングする必要は無い（進捗はコード側が
    直接チャットへ送るため、LLMの呼び出し回数・トークン消費は増えない）。
    1回の呼び出しでサブエージェントの最終回答がそのまま返る。

    設定した安全上限（[subagent].background_inline_wait_max_seconds）を超えても
    なお完了しない場合に限り、job_id を含む案内文を返してこのターンを終える
    （ジョブ自体は裏側で動き続ける）。この場合のみ、後続ターンで
    check_dispatch_agent_job（結果取得）・stop_dispatch_agent_job（明示的な
    中断指示があった場合のみ）を使う。

    Args:
        task: サブエージェントに依頼したいタスクの説明。必要な背景情報・
            期待する出力形式を過不足なく書くこと（サブエージェントは
            この会話の文脈を一切知らない）。対象パスは記憶から書き起こさず、
            直前の glob_file.py 等で得た `@N`（パスメモリー参照）をそのまま
            文中に埋め込んでよい（解決できるものは実パスへ自動置換される）。
        agent_type: 使用するサブエージェントの種別名（必須、暗黙の既定値は
            無い）。利用可能な種別とそれぞれの用途はシステムプロンプトの
            一覧を参照し、タスクの内容に合った種別を毎回明示的に選ぶこと。

    Returns:
        通常はサブエージェントの最終回答テキスト。安全上限に達した場合のみ
        job_id を含む案内文字列。init_tools() が未実行の場合、agent_type が
        不明な場合は起動前に「エラー: ...」を返す（この場合 job は作られない）。
        サブエージェントの実行自体に失敗した場合も、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    if _LLM_CONFIG is None:
        return "エラー: init_tools() が未実行です"
    resolved = _AGENT_TYPES.get(agent_type)
    if resolved is None:
        available = ", ".join(sorted(_AGENT_TYPES)) or "（登録なし）"
        return f"エラー: 不明な agent_type '{agent_type}' です。利用可能: {available}"
    task = _resolve_path_memory_tokens_in_text(task)
    task = _task_with_work_dir_hint(task)
    task = _task_with_plan_hint(task)
    logger.info("dispatch_agent: task=%r agent_type=%r", task, agent_type)

    _purge_stale_dispatch_agent_jobs()

    job = _DispatchAgentJob(
        thread_id=cl.user_session.get("thread_id") or "",
        run_id=uuid.uuid4().hex,
        agent_type=agent_type,
        task_preview=task[:200],
        started_at=time.monotonic(),
        status="running",
        result=None,
        error_message=None,
        max_iterations=_SUBAGENT_MAX_ITERATIONS,
    )
    job_id = uuid.uuid4().hex[:12]
    job.runner_task = asyncio.create_task(_run_dispatch_agent_job(job, job_id, task, resolved))
    _DISPATCH_AGENT_JOBS[job_id] = job

    # asyncio.shield: このwait_forがタイムアウトして例外を送出しても、
    # job.runner_task 自体はキャンセルされず裏側で動き続ける
    # （安全上限超過時のフォールバック挙動の要）。0以下は無期限待ち。
    wait_timeout = _DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS if _DISPATCH_AGENT_BACKGROUND_INLINE_WAIT_MAX_SECONDS > 0 else None
    try:
        await asyncio.wait_for(asyncio.shield(job.runner_task), timeout=wait_timeout)
    except asyncio.TimeoutError:
        # job.runner_task はこの時点でまだ完了していない（完了済みならTimeoutErrorは
        # 発生しない）ため、ジョブ側のfinally節が実行されるより確実に前にこの
        # 代入が間に合う。
        job.turn_still_waiting = False
        logger.warning(
            "dispatch_agent: 安全上限(%s秒)に達したため job_id を返してターンを終えます: job_id=%s",
            wait_timeout,
            job_id,
        )
        return _dispatch_agent_job_started_message(job_id)
    except asyncio.CancelledError:
        # 停止ボタン等でこのターン自体がキャンセルされた場合。job.runner_task は
        # shieldにより生き続けるため、TimeoutError時と同様にリセットを禁止する。
        job.turn_still_waiting = False
        raise

    return _finalize_dispatch_agent_job_result(job, job_id)


@tool
async def check_dispatch_agent_job(job_id: str) -> str:
    """dispatch_agent で起動したジョブの状況・結果を確認する。

    通常は dispatch_agent 自身がジョブ完了まで待ってから最終回答を
    直接返すため、このツールを呼ぶ必要は無い。呼ぶのは、dispatch_agent
    が安全上限に達して job_id を返した場合（＝よほど長時間のジョブ）のみ。

    実行中であれば経過秒数・反復回数と、対象サブエージェントが write_scratch_note で
    書き残した進捗メモ（あれば、末尾最大4000文字）を返す。完了・強制終了・
    エラーのいずれかで終わっていれば最終回答（dispatch_agent と同じ形式の
    テキスト）を返し、以降は同じ job_id を指定できなくなる（登録から削除
    される）。他セッションが起動した job_id は参照できない。

    サブエージェントの内部進捗を取得する手段はスクラッチノート以外に無い。
    agent_type によっては（write_scratch_note を持たない種別、または一度も
    書いていない場合）実行中の進捗欄が空のままとなるが、これは異常ではない。

    実行中が返ってきた場合、数秒間隔で連続して呼び直さないこと。経過を
    ユーザーへ一言伝えたらそのターンを終えて次のユーザー発言を待つか、
    十分な間隔（数十秒〜）を空けてから改めて呼ぶこと。処理に時間が
    かかっていること自体は異常でも打ち切る理由でもない。この指示に反して
    短い間隔で呼び直した場合はサーバー側で拒否され、「まだ確認間隔が
    短すぎます」のような拒否メッセージが返る。

    Args:
        job_id: dispatch_agent の戻り値に含まれるID。

    Returns:
        状況または最終結果を表す文字列。job_id が不明・他セッションのもので
        ある場合、または直前の「実行中」応答から
        [subagent].background_min_poll_interval_seconds 未満しか経っていない
        場合は「エラー: ...」/拒否メッセージ形式の文字列。
    """
    resolved = _resolve_dispatch_agent_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status == "running":
        now = time.monotonic()
        if (
            _DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS > 0
            and job.last_polled_at is not None
            and (now - job.last_polled_at) < _DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS
        ):
            wait_remaining = int(_DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS - (now - job.last_polled_at)) + 1
            return _DISPATCH_AGENT_BACKGROUND_MIN_POLL_MESSAGE.format(
                wait_remaining=wait_remaining,
                job_id=job_id,
                min_interval=_DISPATCH_AGENT_BACKGROUND_MIN_POLL_INTERVAL_SECONDS,
            )
        job.last_polled_at = now
        return _format_dispatch_agent_progress(job, job_id)

    return _finalize_dispatch_agent_job_result(job, job_id)


@tool
async def stop_dispatch_agent_job(job_id: str) -> str:
    """dispatch_agent で起動したジョブを強制終了する。

    ユーザーから明示的に中断・キャンセル・停止を指示された場合にのみ使う
    こと。処理に時間がかかっていること自体（check_dispatch_agent_job が
    実行中を返し続けること）は、自分の判断で打ち切ってよい理由にはならない。
    他セッションが起動した job_id は操作できない。

    Args:
        job_id: dispatch_agent の戻り値に含まれるID。

    Returns:
        強制終了結果を表す文字列。job_id が不明・他セッションのものである、
        または既に終了済みの場合は「エラー: ...」形式の文字列。
    """
    resolved = _resolve_dispatch_agent_job(job_id)
    if isinstance(resolved, str):
        return resolved
    job = resolved

    if job.status != "running":
        return f"エラー: job_id '{job_id}' は既に終了しています（status={job.status}）。" "check_dispatch_agent_job で結果を取得してください。"

    job.status = "killed"
    logger.warning(
        "dispatch_agent: 停止指示により強制終了します (run_id=%s, job_id=%s, iter=%d/%d)",
        job.run_id,
        job_id,
        job.current_iteration,
        job.max_iterations,
    )
    if job.runner_task is not None:
        job.runner_task.cancel()
        try:
            await job.runner_task
        except asyncio.CancelledError:
            pass

    note_path = _scratch_notes_path_for_run(job.run_id)
    tail = ""
    if note_path.is_file():
        tail = note_path.read_text(encoding="utf-8", errors="replace")[-_JOB_OUTPUT_TAIL_CHARS:].rstrip()
    _DISPATCH_AGENT_JOBS.pop(job_id, None)
    if tail:
        return f"強制終了しました。サブエージェントの実行を中断しました。\n[強制終了時点の進捗メモ（末尾）]\n{tail}"
    return "強制終了しました。サブエージェントの実行を中断しました。"


_VALID_TASK_STATUSES = ("pending", "in_progress", "completed")

_STATUS_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}


def _render_plan(plan: list[dict], *, finished: bool = False) -> str:
    """計画（ステップ一覧）をチェックリスト形式の Markdown へ整形する。

    create_plan / update_task_progress の両方から呼ばれる表示専用の純関数。

    Args:
        plan: {"content": str, "activeForm": str,
            "status": "pending"|"in_progress"|"completed"} の辞書のリスト。
        finished: 全ステップ完了時に完了メッセージを付記するかどうか。

    Returns:
        「### 実行計画」見出しに続けて各ステップをチェックリストで並べた Markdown 文字列。
        "in_progress" のステップは content の代わりに activeForm を表示する。
    """
    lines = ["### 実行計画"]
    for step in plan:
        marker = _STATUS_MARKERS[step["status"]]
        text = step["activeForm"] if step["status"] == "in_progress" else step["content"]
        lines.append(f"- {marker} {text}")
    if finished:
        lines.append("\n✅ 計画完了")
    return "\n".join(lines)


# frontend/src/utils/messageTree.ts の PLAN_PREFIX と一致させる
# （サイドパネルの実行計画カード用マーカー。TOKEN_USAGE_PREFIX/WORK_DIR_PREFIX と同じ方式）。
PLAN_PREFIX = "📋 実行計画\n"


def _render_plan_payload(plan: list[dict], *, finished: bool = False, approved: bool = False) -> str:
    """サイドパネル表示用に plan を JSON 化し、プレフィックス付き文字列として返す。

    フロントエンドはこのプレフィックスでメインチャット窓への表示を除外し、
    専用の PlanCard コンポーネント、および送信ボタン付近の PlanModeBadge が
    JSON 部分をパースして描画する（approved は PlanModeBadge の Plan Mode /
    Edit Automatically 表示に使う）。
    """
    payload = {"steps": plan, "finished": finished, "approved": approved}
    return PLAN_PREFIX + json.dumps(payload, ensure_ascii=False)


def _plan_detail_path() -> Path | None:
    """create_plan が detail_markdown を書き出すファイルの絶対パスを決める。

    ファイル名は thread_id から機械的に決め打ちするため、呼び出し側（LLM）が
    任意パスへ書き込むことはできない。_PLANS_DIR 未設定（init_tools() 未実行
    や evals 等の簡易経路）なら None を返し、保存をスキップする。
    """
    if _PLANS_DIR is None:
        return None
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    return _PLANS_DIR / f"plan_{thread_id}.md"


def _write_plan_detail(plan: list[dict], detail_markdown: str) -> Path | None:
    """steps のスナップショットと detail_markdown を1ファイルへまとめて上書き保存する。

    create_plan が呼ばれるたびに全文を上書きする（update_task_progress では
    更新しないため、常に「直近の create_plan 呼び出し時点」のスナップショット
    になる）。
    """
    path = _plan_detail_path()
    if path is None:
        return None
    lines = [
        "# 実行計画",
        "",
        f"- スレッドID: {cl.user_session.get('thread_id') or '_no_session'}",
        f"- 作成日時: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- ステップ数: {len(plan)}",
        "",
        "## ステップ一覧",
        "",
        *[f"{i + 1}. {s['content']}" for i, s in enumerate(plan)],
        "",
        "## 詳細",
        "",
        detail_markdown.rstrip(),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@tool
async def create_plan(steps: list[dict[str, str]], detail_markdown: str | None = None) -> str:
    """複数ステップの実行計画を作成し、ユーザーへチェックリストとして表示する。

    書き込み系ツール（run_script、run_script_background、execute_python_code、
    execute_python_code_background）を1回でも使うタスクに着手する前に、まず
    このツールでステップ一覧を提示する。作成しただけでは書き込み系ツールの
    ブロックは解除されない。承認を得るには続けて approve_plan を呼ぶこと。

    設定（既定）により、このツールを呼ぶ前に同一ターンで
    dispatch_agent(agent_type="planner") が完了している必要がある
    （未実施の場合はエラーを返しブロックする）。調査で得た具体的事実と
    ユーザー要求をplannerへ丸投げし、その草案を確認・調整してから steps/
    detail_markdown を確定させること。自分の記憶・推測だけでsteps文言
    （行番号・セル位置等の具体的数値を含む）を作らない。

    既に approve_plan で承認済み（Edit Automatically）の状態でこのツールを
    再度呼んで steps を差し替えると、config.ini の
    [plan].reset_approval_on_recreate が既定の true の場合、承認状態は
    無条件で失われ Plan Mode（未承認）へ戻る。この場合、続けて approve_plan
    を呼び直さない限り書き込み系ツールは再びブロックされる（false に設定
    されている環境では承認状態が維持され、approve_plan の呼び直しは不要）。
    戻り値のテキストで「作成しました（要approve_plan）」か「更新しました
    （承認維持）」かを都度確認できる。

    run_script_background/execute_python_code_background でバックグラウンド
    ジョブを扱う場合、「起動」と「完了確認」を同じステップにまとめないこと。
    ジョブを起動しただけではまだ処理は終わっていないため、起動ステップを
    completed にしてよいのは check_script_job で最終結果（running 以外の
    状態）を取得できてから。起動ステップとは別に「結果を確認する」ステップを
    設けること。

    detail_markdown を渡すと、steps とは別にメインチャットへそのまま
    表示され（サイドパネルのチェックリストとは別に、通常のメッセージとして
    出力される）、かつ data/plans/ 配下へ詳細な計画Markdownファイルとしても
    保存される（会話スレッドごとに1ファイル、このツールを呼ぶたびに
    上書き更新される。update_task_progress では更新されない）。
    背景・設計判断・調査結果・代替案の検討など、パネル表示のチェックリスト
    には収まらない情報をユーザーに見せたい複雑なタスクでは、この引数も
    渡すことを推奨する（省略しても create_plan 自体はエラーにならない）。

    Args:
        steps: 実行計画の各ステップを表す辞書のリスト（1件以上、実行順）。
            各辞書は次の2キーを持つこと。
            - content: ステップの内容（例: "設定ファイルを読み込む"）。
            - activeForm: 実行中（in_progress）の間だけチェックリストに
              表示する現在進行形の説明（例: "設定ファイルを読み込み中"）。
        detail_markdown: 詳細な実行計画を記した任意のMarkdown本文
            （背景・設計判断・調査結果等、自由形式）。省略時（None または
            空文字）はファイルを保存しない。

    Returns:
        計画を作成した旨とステップ件数を伝えるテキスト。detail_markdown を
        渡した場合は保存先の絶対パスも併記する。steps が空、または
        いずれかの要素に content / activeForm が欠けている場合は、例外を
        送出せず「エラー: ...」形式の文字列を返す。
    """
    if not steps:
        return "エラー: steps が空です。1件以上のステップを指定してください。"
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("content") or not s.get("activeForm"):
            return f"エラー: steps[{i}] には content と activeForm の両方を" f"文字列で指定してください: {s!r}"
    if _PLAN_REQUIRE_PLANNER_DISPATCH and not cl.user_session.get("planner_dispatched_since_plan"):
        return (
            "エラー: create_planの前にdispatch_agent(agent_type=\"planner\")を"
            "呼んでください。調査で得た具体的事実とユーザー要求をplannerへ"
            "過不足なく伝え、計画の草案を作らせてからcreate_planを呼び直すこと"
            "（自分の記憶・推測だけでsteps/detail_markdownを構成しない）。"
        )
    cl.user_session.set("planner_dispatched_since_plan", False)
    plan = [{"content": s["content"], "activeForm": s["activeForm"], "status": "pending"} for s in steps]
    # 既に承認済み（Edit Automatically）だった場合にこの呼び出しでも承認状態を
    # 維持するかは config.ini の [plan].reset_approval_on_recreate で切り替え可能
    # （既定 True＝従来通り常にリセットして Plan Mode へ戻す）。未承認状態からの
    # 呼び出しは元々 False なので、この設定に関わらず結果は変わらない。
    still_approved = bool(cl.user_session.get("plan_approved")) and not _PLAN_RESET_APPROVAL_ON_RECREATE
    cl.user_session.set("plan", plan)
    cl.user_session.set("plan_approved", still_approved)
    cl.user_session.set("awaiting_approve_plan_call", not still_approved)
    message = cl.Message(content=_render_plan_payload(plan, approved=still_approved))
    await message.send()
    cl.user_session.set("plan_message", message)
    logger.info(
        "create_plan: %d steps, detail_markdown=%d chars, still_approved=%s",
        len(steps),
        len(detail_markdown or ""),
        still_approved,
    )

    if still_approved:
        result = f"計画を更新しました（全{len(steps)}件）。承認済み状態を維持しているため、approve_plan を呼ばずに書き込み系ツールを続行できます。"
    else:
        result = f"計画を作成しました（全{len(steps)}件）。approve_plan でユーザーの承認を得てください。"
    if detail_markdown and detail_markdown.strip():
        # プレフィックス無しの通常メッセージとして送る（PLAN_PREFIX 付きの
        # チェックリストと違い、messageTree.ts の selectMainThread でサイドパネル
        # 側へ除外されず、メインチャットにそのまま表示される）。
        await cl.Message(content=detail_markdown.rstrip()).send()
        try:
            plan_path = _write_plan_detail(plan, detail_markdown)
        except OSError as e:
            result += f"\n（詳細計画の保存に失敗しました: {e}）"
        else:
            if plan_path is not None:
                result += f"\n詳細計画を保存しました: {plan_path}"
    return result


async def _ask_with_cross_session_relay(thread_id: str, factory, timeout: int):
    """Ask*Message(...).send() を、スレッド切り替え（フルページリロード）後に
    戻ってきた別セッションからでも回答できるようにして呼び出す。

    左サイドバーでの会話切り替え（frontend/src/components/Sidebar.tsx の
    goToThread）はフルページリロードのため、生成中スレッドから離れて
    ask系ツール（approve_plan/ask_user_question/ask_user_choice）の応答待ちの
    まま戻ってくると、Chainlit的には元セッションとは別物の新セッションになる
    （sessionIdState が毎回新規発行されるため）。素の
    cl.AskActionMessage/AskUserMessage/AskElementMessage.send() だけだと、
    その回答は誰にも届かなくなりボタン・入力フォームも二度と表示されない
    （2026-08-21 ユーザー報告。当初 approve_plan のみで発覚）。

    引き継ぎ先は2種類あり、どちらも同じ pending_asks[thread_id]["future"] を
    解決するだけなので下の asyncio.wait(FIRST_COMPLETED) 側は区別しない:
    (1) 後から戻ってくるセッション — app.py の on_chat_resume が検知して
        今まさに繋がっているセッション側で出し直す（_relay_pending_ask 参照）。
    (2) 既に接続済みのまま留まっているセッション — 下の
        dispatch_to_live_viewers 呼び出しが、登録した直後に該当スレッドを
        見ている全セッションへ即座に出し直す（(1)だけだと「離脱→復帰後、
        画面を見たまま待っている間に別のask系ツールが呼ばれた」場合に
        一切表示されない。2026-08-22 ユーザー実機テストで発覚）。
    元セッション（このコルーチンが実行されているセッション）側の factory()
    自体はブラウザがまだそこに残っていればそのまま機能するので、これらを
    asyncio.wait(FIRST_COMPLETED) で競わせる。ただし元セッション側が
    「本物の回答」で終わった場合のみ即採用し、「無応答タイムアウト
    （None）」で終わった場合は relay_future 側にもう一度 timeout 秒だけ
    チャンスを与える。元セッション側は切断済み（=絶対に誰も応答できない）
    ソケット宛のことが多く、そのtimeoutは「ユーザーが実際に無視した」を
    意味しないため（離脱→戻るまで時間がかかると、死んだソケット側の
    timeoutだけが先に発火し、後から戻ってきたセッションでの正しい回答が
    握りつぶされる不具合があった。2026-08-22 実機テストで発覚。詳細は
    下記実装のコメント参照）。

    Args:
        thread_id: 応答待ちを登録するスレッドID。
        factory: 引数無しの async callable。呼ぶたびに同じ内容の
            Ask*Message(...).send() を新規に実行して結果を返す（呼び出し元の
            セッション文脈で毎回新規に構築されるよう、cl.CustomElement 等を
            使う場合は factory の中で生成すること。中継時は別セッション文脈
            から呼ばれるため、事前に1個だけ作ったオブジェクトを使い回すと
            正しく動作しない）。
        timeout: 応答待ちのタイムアウト秒数（登録のみに使い、実際の
            タイムアウト処理自体は factory が呼ぶ Ask*Message 自身に任せる）。

    登録・解除の状態は app.py 側ではなく src/ask_relay.py（app.py/このモジュール
    どちらにも属さない中立モジュール）に置いてある。以前は app.py 側の状態を
    `from app import ...` で遅延importして参照していたが、chainlit run は
    app.pyを "app" という名前では sys.modules に登録しないため、その import が
    app.py全体を未初期化の別モジュールとして再実行し、稼働中の
    @cl.on_message 等のハンドラを壊してしまう不具合があった
    （src/ask_relay.py のdocstring参照）。
    """
    relay_future = register_pending_ask(thread_id, factory, timeout)
    local_ask_task = asyncio.create_task(factory())
    # on_chat_resume経由の引き継ぎは「後から戻ってくるセッション」にしか
    # 発火しないため、「既に接続済みのまま留まっているセッション」向けに
    # 今すぐ中継する（src/ask_relay.py の dispatch_to_live_viewers docstring
    # 参照。2026-08-22 ユーザー実機テストで発覚: 離脱→復帰後に画面を見たまま
    # 待っている間にask系ツールが呼ばれても入力ボックスが一切出なかった）。
    dispatch_to_live_viewers(thread_id)
    try:
        done, _pending = await asyncio.wait({local_ask_task, relay_future}, return_when=asyncio.FIRST_COMPLETED)
        if relay_future in done:
            if not local_ask_task.done():
                local_ask_task.cancel()
                try:
                    await local_ask_task
                except BaseException:  # noqa: BLE001 - 取消済みタスクの後片付けのみ、例外は握りつぶしてよい
                    pass
            return relay_future.result()
        local_result = local_ask_task.result()
        if local_result is not None:
            # local_ask_task が「本物の回答」で終わった（元セッションのブラウザが
            # まだ生きていてそちらで直接回答された）。relay_future はもう不要。
            relay_future.cancel()
            return local_result
        # local_ask_task はタイムアウト（None）で終わったが、relay_future は
        # まだ未解決。死んだソケット（切断済みの元セッション）宛の送信は
        # 相手が絶対に応答できないため、この timeout は「ユーザーが実際に
        # 無視した」ことを意味しない。離脱→復帰まで時間がかかった場合、
        # 死んだソケット側のtimeoutだけが先に発火して、後から戻ってきた
        # セッションでの正しい回答（relay_future）が握りつぶされてしまう
        # （2026-08-22 実機テストで発覚: switch away → ask発火まで長く待つ →
        # switch back → 質問フォームは正しく表示され回答したのに、LLMには
        # 「応答なし（タイムアウト）」が返っていた）。relay側にも同じ
        # timeout秒数だけ改めてチャンスを与える（resolve_pending_ask が
        # 呼ぶ Ask*Message 自体も同じ timeout を持つため、それ以上長く
        # 待っても新たな回答は来ない）。
        try:
            return await asyncio.wait_for(relay_future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
    finally:
        clear_pending_ask(thread_id, relay_future)


@tool
async def approve_plan() -> str:
    """作成済みの実行計画についてユーザーの承認を得る。

    計画内容を提示し、承認/拒否を選ばせる。承認されると、以後
    run_script/run_script_background/execute_python_code/
    execute_python_code_background のハードブロックが解除され実行できるように
    なる。タイムアウト（未応答）は安全側に倒して未承認扱いにするが、ユーザーが
    明示的に却下した場合とは返り値のテキストで区別する（無応答は単に手が
    離せないだけの可能性が高く、計画自体を作り直す必要はないため）。

    config.ini の [plan].auto_approve が true の場合、承認/却下ボタンの表示・
    応答待ちを一切行わず、その場で自動的に承認済み扱いにする（無人自動化用途向け）。

    Returns:
        承認・明示的却下・タイムアウトのいずれかを伝えるテキスト。計画が未作成の
        場合は例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    plan = cl.user_session.get("plan")
    if not plan:
        return "エラー: 計画がありません。先に create_plan を呼んでください。"
    if _PLAN_AUTO_APPROVE:
        cl.user_session.set("plan_approved", True)
        cl.user_session.set("plan_denied_just_now", False)
        message: cl.Message | None = cl.user_session.get("plan_message")
        if message is not None:
            message.content = _render_plan_payload(plan, approved=True)
            await message.update()
        await cl.Message(content="⚙️ config.ini の [plan].auto_approve が有効なため、ユーザー確認をスキップして計画を自動承認しました。").send()
        logger.info("approve_plan: auto_approve=true のため自動承認しました")
        return "config.ini の [plan].auto_approve が有効なため、ユーザーへの確認をスキップして自動承認しました。書き込み系ツール（run_script/execute_python_code）を実行できます。"
    content = (
        _render_plan(plan) + "\n\nこの計画を承認しますか？承認後は各ステップの書き込み系ツール"
        "（run_script/execute_python_code）が実行できるようになります。"
    )
    actions = [
        cl.Action(name="approve", payload={"value": "approve"}, label="✅ 計画を承認"),
        cl.Action(name="deny", payload={"value": "deny"}, label="🚫 却下"),
    ]
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    timeout = _resolve_ask_timeout(_APPROVAL_TIMEOUT_SECONDS)

    def _factory():
        return cl.AskActionMessage(content=content, actions=actions, timeout=timeout).send()

    res = await _ask_with_cross_session_relay(thread_id, _factory, timeout)
    approved = res is not None and res["payload"].get("value") == "approve"
    cl.user_session.set("plan_approved", approved)
    # 前回却下時に立てたフラグが誤って残らないよう、承認・タイムアウト時は
    # 明示的にクリアする（このターンは却下ではないため）。
    cl.user_session.set("plan_denied_just_now", False)
    message: cl.Message | None = cl.user_session.get("plan_message")
    if message is not None:
        message.content = _render_plan_payload(plan, approved=approved)
        await message.update()
    if approved:
        logger.info("approve_plan: 承認されました")
        return "ユーザーが計画を承認しました。書き込み系ツール（run_script/execute_python_code）を実行できます。"
    if res is None:
        logger.info("approve_plan: 応答なし（タイムアウト）")
        return (
            f"ユーザーからの応答が{_APPROVAL_TIMEOUT_SECONDS}秒間ありませんでした"
            "（離席中の可能性があります）。計画自体はそのまま保持されているので、"
            "作り直す必要はありません。少し時間を置いてから改めて approve_plan を"
            "呼び直してください。"
        )
    logger.info("approve_plan: 明示的に却下されました")
    # app.py の on_tool_end がこのフラグを見て、ツール呼び出しを続けさせず
    # このターンの処理を強制的に打ち切る（LLMが「計画を微修正して続行しよう」
    # と自己判断してしまうのを、プロンプト指示だけに頼らずコード側で確実に防ぐため）。
    cl.user_session.set("plan_denied_just_now", True)
    return "ユーザーが計画を却下しました。これ以上ツールを呼ばず、" "却下された旨を最終回答として述べて処理を終了してください。"


@tool
async def update_task_progress(step_index: int, status: str) -> str:
    """実行計画中のステップの進捗状態を更新し、表示中のチェックリストへ反映する。

    ステップの実行前に "in_progress"、完了後に "completed" を設定してユーザーに
    進捗を見せること。"in_progress" の間はチェックリスト上に content の代わりに
    create_plan で渡した activeForm が表示される。同時に "in_progress" にする
    ステップは1つまでにすること。全ステップが completed になると計画は完了した
    ものとみなし、承認状態を解除する（承認は作成済み計画の実行に限定した
    スコープのため、完了後の無関係な run_script/execute_python_code は
    再びブロックされる）。

    run_script_background/execute_python_code_background に対応するステップは、
    ジョブを起動しただけの時点では completed にしないこと。check_script_job
    自体は読み取り専用でいつでも呼べるが、起動直後に completed にすると
    承認状態が解除され Plan Mode 表示になるため、まだジョブが実行中なのに
    「計画をやり直す必要がある」と誤解し、不要な create_plan/approve_plan を
    繰り返す原因になる。check_script_job で最終結果（running 以外の状態）を
    確認できてから completed にすること。

    Args:
        step_index: create_plan で渡した steps のインデックス（0始まり）。
        status: "pending" | "in_progress" | "completed" のいずれか。

    Returns:
        更新内容を説明する短いテキスト。計画が未作成、step_index が範囲外、
        status が不正な値の場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    plan = cl.user_session.get("plan")
    if not plan:
        return "エラー: 計画がありません。先に create_plan を呼んでください。"
    if status not in _VALID_TASK_STATUSES:
        return f"エラー: status は {_VALID_TASK_STATUSES} のいずれかを指定してください: {status}"
    if not (0 <= step_index < len(plan)):
        return f"エラー: step_index が範囲外です（0〜{len(plan) - 1}）: {step_index}"

    plan[step_index]["status"] = status
    cl.user_session.set("plan", plan)
    finished = all(s["status"] == "completed" for s in plan)
    if finished:
        cl.user_session.set("plan_approved", False)

    message: cl.Message | None = cl.user_session.get("plan_message")
    if message is not None:
        message.content = _render_plan_payload(plan, finished=finished, approved=cl.user_session.get("plan_approved", False))
        await message.update()

    logger.info("update_task_progress: step=%d status=%s finished=%s", step_index, status, finished)
    label = plan[step_index]["content"]
    suffix = "\n計画は全ステップ完了しました。" if finished else ""
    return f"ステップ{step_index}「{label}」を {status} に更新しました。{suffix}"


@tool
async def get_plan_status() -> str:
    """現在 Plan Mode（書き込み系ツールがロック中）か Edit Automatically（承認済み計画の
    実行が許可された状態）かを確認する読み取り専用ツール。

    書き込み系ツール（run_script、execute_python_code）を
    呼ぶ前に自分の状態認識に確信が持てない場合、いつでも呼んでよい（計画の有無や承認
    状態に関わらずブロックされない）。

    Returns:
        現在のモード（"Plan Mode" または "Edit Automatically"）と、計画が存在すれば
        そのステップ一覧・各ステータスを含むテキスト。計画が未作成の場合はその旨を
        伝えるテキストを返す。
    """
    plan = cl.user_session.get("plan")
    approved = bool(cl.user_session.get("plan_approved"))
    if not plan:
        return "現在の状態: Plan Mode（計画は未作成）。書き込み系ツールはブロックされます。"
    mode = "Edit Automatically" if approved else "Plan Mode"
    return f"現在の状態: {mode}\n\n" + _render_plan(plan)


@tool
async def lock_plan_mode() -> str:
    """Edit Automatically から Plan Mode へ手動で戻す（承認状態を取り消す）。

    全ステップの完了を待たず、途中で自動実行を止めて書き込み系ツールを再びロックしたい
    場合に、ユーザーの承認を介さず自分の判断で呼んでよい。計画（ステップ一覧）自体は
    削除されない。再度書き込み系ツールを使うには、改めて approve_plan で承認を得ること。

    Returns:
        状態変更の結果を伝えるテキスト。
    """
    was_approved = bool(cl.user_session.get("plan_approved"))
    cl.user_session.set("plan_approved", False)
    plan = cl.user_session.get("plan")
    message: cl.Message | None = cl.user_session.get("plan_message")
    if message is not None and plan is not None:
        finished = all(s["status"] == "completed" for s in plan)
        message.content = _render_plan_payload(plan, finished=finished, approved=False)
        await message.update()
    logger.info("lock_plan_mode: 呼び出し（元の状態: %s）", "approved" if was_approved else "not approved")
    if not was_approved:
        return "既に Plan Mode です（変更なし）。"
    return "Plan Mode へ戻しました。書き込み系ツールは再びブロックされます。" "再開するには approve_plan で改めて承認を得てください。"


async def toggle_plan_mode_from_ui() -> None:
    """送信ボタン付近の Plan Mode / Edit Automatically バッジをユーザーが
    クリックした際に呼ばれる（app.py の action_callback("toggle_plan_mode")
    経由）。LLMツールではなく、ユーザーがUIから直接操作するための関数。

    計画が存在しない場合は何もしない（切り替える対象が無いため）。また
    config.ini の [plan].allow_badge_unlock が False の場合、Plan Mode →
    Edit Automatically 方向（ロック解除）のクリックは無視する（Edit
    Automatically → Plan Mode 方向のクリックは常に許可する）。
    """
    plan = cl.user_session.get("plan")
    if not plan:
        return
    currently_approved = bool(cl.user_session.get("plan_approved"))
    if not currently_approved and not _PLAN_BADGE_ALLOW_UNLOCK:
        logger.info("toggle_plan_mode_from_ui: allow_badge_unlock=False のため" "ロック解除方向のクリックを無視しました")
        return
    approved = not currently_approved
    cl.user_session.set("plan_approved", approved)
    message: cl.Message | None = cl.user_session.get("plan_message")
    if message is not None:
        finished = all(s["status"] == "completed" for s in plan)
        message.content = _render_plan_payload(plan, finished=finished, approved=approved)
        await message.update()
    logger.info(
        "toggle_plan_mode_from_ui: ユーザーがバッジをクリック（新状態: %s）",
        "approved" if approved else "not approved",
    )


@tool("AskUserQuestion")
async def ask_user_question(question: str, labels: list[str] | None = None) -> str:
    """会話を続けるために必要な追加情報を、ユーザーに自由記述で質問する。

    要求が曖昧・情報が不足している等、自由記述の回答（固有名詞・ファイルパス・
    詳細な要望など）が必要な場合に使う。選択肢から選んでほしい場合は
    ask_user_choice を使うこと。

    単一の質問なら labels を省略する。複数項目（例:
    ファイル名と出力形式）をまとめて一度に自由記述で答えてほしい場合のみ、
    labels に入力欄ごとのラベルを列挙する。項目ごとに本ツールを繰り返す
    必要はない。

    Args:
        question: ユーザーに表示する質問文（labels指定時はフォーム全体の
            見出しとして表示）。
        labels: 複数項目をまとめて聞きたい場合の、入力欄ごとのラベル文字列
            リスト。省略時（None または空リスト）は単一の自由記述入力欄を
            表示する。

    Returns:
        labels を省略した場合はユーザーが入力した回答テキストをそのまま返す。
        labels を指定した場合は "ラベル: 入力値" を改行区切りで並べた文字列を
        返す。設定されたタイムアウト秒数以内に応答が無い場合は、例外を送出せず
        「エラー: ユーザーからの応答がありませんでした（タイムアウト）。」を返す。
    """
    timeout = _resolve_ask_timeout(_ASK_USER_QUESTION_TIMEOUT_SECONDS)
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    if not labels:
        logger.info("ask_user_question: %s", question)

        def _factory():
            return cl.AskUserMessage(content=question, timeout=timeout).send()

        res = await _ask_with_cross_session_relay(thread_id, _factory, timeout)
        if res is None:
            return "エラー: ユーザーからの応答がありませんでした（タイムアウト）。"
        return res.get("output", "")
    logger.info("ask_user_question: %s labels=%s", question, labels)

    def _factory_element():
        # cl.CustomElement はセッション文脈に依存するため、中継時に別セッションで
        # 作り直せるよう毎回ここで新規生成する（_ask_with_cross_session_relay の
        # factory 引数のdocstring参照）。
        element = cl.CustomElement(name="MultiTextForm", props={"question": question, "labels": labels})
        return cl.AskElementMessage(content=question, element=element, timeout=timeout).send()

    res = await _ask_with_cross_session_relay(thread_id, _factory_element, timeout)
    if res is None:
        return "エラー: ユーザーからの応答がありませんでした（タイムアウト）。"
    values = res.get("values") or []
    return "\n".join(f"{label}: {value}" for label, value in zip(labels, values))


_ASK_CHOICE_CANCEL_VALUE = "__cancel__"
_ASK_CHOICE_OTHER_VALUE = "__other__"
_ASK_CHOICE_CANCEL_MESSAGE = "エラー: ユーザーが選択をキャンセルしました。"


@tool
async def ask_user_choice(question: str, choices: list[str], multi_select: bool = False) -> str:
    """会話を続けるために必要な選択を、ユーザーに選択肢形式で質問する。

    複数の進め方・方針からユーザーに1つ（または複数）選んでもらいたい場合に使う。
    自由記述の回答が必要な場合は AskUserQuestion を使うこと。

    表示される選択肢には常に「✏️ その他（自由入力）」「❌ キャンセル」が
    自動的に追加される。「その他」が選ばれた場合は続けて自由記述の入力欄を
    表示しその回答を返す。「キャンセル」が選ばれた場合は choices に無い
    指示をユーザーがしたいときの離脱手段として機能する。

    Args:
        question: ユーザーに表示する質問文。
        choices: 選択肢の文字列リスト（1件以上）。
        multi_select: True の場合、チェックボックス形式で複数選択できるように
            表示し、選択された選択肢をまとめて返す（未選択のまま送信された
            場合は "(選択なし)" を返す）。False（既定）の場合は従来通り、
            選択肢ボタンをクリックした時点で即座にその1件を選んで返す
            （択一で確定させたい場合はこちら）。

    Returns:
        multi_select=False（既定）: ユーザーが選んだ選択肢の文字列。「その他」
        経由の場合は自由記述の回答テキスト。
        multi_select=True: ユーザーが選択した選択肢（＋自由記述があれば追加）を
        「、」区切りで連結した文字列（未選択なら "(選択なし)"）。
        ユーザーがキャンセルした場合は "エラー: ユーザーが選択をキャンセルしま
        した。" を返す。choices が空の場合や、設定されたタイムアウト秒数以内に
        応答が無い場合も、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    if not choices:
        return "エラー: choices が空です。1件以上の選択肢を指定してください。"
    logger.info("ask_user_choice: %s choices=%s multi_select=%s", question, choices, multi_select)
    timeout = _resolve_ask_timeout(_ASK_USER_CHOICE_TIMEOUT_SECONDS)
    thread_id = cl.user_session.get("thread_id") or "_no_session"
    if multi_select:

        def _factory_multi():
            # cl.CustomElement はセッション文脈に依存するため、中継時に別セッションで
            # 作り直せるよう毎回ここで新規生成する（_ask_with_cross_session_relay の
            # factory 引数のdocstring参照）。
            element = cl.CustomElement(name="MultiChoiceForm", props={"question": question, "choices": choices})
            return cl.AskElementMessage(content=question, element=element, timeout=timeout).send()

        res = await _ask_with_cross_session_relay(thread_id, _factory_multi, timeout)
        if res is None:
            return "エラー: ユーザーからの応答がありませんでした（タイムアウト）。"
        if not res.get("submitted", True):
            return _ASK_CHOICE_CANCEL_MESSAGE
        selected = list(res.get("values") or [])
        other = (res.get("other") or "").strip()
        if other:
            selected.append(other)
        return "、".join(selected) if selected else "(選択なし)"

    def _factory_single():
        actions = [cl.Action(name=f"choice_{i}", payload={"value": c}, label=c) for i, c in enumerate(choices)]
        actions.append(cl.Action(name="other", payload={"value": _ASK_CHOICE_OTHER_VALUE}, label="✏️ その他（自由入力）"))
        actions.append(cl.Action(name="cancel", payload={"value": _ASK_CHOICE_CANCEL_VALUE}, label="❌ キャンセル"))
        return cl.AskActionMessage(content=question, actions=actions, timeout=timeout).send()

    res = await _ask_with_cross_session_relay(thread_id, _factory_single, timeout)
    if res is None:
        return "エラー: ユーザーからの応答がありませんでした（タイムアウト）。"
    value = res["payload"].get("value") or res.get("label", "")
    if value == _ASK_CHOICE_CANCEL_VALUE:
        return _ASK_CHOICE_CANCEL_MESSAGE
    if value == _ASK_CHOICE_OTHER_VALUE:

        def _factory_other():
            return cl.AskUserMessage(content=question, timeout=timeout).send()

        other_res = await _ask_with_cross_session_relay(thread_id, _factory_other, timeout)
        if other_res is None:
            return "エラー: ユーザーからの応答がありませんでした（タイムアウト）。"
        return other_res.get("output", "")
    return value


@tool(response_format="content_and_artifact")
def analyze_image(relative_path: str) -> tuple[str, dict | None]:
    """画像ファイルをLLMへ視覚情報として見せ、自分（LLM）がその内容を解析・説明・判断するために使う。

    読み込み系ツールのため、ローカルファイルシステム上の任意の絶対パスを指定できる
    （Read 等と同様、パスの制限は行わない。ただし `_tmp_<thread_id>` の他セッション
    分だけは例外で解析できない）。

    ユーザーが画像そのものを見たい（表示・プレビュー）だけなら、代わりに
    `show_image` を使うこと。このツールはあくまで自分が画像の中身を理解する
    必要がある場合専用（例: SKILL.md 本文が references/assets 配下の画像を
    参照していて内容を踏まえて回答する必要がある場合、run_script が生成した
    画像ファイルの内容を確認して次の判断に使う場合、ユーザーが指定した作業
    ディレクトリ配下にある画像（写真・スキャン画像等）の内容を読み取って
    説明・分析する必要がある場合）。
    ツール呼び出しの結果には画像データを直接積めない実装上の制約があるため、
    この関数はテキストの確認メッセージのみを返す。画像本体は裏側で処理され、
    次のモデル呼び出しで実際にLLMへ見えるようになる。

    Args:
        relative_path: 相対パスを渡すと skills ルートからの相対パスとして
            解決する（例: word-counter/references/example.png）。それ以外の
            場所の画像を見る場合は絶対パス（例:
            C:\\Users\\foo\\data\\2019\\img1.png）で指定すること。
            Glob/Grep/Read の結果に付与されたパスメモリー参照（`@N` 形式）を
            そのまま渡すこともできる。

    Returns:
        (確認テキスト, artifact) のタプル。artifact は画像を読み込めた場合のみ
        {"image_url": "data:<mime>;base64,<...>"} を持つ dict、それ以外は None。
        ファイルが存在しない場合・対応拡張子（png/jpg/jpeg/gif/webp/bmp）
        でない場合、`@N` が未登録の場合は、例外を送出せず「エラー: ...」
        形式のテキストと None を返す。
    """
    resolved_path, error = _resolve_path_memory_token(relative_path)
    if error:
        return f"エラー: {error}", None
    path = _resolve_analyze_image_path(resolved_path)
    tmp_error = _foreign_tmp_dir_error(path)
    if tmp_error:
        return tmp_error, None
    if not path.is_file():
        return f"エラー: ファイルが見つかりません: {relative_path}", None
    if not is_image_file(path):
        return (
            f"エラー: 対応していない画像形式です（png/jpg/jpeg/gif/webp/bmpのみ）: {relative_path}",
            None,
        )
    # 同一画像の重複表示を検知する（tune-prompt調査で同じ画像を14回重複して
    # 呼ぶ実例あり。画像artifactはトークン消費が大きく、繰り返し会話へ積むと
    # コンテキストを大きく圧迫するため、2回目以降はテキストのみ返し
    # artifactを積まない）。解決済みの絶対パスをキーにするため、`@N`や相対/絶対
    # パスなど表記が違っても同一ファイルなら重複として検知できる。
    if _record_and_check_duplicate(_duplicate_guard_session_key("analyze_image_call_signatures"), str(path)):
        return (
            f"エラー: この画像は既に一度確認済みです: {relative_path}。"
            "同一画像の再表示は省略しました。会話履歴にある前回の説明を参照するか、"
            "他の画像・他の手段に進んでください。",
            None,
        )
    logger.info("analyze_image: %s", relative_path)
    # 4032x3024 のような高解像度写真をそのまま渡すと数枚でトークン上限に達するため、
    # config.ini [images] の設定に従って縮小してから渡す（既定は縮小なし）。
    cfg = _LLM_CONFIG
    return f"画像を読み込みました: {relative_path}", {
        "image_url": to_data_url(
            path,
            max_long_side=cfg.image_max_long_side_pixels if cfg else 0,
            jpeg_quality=cfg.image_jpeg_quality if cfg else 85,
        )
    }


def _with_image_followups(result: dict) -> dict:
    """ToolMessage.artifact に画像があれば、直後に画像付き HumanMessage を追加する。

    analyze_image が {"image_url": ...} という artifact 付きの ToolMessage を
    返した場合にのみ発火する。それ以外のツール結果には触れない。

    Args:
        result: ToolNode.invoke/ainvoke の戻り値（{"messages": [ToolMessage, ...]}）。

    Returns:
        画像artifactがあれば末尾に HumanMessage を追加した新しい dict、
        無ければ result をそのまま返す。
    """
    messages = result.get("messages", [])
    extra = [followup for msg in messages if (followup := image_followup_message(getattr(msg, "artifact", None))) is not None]
    if not extra:
        return result
    return {**result, "messages": [*messages, *extra]}


def _extract_tool_call_from_node_input(input) -> dict | None:  # noqa: A002
    """ImageAwareToolNode.invoke/ainvoke が受け取る入力から、今回実行対象の
    単一 tool_call を取り出す。

    LangGraph は並列 tool_calls 実行のため、AIMessage の tool_calls を
    まとめてバッチで渡すのではなく、個々の tool_call を1件ずつ
    {"__type": "tool_call_with_context", "tool_call": {...}, "state": {...}}
    という形で渡してくる（ToolNode の公開 docstring が説明する
    dict/list/tool_calls-list の3形式には無い、実際に確認した挙動）。
    この形でなければ None を返す。
    """
    if isinstance(input, dict) and input.get("__type") == "tool_call_with_context":
        return input.get("tool_call")
    return None


def _log_tool_calls_debug(input) -> None:  # noqa: A002
    """メイングラフのツール呼び出し（呼び出し前）を DEBUG レベルで記録する。

    config.ini の [log].level が "debug" のときのみ実際にログへ出る
    （logger.isEnabledFor で早期リターンし、通常時はほぼゼロオーバーヘッド）。
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return
    logger.debug("tool_call: name=%s args=%r id=%s", call.get("name"), call.get("args"), call.get("id"))


def _contains_error(content: str) -> bool:
    """文字列にエラーを示すキーワードが含まれるかを判定する。

    日本語「エラー」、英語「error」(大文字小文字区別なし)、全角カタカナ
    「ｴﾗｰ」に対応する。
    """
    content_lower = content.lower()
    return "エラー" in content or "error" in content_lower or "ｴﾗｰ" in content


def _log_tool_results_debug(result: dict, call_args: dict | None = None) -> None:
    """メイングラフのツール呼び出し結果を DEBUG レベルで記録する（全文、未省略）。

    execute_python_* 系ツールは成功時も WARNING（スキル開発アイデアの
    シグナルとして記録。代替スキルが作られれば LLM は呼ばなくなる）。
    エラーキーワードを含むメッセージも WARNING。
    execute_python_code の場合は call_args に code が含まれるため、
    WARNINGログにも含める（monitor-app-log スキルでissue起票可能にする）。
    """
    for msg in result.get("messages", []):
        name = getattr(msg, "name", None) or ""
        content = msg.content or ""
        is_execute_python = name.startswith("execute_python_")
        has_error = _contains_error(content)

        if is_execute_python or has_error:
            if is_execute_python and call_args and "code" in call_args:
                logger.warning(
                    "tool_result: name=%s args_code=%r content=%r",
                    name,
                    call_args["code"][:1000],
                    content[:500],
                )
            else:
                logger.warning("tool_result: name=%s content=%r", name, content[:500])
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("tool_result: name=%s content=%r", name, content)


_ALLOWED_WHILE_AWAITING_APPROVAL = {"approve_plan", "get_plan_status", "lock_plan_mode"}
# このうち呼ばれたらガードのフラグ自体を解除するもの（それ以外
# （get_plan_status）は読み取り専用の確認だけなので、フラグは維持したまま
# 通過させる）。
_CLEARS_AWAITING_APPROVAL = {"approve_plan", "lock_plan_mode"}


def _guard_awaiting_approve_plan(input):  # noqa: A002
    """create_plan 直後、approve_plan/get_plan_status/lock_plan_mode 以外の
    tool_calls を実行させず、合成 ToolMessage に差し替える。

    本番ログ・evalで、create_plan の直後に規定の approve_plan ではなく
    ask_user_choice 等を自作して承認確認の代わりに使ってしまう事例が確認された
    （システムプロンプトの指示のみでは確実性が無いため、コード側でも強制する）。
    ただし、モデルが調査不足に自分で気づき lock_plan_mode で計画をやり直そうと
    する自己修正（evalで実際に観測済み）は正当な経路として許可する。
    ガード対象でなければ None を返す（呼び出し側はこの場合、通常通り
    super().ainvoke()/invoke() を呼ぶこと）。

    Args:
        input: ImageAwareToolNode.invoke/ainvoke がそのまま受け取った入力
            （_extract_tool_call_from_node_input 参照）。

    Returns:
        ブロックする場合は ToolNode.invoke/ainvoke と同じ形式の結果
        （{"messages": [ToolMessage]}）。ガード対象外・許可リスト内の
        呼び出しの場合は None。
    """
    if not cl.user_session.get("awaiting_approve_plan_call"):
        return None
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return None
    name = call.get("name")
    if name in _ALLOWED_WHILE_AWAITING_APPROVAL:
        if name in _CLEARS_AWAITING_APPROVAL:
            cl.user_session.set("awaiting_approve_plan_call", False)
        return None
    # フラグはクリアしない（モデルが approve_plan/lock_plan_mode を呼ぶまで、
    # 次の呼び出しも引き続きブロックする。plan_approved ガードと同様、
    # 明示的に解除されるまでブロックし続ける設計）。
    return {
        "messages": [
            ToolMessage(
                content=("エラー: create_planの直後はapprove_planを呼んでください" "（他のツールは実行されませんでした）。"),
                name=name,
                tool_call_id=call.get("id"),
                status="error",
            )
        ]
    }


def _guard_main_agent_tool_limit(input):  # noqa: A002
    """[main_agent_tool_guard] に登録済みのツールを、メインエージェントが直接
    呼べる回数を制限し、dispatch_agentへの委譲を促すガード。

    旧実装は Glob専用（_check_main_agent_glob_limit）・run_script専用
    （旧 _check_main_agent_run_script_limit）と対象ごとにコードを決め打ちして
    いたが、実際にトークン上限へ追い込んだ主因は run_script の連打ではなく
    その後 analyze_image をメインエージェント自身が委譲せずページ数分連打した
    ことだった（英検3級PDF調査、2026-08-10）。ビルトインツールを一切問わず
    任意の名前を登録できるよう、ImageAwareToolNode.invoke/ainvoke の共通
    差し込みポイント（_guard_awaiting_approve_plan と同じ場所）でツール名
    そのものを判定する汎用ガードに統合し、Globガードもここへ統合済み
    （既定の [main_agent_tool_guard].entries に ["Glob", 1] を含める）。

    登録形式（[main_agent_tool_guard].entries）の各要素は
    [対象, max_calls] の2要素で、対象は2種類を許容する:
      - 文字列1件（例: "Glob", "analyze_image"）: そのツール名の呼び出し
        そのものを引数を問わず対象にする。
      - [スキル名, スクリプトファイル名] の2要素（例:
        ["pdf-tools","render_pdf_pages.py"]）: name が run_script/
        run_script_background で、かつ args の skill_name/script_filename が
        一致する場合のみ対象にする。
    max_calls はエントリごとに個別指定する（ツールごとに許容回数を変えたい
    ケースがあるため、旧実装のような全体共通の1値ではない）。0以下を指定すると
    「1回も呼べない（完全ブロック）」を意味する。他の呼び出し回数ガード
    （_check_file_tools_duplicate が使う _record_and_check_duplicate）は
    0以下を「無制限（ガード無効）」として扱うが、本ガードは対象を明示的に
    登録するホワイトリスト方式であり、「登録した上で無制限にする」は
    「そもそも登録しない」のと同義で意味を成さない。そのため0以下を
    _record_and_check_duplicate へ委譲せず、ここで先に「常時ブロック」として
    扱う（このガード固有の意味論であり、他の呼び出し回数ガードには影響しない）。

    サブエージェント（dispatch_agent経由）内部での呼び出しは対象外
    （_IN_SUBAGENT が True の間はガードしない。調査そのものが役目のため）。

    Args:
        input: ImageAwareToolNode.invoke/ainvoke がそのまま受け取った入力
            （_extract_tool_call_from_node_input 参照）。

    Returns:
        上限に達していればブロックする結果（{"messages": [ToolMessage]}）、
        そうでなければ None（呼び出し側は通常通りツールを実行してよい）。
    """
    cfg = _LLM_CONFIG
    guard_enabled = cfg.main_agent_tool_guard_enabled if cfg else False
    if not guard_enabled:
        return None
    if _IN_SUBAGENT.get():
        return None
    entries = cfg.main_agent_tool_guard_entries if cfg else frozenset()
    if not entries:
        return None
    entries_by_key = dict(entries)
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return None
    name = call.get("name")
    args = call.get("args") or {}
    signature: str | None = None
    guard_max_calls: int | None = None
    if name in entries_by_key:
        signature = name
        guard_max_calls = entries_by_key[name]
    elif name in ("run_script", "run_script_background"):
        pair = (args.get("skill_name"), args.get("script_filename"))
        if pair in entries_by_key:
            signature = f"{name}:{pair[0]}/{pair[1]}"
            guard_max_calls = entries_by_key[pair]
    if signature is None:
        return None
    if guard_max_calls > 0 and not _record_and_check_duplicate("main_agent_tool_guard_call_count", signature, guard_max_calls):
        return None
    content = (
        f"エラー: {name} はメインエージェントとして呼び出しを禁止されています" "（max_calls=0）。"
        if guard_max_calls <= 0
        else (f"エラー: {name} はメインエージェントとして既に呼び出し上限" f"（{guard_max_calls}回）に達しています。")
    )
    # agent_type一覧はagents/*.mdの走査結果（_AGENT_TYPES）から動的に組み立てる。
    # 種別を文言へ決め打ちすると、将来 agent_type が増減しても案内が追従せず、
    # 存在しない/古い種別へ誘導してしまう（dispatch_agentの不明agent_typeエラー
    # メッセージと同じ組み立て方、2972行目付近参照）。
    available_agent_types = ", ".join(sorted(_AGENT_TYPES)) or "dispatch_agentのagent_type一覧を確認"
    return {
        "messages": [
            ToolMessage(
                content=(
                    f"{content}これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: {available_agent_types}）へ委譲してください。"
                ),
                name=name,
                tool_call_id=call.get("id"),
                status="error",
            )
        ]
    }


class ImageAwareToolNode(ToolNode):
    """analyze_image の実行結果（画像）を、後続の HumanMessage として自動追加する ToolNode。

    OpenAI互換API の tool role メッセージは文字列content しか持てないため、
    画像を持つ ToolMessage.artifact をそのまま次のモデル呼び出しに含めることは
    できない。そこで ToolNode 実行後に _with_image_followups() で後処理し、
    画像を content に持つ HumanMessage を会話履歴へ追加する。
    handwritten/prebuilt いずれのグラフ実装でも、素の ToolNode の代わりに
    このクラスを使うだけで画像受け渡しに対応できる。

    また ToolNode の公式拡張点 awrap_tool_call 経由で、全ツール呼び出しを
    セッション毎の Semaphore（_TOOL_CALL_SEMAPHORES）によりガードする
    （_tool_call_semaphore_wrap 参照。ToolNode._afunc が同一AIMessage内の
    複数tool_callsを asyncio.gather() で完全並列実行する挙動への対策。
    dispatch_agent 専用の _DISPATCH_AGENT_SEMAPHORES と同じ理由づけの
    メインエージェント版）。
    """

    def __init__(self, tools, **kwargs):
        kwargs.setdefault("awrap_tool_call", _tool_call_semaphore_wrap)
        super().__init__(tools, **kwargs)

    def invoke(self, input, config=None, **kwargs):  # noqa: A002
        _log_tool_calls_debug(input)
        call_args = _extract_tool_call_from_node_input(input)
        if call_args:
            call_args = call_args.get("args", {})
        blocked = _guard_awaiting_approve_plan(input) or _guard_main_agent_tool_limit(input)
        if blocked is not None:
            _log_tool_results_debug(blocked, call_args)
            return _with_image_followups(blocked)
        result = super().invoke(input, config, **kwargs)
        _log_tool_results_debug(result, call_args)
        return _with_image_followups(result)

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: A002
        _log_tool_calls_debug(input)
        call_args = _extract_tool_call_from_node_input(input)
        if call_args:
            call_args = call_args.get("args", {})
        blocked = _guard_awaiting_approve_plan(input) or _guard_main_agent_tool_limit(input)
        if blocked is not None:
            _log_tool_results_debug(blocked, call_args)
            return _with_image_followups(blocked)
        result = await super().ainvoke(input, config, **kwargs)
        _log_tool_results_debug(result, call_args)
        return _with_image_followups(result)


def _require_memory_root() -> Path:
    """_MEMORY_ROOT を返す。init_tools() が未実行なら RuntimeError。"""
    if _MEMORY_ROOT is None:
        raise RuntimeError("init_tools() が未実行です")
    return _MEMORY_ROOT


@tool
def create_memory(name: str, description: str, memory_type: str, content: str) -> str:
    """新しい永続メモリーを保存する。

    スレッドをまたいで将来の会話へ引き継ぎたい価値ある事実を学んだときに使う。
    memory_type ごとの保存タイミング・保存してはいけないものはシステムプロンプトの
    Memory System セクションを参照すること。同名のメモリーが既にある場合はエラーに
    なるので、既存メモリーの更新には update_memory を使うこと（迷ったら先に
    search_memory / list_memories で重複が無いか確認する）。

    Args:
        name: 一意な名前（英数字・ハイフン・アンダースコアのみ、64文字以内）。
        description: 一行の説明文（索引 MEMORY.md にそのまま載る）。
        memory_type: "user" | "feedback" | "project" | "reference" のいずれか。
        content: メモリー本文。feedback/project タイプはルール/事実の後に
            「**Why:**」「**How to apply:**」の行を含めることが望ましい。

    Returns:
        保存したファイルパスを伝えるテキスト。name/memory_type が不正、
        description/content が空、同名のメモリーが既に存在する場合は、
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.create_memory(_require_memory_root(), name, description, memory_type, content)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("create_memory: %s", name)
    return f"メモリーを保存しました: {path}"


@tool
def update_memory(name: str, content: str) -> str:
    """既存の永続メモリーの本文を更新する（name/description/memory_typeは変わらない）。

    Args:
        name: 更新対象メモリーの名前。
        content: 新しい本文（既存の本文を丸ごと置き換える）。

    Returns:
        更新したファイルパスを伝えるテキスト。content が空、または name の
        メモリーが存在しない場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.update_memory(_require_memory_root(), name, content)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("update_memory: %s", name)
    return f"メモリーを更新しました: {path}"


@tool
def delete_memory(name: str) -> str:
    """永続メモリーを削除する。

    Args:
        name: 削除対象メモリーの名前。

    Returns:
        削除したファイルパスを伝えるテキスト。name のメモリーが存在しない場合は、
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        path = memory.delete_memory(_require_memory_root(), name)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("delete_memory: %s", name)
    return f"メモリーを削除しました: {path}"


@tool
def read_memory(name: str) -> str:
    """永続メモリー1件を本文込みで全文読み込む。

    search_memory / list_memories は一覧（name+description）しか返さないため、
    メモリーの内容そのものを確認・引用する前には必ずこのツールで全文を読むこと。

    Args:
        name: 読み込むメモリーの名前。

    Returns:
        「[type] name\\ndescription\\n\\ncontent」形式の全文。name のメモリーが
        存在しない場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        mem = memory.read_memory(_require_memory_root(), name)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("read_memory: %s", name)
    return f"[{mem.memory_type}] {mem.name}\n{mem.description}\n\n{mem.content}"


@tool
def search_memory(query: str, memory_type: str | None = None) -> str:
    """永続メモリーを name/description/本文に対するキーワード部分一致で検索する。

    本文は返さず一覧（name+description）のみを返す（コンテキスト節約のため）。
    内容そのものが必要な場合は、ヒットした name を read_memory へ渡すこと。

    Args:
        query: 検索キーワード（大文字小文字は区別しない）。
        memory_type: 指定すれば "user"|"feedback"|"project"|"reference" の
            いずれかに絞り込む（省略時は全type対象）。

    Returns:
        ヒットしたメモリーの「- [type] name: description」一覧と件数。
        0件の場合は「一致するメモリーはありません」。query が空、または
        memory_type が不正な値の場合は、例外を送出せず「エラー: ...」形式の
        文字列を返す。
    """
    try:
        hits = memory.search_memories(_require_memory_root(), query, memory_type)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("search_memory: query=%r type=%s hits=%d", query, memory_type, len(hits))
    if not hits:
        return "一致するメモリーはありません。"
    lines = [f"- [{m.memory_type}] {m.name}: {m.description}" for m in hits]
    return f"{len(hits)}件ヒットしました。\n" + "\n".join(lines)


@tool
def list_memories(memory_type: str | None = None) -> str:
    """保存されている永続メモリーを一覧表示する。

    Args:
        memory_type: 指定すれば "user"|"feedback"|"project"|"reference" の
            いずれかに絞り込む（省略時は全type対象）。

    Returns:
        「- [type] name: description」一覧。0件の場合は
        「保存されているメモリーはありません」。memory_type が不正な値の
        場合は、例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    try:
        mems = memory.list_memories(_require_memory_root(), memory_type)
    except (RuntimeError, ValueError) as e:
        return f"エラー: {e}"
    logger.info("list_memories: type=%s count=%d", memory_type, len(mems))
    if not mems:
        return "保存されているメモリーはありません。"
    return "\n".join(f"- [{m.memory_type}] {m.name}: {m.description}" for m in mems)


@tool("help")
def show_help() -> str:
    """このシステムの使い方に関するヘルプ本文を返す。

    ユーザーがヘルプや使い方、フィードバックの窓口について尋ねてきた場合に呼ぶ。
    本文は設定されたヘルプ用Markdownファイルに記述されており、このツールは
    その内容をそのまま読み込んで返すだけの薄いラッパー（憶測でヘルプ内容を
    生成しない）。

    Returns:
        ヘルプ本文（UTF-8 テキスト、Markdown形式）。init_tools() が未実行、
        または help_path のファイルが存在しない場合は、例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    if _HELP_PATH is None:
        return "エラー: init_tools() が未実行です"
    if not _HELP_PATH.is_file():
        return f"エラー: ヘルプファイルが見つかりません: {_HELP_PATH}"
    logger.info("show_help")
    return _HELP_PATH.read_text(encoding="utf-8")


# init_tools() の _resolve_agent_types() がこのリストを実行時に参照するだけなので、
# 定義順は init_tools()/dispatch_agent より後でもよい（analyze_image・メモリー系
# ツールを含めるため、それらの定義後に置く）。
_SUBAGENT_TOOLS: list = [
    read_skill,
    read_skill_file,
    provide_download,
    show_image,
    run_script,
    run_script_background,
    check_script_job,
    stop_script_job,
    execute_python_code,
    execute_python_code_background,
    execute_python_code_readonly,
    get_tool_source,
    check_work_dir_status,
    analyze_image,
    read_tool,
    glob_tool,
    grep_tool,
    json_query,
    list_path_memory,
    write_scratch_note,
    write_thread_note,
    list_thread_notes,
    read_thread_note,
    create_memory,
    update_memory,
    delete_memory,
    read_memory,
    search_memory,
    list_memories,
]


# グラフに渡す組み込みツール一覧（3 段階の progressive disclosure + サブエージェント委譲 +
# ユーザー質問 + 永続メモリー + ヘルプ）。MCPサーバー由来の動的ツール（_MCP_TOOLS）とは
# 別管理にし、get_all_tools() で合流させる（詳細は _MCP_TOOLS 定義箇所参照）。
_BASE_TOOLS: list[BaseTool] = [
    read_skill,
    read_skill_file,
    provide_download,
    show_image,
    run_script,
    run_script_background,
    check_script_job,
    stop_script_job,
    execute_python_code,
    execute_python_code_background,
    get_tool_source,
    check_work_dir_status,
    analyze_image,
    read_tool,
    glob_tool,
    grep_tool,
    json_query,
    list_path_memory,
    write_thread_note,
    list_thread_notes,
    read_thread_note,
    dispatch_agent,
    check_dispatch_agent_job,
    stop_dispatch_agent_job,
    ask_user_question,
    ask_user_choice,
    create_plan,
    approve_plan,
    update_task_progress,
    get_plan_status,
    lock_plan_mode,
    create_memory,
    update_memory,
    delete_memory,
    read_memory,
    search_memory,
    list_memories,
    show_help,
]

# MCPサーバーから取得した動的ツール（init_mcp_tools() が起動時に1回だけ設定する）。
# src/mcp_client.py が register_mcp_tools() 経由で書き込む。
_MCP_TOOLS: list[BaseTool] = []


def register_mcp_tools(mcp_tools: list[BaseTool]) -> None:
    """MCPサーバーから取得した動的ツール一覧を差し替える。

    src.mcp_client.init_mcp_tools()（app.py の @cl.on_app_startup から
    アプリ起動時に1回だけ呼ばれる）が呼ぶ。

    Args:
        mcp_tools: 接続に成功した全MCPサーバーのツールをフラットにまとめたリスト。
    """
    global _MCP_TOOLS
    _MCP_TOOLS = list(mcp_tools)


def get_all_tools() -> list[BaseTool]:
    """メインエージェントに渡す全ツール（組み込み + MCP動的登録分）を返す。

    src/graph.py の build_graph() がセッション構築の都度これを呼ぶため、
    MCP接続が完了する前に呼ばれても（register_mcp_tools() 未実行なら）
    組み込みツールのみで安全にグラフを構築できる。
    """
    return [*_BASE_TOOLS, *_MCP_TOOLS]
