---
name: officecli-python-bridge
description: officecli-xlsx/officecli-docx/officecli-pptx等のofficecliスキルを使うと決めた後、実際に呼び出すコードを書く前に読む。officecliのSKILL.md本文はbash/zsh前提の記法（heredoc、forループ、grep/jqパイプ、$VAR展開）で書かれているが、Locohaneのexecute_python_codeにはシェルが無くPythonしか実行できない。bashの例をPythonのsubprocess呼び出しへ変換する対応表と、実際の検証で繰り返し発生した落とし穴（open/close忘れ、ファイルパスの不整合、コマンド長超過等）のチェックリストを示す。
license: MIT
metadata:
  author: ytakamoto
  version: "2.0"
---

# officecli-python-bridge

officecli-xlsx / officecli-docx / officecli-pptx のSKILL.md本文は、bash/zshのシェルで
`officecli` コマンドを直接叩く前提で書かれている。しかし Locohane の `execute_python_code` は
**Pythonコードしか実行できず、bash/zsh/PowerShellのようなシェルは存在しない**。

officecliのSKILL.md本文にある bash の例をそのままコピー＆ペーストしても動かない。
このスキルは、それらの bash 例を `execute_python_code` 内の Python コードへ変換する方法と、
Locohane環境で実際に発生した落とし穴をまとめる。`officecli-*` を使うと決めたら、
コードを書く前に必ずこのスキルも読むこと。

officecli 自体の呼び出し規約（`--json` の意味、element pathの書き方等）はここには書かない。
それは `officecli-xlsx` 等の本文を参照すること。ここに書くのは**Pythonへの変換方法と、
Locohane環境特有の落とし穴だけ**。

## よくある間違い（チェックリスト）

過去の検証で実際に発生した失敗パターン。officecliコマンドを書く前に一通り目を通すこと。

1. **`run_script` では呼べない**: `officecli-*` は外部バイナリのCLIツールで
   `scripts/` ディレクトリを持たない。`run_script(skill_name="officecli-xlsx", ...)` の
   ように呼ぶと「scripts/ ディレクトリがありません」というエラーになる。これは
   「officecliが使えない」という意味ではない。必ず `execute_python_code` 内で
   `subprocess` から呼ぶこと（本スキルのコード例を参照）。
2. **ファイルパスは、作業の最初から最後まで同じ絶対パスを使い続けること。
   相対パスと絶対パスを混在させない。** `execute_python_code` の実行ディレクトリは
   `_tmp_<セッションID>` のようなサンドボックス配下であり、`cli("create", "report.xlsx")`
   のように**相対パスで作成すると、そのサンドボックス内に作られる**。別の
   `execute_python_code` 呼び出しで今度は作業ディレクトリ直下の**絶対パス**
   （例: `os.path.join(work_dir, "report.xlsx")`）を使って同じファイルのつもりで
   操作すると、officecliから見るとこれは**別ファイル**であり、「シートが見つからない」
   「`query`が空を返す」「ファイルがロックされている」といった一見バラバラな症状が出る
   （実際にこの原因で1回、原因調査に時間がかかった実例がある）。
   対策: 最初に `file_path = os.path.join(work_dir, "report.xlsx")` のように
   **絶対パスを1つの変数に決め、以降すべての `cli(...)` 呼び出し・`dispatch_agent` への
   委譲・最終回答での報告に、その同じ変数（または同じ絶対パス文字列）だけを使う**。
   相対パスでの `create` は使わない。
3. **`open`（resident/常駐モード）を使うなら必ず`close`で終える。** 詳細・対策コードは
   下記「`open`を使う場合の注意」を参照。基本方針は`open`/`close`を使わないこと。
4. **`batch --commands` にJSON文字列を直接渡すと、Windowsのコマンドライン引数長の
   上限（約8191文字/プロセス）に達してエラーになることがある。** 操作件数が多い場合は
   最初から `--input <一時JSONファイル>` を使う（下記コード例2参照）。
