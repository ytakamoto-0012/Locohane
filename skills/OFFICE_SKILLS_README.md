# Office系スキル（excel-*/docx-*/pptx-*）実装メモ

このファイルは `skills/SKILLS_README.md`（Agent Skills仕様全般の実装メモ）を前提に、
Excel/Word/PowerPointを扱うスキル群固有の実装上の注意点をまとめる。**他基盤
（Claude Code等、別のAgent Skills対応環境）へこれらのスキルフォルダを個別に
持ち出す場合は、`SKILLS_README.md`の「6. Anthropic互換について」が述べる
一般的なポータビリティに加えて、以下の固有事情を必ず確認すること。**

## 対象スキルとファミリー分け

| ファミリー | スキル |
|---|---|
| excel | `excel-read` / `excel-edit` / `excel-recalc` / `excel-render` / `excel-vba-read` / `excel-vba-edit` |
| docx | `docx-read` / `docx-create` / `docx-edit` / `docx-render` |
| pptx | `pptx-read` / `pptx-inspect` / `pptx-create` / `pptx-edit` / `pptx-render` |

各ファミリーは同一ライブラリ（`openpyxl`/`xlrd`、`python-docx`、`python-pptx`）に依存し、
共通のヘルパーコードをファミリー内で共有する。共有の実装方式が**2種類混在している**
点が本ファイルの主題。

## 1. ヘルパーの共有方式（2パターン）

### 1-A. 複製方式（既定・大半のヘルパーが該当）

`_common.py`（UTF-8標準入出力設定・パスメモリー登録・結果JSON書き出し等）と、
excel系の`_style.py`（セル書式変換）は、**ファミリー内の全スキルの`scripts/`配下へ
バイト単位で同一内容を複製**している（`skills/*/scripts/_common.py`をファミリー内で
比較すればハッシュが完全一致する）。

理由: Agent Skills仕様はスキル間の相互import を想定しておらず、`run_script`も
「実行対象スキル自身の`scripts/`配下」以外への依存を前提としない設計のため、
各スキルフォルダを単独で持ち出しても動くよう、共有コードは複製で持たせている。

**この複製を保証する自動チェックは現状存在しない**（手作業での同時更新に依存）。
いずれかの`_common.py`/`_style.py`を修正した場合は、同じファミリーの全スキルへ
同じ内容を反映すること。`docx-edit/scripts/_track_changes.py`と
`docx-read/scripts/_track_changes.py`も同様の複製関係にある（docx-readは
`count_revisions`のみ使うが、全文を複製している）。

### 1-B. 相互import方式（例外: excel-readのみ）

`excel-read/scripts/read_excel.py`は、`excel-edit/scripts/_excel_shared.py`
（列グルーピングロジック`group_column_values`/`column_index`。`excel-edit`の
`insert_row_group`opと完全に同一の実装を共有する必要があるため複製ではなく
実体を1箇所に集約した）を、**相対パスで`sys.path`に追加してimportする**:

```python
_EXCEL_EDIT_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "excel-edit" / "scripts"
sys.path.append(str(_EXCEL_EDIT_SCRIPTS))
from _excel_shared import group_column_values
```

**このため`excel-read`は`excel-edit`フォルダが兄弟ディレクトリとして存在しないと
動作しない（`excel-read`単体では`--query-json`の`group_by`クエリが使えない）。**
本リポジトリでは両スキルとも常にセットで同梱されるため実運用上問題ないが、
`excel-read`フォルダだけを他基盤へ個別に持ち出す場合は`excel-edit/scripts/
_excel_shared.py`も一緒にコピーする必要がある（`_excel_shared.py`単体を
`excel-read/scripts/`へ複製しても動作はするが、その場合1-Aの複製方式に戻るため
以後は手動同期が必要になる）。

## 2. Locohane固有の環境変数（ソフト依存）

`_common.py`の`register_output_path`/`write_json_result`は、`run_script`の
子プロセスに注入される以下の環境変数を使う（`src/tools.py`の`_subprocess_env()`）:

- `AGENT_SRC_DIR`: `src/path_memory.py`をimportするためのパス
- `AGENT_THREAD_ID` / `AGENT_PATH_MEMORY_DIR` / `AGENT_PATH_MEMORY_MAX_ENTRIES`: `@N`パスメモリー登録用

いずれも**未設定時は例外を出さずNoneへフォールバックする**設計（`AGENT_THREAD_ID`
未設定時は`"_no_session"`扱い）。そのため他基盤でこれらの環境変数を用意しなくても
スクリプト自体は単体で動作するが、`@N`によるパス参照の簡略化機能だけが働かない
（生成物のパスは`result_path`/`output_path`に絶対パスとしてそのまま出力される）。

## 3. read/inspect側がwrite側の全能力を読み返せるとは限らない

`pptx-edit`の`set_shape_position`は`left_cm`/`top_cm`/`width_cm`/`height_cm`で
shapeの位置・サイズを書き込めるが、修正前の`pptx-inspect`はこれらを一切
読み返せなかった（`describe_shape`が位置・サイズ情報を含んでいなかった）。
「書き込み側にある能力が読み込み側から見えない」ことに変わりはないため、
`describe_shape`（`pptx-inspect`/`pptx-edit`等5スキル共通の`_common.py`）へ
`left_cm`/`top_cm`/`width_cm`/`height_cm`を追加し、`set_shape_position`と
同じ単位で読み返せるようにした。**新しいwrite系opを追加する際は、対応する
read/inspect側が同じ情報を同じ単位で読み返せるかを必ず確認すること**
（read側の役割が「人間向け要約」か「編集アドレッシング用」かに関わらず、
write側が操作する値をread側のどこかが可視化できていなければ、LLMは
現在値を知る手段が無いまま書き込むしかなくなる）。

## 4. アドレッシング設計（拡張時に踏襲すべきパターン）

`excel-edit`（`insert_row_group`のアンカー解決）、`docx-edit`（`DocEditContext`）、
`pptx-edit`（`EditContext`）は、いずれも共通の設計原則に従う:

> **既存コンテンツを指す番号・indexは、常に「そのスクリプト呼び出しを開始した
> 時点（＝直前のread/inspect系スキルが見せていた状態）の番号」として解決し、
> 削除・挿入・複製で後続の番号がずれても呼び出し側（LLM）に手計算させない。**

これはread側（`group_by`クエリ、`docx-read`の段落index、`pptx-inspect`の
`slide`/`shape_index`）とedit側で**同一のアドレッシング契約**を保つための設計で、
食い違うと「読み込んだ内容と実際に書き込まれる位置がズレる」事故につながる
（詳細な経緯は`issue/20260812_162617_excel_verify_fix_loop_non_convergence.md`参照）。
他基盤向けにこれらのスクリプトを拡張・移植する場合も、このアドレッシング契約を
崩さないこと。

## 5. サードパーティ依存

| ファミリー | 主な依存パッケージ | バージョン（`requirements.txt`） |
|---|---|---|
| excel | `openpyxl`（xlsx/xlsm）、`xlrd`（レガシーxls読込のみ） | 3.1.5 / 2.0.2 |
| docx | `python-docx`（import名`docx`） | 1.2.0 |
| pptx | `python-pptx`（import名`pptx`） | 1.0.2 |

他基盤へ持ち出す場合、その環境のPythonにも同等バージョンの導入が必要
（`excel-render`/`docx-render`/`pptx-render`はさらにExcel/Word/PowerPoint本体
またはCOM自動化に依存する場合がある。各スキルの`SKILL.md`を参照）。
