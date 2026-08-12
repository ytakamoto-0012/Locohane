---
name: excel-vba-edit
description: xlsmファイルのVBAマクロコードを追加・上書き・削除し、マクロを実行するスキル。ローカルにMicrosoft Excelが導入され対話セッションから呼ばれている必要があり、Excelトラストセンターで「VBAプロジェクトオブジェクトモデルへのアクセスを信頼する」設定が有効である必要がある。既存データ・ファイルを破壊するコードや悪意あるコード（ファイル削除・外部プロセス実行・レジストリ操作等）は生成しない安全ポリシーがあり、危険APIは機械的に検出・拒否する。ユーザーがVBAマクロのコードを書きたい、既存マクロを修正・削除したい、マクロを実行したいときに使う。既存コードの確認はexcel-vba-readスキルを先に使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-vba-edit

xlsm のVBAマクロコードを追加・上書き・削除し、マクロを実行するスキル。
`edit_vba.py` を `run_script` で実行する。

## VBAコード生成時の制約（絶対厳守）

既存データ・ファイルを破壊するコード、悪意あるコードはいかなる理由でも生成しない。禁止対象: ファイル/フォルダ削除（`Kill`/`RmDir`/`FileSystemObject.DeleteFile`/`DeleteFolder`等）、外部コマンド/プロセス実行（`Shell`/`WScript.Shell`経由の`Run`/`Exec`等）、レジストリ読み書き、他アプリの起動・操作、ネットワーク経由のダウンロード＆実行等ワークブック範囲を超えたシステムアクセス。該当依頼や意図不明な破壊的操作を求められたら目的を確認するか手動対応を提案し、生成しない。危険API検出エラーが出たコードを、目的確認なしにそのまま修正して再送しない。

`add_module`/`set_code`/`find_replace`/`replace_procedure`/`insert_code`は上記のうち`Kill`/`RmDir`/`Shell`/`WScript.Shell`/`DeleteFile`/`DeleteFolder`を含むコードを**技術的に検出してエラー拒否する**（`_check_dangerous_code`。完全な悪意判定はできないため最終判断はLLM自身の責任だが、明らかに危険なAPIは機械的にブロックされる）。

**書き込み直前に簡易構文チェックも行う**（`_lint_vba_syntax`。Sub/End Sub、If/End If、For/Next等のブロック対応を正規表現で検証）。失敗時は書き込み・保存を一切行わずエラーを返す。VBAには「コンパイル成否を返すAPI」が存在しないため、未宣言変数の重複宣言・型不一致・スペルミス等の**意味的**コンパイルエラーはこのチェックでは検出できない（通っても構文的に完全に正しいとは限らない）。

**既存シート・セル・ブックを上書き/更新する処理が正当な目的で必要な場合**（例:「集計結果を書き戻して」「実行のたびに元データを更新したい」）は、**上書き処理の直前に必ず別名バックアップ処理を含める**（`Save`/`Range.Value`書込等は`_check_dangerous_code`では検出されないため、LLMが自主的にこのパターンに従う）。

```vba
Sub UpdateData()
    Dim backupPath As String
    backupPath = ThisWorkbook.Path & "\" & _
        Left(ThisWorkbook.Name, InStrRev(ThisWorkbook.Name, ".") - 1) & _
        "_backup_" & Format(Now, "yyyymmdd_hhnnss") & ".xlsm"
    ThisWorkbook.SaveCopyAs backupPath

    ' ここから既存データを上書きする本処理
End Sub
```
`ThisWorkbook.SaveCopyAs`はブックを閉じずに現在の内容を別名保存でき、実行中マクロの状態に影響しない。バックアップ先は既定で`ThisWorkbook.Path`（ユーザー指定があればそれに従う）。

## 呼び出しと前提条件

