"""eval 実行対象のインスタンス（設定ダッシュボードで作る instances/<name>/）を適用する。

本番の各インスタンスは、管理ツール（admin/supervisor.py）が子プロセス起動時に
渡す環境変数 LOCOHANE_INSTANCE / CONFIG_OVERRIDES_PATH / LOCOHANE_INSTANCE_ENV と、
app.py 先頭で override=True で読むインスタンス別 .env によって設定が決まる。
eval もこれと同じ手順を踏まないと、`default` インスタンスの設定（または
インスタンス別 .env の LLM_MAIN_URL 等の上書きが抜けた設定）で動いてしまい、
狙った環境でのテストにならない。

使い方（適用後の実効設定の確認用）:
    python -m evals.instance <instance>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from admin.instances import InstanceError, validate_name  # noqa: E402
from src.config import DEFAULT_INSTANCE_NAME, resolve_instances_root  # noqa: E402


def resolve_instance_name(cli_value: str | None) -> str:
    """実行対象インスタンス名を決める（--instance > 環境変数 LOCOHANE_INSTANCE > default）。

    Raises:
        SystemExit: インスタンス名が不正、またはインスタンスが存在しない場合。
    """
    name = cli_value or os.getenv("LOCOHANE_INSTANCE") or DEFAULT_INSTANCE_NAME
    try:
        validate_name(name)
    except InstanceError as e:
        raise SystemExit(str(e)) from e
    instances_root = resolve_instances_root()
    # default は管理ツールを一度も起動していない（instances/ 自体が無い）環境でも
    # config.ini だけで動くため、ディレクトリが無くても許容する。
    if name != DEFAULT_INSTANCE_NAME and not (instances_root / name).is_dir():
        existing = sorted(p.name for p in instances_root.iterdir() if p.is_dir()) if instances_root.is_dir() else []
        raise SystemExit(f"インスタンスが見つかりません: {name!r}（{instances_root}、既存: {existing}）")
    return name


def apply_instance(name: str) -> None:
    """管理ツールの子プロセス起動 + app.py 先頭と同じ手順でインスタンスを適用する。

    load_config() より前に呼ぶこと。インスタンス別 .env は override=True で
    読むため、この後に設定する環境変数（run_case.py の MEMORY_DIR 等の隔離用や
    ケース yaml の env:）はさらにそれを上書きできる。
    """
    instance_dir = resolve_instances_root() / name
    os.environ["LOCOHANE_INSTANCE"] = name
    os.environ["CONFIG_OVERRIDES_PATH"] = str(instance_dir / "config_overrides.json")
    os.environ["LOCOHANE_INSTANCE_ENV"] = str(instance_dir / ".env")
    load_dotenv(instance_dir / ".env", override=True)


def main() -> int:
    """指定インスタンスを適用した実効設定（LLM接続先・ログ出力先等）を表示する。"""
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    name = resolve_instance_name(sys.argv[1] if len(sys.argv) > 1 else None)
    apply_instance(name)
    from src.config import load_config

    config = load_config()
    print(f"instance: {name}")
    print(f"config_overrides: {os.environ['CONFIG_OVERRIDES_PATH']}")
    print(f"main_url: {[e.base_url for e in config.main_endpoints]}")
    print(f"sub_url: {[e.base_url for e in config.sub_endpoints]}")
    print(f"log_dir: {config.log_dir}")
    print(f"default_workdir: {config.default_workdir}")
    print(f"skill_draft_dir: {config.skill_draft_dir}")
    print(f"skill_tryout_repeats: {config.skill_tryout_repeats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
