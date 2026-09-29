"""admin/instances.py のテスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from admin import instances as inst

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_INI_PATH = PROJECT_ROOT / "config.ini"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """実プロジェクトの data/ にテスト用ディレクトリを作らないよう、データ保存先を tmp へ逃がす。"""
    monkeypatch.setenv("COMMON_DATA_DIR", str(tmp_path / "data" / "${instance}"))
    monkeypatch.delenv("LOCOHANE_INSTANCE", raising=False)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "instances"


def test_validate_name_accepts_valid():
    inst.validate_name("test-2_ok")


@pytest.mark.parametrize("name", ["", "has space", "has/slash", "a" * 33, "日本語"])
def test_validate_name_rejects_invalid(name):
    with pytest.raises(inst.InstanceError):
        inst.validate_name(name)


def test_ensure_default_instance_creates_once(root):
    meta1 = inst.ensure_default_instance(root)
    assert meta1.name == "default"
    assert meta1.app_port == 8000
    assert meta1.autostart is True
    # 2回目は既存のものをそのまま返す(上書きしない)。
    inst.write_instance(root, inst.InstanceMeta(name="default", display_name="changed", app_host="127.0.0.1", app_port=8000, autostart=False))
    meta2 = inst.ensure_default_instance(root)
    assert meta2.display_name == "changed"


def test_list_instance_names_empty_when_missing(root):
    assert inst.list_instance_names(root) == []


def test_create_and_list_instance(root):
    inst.ensure_default_instance(root)
    meta = inst.create_instance(root, CONFIG_INI_PATH, name="test2", app_host="127.0.0.1")
    assert meta.app_port != 8000
    assert inst.list_instance_names(root) == ["default", "test2"]


def test_create_instance_data_dir_isolated_by_instance_placeholder(root, tmp_path):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    # common_data_dir の上書きは書かず、${instance} の展開だけで分離される。
    assert not inst.overrides_path(root, "test2").exists()
    assert inst._effective_checkpoint_db(CONFIG_INI_PATH, inst.overrides_path(root, "test2"), "test2").parent == tmp_path / "data" / "test2"


def test_default_instance_has_no_common_data_dir_override(root):
    inst.ensure_default_instance(root)
    assert not inst.overrides_path(root, "default").exists()


def test_config_ini_default_common_data_dir_uses_instance_placeholder(monkeypatch):
    monkeypatch.delenv("COMMON_DATA_DIR")
    from src.config import load_config

    cfg = load_config(config_path=CONFIG_INI_PATH, overrides_path=PROJECT_ROOT / "nope.json", instance_name="default")
    assert cfg.checkpoint_db.parent == PROJECT_ROOT / "data" / "default"


def test_create_instance_rejects_duplicate_name(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    with pytest.raises(inst.InstanceError):
        inst.create_instance(root, CONFIG_INI_PATH, name="test2")


def test_create_instance_rejects_port_conflict(root):
    inst.ensure_default_instance(root)
    with pytest.raises(inst.InstanceError):
        inst.create_instance(root, CONFIG_INI_PATH, name="test2", app_host="127.0.0.1", app_port=8000)
    # 失敗時にディレクトリがロールバックされること。
    assert "test2" not in inst.list_instance_names(root)


def test_create_instance_copy_from_copies_overrides_and_resets_data_dir(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="source")
    from admin import overrides
    from admin.ini_catalog import parse_file

    catalog = parse_file(CONFIG_INI_PATH)
    overrides.save(
        path=inst.overrides_path(root, "source"),
        updates={("ui", "max_display_messages"): "77"},
        resets=[],
        base_mtime=overrides.mtime_or_none(inst.overrides_path(root, "source")),
        ini_catalog=catalog,
        config_ini_path=CONFIG_INI_PATH,
        backup_dir=inst.backups_dir(root, "source"),
        backup_keep=5,
        audit_log_path=root / "audit.log",
        instance_name="source",
        actor="tester",
        remote_addr="127.0.0.1",
    )

    # 旧仕様で作られた common_data_dir の上書きを持つ複製元でも、複製先には引き継がない。
    src_ov = inst.overrides_path(root, "source")
    data = json.loads(src_ov.read_text(encoding="utf-8"))
    data["paths"] = {"common_data_dir": "./instances/source/data"}
    src_ov.write_text(json.dumps(data), encoding="utf-8")

    inst.create_instance(root, CONFIG_INI_PATH, name="copy", copy_from="source")
    ov = json.loads(inst.overrides_path(root, "copy").read_text(encoding="utf-8"))
    assert ov["ui"]["max_display_messages"] == "77"
    assert "paths" not in ov


def test_delete_instance(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    inst.delete_instance(root, "test2")
    assert inst.list_instance_names(root) == ["default"]


def test_delete_instance_removes_data_dir_under_project_data(root, tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "PROJECT_ROOT", tmp_path)
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    data_dir = tmp_path / "data" / "test2"
    assert data_dir.is_dir()  # load_config() の副作用で作られている
    inst.delete_instance(root, "test2", CONFIG_INI_PATH)
    assert not data_dir.exists()
    assert (tmp_path / "data").is_dir()


def test_delete_instance_keeps_data_dir_outside_project_data(root, tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "PROJECT_ROOT", tmp_path / "elsewhere")
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    inst.delete_instance(root, "test2", CONFIG_INI_PATH)
    assert (tmp_path / "data" / "test2").is_dir()


def test_delete_instance_keeps_other_instances_data_dir(root, tmp_path, monkeypatch):
    """common_data_dir が別インスタンスのデータディレクトリを指していても、そちらは消さない。"""
    monkeypatch.setattr(inst, "PROJECT_ROOT", tmp_path)
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    default_data = tmp_path / "data" / "default"
    default_data.mkdir(parents=True, exist_ok=True)
    (default_data / "checkpoints.sqlite").write_bytes(b"x")
    monkeypatch.delenv("COMMON_DATA_DIR")
    inst.overrides_path(root, "test2").write_text(
        json.dumps({"paths": {"common_data_dir": str(default_data)}}), encoding="utf-8"
    )
    inst.delete_instance(root, "test2", CONFIG_INI_PATH)
    assert (default_data / "checkpoints.sqlite").is_file()


@pytest.mark.parametrize("copy_from", ["..", "../..", "nope"])
def test_create_instance_rejects_invalid_copy_from(root, copy_from):
    inst.ensure_default_instance(root)
    with pytest.raises(inst.InstanceError):
        inst.create_instance(root, CONFIG_INI_PATH, name="test2", copy_from=copy_from)
    assert "test2" not in inst.list_instance_names(root)


def test_delete_default_rejected(root):
    inst.ensure_default_instance(root)
    with pytest.raises(inst.InstanceError):
        inst.delete_instance(root, "default")


def test_delete_nonexistent_rejected(root):
    with pytest.raises(inst.InstanceError):
        inst.delete_instance(root, "nope")


def test_suggest_port_skips_used_ports(root):
    inst.ensure_default_instance(root)  # uses 8000
    port = inst.suggest_port(root, "127.0.0.1", exclude_ports={8001})
    assert port not in (8000, 8001)


def test_update_instance_meta_partial_update(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2", app_port=8050)
    updated = inst.update_instance_meta(root, CONFIG_INI_PATH, name="test2", display_name="New Name")
    assert updated.display_name == "New Name"
    assert updated.app_port == 8050  # 変更していないフィールドは維持される


def test_update_instance_meta_port_conflict_detected(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2", app_port=8050)
    with pytest.raises(inst.InstanceError):
        inst.update_instance_meta(root, CONFIG_INI_PATH, name="test2", app_port=8000)


def test_read_instance_missing_raises(root):
    with pytest.raises(inst.InstanceError):
        inst.read_instance(root, "nope")


def test_default_instance_headless_true_watch_false(root):
    meta = inst.ensure_default_instance(root)
    assert meta.headless is True
    assert meta.watch is False


def test_create_instance_with_headless_watch(root):
    inst.ensure_default_instance(root)
    meta = inst.create_instance(root, CONFIG_INI_PATH, name="test2", headless=False, watch=True)
    assert meta.headless is False
    assert meta.watch is True
    reread = inst.read_instance(root, "test2")
    assert reread.headless is False
    assert reread.watch is True


def test_update_instance_meta_headless_watch(root):
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    updated = inst.update_instance_meta(root, CONFIG_INI_PATH, name="test2", headless=False, watch=True)
    assert updated.headless is False
    assert updated.watch is True
    # 指定しなかったフィールドは変わらない。
    assert updated.app_port == inst.read_instance(root, "test2").app_port


def test_read_instance_backward_compat_missing_headless_watch_keys(root):
    """headless/watch導入前に作られたinstance.json（キー自体が無い）でも既定値で読める。"""
    inst.ensure_default_instance(root)
    inst.create_instance(root, CONFIG_INI_PATH, name="test2")
    path = inst.instance_json_path(root, "test2")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["headless"]
    del data["watch"]
    path.write_text(json.dumps(data), encoding="utf-8")

    reread = inst.read_instance(root, "test2")
    assert reread.headless is True
    assert reread.watch is False
