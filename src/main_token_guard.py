"""メインエージェントの1リクエストあたりのトークン量を監視し、上限が近づいたら
安全に打ち切って引き継げるようにする。

src/subagent.py の token_guard（サブエージェント側）と同じ考え方をメイングラフへ
持ち込んだもの。低パラメータモデルでは1リクエストあたりのトークン数が一定を超えると
処理を続けられなくなるが、LLM自身は自分の使用トークン量を認識できないため、
コード側で判定して注意メッセージを入力へ差し込む。

大量ファイル処理の実測では、メインの1リクエストあたり total_tokens が
24,833 → 128,000 と単調増加し、34回中23回が64,000を超えてコンテキスト上限に
張り付いたまま処理が停止した。そこまで行くと成果も進捗も引き継げないため、
手前で止めて「どこまで終わったか」と「新しいチャットで再開するための引継ぎ
プロンプト」を出力させる。

判定は閾値超過を検知した各モデル呼び出しで行い、注意メッセージは
**そのときのLLMへの入力にだけ**差し込む（state・checkpointer 上の永続履歴は
書き換えない）。したがって履歴へ積み上がることはなく、1リクエストにつき
必ず1通だけが入る。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from .config import Config

logger = logging.getLogger(__name__)

# 差し込むメッセージの先頭に付ける目印。ログ・テストからの識別用。
GUARD_MARKER = "[コンテキスト上限が近づいています]"


def _last_ai_total_tokens(messages: list[BaseMessage]) -> int | None:
    """直近の AIMessage の usage_metadata から total_tokens を取り出す。

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


def maybe_append_token_guard(
    messages: list[BaseMessage], config: Config
) -> list[BaseMessage]:
    """トークン量が閾値に達していれば、引継ぎを促すメッセージを末尾へ足す。

    Args:
        messages: 今回のモデル呼び出しへ渡す予定のメッセージ列
            （context_trim 適用後のものを想定）。書き換えない。
        config: graph_token_guard_* / graph_handoff_prompt_path を含むアプリ設定。

    Returns:
        閾値に達していれば末尾に HumanMessage を1件足した新しいリスト。
        達していない場合・無効化されている場合・引継ぎプロンプトを読めなかった
        場合は、引数の messages をそのまま返す。
    """
    if not config.graph_token_guard_enabled:
        return messages
    total = _last_ai_total_tokens(messages)
    if total is None or total < config.graph_token_guard_soft_threshold:
        return messages

    try:
        text = config.graph_handoff_prompt_path.read_text(encoding="utf-8")
    except OSError:
        # 文言を読めないことで会話を止めてしまう方が損失が大きいため、
        # 記録だけ残して通常どおり続行する。
        logger.exception(
            "引継ぎプロンプトの読み込みに失敗しました: %s", config.graph_handoff_prompt_path
        )
        return messages

    logger.warning(
        "メインエージェントのトークン使用量が閾値(%d)に達しました"
        "(直近の応答: %dトークン)。引継ぎプロンプトの生成を促します",
        config.graph_token_guard_soft_threshold,
        total,
    )
    return [*messages, HumanMessage(content=f"{GUARD_MARKER}\n{text}")]
