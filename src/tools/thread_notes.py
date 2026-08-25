"""write_thread_note/list_thread_notes/read_thread_note ツール群。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from langchain_core.tools import tool
from pathlib import Path
import re

from ._script_job import _JOB_OUTPUT_TAIL_CHARS
from ._state import _IN_SUBAGENT, _SUBAGENT_AGENT_TYPE
from ._workdir import _resolve_exec_workdir


def _thread_notes_path() -> Path:
    """write_thread_note/list_thread_notes/read_thread_note が使う、
    スレッド全体で共有されるノートファイルの絶対パスを決める。

    _scratch_notes_path() と異なり run_id ではなく thread_id のみで決まるため、
    同一スレッド内の全 dispatch_agent 実行（サブエージェントごとに変わる
    run_id）とメインエージェント自身が同じファイルを共有できる。
    """
    workdir = _resolve_exec_workdir()
    return workdir / "_thread_notes.md"


_THREAD_NOTE_BLOCK_RE = re.compile(r"^## (.+)\n<!-- (.+?) by (.+?) -->\n", re.MULTILINE)


def _thread_note_author() -> str:
    """現在の呼び出し元を表す短い文字列（メインエージェントなら"main"、
    サブエージェントなら agent_type）。write_thread_note が書き込むメタ情報、
    list_thread_notes の一覧表示に使う。
    """
    if _IN_SUBAGENT.get():
        return _SUBAGENT_AGENT_TYPE.get() or "subagent"
    return "main"


@dataclass
class _ThreadNoteBlock:
    topic: str
    timestamp: str
    author: str
    content: str


def _parse_thread_notes(text: str) -> list["_ThreadNoteBlock"]:
    """_thread_notes_path() の内容を `## topic` 単位のブロックへ分割する。

    write_thread_note は常に追記のみで既存ブロックを書き換えないため、
    同じ topic 名のブロックが複数存在しうる（list_thread_notes /
    read_thread_note 側で合算・連結して1つの topic として扱う）。
    """
    blocks: list[_ThreadNoteBlock] = []
    matches = list(_THREAD_NOTE_BLOCK_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(
            _ThreadNoteBlock(
                topic=m.group(1).strip(),
                timestamp=m.group(2).strip(),
                author=m.group(3).strip(),
                content=text[start:end].rstrip("\n"),
            )
        )
    return blocks


@tool
def write_thread_note(topic: str, content: str) -> str:
    """調査で分かった詳細を、スレッド全体で共有されるノートへ書き込む。

    write_scratch_note と違い、このファイルは同一スレッド内であれば
    メインエージェント・どのサブエージェント（agent_type）からも
    list_thread_notes / read_thread_note で読める（run_id には縛られない）。
    委譲元への最終回答を要約に留めたい場合、根拠となる具体的な事実
    （値・件数・該当箇所等）はここに書き、最終回答では topic 名を参照する形にする。

    生データの丸写しではなく、抽出済みの事実を凝縮して書くこと
    （このノート自体が肥大化すると、後で読む側のコンテキストを圧迫する）。
    同じ topic で複数回呼べば追記されていく（既存内容は上書きされない）。

    Args:
        topic: この書き込みの見出し。以後 list_thread_notes / read_thread_note
            から同じ文字列で参照する（表記ゆれがあると別トピック扱いになるので、
            既存トピックへ追記したい場合は list_thread_notes で正確な表記を確認する）。
        content: 書き込む内容（Markdown等の自由形式）。

    Returns:
        書き込み先の絶対パスと、この topic の累計文字数・ファイル全体の文字数。
        topic/content が空、または書き込みに失敗した場合は「エラー: ...」形式で返す。
    """
    topic_clean = topic.strip()
    if not topic_clean:
        return "エラー: topic が空です。"
    if not content.strip():
        return "エラー: content が空です。"
    path = _thread_notes_path()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    author = _thread_note_author()
    block = f"## {topic_clean}\n<!-- {timestamp} by {author} -->\n{content.rstrip()}\n\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        full_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"エラー: thread note への書き込みに失敗しました: {e}"
    topic_chars = sum(b.content.__len__() for b in _parse_thread_notes(full_text) if b.topic == topic_clean)
    return (
        f'書き込みました: topic="{topic_clean}"（このtopic累計 {topic_chars} 文字、'
        f"ファイル全体 {len(full_text)} 文字）。パス: {path}"
    )


def _format_thread_notes_listing(path: Path) -> str | None:
    """thread note ファイルをトピック一覧のテキストへ整形する。

    list_thread_notes（ツール）と thread_note_status_text（圧縮再注入）の
    両方が同じ整形結果を共有するための single source of truth。

    Returns:
        トピックごとの「文字数・書き込み件数・最終更新日時と書き込み者」の
        一覧テキスト。ファイルが無い、または有効なトピックが無ければ None。
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_thread_notes(text)
    if not blocks:
        return None
    grouped: dict[str, list[_ThreadNoteBlock]] = {}
    for b in blocks:
        grouped.setdefault(b.topic, []).append(b)
    lines = [f"thread note のトピック一覧（{len(grouped)}件、ファイル全体 {len(text)} 文字）:"]
    for topic, items in grouped.items():
        total_chars = sum(len(b.content) for b in items)
        last = items[-1]
        lines.append(
            f'- "{topic}": {total_chars}文字（{len(items)}件の書き込み、最終更新 {last.timestamp} by {last.author}）'
        )
    return "\n".join(lines)


