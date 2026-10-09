---
name: promote-skill
description: Locohane の利用者が skill-creator で作ったドラフトスキル（data/<インスタンス名>/skill_drafts/<ユーザー名>/<スキル名>/）を、スキル安定化トライアウト（同じ eval ケースを config.ini [skill_creator].tryout_repeats 回ずつ繰り返し、全回合格）に通したうえで、スキル開発者の承認を得て正式スキル（skills/ または project_locohane_dir 配下の skills/）へ昇格させる。不合格なら理由を付けて作成者へ差し戻す。「ドラフトを昇格して」「ドラフトスキルを正式化して」「skill_draftsを見て」「/promote-skill」等で使う。ドラフトの修正はしない（合否の関門役）。正式スキルのチューニングは tune-prompt、ケースの新規作成は create-eval-case の担当。
---

# promote-skill: ドラフトスキルの昇格

Locohane はミスが許されない専門業務が前提のため、正式スキルへの反映は
「スキル安定化トライアウトに全回合格」かつ「スキル開発者の承認」を経たものだけに限る。
このスキルは合否を判断して昇格・差し戻しを行うだけで、ドラフトの中身は直さない。

ファイル操作と機械的な確認は補助スクリプトで行う（CLAUDE.md 記載の Python 実行環境で、
プロジェクトルートから実行する。以下 `H` = `.claude/skills/promote-skill/scripts/promote_helper.py`）。

## 手順0: 対象を選ぶ

1. `python H list` で全インスタンスのドラフトを一覧にする（同じ置き場を共有するインスタンスは1回だけ出る）。
2. `status` が `draft` のものを選択肢にし、`AskUserQuestion` で1件選ばせる（ラベルは `インスタンス: ユーザー名/スキル名`、
   説明に kind・ケース数・最後のトライアウト結果を書く）。`draft` が1件も無ければその旨を報告して終える。
3. 以降、選んだドラフトのフォルダを `<draft>`、インスタンスを `<instance>`、
   一覧の `tryout_repeats` を `<N>` と呼ぶ。

## 手順1: 事前確認

1. `python H check <draft> --instance <instance>` を実行する。`ok: false` なら `problems` を報告して終える
   （元にした正式スキルが変わっている改善案は、作成者に fork し直してもらう必要がある）。
2. 中身を読む:
   - `kind: improve` なら、元の正式スキル（`_draft_meta.json` の `base_root`/`base_skill`）との差分を
     `git diff --no-index --stat` と `git diff --no-index` で見る（`_draft_meta.json`・`evals/` は対象外）。
   - `kind: new` なら SKILL.md・scripts/・references/ を全部読む。
3. 次のどれかに当たるなら、トライアウトせずに手順4（差し戻し）へ進む:
   - スクリプトが作業フォルダの外へ書く・外部へ送信する・破壊的な操作をする
   - 正式スキルの必須ルール・禁止事項を消している
   - ケースが中身を確かめておらず、合格しても安全性・正確性の根拠にならない
4. 差分の要点と、気になった点をユーザーに短く伝える。
5. `register_entries`（scripts/ のスクリプト）を控える。ドラフトのスクリプトは会話では計画承認・
   main_agent_tool_guard を免除されていたため、昇格時にインスタンスの `config_overrides.json` へ
   `plan_approval_exempt_scripts` と `allow_entries`（max_calls=-1）として登録し、昇格後も同じ挙動にする
   （トライアウトも登録済みと同じ状態で評価される）。

## 手順2: スキル安定化トライアウト

1. `python -m evals.instance <instance>` で接続先を確かめ、llama.cpp server が起動していなければ
   ユーザーに起動を頼んで終える（トライアウトの結果が `error: llm_unreachable` になるため）。
2. Claude Code 自身が実行し直す（`_draft_meta.json` の `tryouts` はローカルLLMの自己判定を含むため参考扱い）:
   ```
   python evals/run_all.py --cases-dir <draft>/evals --skill-overlay <draft> --repeat <N> --instance <instance> --results-dir evals/results/promote_<スキル名>
   ```
   時間がかかるため、Bash の `run_in_background` で起動し、完了の通知を待つ。
   トライアウト中もドラフトは一時フォルダに固定されるが、トライアウト後にドラフトが1か所でも
   変わると合格回数は0に戻り、`install` が拒否する（その場合は今の内容でトライアウトし直す）。
