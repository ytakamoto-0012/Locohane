"""skills ディレクトリ配下へのパス解決・file tools 系のパス解決ヘルパー。"""

from __future__ import annotations

from pathlib import Path
import os

from . import _state
from ._path_memory_helpers import _resolve_path_memory_token
from ._workdir import _foreign_tmp_dir_error, _resolve_workdir


def _resolve_file_tools_path(raw: str) -> tuple[Path | None, str | None]:
    """Read/Glob/Grep/json_query の path 系引数を解決する。

    `@N` 形式のパスメモリー参照を解決したのち、絶対パスはそのまま、
    相対パス（空文字含む）は作業ディレクトリ基準（_resolve_workdir()）で
    解決する。旧 run_script はサブプロセスの cwd=workdir により暗黙に
    作業ディレクトリ基準になっていたため、ネイティブツール化後もこの挙動を
    明示的に再現する（Path.cwd() 等プロセス自身のcwdは使わない）。

    Args:
        raw: file_path/path 引数の生値（空文字・相対パス・絶対パス・`@N`）。

    Returns:
        (解決済み絶対パス, エラーメッセージ) のタプル。`@N` が未登録等で
        解決できない場合は (None, エラー文字列)。
    """
    resolved, error = _resolve_path_memory_token(raw) if raw else (raw, None)
    if error:
        return None, error
    p = Path(resolved) if resolved else _resolve_workdir()
    if not p.is_absolute():
        p = _resolve_workdir() / p
    tmp_error = _foreign_tmp_dir_error(p)
    if tmp_error:
        return None, tmp_error
    return p, None

def _safe_path(relative: str) -> Path:
    """skills ルート配下に限定した絶対パスを返す。境界外なら ValueError。

    ディレクトリトラバーサル対策の中核。relative に ".." やシンボリック
    リンク経由の脱出が含まれていても、resolve() で正規化した上で
    is_relative_to() により境界を検証するため、skills ルート外への
    アクセスは常に拒否される。

    _state._SKILLS_ROOTS は複数ディレクトリを保持しうる（例: [*locohane_skills_dirs,
    skills_dir]）。前方から順に候補を解決し、実在する最初の候補を返す。
    どのルートにも実在しない場合は先頭ルート基準の候補を返す（呼び出し側の
    「見つかりません」エラーへ自然に流すため）。

    Args:
        relative: skills ルートからの相対パス（例: "word-counter/SKILL.md"）。

    Returns:
        skills ルート配下に解決された絶対パス（Path）。

    Raises:
        RuntimeError: init_tools() が未実行で _state._SKILLS_ROOTS が空の場合。
        ValueError: 解決後のパスがいずれかの skills ルート配下に収まらない
            場合（ディレクトリトラバーサルの試行とみなす）。
    """
    if not _state._SKILLS_ROOTS:
        raise RuntimeError("init_tools() が未実行です")
    candidates: list[Path] = []
    for root in _state._SKILLS_ROOTS:
        # resolve() でシンボリックリンクや .. を正規化した上で境界を検証する。
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"skills ディレクトリ外へのアクセスは許可されません: {relative}")
        candidates.append(candidate)
        if candidate.exists():
            return candidate
    return candidates[0]


def _missing_skill_prefix_hint(relative_path: str) -> str:
    """read_skill_file が見つからなかった際、スキル名プレフィックス漏れを疑うヒントを返す。

    read_skill(skill_name) を呼んだ直後のサブエージェントが、以後のパスを
    「そのスキルフォルダの中にいる」前提で書いてしまい、relative_path の
    先頭にスキルフォルダ名を付け忘れる（例: "word-counter/references/notes.md"
    のつもりで "references/notes.md" とだけ渡す）誤りが疑われるため追加した
    ヒント。relative_path の先頭セグメントがどの skills ルートにも
    ディレクトリとして存在しない場合にのみヒントを返す。

    Args:
        relative_path: read_skill_file に渡された相対パス。

    Returns:
        スキル名プレフィックス漏れが疑われる場合のヒント文字列。
        該当しない場合は空文字列。
    """
    first_segment = relative_path.replace("\\", "/").split("/", 1)[0]
    if not first_segment:
        return ""
    for root in _state._SKILLS_ROOTS or []:
        if (root / first_segment).is_dir():
            return ""
    return (
        f"（先頭 '{first_segment}' がスキルフォルダ名になっていない可能性があります。"
        "read_skill_file の relative_path は常にスキルルートからの相対パスで、"
        "スキルフォルダ名自体を先頭に含める必要があります。"
        "例: word-counter/references/notes.md）"
    )

def _resolve_script_filename(skill_name: str, script_filename: str) -> Path:
    """スキルの scripts/ 配下からファイル名でスクリプトを探し、絶対パスを返す。

    get_tool_source / run_script が共有する解決ロジック。
    呼び出し側にディレクトリ構成（scripts/ 配下という規約）を書かせず、
    ファイル名のみで指定できるようにする。低パラメータモデルが「scripts/」
    という文字列と引数名を混同して壊れた値（例: "scripts=read_file.py"）を
    生成する誤動作を避けるための設計（ドキュメント側の引数名も script_filename
    にリネーム済み）。

    Args:
        skill_name: スクリプトを持つスキルのフォルダ名。
        script_filename: 探したいファイル名（例: "read_file.py"）。ディレクトリ
            区切りを含む値（旧形式の "scripts/read_file.py" 等）が渡されても
            os.path.basename() でファイル名部分のみを取り出すため動作する。

    Returns:
        解決済みの絶対パス（Path）。

    Raises:
        ValueError: skill_name に scripts/ ディレクトリが無い場合、
            該当ファイルが見つからない場合。
    """
    basename = os.path.basename(script_filename)
    if not basename:
        raise ValueError(f"スクリプトのファイル名を指定してください: {script_filename!r}")
    scripts_root = _safe_path(f"{skill_name}/scripts")
    if not scripts_root.is_dir():
        raise ValueError(f"スキル '{skill_name}' に scripts/ ディレクトリがありません")
    matches = [p for p in scripts_root.rglob(basename) if p.is_file()]
    if not matches:
        raise ValueError(f"スクリプトが見つかりません: {basename}（skill={skill_name}）")
    matches.sort(key=lambda p: (len(p.relative_to(scripts_root).parts), str(p)))
    return matches[0]
