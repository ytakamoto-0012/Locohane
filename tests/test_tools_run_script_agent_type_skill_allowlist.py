"""run_script のagent_typeごとのスキル制限ガード（_AGENT_TYPE_RUN_SCRIPT_SKILL_ALLOWLIST）の回帰テスト。

背景（eval case 006, 2026-08-15）: `explore`はプロンプト上「run_scriptは
web-searchスキルのsearch_web.pyに限って呼べる」と規定されているが、コード側の
強制が無かったため、低パラメータモデルが指示を無視してexcel-readのread_excel.py
を呼び、本来analyze-docs向けの誤診断防止ルールが適用されないままxlsx調査が
進んでしまう事象が実際に発生した。_prepare_script_execution冒頭のガードが
これをコード側で強制することを検証する。
"""

from src import tools


def _set_agent_type(agent_type):
    return tools._SUBAGENT_AGENT_TYPE.set(agent_type)


def test_explore_blocked_from_non_web_search_skill():
    token = _set_agent_type("explore")
    try:
        result = tools._prepare_script_execution("excel-read", "read_excel.py", ["dummy.xlsx"])
    finally:
        tools._SUBAGENT_AGENT_TYPE.reset(token)

    assert isinstance(result, str)
    assert result.startswith("エラー")
    assert "explore" in result
    assert "web-search" in result


def _call_past_guard(skill_name: str, script_filename: str):
    """ガードを通過したことだけを確認する呼び出し。

    init_tools() 未実行のテスト環境では _resolve_script_filename 以降が
    RuntimeError/ValueError で失敗するが、それ自体がこのガードで
    ブロックされなかった証拠になる（ブロックされていれば例外を投げず
    「エラー: ...限定されています」という文字列を返して即座に戻るため）。
    """
    try:
        return tools._prepare_script_execution(skill_name, script_filename, ["dummy"])
    except (RuntimeError, ValueError) as e:
        return f"passed_guard_then_failed_downstream: {e}"


def test_explore_allowed_web_search_skill_passes_allowlist_guard():
    token = _set_agent_type("explore")
    try:
        result = _call_past_guard("web-search", "search_web.py")
    finally:
        tools._SUBAGENT_AGENT_TYPE.reset(token)

    assert "限定されています" not in result


def test_no_agent_type_context_is_unrestricted():
    # メインエージェント（_SUBAGENT_AGENT_TYPE未設定）はこのガードの対象外。
    result = _call_past_guard("excel-read", "read_excel.py")
    assert "限定されています" not in result


def test_worker_agent_type_is_unrestricted():
    token = _set_agent_type("worker")
    try:
        result = _call_past_guard("excel-read", "read_excel.py")
    finally:
        tools._SUBAGENT_AGENT_TYPE.reset(token)

    assert "限定されています" not in result