@tool
def list_thread_notes() -> str:
    """スレッド全体で共有されるノート（write_thread_note の書き込み先）の
    トピック一覧を、本文抜きで返す。

    本文を読む前に必ずこれを呼び、対象トピックの文字数を確認すること。
    ノートはスレッドが進むほど肥大化するため、read_thread_note でいきなり
    本文を読むとコンテキストを圧迫しうる。文字数が大きいトピックは、
    read_thread_note が案内する通り dispatch_agent での分析委譲を検討する。

    Returns:
        トピックごとの「文字数・書き込み件数・最終更新日時と書き込み者
        （main または agent_type）」の一覧。ノートがまだ無ければその旨を返す。
    """
    path = _thread_notes_path()
    listing = _format_thread_notes_listing(path)
    if listing is not None:
        return listing
    if not path.is_file():
        return "thread note はまだありません。write_thread_note で最初のトピックを書き込めます。"
    return "thread note ファイルは存在しますが、有効なトピックがまだありません。"


def thread_note_status_text() -> str:
    """現在の thread note のトピック一覧を、圧縮再注入（context_compaction.py）
    向けに機械的に整形する。

    current_plan_status_text と同じ理由（要約LLMは write_thread_note の
    tool_calls 引数・ToolMessage を見て要約に含めるかどうかを自分で判断
    しており、含め忘れると圧縮後にthread noteの存在自体が失われる）で、
    要約LLMの出力とは無関係に summary へ無条件で追記するために使う。

    thread note が無い、またはchainlitセッション文脈外（テスト・evals等で
    cl.user_session.get() が ChainlitContextException を送出する場合）は
    空文字列を返す。
    """
    try:
        path = _thread_notes_path()
        listing = _format_thread_notes_listing(path)
    except Exception:
        return ""
    return listing or ""


@tool
def read_thread_note(topic: str) -> str:
    """スレッド全体で共有されるノートから、指定した topic の内容だけを読む。

    呼ぶ前に必ず list_thread_notes で対象トピックの文字数を確認すること。
    このtopicの合計文字数が大きい場合、この関数は先頭・末尾のみを返し
    中略する（このツール自身の出力でコンテキストを圧迫しないため）。
    全量の精読・分析が必要な場合は、切り詰め時に返る案内の通り
    dispatch_agent で worker または explore へ、このファイルパスと
    topic名を指定して分析を委譲すること（サブエージェントの Read は
    メインエージェントのようなツール呼び出し回数ガードの対象外のため、
    このファイルを直接 Read して全文を精読できる）。

    Args:
        topic: list_thread_notes に表示された、正確なトピック名。

    Returns:
        topicの内容（大きい場合は先頭・末尾のみ＋案内）。ノートが無い、
        またはtopicが見つからない場合は「エラー: ...」形式で返す。
    """
    path = _thread_notes_path()
    if not path.is_file():
        return "エラー: thread note はまだありません。"
    text = path.read_text(encoding="utf-8", errors="replace")
    topic_clean = topic.strip()
    blocks = [b for b in _parse_thread_notes(text) if b.topic == topic_clean]
    if not blocks:
        return f'エラー: topic "{topic_clean}" は見つかりません。list_thread_notes で確認してください。'
    merged = "\n\n".join(f"[{b.timestamp} by {b.author}]\n{b.content}" for b in blocks)
    if len(merged) <= _JOB_OUTPUT_TAIL_CHARS:
        return merged
    half = _JOB_OUTPUT_TAIL_CHARS // 2
    head = merged[:half]
    tail = merged[-half:]
    return (
        f"{head}\n\n"
        f'...(中略: topic "{topic_clean}" は合計 {len(merged)} 文字あり、先頭・末尾のみ表示)...\n\n'
        f"{tail}\n\n"
        f'[全量の精読・分析が必要な場合は dispatch_agent で worker または explore へ、'
        f'「{path} の "{topic_clean}" セクションをReadして分析・要約せよ」という形で委譲してください。]'
    )
