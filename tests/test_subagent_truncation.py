"""src/subagent.py の is_truncated_result() の回帰テスト（ISSUE-001）。"""

from src.subagent import _build_truncation_message, is_truncated_result
from langchain_core.messages import AIMessage, HumanMessage


def test_build_truncation_message_is_detected_as_truncated() -> None:
    messages = [HumanMessage(content="task"), AIMessage(content="調査中...")]
    text = _build_truncation_message("最大反復回数(6)に達した", messages)
    assert is_truncated_result(text) is True


def test_normal_final_answer_is_not_truncated() -> None:
    assert is_truncated_result("調査の結果、問題ありませんでした。") is False


def test_non_string_is_not_truncated() -> None:
    assert is_truncated_result(None) is False
    assert is_truncated_result(123) is False
