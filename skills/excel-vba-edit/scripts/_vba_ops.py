"""edit_vba.py が適用する「操作（op）」のディスパッチ実装。

各関数は (workbook, vb_project, op_dict) を受け取り、COM経由でVBAプロジェクト
（vb_project = workbook.VBProject）へ副作用を適用する。openpyxl版の _ops.py と
同じ「OP_HANDLERS辞書 + apply_op」の構造だが、COM操作には Workbook と VBProject
の両方が要るためシグネチャが異なり、ファイルも分離している。

run_script からは直接実行されない。edit_vba.py から import して使う。
"""

from __future__ import annotations

import json
import re

# 既存データ・ファイルの破壊やシステムへの不正アクセスにほぼ確実に使われる
# VBAの低レベルAPI呼び出しを検出するためのパターン（大文字小文字を区別しない）。
# 正当な用途（一時ファイルの削除等）であっても、このスキル経由での生成は
# 一律禁止する（フォールバックのバックアップ保存では救えない種類の危険な
# 操作のため）。FileCopy はバックアップ作成に必要なため意図的に除外している。
_DANGEROUS_CODE_PATTERNS = [
    (re.compile(r"\bKill\b\s*\S", re.IGNORECASE), "Kill（ファイル削除）"),
    (re.compile(r"\bRmDir\b", re.IGNORECASE), "RmDir（フォルダ削除）"),
    (re.compile(r"\bShell\b\s*\S", re.IGNORECASE), "Shell（外部プロセス実行）"),
    (re.compile(r"wscript\.shell", re.IGNORECASE), "WScript.Shell（外部コマンド実行）"),
    (re.compile(r"\.DeleteFile\b", re.IGNORECASE), "DeleteFile（ファイル削除）"),
    (re.compile(r"\.DeleteFolder\b", re.IGNORECASE), "DeleteFolder（フォルダ削除）"),
]


def _check_dangerous_code(code: str) -> None:
    """既存データの破壊やシステムへの不正アクセスに使われる危険なVBA APIを
    検出したら書き込み自体を拒否する。

    完全な悪意判定はできない（技術的に不可能）ため、正当な理由が思いつき
    にくい低レベルAPI呼び出しのみを対象にした簡易ガード。バックアップ保存の
    フォールバックでは救えない種類の操作（ファイル/フォルダ削除、外部コマンド
    実行）に限定し、Save/Delete（ワークシート・セル等）のような正当な用途が
    多い操作は対象外にしている（そちらはSKILL.mdのポリシーで「事前にバック
    アップを取ってから実行する」ことを別途要求する）。
    """
    if not code:
        return
    for pattern, label in _DANGEROUS_CODE_PATTERNS:
        if pattern.search(code):
            raise ValueError(
                f"危険なVBA API（{label}）が含まれているため、このコードは書き込めません。"
                "ファイル・フォルダの削除や外部コマンド実行を伴うコードはこのスキルでは"
                "生成できません。ユーザーから明確な許可と目的を確認した上で、Excel上で"
                "手動により対応するようユーザーに伝えてください。"
            )


# --- 簡易構文lint ---
# VBAオブジェクトモデルには「コンパイル成否を返すAPI」が存在せず、COM経由での
# 実コンパイルチェック（ダミーSub実行、CommandBars経由のCompile実行、保存後の
# 再オープン等）はいずれも実効性が確認できなかったため、機械的に判定できる
# 範囲（Sub/End Sub、If/End If等のブロック構文の対応関係）だけを正規表現で
# 検証する。取りこぼし（#If等の条件付きコンパイル、1行に複数ステートメントを
# 並べるコロン複文、未宣言変数の重複宣言や型不一致等の意味的エラー）はあるが、
# 正しいコードを誤って拒否する方向のリスクを避けることを優先した設計。
_STRING_LITERAL_RE = re.compile(r'"(?:[^"]|"")*"')
_LINE_CONTINUATION_RE = re.compile(r"[ \t]_[ \t]*$")

