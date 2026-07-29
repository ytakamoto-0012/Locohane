"""ローカルファイルシステムに対する読込・検索のロジック層。

旧 skills/file-tools/scripts/*.py（read_file.py/glob_file.py/grep_file.py/
json_query.py）を run_script 経由のサブプロセススクリプトから、
src/tools.py の Read/Glob/Grep/json_query ツールへ直接組み込むための
純粋ロジック層として新設した（ISSUE-003）。

src/memory.py と同じ契約: Chainlit・パスメモリー・作業ディレクトリ解決には
一切依存しない。呼び出し元（src/tools.py）が解決済みの絶対パスを渡す前提で、
バリデーション・エラーメッセージ組み立てまでここで完結させ、例外は
ValueError のみを送出する。ツール層は ValueError を "エラー: ..." 形式の
文字列へ変換するだけの薄いラッパーに徹する。
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
from pathlib import Path

import jmespath
from jmespath.exceptions import JMESPathError

_OUTPUT_MODES = ("files_with_matches", "content", "count")


def read_text_with_fallback(path: Path) -> str:
    """UTF-8 で読めなければ CP932（Shift-JIS系）にフォールバックして読む。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp932")


def looks_binary(path: Path) -> bool:
    """先頭1024バイトに NUL バイトが含まれるかでバイナリらしさを判定する。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
        return b"\x00" in chunk
    except OSError:
        return False


def suggest_similar_dir(path: Path) -> str:
    """存在しないディレクトリパスに対し、実在する近い候補をヒント文字列として返す。

    ローカルLLMがパスを記憶から手打ちで組み立て直した際にスペルミス・余分な
    空白・区切り文字の欠落を起こし、同じ失敗を繰り返すケースへの対策。
    実在する最も近い祖先ディレクトリまで遡り、そこから先の欠けている
    ディレクトリ名に似たものが兄弟ディレクトリの中にないか探す。

    Args:
        path: 存在しなかったディレクトリパス（解決済みの絶対パスを渡すこと）。

    Returns:
        候補が見つかればエラーメッセージに追記できる短いヒント文字列
        （先頭に半角スペース付き）。見つからなければ空文字列。
    """
    target = path
    ancestor = target
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or ancestor == target:
        return ""

    remainder = target.relative_to(ancestor)
    if not remainder.parts:
        return ""
    missing_name = remainder.parts[0]

    try:
        siblings = [child.name for child in ancestor.iterdir() if child.is_dir()]
    except OSError:
        return ""

    close = difflib.get_close_matches(missing_name, siblings, n=1, cutoff=0.5)
    if not close:
        return ""

    suggested = ancestor / close[0]
    return (
        f" もしかして {suggested} ではありませんか？"
        " パスは記憶や推測で再構築せず、直前のツール結果に含まれる文字列や"
        " path_memory の @N をそのままコピーして使ってください。"
    )


def read_file(path: Path, offset: int = 0, limit: int = 10) -> dict:
    """行番号付きでテキストファイルを読み込む（旧 read_file.py 相当）。

    Args:
        path: 読み込む絶対パス。
        offset: 読み飛ばす先頭行数（0始まり）。
        limit: 読み込む最大行数。

    Returns:
        {"path", "total_lines", "start_line", "end_line", "content"}。

    Raises:
        ValueError: ファイルが存在しない・ディレクトリ・バイナリ・
            読み込み権限が無い・読み込みに失敗した場合。
    """
    if not path.exists():
        raise ValueError(f"ファイルが見つかりません: {path}")
    if path.is_dir():
        raise ValueError(f"指定パスはディレクトリです（ファイル専用）: {path}")
    if looks_binary(path):
        raise ValueError(f"バイナリファイルの可能性があるため読み込めません: {path}")

    try:
        text = read_text_with_fallback(path)
    except PermissionError as e:
        raise ValueError(f"ファイルへのアクセス権限がありません: {path}") from e
    except OSError as e:
        raise ValueError(f"ファイル読み込みに失敗しました: {e}") from e

    lines = text.splitlines()
    total_lines = len(lines)
    offset = max(offset, 0)
    selected = lines[offset : offset + limit]

    return {
        "path": str(path),
        "total_lines": total_lines,
        "start_line": offset + 1 if selected else None,
        "end_line": offset + len(selected) if selected else None,
        "content": "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)),
    }


def _count_contents(path: Path) -> dict:
    """path配下(再帰)に含まれるサブフォルダ数とファイル数を数える。

    サブエージェントへの分割委譲判断に使う概算値のため、権限エラー等の
    walk中の例外は無視して数えられた範囲だけを返す。
    """
    dir_count = 0
    file_count = 0
    for _root, dirs, files in os.walk(path, onerror=lambda e: None):
        dir_count += len(dirs)
        file_count += len(files)
    return {"directory_count": dir_count, "file_count": file_count}


def _file_detail(path: Path) -> dict:
    """head-limit適用後の1ファイルについてバイナリ判定と総行数を調べる。

    total_lines は read_file() の total_lines 算出（splitlines の長さ）と
    完全に揃え、モデルがそのまま limit の値を決められるようにする。
    """
    resolved = str(path.resolve())
    if looks_binary(path):
        return {"path": resolved, "binary": True, "total_lines": None}
    try:
        text = read_text_with_fallback(path)
    except OSError:
        return {"path": resolved, "binary": False, "total_lines": None}
    return {"path": resolved, "binary": False, "total_lines": len(text.splitlines())}


def glob_search(base: Path, pattern: str, head_limit: int = 200) -> dict:
    """指定ディレクトリ配下でglobパターンに一致するファイル/ディレクトリを検索する（旧 glob_file.py 相当）。

    Args:
        base: 検索起点ディレクトリの絶対パス。
        pattern: globパターン（例: "**/*.py"）。
        head_limit: ファイル・ディレクトリそれぞれに独立に適用する上限件数。

    Returns:
        {"base", "base_contents", "total_matches", "returned", "truncated",
         "files", "file_details", "total_directories", "returned_directories",
         "directories_truncated", "directories"}。

    Raises:
        ValueError: 起点ディレクトリが存在しない・ディレクトリでない・
            パターンが不正な場合。
    """
    if not base.exists():
        hint = suggest_similar_dir(base)
        raise ValueError(f"検索起点ディレクトリが見つかりません: {base}{hint}")
    if not base.is_dir():
        raise ValueError(f"検索起点パスがディレクトリではありません: {base}")

    try:
        all_matches = list(base.glob(pattern))
    except ValueError as e:
        raise ValueError(f"パターンが不正です: {e}") from e

    file_matches = [p for p in all_matches if p.is_file()]
    dir_matches = [p for p in all_matches if p.is_dir()]
    file_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    dir_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    truncated_files = file_matches[:head_limit]
    truncated_dirs = dir_matches[:head_limit]

    files = [str(p.resolve()) for p in truncated_files]
    directory_paths = [str(p.resolve()) for p in truncated_dirs]
    file_details = [_file_detail(p) for p in truncated_files]
    directories = [
        {"path": path_str, **_count_contents(p)}
        for path_str, p in zip(directory_paths, truncated_dirs)
    ]
    base_contents = _count_contents(base)

    return {
        "base": str(base.resolve()),
        "base_contents": base_contents,
        "total_matches": len(file_matches),
        "returned": len(truncated_files),
        "truncated": len(file_matches) > head_limit,
        "files": files,
        "file_details": file_details,
        "total_directories": len(dir_matches),
        "returned_directories": len(truncated_dirs),
        "directories_truncated": len(dir_matches) > head_limit,
        "directories": directories,
    }


def grep_search(
    base: Path,
    pattern: str,
    glob: str = "",
    output_mode: str = "files_with_matches",
    case_insensitive: bool = False,
    context: int = 0,
    head_limit: int = 50,
) -> dict:
    """指定ファイル/ディレクトリ配下のテキストから正規表現で検索する（旧 grep_file.py 相当）。

    Args:
        base: 検索対象の絶対パス（ファイルまたはディレクトリ）。
        pattern: 検索する正規表現。
        glob: ディレクトリ検索時にファイル名を絞り込むglobパターン（省略可）。
        output_mode: "files_with_matches" | "content" | "count"。
        case_insensitive: 大文字小文字を無視するか。
        context: "content" モード時、マッチ行の前後何行を含めるか。
        head_limit: 返却件数の上限。

    Returns:
        output_mode に応じた形状の辞書（マッチ0件は
        {"matched": False, "files": [], "matches": [], "counts": []}）。

    Raises:
        ValueError: 正規表現が不正・output_modeが不正・対象パスが存在しない場合。
    """
    if output_mode not in _OUTPUT_MODES:
        raise ValueError(f"output_mode が不正です: {output_mode}")
    try:
        regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as e:
        raise ValueError(f"正規表現が不正です: {e}") from e

    if not base.exists():
        hint = suggest_similar_dir(base)
        raise ValueError(f"検索対象パスが見つかりません: {base}{hint}")

    target_files: list[Path] = []
    if base.is_file():
        target_files.append(base)
    else:
        for root, _dirs, files in os.walk(base):
            for name in files:
                if glob and not fnmatch.fnmatch(name, glob):
                    continue
                target_files.append(Path(root) / name)

    files_out: list[str] = []
    counts_out: list[dict] = []
    matches_out: list[dict] = []

    for file_path in target_files:
        if looks_binary(file_path):
            continue
        try:
            text = read_text_with_fallback(file_path)
        except OSError:
            continue

        lines = text.splitlines()
        match_line_indexes = [i for i, line in enumerate(lines) if regex.search(line)]
        if not match_line_indexes:
            continue

        resolved = str(file_path.resolve())
        files_out.append(resolved)
        counts_out.append({"path": resolved, "count": len(match_line_indexes)})

        if output_mode == "content":
            for idx in match_line_indexes:
                start = max(0, idx - context)
                end = min(len(lines), idx + context + 1)
                for i in range(start, end):
                    matches_out.append({"path": resolved, "line": i + 1, "text": lines[i]})

        if len(files_out) >= head_limit and output_mode != "content":
            break

    if not files_out:
        return {"matched": False, "files": [], "matches": [], "counts": []}

    if output_mode == "files_with_matches":
        truncated = files_out[:head_limit]
        return {
            "matched": True,
            "total_files": len(files_out),
            "returned": len(truncated),
            "files": truncated,
        }
    if output_mode == "count":
        truncated_counts = counts_out[:head_limit]
        return {
            "matched": True,
            "total_files": len(counts_out),
            "returned": len(truncated_counts),
            "counts": truncated_counts,
        }
    truncated_matches = matches_out[:head_limit]
    return {
        "matched": True,
        "total_matches": len(matches_out),
        "returned": len(truncated_matches),
        "truncated": len(matches_out) > head_limit,
        "matches": truncated_matches,
    }


def query_json(query: str, file_path: Path | None = None, json_text: str | None = None) -> dict:
    """JSONデータにJMESPathクエリを実行する（旧 json_query.py 相当）。

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
        except OSError as e:
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
