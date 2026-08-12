# excel-readで実在しない既定シート名"Sheet1"/"Sheet2"を指定して失敗、直後にopenpyxlのRowDimension属性名も誤って使用

- **区分**: 問題点
- **検知日時**: 2026-08-12 12:25:07, 12:29:32
- **対象ログファイル**: data/logs/app_20260812_113919.log

## 経緯

`annual_schedule.xlsx`の内容確認フェーズで、workerサブエージェントが`excel-read`スキルの`read_excel.py`をシート名`Sheet1`・`Sheet2`（openpyxlの既定シート名）を指定して2回連続実行したが、実際のシート名は`月間予定表`・`週間予定表`（作成時に明示的に付けた日本語シート名）であり、いずれも「シートが見つかりません」で失敗した。

その約4分後、グループ化（アウトライン）設定を確認するため`execute_python_code`でopenpyxlを直接操作した際、`RowDimension`オブジェクトに`.level`属性でアクセスして`AttributeError`が発生（正しくは`.outline_level`）。この回は同一実行内のstdoutで`Sheet2`側の一部データ確認までは成功していたが、グループ化レベルのprintでクラッシュした。直後（13秒後、12:29:45）に`.outline_level`へ修正した呼び出しで正常に確認できている。

いずれも実害（ファイル破損等）は無く、次の1〜2回の呼び出しで自己回復している。

## ログ引用

```
2026-08-12 12:25:07,316 WARNING src.subagent: subagent tool=run_script args={'skill_name': 'excel-read', 'script_filename': 'read_excel.py', 'script_args': ['E:\\yukinori\\テスト\\annual_schedule.xlsx', '--sheet', 'Sheet2', '--offset', '0', '--limit', '50']} -> [終了コード] 1
2026-08-12 12:25:07,316 DEBUG src.subagent: subagent tool=run_script args={...} -> "[終了コード] 1\n[標準エラー]\nシートが見つかりません: Sheet2（存在するシート: ['月間予定表', '週間予定表']）"

2026-08-12 12:25:07,316 WARNING src.subagent: subagent tool=run_script args={'skill_name': 'excel-read', 'script_filename': 'read_excel.py', 'script_args': ['E:\\yukinori\\テスト\\annual_schedule.xlsx', '--sheet', 'Sheet1', '--offset', '0', '--limit', '51']} -> [終了コード] 1
2026-08-12 12:25:07,316 DEBUG src.subagent: subagent tool=run_script args={...} -> "[終了コード] 1\n[標準エラー]\nシートが見つかりません: Sheet1（存在するシート: ['月間予定表', '週間予定表']）"

2026-08-12 12:29:32,185 DEBUG src.subagent: subagent tool=execute_python_code args={...print(f"  行{row_idx}: level={sheet2.row_dimensions[row_idx].level}")...} -> '[終了コード] 1\n[標準出力]\n...\n[標準エラー]\nTraceback (most recent call last):\n  File "...tmppv3h2twv.py", line 149, in <module>\n    print(f"  行{row_idx}: level={sheet2.row_dimensions[row_idx].level}")\nAttributeError: \'RowDimension\' object has no attribute \'level\''
```

## 推定原因

- シート名の件: 自分自身がこのセッションで既に作成した（月間予定表・週間予定表という日本語名で`add_sheet`した）ファイルにもかかわらず、読み込み時にopenpyxlの既定名`Sheet1`/`Sheet2`を推測で指定してしまった。直前のツール結果（`edit_excel.py`の`ops`引数等）に正しいシート名が含まれていたはずだが、それを参照せず一般的な既定値を使った点で、[issue/20260809_002501_glob_wrong_path_inference_error.md](20260809_002501_glob_wrong_path_inference_error.md)の「パス・識別子を推測で構築してしまう」問題と同根とみられる。
- `RowDimension.level`の件: openpyxlのAPIとして`level`ではなく`outline_level`が正しい属性名。LLMの学習データ内の誤情報またはうろ覚えによる単純なAPI名の取り違えとみられる。

## 追記（YYYY-MM-DD HH:MM）

（同一原因の問題が再検知されるたびに、ここに追記を積み重ねていく）

## ユーザー回答

ここにはユーザーの回答が記述される