```json
{"skill_name": "excel-vba-edit", "script_filename": "edit_vba.py",
 "script_args": ["C:\\Users\\me\\book.xlsm", "--ops-json", "[{\"op\": \"set_code\", \"name\": \"Module1\", \"code\": \"Sub Foo()\\nEnd Sub\"}]"]}
```
- 新規作成: 末尾に`"--new"`（既存ファイルがあれば`"--overwrite"`も必須）。
- `--new`なしは対象パスを開いて編集（不在ならエラー）。
- `--output`省略で対象パスへ上書き保存（出力先拡張子は必ず`.xlsm`）。
- VBAコードは複数行文字列になりやすいため、`--ops-json`直埋め込みより最初から`--ops-file <絶対パス>`（`execute_python_code`でops配列を組み立て`json.dump`で作業ディレクトリ配下の一時ファイルへ書き出し、そのパスを渡す）を基本とする。

### 引数一覧

| 引数 | 必須/任意 | 値の型 | 既定値 | 説明 |
|---|---|---|---|---|
| `path`（位置引数） | 必須 | 文字列（絶対パス） | - | `--new`なし時は編集対象の既存`.xlsm`。`--new`あり時は作成先（`--output`省略時の保存先そのもの） |
| `--ops-json` | `--ops-file`と排他で必須 | 文字列（JSON配列） | - | opsをそのまま1行のJSON文字列で渡す |
| `--ops-file` | `--ops-json`と排他で必須 | 文字列（絶対パス） | - | opsを書いたJSONファイルのパス |
| `--new`（値なしフラグ） | 任意 | - | 付けない＝既存ファイル編集 | 既存ファイルを読まず新規のマクロ有効ブックとして作成する |
| `--overwrite`（値なしフラグ） | 任意 | - | 付けない＝上書き拒否 | `--new`時、保存先（`--output`指定時はそちら、省略時は`path`）に既にファイルがあっても上書きすることを許可する |
| `--output` | 任意 | 文字列（絶対パス、`.xlsm`必須） | 省略＝`path`へ保存 | 別名で保存したいときのみ指定 |

前提条件（実行前に必ずユーザーへ伝える）:
- **ローカルにMicrosoft Excelが導入され対話セッションから呼ばれている必要がある**（excel-recalcスキルと同じ制約）。
- **Excelトラストセンターで「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」が有効である必要がある**（既定無効、プログラムから自動有効化不可）。未設定時は`workbook.VBProject`アクセス自体がエラーになり設定手順を含むメッセージが返るので、そのままユーザーに案内する（設定手順: Excelを開く→ファイル→オプション→トラストセンター→トラストセンターの設定→マクロの設定→「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」にチェック）。
- 対象は`.xlsm`のみ（`.xls`/`.xlsx`非対応）。
- **UserFormの作成・編集は対象外**（標準モジュール・クラスモジュール・ドキュメントモジュール(`ThisWorkbook`/シートモジュール)のみ対応）。
- `run_macro`を含む呼び出しのみマクロ実行許可設定（`msoAutomationSecurityLow`）で開く。それ以外（`add_module`/`set_code`/`delete_module`のみ）はマクロ自動実行無効（`msoAutomationSecurityForceDisable`）で操作するため、編集中に`Workbook_Open`等が意図せず実行されることはない。

## opsの一覧

| op | 必須 | 任意 | 説明 |
|---|---|---|---|
| `add_module` | `name` | `code`(既定`""`＝空モジュール)、`type`(既定`standard`。`standard`/`class`) | 標準/クラスモジュールを新規追加。同名既存はエラー |
| `set_code` | `name`,`code` | - | 既存モジュール（`ThisWorkbook`等含む）を**全文置換** |
| `find_replace` | `name`,`old_code`,`new_code` | - | 一部コード（`old_code`）を`new_code`に置換（差分パッチ）。`old_code`が非一意（0件/複数件）ならエラー |
| `replace_procedure` | `name`,`procedure`,`code` | `kind`(`sub`/`property_get`/`property_let`/`property_set`。省略時自動判別) | 指定Sub/Function/Propertyだけを丸ごと置換 |
| `insert_code` | `name`,`code` | `position`(既定`end`。`end`/`start`/行番号) | 既存コードに触れず新規コードを追加 |
| `delete_module` | `name` | - | モジュール削除（ドキュメントモジュールは削除不可でエラー） |
| `run_macro` | `name` | `args`(配列) | 指定マクロを実行。戻り値があれば`results`配列に入る |

