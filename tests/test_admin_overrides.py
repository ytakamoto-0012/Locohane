"""admin/overrides.py のテスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from admin import overrides
from admin.ini_catalog import parse_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_INI_PATH = PROJECT_ROOT / "config.ini"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """実プロジェクトの data/ にテスト用ディレクトリを作らないよう、データ保存先を tmp へ逃がす。"""
    monkeypatch.setenv("COMMON_DATA_DIR", str(tmp_path / "data" / "${instance}"))


@pytest.fixture
def catalog():
    return parse_file(CONFIG_INI_PATH)


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def _save(workdir, catalog, updates=None, resets=None, base_mtime=None):
    return overrides.save(
        path=workdir / "config_overrides.json",
        updates=updates or {},
        resets=resets or [],
        base_mtime=base_mtime,
        ini_catalog=catalog,
        config_ini_path=CONFIG_INI_PATH,
        backup_dir=workdir / "backups",
        backup_keep=5,
        audit_log_path=workdir / "audit.log",
        instance_name="test",
        actor="alice",
        remote_addr="127.0.0.1",
    )


def test_save_creates_file_and_returns_overrides(workdir, catalog):
    result = _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    assert result == {"ui": {"max_display_messages": "77"}}
    saved = json.loads((workdir / "config_overrides.json").read_text(encoding="utf-8"))
    assert saved == {"ui": {"max_display_messages": "77"}}


def test_save_same_as_default_removes_key(workdir, catalog):
    default_value = next(k.default_value for k in catalog.keys if k.section == "ui" and k.key == "max_display_messages")
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "999"})
    result = _save(workdir, catalog, updates={("ui", "max_display_messages"): default_value})
    assert result == {}
    assert not (workdir / "config_overrides.json").read_text(encoding="utf-8").strip("{} \n")


def test_reset_removes_key(workdir, catalog):
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    result = _save(workdir, catalog, resets=[("ui", "max_display_messages")])
    assert result == {}


def test_unknown_key_rejected(workdir, catalog):
    with pytest.raises(overrides.ValidationError):
        _save(workdir, catalog, updates={("nope_section", "nope_key"): "x"})


def test_admin_section_rejected(workdir, catalog):
    with pytest.raises(overrides.ValidationError):
        _save(workdir, catalog, updates={("admin", "port"): "9999"})


def test_invalid_value_rejected_and_file_unchanged(workdir, catalog):
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    before = (workdir / "config_overrides.json").read_text(encoding="utf-8")
    with pytest.raises(overrides.ValidationError):
        _save(workdir, catalog, updates={("llm", "main_routing_strategy"): "not_a_strategy"})
    after = (workdir / "config_overrides.json").read_text(encoding="utf-8")
    assert before == after


def test_conflict_detection(workdir, catalog):
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    with pytest.raises(overrides.ConflictError):
        _save(workdir, catalog, updates={("ui", "max_display_messages"): "88"}, base_mtime=0.0)


def test_backup_created_on_second_save(workdir, catalog):
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    backups_before = list((workdir / "backups").glob("*.json")) if (workdir / "backups").is_dir() else []
    assert backups_before == []
    _save(
        workdir,
        catalog,
        updates={("ui", "max_display_messages"): "88"},
        base_mtime=(workdir / "config_overrides.json").stat().st_mtime,
    )
    backups_after = list((workdir / "backups").glob("*.json"))
    assert len(backups_after) == 1
    backed_up = json.loads(backups_after[0].read_text(encoding="utf-8"))
    assert backed_up == {"ui": {"max_display_messages": "77"}}


def test_backup_keep_prunes_old_backups(workdir, catalog):
    for i in range(3):
        mtime = (workdir / "config_overrides.json").stat().st_mtime if (workdir / "config_overrides.json").is_file() else None
        overrides.save(
            path=workdir / "config_overrides.json",
            updates={("ui", "max_display_messages"): str(100 + i)},
            resets=[],
            base_mtime=mtime,
            ini_catalog=catalog,
            config_ini_path=CONFIG_INI_PATH,
            backup_dir=workdir / "backups",
            backup_keep=1,
            audit_log_path=workdir / "audit.log",
            instance_name="test",
            actor="alice",
            remote_addr="127.0.0.1",
        )
    backups = list((workdir / "backups").glob("*.json"))
    assert len(backups) == 1


def test_audit_log_records_masked_sensitive_values(workdir, catalog):
    _save(workdir, catalog, updates={("llm", "main_url"): '[{"base_url": "http://x", "api_key": "SECRETVALUE", "model": "m"}]'})
    log_text = (workdir / "audit.log").read_text(encoding="utf-8")
    assert "SECRETVALUE" not in log_text
    assert "config_update" in log_text


def test_audit_log_no_entry_when_no_effective_change(workdir, catalog):
    default_value = next(k.default_value for k in catalog.keys if k.section == "ui" and k.key == "max_display_messages")
    _save(workdir, catalog, updates={("ui", "max_display_messages"): default_value})
    assert not (workdir / "audit.log").exists()


def test_preview_does_not_write_file(workdir, catalog):
    new_data, changes = overrides.preview(
        path=workdir / "config_overrides.json",
        updates={("ui", "max_display_messages"): "77"},
        resets=[],
        ini_catalog=catalog,
        config_ini_path=CONFIG_INI_PATH,
    )
    assert new_data == {"ui": {"max_display_messages": "77"}}
    assert len(changes) == 1
    assert not (workdir / "config_overrides.json").exists()


def test_preview_invalid_value_raises(workdir, catalog):
    with pytest.raises(overrides.ValidationError):
        overrides.preview(
            path=workdir / "config_overrides.json",
            updates={("llm", "main_routing_strategy"): "bogus"},
            resets=[],
            ini_catalog=catalog,
            config_ini_path=CONFIG_INI_PATH,
        )


def test_restore_replaces_content(workdir, catalog):
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    backup_file = workdir / "manual_backup.json"
    backup_file.write_text(json.dumps({"ui": {"max_display_messages": "42"}}), encoding="utf-8")
    result = overrides.restore(
        path=workdir / "config_overrides.json",
        backup_file=backup_file,
        config_ini_path=CONFIG_INI_PATH,
        backup_dir=workdir / "backups",
        backup_keep=5,
        audit_log_path=workdir / "audit.log",
        instance_name="test",
        actor="alice",
        remote_addr="127.0.0.1",
    )
    assert result == {"ui": {"max_display_messages": "42"}}
    saved = json.loads((workdir / "config_overrides.json").read_text(encoding="utf-8"))
    assert saved == {"ui": {"max_display_messages": "42"}}


def test_restore_invalid_content_rejected(workdir, catalog):
    backup_file = workdir / "manual_backup.json"
    backup_file.write_text(json.dumps({"llm": {"main_routing_strategy": "bogus"}}), encoding="utf-8")
    with pytest.raises(overrides.ValidationError):
        overrides.restore(
            path=workdir / "config_overrides.json",
            backup_file=backup_file,
            config_ini_path=CONFIG_INI_PATH,
            backup_dir=workdir / "backups",
            backup_keep=5,
            audit_log_path=workdir / "audit.log",
            instance_name="test",
            actor="alice",
            remote_addr="127.0.0.1",
        )


def test_read_returns_empty_dict_when_missing(workdir):
    assert overrides.read(workdir / "nope.json") == {}


def test_mtime_or_none(workdir, catalog):
    assert overrides.mtime_or_none(workdir / "nope.json") is None
    _save(workdir, catalog, updates={("ui", "max_display_messages"): "77"})
    assert overrides.mtime_or_none(workdir / "config_overrides.json") is not None
