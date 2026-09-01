"""Chainlit の UI 呼び出しをスタブ化し、Chainlit サーバー無しで
src.tools のツール群（run_script / execute_python_code / AskUserQuestion /
ask_user_choice / create_plan / approve_plan / update_task_progress）を
そのまま ainvoke できるようにする。

src.tools は `import chainlit as cl` 済みだが、`cl.xxx` への参照は
呼び出し時に解決される（モジュール属性ルックアップ）ため、install() を
src.tools の関数を呼ぶより前に一度実行しておけば十分。差し替えは
tools.py が実際に使っている範囲（user_session / AskActionMessage /
AskUserMessage / CustomElement / AskElementMessage / Message）に限定し、
cl.Action はコンストラクタが副作用を持たないためそのまま使う。

1プロセス=1ケース実行という前提のため、元の chainlit 属性へ戻す処理は
用意しない（run_case.py はケースごとに新規プロセスとして起動される）。
"""

from __future__ import annotations

import chainlit as cl

_state = {"auto_approve": True, "scripted_text_answers": []}


class _FakeUserSession:
    """cl.user_session の代替。プロセス内 dict だけを保持する。"""

    def __init__(self) -> None:
        self._store: dict = {}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value) -> None:
        self._store[key] = value


class _FakeAskActionMessage:
    """cl.AskActionMessage の代替。承認/拒否ダイアログを即答する。

    auto_approve が True なら payload.value == "approve" のアクションを選ぶ
    （run_script/execute_python_code/approve_plan の承認ダイアログ用）。
    False ならそれ以外（"deny" 等）を選ぶ。ask_user_choice のように
    approve/deny という値を持たない選択肢のみの場合は、その中から
    auto_approve=True なら先頭、False なら末尾を選ぶ。
    """

    def __init__(self, content: str = "", actions=None, timeout: int = 0, **kwargs) -> None:
        self.content = content
        self.actions = actions or []

    async def send(self):
        if not self.actions:
            return None
        target = "approve" if _state["auto_approve"] else "deny"
        for action in self.actions:
            payload = getattr(action, "payload", None) or {}
            if payload.get("value") == target:
                return {"payload": payload, "label": getattr(action, "label", "")}
        chosen = self.actions[0] if _state["auto_approve"] else self.actions[-1]
        payload = getattr(chosen, "payload", None) or {}
        return {"payload": payload, "label": getattr(chosen, "label", "")}


class _FakeAskUserMessage:
    """cl.AskUserMessage の代替。scripted_text_answers を順に1件ずつ消費する。

    尽きた場合は None を返す（本物のタイムアウトと同じ扱いにし、
    AskUserQuestion 側の「エラー: ユーザーからの応答がありませんでした」
    という分岐も正当なテスト対象にする）。
    """

    def __init__(self, content: str = "", timeout: int = 0, **kwargs) -> None:
        self.content = content

    async def send(self):
        answers = _state["scripted_text_answers"]
        if answers:
            return {"output": answers.pop(0)}
        return None


class _FakeCustomElement:
    """cl.CustomElement の代替。name/props を保持するだけで副作用は持たない。"""

    def __init__(self, name: str = "", props: dict | None = None, **kwargs) -> None:
        self.name = name
        self.props = props or {}


class _FakeAskElementMessage:
    """cl.AskElementMessage の代替。AskUserQuestion(labels指定時)の
    "MultiTextForm" と ask_user_choice(multi_select=True)の "MultiChoiceForm"
    のみを扱う（tools.py が生成する element.name はこの2種類のみのため）。

    MultiTextForm: labels 1件につき scripted_text_answers を1件ずつ消費し、
    値のリストを返す。件数が足りなくなった時点でタイムアウト扱い（None）に
    する（_FakeAskUserMessage と同じ規約）。
    MultiChoiceForm: _FakeAskActionMessage と同じ auto_approve 規約に倣い、
    auto_approve=True なら choices の先頭1件を選択、False なら未選択のまま
    キャンセル扱い（submitted=False）にする。
    """

    def __init__(self, content: str = "", element=None, timeout: int = 0, **kwargs) -> None:
        self.content = content
        self.element = element

    async def send(self):
        props = getattr(self.element, "props", {}) or {}
        name = getattr(self.element, "name", "")
        if name == "MultiTextForm":
            labels = props.get("labels") or []
            answers = _state["scripted_text_answers"]
            if len(answers) < len(labels):
                return None
            values = [answers.pop(0) for _ in labels]
            return {"values": values}
        if name == "MultiChoiceForm":
            choices = props.get("choices") or []
            if not choices or not _state["auto_approve"]:
                return {"submitted": False}
            return {"submitted": True, "values": [choices[0]], "other": ""}
        return None


class _FakeMessage:
    """cl.Message の代替。create_plan/update_task_progress からの表示更新のみを吸収する。"""

    def __init__(self, content: str = "", **kwargs) -> None:
        self.content = content

    async def send(self):
        return self

    async def update(self):
        return self

    async def stream_token(self, token: str) -> None:
        self.content += token


def install(auto_approve: bool = True, scripted_text_answers: list[str] | None = None) -> None:
    """chainlit モジュールの属性をヘッドレス用スタブへ差し替える。

    run_case.py の冒頭、src.tools の関数を呼ぶより前に一度だけ呼ぶ。

    Args:
        auto_approve: run_script/execute_python_code/approve_plan の
            承認ダイアログを自動で承認するか拒否するか。
        scripted_text_answers: AskUserQuestion が呼ばれるたびに消費して返す
            回答のリスト。labels 省略時（単一質問）は1件、labels 指定時は
            その件数だけまとめて消費する。尽きればタイムアウト扱い（None）。
    """
    _state["auto_approve"] = auto_approve
    _state["scripted_text_answers"] = list(scripted_text_answers or [])

    cl.user_session = _FakeUserSession()
    cl.AskActionMessage = _FakeAskActionMessage
    cl.AskUserMessage = _FakeAskUserMessage
    cl.CustomElement = _FakeCustomElement
    cl.AskElementMessage = _FakeAskElementMessage
    cl.Message = _FakeMessage