3. 出力の「スキル安定化トライアウト」節と `tryout.json` を見る:
   - `pass`: 全ケース N/N 合格。
   - `fail`: 不合格・エラーがある。`results.json` で該当回の `rule_results`・`final_answer` を確かめ、手順4へ。
   - `needs_judge`: judge 付きのケースがある。`results.json` の**全回分**の `transcript` と `judge` を読んで判定する。
     1回でも不合格と判断したら手順4へ。全回合格と判断できたときだけ手順3へ進み、`install` に `--judged-pass` を付ける。
4. 判定の根拠（ケースごとの合格回数、judge で見た点）をユーザーに報告する。

## 手順3: 昇格

1. 昇格先を決める:
   - `kind: improve` は元の正式スキルの場所に固定（補助スクリプトが確かめる）。
   - `kind: new` は `AskUserQuestion` で、一覧の `skills_roots`（`skills/` と各 project_locohane_dir の `skills/`）から選ばせる。
     会社専用のスキルは project_locohane_dir 側を推奨にする。
2. `register_entries` がある場合、設定を登録するインスタンスを決める。設定はインスタンスごとの
   `config_overrides.json` にあるため、昇格先を走査する他のインスタンス（`list` の `skills_roots` で分かる。
   `skills/` は全インスタンス共通）にも登録が要る。`AskUserQuestion`（multiSelect）で、昇格先を走査する
   インスタンスから選ばせる（`<instance>` を推奨）。選ばれなかったインスタンスではスクリプトが計画承認・
   委譲の対象になり、トライアウトと挙動が変わる旨を説明に書く。
3. `AskUserQuestion` で最終承認を得る（昇格先・登録先インスタンスと登録内容・トライアウト結果・差分の要点を説明に書く）。
   拒否されたら何もせず終える。
4. 承認されたら実行する:
   ```
   python H install <draft> --dest <昇格先の skills ルート> --instance <instance> --tryout <tryout.json のパス> --register-instance <登録先> [--register-instance <登録先> ...] [--judged-pass] [--note "判定メモ"]
   ```
   - `tryout.json` が今のドラフト（全ケース・`<N>` 回以上・トライアウト後に変更なし）のものでなければ止まる。
   - 設定は管理ツールと同じ処理で `instances/<登録先>/config_overrides.json` に保存される（検証・バックアップ・
     `instances/admin_changes.log` への記録つき）。登録先の `.env` に `PLAN_APPROVAL_EXEMPT_SCRIPTS` /
     `MAIN_AGENT_TOOL_GUARD_ALLOW_ENTRIES` があると登録が効かないため止まる（ユーザーに .env の見直しを頼む）。
   - 改善案は置き換え前の正式スキルを `evals/history/promote/` に退避してから上書きする。ケースは `evals/cases/<スキル名>/` へ写され、
     以後 tune-prompt の対象になる。記録は `evals/promotion_log.md` に追記される。
5. 登録先インスタンスと、昇格先を走査するインスタンス（出力の `instances_using_dest`）の再起動で、
   全ユーザーのスキル一覧に出て設定も効く旨を報告する。

## 手順4: 差し戻し

1. 作成者が直せるよう、理由を具体的に書く（どのケースが何回中何回落ちたか、何が足りないか）。
2. `python H return <draft> --reason "<理由>"` を実行する（作成者の skill-creator の `list_drafts.py` に理由が出る）。
   作成者がドラフトを直すと自動で `status: draft` に戻り、再び手順0の候補になる（合格回数は0から）。
   採用の見込みが無いと判断したときだけ、ユーザーに確認してから `--reject` を付ける。
3. 報告して終える。ドラフトの中身は直さない。

## 安全策

- 一度に昇格させるのは1件だけ。
- ドラフト・正式スキルを手で編集しない（コピーは補助スクリプトだけが行う）。
- git へのコミット・ステージングはしない（ユーザーの指示があったときだけ）。
- トライアウトは直列で1回だけ実行する（llama.cpp server は1つ）。
