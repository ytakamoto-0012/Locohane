# pptx-create SKILL.mdの「output_pathは絶対パス」指示が、run_scriptの実際のcwd制約（セッション専用一時フォルダ限定）と食い違い書き込みガードでブロックされる

- **区分**: バグ（ドキュメント） → 修正済み（2026-08-23）
- **検知日時**: 2026-08-23 14:22:14
- **対象ログファイル**: data/logs/app_20260823_135730.log

## 経緯

pptx-createで図鑑を作成するタスク中、サブエージェントが
`create_pptx.py` の `output_path`（位置引数）に `C:\DT_Python\Locohane\data\temp\fish_guide.pptx`
（`default_workdir` 直下、ユーザーの作業ディレクトリ未設定時の既定パス）を
絶対パスとして指定して `run_script` を実行したが、書き込みサンドボックス
ガードにより拒否された。実際の `run_script` の cwd は
`C:\DT_Python\Locohane\data\temp\_tmp_05c16259-181a-430f-8490-f4d583f96c3d`
（セッション専用の一時サブフォルダ）であり、その1階層上の `default_workdir`
直下へは直接書き込めない。

これは [issue/20260822_215000_execute_python_code_cwd_diverges_from_workdir.md](20260822_215000_execute_python_code_cwd_diverges_from_workdir.md)
で修正された「SKILL.mdの`絶対パス`指示と実際のcwd制約の食い違い」と同根の
問題だが、そちらの修正対象リスト（excel-edit/excel-vba-edit/pdf-tools/
docx-render/pptx-render）に `pptx-create` は含まれておらず、今回新たに
同じ問題が顕在化した。

この失敗をきっかけに、サブエージェントは「一時フォルダへ生成 →
`execute_python_code` で手動コピー」という代替手段を試みたが、そちらも
別バグ（[issue/20260823_144300_shutil_copy_guard_blocks_readonly_source_outside_workdir.md](20260823_144300_shutil_copy_guard_blocks_readonly_source_outside_workdir.md)
とは別件で、こちらはコピー先が `_tmp_<thread_id>` 内なので該当しない）で
失敗し、最終的に手書きのpython-pptxコードで再生成する方向へ迂回した。
結果として20分以上（14:22〜14:43時点でまだ継続中）にわたり試行錯誤が続き、
その間に `src.context_compaction` の要約失敗が多数回発生している。

## ログ引用

```
2026-08-23 14:22:14,286 INFO src.tools: run_script: pptx-create create_pptx.py cwd=C:\DT_Python\Locohane\data\temp\_tmp_05c16259-181a-430f-8490-f4d583f96c3d
2026-08-23 14:22:14,921 WARNING src.subagent: subagent tool=run_script args={'skill_name': 'pptx-create', 'script_filename': 'create_pptx.py', 'script_args': ['C:\\DT_Python\\Locohane\\data\\temp\\fish_guide.pptx', '--data', '...']} -> [終了コード] 1
[標準エラー]
ファイルの保存に失敗しました: [書き込みサンドボックスガード] 作業ディレクトリ配下以外は書き込みできません: C:\DT_Python\Locohane\data\temp\fish_guide.pptx
作業ディレクトリ（またはセッション専用の一時フォルダ _tmp_<thread_id>）配下のみ書き込み・削除可能です。default_workdir直下など共有フォルダへは直接書き込めません。
```

同一パターンで2回目（14:31:16、`script_args`のキー順以外は同一内容）も再発。

## 推定原因

`skills/pptx-create/SKILL.md` の「引数一覧」（40行目）が
`output_path（位置引数）| 必須 | 文字列（絶対パス） | - | 生成する.pptxの出力先パス`
とのみ記載しており、「その絶対パスは `run_script` の実際のcwd
（ユーザー設定の作業ディレクトリ、未設定なら `_tmp_<thread_id>`）配下でなければ
書き込みガードに拒否される」という制約の説明が無い。LLMは
`default_workdir`（`data/temp`）を「作業ディレクトリそのもの」と誤認し、
その直下への絶対パスを自然に組み立ててしまう。

対応は8/22の類似issueと同様、`skills/pptx-create/SKILL.md` に
「`output_path` はrun_scriptの実際のcwd配下（`check_work_dir_status`等で
確認できる作業ディレクトリ、またはセッション専用一時フォルダ）を指すこと。
`default_workdir`直下の固定パスを直接指定しない」旨を明記することが
考えられる。同種の記述漏れが他の `*-create`/`*-edit` 系スキルにも残って
いないか、横断的な棚卸しが望ましい。

## 追記（2026-08-23 15:10）— 修正済み

`skills/pptx-create/SKILL.md`の「引数一覧」直後に、`output_path`は絶対パス
だが実際に書き込みが許可されるのはユーザーの作業ディレクトリ配下、または
未設定時はセッション専用の`_tmp_<thread_id>`配下に限られること、
`default_workdir`直下を直接指定すると書き込みサンドボックスガードに拒否
されることを明記した。不明な場合は`check_work_dir_status`で確認するか
委譲元のtask文の出力先パスをそのまま使うよう案内を追加。

他の`*-create`系スキルへの横展開・`check_work_dir_status`のメッセージ自体の
見直し（work_dir未設定時に「読み書き可能」とだけ返し、実際は書き込みが
`_tmp_<thread_id>`へ縮小される点を説明していない）は本issueのスコープ外
として今回は対応していない。

検証: `pytest tests/` 444件全通過（ドキュメントのみの変更のため既存テストへの影響なし）。

## ユーザー回答

ここにはユーザーの回答が記述される
