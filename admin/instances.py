"""instances/ ディレクトリ配下のインスタンス管理（作成・削除・複製・整合性チェック）。

各インスタンスは Locohane 本体（app.py）を独立した子プロセスとして動かす単位。
instances/<name>/ 配下に instance.json（起動情報）・config_overrides.json
（admin/overrides.py が書く設定差分）・.env（admin/env_files.py が書く
ログイン情報）・backups/ を持つ。データ本体は config.ini 既定の
common_data_dir = ./data/${instance} により data/<name>/ に置かれる（README.md「ディレクトリ構成」参照）。
"""

from __future__ import annotations

import json
import re
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

from src.config import PROJECT_ROOT, load_config

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

DEFAULT_INSTANCE_NAME = "default"
DEFAULT_APP_HOST = "127.0.0.1"
DEFAULT_APP_PORT = 8000


class InstanceError(Exception):
    """instances.py の操作失敗。"""


@dataclass
class InstanceMeta:
    """instance.json の内容。"""

    name: str
    display_name: str
    app_host: str = DEFAULT_APP_HOST
    app_port: int = DEFAULT_APP_PORT
    autostart: bool = False
    # chainlit run の -h/--headless（ブラウザタブの自動オープンを抑止）。
    # 既定 True（管理ツール経由の起動でタブが乱立しないように）。
    headless: bool = True
    # chainlit run の -w/--watch（ファイル変更検知での自動リロード）。
    # 既定 False（本番運用では非推奨。開発時の動作確認用にインスタンス単位で
    # 有効化できる）。
    watch: bool = False

    def to_json(self) -> dict:
        return {
            "display_name": self.display_name,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "autostart": self.autostart,
            "headless": self.headless,
            "watch": self.watch,
        }


def validate_name(name: str) -> None:
    """インスタンス名の妥当性を検証する（そのままディレクトリ名に使うため制限する）。"""
    if not _NAME_RE.match(name):
        raise InstanceError(f"インスタンス名は半角英数字・アンダースコア・ハイフンのみ、1〜32文字にしてください: {name!r}")


def instance_dir(instances_root: Path, name: str) -> Path:
    return instances_root / name


def instance_json_path(instances_root: Path, name: str) -> Path:
    return instance_dir(instances_root, name) / "instance.json"


def overrides_path(instances_root: Path, name: str) -> Path:
    return instance_dir(instances_root, name) / "config_overrides.json"


def env_path(instances_root: Path, name: str) -> Path:
    return instance_dir(instances_root, name) / ".env"


def settings_dir(instances_root: Path, name: str) -> Path:
    """インスタンス専用の表示設定（public/settings/ より優先される。app.py 参照）。"""
    return instance_dir(instances_root, name) / "settings"


def backups_dir(instances_root: Path, name: str) -> Path:
    return instance_dir(instances_root, name) / "backups"


def stdout_log_path(instances_root: Path, name: str) -> Path:
    return instance_dir(instances_root, name) / "app_stdout.log"


def list_instance_names(instances_root: Path) -> list[str]:
    """instance.json を持つディレクトリの一覧（名前順）。instances_root が無ければ空リスト。"""
    if not instances_root.is_dir():
        return []
    return sorted(p.name for p in instances_root.iterdir() if p.is_dir() and (p / "instance.json").is_file())


def read_instance(instances_root: Path, name: str) -> InstanceMeta:
    path = instance_json_path(instances_root, name)
    if not path.is_file():
        raise InstanceError(f"インスタンス {name!r} が見つかりません。")
    data = json.loads(path.read_text(encoding="utf-8"))
    return InstanceMeta(
        name=name,
        display_name=data.get("display_name", name),
        app_host=data.get("app_host", DEFAULT_APP_HOST),
        app_port=int(data.get("app_port", DEFAULT_APP_PORT)),
        autostart=bool(data.get("autostart", False)),
        # 既存インスタンス（headless/watch導入前に作成済み）との後方互換のため、
        # キー自体が無い場合は InstanceMeta の既定値（headless=True, watch=False）を使う。
        headless=bool(data.get("headless", True)),
        watch=bool(data.get("watch", False)),
    )


