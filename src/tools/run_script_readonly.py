"""run_script_readonly ツール。"""

from __future__ import annotations

from langchain_core.tools import tool

from ._script_job import _run_script_readonly_impl


@tool
async def run_script_readonly(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """スキルの scripts/ 配下のスクリプトを実行し、標準出力/標準エラーを返す
    （読み取り専用・書き込み不可版）。

    run_script と同じ方式（スキル作者が書いた既存の scripts/ 配下のスクリプトを
    そのまま起動）だが、書き込み・削除・改名を場所を問わず一切許可しない。
    既存ファイルの読み込み・解析専用のスクリプト（*-read/*-render 系の
    read_*.py/render_*.py 等）に使う。成果物ファイルの生成・編集には
    このツールではなく run_script、または対応するスキルの書き込み系スクリプトを
    使うこと（このツールで書き込もうとしても PermissionError になり失敗する）。

    run_script と異なり計画承認（Plan Mode）を必要としない（書き込みが
    一切できないため、計画未承認でも安全に実行できる）。agent_type ごとの
    スキル/スクリプト制限（呼び出し元が agent_type 付きのサブエージェントの
    場合）は run_script と同様に適用される。

    作業ディレクトリは run_script と同じ解決順（セッションの作業フォルダ →
    未設定・アクセス不可なら default_workdir）だが、書き込みを行わないため
    `_tmp_<thread_id>` への縮小は行わない。タイムアウトは設定値（既定 60 秒）。
    .py スクリプトは設定された Python 実行ファイルで起動する。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 実行したいスクリプトのファイル名（例: "read_excel.py"）。
            パスや scripts/ プレフィックスは不要 — スキルフォルダの scripts/
            配下から自動検索される。同名ファイルが複数階層にある場合は
            最も浅い階層のものが使われる。
        script_args: スクリプトへ渡す追加引数のリスト（省略可）。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラーがあれば
        それぞれ「[標準出力]」「[標準エラー]」の見出し付きで連結した文字列。
        スキルに scripts/ が無い場合、スクリプトが見つからない場合、
        タイムアウトした場合、起動自体に失敗した場合、書き込みガードの
        準備自体に失敗した場合はいずれも例外を送出せず「エラー: ...」形式で
        返す。スクリプトが書き込み・削除・改名を試みた場合は PermissionError と
        なり、その内容が標準エラーに含まれる。
    """
    return await _run_script_readonly_impl(skill_name, script_filename, script_args)