5. **生成したファイルの検証（`dispatch_agent(agent_type="verifier")`への委譲）や
   最終回答での報告には、自分が実際に使った絶対パスをそのまま使う。パスを推測・
   再構築しない。** `execute_python_code` の実行結果には生成・更新したファイルの
   `path_memory`参照（`@N`）が含まれる。それを使うか、チェックリスト2で決めた
   絶対パス変数をそのまま使うこと。

## `open` を使う場合の注意

officecli-xlsx等のSKILL.md本文には「Performance: Resident Mode」として、
`officecli open "$FILE"` → 複数回の編集 → `officecli close "$FILE"` という高速化手段が
紹介されている場合がある。**`open`した場合、最後に必ず`close`を呼ぶこと。**
`open`後の編集は常駐プロセスのメモリ上に保持され、`close`を呼んで初めてディスクへ
確定反映される（早い段階の構造変更だけがディスクに残り、後続のセルデータ書き込みが
一切反映されないまま「成功」の終了コードだけが返る、という事象が実際に確認されている）。

**基本方針: 特別な理由が無ければ`open`/`close`は使わず、単発コマンド方式
（コード例1・例2のように、ファイルパスを毎回引数で渡す方式）を使うこと。**
単発コマンド方式は1コマンドごとに読み込み→変更→保存→終了が完結するため、
`close`忘れによるデータ消失が原理的に起こらない。`open`/`close`は同一ファイルへの
編集回数が非常に多く、単発方式では時間がかかりすぎる場合の最適化としてのみ検討し、
使う場合は必ず次の形（`close`を`finally`で保証する）にすること:

```python
cli("open", file_path)
try:
    cli("set", file_path, "/Sheet1/A1", "--prop", "value=売上")
    # ... 複数回の編集 ...
finally:
    cli("close", file_path)  # ここが呼ばれないと編集内容がディスクに反映されない
```

途中で1回でも`close`を呼ばずに処理が終わる・エラーで中断する経路があると、
それまでの編集がすべて失われる。`finally`で確実に呼べる自信が無い場合は、
そもそも`open`/`close`を使わず単発コマンド方式にすること。

## 大原則

- 1コマンドは `subprocess.run([...])` の引数リストに素直に分解するだけでよい。
  シェルの `$FILE` のような変数展開は、Pythonの変数をリスト要素として渡せば済む
  （シェル展開そのものが不要になる）。
- ファイルパスは常に絶対パスの変数を使う（上記チェックリスト2参照）。
- `env=` は指定しない。省略すればOSの環境がそのまま子プロセスへ継承され、
  `officecli` はPATH経由で解決できる（Locohane側で `config.ini [paths] bin_path` が
  自動的にPATHへ追加してくれている）。`env=os.environ.copy()` 等で明示的に上書きすると
  意図せずPATHが失われる場合があるので、書く必要が無い限り書かない。
- 結果は必ず `--json` を付けて取得し、`json.loads(result.stdout)` でパースする。
  `grep`/`wc`/`jq` のような後処理はPython側の辞書・リスト操作に置き換える。
- officecli-xlsx等が言う「1コマンドずつ実行し終了コードを確認する」という規律は、
  Pythonの `for` ループの中で `if result.returncode != 0: ...; break（またはraise）` として
  そのまま再現する。

## 変換対応表

