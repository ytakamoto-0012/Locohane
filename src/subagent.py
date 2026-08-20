"""サブエージェント（タスク委譲）用の独立した ReAct ループ。

tools.py の dispatch_agent ツールから呼ばれる。LangGraph の StateGraph は
使わず、graph.py の _build_handwritten_graph（call_model → should_continue
→ ToolNode → call_model … のループ）と同じ流れを async 関数の中で再現する。

意図的に tools.py / graph.py を import しない（呼び出し側から tools /
system_prompt / config を引数で受け取る汎用エンジンにすることで、
tools.py → subagent.py → tools.py という循環 import を避けている）。

model.ainvoke() / tool.ainvoke() はいずれも RunnableConfig を明示的に渡さない
ため、親グラフの astream_events には内部の呼び出しが一切伝播しない。
これにより、サブエージェント内部の思考過程・ツール呼び出しは Chainlit の
UI には表示されず、ログファイルにのみ記録される（コンテキスト節約が目的）。

async 化している理由: run_script が実行前にユーザー承認を求める async ツール
になったため（tools.py 参照）。dispatch_agent 自身も async ツールであり、
ImageAwareToolNode.ainvoke から同一イベントループ上で直接 await されるので、
ここで ainvoke に統一しても新規スレッド/イベントループのブリッジは不要。
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Callable

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool


def _contains_error(content: str) -> bool:
    """文字列にエラーを示すキーワードが含まれるかを判定する。

    日本語「エラー」、英語「error」(大文字小文字区別なし)、全角カタカナ
    「ｴﾗｰ」に対応する。
    """
    content_lower = content.lower()
    return "エラー" in content or "error" in content_lower or "ｴﾗｰ" in content


from .config import Config
from .context_compaction import maybe_compact, should_compact
from .context_trim import is_trigger_reached, trim_old_ai_messages, trim_old_tool_messages
from .images import image_followup_message
from .llm import (
    LLM_CONNECTION_ERRORS,
    ThinkingLoopDetected,
    build_model,
    describe_current_task,
    pick_loop_nudge_message,
)

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE_NUDGE_TEXT = (
    "[システム通知: 直前の応答にはツール呼び出しも本文もありませんでした。"
    "これまでに分かったことをまとめて回答するか、必要な追加のツール呼び出しを"
    "行ってください]"
)


async def _run_one_tool_call(call: dict, tools_by_name: dict[str, BaseTool]) -> tuple[ToolMessage, HumanMessage | None]:
    """1件の tool_call を実行し、(ToolMessage, followup) を返す。例外は送出しない。"""
    tool_obj = tools_by_name.get(call["name"])
    if tool_obj is None:
        tool_message = ToolMessage(
            content=f"エラー: 未知のツールです: {call['name']}",
            tool_call_id=call["id"],
        )
    else:
        try:
            # ToolCall dict（"name"/"args"/"id"/"type" を持つ）をそのまま
            # ainvoke に渡す。call["args"] のみを渡すと BaseTool 側で
            # tool_call_id が None のまま扱われ、response_format=
            # "content_and_artifact" のツール（view_image 等）の artifact
            # が握りつぶされてしまうため、ToolCall 形式を維持する。
            tool_message = await tool_obj.ainvoke(call)
        except Exception as e:  # noqa: BLE001 - ツール異常はエラー文字列化してループ継続
            logger.exception("subagent tool error: %s", call["name"])
            tool_message = ToolMessage(
                content=f"エラー: ツール実行に失敗しました: {e}\n{traceback.format_exc()}",
                tool_call_id=call["id"],
            )
    # execute_python_*系ツールは成功時もWARNING（スキル開発アイデアのシグナルとして記録）
    # それ以外でエラーメッセージが含まれる場合もWARNING
    content = str(tool_message.content)
    is_execute_python = call["name"].startswith("execute_python_")
    has_error = _contains_error(content)

    if is_execute_python or has_error:
        logger.warning(
            "subagent tool=%s args=%s -> %s",
            call["name"],
            call["args"],
            content[:500],
        )
    else:
        logger.info(
            "subagent tool=%s args=%s -> %s",
            call["name"],
            call["args"],
            content[:500],
        )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("subagent tool=%s args=%r -> %r", call["name"], call["args"], tool_message.content)
    followup = image_followup_message(getattr(tool_message, "artifact", None))
    return tool_message, followup


async def _invoke_with_loop_retry(model, messages: list, config: Config, tools: list[BaseTool]):
    """model.ainvoke() を呼び、反復ループを検知したら注意メッセージを注入して再試行する。

    ThinkingLoopDetected（src/llm.py の ChatLlamaCpp がストリーム中に検知して
    送出する）を捕捉し、config.thinking_loop_guard_nudge_messages から選んだ
    メッセージを一時的に追加した**ローカルコピー**で再試行する。サブエージェントの
    会話履歴（呼び出し元の messages）はローカルなPythonリストでLangGraphの
    チェックポイントを一切使わないため、成功しても失敗しても nudge が
    messages 本体に残ることはない（後始末が不要）。

    ループ検知のたびに、app.py の on_message と同様にモデルを再構築してから
    リトライする。壊れたクライアントのまま再試行すると、応答ヘッダーが
    返らず無期限にハングする恐れがある（本番incident・2026-07-20参照。
    src/llm.py の ThinkingLoopDetected docstring 参照）。

    再構築は exc.client_broken の真偽に関わらず無条件で行う。
    client_broken はストリームの後始末(aclose)が例外を送出した場合のみ
    真になるが、httpcoreのTraceフックがクローズ失敗をログするだけで
    再raiseしないケースがあり、その場合client_brokenが立たないまま壊れた
    クライアントを使い回して同種のハングが再発したため（2026-07-21）。

    ただし aclose_active_llm_clients()（on_stop専用）は呼ばない。
    src/llm.py の _active_async_clients は現在セッションID別の辞書に
    なっており、thread_id を指定すれば他タブを巻き添えにせず自セッション
    分だけを強制クローズできる。しかしここ（同一セッション内のリトライ
    経路）ではそれでも呼ばない: dispatch_agentは並列サブエージェント実行
    が既知の機能として存在し、同じセッション内で並行実行中の別サブ
    エージェントやメイングラフが使用中のクライアントまで巻き添えで閉じて
    しまい、"Cannot send a request, as the client has been closed" という
    別の実害を招くため（2026-07-21、app.py側の同種修正で実際に発生し
    確認済み）。build_model()は呼ばれるたびに新しいhttpx.AsyncClientを
    生成するため、壊れた可能性のある旧クライアントは単に参照を手放す
    だけでよい（[llm].no_keepalive_limitsによりコネクションプールでの
    再利用も元々無効化されている）。

    なお build_model() が生成するhttpx.AsyncClientは、呼び出し時点で
    src/llm.py の set_current_session() により設定されているセッションID
    へ自動的に紐づく（asyncio.create_task/gatherが子タスクへcontextvarを
    コピーする性質による。src/tools.py の _IN_SUBAGENT と同型）。そのため
    dispatch_agent → run_subagent 経由のこの再構築は、呼び出し元セッション
    を意識するコードを一切書かなくても、正しいセッションのクライアント
    集合へ登録される。

    Args:
        model: build_model() が構築した（bind_tools 済みの）モデル。
        messages: これまでの会話履歴（変更しない）。
        config: thinking_loop_guard_nudge_messages/_max_retries を含むアプリ設定。
        tools: ループ検知時にモデルを再構築する際 bind_tools に渡すツール一覧。

    Returns:
        (model.ainvoke() が返す AIMessage, 実際に使用したモデルインスタンス)の
        タプル。呼び出し元は次回イテレーション以降、このモデルを使い続けること
        （ループ検知によりモデルが再構築された場合、元の model はもう使えない）。

    Raises:
        ThinkingLoopDetected: max_retries 回再試行してもなお反復ループが
            解消しなかった場合。
    """
    max_retries = config.thinking_loop_guard_max_retries
    local_messages = messages
    current_model = model
    for attempt in range(max_retries + 1):
        try:
            response = await current_model.ainvoke(local_messages)
            return response, current_model
        except ThinkingLoopDetected as exc:
            if attempt >= max_retries:
                raise
            logger.warning(
                "subagent: LLM応答のループを検知（%d回目の再試行）: 直近テキスト=%r [%s]",
                attempt + 1,
                exc.snippet,
                describe_current_task(),
            )
            # app.py の on_message は astream_events を消費して「思考中」Stepを
            # 表示するが、このリトライはここで完結し例外を呼び出し元へ伝播
            # しないため、on_message 側の except ThinkingLoopDetected（自グラフ
            # 自身のループ検知用）は発火しない。対策しないと、打ち切られた
            # 直前の試行分の思考Stepが「停止」バッジ無しで残ったまま、リトライ
            # 後の新しいトークンが同じStepへ継ぎ足されてしまう（2026-08-21
            # ユーザー報告）。astream_events はcontextvar経由でツール内部の
            # 呼び出しも同じストリームへ伝播させる（_resolve_parent_id参照）
            # ため、この adispatch_custom_event もそのストリームに乗り、
            # on_message 側で該当Stepを明示的にクローズ&リセットできる。
            # 呼び出し元にRunnableConfigの親run_idが無い経路（単体テスト等）
            # では失敗しうるが、UI通知はあくまで付加的なものでありリトライ
            # 自体を止めてはならないため、失敗しても握りつぶす。
            try:
                await adispatch_custom_event("subagent_loop_retry", {"snippet": exc.snippet})
            except Exception:  # noqa: BLE001 - UI通知の失敗でリトライ自体を止めない
                logger.debug("subagent_loop_retry イベントの送出に失敗しました", exc_info=True)
            current_model = build_model(config, role="sub").bind_tools(tools)
            logger.warning(
                "subagent: リトライ前にLLMモデルを再構築しました" "（client_broken=%s） [%s]",
                exc.client_broken,
                describe_current_task(),
            )
            text = pick_loop_nudge_message(config.thinking_loop_guard_nudge_messages, attempt)
            local_messages = [*messages, HumanMessage(content=text)]
        except asyncio.CancelledError as exc:
            # サブエージェントがキャンセルされた場合、握りつぶさず
            # 診断ログを出してから再送出する（従来は捕捉していなかった）。
            logger.warning(
                "subagent: asyncio.CancelledError を検知 [%s, cause=%r]",
                describe_current_task(),
                repr(exc.__cause__),
            )
            raise
    raise AssertionError("unreachable")  # pragma: no cover


async def _invoke_with_empty_response_retry(model, messages: list, config: Config, tools: list[BaseTool]):
    """_invoke_with_loop_retry を呼び、tool_calls も本文も空の応答を検知したら再試行する。

    llama.cpp サーバーがまれに tool_calls・content ともに空の AIMessage を
    返すことがある（本番incident・2026-07-23、`explore` サブエージェントが
    8回目の反復で空応答を返し、run_subagent がそれを「正常終了」として
    空文字列をそのまま委譲元へ返し、それまでの調査結果が丸ごと失われた）。
    run_subagent は「tool_calls が空 = 最終回答」とみなす設計のため、この
    チェックを挟まないと同じ事象を無検査で通してしまう。

    リトライ時に注入する nudge メッセージはローカルコピーにのみ追加し、
    呼び出し元の messages 本体は変更しない（_invoke_with_loop_retry と
    同じ方針。失敗した試行を永続履歴に残さない）。

    Args:
        model: build_model() が構築した（bind_tools 済みの）モデル。
        messages: これまでの会話履歴（変更しない）。
        config: subagent_empty_response_max_retries を含むアプリ設定。
        tools: _invoke_with_loop_retry がループ検知時にモデルを再構築する
            際、bind_tools に渡す。

    Returns:
        (最終的な AIMessage, 使用したモデルインスタンス, 空応答のまま
        リトライ上限に達したかどうかの bool) のタプル。3番目が True の
        場合、返される AIMessage は空応答のままなので、呼び出し元は
        これを正常終了として扱ってはならない。
    """
    max_retries = config.subagent_empty_response_max_retries
    local_messages = messages
    current_model = model
    for attempt in range(max_retries + 1):
        response, current_model = await _invoke_with_loop_retry(current_model, local_messages, config, tools)
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls or str(response.content).strip():
            return response, current_model, False
        if attempt >= max_retries:
            return response, current_model, True
        logger.warning(
            "subagent: 空の応答（tool_calls無し・本文無し）を検知したため再試行します" "(%d/%d回目)",
            attempt + 1,
            max_retries,
        )
        local_messages = [
            *local_messages,
            response,
            HumanMessage(content=_EMPTY_RESPONSE_NUDGE_TEXT),
        ]
    raise AssertionError("unreachable")  # pragma: no cover


async def _invoke_with_timeout_retry(
    model, messages: list, config: Config, tools: list[BaseTool], max_retries: int
):
    """_invoke_with_empty_response_retry を呼び、LLM呼び出しタイムアウトを検知したら再試行する。

    run_subagent は元々、1回でも TimeoutError/LLM_CONNECTION_ERRORS が起きると
    その反復までの内容を要約して即座に打ち切っていた。しかし dispatch_agent の
    ように長時間動くジョブがバックグラウンドタスクとして裏側で動き続ける実行では、
    数百件中のたった1回の一時的なタイムアウト（llama-serverの瞬間的な混雑等）
    で残り全件分の進捗を諦めてしまうのは本末転倒。max_retries>0 の場合のみ、
    _invoke_with_loop_retry と同じ「モデルを再構築してから同じ内容で再試行する」
    パターンで温存する。

    max_retries=0（既定）では従来どおり初回のタイムアウトで例外がそのまま
    呼び出し元（run_subagent）へ伝播する。この既定値は明示的に retries を
    指定しない呼び出し元向けの安全側デフォルトであり、dispatch_agent は
    常に非ゼロの値を明示的に渡す（下記 llm_timeout_max_retries 参照）。

    Args:
        model: build_model() が構築した（bind_tools 済みの）モデル。
        messages: これまでの会話履歴（変更しない）。
        config: LLM 接続情報を含むアプリ設定。
        tools: モデル再構築時に bind_tools へ渡す。
        max_retries: タイムアウト時に再試行する最大回数。

    Returns:
        _invoke_with_empty_response_retry と同じ
        (AIMessage, 使用したモデルインスタンス, 空応答のままリトライ上限に
        達したかどうかの bool) のタプル。

    Raises:
        TimeoutError, LLM_CONNECTION_ERRORS: max_retries 回再試行してもなお
            タイムアウト/接続エラーが解消しなかった場合。openai SDK が
            httpx のread timeoutを openai.APITimeoutError（openai.APIConnectionError
            のサブクラス）へラップして再送出するケースがあり、単純な
            (TimeoutError, httpx.TimeoutException) では取りこぼすため
            LLM_CONNECTION_ERRORS（src/llm.py）を合わせて捕捉する
            （本番incident・2026-08-21: このラップにより本リトライが
            一度も発動せず、dispatch_agent が生のトレースバックで
            即失敗した）。
    """
    current_model = model
    for attempt in range(max_retries + 1):
        try:
            return await _invoke_with_empty_response_retry(current_model, messages, config, tools)
        except (TimeoutError, *LLM_CONNECTION_ERRORS) as exc:
            if attempt >= max_retries:
                raise
            logger.warning(
                "dispatch_agent: LLM呼び出しがタイムアウトしたため再試行します" "(%d/%d回目): %s",
                attempt + 1,
                max_retries,
                exc,
            )
            current_model = build_model(config, role="sub").bind_tools(tools)
    raise AssertionError("unreachable")  # pragma: no cover


def _extract_total_tokens(response: AIMessage) -> int | None:
    """AIMessage.usage_metadata から total_tokens を取り出す。

    config.track_token_usage=False の場合や、サーバーが usage を返さない
    場合は usage_metadata が None になりうる（src/llm.py build_model 参照）。

    Args:
        response: model.ainvoke() が返した AIMessage。

    Returns:
        total_tokens の値。usage_metadata が無い/キーが無ければ None。
    """
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None
    return usage.get("total_tokens")


_TRUNCATION_PREFIX = "[サブエージェント: "


def is_truncated_result(content: object) -> bool:
    """dispatch_agent の戻り値が run_subagent の打ち切りによるものかを判定する。

    _build_truncation_message() が生成した文字列（max_iterations到達・
    空応答連続・トークン閾値超過・LLMタイムアウトのいずれか）かどうかを
    先頭プレフィックスで判定する。app.py の on_tool_end から、打ち切りを
    UI（stopped_reasonバッジ）とユーザー通知に反映するために使う。
    """
    return isinstance(content, str) and content.startswith(_TRUNCATION_PREFIX)


def _build_truncation_message(reason: str, messages: list) -> str:
    """打ち切り時の共通メッセージを組み立てる（反復上限・トークン閾値超過で共用）。

    Args:
        reason: 打ち切り理由の文言（例: "最大反復回数(50)に達した"、
            "トークン使用量が上限(55000トークン)に達した"）。文末に
            「ため打ち切りました」が続く前提で渡すこと。
        messages: これまでの会話履歴。打ち切り直前の最後のメッセージ
            （AIMessageなら content）と、_collect_tool_results_summary()
            によるツール結果要約の抽出に使う。

    Returns:
        打ち切り理由に続けて、直前のAIMessage.content（あれば）と、
        収集済みツール結果要約（あれば）を連結した文字列。
    """
    last = messages[-1]
    last_text = last.content if isinstance(last, AIMessage) else ""
    parts = [f"{_TRUNCATION_PREFIX}{reason}ため打ち切りました]"]
    if last_text:
        parts.append(last_text)
    collected = _collect_tool_results_summary(messages)
    if collected:
        parts.append(
            "[ここまでに収集できたツール実行結果（打ち切りにより要約や整理は未完了）。"
            "委譲元はこれを踏まえて続きの調査が必要か判断すること]\n" + collected
        )
    return "\n\n".join(parts)


def _build_llm_input(messages: list, config: Config) -> list:
    """会話履歴からLLM入力を組み立てる。[context_trim] が有効かつ
    trigger_total_tokens の閾値に達していれば、graph.py の pre_model_hook /
    call_model と同じロジックで古い ToolMessage/AIMessage を間引く
    （Claude Codeがメイン会話・サブエージェントを区別せず同一の
    コンテキスト管理を適用するのに倣い、メインエージェントと同じ設定・
    同じ関数をサブエージェントのローカル履歴にも適用する）。

    呼び出し元の messages 本体は書き換えない（トリム結果はこの関数呼び出し
    1回分のLLM入力としてのみ使う）。
    """
    if not config.context_trim_enabled or not is_trigger_reached(
        messages, config.context_trim_trigger_total_tokens
    ):
        return messages
    trimmed = trim_old_tool_messages(
        messages,
        keep_recent=config.context_trim_keep_recent_tool_messages,
        max_chars=config.context_trim_truncated_max_chars,
        guarded_tool_max_chars=config.context_trim_duplicate_guard_tool_max_chars,
    )
    if config.context_trim_ai_messages:
        trimmed = trim_old_ai_messages(
            trimmed,
            keep_recent=config.context_trim_keep_recent_ai_messages,
            max_chars=config.context_trim_truncated_max_chars,
        )
    return trimmed


async def run_subagent(
    task: str,
    tools: list[BaseTool],
    system_prompt: str,
    config: Config,
    max_iterations: int,
    on_iteration: Callable[[int, int], None] | None = None,
    llm_timeout_max_retries: int = 0,
) -> str:
    """独立した ReAct ループでタスクを処理し、最終回答のテキストのみを返す。

    tool_calls が空になった時点の AIMessage.content を返す。ただし content も
    空（tool_calls無し・本文も空のLLM異常応答）だった場合は
    _invoke_with_empty_response_retry により config.subagent_empty_response_max_retries
    回まで再試行し、それでも解消しなければ正常終了として空文字列を返さず、
    max_iterations 到達時と同じ要約フォーマットで打ち切る（本番incident・
    2026-07-23: 空応答を無検査で正常終了扱いし、それまでの調査結果が
    丸ごと失われた事象への対処）。max_iterations に達してもループが
    終わらない場合も、その時点までの最後の応答に打ち切りである旨を
    明記して返す。ツール実行時の例外はここで捕捉してエラー文字列化し
    ループを継続する（例外を外へ送出しない）。

    config.subagent_token_guard_enabled が True（かつ track_token_usage が
    True）の場合、応答の usage_metadata.total_tokens を監視し、
    subagent_token_guard_soft_threshold 到達時に一度だけ注意メッセージを
    注入し、それでも subagent_token_guard_hard_threshold まで超過が続いた
    場合は max_iterations 到達時と同じ要約フォーマットで打ち切る
    （LLM自身は自分のトークン使用量を認識できないため、コード側で判定する）。

    Args:
        task: サブエージェントに委譲するタスクの説明文。
        tools: サブエージェントが使えるツールのリスト（dispatch_agent
            自身は含めないこと。含めると再帰委譲が可能になってしまう）。
        system_prompt: サブエージェント用のシステムプロンプト全文。
        config: LLM 接続情報を含むアプリ設定。build_model 経由でモデル構築に使う。
        max_iterations: agent→tools の遷移を許す最大回数。
        on_iteration: 各反復で応答を得るたびに `(iteration, max_iterations)` を
            渡して呼ぶ同期コールバック（省略可）。dispatch_agent が
            進捗表示（現在の反復回数）をジョブオブジェクトへ書き込むために使う。
            戻り値は無視する。例外は送出しない前提（呼び出し元の責任）。
        llm_timeout_max_retries: LLM呼び出しが [llm].request_timeout_seconds/
            stream_chunk_timeout_seconds に達した場合、モデルを再構築してから
            同じ反復を再試行する最大回数。既定0＝明示的に指定しない呼び出し元
            向けの安全側デフォルト（初回のタイムアウトで即座に打ち切る）。
            dispatch_agent は人間がターン内でリアルタイムに待つ間もジョブ自体は
            バックグラウンドタスクとして動き続けるため、より大きい値を渡して
            耐性を上げる（_invoke_with_timeout_retry 参照）。

    Returns:
        サブエージェントの最終回答テキスト。
    """
    model = build_model(config, role="sub").bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}
    messages: list = [SystemMessage(content=system_prompt), HumanMessage(content=task)]

    logger.info("dispatch_agent 開始: task=%r max_iterations=%d", task, max_iterations)

    token_guard_enabled = config.subagent_token_guard_enabled and config.track_token_usage
    soft_warning_issued = False
    # [context_compaction] もメインエージェントと同じ設定を使ってサブエージェントの
    # ローカル履歴にも適用する（Claude Code方式。src/context_compaction.py 参照）。
    # トークン使用量が取得できない場合（track_token_usage=false）は
    # should_compact() が常にFalseを返すため実質無効化される。
    compaction_enabled = config.context_compaction_enabled and config.track_token_usage
    cumulative_tokens_sub = 0

    for iteration in range(1, max_iterations + 1):
        llm_input = _build_llm_input(messages, config)
        try:
            response, model, empty_retries_exhausted = await _invoke_with_timeout_retry(
                model, llm_input, config, tools, llm_timeout_max_retries
            )
        except (TimeoutError, *LLM_CONNECTION_ERRORS) as exc:
            logger.warning(
                "dispatch_agent: LLM呼び出しがタイムアウトしたため打ち切り(iter=%d): %s",
                iteration,
                exc,
            )
            return _build_truncation_message(f"LLM呼び出しがタイムアウトした({exc})", messages)
        messages.append(response)
        logger.info("subagent iter=%d ai=%r", iteration, str(response.content)[:500])
        if on_iteration is not None:
            on_iteration(iteration, max_iterations)

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            if empty_retries_exhausted:
                logger.warning(
                    "dispatch_agent: 空の応答が%d回続いたため打ち切り (iter=%d)",
                    config.subagent_empty_response_max_retries + 1,
                    iteration,
                )
                return _build_truncation_message(
                    f"LLMが空の応答を{config.subagent_empty_response_max_retries + 1}" "回連続で返した",
                    messages,
                )
            logger.info("dispatch_agent 正常終了: %d回で完了", iteration)
            return response.content

        total_tokens = _extract_total_tokens(response) if config.track_token_usage else None
        if total_tokens is not None:
            cumulative_tokens_sub += total_tokens

        if token_guard_enabled and soft_warning_issued and total_tokens is not None and total_tokens >= config.subagent_token_guard_hard_threshold:
            logger.warning(
                "dispatch_agent: トークン使用量が閾値(%d)に達したため打ち切り" "(iter=%d, total_tokens=%d)",
                config.subagent_token_guard_hard_threshold,
                iteration,
                total_tokens,
            )
            return _build_truncation_message(
                "トークン使用量が上限" f"({config.subagent_token_guard_hard_threshold}トークン)に達した",
                messages,
            )

        results = await asyncio.gather(*(_run_one_tool_call(call, tools_by_name) for call in tool_calls))
        for tool_message, followup in results:
            messages.append(tool_message)
            if followup is not None:
                messages.append(followup)

        if compaction_enabled and should_compact(
            {"total": cumulative_tokens_sub},
            {"total_tokens": total_tokens},
            len(messages),
            config,
        ):
            # 圧縮用モデルはツール未bindの素のインスタンスを使う（本編の model は
            # bind_tools 済みで、要約専用の呼び出しにツール定義を含める必要が
            # 無いため。src/context_compaction.py の maybe_compact docstring参照）。
            summary_model = build_model(config, role="sub")
            # messages[0] は run_subagent 開始時に積んだ SystemMessage。graph.py の
            # メインエージェントは system_prompt を state["messages"] に含めず
            # call_model 側で毎回付け足す構造のため要約対象から自然に外れるが、
            # サブエージェントの messages はローカルリストの先頭に SystemMessage を
            # 保持する構造が異なる。除外せずに渡すと要約で先頭が切り捨てられた際に
            # サブエージェントが以後システムプロンプト（役割・ツール方針等）を
            # 失ってしまうため、常に保持対象として明示的に除外してから渡す。
            new_tail = await maybe_compact(messages[1:], summary_model, config)
            if new_tail is not None:
                logger.info(
                    "subagent: 会話履歴を圧縮しました (iter=%d) [%s]",
                    iteration,
                    describe_current_task(),
                )
                messages = [messages[0], *new_tail]
                cumulative_tokens_sub = 0

        if (
            token_guard_enabled
            and not soft_warning_issued
            and total_tokens is not None
            and total_tokens >= config.subagent_token_guard_soft_threshold
        ):
            messages.append(HumanMessage(content=config.subagent_token_guard_soft_warning_text))
            soft_warning_issued = True
            logger.warning(
                "subagent: トークン使用量が閾値(%d)に近づいたため注意メッセージを注入" "(iter=%d, total_tokens=%d)",
                config.subagent_token_guard_soft_threshold,
                iteration,
                total_tokens,
            )

    logger.warning("dispatch_agent: 最大反復回数(%d)に到達したため打ち切り", max_iterations)
    return _build_truncation_message(f"最大反復回数({max_iterations})に達した", messages)


_TOOL_RESULT_SNIPPET_LIMIT = 1500
# snippets 全体（結合後）の上限文字数。大量ファイルの調査中に打ち切られた
# 場合、個々のスニペットを1500字に切り詰めても件数が多ければ合計は
# 際限なく増える（実例: 30件超のファイルを読んだ後に打ち切られ、合計
# 数万字が呼び出し元へ返り、呼び出し元自身のトークン上限を圧迫した）。
# 委譲による分離効果を保つため、直近の結果を優先して全体もここで切る。
_TOOL_RESULT_TOTAL_LIMIT = 6000


def _collect_tool_results_summary(messages: list) -> str:
    """反復上限打ち切り時に、それまでの ToolMessage 内容を軽量に要約する。

    実データ規模の探索（例: 100件超のファイル一覧取得）で反復上限に達すると、
    せっかく収集できていた生データが呼び出し元に一切引き継がれず丸ごと
    破棄される事象が確認された（tune-prompt調査、2026-07-18）。ここでは
    完全な集約ロジックは持たず、各 ToolMessage の内容を一定文字数で
    切り詰めて列挙するだけに留める（呼び出し元の会話がさらに肥大化し
    すぎないようにするため）。結合後の合計も _TOOL_RESULT_TOTAL_LIMIT で
    切り詰め、直近の結果（打ち切り直前の状況に近く、続きの判断に有用）を
    優先して残す。

    view_image の ToolMessage.content は「画像を読み込みました: <path>」
    という固定文言のみで、画像から実際に読み取った内容は、その直後に
    続く AIMessage（画像を見た直後にモデルが生成する説明文）にしか
    含まれない。ToolMessage だけを集めると、反復上限で打ち切られた際に
    この説明が丸ごと失われ、呼び出し元（メインエージェント）が
    「サブエージェントの結果は使えない」と判断して同じファイルを自力で
    再確認せざるを得なくなる副作用が大きいことが確認された
    （tune-prompt iter27、021ケース: evals/tuning_log.md参照）。そのため、
    連続する ToolMessage 群の直後に続く最初の AIMessage.content
    （モデル自身の解釈・説明）も1回だけ併記する。

    Args:
        messages: これまでの会話履歴（SystemMessage/HumanMessage/AIMessage/
            ToolMessage の列）。

    Returns:
        "- ツール=<name>: <内容（省略あり）>" の行に、直後の AIMessage の
        説明があれば "  → モデルの解釈: <内容（省略あり）>" を続けて
        改行で連結した文字列。ToolMessage が1件も無ければ空文字列。
    """
    snippets = []
    n = len(messages)
    i = 0
    while i < n:
        if not isinstance(messages[i], ToolMessage):
            i += 1
            continue
        # 並列 tool_calls 分の連続する ToolMessage をまとめて処理する。
        while i < n and isinstance(messages[i], ToolMessage):
            content = str(messages[i].content)
            if len(content) > _TOOL_RESULT_SNIPPET_LIMIT:
                content = content[:_TOOL_RESULT_SNIPPET_LIMIT] + "...[truncated]"
            name = getattr(messages[i], "name", None) or "?"
            snippets.append(f"- ツール={name}: {content}")
            i += 1
        # このツール群の直後（view_image の画像 followup である
        # HumanMessage は飛ばす）に続く最初の AIMessage があれば、
        # モデル自身の解釈として1回だけ併記する。
        for j in range(i, n):
            nxt = messages[j]
            if isinstance(nxt, ToolMessage):
                break
            if isinstance(nxt, AIMessage):
                ai_content = str(nxt.content).strip()
                if ai_content:
                    if len(ai_content) > _TOOL_RESULT_SNIPPET_LIMIT:
                        ai_content = ai_content[:_TOOL_RESULT_SNIPPET_LIMIT] + "...[truncated]"
                    snippets.append(f"  → モデルの解釈: {ai_content}")
                break
    joined = "\n".join(snippets)
    if len(joined) > _TOOL_RESULT_TOTAL_LIMIT:
        joined = "(too many results, first part omitted. showing recent results only)\n...\n" + joined[-_TOOL_RESULT_TOTAL_LIMIT:]
    return joined
