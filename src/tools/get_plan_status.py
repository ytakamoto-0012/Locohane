"""get_plan_status ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import chainlit as cl

from ._plan_render import _render_plan


@tool
async def get_plan_status() -> str:
    """現在 Plan Mode（書き込み系ツールがロック中）か Edit Automatically（承認済み計画の
    実行が許可された状態）かを確認する読み取り専用ツール。

    書き込み系ツール（run_script、execute_python_code）を
    呼ぶ前に自分の状態認識に確信が持てない場合、いつでも呼んでよい（計画の有無や承認
    状態に関わらずブロックされない）。

    Returns:
        現在のモード（"Plan Mode" または "Edit Automatically"）と、計画が存在すれば
        そのステップ一覧・各ステータスを含むテキスト。計画が未作成の場合はその旨を
        伝えるテキストを返す。
    """
    plan = cl.user_session.get("plan")
    approved = bool(cl.user_session.get("plan_approved"))
    if not plan:
        return "現在の状態: Plan Mode（計画は未作成）。書き込み系ツールはブロックされます。"
    mode = "Edit Automatically" if approved else "Plan Mode"
    return f"現在の状態: {mode}\n\n" + _render_plan(plan)
