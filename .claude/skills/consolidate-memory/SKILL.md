---
name: consolidate-memory
description: Locohane の永続メモリー（data/memory/{user,feedback,project,reference}/*.md）を1日1回棚卸しし、同一テーマ・重複する内容を1本に統合してマージすることで、似たタスクの改善案がバラバラなnameで分散するのを防ぐ。スキル自身がCronCreateで日次・durable:trueの自己スケジュールを登録し、以後はcron発火のたびに自分自身を再度呼び出す。「メモリーを整理して」「重複メモリーを統合して」「/consolidate-memory」等で使う。統合の要否はLLM自身が本文を読んで判断し、機械的な文字列一致だけでは統合しない。
---

# consolidate-memory: Locohane永続メモリーの重複統合

`Locohane` は `data/memory/{user,feedback,project,reference}/*.md`（`config.ini` の
`[memory].memory_dir`、既定 `./data/memory`）に YAML frontmatter 付き Markdown として
永続メモリーを保存する（`src/memory.py`）。Locohane側のエージェントは `search_memory`
（キーワード部分一致のみ）で重複確認してから記録する運用だが、キーワードの言い換えや
低パラメータモデルの判断ミスで、同じテーマの改善案が別 name のメモリーとして分散して
しまうことがある。本スキルはこれをバックグラウンドで定期的に棚卸しし、重複・関連の強い
メモリー同士を1本に統合する。

## 状態ファイル

`.claude/state/consolidate-memory/state.json`（プロジェクトルート基準）:

```json
{"last_consolidated": "2026-09-04T09:00:00", "cron_job_id": "xxxxx"}
```

- `last_consolidated`: 前回統合チェックを完了した時刻（ISO形式、ローカル時刻）。
  ファイルが無い/壊れている場合は全件を対象に初回統合を行い、完了後に現在時刻で
  作成する。
- `cron_job_id`: このスキルが自己登録した `CronCreate` のジョブID。

## 手順

### 1. 自己スケジュールの確認・登録

1. `state.json` を読む（無ければ手順末尾の初期化を行う）。
2. `cron_job_id` があれば `CronList` で該当ジョブがまだ存在するか確認する。
3. ジョブが存在しない場合（初回起動、または登録から7日経過して自動失効した場合）
   のみ、以下の内容で `CronCreate` を呼び、返ってきたジョブIDを `state.json` の
   `cron_job_id` に書き込む。既にジョブが生きている場合は**絶対に再登録しない**
   （重複ジョブが並走してしまうため）。
   - `cron`: `"22 4 * * *"`（毎日、深夜4時22分。0分/30分ちょうどを避ける）
   - `recurring`: `true`
   - `durable`: `true`
   - `prompt`: `"/consolidate-memory を実行し、data/memory 配下の重複・関連メモリーを棚卸しして統合してください。"`
   - `reason`（分かればログ用に）: `"永続メモリーの重複統合のための自己スケジュール"`

**制約**: durable recurring ジョブは登録から7日で自動失効する（ツール仕様の上限）。
失効後は誰もこのスキルを起動しないため統合が止まる。ユーザーには「7日以上
Claude Code を開かない期間があると統合が止まるが、気づいたときに一度このスキルを
手動起動すれば自動的に再登録される」旨を、初回登録時と再登録時に一言添える。

### 2. 対象メモリーの列挙

1. `config.ini` の `[memory].memory_dir`（既定 `./data/memory`）を読み、プロジェクト
   ルート（このリポジトリのルート）基準の絶対パスに解決する。
2. `memory_dir/{user,feedback,project,reference}/*.md` を全件 `Glob`/`Read` で列挙し、
   各ファイルの frontmatter（`name`/`description`/`type`/`created`/`updated`）と
   本文を読む。
3. 対象が数十件を超える場合は、`state.json` の `last_consolidated` より `updated` が
   新しいメモリー（＝前回統合以降に新規作成・更新されたもの）を「起点」とし、それぞれ
   について同じ `type` の既存メモリー全件との類似性のみ確認する（type横断では統合しない。
   全件×全件の総当たりはしない）。`last_consolidated` が無い初回実行時は、type内で
   全件同士を比較する。

### 3. 重複・関連の判定（機械的な文字列一致だけで統合しない）

各起点メモリーについて、同じtype内の他メモリーと `description`・本文を実際に読み比べ、
次のいずれかに該当するか判断する。

- **完全な重複**: 同一の対象・同一の教訓を指している（nameや言い回しが違うだけ）。
- **包含関係**: 一方が他方の内容を包含し、かつ新しい情報を含む。
- **強い関連**: 同一の対象（同じスキル・同じコンポーネント）について、Why/How to apply
  の異なる側面を別々に記録しているだけで、1本にまとめた方が今後参照しやすい。

該当しない（単に同じキーワードを含むだけで話題が違う）場合は統合しない。判断に迷う場合は
統合を見送り、次回以降の判断に委ねる（誤統合による情報欠落を避ける）。

### 4. 統合の実行

統合すると判断したメモリー群について:

1. 最も内容が充実している、または最も新しい（`updated`が新しい）ものを残す側として
   選ぶ。
2. 残す側の本文に、他のメモリーが持っていた情報（Why/How to apply/具体例等）を欠落
   させず統合する。単純な結合ではなく、重複する記述は削り簡潔にまとめる（情報を
   失わないことを優先し、無理に短くしすぎない）。
3. 統合後の内容で対象ファイルを直接編集する（`Edit`/`Write`）。frontmatterの`updated`
   を現在時刻（UTC、`YYYY-MM-DDTHH:MM:SSZ`形式）に更新する。`name`/`description`/
   `type`/`created` は変更しない（`description`は必要なら統合後の内容に合わせて
   書き直してよいが、`name`は変更しない＝索引の一意性を壊さない）。
4. 吸収された側のファイルを削除する。

### 5. 索引の再構築

ファイル操作後は必ず `MEMORY.md` 索引を再構築する（Locohane本体の `src/memory.py` の
`rebuild_index()` をそのまま呼ぶ。索引を手で書き直さない）。CLAUDE.md記載のPython実行
環境を使い、プロジェクトルートで実行する:

```
"C:\DT_Python\Python311\env_local_agent_system\Scripts\python.exe" -c "from pathlib import Path; from src.memory import rebuild_index; rebuild_index(Path('data/memory'))"
```

### 6. 状態更新と報告

1. `state.json` の `last_consolidated` を今回のチェック終了時刻（現在時刻）で更新する。
2. 統合した件数・統合前後のname一覧を要約してユーザーへ報告する（手動起動時のみ。
   cron発火による自動実行時は、統合が1件でもあった場合のみ簡潔に報告し、無ければ
   何も報告しなくてよい）。

## 安全策

- ジョブの重複登録を避けるため、`CronCreate` は「`CronList` で確認して存在しない
  場合のみ」呼ぶ。
- 統合は必ず本文を実際に読み比べてから行う。nameやdescriptionの文字列類似度だけで
  機械的に統合しない。
- 統合により情報が失われないことを優先する（両方の記述を統合後の本文に残す。片方を
  無条件に削除しない）。
- 判断に迷うペアは統合せず見送る（1回の実行で無理に処理しきらない）。
- `data/memory/` 配下以外のファイル（アプリ本体のコード・`config.ini`等）は一切
  変更しない。
- git へのコミット・ステージングは行わない（削除・統合の履歴はgit管理下のワーキング
  ツリーの変更として残るため、必要ならユーザー自身が確認・コミットできる）。
