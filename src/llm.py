"""LLM 接続の共通ヘルパー（llama.cpp server / OpenAI 互換）。

graph.py（メインの ReAct ループ）と subagent.py（サブエージェントの
ReAct ループ）の両方から使う。tools.py が subagent.py を import する
関係上、build_model を graph.py 側に置くと循環 import になるため
ここへ切り出している。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import threading
import time
import weakref
import zlib
from difflib import SequenceMatcher
from typing import Any, Literal

import httpx
import openai
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from .config import Config, LLMEndpoint

logger = logging.getLogger(__name__)


# --- P0-3: cancel scope breakage watcher ---
# httpcore が "Attempted to exit cancel scope in a different task" を
# DEBUG でログするが、再raiseしないため app.py の client_broken フラグ
# に影響しない。このフィルタが検知したら src.llm 側で WARNING を出し、
# 直近の検知時刻/回数を公開関数で参照できるようにする。
class _CancelScopeBreakageWatcher(logging.Filter):
    """httpcore の cancel scope task 不一致ログを検知して WARNING へ格上げする。"""

    PATTERN = "Attempted to exit cancel scope in a different task"

    def __init__(self) -> None:
        super().__init__()
        self._hits: list[float] = []

    def filter(self, record: logging.LogRecord) -> bool:
        if self.PATTERN in record.getMessage():
            import time

            now = time.time()
            self._hits.append(now)
            # 直近60秒間のヒット数を保持
            self._hits[:] = [t for t in self._hits if now - t < 60]
            logger.warning(
                "httpcore cancel scope breakage 検知（直近60秒で%d回）。" "ストリームの後始末が別タスクで行われた可能性が高い",
                len(self._hits),
            )
        return True


_cancel_scope_watcher = _CancelScopeBreakageWatcher()

# --- LLM 同時実行数ガード ---
# llama-server への実 HTTP リクエスト総数をガードするセマフォ。
# [llm].max_concurrent_requests に応じて init_llm_concurrency() が
# 再設定する。None はガード無効（無制限）を表す。既定 Semaphore(1) は
# init_llm_concurrency() 未実行時（テスト等）の安全側フォールバック。
_LLM_REQUEST_SEMAPHORE: "asyncio.Semaphore | None" = asyncio.Semaphore(1)


def init_llm_concurrency(max_concurrent_requests: int) -> None:
    """llama-server への同時リクエスト数上限を初期化する。

    Args:
        max_concurrent_requests: 同時実行数上限。1 以上: Semaphore(N) で
            ガードする。0 以下: ガードを無効化（None に設定）する。
    """
    global _LLM_REQUEST_SEMAPHORE
    _LLM_REQUEST_SEMAPHORE = asyncio.Semaphore(max_concurrent_requests) if max_concurrent_requests > 0 else None


def _register_cancel_scope_watcher() -> None:
    """httpcore ロガーに cancel scope breakage フィルタを登録する（冪等）。

    app.py の _setup() から呼ぶ。複数回呼んでも重複登録しない。
    """
    if _cancel_scope_watcher not in logging.getLogger("httpcore").filters:
        logging.getLogger("httpcore").addFilter(_cancel_scope_watcher)


def recent_cancel_scope_breakage(within_seconds: float = 60.0) -> int:
    """直近 within_seconds 秒以内に検知された cancel scope breakage の回数を返す。

    0 を返す場合は検知なし（ただしフィルタ自体が未登録の場合は常に0）。
    app.py の for ループ先頭（新しい astream_events 開始時）で呼び、
    リトライ経路で何が起きたかを診断する。
    """
    now = time.time()
    return sum(1 for t in _cancel_scope_watcher._hits if now - t < within_seconds)


# build_model() が生成した httpx.AsyncClient を、生成時点のセッションID（app.py の
# thread_id）ごとに弱参照で分けて保持する。以前はプロセス全体で1つの WeakSet に
# 一括で集めていたが、それだと on_stop（1タブの停止操作）が他タブの実行中
# クライアントまで巻き添えで強制クローズしてしまう不具合があった
# （"Cannot send a request, as the client has been closed" が別タブで発生）。
# セッションごとに分けることで、aclose_active_llm_clients(session_id) が
# 自セッション分のクライアントだけを閉じられるようにする。
# 値の WeakSet は使い捨てクライアント（サブエージェント用等）がGCされれば
# 自然に空になるが、キー（session_id）自体は明示的に forget_session() で
# 消さない限り残る（app.py の @cl.on_chat_end から呼ぶ）。
_active_async_clients: "dict[str, weakref.WeakSet[httpx.AsyncClient]]" = {}

# build_model() が生成する httpx.AsyncClient を、どのセッションに紐づけて
# _active_async_clients へ登録するかを示す。Chainlit は @cl.on_chat_start /
# @cl.on_message / @cl.on_stop のたびに asyncio.create_task() で新しい
# タスクを起こし、各タスクは呼び出し時点の contextvars のコピーを持つ
# （他タブ＝他タスクへ値が漏れることはない）。dispatch_agent 経由の
# サブエージェント（src/subagent.py の run_subagent が asyncio.gather() で
# 並列実行する）にも、子タスク生成時に値がコピーされるため、src/tools.py の
# _IN_SUBAGENT と同様、サブエージェント側のコード変更なしに自動で正しい
# セッションIDへ伝播する。デフォルト None は Chainlit セッションを持たない
# 呼び出し元（evals/ の評価ハーネス等）向け。
_CURRENT_SESSION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_session_id", default=None)


def set_current_session(session_id: str | None) -> None:
    """これ以降このタスク（及びその子タスク）で build_model() が生成する
    httpx.AsyncClient を、どのセッションに紐づけて登録するかを設定する。

    app.py の @cl.on_chat_start・@cl.on_message 冒頭、および _rebuild_graph()
    から呼ぶ。各呼び出しは新しい asyncio.Task（＝ contextvars の独立した
    コピー）の中で行われるため、他タブへ値が漏れる心配はなく、明示的な
    reset も不要（次にこの関数が呼ばれるまで値を保持するだけでよい）。

    Args:
        session_id: このセッションの thread_id（cl.user_session の
            "thread_id"）。
    """
    _CURRENT_SESSION_ID.set(session_id)


def forget_session(session_id: str) -> None:
    """セッション終了時（タブを閉じた等）に、_active_async_clients の
    ブックキーピング用エントリだけを片付ける。

    クライアント自体は値の WeakSet が参照を失い次第GCで自然に回収される
    ため、ここでは辞書キー（session_id文字列）がプロセス寿命中ずっと
    残り続けるのを防ぐのが目的。強制クローズは行わない
    （app.py の @cl.on_chat_end 参照: タスクキャンセルを伴わないため、
    孤立した処理が残っている可能性があり、ここで close すると新たな
    エラーを誘発しかねないため）。

    Args:
        session_id: 片付け対象セッションの thread_id。
    """
    _active_async_clients.pop(session_id, None)


async def aclose_active_llm_clients(session_id: str) -> None:
    """指定セッションに紐づく httpx.AsyncClient のみを強制的にクローズする。

    Chainlit の停止ボタンは `session.current_task.cancel()` で実行中タスクへ
    `asyncio.CancelledError` を投げ込むだけで、LLMサーバーへの根底のHTTP接続を
    切断する処理は持たない。`ChatLlamaCpp._astream` の `finally` 節にある
    `agen.aclose()` は、タスクが既にキャンセル済みのコンテキストでは正しく
    完了しない可能性が高く（コメント参照）、接続が生きたままだと llama-server
    側が生成を続けてしまい、停止ボタンを押しても CPU/GPU 使用率が下がらない
    事象につながる（tune-prompt iter27でユーザー報告・調査）。

    app.py の `@cl.on_stop` から、キャンセルされていない別の task コンテキストで
    呼び、明示的に接続を切断する。session_id には停止操作を行った自セッションの
    thread_id を渡すこと。これにより、他タブが使用中のクライアントには一切
    影響しない（以前はプロセス全体で共有される1つの WeakSet を無差別に
    クローズしており、それが他タブの巻き添え停止の原因になっていた）。

    Notes:
        強制クローズした `httpx.AsyncClient` は以降二度と使用できない。
        呼び出し元（app.py の on_stop）はこの直後に必ず自セッションのグラフを
        再構築し、新しい `build_model()` 呼び出しで新しいクライアントに
        差し替えること。差し替えないと、以降そのセッションで LLM 呼び出しが
        恒久的に壊れたままになる。
    """
    clients = _active_async_clients.pop(session_id, None)
    if not clients:
        return
    pending_cancel: asyncio.CancelledError | None = None
    for client in list(clients):
        try:
            await client.aclose()
        except asyncio.CancelledError as exc:
            # このタスク自体へキャンセル要求が届いても、残りのクライアントの
            # close は最後まで試みる（asyncioの定石: 後始末を終えてから
            # 呼び出し元へ伝播する）。ここで即raiseしない。
            pending_cancel = exc
        except Exception:  # noqa: BLE001 - 1件の失敗が残りのcloseを妨げないようにする
            logger.debug("httpx.AsyncClient のクローズ中に例外が発生しました", exc_info=True)
    if pending_cancel is not None:
        raise pending_cancel


class ThinkingLoopDetected(Exception):
    """LLM応答（thinking/本文）が反復ループに陥ったと判定され、生成を打ち切ったことを示す。

    ChatLlamaCpp._stream/_astream がストリーム中に送出する。呼び出し元
    （src/graph.py の ainvoke_ensuring_final_text、app.py の on_message、
    src/subagent.py の run_subagent）がこれを捕捉し、注意メッセージを注入して
    再試行する。

    Attributes:
        snippet: 検知時点までにバッファされていた直近テキスト（末尾最大数百文字）。
            真の反復ループ（同一文の繰り返し）なのか、たまたま圧縮率が下がった
            構造化テキスト（表・JSON等）による誤検知なのかを事後に判別するための
            診断用データ。呼び出し元はこれをログ・eval結果へ残すことができる。
        client_broken: このループ検知に伴うストリームの後始末（_astreamの
            finally節でのagen.aclose()）が失敗し、httpx.AsyncClientの内部
            状態が壊れている可能性が高いことを示すフラグ。True の場合、
            同じクライアント（延いてはそれを使うグラフ）で次のリトライ
            リクエストを送ると、サーバー側に残った旧ストリームのせいで
            応答ヘッダーが永遠に返らずハングする（本番incident・2026-07-20で
            実際に7分11秒間ハングした事例を確認）。呼び出し元はリトライ前に
            aclose_active_llm_clients() でクライアントを強制クローズしてから
            再試行すること（app.pyのon_messageはさらにグラフも再構築する。
            src/subagent.pyの_invoke_with_loop_retryはモデルのみ再構築する。
            src/graph.pyのainvoke_ensuring_final_text（eval用ハーネス、本番
            動作には直結しない）は本対応の優先度を下げており未対応のまま）。
    """

    def __init__(self, message: str, snippet: str = "", client_broken: bool = False) -> None:
        super().__init__(message)
        self.snippet = snippet
        self.client_broken = client_broken


_DEFAULT_LOOP_NUDGE = "直前の応答は同じ内容を繰り返すループに陥ったため打ち切りました。" "落ち着いて、今のタスクの続きを行ってください。"


# openai SDK が httpx 例外をラップしないケースがあるため、両系統を1箇所に集約。
# APIConnectionError はサブクラスに APITimeoutError も含む。
# InternalServerError は 5xx。4xx はリトライ対象に含めない（クライアント側の誤り）。
#
# httpx側は個別の例外クラスを列挙するのではなく、その共通基底クラスである
# httpx.TransportError を使う。ストリーミング応答のヘッダー受信後（チャンク
# 受信中）に発生する低レベル例外はopenai SDKにもラップされず生のhttpx例外が
# そのまま上がってくるため（ChatLlamaCpp._astream参照）、個別列挙方式だと
# 新しい例外型（例: llama-serverプロセスが応答途中で落ちた際のhttpx.ReadError）
# が1つ漏れるたびに on_message の回復経路（_rebuild_checkpointer/
# _rebuild_graph）を素通りしてセッションが復旧不能になる再発を繰り返す
# （本番incident・2026-07-31: httpx.ReadErrorがこのタプルに含まれておらず
# Chainlit最上位ハンドラまで伝播し、グラフ・checkpointerが再構築されないまま
# セッションが壊れ続けた）。基底クラスにしておけば
# ConnectError/ReadError/WriteError/WriteTimeout/PoolTimeout/
# RemoteProtocolError等のトランスポート層例外を将来にわたって網羅できる。
LLM_CONNECTION_ERRORS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    openai.InternalServerError,
    httpx.TransportError,
)


def pick_loop_nudge_message(messages: list[str], attempt_index: int) -> str:
    """ループ検知時に注入する注意メッセージを選ぶ。

    attempt_index（0始まり、何回目のループ検知リトライか）が messages の
    範囲内ならその順番のメッセージを、範囲外（用意した数を使い切った場合）は
    ランダムに選ぶ（同じ文言の繰り返しで同じ堂々巡りを誘発しないため）。

    Args:
        messages: config.ini の [thinking_loop_guard].nudge_messages 由来の
            メッセージ候補（0件でもよい）。
        attempt_index: 0始まりのループ検知リトライ回数。

    Returns:
        注入するメッセージ文字列。messages が空なら組み込みの既定文言を返す。
    """
    if not messages:
        return _DEFAULT_LOOP_NUDGE
    if attempt_index < len(messages):
        return messages[attempt_index]
    return random.choice(messages)


class _ThinkingLoopDetector:
    """LLM応答（thinking/content）のストリーミング中に文章の反復（暴走ループ）を検知する。

    直近window_chars文字（recent window）が、それより前の履歴（最大
    max_history_chars文字に制限、以下MAX_K）の中にどれだけ長く一致する
    部分文字列を持つかを、difflib.SequenceMatcher.find_longest_match()で
    動的に測定する。match_ratio = 最長一致長 / window_chars が
    match_ratio_threshold を上回る状態が confirm_count 回連続したらループと
    判定する。

    旧実装（zlib圧縮率 + 累積n-gram新規性のAND条件）は、固定長ウィンドウ内
    だけを見る設計のため、反復ブロックの周期がウィンドウ長に近い・それを
    超えるケースで検知力が不安定になる根本的な脆弱性を持っていた
    （evals/tuning_log.md iter25参照: 実際の暴走で反復周期422文字に対し
    window_chars=600だと1周期分がウィンドウに収まらず、圧縮率が閾値0.3を
    一度も下回らずThinkingLoopDetectedが51分間発火しない重大な回帰が発生）。
    本方式は固定長Kを仮定せず、その時点で実際にどれだけ長く過去のコピーに
    なっているかを直接測定するため、反復周期の大小に依存しない。真の反復
    ループはmatch_ratioが1.0に収束する一方、値が都度変わる正当なJSON生成は
    低いmatch_ratioを維持する（iter25の合成データ・実測暴走テキスト検証で
    真のループ=1.000、JSON生成=0.09〜0.12、自然文=0.02程度と明確に分離
    することを確認済み）。
    """

    def __init__(
        self,
        window_chars: int,
        check_interval_chars: int,
        confirm_count: int,
        max_history_chars: int,
        match_ratio_threshold: float,
    ) -> None:
        self._buffer = ""
        self._window_chars = window_chars
        self._check_interval_chars = max(check_interval_chars, 1)
        self._confirm_count = max(confirm_count, 1)
        self._max_history_chars = max_history_chars
        self._match_ratio_threshold = match_ratio_threshold
        self._checked_len = 0
        self._consecutive_hits = 0

    def snippet(self, max_chars: int = 400) -> str:
        """検知時点の診断ログ用に、バッファ末尾の直近テキストを返す。"""
        return self._buffer[-max_chars:]

    def feed(self, text: str) -> bool:
        """新しいテキスト断片を追加し、ループが確定判定されたら True を返す。"""
        if not text:
            return False
        self._buffer += text
        if len(self._buffer) < self._window_chars:
            return False
        if len(self._buffer) - self._checked_len < self._check_interval_chars:
            return False
        self._checked_len = len(self._buffer)

        recent = self._buffer[-self._window_chars :]
        history_end = len(self._buffer) - self._window_chars
        history_start = max(0, history_end - self._max_history_chars)
        history = self._buffer[history_start:history_end]

        if history:
            # autojunk=False: 既定Trueだと高反復テキストでマッチ長が0扱いに
            # なる罠がある（iter22の時間軸類似度検証で確認済みの注意点）。
            matcher = SequenceMatcher(None, history, recent, autojunk=False)
            match = matcher.find_longest_match(0, len(history), 0, len(recent))
            match_ratio = match.size / len(recent)
        else:
            match_ratio = 0.0

        hit = match_ratio > self._match_ratio_threshold
        if hit:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        if logger.isEnabledFor(logging.DEBUG):
            # 生成が長時間停止ボタン等で強制中断された場合、on_llm_end
            # （正常完了時のみ発火）に頼った全文ログは残らない（2026-07-21、
            # 21分間ThinkingLoopDetectedが一度も発火しないまま暴走し、
            # 停止後に生成内容を一切追跡できなかった事例で判明）。
            # check_interval_charsごとのこのチェックのたびに直近テキストの
            # 断片を残しておけば、強制中断されても暴走時に実際どんな内容が
            # 生成されていたか事後に追跡できる。
            logger.debug(
                "ループ検知チェック: buffer_len=%d match_ratio=%.3f " "consecutive_hits=%d 直近テキスト=%r",
                len(self._buffer),
                match_ratio,
                self._consecutive_hits,
                recent[-300:],
            )
        return self._consecutive_hits >= self._confirm_count


def _chunk_delta_text(chunk: Any) -> str:
    """ストリームチャンク（ChatGenerationChunk）から、監視対象のテキストを取り出す。

    content と reasoning_content（ChatLlamaCpp._convert_chunk_to_generation_chunk
    が additional_kwargs へ拾い上げたもの）のデルタのみを対象にする。tool_call
    引数のJSONは別チャンネル（message.tool_call_chunks）で流れるため、ここでは
    自然に対象外となる。
    """
    message = getattr(chunk, "message", None)
    if message is None:
        return ""
    parts = []
    content = getattr(message, "content", None)
    if isinstance(content, str) and content:
        parts.append(content)
    reasoning = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
    if reasoning:
        parts.append(reasoning)
    return "".join(parts)


class _DebugResponseLogger(BaseCallbackHandler):
    """LLM応答本文・thinking（reasoning_content）・tool_callsを DEBUG レベルで記録する。

    build_model() が構築するモデルインスタンスに常時アタッチする（コストは
    logger.debug() 自体の isEnabledFor チェックのみで、config.ini の
    [paths].log_level が "debug" 以外のときはほぼゼロオーバーヘッド）。
    on_llm_end はチャットモデルでも呼ばれ、streaming=True の場合も最終的に
    集約されたメッセージ1件を持つ LLMResult が渡される。graph.py（メイングラフ、
    handwritten/prebuilt いずれの実装でも）・subagent.py（dispatch_agent内部の
    ReAct ループ）の両方が build_model() 経由でモデルを作るため、この1箇所で
    両方のLLM呼び出しを網羅できる。
    """

    def on_llm_end(self, response, **kwargs: Any) -> None:  # noqa: ANN001
        if not logger.isEnabledFor(logging.DEBUG):
            return
        for generation in response.generations:
            for gen in generation:
                message = getattr(gen, "message", None)
                content = getattr(message, "content", None) if message is not None else gen.text
                reasoning = None
                tool_calls = None
                if message is not None:
                    reasoning = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
                    tool_calls = getattr(message, "tool_calls", None)
                logger.debug(
                    "LLM応答: content=%r reasoning_content=%r tool_calls=%r",
                    content,
                    reasoning,
                    tool_calls,
                )


def describe_current_task(now: float | None = None) -> str:
    """現在の asyncio.Task の診断情報を1文字列にまとめる。

    task name, task id, cancelling() 回数, getattr で安全に取り出した
    _must_cancel 私有属性（3.11 の uncancel() がリセットしないため
    孤立キャンセルの直接証拠になりうる）, cancelled() 等を出力する。
    呼び出し元は末尾に [diag] task=... の形でログに付加する。

    イベントループが未起動の同期コンテキストでは "task=NONE thread=<id>"
    を返す。threading.get_ident() を付加することで、同期経路のログでも
    どのスレッド由来か事后に相関を取れるようにする。

    Args:
        now: 経過ms計算用の基準時刻（None なら time.time() を使う）。

    Returns:
        "name=<name> id=<id> cancelling=<n> must_cancel=<mc> cancelled=<c>"
        形式の文字列。task が None の場合は "task=NONE thread=<id>"。
    """
    if now is None:
        now = time.time()
    try:
        task = asyncio.current_task()
    except RuntimeError:
        # イベントループ未起動（同期コンテキスト）
        return f"task=NONE thread={threading.get_ident()}"
    if task is None:
        return f"task=NONE thread={threading.get_ident()}"
    parts = [
        f"name={task.get_name()!r}",
        f"id={id(task)}",
        f"cancelling={task.cancelling()}",
        f"cancelled={task.cancelled()}",
    ]
    # Python 3.11 の asyncio.Task には _must_cancel 私有属性があり、
    # uncancel() 後も残るため「未処理のキャンセル要求」の直接証拠になりうる。
    # getattr で防御的にアクセスする（存在しないバージョンでも安全）。
    mc = getattr(task, "_must_cancel", None)
    if mc is not None:
        parts.append(f"must_cancel={mc}")
    elapsed = now - getattr(task, "_started_at", now)
    parts.append(f"elapsed_ms={elapsed * 1000:.0f}")
    return " ".join(parts)


def _log_first_chunk_latency(elapsed_seconds: float, *, sync: bool, never_received: bool = False) -> None:
    """ストリーム開始から初回チャンク受信までの経過時間を DEBUG ログで記録する。

    常に DEBUG レベルのみで出力する（WARNING 閾値はここに持たせない）。
    正常に初回チャンクを受信した場合は "初回チャンクまで%dms" を出す。
    ストリーム終了までに一度もチャンクを受信しなかった場合は
    "初回チャンクを一度も受信せずストリーム終了" を出す。

    WARNING 閾値ベースの異常判定は、リトライ文脈を知っている app.py 側
    で行う（通常の初回リクエストの長い prefill との誤検知を避けるため）。

    Args:
        elapsed_seconds: ストリーム開始から現在までの経過秒数。
        sync: True なら同期経路(_stream)、False なら非同期経路(_astream)。
        never_received: True の場合、初回チャンク未受信であることを示すログを出力。
    """
    elapsed_ms = elapsed_seconds * 1000
    path = "sync" if sync else "async"
    if never_received:
        logger.debug(
            "初回チャンクを一度も受信せずストリーム終了 [%s, elapsed_ms=%.0f]",
            path,
            elapsed_ms,
        )
    else:
        logger.debug(
            "初回チャンクまで%.0fms [%s]",
            elapsed_ms,
            path,
        )


class ChatLlamaCpp(ChatOpenAI):
    """llama.cpp server（reasoning_format=deepseek）向けの ChatOpenAI 拡張。

    llama-server は Qwen3 系の <think> ブロックを OpenAI 非標準の
    delta.reasoning_content フィールドで返す（reasoning_in_content=false）。
    ChatOpenAI はこの拡張フィールドを読み捨てる仕様（本家 docstring 参照）
    のため、ここで additional_kwargs["reasoning_content"] に拾い上げて
    UI 側（app.py）で思考過程として表示できるようにする。

    あわせて、ストリーミング中の応答（thinking/本文）が反復ループに陥って
    いないかを _ThinkingLoopDetector で監視し、検知したら ThinkingLoopDetected
    を送出して生成を打ち切る（loop_guard_* フィールド、config.ini の
    [thinking_loop_guard] 由来、build_model() が注入する）。
    """

    loop_guard_enabled: bool = True
    loop_guard_window_chars: int = 600
    loop_guard_check_interval_chars: int = 150
    loop_guard_confirm_count: int = 2
    loop_guard_max_history_chars: int = 4000
    loop_guard_match_ratio_threshold: float = 0.2

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        generation_chunk = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return generation_chunk
        choices = chunk.get("choices") or []
        if choices:
            reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
            if reasoning:
                generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk

    def _make_loop_detector(self) -> _ThinkingLoopDetector:
        return _ThinkingLoopDetector(
            self.loop_guard_window_chars,
            self.loop_guard_check_interval_chars,
            self.loop_guard_confirm_count,
            self.loop_guard_max_history_chars,
            self.loop_guard_match_ratio_threshold,
        )

    def _stream(self, *args: Any, **kwargs: Any) -> Any:
        if _LLM_REQUEST_SEMAPHORE is not None:
            logger.warning("同期ストリームパス (_stream) が呼ばれました。この経路はセマフォでガードされません。")
        if not self.loop_guard_enabled:
            yield from super()._stream(*args, **kwargs)
            return
        detector = self._make_loop_detector()
        inner = super()._stream(*args, **kwargs)
        first_chunk_seen = False
        try:
            for chunk in inner:
                if detector.feed(_chunk_delta_text(chunk)):
                    snippet = detector.snippet()
                    logger.warning(
                        "LLM応答のループを検知したため生成を打ち切ります（直近テキスト: %r）",
                        snippet,
                    )
                    raise ThinkingLoopDetected("LLM応答が反復ループに陥ったため打ち切りました", snippet=snippet)
                if not first_chunk_seen:
                    first_chunk_seen = True
                    _log_first_chunk_latency(time.time(), sync=True)
                yield chunk
        finally:
            try:
                inner.close()
            except Exception:  # noqa: BLE001 - 過剰ログを避ける
                logger.debug("ストリームの後始末(close)中に例外が発生しました", exc_info=True)
            if not first_chunk_seen:
                _log_first_chunk_latency(time.time(), sync=True, never_received=True)

    async def _astream(self, *args: Any, **kwargs: Any) -> Any:
        """llama-server への HTTP リクエストを _LLM_REQUEST_SEMAPHORE でガードする。

        本体処理（ループ検知・finally節）は _astream_guarded に分離し、
        この関数自体はセマフォ取得の薄いラッパーとして振る舞う。
        """
        sem = _LLM_REQUEST_SEMAPHORE
        if sem is None:
            async for chunk in self._astream_guarded(*args, **kwargs):
                yield chunk
            return
        if sem.locked():
            logger.debug("空きスロットが無いため待機します（llm concurrent guard）")
        async with sem:
            async for chunk in self._astream_guarded(*args, **kwargs):
                yield chunk

    async def _astream_guarded(self, *args: Any, **kwargs: Any) -> Any:
        """_astream の本体（ループ検知・finally節）。

        _astream からセマフォの内側で呼ばれる。
        """
        if not self.loop_guard_enabled:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
            return
        detector = self._make_loop_detector()
        agen = super()._astream(*args, **kwargs)
        # finally節でaclose失敗時にclient_brokenを立てるため、raiseした
        # ThinkingLoopDetectedへの参照をローカル変数として保持しておく
        # （finally節からraise済みの例外オブジェクトへ直接アクセスする
        # 標準の手段が無いため、参照を生かしておく必要がある）。
        loop_exc: ThinkingLoopDetected | None = None
        diag_start = None
        first_chunk_seen = False
        first_chunk_at: float | None = None
        try:
            async for chunk in agen:
                if detector.feed(_chunk_delta_text(chunk)):
                    snippet = detector.snippet()
                    diag = describe_current_task(diag_start)
                    logger.warning(
                        "LLM応答のループを検知したため生成を打ち切ります" "（直近テキスト: %r） [%s]",
                        snippet,
                        diag,
                    )
                    loop_exc = ThinkingLoopDetected("LLM応答が反復ループに陥ったため打ち切りました", snippet=snippet)
                    raise loop_exc
                if not first_chunk_seen:
                    first_chunk_seen = True
                    first_chunk_at = time.time()
                yield chunk
                # detector.feed()は同期的でCPUバウンドな処理（difflibでの
                # 文字列比較）を含む。Chainlitは単一プロセス・単一イベント
                # ループで全セッションを捌くため、ここで一度制御を返して
                # おかないと、他セッションのソケットイベント（承認ボタンの
                # クリック等）の処理が後回しにされ得る。
                await asyncio.sleep(0)
        finally:
            # 初回チャンク受信までの待ち時間を記録（acloseより前）。
            if first_chunk_at is not None:
                _log_first_chunk_latency(time.time() - first_chunk_at, sync=False)
            elif not first_chunk_seen:
                _log_first_chunk_latency(0.0, sync=False, never_received=True)
            try:
                # 停止ボタン等でこの _astream 自体が既にキャンセル済みの
                # タスク内にいる場合、ここでの await も即座に再キャンセル
                # されうる（asyncio の一般的な挙動）。asyncio.shield() で
                # 別taskとして保護すると、cancel scope が前提とする
                # 「開いたのと同じtaskで閉じる」制約を破るため採用しない。
                # 従来 asyncio.wait_for() でタイムアウトのみ付与していたが、
                # wait_for は渡されたコルーチンを ensure_future で別task に
                # ラップして実行するため、shield と同型の task 不一致を
                # 引き起こしうる（agen.aclose() が巻き戻す先の anyio
                # CancelScope は「開いたのと同じtaskでしか閉じられない」
                # 制約を持つ）。asyncio.timeout() は現在のtaskに直接
                # タイムアウトキャンセルを注入し、新規taskを生成しないため、
                # 同一task制約を破らずにタイムアウトを維持できる。
                diag_start = time.time()
                async with asyncio.timeout(5.0):
                    await agen.aclose()
                diag_elapsed = (time.time() - diag_start) * 1000
                logger.debug(
                    "ストリームの後始末(aclose)完了 [%s, elapsed_ms=%.0f]",
                    describe_current_task(diag_start),
                    diag_elapsed,
                )
            except TimeoutError:
                diag_elapsed = (time.time() - diag_start) * 1000
                if loop_exc is not None:
                    loop_exc.client_broken = True
                    logger.warning(
                        "ストリームの後始末(aclose)がタイムアウトしました"
                        "（5秒）。httpx.AsyncClientが壊れている可能性が高い。"
                        "呼び出し元でのリトライ前にクライアントの再生成が必要です"
                        " [%s, elapsed_ms=%.0f]",
                        describe_current_task(diag_start),
                        diag_elapsed,
                    )
                else:
                    logger.debug(
                        "ストリームの後始末(aclose)がタイムアウトしました" "（5秒） [%s, elapsed_ms=%.0f]",
                        describe_current_task(diag_start),
                        diag_elapsed,
                    )
            except Exception:
                diag_elapsed = (time.time() - diag_start) * 1000
                if loop_exc is not None:
                    loop_exc.client_broken = True
                    logger.warning(
                        "ストリームの後始末(aclose)に失敗しました。"
                        "httpx.AsyncClientが壊れている可能性があるため、"
                        "呼び出し元でのリトライ前にクライアントの再生成が必要です"
                        " [%s, elapsed_ms=%.0f]",
                        describe_current_task(diag_start),
                        diag_elapsed,
                    )
                else:
                    logger.debug(
                        "ストリームの後始末中に例外が発生しました [%s, elapsed_ms=%.0f]",
                        describe_current_task(diag_start),
                        diag_elapsed,
                    )

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        """llama-server への HTTP リクエストを _LLM_REQUEST_SEMAPHORE でガードする。

        現状は stream=True 常に設定されているため到達しないが、将来
        stream=False が明示指定された場合の保険として追加する。
        """
        sem = _LLM_REQUEST_SEMAPHORE
        if sem is None:
            return await super()._agenerate(*args, **kwargs)
        if sem.locked():
            logger.debug("空きスロットが無いため待機します（llm concurrent guard）")
        async with sem:
            return await super()._agenerate(*args, **kwargs)

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        """同期生成パスはセマフォ二重管理を行わず、警告ログのみ出力する。

        項目2の修正後は呼び出し箇所がなくなるため、想定外に到達した場合に
        気づけるよう警告ログを出力して親の処理をそのまま返す。
        """
        if _LLM_REQUEST_SEMAPHORE is not None:
            logger.warning("同期生成パス (_generate) が呼ばれました。" "この経路はセマフォでガードされません。")
        return super()._generate(*args, **kwargs)


# --- LLM接続先の簡易ルーティング ([llm].main_url / sub_url が複数件のとき) ---
# _LLM_REQUEST_SEMAPHORE 等と同じく、プロセス全体で共有するグローバル可変
# 状態（ロック不要。asyncio単一イベントループ内での逐次的な読み書きのみを
# 想定）。ただし _LAST_SELECTED_INDEX だけは role 単独ではなく
# (role, セッションID) をキーにする。複数タブ・複数ユーザーが同時に
# 接続する運用では、role単独キーだと「直近グローバルに選ばれたindex」が
# 別セッションの選択で上書きされてしまい、mark_last_endpoint_failed() が
# 実際には無関係な接続先をクールダウンしてしまう恐れがあるため
# （priority_failover を複数接続先・並行セッションで使う想定への対応）。
# _CURRENT_SESSION_ID は set_current_session(thread_id) が会話単位で設定する
# contextvar で、build_model() 呼び出し時と、後続の通信エラー検知時
# （app.py の except LLM_CONNECTION_ERRORS）の両方で同一会話中は同じ値になる。
_ROUND_ROBIN_COUNTERS: dict[str, int] = {}
_LAST_SELECTED_INDEX: dict[tuple[str, str], int] = {}
_ENDPOINT_COOLDOWN_UNTIL: dict[tuple[str, int], float] = {}
# priority_failover 戦略で、通信エラーを検知した接続先を一時的に避ける秒数。
_ENDPOINT_FAILOVER_COOLDOWN_SECONDS = 60.0


def _select_endpoint(role: str, endpoints: tuple[LLMEndpoint, ...], strategy: str) -> LLMEndpoint:
    """config.ini [llm].main_routing_strategy / sub_routing_strategy に従って接続先を1つ選ぶ。

    Args:
        role: "main" または "sub"（ルーティング状態を役割ごとに分けるためのキー）。
        endpoints: config.main_endpoints または config.sub_endpoints。
        strategy: config.main_routing_strategy または config.sub_routing_strategy
            （"round_robin"/"random"/"priority_failover"/"sticky" のいずれか）。

    Returns:
        選ばれた LLMEndpoint。要素数が1件の場合は strategy に関わらず常にそれを返す。
    """
    # sticky のハッシュキー、および mark_last_endpoint_failed() が後で
    # 引けるようにするための「選択時点の会話ID」。未設定（サブエージェント
    # 経由でset_current_session未実行など）なら空文字として扱う。
    session_id = _CURRENT_SESSION_ID.get() or ""
    state_key = (role, session_id)

    if len(endpoints) == 1:
        _LAST_SELECTED_INDEX[state_key] = 0
        return endpoints[0]

    if strategy == "random":
        index = random.randrange(len(endpoints))
    elif strategy == "sticky":
        # 会話（thread_id）単位で常に同じ接続先を選ぶ。llama.cpp はプロンプト
        # 先頭が同じだとKVキャッシュが効くため、同一会話を毎回同じサーバーに
        # 固定すると有利。
        index = zlib.crc32(session_id.encode("utf-8")) % len(endpoints)
    elif strategy == "priority_failover":
        # 先頭から順に見て、クールダウン中でない最初の接続先を使う。
        # 全滅時は安全側として先頭(0)へフォールバックする。
        now = time.time()
        index = 0
        for i in range(len(endpoints)):
            if _ENDPOINT_COOLDOWN_UNTIL.get((role, i), 0.0) <= now:
                index = i
                break
    else:  # "round_robin"（既定）
        index = _ROUND_ROBIN_COUNTERS.get(role, 0) % len(endpoints)
        _ROUND_ROBIN_COUNTERS[role] = index + 1

    _LAST_SELECTED_INDEX[state_key] = index
    return endpoints[index]


def mark_last_endpoint_failed(role: str) -> None:
    """直近この会話の build_model(config, role) が選んだ接続先を一時的にクールダウンする。

    priority_failover 戦略専用のフィードバックフック。通信エラーを検知した
    呼び出し元（現状は app.py の except LLM_CONNECTION_ERRORS）が、エラーを
    検知した会話のコンテキスト内（set_current_session(thread_id) 済みの状態）
    でここを呼ぶと、次回以降の build_model() 呼び出しで
    _ENDPOINT_FAILOVER_COOLDOWN_SECONDS秒間その接続先を避け、次点の接続先へ
    切り替わる（round_robin/random/sticky 戦略では index は記録されるが
    参照されないため実質無視される）。

    _LAST_SELECTED_INDEX は (role, セッションID) 単位で管理しているため、
    複数タブ・複数ユーザーが同時に接続していても、他セッションの選択に
    巻き込まれず「このセッションが直近実際に使っていた接続先」だけを
    クールダウンできる。

    Args:
        role: "main" または "sub"。
    """
    session_id = _CURRENT_SESSION_ID.get() or ""
    index = _LAST_SELECTED_INDEX.get((role, session_id))
    if index is None:
        return
    _ENDPOINT_COOLDOWN_UNTIL[(role, index)] = time.time() + _ENDPOINT_FAILOVER_COOLDOWN_SECONDS
    logger.warning(
        "LLM接続失敗を検知したため接続先を一時的に避けます（role=%s, index=%d, %.0f秒間）",
        role,
        index,
        _ENDPOINT_FAILOVER_COOLDOWN_SECONDS,
    )


def build_model(config: Config, role: Literal["main", "sub"] = "main") -> ChatOpenAI:
    """llama.cpp server の OpenAI 互換エンドポイントに繋ぐ ChatOpenAI を作る。

    Ollama 固有の API・ライブラリは使わず、langchain-openai の ChatOpenAI
    を llama.cpp の OpenAI 互換サーバーへ向けて構築する。

    top_p / max_tokens / frequency_penalty / presence_penalty は ChatOpenAI が
    ネイティブに持つフィールドとしてそのまま渡す。top_k / repeat_penalty /
    DRY サンプラー系（dry_*）は OpenAI 標準 API には無い llama.cpp 拡張
    パラメータのため、model_kwargs では openai SDK の Completions.create() が
    未知のキーワード引数として例外を起こしてしまう。extra_body（openai SDK の
    正式な予約引数）に載せることで、HTTP リクエストボディのトップレベルに
    マージされ llama-server に届く。

    Args:
        config: main_endpoints/main_routing_strategy（role="main"時）または
            sub_endpoints/sub_routing_strategy（role="sub"時）/ temperature /
            top_p / top_k / repeat_penalty / frequency_penalty / presence_penalty /
            max_tokens / dry_multiplier / dry_base / dry_allowed_length /
            dry_penalty_last_n / dry_sequence_breakers / enable_thinking / track_token_usage /
            request_timeout_seconds / thinking_loop_guard_* を含むアプリ設定。
            未指定（None）の項目はリクエストに含めず llama-server 側の
            デフォルトに委ねる。thinking_loop_guard_* は ChatLlamaCpp の
            loop_guard_* フィールドへ渡され、ストリーミング中の反復ループ検知
            （ThinkingLoopDetected）に使う。
        role: "main"（メインエージェント、既定）または "sub"（サブエージェント
            ／dispatch_agent）。どちらの接続先リスト・ルーティング戦略を
            使うかを切り替える（_select_endpoint() 参照）。

    Returns:
        streaming=True で構築された ChatLlamaCpp インスタンス（未 bind_tools）。
        config.track_token_usage が True の場合、stream_usage=True も
        設定される（base_url を指定した ChatOpenAI は既定で usage 取得が
        無効化されるため、明示的に有効化する必要がある）。

    Notes:
        内部で生成する httpx.AsyncClient は、呼び出し時点の
        set_current_session() で設定されたセッションIDに紐づけて
        _active_async_clients へ登録される。これにより
        aclose_active_llm_clients(session_id) が自セッション分だけを
        強制クローズできる。
    """
    endpoints = config.main_endpoints if role == "main" else config.sub_endpoints
    routing_strategy = config.main_routing_strategy if role == "main" else config.sub_routing_strategy
    endpoint = _select_endpoint(role, endpoints, routing_strategy)

    extra_body: dict[str, Any] = {}
    if config.top_k is not None:
        extra_body["top_k"] = config.top_k
    if config.repeat_penalty is not None:
        extra_body["repeat_penalty"] = config.repeat_penalty
    if config.dry_multiplier is not None:
        extra_body["dry_multiplier"] = config.dry_multiplier
    if config.dry_base is not None:
        extra_body["dry_base"] = config.dry_base
    if config.dry_allowed_length is not None:
        extra_body["dry_allowed_length"] = config.dry_allowed_length
    if config.dry_penalty_last_n is not None:
        extra_body["dry_penalty_last_n"] = config.dry_penalty_last_n
    if config.dry_sequence_breakers is not None:
        extra_body["dry_sequence_breakers"] = config.dry_sequence_breakers
    if config.enable_thinking is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}

    # keep-alive接続を無効化する: 思考ループ検知時にストリームを正しく
    # クローズできなかった場合（cancel scopeのtask不一致等）、壊れた接続が
    # コネクションプールに残り続け、次のリクエストがそれを再利用して
    # 応答ヘッダー待ちのまま無限にフリーズすることがある（実際に発生した
    # インシデントより）。ローカルのllama-server相手のためTCP再確立の
    # コストは無視できるレベルなので、毎回新規接続にして安全側に倒す。
    no_keepalive_limits = httpx.Limits(max_keepalive_connections=0)

    # 応答待ちタイムアウトを明示する: httpxのデフォルトは無制限のため、
    # 上記のkeep-alive無効化だけではカバーできないケース（新規接続を
    # 張った後でも、サーバー側に残った旧ストリームの影響で応答ヘッダーが
    # 返らない場合）に、クライアント側が無期限に待ち続けてしまう
    # （本番incident・2026-07-20: ThinkingLoopDetected後のaclose失敗で
    # クライアント内部状態が壊れ、次のリトライが応答ヘッダー待ちのまま
    # 7分11秒間ハングした事例を確認）。read/write/poolは
    # config.request_timeout_seconds（config.ini [llm].request_timeout_seconds）
    # を使う。read timeoutはチャンク間のアイドル時間の上限であり、生成が
    # 続く限り新しいチャンクのたびにリセットされるため、長い思考・長い
    # max_tokens生成そのものは妨げない。connectのみ短い固定値にし、
    # llama-server自体が未起動等の接続失敗は素早く検知する（ローカル
    # サーバー相手のため通常は一瞬で確立できるはず）。
    llm_timeout = httpx.Timeout(config.request_timeout_seconds, connect=10.0)
    async_client = httpx.AsyncClient(limits=no_keepalive_limits, timeout=llm_timeout)
    session_id = _CURRENT_SESSION_ID.get()
    _active_async_clients.setdefault(session_id, weakref.WeakSet()).add(async_client)

    # なぜ request_timeout を明示する必要があるか:
    # langchain_openai の ChatOpenAI は内部属性 self.timeout を持っており、
    # 既定値は None。openai SDK は毎リクエスト build_request(timeout=...) で
    # self.timeout を httpx リクエストに渡す。self.timeout が None だと、
    # httpx.AsyncClient に設定した httpx.Timeout が「無視される」のではなく、
    # openai SDK 側で「明示的な無制限指定」として扱われ、connect/read/write/pool
    # が全て無制限になる（実ログで connect_tcp.started ... timeout=None を
    # 確認済み）。build_model() は httpx.Timeout オブジェクトを llm_timeout として
    # 作っているが、ChatOpenAI.request_timeout を未設定だったため、この上書きを
    # 止めるには ChatLlamaCpp 構築時に request_timeout=llm_timeout を渡す必要が
    # ある（2026-07-20, 2026-07-21, 2026-07-28 の各 incident で確認された
    # 構造的な欠陥）。
    return ChatLlamaCpp(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,  # llama.cpp は認証不要のためダミー値
        model=endpoint.model,
        temperature=config.temperature,
        top_p=config.top_p,
        max_tokens=config.max_tokens,
        frequency_penalty=config.frequency_penalty,
        presence_penalty=config.presence_penalty,
        extra_body=extra_body or None,
        http_client=httpx.Client(limits=no_keepalive_limits, timeout=llm_timeout),
        http_async_client=async_client,
        request_timeout=llm_timeout,
        # openai SDKは既定でmax_retries=2の自動リトライを持つが、これは
        # request_timeout_seconds分のハングを我々のコードから見えないまま
        # 最大3倍まで増幅させてしまう（本番incident・2026-07-20:
        # ThinkingLoopDetected後のリトライがcancel scope破損済みクライアント
        # 上でハングした際、SDK内部リトライにより8分近く沈黙し、
        # thinking_loop_guard/client_broken側の回復ロジックが発動する前に
        # 停止ボタンでの強制終了が必要になった）。1回の試行を確実に
        # request_timeout_seconds以内で失敗させ、アプリ側の
        # リトライ・クライアント再構築ロジックに委ねるため無効化する。
        max_retries=0,
        streaming=True,
        stream_usage=config.track_token_usage,
        stream_chunk_timeout=config.stream_chunk_timeout_seconds,
        callbacks=[_DebugResponseLogger()],
        loop_guard_enabled=config.thinking_loop_guard_enabled,
        loop_guard_window_chars=config.thinking_loop_guard_window_chars,
        loop_guard_check_interval_chars=config.thinking_loop_guard_check_interval_chars,
        loop_guard_confirm_count=config.thinking_loop_guard_confirm_count,
        loop_guard_max_history_chars=config.thinking_loop_guard_max_history_chars,
        loop_guard_match_ratio_threshold=config.thinking_loop_guard_match_ratio_threshold,
    )
