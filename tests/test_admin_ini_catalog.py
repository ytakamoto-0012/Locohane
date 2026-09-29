"""admin/ini_catalog.py のテスト。

config.ini を書き換えず読み取り専用で解析することを確認する。全キー検出漏れが
無いことは、実際の config.ini を対象に検証する（代表例だけで済ませない）。
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from admin.ini_catalog import parse, parse_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_INI_PATH = PROJECT_ROOT / "config.ini"


def test_parse_detects_all_keys_in_real_config_ini():
    """実際の config.ini から、グレップで数えた総キー数と一致する件数を検出する。"""
    text = CONFIG_INI_PATH.read_text(encoding="utf-8")
    expected_count = len(re.findall(r"^[A-Za-z_][A-Za-z0-9_.]*\s*=", text, flags=re.MULTILINE))

    catalog = parse(text)

    assert len(catalog.keys) == expected_count


def test_parse_matches_configparser_for_every_key():
    """複数行値も含め、値をつなげた内容が configparser の解釈と一致する。

    configparser は継続行を strip() して連結するため、行頭インデントの
    有無や行内コメント専用行（インデントされていても "#" 始まりなら
    フルラインコメントとして除去される）の扱いが異なる。ここでは
    「行ごとにstripした中身の並び」を比較することで、コメントの
    扱いの違いを除いた実質的な整合性を検証する。
    """
    parser = configparser.ConfigParser()
    parser.read(CONFIG_INI_PATH, encoding="utf-8")
    catalog = parse_file(CONFIG_INI_PATH)

    for info in catalog.keys:
        cp_value = parser.get(info.section, info.key)
        cp_lines = [line.strip() for line in cp_value.split("\n") if line.strip()]
        our_lines = [line.strip() for line in info.default_value.split("\n") if line.strip() and not line.strip().startswith("#")]
        assert our_lines == cp_lines, f"[{info.section}].{info.key}"


def test_all_162_original_plus_admin_keys_present():
    """既知のキーが存在し続けることを確認する（代表的なキーのスポットチェック）。"""
    catalog = parse_file(CONFIG_INI_PATH)
    key_pairs = {(info.section, info.key) for info in catalog.keys}
    for expected in [
        ("llm", "main_url"),
        ("llm", "main_routing_strategy"),
        ("ui", "max_display_messages"),
        ("admin", "port"),
        ("admin", "host"),
        ("mcp", "settings_path"),
        ("auth", "enabled"),
    ]:
        assert expected in key_pairs, expected


def test_ui_kind_inference():
    catalog = parse_file(CONFIG_INI_PATH)
    by_pair = {(info.section, info.key): info for info in catalog.keys}
    assert by_pair[("auth", "enabled")].ui_kind == "bool"
    assert by_pair[("admin", "port")].ui_kind == "text"
    assert by_pair[("llm", "main_url")].ui_kind == "multiline"


def test_group_heading_and_description_present_for_known_key():
    catalog = parse_file(CONFIG_INI_PATH)
    by_pair = {(info.section, info.key): info for info in catalog.keys}
    info = by_pair[("llm", "main_url")]
    assert info.group_heading is not None
    assert len(info.description) > 0


def test_blank_line_breaks_comment_association():
    """空行を挟むと、その前のコメントは次のキーの説明として引き継がれない。"""
    text = "[sec]\n# これは無関係のコメント\n\nfoo = bar\n"
    catalog = parse(text)
    info = catalog.keys[0]
    assert info.key == "foo"
    assert info.description == ""


def test_multiline_value_continuation_lines_captured():
    text = "[sec]\nfoo = [\n    1,\n    2,\n    ]\nbar = baz\n"
    catalog = parse(text)
    by_key = {info.key: info for info in catalog.keys}
    assert by_key["foo"].default_value == "[\n    1,\n    2,\n    ]"
    assert by_key["bar"].default_value == "baz"


def test_parse_file_sets_mtime():
    catalog = parse_file(CONFIG_INI_PATH)
    assert catalog.mtime > 0
