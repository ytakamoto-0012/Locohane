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

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .config import Config
from .context_trim import trim_old_tool_messages

logger = logging.getLogger(__name__)

_SUMMARY_HEADER = "[自動要約: コンテキスト圧縮のため、以前の会話の一部を要約しました。" "この内容を踏まえて続きの作業を行ってください]\n"


def should_compact(usage: dict | None, message_count: int, config: Config) -> bool:
    """直近のLLM呼び出しの usage とメッセージ数から、圧縮を検討すべきか判定する。

    Args:
        usage: on_chat_model_end で得た直近1回分の usage_metadata
            （"total_tokens" キーを持つ dict）。track_token_usage=false 等で
            取得できなかった場合は None。
        message_count: 現在の会話履歴（state["messages"]）の件数。
        config: context_compaction_* 設定を含むアプリ設定。

    Returns:
        圧縮を試みるべきなら True。
    """
    if not config.context_compaction_enabled:
        return False
    if usage is None:
        return False
    if message_count < config.context_compaction_min_messages_to_compact:
        return False
    total = usage.get("total_tokens", 0) or 0
    return total >= config.context_compaction_token_threshold


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
) -> list[BaseMessage] | None:
    """必要なら古い会話履歴を要約し、状態更新用のメッセージ列を返す。

    呼び出し元は、返り値が None でなければ次のように使うこと（1回の
    aupdate_state 呼び出しで完結させ、途中の中間状態を作らないこと。
    RemoveMessageによる全削除と要約結果の追加を2回に分けて呼ぶと、
    その間に別の処理が会話履歴を読みに行った場合に不整合な中間状態
    （メッセージが一時的に空）を観測しうるため）:

        new_messages = await maybe_compact(messages, model, config)
        if new_messages is not None:
            await graph.aupdate_state(
                config,
                {"messages": [RemoveMessage(id=m.id) for m in messages] + new_messages},
            )

    Args:
        messages: 現在の会話履歴全体（state["messages"]）。
        model: 要約に使うモデル（build_model() の素のインスタンスでよい。
            ツールは不要）。
        config: context_compaction_* 設定を含むアプリ設定。

    Returns:
        要約が実行された場合、「要約結果のHumanMessage」+「直近ターンの
        メッセージ（新しいidを振った複製）」のリスト。圧縮不要、または
        要約LLM呼び出しに失敗した場合は None（呼び出し元は何もしない）。
    """
    cut_index = _find_cut_index(messages, config.context_compaction_keep_recent_turns)
    if cut_index is None:
        return None

    old_messages = messages[:cut_index]
    kept_messages = messages[cut_index:]
    if not old_messages:
        return None

    # 要約対象自体が長大だと要約プロンプト自体のプリフィルが遅くなるため、
    # context_trim と同じ切り詰めを要約対象にも適用してから渡す
    # （keep_recent=0: 要約対象内では「直近だから全文保持」は意味を持たない）。
    trimmed_old = trim_old_tool_messages(
        old_messages,
        keep_recent=0,
        max_chars=config.context_trim_truncated_max_chars,
    )
    text = _messages_to_text(trimmed_old)
    if not text.strip():
        return None

    try:
        prompt = config.context_compaction_prompt_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("要約プロンプトの読み込みに失敗しました: %s", config.context_compaction_prompt_path)
        return None

    try:
        response = await model.ainvoke([HumanMessage(content=prompt + "\n\n---\n\n# 要約対象の会話履歴\n\n" + text)])
    except Exception:
        # 要約自体の失敗で本編の会話を壊さないよう、失敗時は元の履歴のまま続行する。
        logger.exception("会話履歴の自動要約に失敗しました。今回は圧縮をスキップします")
        return None

    summary_text = response.content if isinstance(response.content, str) else str(response.content)
    if not summary_text.strip():
        logger.warning("会話履歴の自動要約結果が空だったため、今回は圧縮をスキップします")
        return None

    summary_message = HumanMessage(content=_SUMMARY_HEADER + summary_text)
    # kept_messages は同一の aupdate_state 呼び出し内で RemoveMessage と
    # 競合しないよう、新しい id を振った複製にする（add_messages リデューサは
    # 既存stateに無いidのメッセージを渡された順に末尾へ追記する）。
    kept_copies = [m.model_copy(update={"id": str(uuid.uuid4())}) for m in kept_messages]

    logger.info(
        "会話履歴を圧縮しました: %d件 -> 要約1件 + 直近%d件",
        len(old_messages),
        len(kept_messages),
    )
    return [summary_message, *kept_copies]