_BLOCK_OPENERS = [
    (re.compile(r"^(?:Public\s+|Private\s+|Friend\s+|Static\s+)*Sub\s+\w", re.IGNORECASE), "Sub", "End Sub"),
    (re.compile(r"^(?:Public\s+|Private\s+|Friend\s+|Static\s+)*Function\s+\w", re.IGNORECASE), "Function", "End Function"),
    (re.compile(r"^(?:Public\s+|Private\s+|Friend\s+)*Property\s+(?:Get|Let|Set)\s+\w", re.IGNORECASE), "Property", "End Property"),
    (re.compile(r"^If\b.*\bThen\s*$", re.IGNORECASE), "If", "End If"),
    (re.compile(r"^For\b", re.IGNORECASE), "For", "Next"),
    (re.compile(r"^Do\b", re.IGNORECASE), "Do", "Loop"),
    (re.compile(r"^With\b", re.IGNORECASE), "With", "End With"),
    (re.compile(r"^Select\s+Case\b", re.IGNORECASE), "SelectCase", "End Select"),
]

_TAG_DISPLAY_NAMES = {
    "Sub": "Sub",
    "Function": "Function",
    "Property": "Property",
    "If": "If",
    "For": "For",
    "Do": "Do",
    "With": "With",
    "SelectCase": "Select Case",
}

_BLOCK_CLOSERS = [
    (re.compile(r"^End\s+Sub\b", re.IGNORECASE), "Sub", "End Sub"),
    (re.compile(r"^End\s+Function\b", re.IGNORECASE), "Function", "End Function"),
    (re.compile(r"^End\s+Property\b", re.IGNORECASE), "Property", "End Property"),
    (re.compile(r"^End\s+If\b", re.IGNORECASE), "If", "End If"),
    (re.compile(r"^Next\b", re.IGNORECASE), "For", "Next"),
    (re.compile(r"^Loop\b", re.IGNORECASE), "Do", "Loop"),
    (re.compile(r"^End\s+With\b", re.IGNORECASE), "With", "End With"),
    (re.compile(r"^End\s+Select\b", re.IGNORECASE), "SelectCase", "End Select"),
]


def _sanitize_line(line: str) -> str:
    """1物理行から文字列リテラル・コメント・行継続記号を取り除いた判定用テキストを返す。

    VBAの文字列リテラルは物理行をまたげない言語仕様のため、行単位の除去で
    取りこぼしがない。
    """
    if re.match(r"^\s*rem\b", line, re.IGNORECASE):
        return ""
    text = _STRING_LITERAL_RE.sub(lambda m: " " * len(m.group()), line)
    comment_idx = text.find("'")
    if comment_idx != -1:
        text = text[:comment_idx]
    return _LINE_CONTINUATION_RE.sub("", text)


def _iter_logical_lines(code: str):
    """行継続（末尾" _"）で連結された論理行を (開始物理行番号, 判定用テキスト) で列挙する。"""
    physical_lines = code.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    buffer_parts: list[str] = []
    start_no = None
    for i, raw_line in enumerate(physical_lines, start=1):
        if start_no is None:
            start_no = i
        buffer_parts.append(_sanitize_line(raw_line))
        if _LINE_CONTINUATION_RE.search(raw_line):
            continue
        yield start_no, " ".join(buffer_parts).strip()
        buffer_parts = []
        start_no = None
    if buffer_parts:
        yield start_no, " ".join(buffer_parts).strip()


def _syntax_error_message(detail: str) -> str:
    return (
        f"VBAコードの構文が不正な可能性があります（{detail}）。"
        "Sub/End Sub、If/End If、For/Next等のブロックの対応が崩れていないか、"
        "read_vba.pyで書き込み予定のコード全体を見直してから再送してください。"
        "なお、このチェックはブロック構文の対応関係のみを見る簡易的なものです"
        "（#If等の条件付きコンパイル、1行に複数ステートメントをコロンで並べる"
        "書き方は対象外です）。"
    )


