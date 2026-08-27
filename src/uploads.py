"""アップロードファイルの保持期間管理。

役割:
- config.upload_dir 直下にはログインユーザー名ごとのサブフォルダが
  作られ（app.py の _save_uploads 参照）、その中にアップロードファイル
  がフラットに溜まり続ける。config.ini の [uploads] で指定した保持日数
  を過ぎたファイルを、ファイル単位で自動削除する（サブフォルダ自体の
  更新日時で一括判定すると、使い続けているユーザーの古いファイルが
  いつまでも消えないため、個々のファイルの更新日時で判定する）。
  削除の結果空になったユーザーフォルダは併せて削除する。
- 起動時の1回チェック（cleanup_old_uploads）と、アプリ稼働中の定期チェック
  （run_cleanup_loop、asyncio.create_task で常駐させる想定）の両方から使う。

実体は src/cleanup.py の汎用実装（サブディレクトリ配下の期限切れ
ファイル削除）をそのまま呼ぶ薄いラッパー。関数名・シグネチャは
呼び出し元（app.py）との互換のため維持している。
"""

from __future__ import annotations

from pathlib import Path

from .cleanup import cleanup_old_files_in_subdirs, run_cleanup_files_in_subdirs_loop as _run_cleanup_loop


def cleanup_old_uploads(upload_dir: Path, retention_days: int) -> int:
    """upload_dir 直下の各ユーザーフォルダ内の、更新日時が retention_days より古いファイルを削除する。

    Args:
        upload_dir: チェック対象のアップロード保存先ディレクトリ。
        retention_days: 保持日数。0以下を指定した場合は何もせず 0 を返す。

    Returns:
        削除したファイル数。
    """
    return cleanup_old_files_in_subdirs(upload_dir, retention_days)


async def run_cleanup_loop(upload_dir: Path, retention_days: int, interval_hours: float) -> None:
    """interval_hours 間隔で cleanup_old_uploads を回し続ける常駐タスク。

    Args:
        upload_dir: チェック対象のアップロード保存先ディレクトリ。
        retention_days: 保持日数。0以下の場合は即座に return する。
        interval_hours: チェック間隔（時間）。

    Returns:
        None。
    """
    await _run_cleanup_loop(upload_dir, retention_days, interval_hours)
