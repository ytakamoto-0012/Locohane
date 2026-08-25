"""LLMセッション管理・httpxクライアントのライフサイクル・接続先ルーティング。

[llm].main_url / sub_url が複数件のときの選択ロジック（round_robin / random /
priority_failover）と、そのために必要なセッションID管理・
httpx.AsyncClient のセッション別ブックキーピングをまとめる。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import time
import weakref
from urllib.parse import urlsplit

import httpx
from langchain_openai import ChatOpenAI

from ..config import LLMEndpoint

logger = logging.getLogger(__name__)


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

# _CURRENT_SESSION_ID（thread_id）とは別に、実際のブラウザタブ／接続
# （cl.context.session.id）単位の識別子を保持する。_LAST_SELECTED_INDEX の
# キーに使う（下記参照）。_CURRENT_SESSION_ID をそのまま使わない理由:
# 同じ thread_id を複数タブで同時に開く操作（同じ会話を別タブで開く／
# _stop_thread_generating で他タブから停止する等）はこのアプリが正式に
# サポートしており、_active_async_clients はまさにその複数タブ間で
# 意図的に共有する必要があるため thread_id をキーにしているが（上記
# docstring参照）、_LAST_SELECTED_INDEX（「このタブの直近の接続先選択」）
# まで thread_id 単位で共有してしまうと、同じスレッドを開いた別タブの
# build_model() 呼び出しが割り込んで上書きし、role="sub" の
# inherit_from_role 継承が本来とは別タブの接続先を拾ってしまう恐れがある
# （2026-08-26 監査で発見）。tab_id 未指定（evals/ の評価ハーネス等）の
# 場合は従来通り session_id をそのまま使う。
_CURRENT_TAB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_tab_id", default=None)


def set_current_session(session_id: str | None, *, tab_id: str | None = None) -> None:
    """これ以降このタスク（及びその子タスク）で build_model() が生成する
    httpx.AsyncClient を、どのセッションに紐づけて登録するかを設定する。

    app.py の @cl.on_chat_start・@cl.on_message 冒頭、および _rebuild_graph()
    から呼ぶ。各呼び出しは新しい asyncio.Task（＝ contextvars の独立した
    コピー）の中で行われるため、他タブへ値が漏れる心配はなく、明示的な
    reset も不要（次にこの関数が呼ばれるまで値を保持するだけでよい）。

    Args:
        session_id: このセッションの thread_id（cl.user_session の
            "thread_id"）。_active_async_clients のキーに使う（複数タブで
            同じ thread_id を開いた場合も意図的に共有する。上記docstring
            参照）。
        tab_id: 実際のブラウザタブ／接続の識別子（app.py から渡す場合は
            cl.context.session.id）。_LAST_SELECTED_INDEX のキーに使う
            （_CURRENT_TAB_ID docstring参照）。省略時は session_id を
            そのまま使う（tab_id の概念が無い呼び出し元向けの後方互換）。
    """
    _CURRENT_SESSION_ID.set(session_id)
    _CURRENT_TAB_ID.set(tab_id if tab_id is not None else session_id)


def get_current_session() -> str | None:
    """set_current_session() で設定された現在のセッションID（thread_id）を返す。

    src/tools.py がセッション毎の並列数ガード（_TOOL_CALL_SEMAPHORES /
    _DISPATCH_AGENT_SEMAPHORES）のキーとして流用する。未設定（evals/ の
    評価ハーネス等、Chainlitセッションを持たない呼び出し元）なら None。
    """
    return _CURRENT_SESSION_ID.get()


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


async def aclose_model_client(model: ChatOpenAI) -> None:
    """指定した1つのモデルインスタンスに紐づく httpx.AsyncClient だけを強制クローズする。

    aclose_active_llm_clients(session_id) はセッション内の全クライアントを
    一括で閉じるため、dispatch_agent の並列サブエージェントやメイングラフが
    同じセッションで同時に別のクライアントを使用中だと巻き添えで
    "Cannot send a request, as the client has been closed" を招く
    （src/subagent.py の _invoke_with_loop_retry docstring 参照）。

    要約専用モデル（src/context_compaction.py の maybe_compact 等、
    build_model() を都度その場だけで使い捨てる呼び出し元）のように、
    「このモデルインスタンス1つの接続だけを確実に切断したいが、同じ
    セッションの他のクライアントには触れたくない」場合はこちらを使う。

    build_model() は ChatLlamaCpp(http_async_client=async_client) という
    形で httpx.AsyncClient を明示的に渡しており、langchain_openai は
    それをそのまま model.root_async_client._client として保持する
    （openai.AsyncOpenAI.close() が呼ぶのと同じクライアント。実測で
    `model.root_async_client._client is async_client` を確認済み）。
    ライブラリの非公開属性に依存するため、将来のバージョンアップで
    属性名が変わった場合に備えて例外は握りつぶし、ログのみに留める
    （force closeできなくても、build_model()側でリトライ用に新しい
    クライアントを都度生成する設計のため、呼び出し元のリトライ自体は
    引き続き機能する）。

    Args:
        model: build_model() が返した ChatOpenAI（ChatLlamaCpp）インスタンス。
    """
    client = getattr(getattr(model, "root_async_client", None), "_client", None)
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 - 1回のクローズ失敗でリトライ自体を止めない
        logger.debug("モデル専用クライアントのクローズ中に例外が発生しました", exc_info=True)


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


async def _probe_llama_cpp_slots_available(base_url: str, timeout_seconds: float) -> bool | None:
    """llama.cpp server の管理API GET /slots を叩き、空きスロットがあるか確認する。

    provider="llama_cpp" の接続先のみが対象（llama-server起動時に --slots が
    有効な場合のみ機能する）。base_url は "http://host:port/v1" のように
    OpenAI互換パス（/v1）付きの形式を想定しているが、/slots はそのパス配下
    ではなくサーバールート直下にあるため、scheme+netlocだけを取り出して
    組み立て直す。

    通信エラー・タイムアウト・想定外のレスポンス形式など、確実な判定が
    できない場合は必ず None を返す（例外は外へ伝播させない）。呼び出し元
    （_select_round_robin_endpoint）は None を「わからない＝空きありとみなす」
    フェイルセーフとして扱う（round_robinは元々スロット確認なしで即座に
    選んでいたため、確認できない場合は従来動作に寄せて選択を止めない）。

    Args:
        base_url: 接続先の base_url（LLMEndpoint.base_url）。
        timeout_seconds: リクエスト全体のタイムアウト秒数（[llm].
            round_robin_slots_probe_timeout_seconds）。

    Returns:
        True: 少なくとも1スロットが待機中（空きあり）。
        False: 全スロットが生成中（空きなし）。
        None: 確認できなかった（通信エラー・想定外のレスポンス形式等）。
    """
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    slots_url = f"{parsed.scheme}://{parsed.netloc}/slots"
    timeout = httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(slots_url)
            response.raise_for_status()
            slots = response.json()
    except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
        logger.debug("GET %s の確認に失敗しました（空きありとみなします）", slots_url, exc_info=exc)
        return None
    if not isinstance(slots, list):
        return None
    try:
        return any(not slot.get("is_processing", True) for slot in slots)
    except AttributeError:
        return None


def _endpoint_available_now(endpoint: LLMEndpoint) -> bool:
    """LLMEndpoint.start/end（使用可能時間帯、時間単位・0〜24）に基づき、現在時刻が範囲内かを判定する。

    start/end が両方 None（未指定）の接続先は常に True。start > end の場合は
    日をまたぐ範囲（例: start=22, end=6 なら22:00〜翌6:00）として扱う。

    Args:
        endpoint: 判定対象の接続先。

    Returns:
        現在時刻（ローカルタイム）が使用可能時間帯内なら True。
    """
    if endpoint.start is None and endpoint.end is None:
        return True
    now = time.localtime()
    now_hour = now.tm_hour + now.tm_min / 60.0 + now.tm_sec / 3600.0
    start = endpoint.start if endpoint.start is not None else 0.0
    end = endpoint.end if endpoint.end is not None else 24.0
    if start <= end:
        return start <= now_hour < end
    return now_hour >= start or now_hour < end


def _compute_eligible_indices(endpoints: tuple[LLMEndpoint, ...]) -> list[int]:
    """endpoints のうち現在時刻が start/end の使用可能時間帯内のものだけに絞り込む。

    _endpoint_available_now 参照。絞り込み結果が空になった場合は全件へ
    フォールバックする（config.py の _as_llm_endpoints が最低1件の常時
    使用可能な接続先を要求しているため通常は起こらないが、念のため）。

    Args:
        endpoints: 絞り込み対象の接続先タプル。

    Returns:
        使用可能な接続先の index 一覧（1件以上）。
    """
    eligible = [i for i, e in enumerate(endpoints) if _endpoint_available_now(e)]
    return eligible if eligible else list(range(len(endpoints)))


async def _select_round_robin_endpoint(
    role: str,
    endpoints: tuple[LLMEndpoint, ...],
    *,
    probe_timeout_seconds: float,
    busy_poll_interval_seconds: float,
    wait_when_busy: bool = True,
) -> int:
    """round_robin戦略の本体。呼び出しごとに順番を回しつつ、provider="llama_cpp"
    の接続先だけは選ぶ前に GET /slots で空きスロットの有無を確認する。

    空きが無い（全スロット生成中）候補はスキップして次点へ回し、候補を一巡
    しても1件も空きが見つからなければ、wait_when_busy=True（既定）なら
    busy_poll_interval_seconds 秒待ってから再試行する（空きが出るまで無期限に
    待機する）。wait_when_busy=False なら待たずに次点の候補（order[0]）を
    暫定選択して即座に返す（呼び出し元が「今すぐ何らかの接続先が確定すれば
    よく、生成の予定が無い/未確定の操作」の場合に使う。build_model() 参照）。
    provider="openai_compatible"の接続先は確認を行わず、従来通り即座に選ぶ
    （空き状況が分からないサーバー種別のため）。

    候補（時間帯で使用可能な接続先）は待機の周回ごとに _compute_eligible_indices
    で再計算する。空き待ちが長引いて start/end の境界をまたいでも、次の周回では
    最新の使用可能時間帯に追従する（呼び出し時点の候補一覧を固定してしまうと、
    待機中に使用可能時間帯を外れた接続先を待ち続けたり、新しく使用可能になった
    接続先を見逃したりするため）。

    Args:
        role: "main" または "sub"。
        endpoints: 選択対象の接続先タプル。
        probe_timeout_seconds: [llm].round_robin_slots_probe_timeout_seconds。
        busy_poll_interval_seconds: [llm].round_robin_busy_poll_interval_seconds。
        wait_when_busy: False の場合、全候補ビジー時に待機せず即座に
            フェイルセーフ選択する。

    Returns:
        選ばれた接続先の index（endpoints に対する）。
    """
    attempt = 0
    while True:
        eligible_indices = _compute_eligible_indices(endpoints)
        counter = _ROUND_ROBIN_COUNTERS.get(role, 0)
        _ROUND_ROBIN_COUNTERS[role] = counter + 1
        order = [eligible_indices[(counter + offset) % len(eligible_indices)] for offset in range(len(eligible_indices))]
        for index in order:
            endpoint = endpoints[index]
            if endpoint.provider != "llama_cpp":
                logger.info(
                    "接続先選択[round_robin]: role=%s order=%s -> index=%d "
                    "(理由: provider=%s のため空き確認なしで選択)",
                    role,
                    order,
                    index,
                    endpoint.provider,
                )
                return index
            available = await _probe_llama_cpp_slots_available(endpoint.base_url, probe_timeout_seconds)
            if available is not False:
                logger.info(
                    "接続先選択[round_robin]: role=%s order=%s -> index=%d base_url=%s "
                    "(理由: GET /slots 確認結果=%s。Trueは空きあり、Noneは確認不能につきフェイルセーフで選択)",
                    role,
                    order,
                    index,
                    endpoint.base_url,
                    available,
                )
                return index
            logger.info(
                "接続先選択[round_robin]: role=%s index=%d base_url=%s は空きスロットが無いためスキップ",
                role,
                index,
                endpoint.base_url,
            )
        attempt += 1
        if not wait_when_busy:
            index = order[0]
            logger.warning(
                "接続先選択[round_robin]: role=%s 候補%s全ての空きスロットが無いですが、"
                "wait_when_busy=False のため待機せず index=%d を暫定選択します",
                role,
                order,
                index,
            )
            return index
        logger.warning(
            "接続先選択[round_robin]: role=%s 候補%s全ての空きスロットが無いため%.1f秒待機します"
            "（試行%d回目、次回は使用可能時間帯を再確認します）",
            role,
            order,
            busy_poll_interval_seconds,
            attempt,
        )
        await asyncio.sleep(busy_poll_interval_seconds)


async def _select_endpoint(
    role: str,
    endpoints: tuple[LLMEndpoint, ...],
    strategy: str,
    *,
    inherit_from_role: str | None = None,
    probe_timeout_seconds: float = 3.0,
    busy_poll_interval_seconds: float = 2.0,
    wait_when_busy: bool = True,
) -> LLMEndpoint:
    """config.ini [llm].main_routing_strategy / sub_routing_strategy に従って接続先を1つ選ぶ。

    選択対象は、まず endpoints のうち現在時刻が start/end の使用可能時間帯内の
    ものだけに絞り込む（_endpoint_available_now 参照）。start/end 未指定の
    接続先は常に対象に含まれるため、config.py の _as_llm_endpoints が最低1件の
    常時使用可能な接続先を要求しており、絞り込み結果が空になることは無い
    （念のため空になった場合は全件にフォールバックする）。

    round_robin戦略でGET /slotsによる空き確認・空き待ち（_select_round_robin_endpoint
    参照）を行うため async def。build_model()からawaitで呼ぶ。

    Args:
        role: "main" または "sub"（ルーティング状態を役割ごとに分けるためのキー）。
        endpoints: config.main_endpoints または config.sub_endpoints。
        strategy: config.main_routing_strategy または config.sub_routing_strategy
            （"round_robin"/"random"/"priority_failover" のいずれか）。
            inherit_from_role が使われた場合は無視される。
        inherit_from_role: 指定時（config.sub_endpoints_inherit_main が True の
            ときの role="sub" 呼び出し）、strategy による独自選択は行わず、
            同一セッションIDで inherit_from_role（"main"）が直近実際に選んだ
            接続先indexをそのまま使う。dispatch_agent は同一 thread_id の
            contextvar をそのまま引き継ぐため（src/subagent.py 参照）、これに
            より「委譲元メインエージェントがこの会話で今使っている接続先」を
            継承できる。まだ inherit_from_role 側の選択が行われていない
            （このセッションでメインエージェントが一度もLLM呼び出しをして
            いない）場合のみ、安全側として通常のロジックへフォールバックする。
        probe_timeout_seconds: [llm].round_robin_slots_probe_timeout_seconds。
            round_robin戦略でGET /slots問い合わせ自体のタイムアウト秒数。
        busy_poll_interval_seconds: [llm].round_robin_busy_poll_interval_seconds。
            round_robin戦略で候補の全接続先に空きスロットが無かった場合、
            再確認までに待機する秒数。
        wait_when_busy: round_robin戦略で候補の全接続先がビジーだった場合に
            空きが出るまで待つか（True、既定）、待たずにフェイルセーフ選択
            するか（False）。build_model() の同名引数を参照。

    Returns:
        選ばれた LLMEndpoint。使用可能な接続先が1件かつ strategy が
        round_robin 以外の場合は常にそれを返す（round_robin は1件しか
        無い場合でも GET /slots による空き確認・待機を行う）。
    """
    # ログ表示・呼び出し元の把握用（会話単位のID）。未設定（サブエージェント
    # 経由でset_current_session未実行など）なら空文字として扱う。
    session_id = _CURRENT_SESSION_ID.get() or ""
    # _LAST_SELECTED_INDEX のキーは session_id（thread_id）ではなく tab_id
    # を使う。同じ thread_id を複数タブで同時に開いた場合でも、「このタブが
    # 直近実際に選んだ接続先」がタブをまたいで上書き・混線しないようにする
    # ため（mark_last_endpoint_failed() 参照。_CURRENT_TAB_ID docstring参照）。
    tab_id = _CURRENT_TAB_ID.get() or session_id
    state_key = (role, tab_id)

    logger.info(
        "接続先選択[開始]: role=%s strategy=%s session_id=%r inherit_from_role=%r "
        "endpoints_total=%d endpoints=%s",
        role,
        strategy,
        session_id,
        inherit_from_role,
        len(endpoints),
        [f"[{i}] {e.base_url} model={e.model} start={e.start} end={e.end}" for i, e in enumerate(endpoints)],
    )

    if inherit_from_role is not None:
        inherited_index = _LAST_SELECTED_INDEX.get((inherit_from_role, tab_id))
        if inherited_index is not None and inherited_index < len(endpoints):
            _LAST_SELECTED_INDEX[state_key] = inherited_index
            logger.info(
                "接続先選択[結果]: role=%s session_id=%r -> index=%d base_url=%s model=%s "
                "(理由: inherit_from_role=%s の直近選択を継承)",
                role,
                session_id,
                inherited_index,
                endpoints[inherited_index].base_url,
                endpoints[inherited_index].model,
                inherit_from_role,
            )
            return endpoints[inherited_index]
        logger.info(
            "接続先選択: role=%s session_id=%r inherit_from_role=%s の直近選択が無いため通常ロジックへフォールバック "
            "(_LAST_SELECTED_INDEX に (%s, %r) が未登録)",
            role,
            session_id,
            inherit_from_role,
            inherit_from_role,
            tab_id,
        )

    # round_robin戦略はこのあと _select_round_robin_endpoint 内で待機の
    # 周回ごとに自前で再計算するため、ここでの eligible_indices は
    # 「単一候補の早期リターン判定」と下記ログ用のスナップショットに過ぎない。
    eligible_indices = _compute_eligible_indices(endpoints)
    excluded_indices = [i for i in range(len(endpoints)) if i not in eligible_indices]
    if excluded_indices:
        logger.info(
            "接続先選択: role=%s session_id=%r 時間帯外のため除外されたインデックス=%s "
            "（%sのstart/end設定を確認）",
            role,
            session_id,
            excluded_indices,
            [f"[{i}] base_url={endpoints[i].base_url} start={endpoints[i].start} end={endpoints[i].end}" for i in excluded_indices],
        )

    if len(eligible_indices) == 1 and strategy != "round_robin":
        index = eligible_indices[0]
        _LAST_SELECTED_INDEX[state_key] = index
        logger.info(
            "接続先選択[結果]: role=%s strategy=%s session_id=%r -> index=%d/%d base_url=%s model=%s "
            "(理由: 使用可能な接続先が1件しかないためstrategyに関わらず強制選択。endpoints_total=%d)",
            role,
            strategy,
            session_id,
            index,
            len(endpoints),
            endpoints[index].base_url,
            endpoints[index].model,
            len(endpoints),
        )
        return endpoints[index]

    if strategy == "random":
        index = random.choice(eligible_indices)
        logger.info(
            "接続先選択[random]: role=%s session_id=%r candidates=%s -> index=%d",
            role,
            session_id,
            eligible_indices,
            index,
        )
    elif strategy == "priority_failover":
        # 使用可能な接続先を先頭から順に見て、クールダウン中でない最初の
        # 接続先を使う。全滅時は安全側として使用可能な先頭へフォールバック
        # する。
        now = time.time()
        index = eligible_indices[0]
        for i in eligible_indices:
            if _ENDPOINT_COOLDOWN_UNTIL.get((role, i), 0.0) <= now:
                index = i
                break
        logger.info(
            "接続先選択[priority_failover]: role=%s session_id=%r eligible_indices=%s "
            "cooldown_until(role別全件)=%s now=%.3f -> index=%d",
            role,
            session_id,
            eligible_indices,
            {k: v for k, v in _ENDPOINT_COOLDOWN_UNTIL.items() if k[0] == role},
            now,
            index,
        )
    else:  # "round_robin"（既定）
        index = await _select_round_robin_endpoint(
            role,
            endpoints,
            probe_timeout_seconds=probe_timeout_seconds,
            busy_poll_interval_seconds=busy_poll_interval_seconds,
            wait_when_busy=wait_when_busy,
        )

    _LAST_SELECTED_INDEX[state_key] = index
    logger.info(
        "接続先選択[最終結果]: role=%s strategy=%s session_id=%r -> index=%d/%d base_url=%s model=%s "
        "state_key=%r _LAST_SELECTED_INDEX(role別全件)=%s",
        role,
        strategy,
        session_id,
        index,
        len(endpoints),
        endpoints[index].base_url,
        endpoints[index].model,
        state_key,
        {k: v for k, v in _LAST_SELECTED_INDEX.items() if k[0] == role},
    )
    return endpoints[index]


def mark_last_endpoint_failed(role: str) -> None:
    """直近この会話の build_model(config, role) が選んだ接続先を一時的にクールダウンする。

    priority_failover 戦略専用のフィードバックフック。通信エラーを検知した
    呼び出し元（現状は app.py の except LLM_CONNECTION_ERRORS）が、エラーを
    検知した会話のコンテキスト内（set_current_session(thread_id) 済みの状態）
    でここを呼ぶと、次回以降の build_model() 呼び出しで
    _ENDPOINT_FAILOVER_COOLDOWN_SECONDS秒間その接続先を避け、次点の接続先へ
    切り替わる（round_robin/random 戦略では index は記録されるが参照
    されないため実質無視される）。

    _LAST_SELECTED_INDEX は (role, タブID) 単位で管理しているため、
    複数タブ・複数ユーザーが同時に接続していても、他タブの選択に
    巻き込まれず「このタブが直近実際に使っていた接続先」だけを
    クールダウンできる（同じ thread_id を複数タブで開いた場合も含む。
    _CURRENT_TAB_ID docstring参照）。

    Args:
        role: "main" または "sub"。
    """
    tab_id = _CURRENT_TAB_ID.get() or _CURRENT_SESSION_ID.get() or ""
    index = _LAST_SELECTED_INDEX.get((role, tab_id))
    if index is None:
        return
    _ENDPOINT_COOLDOWN_UNTIL[(role, index)] = time.time() + _ENDPOINT_FAILOVER_COOLDOWN_SECONDS
    logger.warning(
        "LLM接続失敗を検知したため接続先を一時的に避けます（role=%s, index=%d, %.0f秒間）",
        role,
        index,
        _ENDPOINT_FAILOVER_COOLDOWN_SECONDS,
    )
