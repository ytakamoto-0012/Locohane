"""get_tool_source ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
import logging

from ._duplicate_guard import _check_file_tools_duplicate
from ._safe_path import _resolve_script_filename

logger = logging.getLogger(__name__)


@tool
def get_tool_source(skill_name: str, script_filename: str) -> str:
    """run_script で実行したスクリプトの絶対パスを返す（中身は返さない）。

    run_script がエラー（非0終了コード・スタックトレース）を返した場合の原因調査に使う。
    このツールでソースファイルの絶対パスを取得し、必要なら read_skill_file で中身を
    確認するか、execute_python_code 内で `sys.path.insert(0, "<このパスの親ディレクトリ>")`
    のようにして _common.py 等の同スキル内ヘルパーモジュールを直接 import して調査・
    代替コードの実行に使う。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 探したいファイル名（例: "read_file.py"）。パスや scripts/
            プレフィックスは不要 — スキルフォルダの scripts/ 配下から自動検索される。

    Returns:
        スクリプトの絶対パス文字列。skill_name に scripts/ ディレクトリが無い場合、
        スクリプトが見つからない場合は、例外を送出せず「エラー: ...」形式の
        文字列を返す。
    """
    try:
        script_path = _resolve_script_filename(skill_name, script_filename)
    except ValueError as e:
        return f"エラー: {e}"
    dup_error = _check_file_tools_duplicate("get_tool_source", f"get_tool_source\x00{skill_name}\x00{script_filename}")
    if dup_error:
        return dup_error
    logger.info("get_tool_source: %s/%s", skill_name, script_filename)
    return str(script_path)
