"""config.ini の読み取り専用パーサー。

configparser は使わない。configparser で読み込んで書き戻すと、コメント・
空行・書式がすべて失われる（configparser はコメントを保持しない）ため、
「キー一覧・既定値・直前のコメントを説明文として取り出す」目的に特化した
行ベースの簡易パーサーをここに実装する。

このモジュールは config.ini を**書き換えない**（管理ツールは config.ini を
既定値として扱い、変更は instances/<name>/config_overrides.json に記録する。
admin/overrides.py 参照）。そのため、ここでは「読んで一覧化する」ことだけに
責務を絞る。

configparser の継続行（値の1行目の次の行以降、行頭が空白/タブで始まる行は
前のキーの値の続き）と同じ規則で複数行値を検出する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# セクションヘッダ: "[section]"（前後の空白は許容、行末は改行のみ）。
_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")
# キー行: 行頭が空白でなく、識別子っぽい名前 = 値、の形。
# config.ini のキー名は英数字・アンダースコア・ドットのみ（例: context_trim.subagent
# はセクション名であり、キー名自体にドットを含む例は無いが、将来の拡張に備え許容する）。
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*)$")
# グループ見出しコメント: "# --- 見出しテキスト ---" 形式。
_HEADING_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
# 通常のコメント行（行頭が # で始まる行。config.ini は全コメントを行頭=カラム0に
# 置く方針で統一されているため、行頭空白は許容しない）。
_COMMENT_RE = re.compile(r"^#(.*)$")


@dataclass(frozen=True)
class KeyInfo:
    """config.ini 内の1キーの情報。"""

    section: str
    key: str
    # 値（"=" の右側）。複数行の場合は "\n" 区切りで、2行目以降は元のインデントを
    # そのまま含む（例: "[\n    {...},\n    ]"）。
    default_value: str
    # 直前に連続していたコメント行を "\n" 区切りで連結した説明文
    # （各行頭の "# " は取り除き済み）。無ければ空文字列。
    description: str
    # このキーが属するグループ見出し（直近の "# --- xxx ---" 行の xxx 部分）。
    # 見出しが一度も出ていない場合は None。
    group_heading: str | None
    # フォームでの表現形の推定: "bool" | "multiline" | "text"。
    ui_kind: str


@dataclass(frozen=True)
class IniCatalog:
    """config.ini 全体のパース結果。"""

    keys: tuple[KeyInfo, ...]
    # ファイルの最終更新時刻（管理ツールが config.ini 自体の変更を検知する用途。
    # config.ini 自体は書き換えないが、ユーザーが手動編集した場合に備える）。
    mtime: float


def _infer_ui_kind(value: str) -> str:
    """値の見た目から、フォームでの入力欄の種類を推定する。

    最終的な妥当性判定は src.config.load_config() 側のバリデーションに委ねる
    （ここでの推定はあくまでUI表示のヒント）。
    """
    stripped = value.strip()
    if "\n" in value or stripped.startswith("[") or stripped.startswith("{"):
        return "multiline"
    if stripped.lower() in ("true", "false"):
        return "bool"
    return "text"


def parse(text: str) -> IniCatalog:
    """config.ini のテキストを解析し、キー一覧を返す。

    Args:
        text: config.ini の全文（改行コードは "\n" 前提。呼び出し元で
            universal newlines 読み込み（open(..., newline=None) 相当）を
            済ませておくこと）。

    Returns:
        IniCatalog。mtime は呼び出し元が別途セットする想定のため常に 0.0。
    """
    lines = text.split("\n")
    n = len(lines)
    keys: list[KeyInfo] = []
    section: str | None = None
    current_heading: str | None = None
    pending_comment: list[str] = []

    i = 0
    while i < n:
        line = lines[i]

        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1)
            current_heading = None
            pending_comment = []
            i += 1
            continue

        comment_match = _COMMENT_RE.match(line)
        if comment_match:
            heading_match = _HEADING_RE.match(line)
            if heading_match:
                current_heading = heading_match.group(1)
                pending_comment = []
            else:
                pending_comment.append(comment_match.group(1).strip())
            i += 1
            continue

        if not line.strip():
            # 空行はコメントブロックとキーの結び付きを断つ（次のキーには
            # この空行より前のコメントを説明文として付けない）。
            pending_comment = []
            i += 1
            continue

        key_match = _KEY_RE.match(line) if section is not None else None
        if key_match:
            key = key_match.group(1)
            value_lines = [key_match.group(2)]
            j = i + 1
            while j < n and lines[j].strip() and lines[j][:1] in (" ", "\t"):
                value_lines.append(lines[j])
                j += 1
            value = "\n".join(value_lines)
            keys.append(
                KeyInfo(
                    section=section,
                    key=key,
                    default_value=value,
                    description="\n".join(pending_comment).strip(),
                    group_heading=current_heading,
                    ui_kind=_infer_ui_kind(value),
                )
            )
            pending_comment = []
            i = j
            continue

        # 未対応の行（キーでもコメントでもセクションでもない。通常は
        # 発生しないが、壊れた行があってもパース全体を止めない）。
        pending_comment = []
        i += 1

    return IniCatalog(keys=tuple(keys), mtime=0.0)


def parse_file(path: Path) -> IniCatalog:
    """config.ini ファイルを読んで parse() する。mtime も埋める。"""
    text = path.read_text(encoding="utf-8")
    catalog = parse(text)
    return IniCatalog(keys=catalog.keys, mtime=path.stat().st_mtime)


def find_key(catalog: IniCatalog, section: str, key: str) -> KeyInfo | None:
    """指定した [section].key の KeyInfo を返す（無ければ None）。"""
    for info in catalog.keys:
        if info.section == section and info.key == key:
            return info
    return None
