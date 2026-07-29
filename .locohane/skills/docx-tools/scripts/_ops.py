"""edit_docx.py が適用する「操作（op）」のディスパッチ実装。

各関数は (doc, op_dict) を受け取り、python-docx の Document へ副作用を
適用する。戻り値は呼び出し元へ報告する結果（dict）または None。

run_script からは直接実行されない。edit_docx.py から import して使う。
"""

from __future__ import annotations

from _blocks import BLOCK_HANDLERS
from _track_changes import (
    DEFAULT_AUTHOR,
    accept_all_changes,
    apply_tracked_replace_to_run,
    is_plain_text_run,
    now_iso,
    reject_all_changes,
)


def _apply_plain_replace_to_run(run, old_text: str, new_text: str, occurrence: str) -> int:
    text = run.text
    if old_text not in text:
        return 0
    count = 1 if occurrence == "first" else text.count(old_text)
    run.text = text.replace(old_text, new_text, 1 if occurrence == "first" else -1)
    return count


def op_find_replace(doc, op: dict) -> dict:
    old_text = op["old_text"]
    if not old_text:
        raise ValueError("old_text は空文字列にできません")
    new_text = op.get("new_text", "")
    track = bool(op.get("track_changes", False))
    occurrence = op.get("occurrence", "all")
    if occurrence not in ("all", "first"):
        raise ValueError(f"occurrence は 'all' または 'first' である必要があります: {occurrence!r}")
    author = op.get("author") or DEFAULT_AUTHOR
    date = op.get("date") or now_iso()

    paragraphs = doc.paragraphs
    if op.get("paragraph_index") is not None:
        idx = int(op["paragraph_index"])
        if not (0 <= idx < len(paragraphs)):
            raise ValueError(f"存在しないparagraph_indexです: {idx}（総段落数: {len(paragraphs)}）")
        targets = [paragraphs[idx]]
    else:
        targets = paragraphs

    replaced = 0
    for para in targets:
        for run in list(para.runs):
            if not is_plain_text_run(run._r):
                continue
            if track:
                n = apply_tracked_replace_to_run(doc, run._r, old_text, new_text, occurrence, author, date)
            else:
                n = _apply_plain_replace_to_run(run, old_text, new_text, occurrence)
            replaced += n
            if occurrence == "first" and replaced:
                break
        if occurrence == "first" and replaced:
            break

    if replaced == 0:
        raise ValueError(
            f"old_text が見つかりませんでした: {old_text!r}"
            "（1つのrun内に収まっている必要があります。書式が混在する文字列や複数runにまたがる文字列、"
            "既存のTrack Changes内の文字列は検出できません）"
        )
    return {"op": "find_replace", "replaced_count": replaced}


def op_append_block(doc, op: dict) -> dict:
    block = op["block"]
    if not isinstance(block, dict):
        raise ValueError("block はオブジェクトである必要があります")
    handler = BLOCK_HANDLERS.get(block.get("type"))
    if handler is None:
        raise ValueError(f"未対応のblock typeです: {block.get('type')!r}（対応: {sorted(BLOCK_HANDLERS)}）")
    handler(doc, block)
    return {"op": "append_block", "block_type": block.get("type")}


def op_delete_paragraph(doc, op: dict) -> dict:
    index = op["index"]
    if op.get("track_changes"):
        raise ValueError(
            "delete_paragraph はtrack_changesに対応していません（段落マーク削除はOOXML上複雑なため非対応です）。"
            "変更履歴として残したい場合は、段落内の全文をfind_replaceでtrack_changes:trueにより"
            "空文字へ置換してください（段落自体は残り、中身の削除のみが変更履歴になります）"
        )
    paragraphs = doc.paragraphs
    idx = int(index)
    if not (0 <= idx < len(paragraphs)):
        raise ValueError(f"存在しないindexです: {index}（総段落数: {len(paragraphs)}）")
    p_element = paragraphs[idx]._p
    p_element.getparent().remove(p_element)
    return {"op": "delete_paragraph", "deleted_index": idx}


def op_accept_all_changes(doc, op: dict) -> dict:
    result = accept_all_changes(doc)
    return {"op": "accept_all_changes", **result}


def op_reject_all_changes(doc, op: dict) -> dict:
    result = reject_all_changes(doc)
    return {"op": "reject_all_changes", **result}


OP_HANDLERS = {
    "find_replace": op_find_replace,
    "append_block": op_append_block,
    "delete_paragraph": op_delete_paragraph,
    "accept_all_changes": op_accept_all_changes,
    "reject_all_changes": op_reject_all_changes,
}


def apply_op(doc, op: dict) -> dict | None:
    op_name = op.get("op")
    handler = OP_HANDLERS.get(op_name)
    if handler is None:
        raise ValueError(f"未対応のopです: {op_name!r}（対応op: {sorted(OP_HANDLERS)}）")
    return handler(doc, op)
