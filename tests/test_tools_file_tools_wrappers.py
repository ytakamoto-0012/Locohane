"""Read/Glob/Grep/json_query/list_path_memory（src/tools.py の @tool ラッパー）の回帰テスト。

旧 run_readonly_script 経由の file-tools スクリプトをネイティブツール化した
（ISSUE-003）際の重要な仕様を固定化する:
- 相対パスは Path.cwd()（プロセスcwd）ではなく作業ディレクトリ（_resolve_workdir()）基準
- `@N` パスメモリーの解決・登録
- 重複呼び出しガード（config.ini [file_tools_duplicate_guard]）
- pydantic スキーマに args/kwargs 予約語衝突が無いこと
"""

import json

import pytest

from src import tools


class _FakeUserSession:
    def __init__(self):
        self._data: dict = {"thread_id": "thread-1"}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def file_tools_env(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    path_memory_dir = tmp_path / "path_memory_data"

    monkeypatch.setattr(tools, "_PATH_MEMORY_DIR", path_memory_dir)
    monkeypatch.setattr(tools, "_PATH_MEMORY_MAX_ENTRIES", 500)
    monkeypatch.setattr(tools, "_DEFAULT_WORKDIR", workdir)
    monkeypatch.setattr(tools, "_LLM_CONFIG", None)
    monkeypatch.setattr(tools.cl, "user_session", _FakeUserSession())

    return workdir


class TestPydanticSchema:
    @pytest.mark.parametrize(
        "tool_obj",
        [tools.read_tool, tools.glob_tool, tools.grep_tool, tools.json_query, tools.list_path_memory],
    )
    def test_schema_has_no_pydantic_placeholder_fields(self, tool_obj) -> None:
        schema = tool_obj.args
        assert not any(name in ("args", "kwargs") for name in schema)
        assert not any(name.startswith("v__") for name in schema)


class TestReadTool:
    def test_reads_absolute_path_and_registers_path_memory(self, file_tools_env) -> None:
        f = file_tools_env / "notes.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")

        result = json.loads(tools.read_tool.func(file_path=str(f)))

        assert result["total_lines"] == 2
        assert result["path_memory"] == {"@1": str(f.resolve())}

    def test_relative_path_resolves_against_workdir_not_process_cwd(self, file_tools_env) -> None:
        f = file_tools_env / "notes.txt"
        f.write_text("hello\n", encoding="utf-8")

        result = json.loads(tools.read_tool.func(file_path="notes.txt"))

        assert result["path"] == str(f)

    def test_resolves_path_memory_token(self, file_tools_env) -> None:
        f = file_tools_env / "notes.txt"
        f.write_text("hello\n", encoding="utf-8")
        tools.read_tool.func(file_path=str(f))

        result = json.loads(tools.read_tool.func(file_path="@1", offset=0, limit=5))

        assert result["path"] == str(f.resolve())

    def test_unregistered_token_returns_error(self, file_tools_env) -> None:
        result = tools.read_tool.func(file_path="@99")

        assert result.startswith("エラー:")
        assert "登録されていません" in result

    def test_missing_file_returns_error(self, file_tools_env) -> None:
        result = tools.read_tool.func(file_path=str(file_tools_env / "nope.txt"))

        assert result.startswith("エラー:")

    def test_duplicate_call_is_blocked(self, file_tools_env) -> None:
        f = file_tools_env / "notes.txt"
        f.write_text("hello\n", encoding="utf-8")

        first = tools.read_tool.func(file_path=str(f))
        second = tools.read_tool.func(file_path=str(f))

        assert not first.startswith("エラー:")
        assert second.startswith("エラー:")
        assert "上限" in second


class TestGlobTool:
    def test_lists_files_and_registers_path_memory(self, file_tools_env) -> None:
        (file_tools_env / "a.py").write_text("a", encoding="utf-8")
        (file_tools_env / "sub").mkdir()
        (file_tools_env / "sub" / "b.py").write_text("b", encoding="utf-8")

        result = json.loads(tools.glob_tool.func(pattern="**/*.py"))

        assert result["total_matches"] == 2
        assert "path_memory" in result

    def test_empty_path_resolves_to_workdir(self, file_tools_env) -> None:
        (file_tools_env / "a.py").write_text("a", encoding="utf-8")

        result = json.loads(tools.glob_tool.func(pattern="*.py", path=""))

        assert result["base"] == str(file_tools_env)

    def test_missing_base_returns_error(self, file_tools_env) -> None:
        result = tools.glob_tool.func(pattern="*.py", path=str(file_tools_env / "nope"))

        assert result.startswith("エラー:")


class TestGrepTool:
    def test_content_mode(self, file_tools_env) -> None:
        (file_tools_env / "a.py").write_text("before\nTODO: fix\nafter\n", encoding="utf-8")

        result = json.loads(
            tools.grep_tool.func(pattern="TODO", output_mode="content", context=1)
        )

        assert result["matched"] is True
        assert "path_memory" in result

    def test_no_match_has_no_path_memory_key(self, file_tools_env) -> None:
        (file_tools_env / "a.py").write_text("nothing\n", encoding="utf-8")

        result = json.loads(tools.grep_tool.func(pattern="TODO"))

        assert result["matched"] is False
        assert "path_memory" not in result


class TestJsonQuery:
    def test_query_from_file(self, file_tools_env) -> None:
        f = file_tools_env / "data.json"
        f.write_text('{"a": {"b": 1}}', encoding="utf-8")

        result = json.loads(tools.json_query.func(query="a.b", file_path=str(f)))

        assert result == {"result": 1}

    def test_query_from_json_text(self, file_tools_env) -> None:
        result = json.loads(tools.json_query.func(query="a.b", json_text='{"a": {"b": 2}}'))

        assert result == {"result": 2}

    def test_both_and_neither_are_errors(self, file_tools_env) -> None:
        f = file_tools_env / "data.json"
        f.write_text("{}", encoding="utf-8")

        both = tools.json_query.func(query="a", file_path=str(f), json_text="{}")
        neither = tools.json_query.func(query="a")

        assert both.startswith("エラー:")
        assert neither.startswith("エラー:")

    def test_different_json_text_are_not_treated_as_duplicate(self, file_tools_env) -> None:
        first = tools.json_query.func(query="a.b", json_text='{"a": {"b": 1}}')
        second = tools.json_query.func(query="a.b", json_text='{"a": {"b": 2}}')

        assert not first.startswith("エラー:")
        assert not second.startswith("エラー:")


class TestListPathMemory:
    def test_lists_registered_entries(self, file_tools_env) -> None:
        f = file_tools_env / "notes.txt"
        f.write_text("hello\n", encoding="utf-8")
        tools.read_tool.func(file_path=str(f))

        result = json.loads(tools.list_path_memory.func())

        assert result["entries"] == [
            {"index": 1, "path": str(f.resolve()), "valid": True, "description": None}
        ]

    def test_empty_when_nothing_registered(self, file_tools_env) -> None:
        result = json.loads(tools.list_path_memory.func())

        assert result == {"entries": []}
