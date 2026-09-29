"""管理ツールの変更履歴ログ（instances/admin_changes.log、JSON Lines）。

全インスタンス共通の1ファイルに、1操作1行のJSONを追記する。config系の
変更ではキーごとの旧値・新値も記録するが、パスワード・APIキー等の
機密情報は記録前にマスクする（is_sensitive_key/mask）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# 値をマスクする対象と判定するキー名の断片（小文字比較）。
_SENSITIVE_KEY_MARKERS = ("api_key", "password", "secret", "token")
# キー名そのものには上記の断片を含まないが、値の内部構造に機密情報を
# 埋め込みうる既知のキー（完全一致で判定）。[llm].main_url/sub_url は
# {"base_url":..., "api_key":..., "model":...} のリストで、api_key は
# キー名ではなくJSON内のフィールド名として埋まるため、_SENSITIVE_KEY_MARKERS
# の部分一致だけでは拾えない。値全体を丸ごとマスクする（部分マスクは
# JSON構造を壊すリスクがあり割に合わないため）。
_SENSITIVE_EXACT_KEYS = frozenset({"main_url", "sub_url"})

_MASKED = "***"


def is_sensitive_key(key: str) -> bool:
    """キー名（例: "main_url" や "AUTH_USERS"）が機密情報らしいかを判定する。"""
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def mask(key: str, value: object) -> object:
    """機密キーの値をマスクして返す。非機密キーはそのまま返す。

    value が None の場合はそのまま None を返す（「そのキーが存在しなかった」
    ことと「値が空文字列」を区別するため）。
    """
    if value is None:
        return None
    if is_sensitive_key(key):
        return _MASKED
    return value


def append(log_path: Path, entry: dict) -> None:
    """1エントリを追記する。entry に "ts" が無ければ現在時刻を補う。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    line = json.dumps(record, ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_recent(log_path: Path, limit: int = 200, instance: str | None = None) -> list[dict]:
    """直近のログを新しい順に最大 limit 件返す。壊れた行はスキップする。"""
    if not log_path.is_file():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entries: list[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if instance is not None and entry.get("instance") != instance:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries
