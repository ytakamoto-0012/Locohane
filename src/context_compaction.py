"""会話履歴が長くなった際に、古い部分をLLM自身に要約させて圧縮する。

ClaudeCode の compact 相当。src/context_trim.py が「今回のLLM呼び出しへの
入力だけを縮める・永続履歴（checkpointer上のメッセージ）は書き換えない」
方針であるのに対し、こちらは永続履歴自体を書き換える恒久的な圧縮であり、
会話が長引くほどプリフィル遅延・トークン量の両方に効く。

app.py の on_message から、そのターンの astream_events ループが完全に
終了した後（進行中のグラフ実行と aupdate_state が競合しないタイミング）に
呼ばれる想定。
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .config import Config
from .context_trim import last_ai_total_tokens, trim_old_tool_messages
from .llm import (
    LLM_CONNECTION_ERRORS,
    ThinkingLoopDetected,
    aclose_model_client,
    build_model,
    mark_last_endpoint_failed,
)

logger = logging.getLogger(__name__)

_SUMMARY_HEADER = "[自動要約: コンテキスト圧縮のため、以前の会話の一部を要約しました。" "この内容を踏まえて続きの作業を行ってください]\n"
_PLAN_STATUS_HEADER = "[承認済みの実行計画（最優先タスク）。要約とは無関係にコード側が機械的に付与しています]\n"
_THREAD_NOTE_STATUS_HEADER = (
    "[thread note の現在の状態。要約とは無関係にコード側が機械的に付与しています。"
    "要約に含まれていない具体的な事実（値・件数・該当箇所等）が必要になったら、"
    "ここに挙がっているtopic名を read_thread_note でそのまま読んでください]\n"
)
_PRE_NOTE_MARKER = "[コンテキスト圧縮が近づいています]"
_LOOP_NUDGE_TEXT = "直前の要約生成は同じ内容を繰り返すループに陥ったため打ち切りました。" "落ち着いて、要約対象の会話履歴を踏まえてもう一度簡潔に要約し直してください。"


def maybe_append_precompact_note_nudge(messages: list[BaseMessage], config: Config) -> list[BaseMessage]:
    """圧縮（要約）が近づいたら、write_thread_noteへの書き出しを促すメッセージを末尾へ足す。

    src/main_token_guard.py の maybe_append_token_guard と同じ考え方
    （今回のLLM呼び出しへの入力にだけ差し込み、state・checkpointer上の
    永続履歴は書き換えない）。maybe_compact() による要約は永続履歴を
    書き換える恒久的な操作であり、要約LLMの精度次第で古い会話中の具体的な
    事実（値・件数・該当箇所等）が薄まって失われうる。要約対象から外れる
    前に、そうした事実を write_thread_note（ファイルへの追記であり要約の
    影響を受けない）へ退避させる機会をモデルに与える狙い。

    Args:
        messages: 今回のモデル呼び出しへ渡す予定のメッセージ列
            （context_trim 適用後のものを想定）。書き換えない。
        config: context_compaction_pre_note_* を含むアプリ設定。

    Returns:
        閾値に達していれば末尾に HumanMessage を1件足した新しいリスト。
        達していない場合・無効化されている場合、または直近
        keep_recent_turns ターン以内に write_thread_note が既に呼ばれて
        いる場合は、引数の messages をそのまま返す。
    """
    if not config.context_compaction_enabled or config.context_compaction_pre_note_threshold <= 0:
        return messages
    total = last_ai_total_tokens(messages)
    if total is None or total < config.context_compaction_pre_note_threshold:
        return messages
    if _write_thread_note_called_recently(messages, config.context_compaction_keep_recent_turns):
        # 直近で既に書き出し済みなら、同じ facts を書かせるためだけの
        # 再ナッジは無意味かつ有害（ナッジ自体・再書き込み自体がトークンを
        # 消費し、閾値超過が続く限り毎ターン再発火して無限ループ状態になる）。
        return messages

    logger.warning(
        "メインエージェントのトークン使用量がコンテキスト圧縮の予告閾値(%d)に達しました"
        "(直近の応答: %dトークン)。write_thread_noteへの書き出しを促します",
        config.context_compaction_pre_note_threshold,
        total,
    )
    return [*messages, HumanMessage(content=f"{_PRE_NOTE_MARKER}\n{config.context_compaction_pre_note_warning_text}")]


def _write_thread_note_called_recently(messages: list[BaseMessage], keep_recent_turns: int) -> bool:
    """直近 keep_recent_turns ユーザーターン以内に write_thread_note が
    呼ばれていれば True を返す。

    「直近何ターンか」の切り出しには _find_cut_index と同じ安全な切断点を
    使う（境界より後ろが keep_recent_turns 分の直近範囲）。単純に「前回の
    ナッジ以降」で判定しないのは、ナッジ自体が永続履歴へ書き込まれない
    一時的な差し込みメッセージであり、状態として覚えておく場所が無いため
    （src/main_token_guard.py の maybe_append_token_guard と同じ、今回の
    呼び出し限りの差し込み方針）。keep_recent_turns はどのみち圧縮時に
    丸ごと残る範囲を決めている値であり、その範囲内に書き出し済みなら
    再度書かせても得るものが無い。
    """
    cut_index = _find_cut_index(messages, keep_recent_turns)
    recent = messages[cut_index:] if cut_index is not None else messages
    return any(
        isinstance(m, AIMessage) and any(tc.get("name") == "write_thread_note" for tc in (m.tool_calls or []))
        for m in recent
    )


def should_compact(
    cumulative_usage: dict | None,
    last_usage: dict | None,
    message_count: int,
    config: Config,
) -> bool:
    """メインエージェントの累積トークン使用量と直近1回分の使用量から、圧縮を検討すべきか判定する。

    2つの独立した条件のOR判定になっている:

    1. 累積条件: 直近1回のLLM呼び出し分だけで判定すると、context_trim による
       送信ペイロード削減の影響で閾値未満に収まり続け、圧縮が長期間発火しない
       まま永続履歴（state["messages"]）だけが肥大化しうる。そのため、会話
       全体を通じたメインエージェントの累積トークン量（サブエージェント呼び出し
       分は含まない）でも判定する。
    2. 単発条件: 会話全体の累積は低くても、1ターンで巨大なツール結果や
       ファイル内容を一気に積むなどして、単発のリクエストがモデルの
       context window上限に迫るケースがある。これは累積条件では捉えられない
       ため、直近1回の total_tokens も別途見る。

    Args:
        cumulative_usage: app.py が保持する token_usage_cumulative_main
            （{"input","output","total"} を持つ累積集計辞書）。track_token_usage=false
            等で一度も集計されていない場合は None。
        last_usage: on_chat_model_end で得た直近1回分の usage_metadata
            （"total_tokens" キーを持つ dict）。取得できなかった場合は None。
        message_count: 現在の会話履歴（state["messages"]）の件数。
        config: context_compaction_* 設定を含むアプリ設定。

    Returns:
        圧縮を試みるべきなら True。
    """
    if not config.context_compaction_enabled:
        return False
    if message_count < config.context_compaction_min_messages_to_compact:
        return False
    cumulative_total = (cumulative_usage or {}).get("total", 0) or 0
    if cumulative_total >= config.context_compaction_token_threshold:
        return True
    last_total = (last_usage or {}).get("total_tokens", 0) or 0
    return last_total >= config.context_compaction_single_request_token_threshold


def _find_cut_index(messages: list[BaseMessage], keep_recent_turns: int) -> int | None:
    """安全な切断点のうち、末尾から keep_recent_turns 個目のユーザーターン
    の直前の切断点（スライス境界）を返す。

    旧実装は HumanMessage の個数で判定していたが、analyze_image の画像
    フォローアップ（_with_image_followups）とループガードの nudge は
    ツール往復の途中に HumanMessage を挿入する。LangGraph は tool_call を
    1件ずつ tools ノードへ渡すため、ToolMessage(a) → HumanMessage(画像) →
    ToolMessage(b) という並びが起こりうる。HumanMessage の位置で切ると
    ToolMessage(b) だけが対応する AIMessage を失い、OpenAI 互換 API が
    エラーを返す。

    そこで以下の方式へ置き換える:

    1. 先頭から走査し、各インデックス i で「発行済み tool_call id の集合」と
       「返却済み ToolMessage id の集合」が一致している（＝未処理のツール
       呼び出しが無い）状態になった時点の**スライス境界 i+1**を「安全な
       切断点」として列挙する（`messages[0:境界]` が自己完結することを
       意味する。境界を message[i] の直後、つまり i+1 にするのが重要で、
       安全になった直後の message[i] 自身（多くの場合は直前の ToolMessage）
       を境界にそのまま使うと、その ToolMessage だけが `messages[:境界]`
       から漏れて対応する AIMessage.tool_calls だけが残る、という壊れ方を
       する）。先頭（境界0、何も含まない）も自明に安全なため常に候補へ含める。
    2. ユーザーターン境界（HumanMessage）が keep_recent_turns 個より
       十分にあれば、それを優先して境界を選ぶ。
    3. ユーザーターンが keep_recent_turns 個に満たない場合（1ターン内で
       LLM呼び出しを何十回も繰り返す長時間タスク等）は、HumanMessage境界
       だけでは圧縮の機会が一度も来ない。この場合はツール往復の境界
       （安全な切断点そのもの）を「直近何回ぶんを残すか」の単位として使う。

    これによりターン途中でも安全に切り分けられる。

    Args:
        messages: 現在の会話履歴全体。
        keep_recent_turns: 丸ごと保持する直近のユーザーターン数
            （ユーザーターンが不足する場合は、直近何回ぶんのツール往復を
            残すかの単位として使う）。

    Returns:
        `messages[:戻り値]` が要約対象、それ以降が保持対象になる境界値。
        圧縮しても縮まらない・安全な境界が無い場合は None。
    """
    # --- 1. 安全な切断点（スライス境界）を列挙 ---
    issued_ids: set[str] = set()
    done_ids: set[str] = set()
    safe_cut_points: list[int] = [0]  # 境界0（何も含まない）は常に自明に安全

    for i, m in enumerate(messages):
        # ToolMessage が返ってきた → 対応する tool_call が完了
        if isinstance(m, ToolMessage):
            done_ids.add(m.tool_call_id)
        # AIMessage が tool_calls を発行 → 未完了としてマーク
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                issued_ids.add(tc.get("id", ""))
        # 現在の位置で未処理の tool_call が無い → message[i] を含めた境界 i+1 が安全
        if issued_ids == done_ids:
            safe_cut_points.append(i + 1)

    # --- 2. 末尾から keep_recent_turns 個目のユーザーターンの直前を選ぶ ---
    human_indices = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    total_users = len(human_indices)
    target_idx = total_users - keep_recent_turns  # 切るべきユーザーのインデックス
    if target_idx >= 0:
        target_human_index = human_indices[target_idx]
        cut_index = None
        for boundary in safe_cut_points:
            if boundary > target_human_index:
                break
            cut_index = boundary
        return cut_index if cut_index else None

    # --- 3. ユーザーターンが不足する場合は、安全な切断点の個数を単位にする ---
    # safe_cut_points には常に境界0（何も進んでいない状態）が含まれるため、
    # 実質的に使える切断点数は1個少ない。
    usable_points = len(safe_cut_points) - 1
    if usable_points <= keep_recent_turns:
        return None
    cut_index = safe_cut_points[-(keep_recent_turns + 1)]
    return cut_index if cut_index else None


def _messages_to_text(messages: list[BaseMessage]) -> str:
    """要約対象メッセージ列を、要約LLMへ渡すプレーンテキストへ変換する。"""
    lines = []
    for m in messages:
        role = getattr(m, "type", m.__class__.__name__)
        content = m.content if isinstance(m.content, str) else str(m.content)
        if not content.strip():
            continue
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def maybe_compact(
    messages: list[BaseMessage],
    model,
    config: Config,
    *,
    role: Literal["main", "sub"] = "main",
) -> list[BaseMessage] | None:
    """必要なら古い会話履歴を要約し、状態更新用のメッセージ列を返す。

    呼び出し元は、返り値が None でなければ次のように使うこと（1回の
    aupdate_state 呼び出しで完結させ、途中の中間状態を作らないこと。
    RemoveMessageによる全削除と要約結果の追加を2回に分けて呼ぶと、
    その間に別の処理が会話履歴を読みに行った場合に不整合な中間状態
    （メッセージが一時的に空）を観測しうるため）:

        new_messages = await maybe_compact(messages, model, config, role="main")
        if new_messages is not None:
            await graph.aupdate_state(
                config,
                {"messages": [RemoveMessage(id=m.id) for m in messages] + new_messages},
            )

    要約LLM呼び出しが通信エラー（LLM_CONNECTION_ERRORS）またはループ検知
    （ThinkingLoopDetected）で失敗した場合、app.py の on_message /
    src/subagent.py の run_subagent と同じ方針で接続先を切り替えつつ
    モデルを再構築してリトライする（下記 Notes 参照）。両方の予算を
    使い切った場合のみ、従来通り None を返して今回の圧縮をスキップする。

    Args:
        messages: 現在の会話履歴全体（state["messages"]）。
        model: 要約に使うモデル（build_model() の素のインスタンスでよい。
            ツールは不要）。リトライが発生した場合、このインスタンスは
            以降使われなくなる（新しいインスタンスに差し替わる）。
        config: context_compaction_* 設定を含むアプリ設定。
        role: "main"（app.py の要約呼び出し）または "sub"（dispatch_agent
            内の要約呼び出し）。接続先の再選択・リトライ回数上限（main:
            graph_connection_error_max_retries / sub:
            subagent_background_llm_timeout_max_retries）・クライアントの
            強制クローズ方針を build_model() のロールごとの接続先設定に
            合わせるために使う。

    Returns:
        要約が実行された場合、「要約結果のHumanMessage」+「直近ターンの
        メッセージ（新しいidを振った複製）」のリスト。圧縮不要、または
        要約LLM呼び出しに失敗した場合は None（呼び出し元は何もしない）。

    Notes:
        ThinkingLoopDetected発生時は、そのモデルインスタンス専用の
        httpx.AsyncClientのみを aclose_model_client() で強制クローズする
        （aclose_active_llm_clients()は同一セッションの他クライアント
        － 並行実行中の別サブエージェントやメイングラフ － まで巻き添えで
        閉じてしまうため、要約専用のこの経路では使わない。src/subagent.py
        の _invoke_with_loop_retry と同じ理由）。これにより、ストリームの
        後始末(aclose)自体が失敗・タイムアウトして接続が生きたまま
        llama-server側の生成が終わらない状態
        （ThinkingLoopDetected.client_broken=True）
        でも、次のリトライ・次のターンのLLM呼び出しが応答ヘッダー待ちで
        ハングし続けることを防ぐ。
    """
    cut_index = _find_cut_index(messages, config.context_compaction_keep_recent_turns)
    if cut_index is None:
        return None

    old_messages = messages[:cut_index]
    kept_messages = messages[cut_index:]
    if not old_messages:
        return None

    # 要約対象自体が長大だと要約プロンプト自体のプリフィルが遅くなるため、
    # context_trim と同様の切り詰めを要約対象にも適用してから渡す
    # （keep_recent=0: 要約対象内では「直近だから全文保持」は意味を持たない）。
    # ただし max_chars は [context_trim] のものを流用せず、要約専用の
    # context_compaction_summary_source_max_chars を使う。要約は永続履歴を
    # 置き換える恒久的な操作のため、プリフィル短縮目的の[context_trim]と
    # 同じ小さめの値を使うと、要約対象のツール結果がまとめて情報欠落し、
    # 要約が内容の薄いものになりうる（例: 大量ファイル処理タスクで
    # ファイル名の列挙しか残らない）。
    trimmed_old = trim_old_tool_messages(
        old_messages,
        keep_recent=0,
        max_chars=config.context_compaction_summary_source_max_chars,
    )
    text = _messages_to_text(trimmed_old)
    if not text.strip():
        return None

    try:
        prompt = config.context_compaction_prompt_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("要約プロンプトの読み込みに失敗しました: %s", config.context_compaction_prompt_path)
        return None

    summary_prompt = prompt + "\n\n---\n\n# 要約対象の会話履歴\n\n" + text
    local_input: list[BaseMessage] = [HumanMessage(content=summary_prompt)]
    current_model = model
    connection_attempt = 0
    loop_attempt = 0
    response = None
    while True:
        try:
            response = await current_model.ainvoke(local_input)
            break
        except LLM_CONNECTION_ERRORS as exc:
            # config属性へのアクセスをexcept節内に留めているのは、テスト用の
            # 簡易Configスタブ（retry関連フィールドを持たない）が、通信エラー・
            # ループ検知いずれも起きない成功系のテストで壊れないようにするため。
            connection_max_retries = (
                config.graph_connection_error_max_retries
                if role == "main"
                else config.subagent_background_llm_timeout_max_retries
            )
            if connection_attempt >= connection_max_retries:
                # 要約自体の失敗で本編の会話を壊さないよう、失敗時は元の履歴のまま続行する。
                logger.exception("会話履歴の自動要約が通信エラーで失敗しました。今回は圧縮をスキップします")
                return None
            connection_attempt += 1
            logger.warning(
                "要約LLM呼び出しが通信エラーのため接続先を切り替えて再試行します" "(%d/%d回目, role=%s): %s",
                connection_attempt,
                connection_max_retries,
                role,
                exc,
            )
            # main_routing_strategy/sub_routing_strategy=priority_failover の
            # 場合のみ次点の接続先へ切り替わる（他戦略では実質無視される。
            # app.py の except LLM_CONNECTION_ERRORS と同じフック）。
            mark_last_endpoint_failed(role)
            current_model = await build_model(config, role=role)
        except ThinkingLoopDetected as exc:
            # このモデルインスタンス専用のクライアントだけを、リトライするか
            # 諦めるかに関わらず無条件で強制クローズする（client_broken の
            # 真偽にも関わらない。理由は _invoke_with_loop_retry と同じ:
            # httpcoreのTraceフックがクローズ失敗をログするだけで再raiseせず
            # client_broken が立たないケースがあるため）。ここを諦める分岐の
            # 前に置かないと、リトライ予算を使い切った最後の1回だけ後始末
            # されずに終わり、ストリームの後始末自体が失敗してllama-server側
            # の生成が終わらないまま（client_broken=True）次のLLM呼び出しが
            # 応答ヘッダー待ちでハングし続ける（ユーザー報告の疑いに対応）。
            await aclose_model_client(current_model)
            loop_max_retries = config.thinking_loop_guard_max_retries
            if loop_attempt >= loop_max_retries:
                logger.warning(
                    "要約LLM応答がループし、%d回リトライしましたが改善しなかったため" "今回は圧縮をスキップします",
                    loop_max_retries,
                )
                return None
            loop_attempt += 1
            logger.warning(
                "要約LLM応答のループを検知したため再試行します" "(%d/%d回目, client_broken=%s): %r",
                loop_attempt,
                loop_max_retries,
                exc.client_broken,
                exc.snippet,
            )
            current_model = await build_model(config, role=role)
            local_input = [HumanMessage(content=summary_prompt), HumanMessage(content=_LOOP_NUDGE_TEXT)]
        except Exception:
            # 要約自体の失敗で本編の会話を壊さないよう、失敗時は元の履歴のまま続行する。
            logger.exception("会話履歴の自動要約に失敗しました。今回は圧縮をスキップします")
            return None

    summary_text = response.content if isinstance(response.content, str) else str(response.content)
    if not summary_text.strip():
        logger.warning("会話履歴の自動要約結果が空だったため、今回は圧縮をスキップします")
        return None

    summary_content = _SUMMARY_HEADER + summary_text
    # 要約LLMの読み取り精度に依存せず、圧縮のたびに100%正確な最新の計画状態・
    # thread noteの状態を機械的に追記する（要約対象の tool_calls 引数は
    # _messages_to_text に含まれず要約LLMからは元々見えないため、要約結果に
    # 計画・thread noteの存在が反映される保証が無い）。
    # tools.py からの import はモジュール先頭ではなくここで遅延させる: tools.py は
    # 起動時に `from .subagent import run_subagent` を行っており、subagent.py が
    # このモジュール（context_compaction）を（context_trim と同様の位置づけで）
    # サブエージェントにも使い回すために import すると、
    # tools.py → subagent.py → context_compaction.py → tools.py という循環
    # importになり ImportError になる（subagent.py 冒頭のコメント参照）。
    # 実際に呼ばれるのはアプリ起動が完了しモジュール初期化が済んだ後のため、
    # 関数内 import なら安全。
    from .tools import current_plan_status_text, thread_note_status_text

    plan_status = current_plan_status_text()
    if plan_status:
        summary_content += "\n\n" + _PLAN_STATUS_HEADER + plan_status
    note_status = thread_note_status_text()
    if note_status:
        summary_content += "\n\n" + _THREAD_NOTE_STATUS_HEADER + note_status
    summary_message = HumanMessage(content=summary_content)
    # kept_messages は同一の aupdate_state 呼び出し内で RemoveMessage と
    # 競合しないよう、新しい id を振った複製にする（add_messages リデューサは
    # 既存stateに無いidのメッセージを渡された順に末尾へ追記する）。
    kept_copies = [m.model_copy(update={"id": str(uuid.uuid4())}) for m in kept_messages]

    logger.warning(
        "会話履歴を圧縮しました: %d件 -> 要約1件 + 直近%d件",
        len(old_messages),
        len(kept_messages),
    )
    return [summary_message, *kept_copies]