| bashでの記法 | Pythonでの書き方 |
|---|---|
| `officecli <verb> "$FILE" <path> --prop k=v` | `subprocess.run(["officecli", verb, file, path, "--prop", "k=v", "--json"], capture_output=True, text=True, encoding="utf-8")` |
| `$FILE` 等の変数展開 | シェル展開ではなく、Pythonの変数をそのままリスト要素として渡すだけ |
| `cat <<'EOF' \| officecli batch "$FILE" ... EOF`（シングルクォートheredoc） | `subprocess.run(["officecli","batch",file,"--commands", json.dumps(ops), "--json"], ...)` または `--input <jsonファイルパス>` |
| `cat <<EOF \| officecli batch "$FILE" ... EOF`（クォート無し。heredoc内で`$SLIDE`等をシェルが展開してから渡している） | heredoc内の変数展開は、Python側の f-string で**先にJSON文字列を完成させてから** `--commands`/`input=` へ渡す（最終的に届くJSONの中身は同じになる） |
| bashの `for col in 1 2 3 4; do ... done` | 素のPythonの `for col in [1, 2, 3, 4]:` ループ（変換というより、そのままPythonに書き直すだけ） |
| `\| grep -c ...` / `\| wc -l` / `\| jq '...'` | `--json` を付けて `json.loads(result.stdout)` し、Pythonの `len(...)` や文字列検索で代替する |
| `$(...)` コマンド置換 | 直前の `subprocess.run(...).stdout` をPython変数に代入して使う |

## コード例1: 単発コマンド（README推奨パターン）

```python
import json
import subprocess
import os

# ファイルパスは最初に絶対パスで1回だけ決め、以降ずっとこの変数を使う
work_dir = r"C:\path\to\work_dir"  # 委譲元から渡された作業ディレクトリ
file_path = os.path.join(work_dir, "report.xlsx")

def cli(*args):
    result = subprocess.run(
        ["officecli", *args, "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"officecli failed: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None

cli("create", file_path)
cli("set", file_path, "/Sheet1/row[1]/cell[1]", "--prop", "value=売上")
info = cli("get", file_path, "/Sheet1/row[1]/cell[1]")
print(info)
```

## コード例2: batch（heredocを使わない）

heredoc（`<<'EOF' ... EOF`）は使わず、`--commands` にJSON文字列を直接渡すか、
`--input` で一時JSONファイルを渡す。

```python
import json
import subprocess

ops = [
    {"op": "set", "path": "/Sheet1/row[1]/cell[1]", "props": {"value": "売上"}},
    {"op": "set", "path": "/Sheet1/row[1]/cell[2]", "props": {"value": "原価"}},
]

# 方法A: --commands にJSON文字列を直接渡す（短い場合はこれで十分）
result = subprocess.run(
    ["officecli", "batch", file_path, "--commands", json.dumps(ops), "--json"],
    capture_output=True, text=True, encoding="utf-8",
)

# 方法B: コマンド列が長い場合は --input で一時JSONファイルを渡す
# （Windowsのコマンドライン引数長の上限、約8191文字/プロセスを避けられる。
# 件数が多くなりそうなら最初からこちらを使う）
with open("_batch_ops.json", "w", encoding="utf-8") as f:
    json.dump(ops, f, ensure_ascii=False)
result = subprocess.run(
    ["officecli", "batch", file_path, "--input", "_batch_ops.json", "--json"],
    capture_output=True, text=True, encoding="utf-8",
)

print(json.loads(result.stdout))
```

`ops` の件数が多い（数百件など）場合は、`--commands`/`--input` に一度に渡さず、
officecli-xlsx等の本文にある「N件ずつのチャンクに分けて呼ぶ」方針をそのまま踏襲してよい。
その場合もbashの `while IFS= read -r` パイプではなく、Python側で `ops` をチャンクに
スライスして `for` ループで複数回 `subprocess.run` を呼べばよい。

## pptxスキルのheredocに関する注意

`officecli-pptx` のheredoc例はクォート無し（`<<EOF`）で、heredoc内部の `$SLIDE` 等を
**意図的にシェルへ展開させてから** batchへ渡している（`officecli-xlsx`/`officecli-docx` の
シングルクォートheredocが逆にシェル展開を無効化しているのとは目的が逆）。Pythonで書く場合は
このクォートの有無を気にする必要はなく、単に「JSON文字列を組み立てる際に `$SLIDE` に相当する
値をPython変数として先に埋め込んでおけばよい」というだけの違いになる。
