"""ops（操作）のリストを適用してxlsmのVBAマクロコードを追加・上書き・削除・実行する。

excel-vba-edit スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script から
    python edit_vba.py <path> --ops-json "<JSON配列>" [--new] [--overwrite] [--output <別名保存先>]
または
    python edit_vba.py <path> --ops-file <JSONファイルのパス> [--new] [--overwrite] [--output ...]
の形で呼ばれる。

VBAプロジェクトオブジェクトモデルへの操作にはExcel本体をCOM経由（pywin32）で
実際に起動する必要がある（recalc_excel.pyと同じ理由・同じライフサイクル管理:
pythoncom.CoInitialize -> DispatchEx -> try/finallyでClose/Quit/CoUninitialize）。

前提条件（SKILL.mdにも記載）:
- ローカルにMicrosoft Excelがインストールされ、対話セッションから呼び出されて
  いる必要がある。
- Excelのトラストセンターで「VBA プロジェクト オブジェクト モデルへのアクセスを
  信頼する」が有効になっている必要がある（既定は無効。プログラムからは有効化
  できないユーザー側の手動設定）。未設定の場合は workbook.VBProject へのアクセス
  自体がCOMエラーになるため、分かりやすい日本語メッセージに変換して案内する。
- 対象は .xlsm のみ（.xls/.xlsxは非対応。UserFormの作成・編集も対象外）。

ops の各要素の形式や対応opの一覧はSKILL.mdを参照。実装（各opの処理）は
scripts/_vba_ops.py の OP_HANDLERS を参照。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# office_shared/excel_common.py から共有ヘルパーを import する（1-B 相互import方式）
_OFFICE_SHARED = Path(__file__).resolve().parent.parent.parent / "office_shared"
if str(_OFFICE_SHARED) not in sys.path:
    sys.path.append(str(_OFFICE_SHARED))
from excel_common import (  # noqa: E402
    backup_before_overwrite,
    record_excel_pid,
    register_output_path,
    release_excel_pid,
    setup_utf8_stdio,
    terminate_tracked_processes,
    wait_for_process_exit,
)
from _vba_ops import apply_op  # noqa: E402

_XL_OPEN_XML_WORKBOOK_MACRO_ENABLED = 52


def _load_json_arg(args: argparse.Namespace) -> str:
    if args.ops_json is not None:
        return args.ops_json
    data_path = Path(args.ops_file)
    if not data_path.is_file():
        raise FileNotFoundError(f"opsファイルが見つかりません: {args.ops_file}")
    try:
        return data_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return data_path.read_text(encoding="cp932")


def _trust_center_error_message() -> str:
    return (
        "VBAプロジェクトへアクセスできませんでした。Excelのトラストセンター設定で"
        "「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」が"
        "有効になっていない可能性があります。\n"
        "設定手順: Excelを開く → ファイル → オプション → トラストセンター → "
        "「トラストセンターの設定」→「マクロの設定」→"
        "「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」にチェックを入れる。\n"
        "この設定はセキュリティ上の理由からプログラムからは自動的に有効化できないため、"
        "ユーザー自身での設定が必要です。"
    )


def _edit_vba(path: Path, output_path: Path, ops: list, is_new: bool, overwrite: bool) -> dict:
    import pythoncom
    import win32com.client as win32

    pythoncom.CoInitialize()
    excel = None
    workbook = None
    recorded_pid = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        # run_macroがハングしてrun_scriptの外部タイムアウトでPythonプロセスごと
        # 強制終了されると、下のfinally節が実行されずEXCEL.EXEだけが対象ファイルを
        # 開いたまま残留する。その際に `--recover-locks` で確実に後始末できるよう、
        # 起動直後にPIDを自セッションのサンドボックスへ記録しておく（正常終了時は
        # finallyで取り除く。詳細はSKILL.mdのrun_macro説明を参照）。
        recorded_pid = record_excel_pid(output_path, excel)
        # run_macro を含む場合のみマクロの実行を許可する（msoAutomationSecurityLow=1）。
        # ForceDisable(3)のままだと Application.Run 自体がセキュリティ設定によりブロック
        # されるため。run_macro を含まない場合（add_module/set_code/delete_module のみ）は
        # ForceDisable のままにし、Workbook_Open 等の自動マクロが意図せず実行されるのを防ぐ。
        has_run_macro = any(isinstance(op, dict) and op.get("op") == "run_macro" for op in ops)
        excel.AutomationSecurity = 1 if has_run_macro else 3

        if is_new:
            if output_path.exists() and not overwrite:
                raise FileExistsError(f"作成先が既に存在します（上書きするには --overwrite を指定）: {output_path}")
            workbook = excel.Workbooks.Add()
        else:
            if not path.is_file():
                raise FileNotFoundError(f"編集対象のファイルが見つかりません（新規作成する場合は --new を指定）: {path}")
            workbook = excel.Workbooks.Open(str(path), UpdateLinks=0, IgnoreReadOnlyRecommended=True)

        try:
            vb_project = workbook.VBProject
        except Exception as e:
            raise ValueError(_trust_center_error_message()) from e

        results = []
        for idx, op in enumerate(ops):
            if not isinstance(op, dict) or "op" not in op:
                raise ValueError(f"ops[{idx}] が不正です（'op'キーを持つオブジェクトである必要があります）: {op!r}")
            try:
                result = apply_op(workbook, vb_project, op)
            except KeyError as e:
                raise ValueError(f"ops[{idx}]（op={op.get('op')!r}）に必須キー{e}が指定されていません")
            except (ValueError, TypeError) as e:
                raise ValueError(f"ops[{idx}]（op={op.get('op')!r}）の適用に失敗しました: {e}")
            if result is not None:
                results.append(result)

        backup_path = backup_before_overwrite(output_path)

        # 保存直前のmtimeを控えておき、保存後に実際に変化したか確認する。
        # 別のExcelプロセスが対象ファイルをロックしていると、workbook.Save()/
        # SaveAs()が例外を出さずに完了したように見えてもファイルへ実際には
        # 書き込まれないことがある（2026-08-23 issue: applied_ops付きで成功
        # 報告されたのに実体は更新されず、LLMが13分間原因不明のまま迷走した）。
        pre_save_mtime = output_path.stat().st_mtime if output_path.exists() else None

        if is_new or output_path != path:
            workbook.SaveAs(str(output_path), FileFormat=_XL_OPEN_XML_WORKBOOK_MACRO_ENABLED)
        else:
            workbook.Save()

        if pre_save_mtime is not None and output_path.exists() and output_path.stat().st_mtime == pre_save_mtime:
            raise ValueError(
                "保存処理は例外を出さずに完了しましたが、ファイルの更新時刻が変化していません。"
                "別のExcelプロセスが対象ファイルをロックしている可能性があります。"
                "--recover-locks で自セッションが残留させたExcelプロセスを終了できないか確認するか、"
                "tasklistで無関係なEXCEL.EXEが起動していないか確認してください。"
            )

        result = {
            "path": str(output_path),
            "backup_path": str(backup_path) if backup_path else None,
            "applied_ops": len(ops),
            "results": results,
        }
        path_memory = register_output_path(output_path, description="edit_vbaが生成/更新したファイル")
        if path_memory:
            result["path_memory"] = path_memory
        return result
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        # Close()/Quit()が例外を出さなくても実プロセスが終了したとは限らない
        # ため、実終了を確認できた場合のみレジストリから除去する（2026-08-23
        # issue対応。無条件でreleaseしていたため、Quit()が実効を持たなかった
        # ケースでもPID記録だけが消え、以後追跡不能になっていた）。生存して
        # いれば記録を残し、次回 --recover-locks で発見できるようにする。
        if recorded_pid is not None and wait_for_process_exit(recorded_pid):
            release_excel_pid(recorded_pid)
        pythoncom.CoUninitialize()


def main() -> int:
    setup_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=None, help="編集対象（--new時は作成先）のxlsmパス（--recover-locks指定時は不要）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ops-json", help="適用する操作のJSON配列をそのまま渡す")
    group.add_argument("--ops-file", help="適用する操作のJSON配列を書いたファイルのパス")
    parser.add_argument("--new", action="store_true", help="既存ファイルを読まず新規マクロ有効ブックとして作成する")
    parser.add_argument("--overwrite", action="store_true", help="--new時に出力先が既に存在する場合の上書きを許可する")
    parser.add_argument("--output", default=None, help="保存先パス（省略時は path へ上書き保存。.xlsmのみ指定可）")
    parser.add_argument(
        "--recover-locks",
        action="store_true",
        help=(
            "path/opsを指定せず、自セッションが起動して残留したExcelプロセスのうち"
            "まだ生存しているものだけを終了する。PID番号は受け取らない"
            "（自セッションが記録した対象のみが操作対象になる）。"
        ),
    )
    args = parser.parse_args()

    if args.recover_locks:
        recovered = terminate_tracked_processes()
        print(json.dumps({"recovered": recovered}, ensure_ascii=False))
        return 0

    if not args.path:
        print("pathは必須です（--recover-locks指定時を除く）", file=sys.stderr)
        return 1
    # --newのみ（ops無しでVBAプロジェクトを持つ器だけ先に作りたい）は頻出の
    # 正当な使い方のため、--ops-json/--ops-file省略時はops=[]として扱う
    # （--newなし時は編集対象への操作が無いと無意味なので従来通り必須のまま）。
    if args.ops_json is None and args.ops_file is None and not args.new:
        print("--ops-jsonまたは--ops-fileのいずれかが必要です（--new指定時を除く）", file=sys.stderr)
        return 1

    path = Path(args.path)
    ext = path.suffix.lower()
    if ext != ".xlsm":
        print(f"VBAの書き込み・実行は .xlsm のみ対応です: {ext}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else path
    if output_path.suffix.lower() != ".xlsm":
        print(f"保存先の拡張子は .xlsm である必要があります: {output_path.suffix}", file=sys.stderr)
        return 1

    if args.ops_json is None and args.ops_file is None:
        raw = "[]"
    else:
        try:
            raw = _load_json_arg(args)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    try:
        ops = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"opsのJSON解析に失敗しました: {e}", file=sys.stderr)
        return 1
    if not isinstance(ops, list):
        print("opsはJSON配列である必要があります", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = _edit_vba(path, output_path, ops, args.new, args.overwrite)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except ImportError as e:
        print(f"必要なライブラリが見つかりません（pywin32が必要です）: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - COMエラーはExcelのバージョン・状態により多様なため丸めて報告する
        print(f"VBAの編集に失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
