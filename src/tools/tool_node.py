"""ImageAwareToolNode と、メイングラフのツール呼び出しガード。"""

from __future__ import annotations

from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode
import chainlit as cl
import logging

from . import _state
from ._duplicate_guard import _record_and_check_duplicate
from ._state import _IN_SUBAGENT, _tool_call_semaphore_wrap
from .analyze_image import _with_image_followups

logger = logging.getLogger(__name__)


def _extract_tool_call_from_node_input(input) -> dict | None:  # noqa: A002
    """ImageAwareToolNode.invoke/ainvoke が受け取る入力から、今回実行対象の
    単一 tool_call を取り出す。

    LangGraph は並列 tool_calls 実行のため、AIMessage の tool_calls を
    まとめてバッチで渡すのではなく、個々の tool_call を1件ずつ
    {"__type": "tool_call_with_context", "tool_call": {...}, "state": {...}}
    という形で渡してくる（ToolNode の公開 docstring が説明する
    dict/list/tool_calls-list の3形式には無い、実際に確認した挙動）。
    この形でなければ None を返す。
    """
    if isinstance(input, dict) and input.get("__type") == "tool_call_with_context":
        return input.get("tool_call")
    return None


def _log_tool_calls_debug(input) -> None:  # noqa: A002
    """メイングラフのツール呼び出し（呼び出し前）を DEBUG レベルで記録する。

    config.ini の [log].level が "debug" のときのみ実際にログへ出る
    （logger.isEnabledFor で早期リターンし、通常時はほぼゼロオーバーヘッド）。
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return
    logger.debug("tool_call: name=%s args=%r id=%s", call.get("name"), call.get("args"), call.get("id"))


def _contains_error(content: str) -> bool:
    """文字列にエラーを示すキーワードが含まれるかを判定する。

    日本語「エラー」、英語「error」(大文字小文字区別なし)、全角カタカナ
    「ｴﾗｰ」に対応する。
    """
    content_lower = content.lower()
    return "エラー" in content or "error" in content_lower or "ｴﾗｰ" in content


def _log_tool_results_debug(result: dict, call_args: dict | None = None) -> None:
    """メイングラフのツール呼び出し結果を DEBUG レベルで記録する（全文、未省略）。

    execute_python_* 系ツールは成功時も WARNING（スキル開発アイデアの
    シグナルとして記録。代替スキルが作られれば LLM は呼ばなくなる）。
    エラーキーワードを含むメッセージも WARNING。
    execute_python_code の場合は call_args に code が含まれるため、
    WARNINGログにも含める（monitor-app-log スキルでissue起票可能にする）。
    """
    for msg in result.get("messages", []):
        name = getattr(msg, "name", None) or ""
        content = msg.content or ""
        is_execute_python = name.startswith("execute_python_")
        has_error = _contains_error(content)

        if is_execute_python or has_error:
            if is_execute_python and call_args and "code" in call_args:
                logger.warning(
                    "tool_result: name=%s args_code=%r content=%r",
                    name,
                    call_args["code"][:1000],
                    content[:500],
                )
            else:
                logger.warning("tool_result: name=%s content=%r", name, content[:500])
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("tool_result: name=%s content=%r", name, content)


_ALLOWED_WHILE_AWAITING_APPROVAL = {"approve_plan", "get_plan_status", "lock_plan_mode"}
# このうち呼ばれたらガードのフラグ自体を解除するもの（それ以外
# （get_plan_status）は読み取り専用の確認だけなので、フラグは維持したまま
# 通過させる）。
_CLEARS_AWAITING_APPROVAL = {"approve_plan", "lock_plan_mode"}


def _guard_awaiting_approve_plan(input):  # noqa: A002
    """create_plan 直後、approve_plan/get_plan_status/lock_plan_mode 以外の
    tool_calls を実行させず、合成 ToolMessage に差し替える。

    本番ログ・evalで、create_plan の直後に規定の approve_plan ではなく
    ask_user_choice 等を自作して承認確認の代わりに使ってしまう事例が確認された
    （システムプロンプトの指示のみでは確実性が無いため、コード側でも強制する）。
    ただし、モデルが調査不足に自分で気づき lock_plan_mode で計画をやり直そうと
    する自己修正（evalで実際に観測済み）は正当な経路として許可する。
    ガード対象でなければ None を返す（呼び出し側はこの場合、通常通り
    super().ainvoke()/invoke() を呼ぶこと）。

    Args:
        input: ImageAwareToolNode.invoke/ainvoke がそのまま受け取った入力
            （_extract_tool_call_from_node_input 参照）。

    Returns:
        ブロックする場合は ToolNode.invoke/ainvoke と同じ形式の結果
        （{"messages": [ToolMessage]}）。ガード対象外・許可リスト内の
        呼び出しの場合は None。
    """
    if not cl.user_session.get("awaiting_approve_plan_call"):
        return None
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return None
    name = call.get("name")
    if name in _ALLOWED_WHILE_AWAITING_APPROVAL:
        if name in _CLEARS_AWAITING_APPROVAL:
            cl.user_session.set("awaiting_approve_plan_call", False)
        return None
    # フラグはクリアしない（モデルが approve_plan/lock_plan_mode を呼ぶまで、
    # 次の呼び出しも引き続きブロックする。plan_approved ガードと同様、
    # 明示的に解除されるまでブロックし続ける設計）。
    return {
        "messages": [
            ToolMessage(
                content=("エラー: create_planの直後はapprove_planを呼んでください" "（他のツールは実行されませんでした）。"),
                name=name,
                tool_call_id=call.get("id"),
                status="error",
            )
        ]
    }


def _guard_main_agent_tool_limit(input):  # noqa: A002
    """[main_agent_tool_guard] の許可リストに基づき、メインエージェントが直接
    呼べるツールとその回数を制限し、dispatch_agentへの委譲を促すガード。

    旧実装は Glob専用（_check_main_agent_glob_limit）・run_script専用
    （旧 _check_main_agent_run_script_limit）と対象ごとにコードを決め打ちして
    いたが、実際にトークン上限へ追い込んだ主因は run_script の連打ではなく
    その後 analyze_image をメインエージェント自身が委譲せずページ数分連打した
    ことだった（英検3級PDF調査、2026-08-10）。ビルトインツールを一切問わず
    任意の名前を登録できるよう、ImageAwareToolNode.invoke/ainvoke の共通
    差し込みポイント（_guard_awaiting_approve_plan と同じ場所）でツール名
    そのものを判定する汎用ガードに統合し、Globガードもここへ統合済み。

    本ガードは許可リスト（ホワイトリスト）方式: [main_agent_tool_guard].allow_entries
    に登録されていないツール名・run_scriptスキルスクリプトは、メインエージェント
    から一切呼び出せない（登録＝完全ブロックの0や、登録すらされていない場合を
    区別しない。どちらも「呼べない」という結果は同じ）。そのためメインエージェントの
    基本運用に必須なツール（dispatch_agent・create_plan・ask_user_question等）も
    含め、config.ini 側で明示的に登録しておく必要がある（多くは max_calls=-1
    の無制限指定で登録する）。

    登録形式（[main_agent_tool_guard].allow_entries）の各要素は
    [対象, max_calls] の2要素で、対象は2種類を許容する:
      - 文字列1件（例: "Glob", "analyze_image"）: そのツール名の呼び出し
        そのものを引数を問わず対象にする。
      - [スキル名, スクリプトファイル名] の2要素（例:
        ["pdf-tools","render_pdf_pages.py"]）: name が run_script/
        run_script_background で、かつ args の skill_name/script_filename が
        一致する場合のみ対象にする。
    max_calls はエントリごとに個別指定する（ツールごとに許容回数を変えたい
    ケースがあるため、旧実装のような全体共通の1値ではない）。
      - 0  : 登録はするが完全ブロック（1回も呼べない）。
      - -1 : 登録した上で回数無制限に許可する。
      - 1以上: その回数まで許可し、超過分を拒否する。
    他の呼び出し回数ガード（_check_file_tools_duplicate が使う
    _record_and_check_duplicate）は0以下を「無制限（ガード無効）」として
    扱うが、本ガードは0と-1の意味が逆になる点に注意（このガード固有の
    意味論であり、他の呼び出し回数ガードには影響しない）。

    サブエージェント（dispatch_agent経由）内部での呼び出しは対象外
    （_IN_SUBAGENT が True の間はガードしない。調査そのものが役目のため）。

    Args:
        input: ImageAwareToolNode.invoke/ainvoke がそのまま受け取った入力
            （_extract_tool_call_from_node_input 参照）。

    Returns:
        呼び出せない場合はブロックする結果（{"messages": [ToolMessage]}）、
        呼び出してよい場合は None（呼び出し側は通常通りツールを実行してよい）。
    """
    cfg = _state._LLM_CONFIG
    guard_enabled = cfg.main_agent_tool_guard_enabled if cfg else False
    if not guard_enabled:
        return None
    if _IN_SUBAGENT.get():
        return None
    entries = cfg.main_agent_tool_guard_allow_entries if cfg else frozenset()
    entries_by_key = dict(entries)
    call = _extract_tool_call_from_node_input(input)
    if not call:
        return None
    name = call.get("name")
    args = call.get("args") or {}
    signature: str | None = None
    guard_max_calls: int | None = None
    if name in entries_by_key:
        signature = name
        guard_max_calls = entries_by_key[name]
    elif name in ("run_script", "run_script_background"):
        pair = (args.get("skill_name"), args.get("script_filename"))
        if pair in entries_by_key:
            signature = f"{name}:{pair[0]}/{pair[1]}"
            guard_max_calls = entries_by_key[pair]
    if signature is None:
        # 許可リストに存在しない＝メインエージェントからは一切呼び出せない。
        content = f"エラー: {name} はメインエージェントとして許可されていません（main_agent_tool_guard.allow_entries未登録）。"
    elif guard_max_calls == -1:
        return None
    elif guard_max_calls <= 0:
        content = f"エラー: {name} はメインエージェントとして呼び出しを禁止されています（max_calls=0）。"
    elif not _record_and_check_duplicate("main_agent_tool_guard_call_count", signature, guard_max_calls):
        return None
    else:
        content = f"エラー: {name} はメインエージェントとして既に呼び出し上限（{guard_max_calls}回）に達しています。"
    # agent_type一覧はagents/*.mdの走査結果（_state._AGENT_TYPES）から動的に組み立てる。
    # 種別を文言へ決め打ちすると、将来 agent_type が増減しても案内が追従せず、
    # 存在しない/古い種別へ誘導してしまう（dispatch_agentの不明agent_typeエラー
    # メッセージと同じ組み立て方、2972行目付近参照）。
    available_agent_types = ", ".join(sorted(_state._AGENT_TYPES)) or "dispatch_agentのagent_type一覧を確認"
    return {
        "messages": [
            ToolMessage(
                content=(
                    f"{content}これ以上自分で実行せず、残りの調査・処理は dispatch_agent（agent_type: {available_agent_types}）へ委譲してください。"
                ),
                name=name,
                tool_call_id=call.get("id"),
                status="error",
            )
        ]
    }


class ImageAwareToolNode(ToolNode):
    """analyze_image の実行結果（画像）を、後続の HumanMessage として自動追加する ToolNode。

    OpenAI互換API の tool role メッセージは文字列content しか持てないため、
    画像を持つ ToolMessage.artifact をそのまま次のモデル呼び出しに含めることは
    できない。そこで ToolNode 実行後に _with_image_followups() で後処理し、
    画像を content に持つ HumanMessage を会話履歴へ追加する。
    handwritten/prebuilt いずれのグラフ実装でも、素の ToolNode の代わりに
    このクラスを使うだけで画像受け渡しに対応できる。

    また ToolNode の公式拡張点 awrap_tool_call 経由で、全ツール呼び出しを
    セッション毎の Semaphore（_state._TOOL_CALL_SEMAPHORES）によりガードする
    （_tool_call_semaphore_wrap 参照。ToolNode._afunc が同一AIMessage内の
    複数tool_callsを asyncio.gather() で完全並列実行する挙動への対策。
    dispatch_agent 専用の _state._DISPATCH_AGENT_SEMAPHORES と同じ理由づけの
    メインエージェント版）。
    """

    def __init__(self, tools, **kwargs):
        kwargs.setdefault("awrap_tool_call", _tool_call_semaphore_wrap)
        super().__init__(tools, **kwargs)

    def invoke(self, input, config=None, **kwargs):  # noqa: A002
        _log_tool_calls_debug(input)
        call_args = _extract_tool_call_from_node_input(input)
        if call_args:
            call_args = call_args.get("args", {})
        blocked = _guard_awaiting_approve_plan(input) or _guard_main_agent_tool_limit(input)
        if blocked is not None:
            _log_tool_results_debug(blocked, call_args)
            return _with_image_followups(blocked)
        result = super().invoke(input, config, **kwargs)
        _log_tool_results_debug(result, call_args)
        return _with_image_followups(result)

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: A002
        _log_tool_calls_debug(input)
        call_args = _extract_tool_call_from_node_input(input)
        if call_args:
            call_args = call_args.get("args", {})
        blocked = _guard_awaiting_approve_plan(input) or _guard_main_agent_tool_limit(input)
        if blocked is not None:
            _log_tool_results_debug(blocked, call_args)
            return _with_image_followups(blocked)
        result = await super().ainvoke(input, config, **kwargs)
        _log_tool_results_debug(result, call_args)
        return _with_image_followups(result)
