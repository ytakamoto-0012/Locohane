"""AI judge（judge 付きのケースを、研究室の LLM が仕様と judge の観点で判定する）。

最終的な合否は人がレビューで確認する（承認が judge 合格の扱いになる）。ここでの判定は、
修正ループが「直ったかどうか」を決めるためと、人が確認するときの手がかりにするため。
"""

from __future__ import annotations

from src.config import Config

from . import evaluation
from .llm import LabLLMError, ask_json

_SYSTEM = """あなたは業務用AIアシスタントの動作検証の判定者です。
ユーザーの指示に対してアシスタントが行ったこと（ツール呼び出しと結果）と最終回答を読み、
判定観点を満たしているかを厳しく判定します。少しでも満たしていない・確かめられない場合は不合格にします。
次の形式の JSON だけを出力してください: {"pass": true または false, "reason": "判定の根拠（日本語・具体的に）"}"""


def judge_run(config: Config, *, spec: str, judge_instruction: str, result: dict, excerpt_chars: int) -> dict:
    """1回分の結果を判定する。{"pass": bool, "reason": str}（LLM が使えなければ不合格扱い）。"""
    user = (
        f"## スキル・サブエージェントの仕様\n{spec[:6000]}\n\n"
        f"## 判定観点\n{judge_instruction}\n\n"
        f"## 実行の記録\n{evaluation.transcript_excerpt(result, excerpt_chars)}\n\n"
        f"## 最終回答\n{(result.get('final_answer') or '')[:4000]}"
    )
    try:
        data = ask_json(config, _SYSTEM, user)
    except LabLLMError as e:
        return {"pass": False, "reason": f"AI judge を実行できませんでした: {e}"}
    if not isinstance(data, dict) or not isinstance(data.get("pass"), bool):
        return {"pass": False, "reason": f"AI judge の応答の形式が不正でした: {str(data)[:300]}"}
    return {"pass": data["pass"], "reason": str(data.get("reason", ""))}


def judge_results(config: Config, *, spec: str, results: list[dict], excerpt_chars: int) -> list[dict]:
    """全回の結果を判定し、各回の最終判定（outcome: pass/fail/error と AI judge の結果）を返す。"""
    verdicts = []
    for r in results:
        outcome = evaluation.classify(r)
        entry = {"case_id": r.get("case_id"), "repeat_index": r.get("repeat_index", 1), "outcome": outcome}
        if outcome == "judge":
            ai = judge_run(config, spec=spec, judge_instruction=r.get("judge") or "", result=r, excerpt_chars=excerpt_chars)
            entry["ai_judge"] = ai
            entry["outcome"] = "pass" if ai["pass"] else "fail"
        verdicts.append(entry)
    return verdicts


def all_passed(verdicts: list[dict]) -> bool:
    return bool(verdicts) and all(v["outcome"] == "pass" for v in verdicts)
