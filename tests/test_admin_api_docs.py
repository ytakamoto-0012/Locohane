"""admin/api_docs.py（APIリファレンス md のHTML化）のテスト。"""

from __future__ import annotations

import pytest

from admin import api_docs


def test_bundled_reference_renders():
    result = api_docs.render()
    assert result["markdown"].startswith("# ")
    assert "<h1>" in result["html"]
    # 表（ステータスコード一覧）が table として変換されること
    assert "<table>" in result["html"]


def test_reflects_file_changes_without_cache(tmp_path):
    md = tmp_path / "ref.md"
    md.write_text("# v1", encoding="utf-8")
    assert "v1" in api_docs.render(md)["html"]
    md.write_text("# v2", encoding="utf-8")
    assert "v2" in api_docs.render(md)["html"]


def test_raw_html_is_escaped(tmp_path):
    md = tmp_path / "ref.md"
    md.write_text("<script>alert(1)</script>\n", encoding="utf-8")
    html = api_docs.render(md)["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        api_docs.render(tmp_path / "missing.md")