def _lint_vba_syntax(code: str) -> None:
    """Sub/End Sub、If/End If等のブロック構文の対応関係だけを検証する簡易lint。

    実コンパイルチェック相当の検証はCOM経由では実現できなかったため、正規表現で
    機械的に判定できる範囲（ブロック構文の対応関係）に留める。
    """
    if not code:
        return
    stack: list[tuple[str, str, int]] = []
    for line_no, text in _iter_logical_lines(code):
        if not text:
            continue
        closer = next((c for c in _BLOCK_CLOSERS if c[0].search(text)), None)
        if closer is not None:
            _, expected_tag, closer_label = closer
            if not stack:
                raise ValueError(_syntax_error_message(
                    f"{line_no}行目付近に対応する開始ブロックのない'{closer_label}'があります"
                ))
            top_tag, _, top_line = stack[-1]
            if top_tag != expected_tag:
                raise ValueError(_syntax_error_message(
                    f"{top_line}行目付近で開始した'{_TAG_DISPLAY_NAMES[top_tag]}'ブロックが"
                    f"閉じられないまま、{line_no}行目付近に'{closer_label}'が現れました"
                ))
            stack.pop()
            continue
        opener = next((o for o in _BLOCK_OPENERS if o[0].search(text)), None)
        if opener is not None:
            _, tag, closer_label = opener
            stack.append((tag, closer_label, line_no))

    if stack:
        top_tag, _, top_line = stack[-1]
        raise ValueError(_syntax_error_message(
            f"{top_line}行目付近で開始した'{_TAG_DISPLAY_NAMES[top_tag]}'ブロックに対応する"
            "終了キーワードが見つかりません"
        ))


def _project_replacement(code_module, start_line: int, count_lines: int, new_code: str) -> str:
    """既存コードの[start_line, start_line+count_lines)をnew_codeに置き換えた
    場合の全文を、実際には書き込まずに文字列として組み立てる（lint用）。
    """
    total_lines = code_module.CountOfLines
    full_code = code_module.Lines(1, total_lines) if total_lines else ""
    full_lines = full_code.split("\r\n") if full_code else []
    before = full_lines[: start_line - 1]
    after = full_lines[start_line - 1 + count_lines :]
    new_lines = new_code.split("\n") if new_code else [""]
    return "\r\n".join(before + new_lines + after)


# vbext_ComponentType（遅延バインディングのため定数名でなく整数値を直書きする）
_VBEXT_CT_STD_MODULE = 1
_VBEXT_CT_CLASS_MODULE = 2
_VBEXT_CT_MSFORM = 3
_VBEXT_CT_DOCUMENT = 100

_ADD_MODULE_TYPES = {
    "standard": _VBEXT_CT_STD_MODULE,
    "class": _VBEXT_CT_CLASS_MODULE,
}


def _find_component(vb_project, name: str):
    for comp in vb_project.VBComponents:
        if comp.Name == name:
            return comp
    names = [comp.Name for comp in vb_project.VBComponents]
    raise KeyError(f"モジュールが見つかりません: {name!r}（存在するモジュール: {names}）")


def _replace_code(code_module, new_code: str) -> None:
    """CodeModuleの中身を新しいコードで全文置換する。

    AddFromStringは既存コードがある状態で呼ぶと末尾に追記されてしまうため、
    全文置換には使えない。既存行を全削除してから新しいコードを流し込む。
    """
    line_count = code_module.CountOfLines
    if line_count > 0:
        code_module.DeleteLines(1, line_count)
    if new_code:
        code_module.AddFromString(new_code)


def _to_json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def op_add_module(workbook, vb_project, op: dict):
    name = op["name"]
    mtype = op.get("type", "standard")
    if mtype not in _ADD_MODULE_TYPES:
        raise ValueError(
            f"typeはstandard/classのみ対応です（documentは既存モジュールへのset_codeを、"
            f"formはこのスキルの対象外です）: {mtype!r}"
        )
    if any(comp.Name == name for comp in vb_project.VBComponents):
        raise ValueError(f"モジュール'{name}'は既に存在します（set_codeで上書きするかdelete_moduleで削除してください）")
    code = op.get("code", "")
    _check_dangerous_code(code)
    _lint_vba_syntax(code)
    comp = vb_project.VBComponents.Add(_ADD_MODULE_TYPES[mtype])
    comp.Name = name
    _replace_code(comp.CodeModule, code)
    return None


