"""会話履歴中の古い ToolMessage / AIMessage を切り詰め、LLMへの入力を抑える。

長いReActループ（ファイル読み込み等のツール呼び出しを繰り返すタスク）では、
サイズ上限のないツール実行結果（例: OCR結果のMarkdown全文）が ToolMessage
としてそのまま会話履歴に蓄積し続け、llama.cppへのプロンプトプリフィルが
極端に遅くなる（本番ログで100秒以上の遅延を実測）。

src/graph.py の prebuilt 実装（pre_model_hook）・handwritten 実装
（call_model 直前）の両方から呼ばれる共通ロジック。ToolMessage は1件も
削除しない（LangGraph の create_react_agent が要求する「AIMessage.tool_calls
と対応する ToolMessage が揃っていること」という不変条件を壊さないため）。
content だけを短縮したコピーに差し替えることで、checkpointer 上の永続履歴
には手を付けず、今回のLLM呼び出しへの入力だけを縮める。
"""

from __future__ import annotations

import copy

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

_MARKER_TEMPLATE = (
    "\n...[truncated: {original_len} chars total, first {limit} chars shown. "
    "Full text preserved in conversation history. "
    "To read the rest, re-run the tool with different offset/limit parameters]"
)

# Read/Glob/Grep/json_query/analyze_image は src/tools.py の
# _check_file_tools_duplicate / 個別の重複ガードにより「同一引数での
# 再呼び出しは上限回数まで」しか許されない（結果が変わらない読み取り専用
# ツールのため）。ガードのエラー文言は「会話履歴にある前回の実行結果を
# 参照してください」と案内するが、その前回結果が一般的な max_chars で
# 切り詰められていると、実際にはモデルへ渡っていない分を参照させることに
# なり、案内が機能しない。そのためこれらのツール名の ToolMessage だけは
# 別枠の guarded_tool_max_chars を使えるようにする（ツール名は @tool の
# 明示指定と一致させること）。
_DUPLICATE_GUARD_TOOL_NAMES = frozenset({"Read", "Glob", "Grep", "json_query", "analyze_image"})


def trim_old_tool_messages(
    messages: list[BaseMessage],
    *,
    keep_recent_turns: int,
    max_chars: int,
    guarded_tool_max_chars: int | None = None,
) -> list[BaseMessage]:
    """直近 keep_recent_turns ターン分の ToolMessage は全文保持し、それより
    古いものは content を先頭 max_chars 文字に切り詰める。

    「ターン」の境界は find_turn_cut_index() が返すもの（ユーザーターン
    境界、不足時はツール往復単位にフォールバック）を使う。単純な件数指定
    だと、1回のAIMessageが並列発行した複数tool_callsに対応するToolMessage
    群のうち一部だけが保持され残りが切り詰められる、という分断が起きうる
    ため（同じラウンドトリップは丸ごと同じ側に入ることを保証するため）。

    Args:
        messages: state["messages"]（元の全履歴。書き換えない）。
        keep_recent_turns: 全文保持する直近のユーザーターン数
            （不足する場合は直近何回ぶんのツール往復を残すかの単位）。
        max_chars: 切り詰め後に残す本文の最大文字数（マーカー文言は含まない）。
            _DUPLICATE_GUARD_TOOL_NAMES に含まれないツールの ToolMessage に
            適用する。
        guarded_tool_max_chars: _DUPLICATE_GUARD_TOOL_NAMES に含まれる
            ツール（Read/Glob/Grep/json_query/analyze_image）の ToolMessage
            にだけ適用する切り詰め文字数。None の場合は max_chars を使う
            （従来どおりの挙動）。

    Returns:
        content だけ差し替えたコピーを含むメッセージ列。書き換え不要な
        メッセージは元のオブジェクトをそのまま含む。
    """
    cut_index = find_turn_cut_index(messages, keep_recent_turns)
    keep_from = cut_index or 0

    result: list[BaseMessage] = []
    for i, m in enumerate(messages):
        if i >= keep_from or not isinstance(m, ToolMessage) or not isinstance(m.content, str):
            result.append(m)
            continue
        limit = max_chars
        if guarded_tool_max_chars is not None and getattr(m, "name", None) in _DUPLICATE_GUARD_TOOL_NAMES:
            limit = guarded_tool_max_chars
        if len(m.content) <= limit:
            result.append(m)
            continue
        marker = _MARKER_TEMPLATE.format(original_len=len(m.content), limit=limit)
        result.append(m.model_copy(update={"content": m.content[:limit] + marker}))
    return result


