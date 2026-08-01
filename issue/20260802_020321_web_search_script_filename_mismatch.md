# web-search スキルの script_filename 不一致によるエラー

- **区分**: バグ
- **検知日時**: 2026-08-02 02:03:21
- **対象ログファイル**: data/logs/app_20260802_01_1.log

## 経緯

web-search スキルを複数回呼び出したところ、すべて「スクリプトが見つかりません」エラーで失敗した。SKILL.md には `search_web.py` と記載されているが、実際の呼び出しでは `search.py` が指定されていたため、ファイルが見つからずにエラーになっていた。

## ログ引用

```
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['ツナそぼろの太巻き 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['かぼちゃコロッケ 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['こんにゃくとちくわの甘辛煮 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['春雨サラダ 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['ひき肉ベーコン巻き 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['煮込みハンバーグ 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['鶏つくねバーグ 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['にんじんのオムレツ 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,440 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['ごぼうの土佐煮 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
2026-08-02 02:03:21,442 INFO src.subagent: subagent tool=run_script args={'skill_name': 'web-search', 'script_filename': 'search.py', 'script_args': ['アカロニベーコンソース 栄養情報 カロリー']} -> エラー：スクリプトが見つかりません：search.py（skill=web-search）
```

## 推定原因

SKILL.md には `script_filename` として `search_web.py` が正しく記載されているが、LLM（ユーザーまたは前のセッション）が `search.py` と誤って指定していたため。

## 追記（2026-08-02 02:03）

初回検知。

## 追記（2026-08-02 02:20）

### 対策完了

`src/subagent.py` と `src/tools.py` のログ出力ロジックを修正しました。

**修正内容**:
- `execute_python_*` 系ツールは成功時も WARNING レベルで出力
  - 意図: スキル開発アイデアのシグナルとして記録。代替スキルが作られれば LLM は呼ばなくなる
- ツール実行結果にエラーキーワードが含まれる場合を WARNING レベルで出力
  - 意図: monitor-app-log スキルで検知可能にする
- エラーキーワードの検出対象:
  - 日本語「エラー」
  - 英語「error」(大文字小文字区別なし: "error", "Error", "ERROR")
  - 全角カタカナ「ｴﾗｰ」

**修正箇所**:
- `src/subagent.py:77-110`（サブエージェントのツール呼び出し結果）
- `src/tools.py:2888-2906`（メイングラフのツール呼び出し結果）

## ユーザー回答

上記修正済み

