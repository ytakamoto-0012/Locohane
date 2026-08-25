"""json_query ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
from pathlib import Path
import hashlib
import json
import logging

import jmespath
from jmespath.exceptions import JMESPathError

from ._duplicate_guard import _check_file_tools_duplicate
from ._file_tools_common import read_text_with_fallback
from ._safe_path import _resolve_file_tools_path

logger = logging.getLogger(__name__)


def query_json(query: str, file_path: Path | None = None, json_text: str | None = None) -> dict:
    """JSONデータにJMESPathクエリを実行する。

    Args:
        query: JMESPathクエリ文字列。
        file_path: クエリ対象のJSONファイルの絶対パス。
        json_text: クエリ対象のJSON文字列を直接渡す場合に使う。
            file_path と同時指定・両方省略はエラー。

    Returns:
        {"result": ...}。

    Raises:
        ValueError: file_path/json_text の指定が排他条件を満たさない・
            ファイルが存在しない・JSONの解析に失敗した・
            JMESPathクエリが不正な場合。
    """
    if file_path is not None and json_text is not None:
        raise ValueError("file_path と json_text は同時に指定できません")
    if file_path is None and json_text is None:
        raise ValueError("file_path と json_text のどちらか一方を指定してください")

    if file_path is not None:
        if not file_path.exists():
            raise ValueError(f"ファイルが見つかりません: {file_path}")
        try:
            raw = read_text_with_fallback(file_path)
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"ファイル読み込みに失敗しました: {e}") from e
    else:
        raw = json_text or ""
        if not raw.strip():
            raise ValueError("json_text が空です")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSONの解析に失敗しました: {e}") from e

    try:
        result = jmespath.search(query, data)
    except JMESPathError as e:
        raise ValueError(f"JMESPathクエリが不正です: {e}") from e

    return {"result": result}


@tool
def json_query(query: str, file_path: str = "", json_text: str = "") -> str:
    """JSON/dictデータにJMESPathクエリを実行する。

    JMESPath は jq とは構文が異なる点に注意。
    - パスの先頭に `.` は付けない（`.a.b` ではなく `a.b`）。
    - フィルタ `[?...]` の右辺で数値・真偽値・null を書くときはバッククォートで
      囲む。バックスラッシュでのエスケープは不要（正: `` age > `30` ``）。
    - 文字列リテラルはシングルクォートで囲む（例: `name == 'foo'`）。

    例:
    - `items[?active == `true`].{name: name, id: id}` → active が true の
      要素だけを name/id に整形して抽出
    - `a.b[0].c` → ネストしたキー・配列インデックスへのアクセス

    読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。

    Args:
        query: JMESPathクエリ文字列。
        file_path: クエリ対象のJSONファイルの絶対パス（`@N` 可）。
            file_path/json_text のどちらか一方を必ず指定すること。
        json_text: クエリ対象のJSON文字列を直接渡す場合に使う
            （execute_python_code の出力等をその場でクエリしたい場合）。
            file_path と同時指定・両方省略はエラー。

    Returns:
        `{"result": ...}` のJSON文字列（該当データが無ければ `{"result": null}`）。
        file_path/json_text の指定不備・JSON解析失敗・クエリ不正の場合は
        例外を送出せず「エラー: ...」形式の文字列を返す。
    """
    resolved_path: Path | None = None
    if file_path:
        resolved_path, error = _resolve_file_tools_path(file_path)
        if error:
            return f"エラー: {error}"
    signature_source = json_text if not file_path else ""
    signature = (
        f"json_query\x00{query}\x00{resolved_path or ''}\x00"
        f"{hashlib.sha256(signature_source.encode('utf-8')).hexdigest() if signature_source else ''}"
    )
    dup_error = _check_file_tools_duplicate("json_query", signature)
    if dup_error:
        return dup_error
    try:
        result = query_json(query, file_path=resolved_path, json_text=json_text or None)
    except ValueError as e:
        return f"エラー: {e}"
    logger.info("json_query: %s", query)
    return json.dumps(result, ensure_ascii=False)
