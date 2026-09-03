"""create_plan ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl
import logging
import uuid

from ..plan_persist import persist_plan_state, plan_message_id

from . import _state
from ._plan_render import _render_plan_payload, _write_plan_detail

logger = logging.getLogger(__name__)


@tool
async def create_plan(steps: list[dict[str, str]], detail_markdown: str | None = None) -> str:
    """複数ステップの実行計画を作成し、ユーザーへチェックリストとして表示する。

    書き込み系ツール（run_script、run_script_background、execute_python_code、
    execute_python_code_background）を1回でも使うタスクに着手する前に、まず
    このツールでステップ一覧を提示する。作成しただけでは書き込み系ツールの
    ブロックは解除されない。承認を得るには続けて approve_plan を呼ぶこと。

    設定（既定）により、このツールを呼ぶ前に同一ターンで
    dispatch_agent(agent_type="planner") が完了している必要がある
    （未実施の場合はエラーを返しブロックする）。調査で得た具体的事実と
    ユーザー要求をplannerへ丸投げし、その草案を確認・調整してから steps/
    detail_markdown を確定させること。自分の記憶・推測だけでsteps文言
    （行番号・セル位置等の具体的数値を含む）を作らない。plannerが情報不足を
    理由に草案を返さなかった場合もこのツールはブロックされる（不足情報を
    調査してからplannerを呼び直すこと。この場合の応答を無視してsteps/
    detail_markdownを自作しても通らない）。

    既に approve_plan で承認済み（Edit Automatically）の状態でこのツールを
    再度呼んで steps を差し替えると、config.ini の
    [plan].reset_approval_on_recreate の設定によっては承認状態が失われ
    Plan Mode（未承認）へ戻る（既定は失われる側）。その場合、続けて
    approve_plan を呼び直さない限り書き込み系ツールは再びブロックされる。

    run_script_background/execute_python_code_background でバックグラウンド
    ジョブを扱う場合、「起動」と「完了確認」を同じステップにまとめないこと。
    ジョブを起動しただけではまだ処理は終わっていないため、起動ステップを
    completed にしてよいのは check_script_job で最終結果（running 以外の
    状態）を取得できてから。起動ステップとは別に「結果を確認する」ステップを
    設けること。

    detail_markdown を渡すと、steps とは別にメインチャットへそのまま表示され、
    かつ data/plans/ 配下へ詳細な計画Markdownファイルとしても保存される
    （会話スレッドごとに1ファイル、呼ぶたびに上書き更新。update_task_progress
    では更新されない）。パネル表示のチェックリストに収まらない背景・設計判断・
    調査結果等をユーザーに見せたい複雑なタスクでは、この引数も渡すことを
    推奨する（省略してもエラーにはならない）。

    Args:
        steps: 実行計画の各ステップを表す辞書のリスト（1件以上、実行順）。
            各辞書は次の2キーを持つこと。
            - content: ステップの内容（例: "設定ファイルを読み込む"）。
            - activeForm: 実行中（in_progress）の間だけチェックリストに
              表示する現在進行形の説明（例: "設定ファイルを読み込み中"）。
        detail_markdown: 詳細な実行計画を記した任意のMarkdown本文
            （背景・設計判断・調査結果等、自由形式）。省略時（None または
            空文字）はファイルを保存しない。

    Returns:
        計画を作成した旨とステップ件数を伝えるテキスト。detail_markdown を
        渡した場合は保存先の絶対パスも併記する。steps が空、または
        いずれかの要素に content / activeForm が欠けている場合は、例外を
        送出せず「エラー: ...」形式の文字列を返す。
    """
    if not steps:
        return "エラー: steps が空です。1件以上のステップを指定してください。"
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("content") or not s.get("activeForm"):
            return f"エラー: steps[{i}] には content と activeForm の両方を" f"文字列で指定してください: {s!r}"
    if _state._PLAN_REQUIRE_PLANNER_DISPATCH and not cl.user_session.get("planner_dispatched_since_plan"):
        if cl.user_session.get("planner_info_insufficient"):
            return (
                "エラー: plannerが情報不足のため計画草案を返しませんでした。"
                "create_planは呼べません。plannerの回答に書かれた不足情報を"
                "先に調査し、その事実を添えてdispatch_agent(agent_type=\"planner\")"
                "を呼び直してください。"
            )
        return (
            "エラー: create_planの前にdispatch_agent(agent_type=\"planner\")を"
            "呼んでください。調査で得た具体的事実とユーザー要求をplannerへ"
            "過不足なく伝え、計画の草案を作らせてからcreate_planを呼び直すこと"
            "（自分の記憶・推測だけでsteps/detail_markdownを構成しない）。"
        )
    cl.user_session.set("planner_dispatched_since_plan", False)
    cl.user_session.set("planner_info_insufficient", False)
    plan = [{"content": s["content"], "activeForm": s["activeForm"], "status": "pending"} for s in steps]
    # 既に承認済み（Edit Automatically）だった場合にこの呼び出しでも承認状態を
    # 維持するかは config.ini の [plan].reset_approval_on_recreate で切り替え可能
    # （既定 True＝従来通り常にリセットして Plan Mode へ戻す）。未承認状態からの
    # 呼び出しは元々 False なので、この設定に関わらず結果は変わらない。
    still_approved = bool(cl.user_session.get("plan_approved")) and not _state._PLAN_RESET_APPROVAL_ON_RECREATE
    cl.user_session.set("plan", plan)
    cl.user_session.set("plan_approved", still_approved)
    cl.user_session.set("awaiting_approve_plan_call", not still_approved)
    # send() 失敗（通信エラー等）でも plan/plan_approved の永続化自体は
    # 効かせるため、send() より前に呼ぶ（src/plan_persist.py docstring、
    # 2026-08-24 ユーザー報告参照）。
    await persist_plan_state()
    # id は thread_id から決定的に導出する（app.py の on_chat_resume と
    # 同じ id を使うことで steps テーブルへの永続化がUPSERTとして働き、
    # 重複行が残らない。src/plan_persist.py plan_message_id docstring参照）。
    # thread_id が取れない異常系（テスト/evalハーネスからの直接呼び出し等、
    # on_chat_start/on_chat_resumeを経由していないセッション）では、固定の
    # ダミー文字列にフォールバックすると全セッションが同じidに収束し、
    # 無関係なセッション同士が同じsteps行を奪い合って上書きし合う
    # （2026-09-04 レビュー指摘）。ランダムUUIDにフォールバックして衝突を防ぐ。
    _thread_id = cl.user_session.get("thread_id")
    message = cl.Message(
        id=plan_message_id(_thread_id) if _thread_id else str(uuid.uuid4()),
        content=_render_plan_payload(plan, approved=still_approved),
    )
    await message.send()
    cl.user_session.set("plan_message", message)
    logger.info(
        "create_plan: %d steps, detail_markdown=%d chars, still_approved=%s",
        len(steps),
        len(detail_markdown or ""),
        still_approved,
    )

    if still_approved:
        result = f"計画を更新しました（全{len(steps)}件）。承認済み状態を維持しているため、approve_plan を呼ばずに書き込み系ツールを続行できます。"
    else:
        result = f"計画を作成しました（全{len(steps)}件）。approve_plan でユーザーの承認を得てください。"
    if detail_markdown and detail_markdown.strip():
        # プレフィックス無しの通常メッセージとして送る（PLAN_PREFIX 付きの
        # チェックリストと違い、messageTree.ts の selectMainThread でサイドパネル
        # 側へ除外されず、メインチャットにそのまま表示される）。
        await cl.Message(content=detail_markdown.rstrip()).send()
        try:
            plan_path = _write_plan_detail(plan, detail_markdown)
        except OSError as e:
            result += f"\n（詳細計画の保存に失敗しました: {e}）"
        else:
            if plan_path is not None:
                result += f"\n詳細計画を保存しました: {plan_path}"
    return result
