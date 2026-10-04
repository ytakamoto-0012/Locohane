"""同じ応答（ツール呼び出し）を連続で繰り返すループの検知（[tool_loop_guard]）。

小型ローカルモデルは、ツールの結果（特にエラー）を読んでも、全く同じ応答を
繰り返すことがある。eval 008（2026-10-04）では、本文とツール呼び出し（名前・
引数）が完全に同じ応答を30回連続で出し、トークン上限まで消費して成果物を
作れずに終わった。

ここでは、どのツールのどんな結果かを問わず、モデルの応答（本文と
ツール呼び出しの名前・引数）が連続で全く同じなら、それをループとみなす。
思考（reasoning）は毎回揺れるため比べない。回復は、ストリーム中の反復ループ
（ThinkingLoopDetected）と同じ仕組み（注意メッセージを入れて再試行し、上限で
停止を通知する）に任せる。Qwen3.6 では tool_choice によるツール呼び出しの
強制が効かないため、モデルの強制機能には頼らない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from .config import Config
from .images import is_image_followup_message
from .llm import ToolCallLoopDetected

# 注意メッセージに添える結果の抜粋の最大文字数。
_EXCERPT_CHARS = 200


@dataclass(frozen=True)
class ToolLoop:
    """検知したループの内容。

    Attributes:
        repeats: 同じ応答が連続した回数。
        tool_names: 繰り返された応答で呼ばれたツール名（重複なし、呼び出し順）。
        result_excerpt: 直近の応答に対するツール結果の抜粋（最初のツール結果）。
    """

    repeats: int
    tool_names: tuple[str, ...]
    result_excerpt: str

    def detail(self) -> str:
        """注意メッセージに添える説明を返す。"""
        names = "、".join(self.tool_names)
        return (
            f"（自動検知: 直近{self.repeats}回、全く同じツール呼び出し（{names}）を繰り返しています。"
            f"その結果: {self.result_excerpt}）\n"
            "同じ呼び出しを繰り返さず、結果に書かれた指示に従うか、別の手段に切り替えてください。"
        )


def _content_text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _response_signature(ai: AIMessage) -> tuple:
    """応答の署名（本文とツール呼び出しの名前・引数）。思考は含めない。"""
    calls = sorted(
        (call.get("name") or "", json.dumps(call.get("args"), ensure_ascii=False, sort_keys=True, default=str))
        for call in ai.tool_calls
    )
    return (_content_text(ai).strip(), tuple(calls))


def detect_tool_call_loop(messages: list[BaseMessage], config: Config) -> ToolLoop | None:
    """末尾から連続して全く同じ応答（ツール呼び出し）が max_repeats 回続いていれば返す。

    ツールの結果は比べない（エラーかどうかも問わない）。末尾が ToolMessage
    （画像のフォローアップは読み飛ばす）でなければ、連続は途切れているとみなす
    （注意メッセージ・ナッジ等の HumanMessage を挟んだ後は、また max_repeats 回
    繰り返すまで検知しない）。exclude_tools だけを呼ぶ応答（状態確認の
    ポーリング）でも連続は途切れる。

    Args:
        messages: モデルへ渡す直前の会話履歴。
        config: tool_loop_guard_* を含むアプリ設定。

    Returns:
        検知したループ。無ければ（無効化されている場合も）None。
    """
    if not config.tool_loop_guard_enabled or config.tool_loop_guard_max_repeats <= 1:
        return None
    exclude = set(config.tool_loop_guard_exclude_tools)

    first_signature: tuple | None = None
    first_results: list[ToolMessage] = []
    repeats = 0
    results: list[ToolMessage] = []
    for message in reversed(messages):
        if is_image_followup_message(message):
            continue
        if isinstance(message, ToolMessage):
            results.append(message)
            continue
        if not isinstance(message, AIMessage) or not results:
            break
        results.reverse()
        if all((m.name or "") in exclude for m in results):
            break
        signature = _response_signature(message)
        if first_signature is None:
            first_signature, first_results = signature, results
        elif signature != first_signature:
            break
        repeats += 1
        if repeats >= config.tool_loop_guard_max_repeats:
            names = tuple(dict.fromkeys(m.name or "" for m in first_results))
            excerpt = _content_text(first_results[0]).strip().replace("\n", " ")[:_EXCERPT_CHARS]
            return ToolLoop(repeats=repeats, tool_names=names, result_excerpt=excerpt)
        results = []
    return None


def raise_if_tool_call_loop(messages: list[BaseMessage], config: Config) -> None:
    """ループを検知したら ToolCallLoopDetected を送出する（メインエージェント用）。

    Args:
        messages: モデルへ渡す直前の会話履歴。
        config: tool_loop_guard_* を含むアプリ設定。

    Raises:
        ToolCallLoopDetected: ループを検知した場合。
    """
    loop = detect_tool_call_loop(messages, config)
    if loop is not None:
        raise ToolCallLoopDetected(loop.detail())
