"""Grep ツール。"""

from __future__ import annotations

from langchain_core.tools import tool
from pathlib import Path
import fnmatch
import json
import logging
import os
import re

from ._duplicate_guard import _check_file_tools_duplicate
from ._file_tools_common import looks_binary, read_text_with_fallback, suggest_similar_dir
from ._path_memory_helpers import _register_path_memory
from ._safe_path import _resolve_file_tools_path
from ._workdir import _foreign_tmp_dir_names

logger = logging.getLogger(__name__)

_OUTPUT_MODES = ("files_with_matches", "content", "count")


def grep_search(
    base: Path,
    pattern: str,
    glob: str = "",
    output_mode: str = "content",
    case_insensitive: bool = False,
    context: int = 0,
    head_limit: int = 50,
    exclude_names: frozenset[str] = frozenset(),
) -> dict:
    """指定ファイル/ディレクトリ配下のテキストから正規表現で検索する。

    Args:
        base: 検索対象の絶対パス（ファイルまたはディレクトリ）。
        pattern: 検索する正規表現。
        glob: ディレクトリ検索時にファイル名を絞り込むglobパターン（省略可）。
        output_mode: "files_with_matches" | "content" | "count"。
        case_insensitive: 大文字小文字を無視するか。
        context: "content" モード時、マッチ行の前後何行を含めるか。
        head_limit: 返却件数の上限。
        exclude_names: 走査対象から除外するディレクトリ名の集合
            （basenameで一致するディレクトリは配下ごと走査しない。空なら
            従来通り無条件で全て対象）。

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
        for root, dirs, files in os.walk(base):
            if exclude_names:
                dirs[:] = [d for d in dirs if d not in exclude_names]
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
        except (OSError, UnicodeDecodeError):
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


@tool("Grep")
def grep_tool(
    pattern: str,
    path: str = "",
    glob: str = "",
    case_insensitive: bool = False,
    context: int = 0,
    head_limit: int = 50,
) -> str:
    """指定ファイル・ディレクトリ配下のテキストから正規表現で検索し、マッチした行番号・内容を返す。

    読み取り専用のため、計画の有無に関わらずいつでも呼んでよい。
    ファイル名ではなくファイルの中身（テキスト）を検索する（ファイル名検索は `Glob` を使うこと）。

    Args:
        pattern: 検索する正規表現。
        path: 検索対象の絶対パス（ファイルまたはディレクトリ、`@N` 可）。
            省略時は作業ディレクトリ配下を検索する。
        glob: 検索前にファイル名で対象を絞り込むglobパターン（省略可、例: "*.py"）。
            ディレクトリ検索時のみ有効。マッチ結果には現れない事前フィルタ。
        case_insensitive: True で大文字小文字を無視する。
        context: マッチ行の前後何行を含めるか（既定0）。
        head_limit: 返却するマッチ件数の上限（既定50）。

    Returns:
        `{"matched", "total_matches", "returned", "truncated",
        "matches": [{"path", "line", "text"}, ...], "path_memory"}` 形状のJSON文字列。
        マッチが1件も無い場合は `{"matched": false, "files": [], "matches": [], "counts": []}`。
        正規表現が不正・対象パスが存在しない場合は例外を送出せず
        「エラー: ...」形式の文字列を返す。
    """
    base, error = _resolve_file_tools_path(path)
    if error:
        return f"エラー: {error}"
    signature = f"Grep\x00{pattern}\x00{base}\x00{glob}\x00" f"{case_insensitive}\x00{context}\x00{head_limit}"
    dup_error = _check_file_tools_duplicate("Grep", signature)
    if dup_error:
        return dup_error
    try:
        result = grep_search(
            base,
            pattern,
            glob=glob,
            output_mode="content",
            case_insensitive=case_insensitive,
            context=context,
            head_limit=head_limit,
            exclude_names=_foreign_tmp_dir_names(),
        )
    except ValueError as e:
        return f"エラー: {e}"
    if result["matched"]:
        paths = list(dict.fromkeys(m["path"] for m in result["matches"]))
        path_memory = _register_path_memory(paths)
        if path_memory:
            result["path_memory"] = path_memory
    logger.info("Grep: pattern=%s base=%s", pattern, base)
    return json.dumps(result, ensure_ascii=False)