def _truncate(text: str, max_chars: int) -> str | None:
    """max_chars を超える文字列を切り詰める。切り詰め不要なら None を返す。"""
    if len(text) <= max_chars:
        return None
    return text[:max_chars] + _MARKER_TEMPLATE.format(original_len=len(text), limit=max_chars)


def _trim_tool_call_args(tool_calls: list[dict], max_chars: int) -> list[dict] | None:
    """tool_calls の args に含まれる長い文字列値だけを切り詰める。

    `id`/`name`、および tool_calls の件数は一切変更しない。ToolMessage との
    対応は `tool_call_id` で取られるため、args の中身だけを縮めるぶんには
    「AIMessage.tool_calls と対応する ToolMessage が揃っていること」という
    LangGraph の不変条件を壊さない。

    Args:
        tool_calls: AIMessage.tool_calls（LangChain 正規化済みの dict のリスト）。
        max_chars: 切り詰め後に残す文字数。

    Returns:
        切り詰めが発生した場合のみ新しいリスト。1件も切り詰めなければ None。
    """
    changed = False
    new_calls: list[dict] = []
    for call in tool_calls:
        args = call.get("args")
        if not isinstance(args, dict):
            new_calls.append(call)
            continue
        new_args = None
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            truncated = _truncate(value, max_chars)
            if truncated is None:
                continue
            if new_args is None:
                new_args = copy.copy(args)
            new_args[key] = truncated
        if new_args is None:
            new_calls.append(call)
            continue
        new_call = copy.copy(call)
        new_call["args"] = new_args
        new_calls.append(new_call)
        changed = True
    return new_calls if changed else None


def trim_old_ai_messages(
    messages: list[BaseMessage], *, keep_recent_turns: int, max_chars: int
) -> list[BaseMessage]:
    """直近 keep_recent_turns ターン分の AIMessage は全文保持し、それより
    古いものは content と tool_calls の引数を先頭 max_chars 文字に切り詰める。

    「ターン」の境界は trim_old_tool_messages() と同じ find_turn_cut_index()
    を使う（keep_recent_turns はこちらの独自の値を渡せるため、tool側と
    ai側で異なるターン数を指定できる）。

    trim_old_tool_messages() は ToolMessage しか見ないため、モデル自身が
    `execute_python_code` の `code` 引数へファイル本文を書き写すような使い方を
    すると、ツール結果側だけを絞っても入力が膨らみ続ける（実測: 大量ファイル
    処理で1リクエストあたり24,833→128,000トークンまで単調増加し、コンテキスト
    上限に張り付いて処理が停止した）。その経路を塞ぐための関数。

    Args:
        messages: state["messages"]（元の全履歴。書き換えない）。
        keep_recent_turns: 全文保持する直近のユーザーターン数
            （不足する場合は直近何回ぶんのツール往復を残すかの単位）。
        max_chars: 切り詰め後に残す本文の最大文字数（マーカー文言は含まない）。

    Returns:
        content / tool_calls.args だけ差し替えたコピーを含むメッセージ列。
        書き換え不要なメッセージは元のオブジェクトをそのまま含む。
    """
    cut_index = find_turn_cut_index(messages, keep_recent_turns)
    keep_from = cut_index or 0

    result: list[BaseMessage] = []
    for i, m in enumerate(messages):
        if i >= keep_from or not isinstance(m, AIMessage):
            result.append(m)
            continue
        update: dict = {}
        if isinstance(m.content, str):
            truncated = _truncate(m.content, max_chars)
            if truncated is not None:
                update["content"] = truncated
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            new_calls = _trim_tool_call_args(tool_calls, max_chars)
            if new_calls is not None:
                update["tool_calls"] = new_calls
        result.append(m.model_copy(update=update) if update else m)
    return result


