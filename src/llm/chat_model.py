"""ChatLlamaCpp（llama.cpp向けChatOpenAI拡張）と build_model()（組み立て入口）。

_LLM_REQUEST_SEMAPHORE は ChatLlamaCpp._stream/_astream/_agenerate が毎呼び出し
読み取る可変グローバルで、init_llm_concurrency() が再代入する。再代入を
他モジュールから見えるようにするため（`from module import name` は再代入を
追従しない）、この2つは同じファイルに置く。
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from typing import Any, Literal

import httpx
from langchain_openai import ChatOpenAI

from ..config import Config
from .diagnostics import (
    _DebugResponseLogger,
    _log_first_chunk_latency,
    _RequestEndpointLogger,
    describe_current_task,
)
from .loop_guard import ThinkingLoopDetected, _chunk_delta_text, _ThinkingLoopDetector
from .routing import _active_async_clients, _CURRENT_SESSION_ID, _select_endpoint

logger = logging.getLogger(__name__)

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
        stream_started_at = time.time()
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
                    _log_first_chunk_latency(time.time() - stream_started_at, sync=True)
                yield chunk
        finally:
            try:
                inner.close()
            except Exception:  # noqa: BLE001 - 過剰ログを避ける
                logger.debug("ストリームの後始末(close)中に例外が発生しました", exc_info=True)
            if not first_chunk_seen:
                _log_first_chunk_latency(time.time() - stream_started_at, sync=True, never_received=True)

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
        stream_started_at = time.time()
        agen = super()._astream(*args, **kwargs)
        # finally節でaclose失敗時にclient_brokenを立てるため、raiseした
        # ThinkingLoopDetectedへの参照をローカル変数として保持しておく
        # （finally節からraise済みの例外オブジェクトへ直接アクセスする
        # 標準の手段が無いため、参照を生かしておく必要がある）。
        loop_exc: ThinkingLoopDetected | None = None
        diag_start = None
        first_chunk_seen = False
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
                    # ストリーム開始からこの最初のチャンク受信までの経過時間
                    # （TTFT）をこの場で記録する。finally節まで遅延させると
                    # 「ストリーム終了時刻 - 最初のチャンク受信時刻」という
                    # 別の値（実質的な生成時間）を記録してしまうバグになる。
                    _log_first_chunk_latency(time.time() - stream_started_at, sync=False)
                yield chunk
                # detector.feed()は同期的でCPUバウンドな処理（difflibでの
                # 文字列比較）を含む。Chainlitは単一プロセス・単一イベント
                # ループで全セッションを捌くため、ここで一度制御を返して
                # おかないと、他セッションのソケットイベント（承認ボタンの
                # クリック等）の処理が後回しにされ得る。
                await asyncio.sleep(0)
        finally:
            if not first_chunk_seen:
                _log_first_chunk_latency(time.time() - stream_started_at, sync=False, never_received=True)
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


async def build_model(
    config: Config,
    role: Literal["main", "sub"] = "main",
    *,
    wait_when_busy: bool = True,
) -> ChatOpenAI:
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
            dry_penalty_last_n / dry_sequence_breakers / enable_thinking /
            reasoning_format / reasoning_budget / reasoning_budget_message /
            track_token_usage / request_timeout_seconds / thinking_loop_guard_*
            を含むアプリ設定。
            未指定（None）の項目はリクエストに含めず llama-server 側の
            デフォルトに委ねる。thinking_loop_guard_* は ChatLlamaCpp の
            loop_guard_* フィールドへ渡され、ストリーミング中の反復ループ検知
            （ThinkingLoopDetected）に使う。
        role: "main"（メインエージェント、既定）または "sub"（サブエージェント
            ／dispatch_agent）。どちらの接続先リスト・ルーティング戦略を
            使うかを切り替える（_select_endpoint() 参照）。ただし
            config.sub_endpoints_inherit_main が True（[llm].sub_url 未指定）
            の場合、role="sub" は sub_routing_strategy を使わず、同一
            セッションでメインエージェントが直近実際に使った接続先を
            そのまま継承する。
        wait_when_busy: main_routing_strategy/sub_routing_strategy=round_robin
            かつ provider="llama_cpp" の接続先が全てビジーだった場合、空きが
            出るまで待つか（True、既定）。False の場合は待たずにフェイル
            セーフ選択する。build_model() はグラフ構築時にしか呼ばれず
            （実際の生成のたびには呼ばれない）、構築直後に生成が続くとは
            限らない呼び出し元（スレッド再開・新規セッション開始等、直後に
            メッセージ送信するとは限らない操作）でTrueのままだと、無関係な
            他セッションの生成中に長時間ブロックしてしまう
            （2026-08-26 ユーザー報告: 生成中に会話履歴を切り替えると
            ロードが極端に遅くなる。app.py の on_chat_start/on_chat_resume
            はFalseを渡す）。直後に生成が確定している呼び出し元
            （ThinkingLoopDetected・接続エラー時の再構築など）は既定のTrue
            のままにする。

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
    inherit_from_role = "main" if role == "sub" and config.sub_endpoints_inherit_main else None
    endpoint = await _select_endpoint(
        role,
        endpoints,
        routing_strategy,
        inherit_from_role=inherit_from_role,
        probe_timeout_seconds=config.round_robin_slots_probe_timeout_seconds,
        busy_poll_interval_seconds=config.round_robin_busy_poll_interval_seconds,
        wait_when_busy=wait_when_busy,
    )
    logger.info(
        "build_model()呼び出し: role=%s routing_strategy=%s session_id=%r -> "
        "base_url=%s model=%s [diag] %s",
        role,
        routing_strategy,
        _CURRENT_SESSION_ID.get(),
        endpoint.base_url,
        endpoint.model,
        describe_current_task(),
    )

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
    if config.reasoning_format is not None:
        extra_body["reasoning_format"] = config.reasoning_format
    if config.reasoning_budget is not None:
        extra_body["reasoning_budget"] = config.reasoning_budget
    if config.reasoning_budget_message is not None:
        extra_body["reasoning_budget_message"] = config.reasoning_budget_message

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
        callbacks=[
            _DebugResponseLogger(),
            _RequestEndpointLogger(role, session_id or "", endpoint.base_url, endpoint.model),
        ],
        loop_guard_enabled=config.thinking_loop_guard_enabled,
        loop_guard_window_chars=config.thinking_loop_guard_window_chars,
        loop_guard_check_interval_chars=config.thinking_loop_guard_check_interval_chars,
        loop_guard_confirm_count=config.thinking_loop_guard_confirm_count,
        loop_guard_max_history_chars=config.thinking_loop_guard_max_history_chars,
        loop_guard_match_ratio_threshold=config.thinking_loop_guard_match_ratio_threshold,
    )