def op_set_code(workbook, vb_project, op: dict):
    comp = _find_component(vb_project, op["name"])
    if comp.Type == _VBEXT_CT_MSFORM:
        raise ValueError(f"'{op['name']}'はUserForm（フォームモジュール）のためこのスキルでは編集できません")
    _check_dangerous_code(op["code"])
    _lint_vba_syntax(op["code"])
    _replace_code(comp.CodeModule, op["code"])
    return None


def op_find_replace(workbook, vb_project, op: dict):
    """モジュール内の一部コード（old_code）をnew_codeへ置換する。

    set_code と違いモジュール全文を渡す必要がなく、変更したい断片だけを
    やり取りするため、長いモジュールを低パラメータのLLMが扱う際の取りこぼし・
    誤生成のリスクを避けられる。old_codeがモジュール内で一意に一致しない
    （0件・複数件）場合はエラーにする（Editツールのold_string/new_stringと
    同じ考え方）。改行コードの違い（CRLF/LF）は吸収する。
    """
    comp = _find_component(vb_project, op["name"])
    if comp.Type == _VBEXT_CT_MSFORM:
        raise ValueError(f"'{op['name']}'はUserForm（フォームモジュール）のためこのスキルでは編集できません")
    code_module = comp.CodeModule
    full_code = code_module.Lines(1, code_module.CountOfLines) if code_module.CountOfLines else ""
    full_normalized = full_code.replace("\r\n", "\n")
    old_normalized = str(op["old_code"]).replace("\r\n", "\n")
    if not old_normalized:
        raise ValueError("old_codeは空文字列にできません")
    count = full_normalized.count(old_normalized)
    if count == 0:
        raise ValueError(
            f"old_codeがモジュール'{op['name']}'内に見つかりません"
            "（read_vba.pyで取得した実際のコードから、インデントや改行も含めて正確にコピーしてください）"
        )
    if count > 1:
        raise ValueError(
            f"old_codeがモジュール'{op['name']}'内に{count}箇所一致しました。"
            "一意に特定できるまで前後の行を含めて範囲を広げて指定してください"
        )
    new_normalized = str(op["new_code"]).replace("\r\n", "\n")
    _check_dangerous_code(new_normalized)
    new_full_code = full_normalized.replace(old_normalized, new_normalized, 1)
    _lint_vba_syntax(new_full_code)
    _replace_code(code_module, new_full_code)
    return None


# vbext_ProcKind（Property Get/Let/Set の判別に使う。標準Sub/Functionはpk_Proc）
_PROC_KIND_MAP = {
    "sub": 0,  # vbext_pk_Proc
    "property_let": 1,  # vbext_pk_Let
    "property_set": 2,  # vbext_pk_Set
    "property_get": 3,  # vbext_pk_Get
}
_PROC_KIND_AUTO_ORDER = (0, 3, 1, 2)


