"""show_image ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
from pathlib import Path
import json
import logging

from ..images import is_image_file

from ._workdir import _foreign_tmp_dir_error, _resolve_workdir

logger = logging.getLogger(__name__)


@tool
def show_image(file_path: str) -> str:
    """既存の画像ファイルをチャット画面にプレビュー表示する（LLM自身は内容を見ない）。

    ユーザーが「表示して」「見せて」「プレビューして」のように画像そのものを
    見たいだけの依頼をしてきた場合は、まずこのツールを使う。画像の内容について
    自分（LLM）が説明・分析・判断してから答える必要がある場合にのみ、代わりに
    `analyze_image` を使うこと（`analyze_image` は画像をVision対応モデルへ実際に
    見せてLLM自身に解析させるツール。`show_image` は画像データをLLMへは渡さず、
    チャットUI上に表示するだけ）。迷ったら、ユーザーが求めているのが「画像そのものを
    見ること」か「画像についての説明」かで判断する。

    生成済みの画像（グラフ・スクリーンショット等）や、アップロード済み・
    Glob で見つけた既存の画像をユーザーへ見せたいときに使う。
    provide_download と同様にパスの制限は行わない（ローカルファイルシステム上の
    任意の絶対パスを指定できる。ただし `_tmp_<thread_id>` の他セッション分だけは
    例外で表示できない）。

    Args:
        file_path: 表示したい画像ファイルの絶対パス（相対パスの場合は
            セッションの作業ディレクトリ基準で解決する）。

    Returns:
        成功時: {"output_path": "..."} 形式のJSON文字列
            （自動的にチャットへ画像がプレビュー表示される）。
        失敗時: 「エラー: ...」形式の文字列。
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = _resolve_workdir() / path
    path = path.resolve()
    tmp_error = _foreign_tmp_dir_error(path)
    if tmp_error:
        return tmp_error
    if not path.is_file():
        return f"エラー: ファイルが見つかりません: {file_path}"
    if not is_image_file(path):
        return f"エラー: 画像ファイルではありません（対応形式: png/jpg/jpeg/gif/webp/bmp）: {file_path}"
    logger.info("show_image: %s", path)
    return json.dumps({"output_path": str(path)}, ensure_ascii=False)
