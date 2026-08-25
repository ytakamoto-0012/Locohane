"""run_script ツール。"""

from __future__ import annotations

from langchain_core.tools import tool

from ._script_job import _run_script_impl


@tool
async def run_script(skill_name: str, script_filename: str, script_args: list[str] | None = None) -> str:
    """スキルの scripts/ 配下のスクリプトを実行し、標準出力/標準エラーを返す。

    作業ディレクトリは、作業フォルダアイコンでユーザーが
    セッションに設定していればそのディレクトリを使う。未設定・アクセス不可・
    書き込み不可の場合は既定の作業フォルダ（default_workdir）配下の
    自セッション専用一時フォルダ（`_tmp_<thread_id>`）を使う
    （default_workdirはサーバー側の共有フォルダのため、直下に成果物を
    書くと他セッションから見えてしまう事故を避けるため。この場合の
    成果物はユーザーへ直接見えないため、provide_download/show_image で
    改めて提示すること）。
    タイムアウトは設定値（既定 60 秒）。完了までこのツール呼び出し自体が
    ブロックされるため、タイムアウトに近い長時間の実行が見込まれるスクリプトは
    このツールではなく run_script_background を使うこと。
    .py スクリプトは設定された Python 実行ファイルで起動する。
    Agent Skills 標準の progressive disclosure における第3段階（Execute）に相当する。
    書き込み系ツールのため、create_plan/approve_plan で計画が承認済みでない
    限り実行できない（未承認の場合はエラーを返す）。ただし副作用のない
    読み取り専用スクリプト（一部は事前に例外登録されている。例: excel-vba-read の
    read_vba.py）はこの承認チェックを免除される。

    **重要: 書き込みは作業ディレクトリ配下限定（サンドボックス）**
    起動するサブプロセスへ書き込みガードを注入しており、open()の書き込み
    モードや os.remove/os.rename/shutil.move 等の呼び出し先が作業
    ディレクトリ・`_tmp_<thread_id>` 配下以外（他ドライブ・Locohaneプロジェクト
    本体、default_workdir直下の他ディレクトリを含む）の場合は
    PermissionError で失敗する。出力先パス（output_path/--output 等）は
    必ず作業ディレクトリ配下を指定すること。default_workdirへ絶対パスで
    直接書き込もうとしても（`_tmp_<thread_id>`以外は）ブロックされる。
    既存ファイルの読み取りはこのガードの対象外で制限されない。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 実行したいスクリプトのファイル名（例: "count.py"）。
            パスや scripts/ プレフィックスは不要 — スキルフォルダの scripts/
            配下から自動検索される。同名ファイルが複数階層にある場合は
            最も浅い階層のものが使われる。
        script_args: スクリプトへ渡す追加引数のリスト（省略可）。書き込み先の
            パスは作業ディレクトリ配下を指定すること（上記サンドボックス参照）。

    Returns:
        「[終了コード] N」に続けて、標準出力・標準エラーがあれば
        それぞれ「[標準出力]」「[標準エラー]」の見出し付きで連結した文字列。
        スキルに scripts/ が無い場合、スクリプトが見つからない場合、
        計画が未承認の場合、タイムアウトした場合、起動自体に失敗した場合は
        いずれも例外を送出せず「エラー: ...」形式で返す。
    """
    return await _run_script_impl(skill_name, script_filename, script_args)