def op_replace_procedure(workbook, vb_project, op: dict):
    """モジュール内の指定したSub/Function/Propertyプロシージャ1つだけを丸ごと
    置き換える。COMのCodeModule.ProcStartLine/ProcCountLinesでプロシージャの
    正確な開始行・行数を取得してから、その範囲だけをDeleteLines+InsertLinesで
    差し替えるため、モジュールの他の部分には一切触れない。set_codeよりも
    LLMが用意するコード量を大きく減らせる。
    """
    comp = _find_component(vb_project, op["name"])
    if comp.Type == _VBEXT_CT_MSFORM:
        raise ValueError(f"'{op['name']}'はUserForm（フォームモジュール）のためこのスキルでは編集できません")
    code_module = comp.CodeModule
    proc_name = op["procedure"]

    kind_arg = op.get("kind")
    if kind_arg is not None:
        if kind_arg not in _PROC_KIND_MAP:
            raise ValueError(f"kindはsub/property_get/property_let/property_setのみ対応です: {kind_arg!r}")
        kinds_to_try = (_PROC_KIND_MAP[kind_arg],)
    else:
        kinds_to_try = _PROC_KIND_AUTO_ORDER

    start_line = None
    count_lines = None
    for kind in kinds_to_try:
        try:
            start_line = code_module.ProcStartLine(proc_name, kind)
            count_lines = code_module.ProcCountLines(proc_name, kind)
            break
        except Exception:
            continue
    if start_line is None:
        raise ValueError(
            f"プロシージャ'{proc_name}'がモジュール'{op['name']}'内に見つかりません"
            "（Property Get/Let/Setの場合はkindで明示的に指定してください）"
        )
    _check_dangerous_code(op["code"])
    assembled = _project_replacement(code_module, start_line, count_lines, op["code"])
    _lint_vba_syntax(assembled)
    code_module.DeleteLines(start_line, count_lines)
    code_module.InsertLines(start_line, op["code"])
    return None


def op_insert_code(workbook, vb_project, op: dict):
    """モジュールの既存コードには触れず、新しいコード（通常は新規のSub/Function）
    を末尾・先頭・指定行のいずれかに追加する。既存コードを一切やり取りしなくて
    よいため、モジュールへ新しいプロシージャを1つ追加したいだけの場合に最も
    LLMの負担が小さい。
    """
    comp = _find_component(vb_project, op["name"])
    if comp.Type == _VBEXT_CT_MSFORM:
        raise ValueError(f"'{op['name']}'はUserForm（フォームモジュール）のためこのスキルでは編集できません")
    code_module = comp.CodeModule
    position = op.get("position", "end")
    if position == "end":
        line = code_module.CountOfLines + 1
    elif position == "start":
        line = 1
    elif isinstance(position, int) and position >= 1:
        line = position
    else:
        raise ValueError(f"positionは'end'/'start'または1以上の行番号（整数）である必要があります: {position!r}")
    _check_dangerous_code(op["code"])
    assembled = _project_replacement(code_module, line, 0, op["code"])
    _lint_vba_syntax(assembled)
    code_module.InsertLines(line, op["code"])
    return None


def op_delete_module(workbook, vb_project, op: dict):
    comp = _find_component(vb_project, op["name"])
    if comp.Type == _VBEXT_CT_DOCUMENT:
        raise ValueError(
            f"'{op['name']}'はドキュメントモジュール（ThisWorkbook/シートモジュール）のため削除できません"
            "（コードを空にしたい場合はset_codeで空文字列を渡してください）"
        )
    vb_project.VBComponents.Remove(comp)
    return None


def op_run_macro(workbook, vb_project, op: dict):
    name = op["name"]
    call_args = op.get("args", [])
    excel = workbook.Application
    try:
        result = excel.Run(f"'{workbook.Name}'!{name}", *call_args)
    except Exception as e:  # noqa: BLE001 - COMエラーはマクロ側のエラー内容により多様なため丸めて報告する
        raise ValueError(f"マクロ'{name}'の実行に失敗しました: {e}")
    return _to_json_safe(result)


OP_HANDLERS = {
    "add_module": op_add_module,
    "set_code": op_set_code,
    "find_replace": op_find_replace,
    "replace_procedure": op_replace_procedure,
    "insert_code": op_insert_code,
    "delete_module": op_delete_module,
    "run_macro": op_run_macro,
}


def apply_op(workbook, vb_project, op: dict):
    op_name = op.get("op")
    handler = OP_HANDLERS.get(op_name)
    if handler is None:
        raise ValueError(f"未対応のopです: {op_name!r}（対応op: {sorted(OP_HANDLERS)}）")
    return handler(workbook, vb_project, op)
