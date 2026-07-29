"""プロジェクト固有指示ファイル（ClaudeCode の CLAUDE.md 相当）の読み込み。

.locohane/LOCOHANE.md が存在すれば、その内容をシステムプロンプトの
{{project_instructions}} へ差し込む。ファイルが無くてもエラーにはならず、
プレースホルダーテキストが差し込まれるだけ（CLAUDE.mdと同じ仕様）。

複数ファイルのマージ・階層探索などは行わない。config.ini で指定された
1ファイルを読むだけ、それだけ。
"""

from __future__ import annotations

from pathlib import Path

_MAX_LINES = 200


def render_project_instructions_block(path: Path, max_lines: int = _MAX_LINES) -> str:
    """system_prompt.md の {{project_instructions}} へ差し込むテキストを組み立てる。

    Args:
        path: LOCOHANE.md の絶対パス（config.project_instructions_path）。
        max_lines: 差し込む最大行数。

    Returns:
        差し込み用テキスト。ファイルが存在しない/空の場合は
        「（プロジェクト固有の指示はありません）」を返す。
    """
    if not path.is_file():
        return "（プロジェクト固有の指示はありません）"
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [
            "",
            f"…（{max_lines}行を超えたため以降は切り詰められました。"
            f"全文は {path} を直接確認してください）",
        ]
    text = "\n".join(lines).strip()
    return text or "（プロジェクト固有の指示はありません）"