def find_turn_cut_index(messages: list[BaseMessage], keep_recent_turns: int) -> int | None:
    """安全な切断点のうち、末尾から keep_recent_turns 個目のユーザーターン
    の直前の切断点（スライス境界）を返す。

    src/context_compaction.py の要約対象切り出しと、この
    モジュール（trim_old_tool_messages / trim_old_ai_messages）の
    「直近何ターン分を全文保持するか」判定の両方で共有する。

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
       LLM呼び出しを何十回も繰り返す長時間タスク等。並列tool_callsを含む
       サブエージェントの典型形）は、HumanMessage境界だけでは切り分けの
       機会が一度も来ない。この場合はツール往復の境界（安全な切断点その
       もの）を「直近何回ぶんを残すか」の単位として使う。これにより、1回の
       AIMessageが並列発行した複数のtool_callsに対応するToolMessage群が
       同じラウンドトリップ内で「一部だけ保持・一部だけ切り詰め」という
       ふうに分断されることもない（ラウンドトリップ単位で丸ごと同じ側に
       入る）。

    これによりターン途中でも安全に切り分けられる。

    Args:
        messages: 現在の会話履歴全体。
        keep_recent_turns: 丸ごと保持する直近のユーザーターン数
            （ユーザーターンが不足する場合は、直近何回ぶんのツール往復を
            残すかの単位として使う）。

    Returns:
        `messages[:戻り値]` が古い側、それ以降が直近側になる境界値。
        古い側が空になる・安全な境界が無い場合は None。
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
        # target_idx == total_users（keep_recent_turns <= 0 で保持すべき直近
        # ユーザーターンが1つも無い場合）は human_indices の範囲外になる。
        # この場合は「全ユーザーターンを古い側にしてよい」という意味なので、
        # 境界をメッセージ列の末尾扱いにする（human_indices[target_idx] で
        # IndexErrorになっていた既存バグの修正）。
        target_human_index = human_indices[target_idx] if target_idx < total_users else len(messages)
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


def last_ai_total_tokens(messages: list[BaseMessage]) -> int | None:
    """直近の AIMessage の usage_metadata から total_tokens を取り出す。

    src/main_token_guard.py の閾値判定と同一の取得元（末尾から最初に見つかった
    AIMessage の usage_metadata）を、is_trigger_reached() と共有するために
    ここへ置く。

    Args:
        messages: 会話履歴。末尾から最初に見つかった AIMessage を見る。

    Returns:
        total_tokens。AIMessage が無い、または usage_metadata を取得できない
        （config.ini [llm].track_token_usage=false 等）場合は None。
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, dict):
            return None
        total = usage.get("total_tokens")
        return int(total) if total else None
    return None


def is_trigger_reached(messages: list[BaseMessage], trigger_total_tokens: int) -> bool:
    """トリムを発動すべきか判定する（Claude API の context editing
    （clear_tool_uses_20250919）の trigger.value 相当）。

    Args:
        messages: 判定対象の会話履歴（トリム前）。
        trigger_total_tokens: 直近 AIMessage の total_tokens がこの値以上に
            なって初めてトリムを発動する閾値。0以下を指定すると、常に発動する
            （この閾値機能が無かった旧来の挙動と同じ）。

    Returns:
        トリムを発動すべきなら True。track_token_usage=false 等で
        total_tokens を取得できない場合は、閾値未到達とみなし False を返す
        （src/main_token_guard.py の maybe_append_token_guard と同じ安全側判断）。
    """
    if trigger_total_tokens <= 0:
        return True
    total = last_ai_total_tokens(messages)
    if total is None:
        return False
    return total >= trigger_total_tokens