def write_instance(instances_root: Path, meta: InstanceMeta) -> None:
    path = instance_json_path(instances_root, meta.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_default_instance(instances_root: Path) -> InstanceMeta:
    """default インスタンスの instance.json が無ければ、app.bat と同じ 127.0.0.1:8000 で作る。"""
    if instance_json_path(instances_root, DEFAULT_INSTANCE_NAME).is_file():
        return read_instance(instances_root, DEFAULT_INSTANCE_NAME)
    meta = InstanceMeta(
        name=DEFAULT_INSTANCE_NAME,
        display_name="default",
        app_host=DEFAULT_APP_HOST,
        app_port=DEFAULT_APP_PORT,
        autostart=True,
    )
    write_instance(instances_root, meta)
    return meta


def is_port_available(host: str, port: int) -> bool:
    """host:port へ実際に bind してみて、空いているかを確認する。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def suggest_port(instances_root: Path, host: str, exclude_ports: set[int], start: int = 8000) -> int:
    """8000以上で、既存インスタンス・除外ポート・実際のbind確認のいずれとも重ならない最小のポートを提案する。"""
    used = {read_instance(instances_root, n).app_port for n in list_instance_names(instances_root)}
    used |= exclude_ports
    port = start
    while port in used or not is_port_available(host, port):
        port += 1
        if port > 65535:
            raise InstanceError("空いているポートが見つかりませんでした。")
    return port


def _effective_checkpoint_db(config_ini_path: Path, ov_path: Path, name: str) -> Path:
    """そのインスタンスの実効設定を解決し、checkpoint_db の絶対パスを返す。

    common_data_dir（延いてはデータ保存先全体）が重複していないかの比較に
    使う代表値。checkpoint_db は必ず存在する設定であり、common_data_dir の
    直下にある（config.ini既定）ため、これが一致すれば実質的に同じデータ
    ディレクトリを指しているとみなせる。
    """
    cfg = load_config(config_path=config_ini_path, overrides_path=ov_path, instance_name=name)
    return cfg.checkpoint_db


def _check_data_dir_conflict(instances_root: Path, config_ini_path: Path, *, name: str, own_checkpoint_db: Path) -> None:
    """他インスタンスとのデータ保存先（checkpoint_db）の重複だけを検出する（ポートは見ない）。

    check_conflicts() と check_data_dir_conflict_for_overrides() の共通処理。

    Raises:
        InstanceError: 重複が見つかった場合。
    """
    for other_name in list_instance_names(instances_root):
        if other_name == name:
            continue
        other_checkpoint_db = _effective_checkpoint_db(config_ini_path, overrides_path(instances_root, other_name), other_name)
        if other_checkpoint_db == own_checkpoint_db:
            raise InstanceError(
                f"データ保存先がインスタンス {other_name!r} と重複しています（{own_checkpoint_db}）。"
                "[paths].common_data_dir を分けてください。"
            )


def check_conflicts(
    instances_root: Path,
    config_ini_path: Path,
    *,
    name: str,
    app_host: str,
    app_port: int,
) -> None:
    """他インスタンスとのポート・データ保存先の重複を検出する。

    作成時・設定変更時（ポート変更）・起動時に呼ぶ。

    Raises:
        InstanceError: 重複が見つかった場合。
    """
    own_checkpoint_db = _effective_checkpoint_db(config_ini_path, overrides_path(instances_root, name), name)
    for other_name in list_instance_names(instances_root):
        if other_name == name:
            continue
        other_meta = read_instance(instances_root, other_name)
        if other_meta.app_host == app_host and other_meta.app_port == app_port:
            raise InstanceError(f"ポート {app_host}:{app_port} は既にインスタンス {other_name!r} が使用しています。")
    _check_data_dir_conflict(instances_root, config_ini_path, name=name, own_checkpoint_db=own_checkpoint_db)


def check_data_dir_conflict_for_overrides(
    instances_root: Path, config_ini_path: Path, *, name: str, overrides_tmp_path: Path
) -> None:
    """config_overrides.json 保存前の一時ファイルを反映した checkpoint_db が、
    他インスタンスと重複していないかを確認する。

    admin/overrides.py の preview()/save()/restore() から、
    load_config() 自体の検証が成功した直後に呼ぶ（2026-09-29 レビューで発見：
    設定ダッシュボードで [paths].common_data_dir を書き換えて他インスタンスと
    同じデータディレクトリを指す状態のまま保存でき、後にそのインスタンスを
    削除すると相手のデータまで消えてしまう事故があった。保存時点でここを
    塞ぐことで根本的に防ぐ）。

    Raises:
        InstanceError: 重複が見つかった場合。
    """
    cfg = load_config(config_path=config_ini_path, overrides_path=overrides_tmp_path, instance_name=name)
    _check_data_dir_conflict(instances_root, config_ini_path, name=name, own_checkpoint_db=cfg.checkpoint_db)


def create_instance(
    instances_root: Path,
    config_ini_path: Path,
    *,
    name: str,
    display_name: str | None = None,
    app_host: str = DEFAULT_APP_HOST,
    app_port: int | None = None,
    autostart: bool = False,
    headless: bool = True,
    watch: bool = False,
    copy_from: str | None = None,
) -> InstanceMeta:
    """新しいインスタンスを作成する。

    データの分離は config.ini 既定の common_data_dir = ./data/${instance} に
    任せる（上書きは書かない）。copy_from を指定した場合はそのインスタンスの
    config_overrides.json/.env の内容をコピーするが、common_data_dir の上書き
    だけは除く（同じデータディレクトリを2インスタンスが指す事故を防ぐ）。

    整合性チェック（ポート・データ保存先の重複）に失敗した場合は、作成した
    ディレクトリを削除してロールバックする。

    Raises:
        InstanceError: 名前が不正、既に存在する、または整合性チェック失敗。
    """
    validate_name(name)
    if instance_dir(instances_root, name).exists():
        raise InstanceError(f"インスタンス {name!r} は既に存在します。")
    if copy_from:
        # 未検証のまま instances_root / copy_from に使うと ".." 等で instances/ の
        # 外（プロジェクト直下の .env 等）をコピーできてしまうため、名前として
        # 検証し、実在するインスタンスに限る。
        validate_name(copy_from)
        if not instance_json_path(instances_root, copy_from).is_file():
            raise InstanceError(f"複製元のインスタンス {copy_from!r} が見つかりません。")
    if app_port is None:
        app_port = suggest_port(instances_root, app_host, exclude_ports=set())

    try:
        base_overrides: dict = {}
        if copy_from:
            src_ov = overrides_path(instances_root, copy_from)
            if src_ov.is_file():
                base_overrides = json.loads(src_ov.read_text(encoding="utf-8"))

        paths_ov = base_overrides.get("paths")
        if isinstance(paths_ov, dict):
            paths_ov.pop("common_data_dir", None)
            if not paths_ov:
                del base_overrides["paths"]

        target_ov_path = overrides_path(instances_root, name)
        target_ov_path.parent.mkdir(parents=True, exist_ok=True)
        if base_overrides:
            target_ov_path.write_text(
                json.dumps(base_overrides, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )

        if copy_from:
            src_env = env_path(instances_root, copy_from)
            if src_env.is_file():
                shutil.copy2(src_env, env_path(instances_root, name))

        meta = InstanceMeta(
            name=name,
            display_name=display_name or name,
            app_host=app_host,
            app_port=app_port,
            autostart=autostart,
            headless=headless,
            watch=watch,
        )
        write_instance(instances_root, meta)

        check_conflicts(instances_root, config_ini_path, name=name, app_host=app_host, app_port=app_port)
    except Exception:
        shutil.rmtree(instance_dir(instances_root, name), ignore_errors=True)
        raise
    return meta


def update_instance_meta(
    instances_root: Path,
    config_ini_path: Path,
    *,
    name: str,
    display_name: str | None = None,
    app_host: str | None = None,
    app_port: int | None = None,
    autostart: bool | None = None,
    headless: bool | None = None,
    watch: bool | None = None,
) -> InstanceMeta:
    """instance.json の一部フィールドを更新する（ポート変更時は整合性チェックを行う）。"""
    current = read_instance(instances_root, name)
    new_meta = InstanceMeta(
        name=name,
        display_name=display_name if display_name is not None else current.display_name,
        app_host=app_host if app_host is not None else current.app_host,
        app_port=app_port if app_port is not None else current.app_port,
        autostart=autostart if autostart is not None else current.autostart,
        headless=headless if headless is not None else current.headless,
        watch=watch if watch is not None else current.watch,
    )
    if (new_meta.app_host, new_meta.app_port) != (current.app_host, current.app_port):
        check_conflicts(instances_root, config_ini_path, name=name, app_host=new_meta.app_host, app_port=new_meta.app_port)
    write_instance(instances_root, new_meta)
    return new_meta


def _deletable_data_dir(config_ini_path: Path, instances_root: Path, name: str) -> Path | None:
    """インスタンス削除時に一緒に消してよいデータディレクトリ（無ければ None）。

    checkpoint_db の親（= 既定では common_data_dir）が <プロジェクト>/data/<name>
    （そのインスタン自身の名前のサブディレクトリ）である場合のみ対象にする。
    data/ 自体や、利用者が任意の場所へ変更したディレクトリはもちろん、
    [paths].common_data_dir の書き間違い等で別インスタンスのディレクトリ
    （例: data/default）を指してしまっている場合も、他インスタンスのデータを
    巻き込んで消してしまわないよう対象から外す（2026-09-29 レビューで発見：
    旧実装は「data/ の直下かどうか」しか見ておらず、bのcommon_data_dirを
    誤って ./data/default に向けた状態で b を削除すると default のデータが
    消えた）。
    """
    try:
        cfg = load_config(config_path=config_ini_path, overrides_path=overrides_path(instances_root, name), instance_name=name)
    except Exception:  # noqa: BLE001 - 設定が壊れていてもインスタンス自体の削除は妨げない
        return None
    data_dir = cfg.checkpoint_db.parent
    if data_dir.parent == (PROJECT_ROOT / "data").resolve() and data_dir.name == name and data_dir.is_dir():
        return data_dir
    return None


def delete_instance(instances_root: Path, name: str, config_ini_path: Path | None = None) -> None:
    """インスタンスのディレクトリ（config_overrides.json/.env/backups等）を丸ごと削除する。

    config_ini_path を渡すと、data/<name>/ のデータディレクトリも削除する
    （_deletable_data_dir() 参照）。

    呼び出し元（admin/server.py）は、事前に supervisor で稼働中でないことを
    確認してから呼ぶこと（このモジュール自体はプロセス管理を知らない）。

    Raises:
        InstanceError: default インスタンスを削除しようとした、または存在しない。
    """
    if name == DEFAULT_INSTANCE_NAME:
        raise InstanceError("既定インスタンス(default)は削除できません。")
    path = instance_dir(instances_root, name)
    if not path.is_dir():
        raise InstanceError(f"インスタンス {name!r} が見つかりません。")
    data_dir = _deletable_data_dir(config_ini_path, instances_root, name) if config_ini_path else None
    shutil.rmtree(path)
    if data_dir is not None:
        shutil.rmtree(data_dir)
