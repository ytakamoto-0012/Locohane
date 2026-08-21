"""approve_plan の承認待ち（AskActionMessage）を、スレッド切り替え後の別セッションへ
引き継ぐための状態。app.py（Chainlitエントリスクリプト）と src/tools.py の双方から
参照されるため、どちらにも属さないこの中立モジュールに置く。

背景（2026-08-21 発覚）: 以前はこの状態を app.py 側に置き、src/tools.py から
`from app import ...` で遅延importしていた。しかし `chainlit run app.py` は
`importlib.util.spec_from_file_location(target, target)`（target=app.pyのフルパス）
でapp.pyを読み込み、`sys.modules[target]` に登録する（config.py の load_module 参照）。
つまり実行中のapp.pyは `sys.modules["app"]` という名前では登録されていない。
そのため `from app import ...` を実行すると sys.modules に "app" が見つからず、
（load_module がapp.pyのディレクトリを sys.path へ追加済みのため）sys.path 経由で
app.py がまっさらな別モジュールとして最初から再実行されてしまう。
この再実行は `@cl.on_message`/`@cl.on_chat_start`/`@cl.on_chat_resume` を含む
トップレベルコードを丸ごとやり直し、これらのデコレータはプロセス全体で1つの
シングルトンである `chainlit.config.config.code.on_message` 等を書き換えるため、
稼働中の本物のハンドラが「未初期化の別モジュールのハンドラ」に上書きされて
アプリ全体が壊れる（approve_plan呼び出し直後に接続がおかしくなる、として観測された）。
このモジュールを介することで app.py→src.tools への逆import自体を無くす。
"""

from __future__ import annotations

import asyncio

# thread_id -> 承認待ち（approve_plan の AskActionMessage）の引き継ぎ用状態。
# 値: {"content": str, "action_specs": list[(name, label, payload)],
#      "timeout": int, "future": asyncio.Future}
pending_plan_asks: dict[str, dict] = {}


def register_pending_plan_ask(thread_id: str, content: str, actions: list, timeout: int):
    """approve_plan がAskActionMessage送信前に呼ぶ。他セッションから引き継げる
    よう質問内容を登録し、引き継ぎ側が回答を書き込むための Future を返す。
    """
    fut = asyncio.get_event_loop().create_future()
    pending_plan_asks[thread_id] = {
        "content": content,
        "action_specs": [(action.name, action.label, dict(action.payload)) for action in actions],
        "timeout": timeout,
        "future": fut,
    }
    return fut


def clear_pending_plan_ask(thread_id: str, fut) -> None:
    """approve_plan の finally節から呼ぶ。同じ thread_id へ後続の
    create_plan/approve_plan が既に新しい承認待ちを登録している場合は
    誤って消さないよう、渡された Future が現在の登録と一致する場合のみ消す。
    """
    entry = pending_plan_asks.get(thread_id)
    if entry is not None and entry["future"] is fut:
        pending_plan_asks.pop(thread_id, None)
