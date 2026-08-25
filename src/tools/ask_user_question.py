"""ask_user_question(AskUserQuestion) ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl
import logging

from . import _state
from ._ask_relay_helper import _ask_with_cross_session_relay
from ._state import _resolve_ask_timeout

logger = logging.getLogger(__name__)


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
    timeout = _resolve_ask_timeout(_state._ASK_USER_QUESTION_TIMEOUT_SECONDS)
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
