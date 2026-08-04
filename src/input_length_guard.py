"""ユーザーが1ターンで送信した生入力テキストの文字数を、LLM呼び出し前に事前チェックする。

src/main_token_guard.py は「直近のLLM応答の usage_metadata.total_tokens」を
ReAct ループの各モデル呼び出しごとに監視するのに対し、こちらは「ユーザーの
生入力テキスト（message.content。UNCパス置換や添付ファイルパス追記より前）の
文字数」を app.py の on_message でターン開始時に一度だけチェックする、判定材料・
タイミングの異なる別機構。

低パラメータモデルは一度に大量のテキストを渡されると処理しきれず、応答品質が
落ちたりコンテキスト上限に張り付いたりする。しかしLLM自身は送信前の入力量を
把握できないため、コード側で文字数を測って判定する。

判定対象はユーザーの1ターンの生入力のみ（会話履歴全体や送信ペイロード全体は
対象外）。閾値を超えてもLLM呼び出し自体はスキップせず、「段階を踏んで分割して
進めてください」という趣旨の注意書きを本文の先頭に追加してLLMへ渡す（本文の
末尾に付けると、巨大な本文を読み進める前に注意書きへ気づけないおそれがある
ため）。
"""

from __future__ import annotations

import logging

from .config import Config

logger = logging.getLogger(__name__)

# 差し込む注意書きの先頭に付ける目印。ログ・テストからの識別用。
INPUT_LENGTH_GUARD_MARKER = "[入力文字数超過]"


def apply_input_length_guard(user_text: str, raw_length: int, config: Config) -> str:
    """生入力の文字数が閾値を超えていれば、分割を促す注意書きを本文の先頭へ足す。

    Args:
        user_text: LLMへ渡す本文（register_raw_unc_paths_in_text 適用後の
            processed_text を想定）。ここへ注意書きを prepend する。
        raw_length: 閾値判定に使う生入力の文字数（message.content の len()。
            UNCパス置換や添付ファイルパス追記より前の値であること）。
        config: graph_input_length_guard_* を含むアプリ設定。

    Returns:
        閾値超過時は注意書きを先頭に追加した新しい文字列。無効化されている
        場合・閾値以下の場合は user_text をそのまま返す。
    """
    if not config.graph_input_length_guard_enabled:
        return user_text
    if raw_length <= config.graph_input_length_guard_threshold_chars:
        return user_text

    logger.warning(
        "ユーザー入力の文字数が閾値(%d)を超えました(実測: %d文字)。"
        "分割指示を注入します",
        config.graph_input_length_guard_threshold_chars,
        raw_length,
    )
    notice = f"{INPUT_LENGTH_GUARD_MARKER} {config.graph_input_length_guard_warning_text}"
    return f"{notice}\n\n{user_text}"