**`set_code`は必ずモジュール全文が必要。** 長いモジュールで一部だけ変更したい場合、全文渡し方式は低パラメータLLMほど「一部のつもりが無関係な部分を欠落・改変する」事故が起きやすい。**既存モジュールの一部変更は必ず差分系opを優先する**（`set_code`は総入れ替えの場合のみ）:

| 変更したい範囲 | 使うop |
|---|---|
| 数行〜1つの式・条件式 | `find_replace`（`old_code`はexcel-vba-readスキルで取得したコードから一意な範囲をそのままコピー。改行コードCRLF/LFの違いは自動吸収） |
| 1つのSub/Function/Propertyの中身丸ごと | `replace_procedure`（プロシージャ外の他コードは渡さなくてよい） |
| 既存コードは変えず新規Sub/Functionを追加 | `insert_code`（既存コードのやり取り不要） |

出力: `{"path":..., "backup_path": null, "applied_ops": 2, "results": [3]}`。`results`は戻り値ありop（`run_macro`でFunction実行時等）の結果のみ発生順に入る。`backup_path`は保存先に既にファイルがあった場合、上書き直前にタイムスタンプ付きでコピーした先の絶対パス（無ければ`null`）。生成・更新したファイルは`path_memory`に自動登録される。

## 手順とエッジケース

1. コードを書く前にexcel-vba-readスキルの`read_vba.py`でモジュール一覧・既存コードを確認する。
2. 既存モジュールへの変更は基本`find_replace`/`replace_procedure`/`insert_code`のいずれか（`set_code`は全体書き直し時のみ）。
3. 別名で保存したい場合のみ`--output`を追加する（省略時は`path`へ上書き保存）。
4. `run_script`を呼ぶ。
5. `--new`使用時に保存先（`--output`指定時はそちら、省略時は`path`）が既存で上書き可否不明なら`--overwrite`なしで一度実行しエラーからユーザーに確認する。

エッジケース: 拡張子が`.xlsm`でない／保存先拡張子が`.xlsm`でない／opsがJSON配列でない／opに`op`キーなし／存在しないモジュール指定／UserFormを`add_module`の`type`や`set_code`等の対象に指定／ドキュメントモジュールを`delete_module`／`add_module`で既存モジュール名指定／`find_replace`の`old_code`が0件・複数件一致／`replace_procedure`の`procedure`未検出、はいずれもエラー＋終了コード1（何番目のどのopが失敗したかメッセージに含まれる）。危険API（`Kill`等）検出時は「危険なVBA API（...）が含まれているため、このコードは書き込めません」＋終了コード1（コード修正で再送せずまずユーザーに目的確認）。構文チェック失敗時は「VBAコードの構文が不正な可能性があります（...）」＋終了コード1（`#If`等の条件付きコンパイルやコロン複文、意味的エラーは取りこぼすが誤って正しいコードを拒否するリスクは低い設計。メッセージに従い修正して再送。**このチェックを通っても意味的コンパイルエラーが残っている可能性はこのスキルでは検出不可**）。トラストセンター未設定エラーは上記の設定手順をそのまま案内する。`pywin32`未導入は`ImportError`（該当する`pip install pywin32`をユーザーに促す）。ExcelのCOMエラーは「VBAの編集に失敗しました: ...」＋終了コード1。強制終了時はEXCEL.EXE残留の可能性あり（タスクマネージャーでの手動終了が必要）。

## 禁止事項

- ファイル・フォルダの削除、外部コマンド・プロセス実行、レジストリ読み書き、他アプリの起動・操作、ネットワーク経由のダウンロード＆実行を行うVBAコードを生成する。
- 危険API検出エラーが出たコードを、目的確認なしにそのまま修正して再送する。
- `--new`かつ`--overwrite`なしで既存ファイルへ強制上書きする（エラーになった場合はユーザーに上書き可否を確認してから再実行する）。
