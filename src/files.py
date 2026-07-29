"""ツール実行結果から生成ファイルのパスを抽出する共通ヘルパー。

app.py（Chainlit UIへのダウンロード添付表示）から使う。ファイルを生成する
各スキルのスクリプト（例: pdf-tools/create_pdf.py, pptx-tools/create_pptx.py）は
正常終了時のJSON出力に "output_path" キー（生成した絶対パス）を含める慣習が
あり（skills/SKILLS_README.md 参照）、run_script はその標準出力を含む文字列を
そのままツール結果として返す。また provide_download のように run_script を
介さないツールは、素の JSON 文字列（"[標準出力]" のラップなし）をそのまま
返すことでも同じ経路に乗れる。ここではどちらの形式からも "output_path" を
取り出す処理だけを集約する。ツール名やスキル名による分岐は行わないため、
この慣習さえ守れば新しい生成スキル・ツールを追加しても app.py 側の変更は
不要になる。
"""

from __future__ import annotations

import json
from pathlib import Path

_STDOUT_MARKER = "[標準出力]\n"
_STDERR_MARKER = "\n[標準エラー]"


def extract_generated_file(tool_output: str) -> Path | None:
    """ツール結果文字列から、生成されたファイルのパスを抽出する。

    run_script の "[標準出力]\n<json>" 形式、または素の JSON 文字列の
    いずれかから dict を取り出し、"output_path" キーがあればそのパスを返す。
    JSON解析失敗、キー不在、対象ファイルが実在しない場合はいずれも例外を
    送出せず None を返す。

    Args:
        tool_output: ToolMessage.content（run_script の戻り値文字列や、
            provide_download 等が返す素のJSON文字列）。

    Returns:
        生成ファイルの絶対パス（実在する場合のみ）。それ以外は None。
    """
    idx = tool_output.find(_STDOUT_MARKER)
    if idx != -1:
        stdout_text = tool_output[idx + len(_STDOUT_MARKER):].split(_STDERR_MARKER, 1)[0].strip()
    else:
        stdout_text = tool_output.strip()

    try:
        data = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    path_str = data.get("output_path")
    if not isinstance(path_str, str) or not path_str:
        return None

    path = Path(path_str)
    return path if path.is_file() else None
