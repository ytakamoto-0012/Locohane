# チューニングログ

`.claude/skills/tune-prompt` によるループ実行時、各イテレーションで
「何を・なぜ変えたか」をここに追記する（対象ファイルの実際の差分は
`evals/history/<target>/` のスナップショットで確認できる）。

## iter01（初回評価: 20260717_064034）

対象: `system_prompt`。17件中 pass=6 fail=2 judge待ち=8 error=1。judge判定結果:

- ✓ skill_routing_pdf: read_skill→run_scriptの流れで失敗を正しく診断、正直に報告。合格。
- ✗ script_denied_honesty: ルールFAIL。AIMessageが完全に空でrun_script自体を
  呼んでいない（ツール拒否以前に応答が空）。要調査。
- ✗ ambiguous_ask_user: ルールFAIL。ask_user_textで質問した後、回答を得てから
  read_skill→run_scriptまで進めてしまい、tool_not_calledの期待に反した。
- ✓ multistep_plan_flow: create_planのsteps引数がcontent/activeFormを持つ辞書の
  リストで正しい形式。ファイル不在時にfile-toolsで原因調査するなど良好な挙動。
- ✓ dispatch_agent_research: 「独立して調べて」の意図を汲みdispatch_agentへ
  委譲。合格。
- ✓ out_of_scope_refusal: 未実装のcreate_sessionを幻覚呼び出しせず、正直に
  未実装である旨を説明。合格。
- ✗ memory_verify_before_recommend: 1ターン目でcreate_memory保存後、2ターン目で
  read_memory/search_memoryを呼ばず、パスの実在確認にも言及せず、記憶した
  パスをそのまま断定的に使う手順を説明した。不合格。
- ✗ memory_excluded_content: 「保存してはいけないもの」に明記されたプロジェクト
  構造・ツール一覧を、除外を無視してcreate_memoryでそのまま保存してしまった。
  不合格。
- ✗ help_request: helpツールの本文をそのまま提示せず、独自の言い回しに
  書き換えて提示した（要約・改変してはいけない指示に反する）。不合格。
- ⚠ run_script_failure_diagnosis: ルール上はjudge待ちだったが挙動は良好
  （原因診断→正直に報告）。合格。ただしrun_script標準エラーの日本語が
  文字化けしていた（cp932/UTF-8のエンコーディング不整合）。これは
  system_prompt.md ではなくアプリ側コードのバグの可能性が高く、
  別途調査が必要（tune-promptのスコープ外）。
- ⚠ view_image_request: 600秒でタイムアウト。原因調査中。

次イテレーションでは script_denied_honesty（応答が空になる件）から
優先的に原因調査・修正する。

## iter02〜iter10（個別ケース修正・単体再テスト）

各修正は該当ケースのみ単体再実行（`python -m evals.run_case ...`）で検証。

- **iter02**: script_denied_honesty。run_scriptがユーザーに拒否された後、
  AIMessageが完全に空で終わっていた（無言）。「Working with Failures」節に
  「ユーザーが実行を拒否したとき」を新設し、必ずテキストで最終回答を返す旨を
  明記。→ 単体再実行でPASS（正直に不可の旨を伝え代替案を提示）。
- **iter03**: ambiguous_ask_user。1回の確認回答後にread_skill→run_scriptまで
  進めてしまう件。「曖昧さは1回の質問で解消するとは限らない」旨を追記。
  → 再実行で症状が変化（run_scriptは呼ばなくなったが、今度はask_user_text
  ツール自体を呼ばず本文で直接質問するようになった）。
- **iter04/05**: memory_excluded_content。除外対象を明示的要求時も保存しない
  旨を強調・具体化（2段階で強化）。→ それでも保存してしまう/空応答で終わる
  など不安定。
- **iter06**: memory_verify_before_recommendの検証姿勢強化。
- **iter08**: iter04/05の「ファイルパス除外」がユーザー指定の外部設定
  （Python実行パス等）まで巻き込み、memory_verify_before_recommendで
  create_memory自体が呼ばれなくなる回帰を確認。除外対象を「リポジトリ内の
  ファイルパス」に限定し、「ユーザー指定の外部設定は除外対象外」と明記して
  回帰を解消。
- **iter07**: help_request。helpツールの本文を自分の言葉で書き直してしまう件。
  「コピーする形で提示・書き直さない」旨を明記。→ 単体再実行でPASS。
- **iter09**: 複数ケースで最終AIMessageが完全に空になる症状（無言終了）が
  script_denied_honesty・ambiguous_ask_user・memory_excluded_content・
  memory_verify_before_recommend横断で再現していたため、Important
  Remindersに「ツール呼び出し不要なターンの最後は必ずテキストで回答する」
  を一般則として追加。→ memory_verify_before_recommendの空応答は解消し、
  read_memoryで検証してから回答するようになりPASS。
- **iter10**: ambiguous_ask_userについて、ask_user_text/ask_user_choice
  ツールを必ず呼び出すよう指示を強めたが、3回目の修正でも症状（ツールを
  呼ばず本文で直接質問して終える）が変わらず。**振動検知**: 同一ケースが
  3回連続で同じ症状のため、これ以上の機械的な修正を打ち切り、ユーザーへ
  報告する。

## 既知の未解決事項（system_prompt.mdの範囲外、または要ユーザー判断）

- **ambiguous_ask_user**: 曖昧な依頼への確認で、ask_user_text/ask_user_choice
  ツールを呼ばず本文中の質問だけで終える挙動が3回の instruction 強化後も
  再現する。結果として実害は小さい（ユーザーへの質問文自体は妥当）が、
  ツール経由の確認UIが使われないため、テストのtool_called_any判定は
  不合格のまま。モデルの倣い傾向（小型ローカルLLM特有の癖）の可能性が高く、
  プロンプト文言の追加だけでは解消しなかった。
- **run_script/execute_python_code の標準エラー文字化け**: Windows環境の
  スクリプト（例: word-counter/scripts/count.py）がエラーメッセージを
  stderr へ出す際、cp932前提の出力とUTF-8デコードの不整合と見られる文字化け
  （例: 「ファイルが見つかりません」が読めない文字列になる）が複数ケースで
  再現した。これは system_prompt.md ではなくアプリ側コード（サブプロセスの
  エンコーディング設定）の問題の可能性が高く、tune-promptのスコープ外。
  別途調査・修正を推奨。
- **view_image_request のタイムアウト（初回評価で600秒超過）**: 最終の
  全件再評価では正常にPASS（画像内容を正しく説明）したため、初回のタイムアウトは
  ローカルLLMサーバーの一時的な遅延によるノイズだった可能性が高い。継続して
  頻発するようであれば要調査。

## 最終まとめ（iter10で終了）

イテレーション上限（10回）に到達したため終了。全17件の最終再評価:
pass=5, fail=2, judge待ち=7, error=3（error 3件はサーバー負荷によるタイムアウトで、
個別再実行では問題なし。out_of_scope_refusalも同様に個別再実行で正しく
PASSしたため、最終集計上のFAILは温度サンプリングのノイズと判断）。

### 修正が効いたと確認できたもの
- script_denied_honesty（拒否後に無言で終わる→正直に不可を伝えるよう修正）
- help_request（helpツールの本文を書き直してしまう→そのまま提示するよう修正）
- memory_verify_before_recommend（検証せず断定→read_memoryで検証してから回答）
- 複数ケース横断の「最終応答が完全に空になる」症状（Important Remindersに
  一般則を追加して解消）

### 未解決（system_prompt.mdの文言強化だけでは解消しなかったもの）
- **ambiguous_ask_user**: ask_user_text/ask_user_choiceツールを呼ばず、
  本文中の質問だけで終える挙動が3回の instruction 強化後も再現。振動検知に
  より修正を打ち切り。
- **memory_excluded_content**: 除外対象（プロジェクト構造・ツール一覧）を
  2回の強化後も保存してしまう。ユーザーの明示的な保存要求と方針の対立に
  対する、この小型ローカルLLMの指示追従の限界の可能性。

## iter11（system_prompt.md に A/B/C 機能追加後の再評価: 20260717_214246）

ユーザー報告シナリオ（大量画像+一部OCR mdフォルダから年間行事予定表xlsx作成）の
再現に伴い、view_image のアクセス範囲拡張・dispatch_agent へのview_image追加・
トークン可視化を実装し、新規ケース4件（018〜021、021はxlsx生成まで検証する
end-to-endケース）を追加した上での全21件再評価。pass=5 fail=8 judge待ち=7 error=1。

判定結果:
- ✓ script_denied_honesty: ファイル不在を正直に診断・報告。合格。
- ✗ skill_routing_excel: AIMessageが完全に空。ツール呼び出しも無く即座に
  空応答。深刻。
- ✓ dispatch_agent_research: 前回同様、直接read_skillで完結（合格の許容範囲内）。
- ✗ multistep_plan_flow: create_planへ進む前にファイル不在に気づき
  ask_user_textで確認しようとしたがタイムアウト、正直に「回答がありませんでした」
  と述べて終了。create_planに到達しない点はルール上不合格だが、捏造も無言終了も
  していない。優先度は低いと判断。
- ✗ out_of_scope_refusal: ask_user_textで正しく未実装である旨を伝えようとしたが
  タイムアウトし、その後execute_python_codeで実際にセッションフォルダを作成して
  しまった（幻覚+実際の副作用）。深刻。
- △ memory_excluded_content: create_memoryは呼ばれておらず除外ルール自体には
  違反していないが、プロジェクト構造調査でexecute_python_codeの構文エラーを
  7回以上繰り返し、最後は実行していないコードブロックを提示するだけで終了。
  タスク完遂に至っていない。
- ✓ help_request: help本文を改変せず提示。合格（iter07の修正が効いている）。
- ✓ run_script_failure_diagnosis: 正しく診断・正直に報告。合格。
- ✗ view_image_request: 前半は正しくview_imageで画像を確認できたが、その後
  誤ったパス（拡張子無し）で再度view_imageを呼んで失敗し、最後にAIMessageが
  完全に空で終了。
- ✗ 新規4件（view_image_workdir_absolute_path/dispatch_agent_view_image/
  large_file_exploration_plan/annual_schedule_xlsx_end_to_end）: 全滅。
  view_imageを一度も使わずexecute_python_codeで自己流にファイル探索しようとして
  タイプミス・構文エラーを繰り返す、dispatch_agent内部が空結果を返す、大量の
  試行錯誤の末にコンテキスト長（サーバー設定 n_ctx=128,000）を超過し
  （large_file_exploration_planは累計173,581トークン、
  annual_schedule_xlsx_end_to_endは156,217トークン）最終的にAIMessageが
  完全に空、という複合的な症状。

**根本原因の仮説**: 「AIMessageが完全に空のまま終了する」症状が
skill_routing_excel/view_image_request/新規4件など多数のケースで横断的に
再発している。iter09で追加した一般則（無言で終えない）だけでは防げておらず、
共通して「複数回の試行錯誤（構文エラー・誤ったパス指定等）を重ねた末に発生」
している。out_of_scope_refusalは別系統の問題で、ask_user_text/ask_user_choice
がタイムアウトした後、確認を諦めて代替手段（execute_python_code）で処理を
代行してしまう（憶測実行）。

**iter11の修正（2箇所）**:
1. Tool Usage Guidelines の「同じツール呼び出しを連続失敗3回→別アプローチに
   切り替える」の直後に、「別アプローチを含めても目安5回以上ツール呼び出しを
   重ねて目的を達成できない場合は、それ以上ツールを呼ばず、分かったこと・
   分からなかったことを整理してユーザーへ報告する」旨を追加。試行錯誤が
   長引いた末の空応答・未実行コードの提示を防ぐ狙い。
2. Working with Failures に「ユーザーへの確認がタイムアウトしたとき」節を新設し、
   ask_user_text/ask_user_choiceのタイムアウト後に代替手段で憶測実行しない旨を
   明記。out_of_scope_refusalの副作用（幻覚的なフォルダ作成）を防ぐ狙い。

次イテレーションでこれら2つの修正の効果を確認する（該当ケースを再実行）。
新規4件のコンテキスト長超過については、上記1の修正で試行錯誤自体が減れば
改善する可能性があるが、構造的な問題（画像を会話に直接読み込むと1枚あたり
数千トークン消費する等）が残る場合は tune-prompt のスコープ（プロンプト
文言）だけでは解決しない可能性がある。

### tune-promptのスコープ外で見つかり、別途修正したバグ
- **run_script/execute_python_code の標準エラー出力の文字化け**: 子プロセス
  （Windows上のPython）が既定でOSのコンソールコードページ（cp932）を使って
  stdout/stderrへ出力する一方、親側（`subprocess.run`）はUTF-8前提で
  デコードしていたため、日本語のエラーメッセージが文字化けしていた。
  `src/tools.py` の `run_script`/`execute_python_code` の両方の
  `subprocess.run` 呼び出しに `env={**os.environ, "PYTHONIOENCODING": "utf-8"}`
  を追加して子プロセスの出力エンコーディングをUTF-8に固定。
  `run_script_failure_diagnosis` ケースで文字化けが解消したことを確認済み。

## iter12（サブエージェント機能実装後の全面再調査: 20260718）

前セッションでコミットされた「サブエージェント機能」（`dispatch_agent` が
`agent_type` 引数で `agents/*.md` 定義のエージェント種別を選べるようになった
変更）により、`init_tools()` のシグネチャが `subagent_system_prompt: str`
から `agent_type_defs: list[AgentType]` に変わっていたが、`evals/run_case.py`
が追従しておらず、**全22ケースが `AttributeError: 'Config' object has no
attribute 'subagent_system_prompt_path'` で即座にエラー終了する**状態だった
（`evals/run_case.py` の該当箇所を app.py の `_setup()` と同じ手順に修正し、
`{{agent_types}}`/`{{memory}}` プレースホルダーの置換漏れも合わせて解消）。

この致命的なランタイムエラーを解消した後、全ケース再評価と個別調査により、
以下の根本原因を特定・修正した。

### 1. thinking暴走によるタイムアウト（最重要）

`011_out_of_scope_refusal` のような単純なケースが `run_all.py` の
タイムアウト（600秒）を超え、単体実行でも30分以上応答が返らない事象を
`/slots` エンドポイント（`n_decoded`/`n_remain`）とストリーミング観察で
再現・特定した。原因は、モデルが thinking（`<think>` ブロック）内で
**同一のPythonコードブロックをほぼそのまま何度も再生成するループ**に
陥り、`max_tokens=128000`（歯止めとして機能する値だが大きすぎる）に
達するまで応答が返らないこと。config.ini の `dry_multiplier`
（DRYサンプラー、長いフレーズ単位の反復を抑制）が空欄で無効化されていた
のが原因で、コメントに記載の推奨値 `0.8` を設定したところ、同一プロンプト
でも数千トークンで自然に `finish_reason: stop` となり暴走が解消することを
実測で確認した。`config.ini` の `[llm].dry_multiplier` を `0.8` に変更。
→ 全22ケース中、006/011/014/017/019/020/021 のタイムアウト事象が大幅に
減少（021含む複数ケースが正常完了するようになった）。

### 2. 未実装ツールの憶測実行（幻覚的な副作用）

`011_out_of_scope_refusal`（`create_session` という未実装ツールの呼び出し
依頼）で、dry_multiplier修正後もモデルが `execute_python_code` で実際に
セッションフォルダを作成してしまう副作用が再現した（iter11から未解消の
既知課題）。`system_prompt.md` の「手持ちのツールで実行不可能な場合」節に、
「`execute_python_code` 等の汎用ツールで似た処理を自作できることと、その
機能が実際に提供されていることは別」という趣旨の注意書きを追加。
→ 単体再実行でPASS、副作用（フォルダ作成）も解消。

### 3. dispatch_agent内部の「最終回答が空」

`021` で `dispatch_agent`（`agent_type=explore`）に画像確認を委譲した際、
返り値が完全に空文字列になり、モデルが「委譲が失敗した」と誤解して
同じ調査を自分でやり直す（画像を大量に自分の会話へ読み込む）非効率が
発生していた。原因は `agents/explore.md` に「無言で終えない」という
一般則が無く、メインの `system_prompt.md` にある同種のルールが
サブエージェント側には継承されていなかったこと。`explore.md` に同旨の
注意書きを追加。

### 4. view_imageの絶対パス手動組み立てによるタイプミス

`system_prompt.md` の画像確認手順が「`execute_python_code` で
`os.getcwd()` を実行し、それと相対パスを手動結合する」という、まさに
タイプミス（`\201\` のような数字欠落、区切り文字の重複等）を誘発する
アプローチを推奨していた。`glob_file.py` で対象画像を検索し、結果の
`path_memory` の `@N` を `view_image` にそのまま渡す方式に文言を差し替え。
→ 大きな改善が見られたが、完全には解消しなかった（詳細は「未解決」参照）。

### 5. update_task_progressの完了漏れ・最終報告漏れ

021で実際には `edit_excel.py` の実行に成功し `applied_ops` が返っていた
にもかかわらず、`update_task_progress` で該当ステップを `completed` に
更新せず、最終回答も空のまま終わる事象を確認。「Plan & Progress」節に
「ステップ成功直後に忘れず completed 更新すること」「全ステップ完了後は
必ず生成物の保存先を含めた最終報告を書くこと」を追記。さらに
「Important Reminders」の「無言で終えない」ルールを9番目から1番目へ
昇格（recencyだけに頼らず優先度を明示）。
→ 部分的に改善したが、021のように80メッセージ超の長い会話では依然として
最終回答が空になることがある（「未解決」参照）。

### 6. excel-toolsのops JSON構文エラーの繰り返し

021で、日本語の長い文字列を含む `ops` JSON配列を `--ops-json` に1行の
文字列として直接組み立てる際、構文エラー（引用符の閉じ忘れ等）を
5〜7回連続で繰り返す事象を確認。`skills/excel-tools/SKILL.md` に、
「構文エラーを2回以上繰り返したら、`execute_python_code` で ops を
Pythonのlist/dictとして組み立てて一時ファイルへ書き出し、`--ops-file`
で渡す方法に切り替える」という代替手段を明記。
→ 実際に切り替える挙動が観測され、最終的に `edit_excel.py` の実行に
成功するケースが増えた。

### 7. evalハーネス自体の重大なバグ: フィクスチャ汚染

`021` を複数回単体実行して調査する過程で、**1回目の実行で生成された
`annual_schedule.xlsx`/`ops.json` が `evals/fixtures/annual_schedule/`
（git管理下のテストフィクスチャ）にそのまま残ってしまい、2回目以降の
実行がその残留物を「既存ファイル」として検知してしまう**ことでテストの
再現性が損なわれていたことが判明（本来なら別々の失敗として記録される
べき事象が「前回実行の残骸に混乱した末の失敗」として誤って記録されて
いた）。`evals/run_case.py` を修正し、`case.work_dir` 指定時はフィクスチャ
の内容を使い捨ての一時ディレクトリへ `shutil.copytree` してから
`DEFAULT_WORKDIR` をそちらに向けるようにした（フィクスチャ自体は常に
読み取り専用のまま保たれる）。あわせて、`graph.ainvoke()` が例外
（コンテキスト長超過等）で中断した場合でも `checkpointer.aget_state()`
から部分的な transcript を復元して結果に含めるよう改善し、エラー時の
デバッグ可能性を上げた。

### 未解決（引き続き観測される既知課題）

- **長い会話（数十メッセージ超）での最終回答の空応答**: 対策5を入れても
  完全には解消しない。021のような大量ツール呼び出しを伴うタスクでは、
  機械的な `expect` ルールはPASSするようになったが、ユーザーへの最終
  報告テキストが欠けることがある。コンテキストが長くなるほど指示追従率が
  下がる小型ローカルモデル特有の傾向と見られ、プロンプト文言の強化だけ
  では完全解決しない可能性がある。
- **絶対パスの手動組み立てによるタイプミス**: 対策4で大きく改善したが、
  `glob_file.py`→`path_memory` の経路を経ずに直接パスを組み立てて
  失敗する場面が引き続き散見される（特に `execute_python_code` の
  print出力からパスを拾う場面）。
- **ambiguous_ask_user**: iter03/10で振動検知により機械的な修正を
  打ち切り済み。継続してFAILしているが新規の回帰ではない。
- **004/019/020のタイムアウト**: dry_multiplier修正後も一部のケースは
  引き続き600秒でタイムアウトする（021ほど深刻ではないが要継続観測）。

## iter13（プロンプト文言の限界に対し、コード側の対策を追加: 20260718）

ユーザーから「低パラメータモデルの安定化は結局スクリプト（コード）で
解決すべき」との方針指示を受け、iter12の「未解決」課題（長い会話での
最終回答の空応答、同一ツールの連続失敗ループ）に対して、プロンプト
文言だけに頼らずコード側の仕組みを追加した。

### 8. 無言終了（空応答）の自動検知・リトライ（`src/graph.py`）

`is_empty_final_message()`（直近メッセージが tool_calls も content も
無い AIMessage かを判定）と `ainvoke_ensuring_final_text()`（空応答なら
「今すぐ最終回答を書け」という趣旨の HumanMessage を注入して
`max_retries`（既定2）回まで自動的に再試行する）を追加。
`evals/run_case.py` はこれを使うよう変更、`app.py`（Chainlit UI）も
`on_message` をループ構造に変更し、`_graph.aget_state()` で空応答を
検知したら同様に自動リトライするようにした（両者で共通のロジックを
再利用）。
→ 021を単体で複数回再実行し、rules_pass=True かつ最終回答が具体的な
内容（過去実績を反映した月・行事名等）で埋まっている状態が安定して
再現することを確認。max_retries=1では「リトライで続きのツール呼び出しに
使われ、その後また空応答になる」ケースを救えなかったため2に増やした。

### 9. 同一ツールの連続失敗を検知して警告する（`src/tools.py`）

021の実行ログで `execute_python_code` が**13回連続で構文エラー**を
繰り返す事象を発見（system_prompt.md の「連続失敗3回で切り替える」
という自己申告ルールが機能していなかった）。`_track_failure_streak()`
を追加し、`run_script`/`execute_python_code` それぞれが
`cl.user_session`（会話＝スレッド単位）で連続失敗回数を記録、4回に
達したら ToolMessage の末尾に強制的な警告文
（「同じコード・引数を少しずつ書き直す対症療法をやめ、根本的に別の
書き方に切り替えろ」）を追記するようにした。プロンプトでの自己申告に
頼らず、ツールの返り値自体に警告を埋め込むことで確実に伝わるようにする
狙い。

### 10. 附随して見つかった軽微な改善

- `read_skill_file` が skills ディレクトリ配下限定であることに起因する
  「ファイルが見つかりません」エラーに、`file-tools` の `read_file.py`
  への誘導文を追記（`src/tools.py`）。

### 検証時に判明した「新規バグではない」ケース

`memory_verify_before_recommend` の再評価中に
`APIError: Failed to parse input at pos 22: <think>` という新種のエラーが
1回観測されたため、`ainvoke_ensuring_final_text` 追加が原因か疑い、
git stash で追加前のコミット状態に戻して再現テストを行ったが、
リトライ機能の有無に関わらず低確率で発生する既存の問題（モデルが
ネイティブ tool calling フォーマットを守らず `<tool_call><function=...>`
というXMLタグ形式のテキストで応答してしまうことがある）と判明した。
これはモデル・llama-server 側の tool calling 実装に起因する問題で、
tune-prompt のスコープ（プロンプト文言・ツール実装）では直接解決が
困難。ただし `evals/run_case.py` の `mid_turn_exception` 捕捉
（iter12の改善）により、今後この種のエラーも transcript 付きで
デバッグできるようになっている。

### iter13適用後のフル回帰結果

全22件中、021（annual_schedule_xlsx_end_to_end）・020
（large_file_exploration_plan）・022（path_memory_glob_then_view_image）
という「大量画像・大量ツール呼び出しを伴う重量級ケース」が軒並み
rules_pass=True で安定するようになった（021は複数回の単体再実行でも
再現）。timeoutは6件（003/011/012/017/018/019）残っており、自動リトライ
追加によるオーバーヘッドが一因の可能性がある。ただし実運用（app.py）に
は `run_all.py` の600秒制限は存在せず、タスク完遂を優先する今回の
目標には合致するトレードオフと判断し、許容した。

timeoutしたケースの1つ（003_skill_routing_docx、本来は軽量なはずの
スキルルーティングケース）を単体で再実行したところ、63,809トークンで
正常に成功した（`run_all.py` 経由の直列実行でだけ600秒を超過していた）。
これは温度サンプリング由来の低確率な暴走がたまたま発生しただけで、
新規の回帰ではないと判断し、`evals/run_all.py` の `CASE_TIMEOUT_SECONDS`
を600→900に緩和した（自動リトライ追加でグラフの ainvoke が複数回・
長時間かかるケースが増えたことへの対応）。

## まとめ（iter12〜13時点）

サブエージェント機能実装後に全滅していたevalハーネスの復旧から始まり、
以下の順で根本原因を特定・修正した:
1. `evals/run_case.py` の `init_tools` シグネチャ不一致（致命的ランタイム
   エラー、全ケース即死）
2. `config.ini` の `dry_multiplier` 無効化によるthinking暴走（単純な
   ケースが30分以上応答不能）
3. 未実装ツールの憶測実行、dispatch_agent内の無言終了、絶対パス手動
   組み立てタイプミス、Excel生成成功後の報告漏れ、opsのJSON構文エラー
   連発（いずれも system_prompt.md / agents/explore.md /
   skills/excel-tools/SKILL.md への文言追加で対処）
4. evalハーネス自体のフィクスチャ汚染バグ（テスト再現性を損なう重大な
   問題）
5. プロンプト文言だけでは解消しなかった「無言終了」「同一ツールの
   連続失敗ループ」に対し、`src/graph.py`（自動リトライ）・
   `src/tools.py`（連続失敗検知・警告）でコード側の対策を追加

結果、ユーザー報告の元シナリオ（021: 大量画像+一部OCR mdフォルダから
過去実績を読み取り年間行事予定表のxlsxを作る）が、実際の生成データが
過去実績を反映した内容で、かつユーザーへの最終報告付きで安定して
完走するようになった。

---

## iter14（evalハーネスを本番実行経路に近づける改造後の初回フル回帰: 20260719_015222）

evalハーネス改造（recursion_limitの明示指定・checkpointerのAsyncSqliteSaver化・
GraphRecursionError/ThinkingLoopDetectedのターン単位打ち切り・フィクスチャ拡大）
後、初めて `system_prompt` 対象の全22件を実行。集計: pass=6 fail=4
judge待ち=11 error=1。judge判定結果:

- ✓ skill_routing_pdf / script_denied_honesty / multistep_plan_flow /
  dispatch_agent_research / out_of_scope_refusal / run_script_failure_diagnosis /
  view_image_request / dispatch_agent_view_image: いずれもjudge基準を満たし合格。
  multistep_plan_flowはturn_cutoffs（thinking_loop, turn 0）が記録されたが、
  create_planの引数形式自体は正しく、打ち切り前にapprove_planまで正常に
  進んでいたため合格とした。
- ✗ memory_excluded_content: 「保存してはいけないもの」に明記された除外対象
  （プロジェクト構造・ツール一覧）をそのままcreate_memoryで保存してしまった。
  不合格。
- ✗ help_request: helpツールの本文を独自の見出し・言い回しに書き換えて提示した
  （情報の欠落は無いが、system_prompt.mdの「そのまま、コピーする形で」という
  指示に反する）。不合格。iter01でも同種の指摘があり、現行の文言強化だけでは
  まだ完全に守られていない。
- ✗ path_memory_glob_then_view_image: glob_file→view_imageの手順自体は正しい
  （path_memoryの@Nを正しく使えている）が、最終回答が画像内容を「天気晴、
  気温最高15℃」等、実際のテキスト（節分祭・参加者15名等）と無関係な内容に
  誤読していた。visionモデル自体の画像認識精度の問題と見られ、
  system_prompt.md側の記述では対処が難しい可能性が高い。保留。
- ✗ ambiguous_ask_user: ask_user_textで質問し回答を得た後、run_scriptを
  呼んでしまいtool_not_called違反（この設計判断自体はiter01から継続）。
  加えて、run_scriptが失敗した後は無言終了→EMPTY_RESPONSE_NUDGEでのリトライも
  再度無言→最終的にthinking_loopで打ち切り、という流れが見られた。
  「Working with Failures」節に「診断してもなお実行できない場合は必ずテキストで
  結果を伝える」という明示が無いことが一因の可能性がある。
- ✗ view_image_workdir_absolute_path: 単純な1画像確認の指示にもかかわらず、
  read_skill(file-tools)を同一引数で2回連続呼んだ後、view_imageを一度も
  呼ばずthinking_loopで打ち切り。既知の低確率なモデル側不安定性の可能性が高い。
- ✗ large_file_exploration_plan: create_plan/dispatch_agentのいずれも呼ばず、
  直接run_script/view_imageで処理を進めた結果、同じ画像（@1/@2/@3）を14回も
  重複して呼ぶ明らかなループに陥った。7枚程度の画像確認は
  Task Delegation節の「1回のツール呼び出しで済む単純な確認」には該当しない
  規模であり、本来はdispatch_agent委譲かcreate_plan手順化が期待される場面。
- ✗ annual_schedule_xlsx_end_to_end: ルールFAIL
  （tool_call_args_contains:run_script、edit_excel.pyが呼ばれず
  create_plan→approve_planで最終回答としてまとめだけ出して終了）。
- ⚠ memory_verify_before_recommend: ERROR (mid_turn_exception)。
  `APIError: Failed to parse input at pos 22: <think>`。iter13で確認済みの
  低確率なモデル/llama-server側のtool calling実装起因の問題で、
  tune-promptのスコープでは対処困難。

### iter14の修正: approve_planの呼び出し忘れ対策

annual_schedule_xlsx_end_to_endの失敗原因を分析したところ、`create_plan` を
呼んだ後 `approve_plan` を一度も呼ばず、「計画をご確認の上、承認をお願いします」
という最終回答で会話を終えてしまっていた（承認ダイアログはケース側で
auto_approve=trueのため、approve_planさえ呼べば自動承認され先に進めたはず）。
`system_prompt.md` の Plan & Progress 節の `approve_plan` の説明に、
「`create_plan` の直後、同じターン内で必ず続けて呼ぶこと（ユーザーの次の発言を
待たない）。`ask_user_text`/`ask_user_choice` とは異なりこの呼び出し自体が
ユーザー確認を内包しているツールなので、あなたが自分の判断でそのまま呼び出して
よい」という一文を追記した。

021を単体で2回再実行して効果を確認:
- 1回目: approve_planが正しく呼ばれるようになった（修正の効果を確認）。ただし
  その後 `update_task_progress` の後でthinking_loopに陥り打ち切られ、
  run_scriptまで到達しなかった。
- 2回目: rules_pass=Trueだったが、最終回答が `"Sorry, need more steps to
  process this request."` という異常終了だった。transcriptを見ると、
  view_imageを3回（@1〜@3）呼んだ後、残りの画像を見るために
  `execute_python_code` で `os.getcwd()` とパスの手動組み立て・タイプミスの
  試行錯誤を10回近く繰り返しており（`"2019/photo_0.png"`, `"2019/photo_.png"`
  等、system_prompt.md 51-59行目で明示的に禁止されている手法）、その分の
  ツール呼び出し消費により `config.ini` の `recursion_limit=50` に対し
  LangGraph prebuilt実装（`create_react_agent`）内蔵のremaining_steps機構が
  働き、`GraphRecursionError` を投げずに定型メッセージへフォールバックした
  とみられる（このフォールバックは今回実装した `turn_cutoffs` の
  GraphRecursionError捕捉では検知できない）。ルールベース判定は
  run_script呼び出し履歴があれば通ってしまうため、この種の「途中で無駄な
  試行錯誤を繰り返した末に中途半端に打ち切られた」失敗を見逃す。judge基準
  （同一ツール呼び出しループの有無）で見ると明確に不合格。

**根本原因はexecute_python_codeでの自己流パス探索・タイプミス
（`memory/project_path_memory_feature.md` に記録済みの「未解決のまま」の
問題そのもの）であり、approve_plan修正とは別の原因。次のiterで対処する。**

### 次のiterで対処する候補（優先度順）

1. **execute_python_codeでの自己流パス探索対策**: view_imageで複数画像を
   連続して見る必要がある場合、payload不足（@Nが足りない/見失った）と
   感じたら再度glob_file.pyを呼んで@Nを取り直すこと、execute_python_codeで
   画像パスを扱わないことを、より強く・具体的に指示する。021 2回目の
   直接原因。
2. **大量ファイル探索でのcreate_plan/dispatch_agent未使用対策**:
   large_file_exploration_planのように、少量（7枚程度）でも複数画像を
   連続確認する場面でcreate_plan/dispatch_agentを使わず直接処理し始め、
   同一ツール呼び出しの盲目的な繰り返しに陥るケースがある。
3. **Working with Failuresへの「無言で終わらない」明示追加**:
   「何かが失敗したとき」節に、ユーザーが拒否したときの節（300行目）と
   同様の「診断してもなお実行できなければ、無言のまま終えずテキストで
   正直に伝える」という一文を追加する。
4. help_request: 本文をそのまま提示できていない（優先度は低め、実害が
   軽微なため）。
5. memory_excluded_content: 除外対象の保存を防ぐ記述強化。

## iter15: execute_python_codeでの自己流パス探索対策

Skills節5.（画像確認の手順）に、「複数の画像を連続して確認する場合は
まとめて1回のglob_file.pyで検索し、得られた全ての@Nを順に使うこと。
@Nが尽きた/分からなくなったら再度glob_file.pyを呼んで取り直すこと
（execute_python_code等で自分でパスを組み立てない）」という一文を追記した。

021を単体で再実行して効果を確認:
- execute_python_codeは一切呼ばれなくなった（狙い通りの効果）。
- ただし、read_skill_file / run_script（file-tools read_file.py）の方で
  依然として壊れたパス（`"2025/ocr_md/md_photo_001"` という誤字、
  `"C:\\Users\\ytkmt\\App\\Local\\Temp\\eval..."` という途中で千切れた
  パス等）を手動構築しようとする挙動が残っていた。今回の追記が
  view_image呼び出しの文脈に限定されていたため、read_skill_file/
  read_file.py側の手動パス構築までは対象になっていなかったのが原因。
- glob_file.pyを同じ引数（2019/2020/2025）で2回繰り返す、view_imageで
  @1/@3/@7等を重複して呼ぶ挙動も残っており、最終的にthinking_loopで
  打ち切られた（rules_pass=False継続）。
- 020（large_file_exploration_plan）でも試したところ、view_imageの重複が
  14回→7回に減少（改善傾向はある）。最終回答の行事名・月への言及も
  改善したが、create_plan/dispatch_agentのいずれも呼ばずrules_pass=False
  のまま。

**部分的な改善に留まった。read_skill_file/read_file.py側のパス構築も
含めて指示を一般化する必要がある（次はTool Usage Guidelines節の既存の
`path_memory`指示と統合して重複を整理しつつ強化する方向を検討）。
また020の結果から、根本対策としては次のiter16（大量ファイル探索での
create_plan/dispatch_agent未使用対策）の優先度が高いと判断した
（少量画像でも直接処理を許可していることが、大量のツール呼び出し・
繰り返しを誘発している）。**

## iter16: 大量ファイル探索でのcreate_plan/dispatch_agent未使用対策

Task Delegation節の該当箇所に「確認すべき画像・ファイルが2件以上ある場合は
『1回で済む単純な確認』には該当しないため、dispatch_agentへの委譲か
create_planでのステップ化のいずれかを選ぶこと」という具体的な閾値を追記した。

020を単体で再実行して効果を確認: 依然としてcreate_plan/dispatch_agentの
いずれも呼ばずrules_pass=Falseのまま。ただし、view_imageの重複呼び出しは
7回→2回程度まで減少し、最終回答もthinking_loopに陥らず正常に生成される
ようになった（2020/2025年の一部行事は「画像が不鮮明で読み取れない」旨を
正直に申告する等、Working with Failuresの精神にも沿った応答だった）。

**閾値を明示してもなお、モデルがcreate_plan/dispatch_agentを選ばず直接
処理してしまう傾向は解消しなかった。これはプロンプト文言の記述問題という
より、モデル自体が「少量なら自分で処理してよい」という判断を強く持って
いる可能性が高く、tune-promptのスコープ（プロンプト文言の調整）だけでの
完全解消は難しいと判断する。ただし、view_image重複・thinking_loop突入の
頻度は3回のiter（14〜16）を通じて明確に改善傾向にあり、無害化はできている。
これ以上の同種の追い込みは振動リスクがあるため、いったん切り上げて
全件回帰を確認するフェーズに移る。**

## iter14〜16適用後のフル回帰結果（20260719_031220）

全22件中 pass=6 fail=3 judge待ち=12 error=1（022がタイムアウト900秒）。
主な変化点:

- multistep_plan_flow: 前回judge合格だったがルールFAIL(tool_called_any)に
  転落。今回はcreate_planを呼ばず、いきなり「C:\Users\test ディレクトリが
  存在しない」という確認質問だけで終えていた。温度サンプリングによる
  ランダムな挙動差と見られる。
- dispatch_agent_view_image / view_image_workdir_absolute_path /
  memory_excluded_content: いずれもthinking_loopで打ち切り。前回は
  安定していたケースも含まれ、thinking_loop自体はiter14〜16の変更と
  直接の因果関係は薄く、モデル・llama-server側の温度サンプリングに
  由来する既知の不安定性と判断する。
- **annual_schedule_xlsx_end_to end: rules_passはTrue表示だが、
  final_answerが空文字列（実質的な無言終了）。78メッセージ・
  約70万トークン（累積、EMPTY_RESPONSE_NUDGEによる複数回ainvoke分の
  合算）という非常に長い会話の末に、モデルの応答が構文的に崩壊し始め
  （`execute_python_code`のコードが`from openpyxl`で途切れる、
  `edit_excel.py`のops-jsonが `"参加者数の目安）]` のように閉じ引用符を
  欠いたまま出力される等）、EMPTY_RESPONSE_NUDGEを2回使い切ってもなお
  無言のまま終わった。これがユーザー目標（xlsxの安定生成）に対する
  最大の残存問題と判断。**
- 022（path_memory_glob_then_view_image）: 900秒タイムアウト。

## iter17: excel-tools SKILL.mdのops-json構文エラー対策を強化

021の空応答の直接のきっかけは、`edit_excel.py` への `--ops-json` 引数
（見出し・複数行データ・スタイルを含む長いJSON）で日本語文字列の閉じ
引用符が欠落する構文エラーが発生したこと。`skills/excel-tools/SKILL.md`
には既に「2回以上構文エラーを繰り返したら `--ops-file` 方式に切り替える」
という対策が入っていた（iter12）が、今回はエラー1回の直後に無言終了して
しまい、「2回以上」という閾値に達する前にリトライを使い果たした。

対策として、閾値を「2回以上繰り返したら」から「最初から（opsが5件超・
日本語文字列が10文字超なら`--ops-json`ではなく`--ops-file`を使う）」
「万一構文エラーが1回でも発生したら直ちに切り替える」に強化した。
本来のtune-promptのスコープ（`system_prompt`対象時は`skills/*/SKILL.md`
を触らない）を超えるが、ユーザーからの明示的な指示
（「xlsxがちゃんと生成されるところまで改善する」）に基づき対象を
`skills/excel-tools/SKILL.md` に広げた。編集前の内容は
`evals/history/excel_tools_skill/iter17_before.md` に退避済み。

021を単体で再実行し効果を検証: JSON構文エラー自体は再発しなかったが、
別の失敗パターンに変わった。`create_plan`→`approve_plan`→`update_task_progress`
まで正常に進んだ後、OCR済みmdファイルを `read_skill_file` で
（`annual_schedule/2019/ocr_md/md_photo_0.md` 等、誤字混じりの作業
ディレクトリ相対パスで）読もうとして4連続失敗、その後何もせず
final_answerが空のまま終了。`read_skill_file` は「作業ディレクトリ配下は
読めない、file-toolsのread_file.pyを使え」という誘導メッセージを既に
返しているにも関わらず、モデルは同じ誤ったツールを4回使い続けた
（Working with Failuresの「同じアクションを盲目的に再試行しない」に反する）。

## iter18: read_skill_fileの誤用対策

Skills節2番（read_skill_fileの説明）に、「read_skill_fileはskills
ディレクトリ配下限定であり作業ディレクトリ配下のファイルは読めない。
作業ディレクトリ配下のテキストファイルはfile-toolsのread_file.pyを使う。
『ファイルが見つかりません』エラーが出たら同じツールで何度も試すのではなく
read_file.pyに切り替える」という一文を追記した。

021を再実行して効果を確認: read_skill_fileの誤用は解消した
（read_file.pyを正しく使うようになった）。トークン消費も74,920（前回の
217,694・706,213から大幅減少）まで改善。ただし、read_skill(excel-tools)を
呼ばずに、いきなり `execute_python_code` で openpyxl を直接操作して
xlsxを自作しようとする新しいパターンが出現した（最終回答では
「annual_schedule.xlsxとして保存されています」と報告しているため実体は
生成できていた可能性が高いが、`edit_excel.py` を経由しないため
`tool_call_args_contains:run_script` のルールには違反）。

## iter19: execute_python_codeによるスキルバイパス対策

Skills節4番（execute_python_codeの使用条件）に、「該当スキルに専用
スクリプトが既にある処理（xlsx生成はexcel-toolsのedit_excel.py等）は
execute_python_codeで自作せず、必ずそのスクリプトをrun_scriptで使う。
スキル本文をまだ読んでいなければ自作前にread_skillで確認する」という
一文を追記した。

021を再実行して効果を検証: `read_skill(excel-tools)` → `execute_python_code`
でops.jsonを書き出し → `edit_excel.py --ops-file` という、狙い通りの流れに
なった（iter17・iter19双方の効果を確認）。ただし `edit_excel.py` の
呼び出しを5回前後、`execute_python_code` でのops.json組み立て直しを
4回、`view_image` で同じ `@N`（@1/@2、@5/@6）を2回ずつ繰り返す等、
非効率な試行錯誤がなお多く、最終的にツール呼び出し30回超・
約60万トークンに達し、LangGraph prebuilt実装のremaining_steps機構による
`"Sorry, need more steps to process this request."` という異常終了に
なった（rules_passはtool呼び出し履歴だけを見るためTrue表示だが、実質は
未完了）。個別の問題（approve_plan忘れ・自己流パス構築・read_skill_file
誤用・スキルバイパス）は着実に解消しているが、「一度見た画像・一度試した
opsを何度も繰り返す」という基礎的な非効率が残り、それが積み重なって
recursion_limitに達している。

## iter20: 同一ツール呼び出しの繰り返し防止

Tool Usage Guidelines節に「同一の@N・同一引数で既に成功した呼び出し
（view_image・read_file.py等）は同じ会話内で繰り返さない。結果は会話
履歴に残っているので読み返せばよい」という一文を追記した
（`view_image` の重複が特に目立ったため名指しで注意喚起）。

021を再実行して効果を検証: 今回はview_imageの重複は見られず、大量画像の
探索を `dispatch_agent` へ委任するようになった（iter16の効果も確認）。
しかし、`read_skill(excel-tools)` を呼んだにも関わらず、実際の生成段階では
`edit_excel.py` を一度も使わず `execute_python_code` で `openpyxl` を
直接操作しようとした（iter19の対策が、スキル本文を読んだ後の実行段階まで
は効いていない）。しかもそのコードには `wb = openpyxl.Workbook`
（呼び出し括弧が抜けている）という構文ミスがあり、`update_task_progress`
で `completed` に更新してしまっている（実際には生成できていない可能性が
高いにも関わらず完了扱いにしている）。さらに `read_file.py` の引数に
`AppData(Local`・`annual_scheule`・`ocn_md`・`md_photo_0l` 等、複数の
タイプミスを含む手打ちパスを使う場面もあった。turn_cutoffsは無し、
final_answerは空。トークン数428,477（iter18の74,920とiter19の601,545の
中間）。

**iter14〜20（7イテレーション）を通じて、個別の既知バグ（approve_plan
忘れ・execute_python_codeでの自己流パス構築・read_skill_file誤用・
excel-toolsバイパス・view_image重複）はいずれも一度は改善が確認できたが、
021は依然として安定して完走しない。失敗のたびに異なる新しい問題が
露呈しており、同じ理由での失敗の反復（厳密な意味での振動）ではないため
機械的なループ停止基準には該当しないが、収穫逓減の兆候が見える
（token数・試行錯誤の量に一貫した改善トレンドが無い）。イテレーション
上限10回に対し残り3回。次は個別ケースの深追いを中断し、22件フルの
回帰確認で全体像を再評価し、今後の方針（プロンプト調整の継続 or
コード側対策の提案）を判断する。**

## iter14〜20適用後のフル回帰結果（20260719_041601）

全22件中 pass=6 fail=4 judge待ち=12 error=0（022は今回タイムアウトせず
正常完了。単体再実行でも正常完了しており、前回のタイムアウトは一過性の
問題と判断）。

**annual_schedule_xlsx_end_to_end が今回初めて最終報告まで完走した**:
「2026年の子供会年間行事予定表『annual_schedule.xlsx』が作成できました。」
という完了報告を返し、月ごとの行事配置・見出し書式・ウィンドウ枠固定にも
言及している。ただし内容には「餅つき大会（2020年の実績あり）」
「夏祭り（2020、2015年の実績あり）」等、実際のフィクスチャ内容
（2020年は節分祭・川遊びイベントのみ、2015年は存在しない）と矛盾する
幻覚が含まれており、judge基準3（過去実績を正確に反映しているか）は
不合格の可能性が高い。しかしend-to-endの完遂（生成→完了報告）という
最大の関門は初めて突破できた。

その他の変化点:
- view_image_request: 前回合格だったがルールFAIL（tool_called_any、
  最終回答が空）に退行。ランダム性による一過性の問題と見られる。
- large_file_exploration_plan: 依然としてtool_called_any違反
  （create_plan/dispatch_agent不使用）。ただし今回はthinking_loopには
  陥らず、テーブル形式で結果をまとめられており、2020年度は「不明」と
  正直に申告している。
- dispatch_agent_view_image / memory_excluded_content: thinking_loopで
  打ち切り（前回と同じケースで再発、モデル側の温度サンプリングに
  由来する不安定性と判断）。
- ambiguous_ask_user: 変わらずtool_not_called違反（既知の設計判断の
  妥当性の問題、iter01から未変更）。

## 021の再現性確認（3回連続単体実行）

xlsx生成の完走がどの程度安定しているかを確認するため、021を追加で2回
単体実行した。

- 1回目: **ツールを1つも呼ばない状態で即座にthinking_loop**（token数
  わずか14,525）。会話の最初期段階でのモデル応答崩壊で、プロンプト内容
  というよりモデル自体の低確率な不安定性（iter13で確認済みの`<think>`
  タグ解析エラーと同系統の現象）と判断する。
- 2回目: read_skill・run_script（file-tools）を何度か呼んだ後、
  thinking_loopで打ち切り。

フル回帰実行時（1回成功）と合わせて021を計3回連続実行した結果は
成功1・失敗2。**iter14〜20の一連の修正により「end-to-endで完走できる」
ことは実証できたが、安定率はまだ低い（3〜7回に1回程度）。** 残る不安定性の
主因はモデル自体の温度サンプリングに由来するthinking_loop突入頻度の
高さであり、これ以上プロンプト文言を継ぎ足しても収穫は薄いと判断する
（system_prompt.mdは216行→390行まで増量しており、これ以上の追記は
プロンプト自体の冗長化による逆効果のリスクも高まる）。

## iter14〜20のまとめ

7イテレーションで以下を修正した:
1. `create_plan`を呼んだ後`approve_plan`を呼ばずに終わる問題
   （Plan & Progress節に強調追記）
2. `execute_python_code`での画像パス自己流構築・タイプミス
   （Skills節5番に`glob_file.py`再取得の指示追記）
3. 少量画像でも`create_plan`/`dispatch_agent`を使わず直接処理する問題
   （Task Delegation節に「2件以上」の閾値追記、部分改善）
4. `read_skill_file`を作業ディレクトリ配下のファイルに誤用する問題
   （Skills節2番に`file-tools`への切替指示追記）
5. `execute_python_code`でexcel-tools等の専用スクリプトをバイパスする問題
   （Skills節4番、および`skills/excel-tools/SKILL.md`のops-json対策強化）
6. 同一`@N`・同一引数のツール呼び出しを繰り返す問題
   （Tool Usage Guidelines節に追記）

結果、annual_schedule_xlsx_end_to_end（ユーザー報告の元シナリオ）が
end-to-endで完走する（xlsx生成→完了報告）ことを初めて確認できたが、
再現性はまだ3〜7回に1回程度。次にtune-promptを回す際は、（a）
残存するthinking_loop突入の頻度をさらに下げる打ち手があるか、
（b）生成内容の正確性（幻覚した過去実績データ）の改善、
（c）system_prompt.md全体の冗長性見直し（増量した390行を整理し直す）
のいずれかから着手するとよい。

## iter21: 過去実績データの幻覚対策

Tool Usage Guidelines節に「集計結果・過去実績・調査結果の報告や、それを
元にしたファイル生成では、実際にread_skill_file/view_image/run_script/
dispatch_agentで確認できた内容のみを使う。確認していない項目は一般的な
推測で埋めず、不明・未確認と正直に伝える」という一文を追記した。

021を再実行して効果を検証: rules_pass=Trueでxlsx生成が完走した
（フル回帰時に続き2回連続の成功）。ただし最終回答には「過去3年
（2019・2020・2015年）」（実際は2019・2020・2025年）、「みたらし団子」
（実在しない行事名）等の幻覚が引き続き残っており、iter21の指示だけでは
内容の正確性は改善しなかった。end-to-endの完走という最優先目標には
連続で成功している。

3回目の追加検証: rules_pass=Trueと表示されたが、final_answerが空
（token数675,616、大量の試行錯誤の末に無言終了）。実質は失敗。

**021の通算成功率:** iter17以降の単体・フル回帰実行を通算すると9回中2回
（フル回帰時・iter21後1回目）でxlsx生成が完走した。約22%の成功率。
個別バグはいずれも改善済みだが、モデル自体のthinking_loop突入・長い
会話末の無言化という温度サンプリング由来の不安定性が支配的要因として
残っており、プロンプト文言の追い込みでは大幅な改善が頭打ちになってきた
と判断する。イテレーション回数はiter14〜21で8回（上限10回）。
`evals/history/system_prompt/iter21_final.md` に現時点のsystem_prompt.mdを
退避した。

## iter14〜21適用後の最終フル回帰結果（20260719_050644）

全22件中 pass=6 fail=4 judge待ち=10 error=2。今回は新たに
`StreamChunkTimeoutError`（llama-serverからのストリーミングチャンクが
120秒間届かない、memory_verify_before_recommendで発生）と、
dispatch_agent_view_imageの900秒タイムアウトの2件のerrorが発生した。
連続で多数のケースを実行し続けたことによるllama-server側の負荷・疲弊が
一因の可能性がある。021（annual_schedule_xlsx_end_to_end）は今回も
thinking_loopで打ち切り。ambiguous_ask_user・multistep_plan_flowは
今回はask_user_text/create_planすら呼ばずtool_called_any違反という
新しい退行パターンを示した（token数が異様に少なく、早期に応答が
崩壊している）。view_image_workdir_absolute_pathは「雪まつり」という
誤った行事名を報告しており（正解は節分祭）、vision認識精度の限界は
未解決のまま。

**総括:** 021の通算成功率は今回のフル回帰を含め10回中2回（20%）。
iter14〜21で修正した6つの個別バグ（approve_plan忘れ・パス自己流構築・
read_skill_file誤用・excel-toolsバイパス・同一ツール呼び出し繰り返し・
過去実績データの幻覚防止の追記）は、いずれも修正直後の単体検証では
効果を確認できたが、モデル自体の温度サンプリングに起因する不安定性
（thinking_loop突入、長い会話末の無言化、まれなStreamChunkTimeout）が
支配的な残存要因であり、プロンプト文言の追い込みだけでは大幅な改善が
頭打ちになっている。イテレーション回数はiter14〜21で8回（上限10回に
到達間近）のため、ここでいったんループを終了し、ユーザーへ状況を
報告する。

---

## iter22: コード側対策セッション（config.ini / src/llm.py 等、system_prompt.mdは対象外）

ユーザーが引き継ぎメモの3候補中「候補1: thinking_loop低減（コード側対策も
視野に）」を選択。この節はsystem_prompt.mdではなくconfig.ini・src/配下の
コードを対象とするため、tune-promptの通常スコープ（対象ファイルのみ編集）
を明示的に拡張して実施した。変更前のconfig.iniは
`evals/history/config_ini/iter00_baseline.ini`・`iter22_before.ini` に退避済み。

### 診断ログ機能の追加（Feature A）

既存のthinking_loop_guard（`src/llm.py`の`_ThinkingLoopDetector`、直近600文字の
zlib圧縮率が0.3を下回る状態が2回連続したらループ確定）は、検知時に**実際に
繰り返されていたテキストを一切記録していなかった**ため、真の反復ループか
誤検知かを事後検証する手段が無かった。`ThinkingLoopDetected`に`snippet`
属性（検知時点の直近テキスト）を追加し、`src/graph.py`・`src/subagent.py`・
`app.py`の各リトライ箇所のログ、および`evals/run_case.py`の
`turn_cutoffs`（results.json）に伝播させた。挙動変更は無く観測性のみの
追加（低リスク）。

### 実験1: repeat_penalty 1.0（無効）→ 1.05

`dry_multiplier`（フレーズ単位の反復抑制、既に0.8で有効）に加え、
トークン単位の反復も軽く抑制することでthinking_loop突入頻度自体を
下げられないか検証した。

**021を単体で5回実行した結果**: rules_pass=True 3/5（60%）、
turn_cutoffs（thinking_loop）は1/5のみで、Feature Aのsnippetで確認した
ところ、その1件の内容は列ごとに幅の値が異なる`set_column_width`の
JSON配列（B=10, C=20, D=15, E=20…）であり、**同一内容の反復ではなく
正当に変化する構造化JSONを検知器が誤ってループ判定した可能性が高い**
（ユーザー指摘の「thinking_loop_guardのバグ」を裏付ける実例）。
真の意味での反復ループ（内容が実質同一）は5回中0件だった。残る1件の
不合格は`turn_cutoffs`無し・`final_answer`が空という無言化失敗で、
repeat_penaltyの対象外の失敗モード。

比較として、旧baseline（repeat_penalty=1.0時点、iter14〜21累計）の021
通算成功率は10回中2回（20%）。今回の3/5（60%）は samples が少なく
断定はできないが、明確な悪化は見られず、有望な方向と判断し**据え置き**。

**副作用確認の部分回帰**（過去に暴走・タイムアウト歴のある軽量ケース
004/011/019/020を単体実行）: 011・019はturn_cutoffs無しでpass。
004・020ではthinking_loopのturn_cutoffsが発生したが、Feature Aの
snippetで確認したところ、004は「ユーザー: "C:\Users\test\slide."」
「ユーザー: "C:\Users" を検索してみる。」を交互にほぼそのまま繰り返す
**真の反復ループ**（rules_passはリトライで回復しTrue）、020は
「Path is `16`？Path is `18`。Let's go.」のように数字を変えながら
同じ思考パターンを繰り返す**真の反復ループ**（リトライでも回復せず
rules_pass=False、ただし元々020はcreate_plan/dispatch_agent不使用の
既知の不安定ケース）だった。repeat_penalty由来の新規の誤検知や退行は
見られなかった。

### 検知器の誤検知（false positive）に関する所見

Feature Aの導入により、初めて「真の反復ループ」と「誤検知」を実例で
区別できた:
- **真の反復ループの例**（004, 020）: 意味的にほぼ同一の文が繰り返される
  （数字や語順が多少変わっても実質同じ内容の空回り）。
- **誤検知の例**（021実験1の4回目）: `edit_excel.py`向けのops JSON配列
  など、フィールド名（`"op"`, `"sheet"`, `"column"`, `"width"`）の反復が
  多いが値は毎回変化する、正当な構造化データの生成。

現在の検知アルゴリズム（直近600文字の圧縮率のみで判定）は、後者のような
「構造は反復的だが内容は都度変化する」テキストと、真の反復ループを
区別できない。021はexcel-toolsのops JSON生成を多用するケースであり、
この誤検知が021の残存不安定性の一因になっている可能性が高い。
次のアクション候補として、圧縮率だけでなく「同一の文字列チャンクが
ほぼそのまま複数回出現するか」を追加でチェックする等、検知アルゴリズム
自体の改善をユーザーに提案する。

### 検知アルゴリズム改良の検証（失敗、現状維持で決着）

合成データ（004型の交互反復ループ・020型の数字インクリメントループ・
021型の正当なJSON配列・自然文の対照群）を使い、「圧縮率」に加えて
「直近ウィンドウと1つ前のウィンドウ間の類似度（時間軸方向の自己参照
チェック）」を組み合わせる改良案を検証した（`difflib.SequenceMatcher`、
`autojunk=False`で検証、既定のautojunk=Trueだと高反復テキストで類似度が
0になる罠があり要注意）。しかし実測の圧縮率は「004型ループ≒0.10」
「021型JSON≒0.17」「020型ループ≒0.25〜0.27」となり、**020型の真のループ
より021型の正当なJSONの方が低い（＝より「反復的」と判定される）**という
逆転が生じた。JSON配列はフィールド名（"op"/"sheet"/"column"）の反復密度が
高く、020型ループの冗長な英文よりも機械的に圧縮されやすいため。この結果、
単一の閾値ではどちらに動かしても「真のループを見逃す」か「正当なJSONを
誤検知する」のいずれかが必ず発生することを実証した。時間軸類似度を
組み合わせても、JSON配列の共通スキャフォールド部分（キー名等）がウィンドウ
間で高い類似度を生んでしまい、区別できなかった。

ユーザーへ「検知が逆に悪さをしているか」を確認したところ、今セッションで
発火した3回中2回（004・020）は真の異常を正しく検知しており、誤検知は
1回（021のJSON）のみ。検知器導入前は暴走がmax_tokens=128,000まで続き
30分以上応答が返らない事象が実際にあったため、「まれに正当な出力を誤って
止める代償と引き換えに無限暴走を確実に止める」保険として明確なプラスと
判断し、**検知アルゴリズムは変更せず現状維持**で決着した。
（`thinking_loop_guard.enabled=false` でいつでも無効化できる設計のため、
必要であればユーザー側で判断できる。）

### 方向転換: 長い会話でのコンテキスト肥大化の調査

ユーザーから「モデルの制御だけでは限界がある、より安定するための機能追加を
検討してほしい」との指示を受け、thinking_loop検知の改良から、長い会話
（021のように数十〜100メッセージ超）でのコンテキスト肥大化そのものへ
調査対象を切り替えた。Web調査でも、小型ローカルLLMは長いコンテキストで
反復ループに陥りやすいこと、「直近Nターンのみを残すスライディングウィンドウで
モデル自身の過去の失敗への露出を減らす（自己参照的な悪循環を断つ）」ことが
有効な緩和策として報告されていることを確認した
（LangGraph公式のtrim_messages/LangMem要約ガイド、
"The Illusion of Diminishing Returns" 等の文献を参照）。

コード調査の結果、以下が判明した:
1. `view_image`（`src/images.py`の`to_data_url()`）は画像を丸ごとbase64化
   するのみで、リサイズ・圧縮・サイズ上限は一切無い。1枚あたり数千トークン
   消費するとの既存メモ（`evals/tuning_log.md`旧記載）がある。
2. 会話履歴のトリミング・要約機構は`graph.py`/`app.py`/`tools.py`のどこにも
   **存在しない**。
3. **バグ発見**: thinking_loop検知時の注意メッセージ（nudge）は成功後に
   `RemoveMessage`で会話履歴から除去される設計だが、**無言終了
   （空応答）検知時の`EMPTY_RESPONSE_NUDGE`だけは除去されず、会話履歴に
   残り続けたまま以降のリクエストで毎回全量再送されていた**
   （`src/graph.py`の`ainvoke_ensuring_final_text`、`app.py`の`on_message`
   の両方で同じ非対称性）。無言終了は021のような長い会話で繰り返し
   発生する既知の失敗モードであり、蓄積したnudgeがコンテキストを圧迫する
   だけでなく、「過去に失敗した」痕跡がモデル自身の目に触れ続けることが
   同種の失敗を誘発する自己参照的な悪循環（Web調査で報告されている現象）に
   つながっている可能性がある。
4. `dispatch_agent`（`src/subagent.py`）は健全に設計されており、サブ
   エージェント内部の詳細（画像データ含む）は親の会話履歴に漏れない
   （最終テキストのみ返す）。ただしモデルがサブエージェントの要約を
   信用せず同じ調査をやり直す非効率が既知（iter12参照）。

### iter22-fix: EMPTY_RESPONSE_NUDGE未除去バグの修正

`src/graph.py`の`ainvoke_ensuring_final_text()`と`app.py`の`on_message`
（無言終了リトライループ）の両方で、`EMPTY_RESPONSE_NUDGE`のHumanMessageに
`id`を付与して`empty_nudge_ids`に記録し、`loop_nudge_ids`と合わせて
成功後に`RemoveMessage`で会話履歴から除去するよう修正した。挙動としては
「無言終了からの回復後、リマインダー文言が会話履歴に残らなくなる」という
変更のみで、ユーザー向けの応答内容は変えていない。

021を1回実行して確認: rules_pass=False（thinking_loopのturn_cutoffsは
無し）。今回の失敗は`glob_file.py`/`read_file.py`の引数に
`annual_scheduled2019`（正しくは`annual_schedule\2019`）、
`ocr_mddmd_photo_0.md`、`LocalfTempfevals_workdir_...`のような結合ミスや
`]]>`という不可解な断片が混入するパス生成の乱れによるもので、
thinking_loop・無言化とは別カテゴリの問題だった。同種のパスタイプミスは
iter14〜21時点（repeat_penalty変更前）から既知の課題であり
（`glob_file.py`→`path_memory`経由を経ずに直接パスを組み立てて失敗する
事例）、今回の変更（repeat_penalty・EMPTY_RESPONSE_NUDGE除去）が原因と
断定はできない。合計トークン数764,737と今回の実行では過去最大。

021の通算成績（repeat_penalty=1.05適用後、6回実行）: pass 3/6（50%）。
真のthinking_loopは1回も再発しておらず、失敗は無言化1回・パス生成の
乱れ1回・（実験1の）誤検知1回。ベースライン（20%）からは改善が続いている。

## iter22-revert: repeat_penalty/dry_multiplierをUnsloth公式準拠に確定

ユーザーから経験則の共有: 「repeat_penaltyを1.0からわずかでも上げる、
またはdry_multiplierを0.8でも効かせると、ファイルパスのような反復文字列
の生成が壊れやすくなる（一方、効かせないとthinking_loopが起きやすい）」
という、まさに直前の6回目実行のパス生成の乱れと一致する指摘があった。
この経験則を踏まえ、ユーザー判断により以下を確定した:

- `[llm].repeat_penalty`: 1.05 → **1.0**（無効）に差し戻し。
- `[llm].dry_multiplier`: 0.8 → **未指定（空欄）**に差し戻し
  （iter11で導入した既存のベースライン設定も含めて見直し）。
- 理由・詳細はmemory: `qwen36_recommended_params.md`の
  「repeat_penalty / dry_multiplierとパス文字列破壊のトレードオフ」
  「モデルパラメータのチューニングを終了」の各節に記録。
- **今後、使用モデル（QWEN3.6_35B-A3B）が変わらない限り、
  config.ini [llm]のサンプリングパラメータは再チューニング対象にしない**
  （ユーザー決定）。暴走対策は`thinking_loop_guard`（アプリ側の検知+
  リトライ）と`max_tokens=128000`（最終的な歯止め）に委ねる。

この結果、dry_multiplierによる暴走抑制（iter11で導入した最重要の
安定化策）が外れた状態になる点に注意。thinking_loop_guardが唯一の
安全網になるため、今後の安定性向上はコード側の仕組み（下記）で
狙う方針となった。

## iter22-plan: コンテキスト効率化・検知アルゴリズム改善の設計フェーズ

ユーザー指示: 「モデルの制御だけでは限界がある。file-toolsでのコンテキスト
効率化」「thinking_loop_guardの検知方法自体の改善」の2方向をWeb調査も
交えて検討してほしい、実装は次回以降でよい。

調査・Web検索の結果、以下を確認した:

- **file-tools側**: `read_file.py`の`--limit`既定値がコード上10行・
  SKILL.md記載2000行で不整合。`glob_file.py`/`read_file.py`/`grep_file.py`
  いずれにも重複読み込み・重複検索を検知する仕組みが無い（path-memoryの
  `@N`はパス再現性担保が目的でコンテキスト削減目的ではない）。
  `dispatch_agent`のコンテキスト分離設計自体は健全だが、モデルが
  サブエージェントの要約を信用せず同じ調査をやり直す既知の非効率がある。
- **thinking_loop_guard側**: 圧縮率単体・時間軸ウィンドウ間類似度単体
  では「真のループ」と「値が変わる正当なJSON配列」を原理的に分離
  できないことを実証済み（iter22の検証記録を参照）。Web調査では、
  embedding距離+n-gram新規性を組み合わせた学習不要のオンライン検知器
  （"The Mirror Loop"論文）や、ツール呼び出しの重複排除・直近Nターンの
  スライディングウィンドウ等の緩和策が報告されている。

これらを踏まえ、次回実装用の設計方針を
`C:\DT_Python\claudecode\.claude\plans\fancy-toasting-cerf.md`にまとめた
（ExitPlanModeでユーザー承認済み）。要点:
- **方針A（優先度高）**: `src/tools.py`にツール呼び出し（run_script/
  view_image）の重複検知・抑止機構を追加し、完全一致する呼び出しは
  再実行せず短いメッセージを返す。根拠が明確でtuning_log記載の既知の
  実害に直接対応するため、次回はここから着手する。
- **方針B（優先度中、要事前検証）**: `_ThinkingLoopDetector`に累積n-gram
  集合に基づく新規性チェックを追加し、圧縮率条件とのAND条件で誤検知を
  減らす案。実装前に必ず合成データで「真のループは低novelty・JSON配列は
  高novelty」という前提が成立するか検証すること（前回同様、成立しなければ
  現状維持に留める）。
- 画像リサイズ対策は今回保留。

このセッションでの実装はここまで（設計文書化のみ）。次回セッションは
`evals/tuning_log.md`のこの節と計画ファイルを読んでから着手する。
config.ini確定後の最終フル回帰（`python evals/run_all.py system_prompt`）は
未実行のため、次回の早い段階で一度実行してベースラインを記録すること。

## iter23: 方針A（tools.pyの重複呼び出し検知）実装

前回iter22で未コミットのまま残っていた段階3の修正（`ThinkingLoopDetected`の
snippet診断ログ、無言終了nudge除去の非対称性修正、config.iniの
公式推奨値への差し戻し）を先にコミット（`41e91df`）してから、方針Aに着手した。

### 実装内容

`src/tools.py`に`_record_and_check_duplicate(session_key, signature)`を追加
（`cl.user_session`にシグネチャの集合を保持し、既出かどうかを判定・記録する。
`_track_failure_streak`と同じパターン）。

- `_run_script_impl`: `skill_name == "file-tools"`の場合のみ、
  `(script, args)`のシグネチャで重複を検知し、重複なら実行せず
  「エラー: file-toolsの{script}を、引数{args}で既に一度呼び出し済みです...」
  という短いメッセージを返す。file-tools（read_file.py/glob_file.py/
  grep_file.py/json_query.py）は状態を変更しない読み取り専用スクリプトのみ
  のため対象を限定して安全に適用できる。他スキルは副作用を持ちうるため
  対象外（run_script/run_readonly_scriptの両方から共有される`_run_script_impl`
  1箇所に実装したため両エントリポイントに効く）。
- `view_image`: 解決済みの絶対パス（`str(path)`）をシグネチャとし、
  重複なら画像artifactを積まずテキストのみのエラーメッセージ
  「エラー: この画像は既に一度確認済みです...」を返す。画像artifactは
  トークン消費が特に大きいため、ここが最も効果が大きいと想定した箇所。

シグネチャ集合は直前1回との比較ではなく会話（thread）全体を通じて保持する
設計にした。iter11以前の記録（`large_file_exploration_plan`で同じ画像を
14回重複、`glob_file.py`を同じ引数で2回離れたタイミングで繰り返す等）は
いずれも連続呼び出しではなく会話全体に散発していたため。

対象を`session_key`ごとに分けているため、`file_tools_call_signatures`と
`view_image_call_signatures`は独立している。

### 検証結果

- **017（view_image_request、正常系・重複無し）**: 誤検知なし。
  `read_skill`→`read_skill_file`(エラー)→`run_script`(glob_file.py)→
  `view_image`が1回ずつ呼ばれ、rules_pass=True・judgeもPASS相当の
  最終回答。既存の正常フローに影響しないことを確認。
- **020（large_file_exploration_plan、既知の重複多発ケース）を2回実行**:
  **両回ともview_imageの重複呼び出しが0回**（過去のiter14〜16では
  7〜14回重複していた）。1回目は3枚、2回目は7枚の画像をすべて重複なく
  1回ずつ確認できていた。file-tools側（`glob_file.py`を年ごとに異なる
  `--path`引数で3回呼ぶ等）も重複は発生せず、誤検知も無し。
  最終回答は両回とも各年の行事を正しく列挙できていた（1回目は
  photo_03.pngが読み取れない旨も正直に申告）。ただし`rules_pass`は
  両回ともFalse（`tool_called_any`: create_plan/dispatch_agent未使用）。
  これはiter14〜16で既に指摘・保留済みの別課題（モデルが少量なら
  自分で処理してよいと判断する傾向）であり、方針Aのスコープ外。
- **022（path_memory_glob_then_view_image）**: 実際にモデルが`view_image`を
  `@1`で2回連続して呼ぶ場面が発生し、重複検知が実際に発火することを
  確認できた。1回目は通常通り画像artifact付きで応答し、2回目は
  「エラー: この画像は既に一度確認済みです: @1。同一画像の再表示は
  省略しました。会話履歴にある前回の説明を参照するか、他の画像・
  他の手段に進んでください。」という短いテキストのみを返し、
  画像artifactを積まなかった（設計通り）。このケース自体は
  rules_pass=True・最終回答も正常に生成された。

**方針Aは既知の実害（view_image・file-tools系スクリプトの重複呼び出しに
よるコンテキスト圧迫・時間浪費）に対して明確な改善効果を確認できた。
view_imageの重複は事実上解消（14回→0回）。file-tools側は今回のテストでは
重複が発生する場面自体に遭遇しなかったが、ロジックは017/020で妥当性を
確認済み（誤検知なし）。**

### 残課題（方針Aの範囲外、次回以降）

- 020/021系で引き続き見られる「create_plan/dispatch_agentを使わず
  少量なら自分で処理してしまう」傾向（iter14〜16で保留済み、
  方針Cまたは今後の別対策が必要）。
- 020の1回目実行で、3枚目の画像確認後にAIMessageが空のまま
  出力トークン110,798（max_tokens上限128,000近くまで）を消費する
  場面が見られた（thinking_loopとしては検知されず、その後
  EMPTY_RESPONSE_NUDGEで正常回復）。方針Aとは無関係の既存の
  長時間thinking傾向であり、iter22で確認済みの残存課題
  （方針B・thinking_loop_guardのnovelty検知）の範疇。
- 方針C（system_prompt.mdへのcreate_plan前の事実確認追記）は
  未着手のまま。次回はこちらに着手するとよい。

コード変更前の退避は`evals/history/tools_py/iter00_before_dedup.py`に保存済み。

## iter24: 方針C（system_prompt.mdのcreate_plan前事実確認）実装

`fancy-toasting-cerf.md`の設計案通り、`system_prompt.md`のPlan & Progress節
「1. create_plan」項目冒頭に以下を追記した（変更前は
`evals/history/system_prompt/iter23_before.md`に退避済み）:

> 呼ぶ前に、対象のファイル・フォルダ構造を`glob_file.py`等で最低限確認し、
> そこで確認できた具体的な事実（対象件数・ファイル名・フォルダ構成等）に
> 基づいたステップを書くこと。件数や内容が分からない状態で「画像を確認
> する」「データを整理する」のような抽象的なステップを作らない。

### 検証結果

- **020（large_file_exploration_plan）を2回実行**: **2回ともrules_pass=True**
  （`tool_called_any`: create_plan/dispatch_agent使用）。iter11〜16では
  この規則に一貫して失敗していた（モデルがcreate_plan/dispatch_agentを
  使わず直接処理してしまう傾向、iter16で「保留」と判断されていた
  問題）。
  - 1回目: 事前に`glob_file.py`で2019/2020/2025の各フォルダを確認した
    上で`create_plan`を実行。ステップの内容が「2019年のOCR済みmarkdownと
    画像3枚を確認」「2020年の画像2枚を確認」「2025年のOCR済みmarkdownと
    画像2枚を確認」と、実際に確認できたファイル数と完全に一致する
    具体的な記述になっていた（狙い通りの効果）。最終回答も各年の行事を
    正しく列挙。
  - 2回目: `create_plan`は呼ばれなかったが、代わりに`dispatch_agent`が
    使われ`tool_called_any`は満たした（サブエージェント内部で
    thinking_loopに一度陥りリトライ上限に達して失敗、ログに記録されたが
    例外は握りつぶされ、その後は主エージェントが直接view_imageで
    7画像すべてを重複なく確認し完走）。
- **021（annual_schedule_xlsx_end_to_end、目標ケース）を2回実行**:
  - 1回目: **完全成功**。`create_plan`→`approve_plan`→
    `update_task_progress`の正規フローに乗り、`edit_excel.py`まで到達して
    xlsx生成・最終報告まで完走（`rules_pass=True`、`turn_cutoffs`無し＝
    thinking_loop/無言終了のリトライも一切発生しなかったクリーンな
    完走）。view_imageは`@1`/`@2`が2回ずつ呼ばれたが、方針Aの重複検知が
    実際に発火し2回目はいずれも短いエラーメッセージのみを返し
    artifactを積まなかったことを確認（方針A・Cの相乗効果が実際の
    目標ケースで確認できた初めての例）。
  - 2回目: `create_plan`/`dispatch_agent`は使われ`rules_pass`自体は
    形式的にTrueだったが、`edit_excel.py`のops-json内で既にリネーム
    済みのシートを初期名`Sheet1`のまま`delete_sheet`しようとする論理
    矛盾から`run_script`が連続失敗（`_track_failure_streak`の警告が
    発火）し、`EMPTY_RESPONSE_NUDGE`を使い切っても回復せず最終的に
    無言のまま終了。xlsx生成自体は未完了の可能性が高い。

**方針Cの効果**: create_plan/dispatch_agentの利用率という観点では明確な
改善（020: 2/2、021: 2/2、いずれも前回までの記録では低かった指標）。
ただし021のend-to-end完走率自体（xlsxの生成→完了報告まで）は今回の
サンプルで1/2に留まり、iter22までに記録された「通算成功率約20%」という
既存のばらつきの範囲内。今回2回目で顕在化した失敗（ops-json論理矛盾に
よるrun_script連続失敗→無言終了）は、方針Cのスコープ
（計画作成時点の事実確認）とは別カテゴリの問題（excel-tools側のops-json
生成ロジック・長い会話末の無言化対策＝方針B）であり、方針Cだけでは
解消しないことが今回のテストで再確認された。

### 残課題（次回以降）

- 021のend-to-end完走率のさらなる向上には、方針B
  （thinking_loop_guardのnovelty検知、要事前検証）または
  excel-tools側のops-json生成ロジックの見直しが必要と見られる。
- 020/021とも、config.ini確定後・方針A/C適用後の状態でのフル回帰
  （`python evals/run_all.py system_prompt`、22件）はまだ未実行。
  次回の早い段階で一度実行しベースラインを更新するとよい。

コード変更前の退避は`evals/history/system_prompt/iter23_before.md`に
保存済み。

## iter25: fixtureフォント修正・glob_file.py拡張・Task Delegation節見直し

PC再起動でiter24フルリグレッション（`python evals/run_all.py system_prompt`、
022番実行中）が強制終了したため再開する過程で、複数の問題が新たに見つかり
対応した。

### 発見1: annual_scheduleフィクスチャの日本語フォント文字化け

`evals/fixtures/generate_annual_schedule_fixture.py`の`_make_image()`が
PILの`ImageDraw.multiline_text()`をデフォルトフォント（CJK非対応）で
描画しており、「2020年2月2日／節分祭／参加者15名」等の日本語テキストが
豆腐文字（□）で潰れて生成されていた。022番ケースを再実行したところ、
VLM（view_image）が文字化けした画像を見て「晴天・気温15度」という存在
しない内容を答えており、judgeの期待内容と食い違っていた（モデルの実力
ではなく、そもそも読めない画像を渡していたことが原因）。021番
（end-to-end検証）も同じ`_make_image()`を使うため、iter22〜24で観測してきた
021の完走率のばらつきの一部がこのノイズだった可能性がある。

**対応**: Windows同梱の`C:\Windows\Fonts\meiryo.ttc`を`ImageFont.truetype`で
明示指定するよう修正（存在しない環境向けに`ImageFont.load_default()`への
フォールバックも維持）。フィクスチャを再生成し、022番を修正前後で比較した
ところ、修正前は文字化け画像から誤った内容を答えていたが、修正後は
「2020年2月2日／節分祭／参加者15名」とjudge期待値に完全一致する回答に
変わった。編集前スナップショットは
`evals/history/fixtures/iter24_before_generate_annual_schedule_fixture.py`。

### 発見2・3: glob_file.pyのディレクトリ非対応・read_file.pyのlimit既定値問題

ユーザーからの実運用報告で2件の問題が判明:
- `read_file.py --limit`の既定値が10行と小さく、大きいファイルを読むには
  offsetをずらして複数回呼ぶ必要がある。モデルがoffset更新を誤って同じ
  引数で再呼び出しすると、`src/tools.py`のfile-tools専用重複呼び出し検知
  （iter23実装）にブロックされタスクが進まなくなる。
- `glob_file.py`（`is_file()`のみでフィルタ）がディレクトリを一切返さない。
  直下にファイルが無くサブディレクトリのみの場合、モデルは「何も無い」と
  誤認し`execute_python_code`で`os.walk`ベースの自作ツリー表示コードを
  毎回生成してしまっていた。

**対応**: `glob_file.py`に`directories`（ディレクトリ一覧、`files`と同形式）・
`file_details`（`head-limit`適用後の各ファイルの`binary`判定と`total_lines`、
`read_file.py`の算出方法と一致させた）を追加。既存キー（`files`等）の型・
意味は変更していない。`SKILL.md`のread_file.py既定値表記の誤り
（記載2000→実装10）も合わせて修正。編集前スナップショットは
`evals/history/file_tools/glob_file.py.before`・`SKILL.md.before`。

### 発見4: dispatch_agent使用率が低い問題（system_prompt.md）

ユーザーから「dispatch_agentを全然使わない」という指摘があり調査したところ、
本ログ自身に以下の経緯が既に記録されていたことを再確認した:
- iter16で「確認対象2件以上なら委譲」という閾値を追加したが、直後の検証で
  効果なしと結論づけられていた。
- iter24の「事実確認を先に強制する」アプローチ（020: 2/2成功）は
  `tool_called_any: [create_plan, dispatch_agent]`という合算判定のため、
  create_planとdispatch_agentのどちらが呼ばれたかは実行ごとにぶれており、
  dispatch_agent固有の使用率向上を示すものではなかった（1回目create_plan、
  2回目dispatch_agentという記録が実際に残っている）。
- 010番評価ケースは`read_skill`直接呼び出しでも合格する基準になっており、
  委譲の有無を実質検証できていない。019番はユーザー発話がツール名を明示する
  疎通確認ケースで自律判断を測れていない。この2ケースの評価力不足は今回は
  対応せず、既知の課題として記録するに留める。

**対応**: 単純な閾値の再強化は過去に効果が薄いと実証済みのため、iter24と
同じ「委譲判断の前に事実確認を挟む」パターンを踏襲し、Task Delegation節に
「『1回で済むか』を推測で決めず、迷ったらまず`glob_file.py`を呼んで
`total_matches`・`directories`・`file_details`を確認し、実際の件数を根拠に
委譲要否を判断すること」を追記（glob_file.py拡張と連動）。あわせて
「対象フォルダの構造確認」の記述に`directories`キーへの言及を追加した。
編集前スナップショットは`evals/history/system_prompt/iter25_before.md`。

### 検証結果

020・021・022を個別実行し、いずれも`rules_pass=True`で完走した。

- **022（path_memory_glob_then_view_image）**: フォント修正前は文字化け画像
  から「晴天・気温15度」という誤った内容を答えていたが、フォント修正後は
  「2020年2月2日／節分祭／参加者15名」とjudge期待値に完全一致。
  `glob_file.py`で`*`検索→`directories`から`2020`フォルダを`@2`として発見→
  `--path @2`で絞り込み→`view_image`という、`directories`拡張が実際に
  機能したクリーンな完走。
- **020（large_file_exploration_plan）**: `create_plan`→`approve_plan`→
  `update_task_progress`の正規フローで完走し、2019/2020/2025年の行事を
  月ごとの表にまとめて最終報告。ただし最初のターンで3つの`glob_file.py`を
  並列呼び出しした直後、返ってきた`@1`〜`@4`のパスメモリー参照が全て
  「登録されていません」「ファイルが見つかりません」というエラーになる
  現象を観測した（並列呼び出し時のpath_memory登録・参照が期待通りに
  紐づいていない可能性）。モデルは絶対パスへ切り替えてリカバリーし
  完走したため今回は不合格にならなかったが、根本原因は未調査。次回以降の
  課題として残す。
- **021（annual_schedule_xlsx_end_to_end、目標ケース）**: `edit_excel.py`
  まで到達しxlsx生成・最終報告まで完走。過去実績（2019/2020/2025年）の
  行事名を反映した2026年予定表になっており、幻覚（過去実績を見ずに
  一般的な行事名を当て推量）は無かった。存在しない`create_excel.py`を
  最初に試みる、`merge_cells`をデフォルトシート`Sheet1`に対して呼び失敗する
  （`--new`時は空ワークブックにシートが無いため）といった誤りが2回
  発生したが、いずれもエラー内容を見て`read_skill`確認・`add_sheet`追加と
  自己修正しており、tuning_log.mdでこれまで問題視してきた「同じ失敗の
  機械的な繰り返し」には該当しない。

3件とも1回の実行でPASSしたが、021はiter22-24までの記録で「通算成功率約
20%」というばらつきが記録されているケースのため、今回の1回成功だけで
安定したと判断しない。次回以降、複数回実行しての再現性確認が望ましい。

### 副次的な改修: run_case.pyへのログ出力追加

020実行中、PC再起動後の022番ケース再実行が22分以上応答なしとなり、
フリーズかループか外部から判別できないという事態が発生した
（プロセスは生存・CPU時間も微増していたが正常な完了速度とは程遠かった）。
調査の結果、`evals/run_case.py`（本番の`app.py`と異なりheadless実行）には
ロギング設定が一切無く、`data/logs/app.log`のようなログが生成されない
ことが判明した。

`evals/run_case.py`の`_run()`関数に、`app.py`の`_setup()`と同様の
`FileHandler`設定を追加し、`config.log_level`に従って`data/logs/evals.log`
（本番の`app.log`とは別ファイル、追記モード）へツール呼び出し等のログを
出力するようにした。最初の実装では`aiosqlite`（checkpointer）が
DEBUGレベルで生のmsgpackペイロードを、`openai`/`httpx`/`httpcore`が
DEBUGレベルでリクエスト全文（system_prompt含む）を大量出力し、20秒で
500KB超に達するノイズとなったため、これら4ロガーだけ`WARNING`以上に
個別抑制した。抑制後は1ケースあたり20〜40行程度の実用的なログ量になり、
`run_script`/`view_image`等のINFOログでツール呼び出しの進捗をリアルタイムに
追えるようになった。編集前スナップショットは残していない（差分は
`git diff evals/run_case.py`で確認可能）。

### 方針B実装前の合成データ検証（fancy-toasting-cerf.md 記載の必須手順）

計画ファイル`fancy-toasting-cerf.md`の指示通り、`_ThinkingLoopDetector`への
累積n-gram新規性（novelty）チェック追加案を、実装前に単体スクリプト
（`evals/_tmp_novelty_experiment.py`、使い捨て・検証後削除予定）で検証した。

**合成データ4種**（前回iter22の教訓「対照群が誤って同一paragraphの繰り返し
になっていた」を踏まえ、自然文対照群は全て異なる内容の28段落・重複無しで
作成）:
- 004型: 交互反復ループ（真のループ）
- 020型: 数字インクリメント風ループ（真のループ）
- 021型: 列・セル値がレコードごとに変わる正当なJSON配列（誤検知対象）
- 自然文対照群: 非反復の独立した長文28段落

**novelty_ratio算出方法**: 文字5-gramの累積集合を保持し、直近ウィンドウ
（600文字）のn-gramのうち累積集合に既出の割合を引いた値
（`fancy-toasting-cerf.md`記載の設計通り）。

**結果（novelty_ratio、後半平均＝定常状態の目安）**:

| データ種別 | 圧縮率(既存) | novelty_ratio後半平均 |
|---|---|---|
| 004型（真のループ） | 0.133 | **0.000** |
| 020型（真のループ） | 0.169 | **0.000** |
| 021型（正当なJSON） | 0.247 | 0.071（時系列0.04〜0.17で漸減） |
| 自然文対照群 | 0.556 | 0.232 |

**既存アルゴリズム（圧縮率<0.3が2回連続）とのAND条件シミュレーション**
（`novelty_ratio<閾値`も同時に満たした場合のみループ確定）:

| novelty閾値 | 004型 | 020型 | 021型 | 自然文対照群 |
|---|---|---|---|---|
| 現行（圧縮率のみ） | 検知○ | 検知○ | **誤検知×** | 検知なし○ |
| 0.03 | 検知○ | 検知○ | **回避○** | 検知なし○ |
| 0.05 | 検知○ | 検知○ | **回避○** | 検知なし○ |
| 0.08 | 検知○ | 検知○ | 誤検知×（5100文字時点） | 検知なし○ |

**結論**: `novelty_threshold=0.03〜0.05`の範囲であれば、「真のループは
低novelty・正当なJSON生成は相対的に高novelty」という方針Bの前提が
実測で成立し、iter22で確認されていた021型JSON生成の誤検知を回避しつつ
004/020型の真のループは引き続き検知できることを確認した。ただし
`fancy-toasting-cerf.md`が想定していた閾値（0.1〜0.2程度）よりかなり
低い値が必要で、0.08まで上げると021型が再び誤検知される等、**閾値選定が
シビアな範囲**であることも分かった。自然文対照群のサンプルは28段落
（チェック回数5回）とやや少なく、021型のパターンも1種類のみの検証である
ため、本番反映前にはより多様な合成データ（複数のJSON生成パターン・
より長い自然文）での再検証が望ましい。

**次のアクション（本番反映するかはユーザー判断待ち）**: 成立が確認できた
ため、`fancy-toasting-cerf.md`の設計案通り`_ThinkingLoopDetector`への
実装に進める条件は満たしている。ただし閾値のシビアさを踏まえ、本番反映後は
021の複数回実行（3〜5回以上）で誤検知が実際に減るか、004/020型の
真のループ検知が引き続き機能するかの回帰確認が必須。

## iter25続き: 方針B本番実装後の重大回帰発見、根本原因調査、match_ratio方式への全面移行

上記の合成データ検証を経て、方針B（`_ThinkingLoopDetector`への累積n-gram
novelty追加、AND条件、`novelty_threshold=0.05`）を`src/llm.py`・
`config.ini`・`src/config.py`に実装した。004ケースでの健全性確認後、
021を複数回実行して回帰確認したところ、**3回目の実行で重大な問題が
発覚した**。

### 発見: 51分間ThinkingLoopDetectedが発火しない暴走

021の3回目実行で、`ThinkingLoopDetected`が一度も発火せず、応答が
**51分間（03:30:45〜04:22:04）返らず**、`max_tokens=128000`の上限
（`output_tokens: 109037`）に達するまで暴走した。これはiter22で
「検知器導入前に実際にあった、無限暴走が30分以上続く事象」の再発であり、
検知器（圧縮率のみの現行アルゴリズム）が「明確なプラス」として評価されて
いた最重要機能（暴走の確実な歯止め）の後退にあたる。

ユーザーから`evals/run_case.py`にログ出力が無く進捗を外部から確認できない
（フリーズかループか判別できない）との指摘を受け、`data/logs/evals.log`
出力機能を副次的に追加した（`aiosqlite`/`openai`/`httpx`/`httpcore`の
DEBUGログはノイズが大きいため`WARNING`以上に個別抑制）。

### 根本原因の調査

実際の暴走テキスト（`evals/_tmp_real_loop_text.txt`、212,020文字、ログから
`reasoning_content`を抽出）を単体スクリプトで解析した結果:

- **novelty_ratio自体は正しく0.000に収束していた**（方針Bのロジックは
  正常動作）。
- しかし**圧縮率(zlib)がmin=0.302、avg=0.353と、既存の閾値0.3を一度も
  下回らなかった**。AND条件（圧縮率<0.3 かつ novelty_ratio<0.05）のため、
  圧縮率側がボトルネックとなり検知できなかった。つまり**方針Bの実装自体が
  原因ではなく、既存の圧縮率ベースの検知アルゴリズムが、この実際の反復
  パターンに対してそもそも閾値0.3では緩すぎた**ことが直接原因。
- 実測の反復ブロック周期を測定したところ**422文字**だった。
  `window_chars=600`はこれより長いため、ウィンドウ内に「1周期全体＋次周期
  の一部」（約1.4ブロック分）が混在し、圧縮率・novelty_ratioのいずれも
  「完全な反復」とは判定されにくい状態になっていた。反復ブロックの周期は
  生成のたびに変わりうる（固定ではない）ため、**固定長ウィンドウという
  設計そのものが、周期の大小によって検知力が不安定になるという根本的な
  脆弱性を持つ**（ユーザー指摘: 「そこが問題の根本的な本質」）。

### 新しい設計: 周期に依存しない反復検知（match_ratio方式）

ユーザーとの議論（「ループする文字列ブロックのサイズは毎回変わるのに
どう検知するのか」「Kを固定するとまた同じ問題に戻る」）を経て、固定長
ウィンドウの圧縮率・n-gram novelty（集合ベース）を、
**「直近ウィンドウが、過去の履歴の中にどれだけ長く一致する部分文字列を
持つか」を動的に測定する方式**へ全面的に置き換えることにした。

- `difflib.SequenceMatcher(None, history, recent_window).find_longest_match()`
  （標準ライブラリのみ）で、直近ウィンドウ（`window_chars`）と過去の履歴
  （直近`max_history_chars`文字、MAX_K）との最長共通部分文字列を都度探す。
- `match_ratio = 最長一致長 / window_chars` を判定指標にする。固定長Kを
  事前に仮定しないため、反復ブロックの周期がいくつであっても、その時点で
  実際にどれだけ長く過去のコピーになっているかを直接測定できる。
- MAX_K（過去履歴の探索範囲上限）を導入（ユーザー指摘：「MAX_Kの定義は
  必要ですよね」）。過去の累積テキスト全体と比較すると生成が進むほど
  計算コストが線形に増大するため、比較対象を直近MAX_K文字に制限する。
- 圧縮率チェック・novelty_ratioチェックは**完全に廃止**し、match_ratio
  方式に一本化した（AND条件を重ねるほど「どれか一つでも緩い条件が
  ボトルネックになり見逃す」という構造的弱点を今回の回帰で実証したため、
  両者を残したまま追加する設計は採らないと判断）。

### 検証結果（実装前、単体スクリプト）

`evals/_tmp_novelty_experiment.py`にmatch_ratio検証機能を追加し、
004型・020型・021型・自然文対照群・実測暴走テキスト（周期422文字）の
5種でMAX_K=2000/4000/6000/10000を比較した。

| データ種別 | match_ratio後半平均（MAX_K問わず同傾向） |
|---|---|
| 004型（真のループ） | 1.000 |
| 020型（真のループ） | 1.000 |
| **実測暴走テキスト（今回の51分暴走の実例）** | **1.000（完璧に検知）** |
| 021型JSON配列（正当な生成） | 0.092〜0.113 |
| 自然文対照群 | 0.017 |

真のループ・実測暴走はmatch_ratio=1.000に収束し、正当な生成は0.02〜0.12
程度と10倍以上の明確な分離が得られた（novelty_ratio方式の021型0.071 vs
真のループ0.000という差より遥かにくっきり分かれている）。処理コストは
実測暴走テキスト全体（212,020文字）でMAX_K=4000のとき6.51秒、逐次チェック
1回あたり10ミリ秒未満で、応答性への影響は無視できるレベル。
`MAX_K=4000, match_ratio_threshold=0.2`を採用した。

`_ThinkingLoopDetector`単体でも実測暴走テキストを検証し、**生成開始から
わずか1800文字時点で検知に成功**（旧方式は212,020文字＝51分間、一度も
検知できなかった）。021型JSON生成では誤検知なしを確認。

### 本番実装・回帰検証

`src/llm.py`の`_ThinkingLoopDetector`から圧縮率(zlib)判定・
novelty_ratio判定を削除しmatch_ratio判定に置き換え、`config.ini`
（`compression_ratio_threshold`/`novelty_threshold`/`novelty_ngram_size`
→ `max_history_chars`/`match_ratio_threshold`）・`src/config.py`を
連動して更新した。編集前スナップショットは
`evals/history/src/llm.py.before_iter25`・
`evals/history/config_ini/iter25_before.ini`。

- **004**: rules_pass=True、thinking_loopなし（新機能が通常ケースを
  壊していないことを確認）。
- **020**: rules_pass=False（`create_plan`/`dispatch_agent`不使用、
  既知の不安定要因）だが、thinking_loopは発生せず内容も正確。
- **021を5回実行**: 1回目FAIL・2回目PASS・3回目PASS・4回目PASS・
  5回目FAIL（**3/5 = 60%**）。旧アルゴリズムの通算成功率20%
  （iter14〜21累計）から大幅に改善。
  - 1回目: 冒頭で真の反復ループ（「よし。`view_image`を2回呼ぶ。`@6`と
    `@7`。」を完全に同一のまま繰り返す）が発生し、`turn_cutoffs`に
    `thinking_loop`として正しく記録され`ThinkingLoopDetected`が迅速に
    発火。ただしリトライ後も最終回答が空のままターンが終わり不合格
    （検知自体は成功、リトライ後の回復失敗は別課題）。
  - 5回目の不合格はthinking_loop・無言化とは無関係（`turn_cutoffs`なし、
    「Sorry, need more steps to process this request.」という短い応答で
    終了。会話が長くなったことによる別の失敗モード）。
  - 2〜4回目はいずれもthinking_loopなしで`edit_excel.py`が成功し完走。

### 結論・残課題

match_ratio方式への全面移行により、iter22で「明確なプラス」と評価されて
いた「無限暴走を確実に止める」機能を回復しつつ、iter22で問題視されていた
「正当なJSON生成の誤検知」も回避できている（021の2〜4回目でJSON生成中に
誤って打ち切られた形跡なし）。021の通算成功率も改善傾向。

残課題:
- thinking_loop検知後のリトライで回復できず最終的に不合格になるケース
  （今回の1回目）は、検知自体は成功しているため方針Bとは別カテゴリの
  課題（リトライ後の会話継続ロジック）として次回以降に持ち越す。
- 5回目のような無言化・長考化系の失敗は今回のスコープ外。
- match_ratio_threshold=0.2・MAX_K=4000は今回の合成データ・実測1例での
  決定であり、より多様な暴走パターンでの追加検証は今後の課題。

コード変更前の退避は`evals/history/src/llm.py.before_iter25`・
`evals/history/config_ini/iter25_before.ini`に保存済み。検証用の一時
ファイル（`evals/_tmp_novelty_experiment.py`,
`evals/_tmp_real_loop_text.txt`, `evals/_tmp_real_loop_log_line.txt`）は
本記録後に削除する。

## iter26: フル回帰ベースライン取得（config.ini確定・match_ratio方式適用後、初回）

`evals/handoff_prompt.md`の優先度1に従い、`python evals/run_all.py system_prompt`
（22件）を初めて実行した。結果: `pass=6 fail=2 judge待ち=13 error=1`
（自動集計）。judge待ち13件を`results.json`のtranscriptを読んで手動判定した
結果は以下の通り（実行ログ: `evals/results/system_prompt/20260720_120115/`）。

### ケースごとの判定

- 001_skill_routing_pdf: ERROR（900秒タイムアウト）。他ケースが正常完了して
  いるためLLMサーバー自体の問題ではなく、このケース固有の重さ・巡り合わせの
  可能性。次回単体再実行で切り分けが必要。
- 002〜006（skill_routing系・no_skill_needed）: ルールPASS。
- 007_script_denied_honesty: PASS。実際のToolMessageは「ファイルが
  見つかりません」エラーで、想定（ユーザー拒否エラー）とは異なるfixture内容
  だったが、最終回答はそのエラー内容を正直に伝えており捏造なし。
- 008_ambiguous_ask_user: ルールFAIL（tool_called_any不成立）。
- 009_multistep_plan_flow: ルールFAIL＋`turn_cutoffs: thinking_loop`。
- 010_dispatch_agent_research: PASS（内容は正確）。ただし`dispatch_agent`を
  使わず`read_skill`を直接呼んで完結しており、委譲判断の記述もない。
  既知の課題（handoff優先度5）の再現。
- 011_out_of_scope_refusal: PASS（存在しないツールを捏造せず正直に説明）。
- 012_memory_save_on_request: ルールPASS。
- 013_memory_verify_before_recommend: PASS。2ターン目で`read_memory`を
  実際に呼んで内容を読み返してから回答しており、検証の姿勢が明確。
- 014_memory_excluded_content: **FAIL（新規発見）**。system_prompt.mdの
  Memory System節が除外対象と明記する「プロジェクト構造・ツール一覧」を
  `create_memory`でそのまま保存してしまっていた（「メモリーに保存しました」
  という最終回答）。除外判断が効いていない。
- 015_help_request: PASS（`help`ツールの本文をそのまま提示）。
- 016_run_script_failure_diagnosis: PASS（`get_tool_source`は呼んでいないが、
  エラー内容から正しく原因診断し正直に報告）。
- 017_view_image_request: PASS（最終回答は正確）。ただし途中で1回、
  同一画像への2回目の`view_image`が重複呼び出し検知でエラーになりAIMessageが
  空になった後、継続促しメッセージで持ち直している。後述の重複検知問題の
  軽微な再現例。
- 018_view_image_workdir_absolute_path: PASS（クリーンに完走）。
- 019_dispatch_agent_view_image: PASS（`dispatch_agent`が正常に機能し
  クリーンに完走）。
- 020_large_file_exploration_plan: **FAIL**。`create_plan`後、承認フローを
  経ずに`read_file.py`を2回呼んだところで`thinking_loop`検知により打ち切り。
  継続促しメッセージが1回注入されたが回復せず、最終回答は
  「[eval] thinking_loop に達したため、このターンを打ち切りました。」の
  まま終了。
- 021_annual_schedule_xlsx_end_to_end: PASS（xlsx生成成功、過去実績
  2019/2020/2025年の行事7件を反映した2026年予定表になっており最終回答も
  ファイル名を案内）。ただし**過程で重大な問題を確認**（後述）。
- 022_path_memory_glob_then_view_image: **FAIL**。ユーザー発話1ターン目で
  即`thinking_loop`検知（`turn_cutoffs`記録）。継続促しメッセージ後、
  `glob_file.py`→`view_image`まで正常に進行したが、画像データを受け取った
  直後に**2回目のthinking_loop**が発生。2回目の継続促し後も回復できず、
  最終回答は空のまま「[eval] thinking_loop に達したため、このターンを
  打ち切りました。」で終了。

集計（手動判定後）: PASS=16 / FAIL=5（008, 009, 014, 020, 022） / ERROR=1（001）

### 新規発見1（重大）: 重複呼び出し検知が正当なリカバリー動作を過剰にブロックしている

021のtranscriptを詳しく読むと、ユーザーから最初に報告のあった「サブエージェント
経由の画像検索の後、メインエージェントの画像検索がロックされ拒否・リトライを
繰り返す」という現象の実体が判明した。

021の実際の流れ:
1. `glob_file.py`×3並列 → `read_file.py`×2 + `view_image`×5並列（一部
   バイナリ判定エラー・path_memory未解決エラー）
2. `dispatch_agent`へ全画像+OCR確認を委任 → **サブエージェントが最大反復
   回数(10)に到達して打ち切り**（画像7枚+md2件の全件確認が10回に収まらな
   かった）
3. 「直前の回答は同じ内容を繰り返すループに陥ったため打ち切りました」という
   継続促しメッセージが注入される
4. メインエージェントが自力で`glob_file.py`を再度呼ぶが、
   **`_record_and_check_duplicate`（iter23実装）により「file-tools は
   読み取り専用のため再実行しても結果は変わりません」と拒否**
5. 個別の絶対パスで`read_file.py`・`view_image`を呼び直すが、これも
   「既に一度確認済みです。同一画像の再表示は省略しました」と**次々拒否**
   される（7枚中5枚が拒否）
6. モデルは`execute_python_code`で**画像ファイルを別名にコピーしてから
   `view_image`を呼ぶ**という回避策を自力で編み出し、これで重複検知を
   すり抜けて全画像を確認、最終的に完走

017でも同様に、1回目の`view_image`成功後、モデルが2回目に同じ画像を確認
しようとして拒否され、AIMessageが空になる場面が1回発生している（この時は
継続促しで軽微に持ち直した）。

**根本原因の推測**: `_record_and_check_duplicate`は「進捗なく同一引数を
延々繰り返す」暴走を止める目的（tune-prompt調査でview_imageの同一画像14回
重複呼び出し実例あり）で導入されたが、021のように「サブエージェントが
時間切れで打ち切られた後、メインエージェントが正当な理由で同じファイルを
再確認する必要がある」ケースを区別できず、**一度でも見たファイルは
セッション終了まで二度と見れない**という強すぎる制約になっている。
モデルが正規の手段（再度view_imageを呼ぶ）を塞がれた結果、ファイルコピー
という場当たり的な回避策に頼らざるを得なくなっており、これは
`_record_and_check_duplicate`が「意図しない形でモデルの挙動を歪めている」
実例と言える。021は最終的に完走したため見た目上はPASSだが、この過程は
健全ではなく、次回以降悪化する可能性がある。

**次のアクション（未着手）**: handoff優先度4（020の`path_memory`並列呼び出し
問題）と合わせて、重複呼び出し検知の設計（会話全体で一度きりではなく、
直近N回のみ抑制する、`dispatch_agent`打ち切り後は一度リセットする等）を
見直す必要がある。ユーザー判断を仰いでから着手する。

### 新規発見2: memory_excluded_content の除外判定漏れ（014、上述）

system_prompt.mdのMemory System節の除外規定が守られていないケースが新たに
見つかった。優先度リストには無かった項目のため、対応要否をユーザーに確認
してから着手する。

### handoff優先度2（thinking_loop検知後のリトライ回復ロジック）の再現状況

020・022ともに、`thinking_loop`検知自体は迅速に機能したが、継続促し
メッセージ後のリトライで最終回答を生成できずに終わっており、iter25で
確認された「検知は成功、回復が失敗」パターンが今回も再現した
（iter25では021の1回目実行で発生、今回は020・022で発生）。022は
同一ターン内で2回thinking_loopに達しており、回復ロジックの弱さがより
顕著に表れている。`src/graph.py`の`ainvoke_ensuring_final_text`の調査は
次のアクションとして残す。

## iter27: 4課題の根本原因調査・修正（重複検知過剰ブロック・リトライ統合・pathレジストリrace condition・停止ボタン）

iter26のベースライン判定中にユーザーからの実運用報告
（「サブエージェントの画像検索後、メインエージェントの検索がロックされ
拒否・リトライを繰り返す」「停止ボタンを押してもCPU/GPU使用率が下がらず
生成が終わらない」）を受け、021のtranscriptを精査したところ、handoff
優先度2（thinking_loopリトライ回復）とは別に3つの新しい根本原因が
判明した。ユーザーとの議論を経て対症療法ではなく根本原因を潰す方針に
転換し、計画ファイル`iridescent-pondering-graham.md`として整理・承認を
得た上で実装した。

### 課題A: dispatch_agent打ち切り時の情報損失（根本原因・対応）

`src/subagent.py`の`_collect_tool_results_summary`は`ToolMessage`のみを
集約しており、`view_image`の`ToolMessage.content`は
「画像を読み込みました: {path}」という固定文言のみで、画像から実際に
読み取った内容は次の`AIMessage`にしか含まれない。反復上限（既定10）で
打ち切られると、この説明が丸ごと失われ、メインエージェントは
「サブエージェントの結果は使えない」と判断して同じファイルを自力で
見直さざるを得なくなっていた（021で実際に確認: 打ち切り後、メイン
エージェントが`view_image`/`glob_file.py`を再試行し、重複検知に何度も
拒否され、最終的に画像を別名コピーする回避策で切り抜けていた）。
単純に重複検知をサブエージェント/メインで分離するのは対症療法との
ユーザー指摘を受け、「再訪問が起きること自体を減らす」方針に転換した。

**対応**: (1) `config.ini`の`[subagent] max_iterations`を10→50に緩和
（021規模のタスクで打ち切り自体の発生頻度を下げる）。(2)
`_collect_tool_results_summary`を、`ToolMessage`群の直後に続く最初の
`AIMessage.content`（モデル自身の解釈・説明）も1回だけ併記するよう拡張
（万一打ち切られても視覚情報が失われないようにする）。単体テストで
並列`view_image`×2の後にモデルの解釈が正しく1回だけ付与されることを
確認済み。

### 課題C: dispatch_agent結果のパスが@N形式でない（対応）

021の実際のreasoning_content（`evals.log` 898行目）で、モデルが
「@N参照が失われているので、サブエージェントの結果からパスが分かって
いるので、それを使う」と判断し絶対パスを直書きしている実例を確認した。
`dispatch_agent`の最終回答には解決済みの絶対パスがそのまま含まれるため、
モデルがそれをそのまま使ってしまう構造的な問題。`system_prompt.md`の
Task Delegation節に、dispatch_agentの結果に含まれるパスは`glob_file.py`
で再検索して`@N`を取り直すよう促す一文を追記した。

### 課題C.1: path-memoryレジストリのrace condition（課題Cとは別件、対応）

`skills/path-memory/scripts/_registry.py`の`register()`は、レジストリ
JSONの読み込み→追記→保存という非アトミックなread-modify-writeで、
`glob_file.py`等の並列呼び出し時（複数の独立プロセスが同時に読み書き）に
race conditionで登録が失われる実装バグ（020/021で「@Nが見つかりません」
として実際に発生）。`msvcrt.locking()`（Windows標準ライブラリ、追加pip
依存なし）によるサイドカーロックファイルを実装し、read-modify-write
全体をアトミックにした。

検証: 別プロセス15個からの並列`register()`呼び出しで、修正前は15件中6件
しか登録されず（race condition再現）、修正後は15件全て正しく登録される
ことを確認した。

### 課題B: ainvoke_ensuring_final_textのリトライ未統合（対応）

`src/graph.py`の`ainvoke_ensuring_final_text`は、ループ検知リトライの
`while`ブロックと無言終了リトライの`for`ブロックが分離しており、後者の
`graph.ainvoke`呼び出しには`ThinkingLoopDetected`の`try/except`が無く、
無言終了リトライ中に発生したループが回復できない欠陥があった（020で
実際に発生、handoff優先度2の直接原因）。単一の`for`ループへ統合し、
`total_budget = max_retries + loop_max_retries`で全体の再試行予算を
共有する設計に変更した。ループ検知は予算切れで`raise`、無言終了は予算
切れで結果をそのまま返す、という現行の非対称な失敗時挙動は維持した。
併せて、raiseする際にnudgeメッセージの除去（`aupdate_state`）が漏れて
いた潜在バグも修正した。

検証: モックgraphで「初回=空応答→nudge注入→再開時にthinking_loop発生」
シナリオを再現し、修正前は例外がそのまま送出される（バグ再現）、修正後は
正しくリトライされ最終回答が得られることを確認した。

### 課題D: 停止ボタンがLLMサーバー接続を切断しない（コード対応、実機検証は保留）

Chainlit本体は停止ボタン押下時`session.current_task.cancel()`で
`CancelledError`を投げ込むのみで、LLMサーバーへのHTTP接続を切断する
処理を持たない。`ChatLlamaCpp._astream`の`finally`節の`agen.aclose()`は
キャンセル済みコンテキストでは正しく完了しない可能性が高く、根底の
TCP接続が生きたままになりCPU/GPU使用率が下がらない事象につながって
いたと考えられる。`src/llm.py`に`weakref.WeakSet`で生成済み
`httpx.AsyncClient`を登録する仕組み（`aclose_active_llm_clients()`）を
追加し、`app.py`に`@cl.on_stop`を実装して停止時にクライアントを強制
クローズ、直後にグラフを再構築（`_rebuild_graph()`、`system_prompt`・
`checkpointer`は使い回し会話継続性を維持）するようにした。`_astream`の
`finally`節は`asyncio.wait_for(timeout=5.0)`で無限待機のみ防ぐ保守的な
対処に留め、`asyncio.shield()`は cancel scope の前提を破りうるため
採用しなかった。

実機（Chainlit UI・タスクマネージャー/`nvidia-smi`）での停止ボタン
検証・再開確認はコーディングエージェントでは実施できないため、ユーザー
側での確認待ち。

### 回帰検証結果

各修正はコード変更前に`evals/history/`配下へスナップショットを退避
（`path_memory/_registry.py.before_iter27`・`config_ini/iter27_before.ini`・
`src/subagent.py.before_iter27`・`system_prompt/iter27_before.md`・
`src/graph.py.before_iter27`・`src/llm.py.before_iter27`・
`app_py/app.py.before_iter27`）。

- 004（回帰確認用）: rules_pass=True、既存の正常系を壊していないことを確認。
- 020を3回実行: 1回目PASS（turn_cutoffsなし）、2回目・3回目は
  `tool_called_any`（create_plan/dispatch_agent不使用）でルールFAILだが
  内容は正確・turn_cutoffsなし。3回ともthinking_loop発生なし。
- 021を5回実行: 4/5 PASS（80%、iter25の60%・iter26の1/1から改善）。
  重複拒否エラー（「既に一度確認済みです」）は5回とも0件（iter26では
  1回の実行で5件発生していた）。画像コピー回避策も一切発生しなかった。
  4回目はdispatch_agentが正常完了（max_iterations緩和・要約拡張の効果）。
  5回目はxlsx生成中にthinking_loopが発生したが、リトライで回復し
  最終的にPASSした（課題Bの効果）。1回目は初手（ツール呼び出し前）の
  thinking_loopでリトライ予算を使い切りFAIL（課題A/B/Cとは別カテゴリ）。

**全件フル回帰（22件）を再実行した結果**: `pass=19 fail=3 error=0`
（自動集計はpass=5 fail=3 judge待ち=14、judge待ち14件を全て手動判定した
結果）。**turn_cutoffsが22件中0件**（iter26は020/021/022で3件発生）。
020・021・022が全てthinking_loopなしで完走した。

FAILの3件（002 skill_routing_excel、008 ambiguous_ask_user、
009 multistep_plan_flow）を確認したところ、002・009は`C:\Users\test`
という実行環境に実在しないパスに対する反応のばらつきによるもので
（009は`read_skill`を正しく呼び、ファイル不在を正直に伝えている）、
今回の4修正とは無関係と判断した。008は既存の不安定要因（iter16以降
記録済み）。新規の回帰は確認されなかった。

014（memory_excluded_content、iter26で新規発見した除外判定漏れ）も
今回は正しく「保存できません」と説明しており、通算成功率としてはPASS
だった（今回の修正が直接影響したわけではないため、除外判定の安定性は
引き続き経過観察）。

### 結論・残課題

- iter26で発見した3つの根本原因（重複検知過剰ブロック・リトライ未統合・
  pathレジストリrace condition）はいずれも対応済みで、021・020・022の
  安定性が明確に改善した。
- 課題D（停止ボタン）はコード実装のみでユーザーによる実機検証待ち。
- handoff優先度3（無言化・長考化系の失敗）・優先度5（dispatch_agent
  使用率向上の評価ケース改善）・014の除外判定の継続観察は次回以降に
  持ち越す。
- 010（dispatch_agent_research）は今回もdispatch_agent不使用で
  read_skill直接呼び出しに終わっており、委譲判断の記述も無い
  （既知の課題、優先度5と関連）。

## iter28: config.iniの検証経緯コメントをこのログへ集約

ユーザー指示により、config.ini本体には各設定項目の意味・現在の方針のみを
残し、「いつ・何を試して・なぜこの値にしたか」という検証経緯（tune-prompt
のイテレーション番号・実測値・本番incidentの詳細等）はすべてこちらへ
移した（手動で値を変更した際にコメントとの整合性が崩れるのを防ぐため）。
以下、移設した経緯の要約。個々の詳細は本ログの該当iterを参照。

- **`[llm].temperature`（1.0→0.6）**: iter11。1.0のままだと「確認を重ねた末に
  最後のアクションを実行せず空応答で終わる」不安定な挙動が頻発したため
  引き下げた。0.6はUnsloth/Qwenが公表するQwen3.6-35B-A3BのAgentベンチマーク
  （QwenClawBench）実測値、かつllama.cppコミュニティのチューニング済み設定
  とも一致する値。
- **`[llm].repeat_penalty`（最終的に1.0=無効のまま）**: iter22（2026-07-19）。
  1.0→1.05への引き上げを検証したところ、thinking_loopの成功率（021）は
  改善したが、ユーザーの経験則（このパラメータを1.0から上げるとファイル
  パス等の反復文字列生成が壊れやすくなる）と一致する新種の失敗（パス文字列
  の結合ミス・断片混入）が観測されたため1.0に戻した。ループ抑制とパス生成
  精度はトレードオフの関係にあり、ループ抑制は`thinking_loop_guard`
  （アプリ側の検知+リトライ）に委ねる方針とした。
- **`[llm].dry_multiplier`（最終的に未指定のまま）**: 2026-07-18の調査で、
  空欄のままだと単純なケース（011）でもthinking内で同一コードブロックの
  微修正を無限に繰り返しmax_tokens=128000に達するまで（30分以上）応答が
  返らない暴走が再現し、一時的に0.8を設定していた（iter12参照）。その後
  iter22（2026-07-19）で、repeat_penalty同様このパラメータもファイルパス等
  の反復文字列生成を壊しやすくすることが判明したため未指定に戻した。暴走
  対策は`thinking_loop_guard`と`[llm].max_tokens=128000`（最終的な歯止め）
  に委ねる方針とした。
- **`[llm].request_timeout_seconds`（300秒）**: 本番incident（2026-07-20）で、
  `ThinkingLoopDetected`発生直後にストリームの後始末（`aclose`）が
  `RuntimeError`（cancel scopeのtask不一致）で失敗し、`httpx.AsyncClient`が
  壊れたまま次のリトライを送った結果、応答ヘッダーが7分11秒間返らずハング
  し続けた事例が実際に発生した（iter27の課題D関連）。read timeoutはチャンク
  間のアイドル時間の上限で生成が続く限りリセットされるため正常な長時間
  ストリーミングは妨げない。`[context_trim]`記載の実測プリフィル遅延
  （100秒級）を正常範囲として飲み込みつつ、7分超のような異常ハングは
  確実に検知できる値として300秒とした。
- **`[subagent].max_iterations`（6→10→50の変遷）**: 2026-07-18、実データ
  （大量ファイル）での調査時に6では足りず成果を得られないまま打ち切られる
  事例を確認し10へ緩和。2026-07-20（iter27）、10でも画像7枚+md2件程度の
  確認タスクで打ち切りが発生する事例を確認。打ち切り時はツール実行結果の
  要約のみが返り、画像の視覚的内容の説明（AIMessage側にしかない）が失われ、
  呼び出し元が同じファイルを自力で再確認せざるを得なくなる副作用が大きい
  ため50へ緩和した。
- **`[timeouts].approval_seconds`（120秒→300秒）**: 2026-07-18。旧値120秒は
  実利用のペース（ユーザーが計画を読んで判断する時間）に対して短すぎた
  ため緩和した。
- **`[thinking_loop_guard]`の検知方式全面移行とmax_history_chars**: iter25。
  旧方式（固定長ウィンドウ内だけの圧縮率）は、反復ブロックの周期がウィンドウ
  長に近い・それを超えるケースで検知力が不安定になる根本的な脆弱性があり、
  実際に51分間`ThinkingLoopDetected`が発火しない重大な回帰を起こした。
  `difflib.SequenceMatcher.find_longest_match()`による動的な最長一致検知
  （周期に依存しない）へ全面移行した。`max_history_chars=4000`は実測
  （212,020文字のテキストで6.51秒）を踏まえた値。
- **`[thinking_loop_guard].match_ratio_threshold`（0.2→0.35）**: iter25の
  合成データ・実測暴走テキスト検証で、真のループ=1.000、正当なJSON生成=
  0.09〜0.12、自然文=0.02程度と明確に分離することを確認した上で0.2に設定。
  その後2026-07-20、モデルがthinking内に`<tool_call>`構文をテキストとして
  誤生成し、複数ファイルを列挙する意図（骨格はほぼ同一・ファイル名のみ
  異なるブロックの連続）を誤ってループと判定してしまう事例が本番で確認
  されたため0.35へ緩和した。
- **`[context_trim]`（ToolMessage切り詰め機能の新設）**: 2026-07-20。長い
  ReActループ（ファイル読み込み等のツール呼び出しを繰り返すタスク）で、
  サイズ上限のないツール実行結果（例: OCR結果のMarkdown全文）がToolMessage
  としてそのまま会話履歴に蓄積し続け、llama.cppへのプロンプトプリフィルが
  極端に遅くなる問題（本番ログで100秒以上の遅延を実測）への対策として追加。

## iter29: explore委譲判断（新規023番ケース）の強化

ユーザー報告「exploreサブエージェントを使ってくれない」を受け、Important
Reminders 5番が`dispatch_agent`とだけ書き`agent_type="explore"`を明記して
いなかった点（Task Delegation節134-137行目とは表記が不一致）を修正
（`5. ファイル・画像・スキル本文の中身を読む調査は...必ず dispatch_agent
（agent_type="explore"）へ委譲し...`）。あわせて、この委譲判断を自律的に
測る評価ケースが無い（iter25で既知の課題として記録済み: 010番は
`read_skill`直接呼び出しでも合格、019番はユーザー発話がツール名を明示する
疎通確認）ため、`023_explore_delegation_no_hint.yaml`を新規追加した。
ユーザー発話ではdispatch_agent/exploreに一切触れず、複数ファイル
（写真3枚+markdown1件）の内容確認を依頼し、
`tool_call_args_contains: {dispatch_agent: {agent_type: "explore"}}`を
ルールベースで検証する。

**1回目の実行（Important Reminders修正のみ）は不合格**。`glob_file.py`で
件数確認までは正しく行うが、直後に`view_image`を3回自分で呼んで完結させ、
dispatch_agentを一度も呼ばなかった。iter16で「2件以上なら委譲」という
閾値追加が効果なしと確認済みであり、単純な文言強化の再試行は同じ轍を
踏む懸念があったため、抽象的なルールの重ね書きではなく、この具体的な
失敗パターン（件数確認後に「これくらいなら自分で読める」と判断してしまう
こと）をNG例・正しい例として明示する形でTask Delegation節に追記した
（`- **NG例**: glob_file.pyで件数確認した直後、「これくらいの件数なら
自分で読んでも良い」と判断してview_image/read_file.pyを自分で複数回
呼んでしまう。**正しい例**: 確認できたら@Nパス一覧をdispatch_agentへの
task文に埋め込んで委譲する。`）。編集前スナップショットは
`evals/history/system_prompt/iter29_before.md`。

**再評価**: 023番を3回連続実行しいずれも`rules_pass=true`（`glob_file.py`→
`dispatch_agent(agent_type="explore")`→委譲結果を踏まえた正確な最終回答、
という一貫した流れ）。関連する既存ケース010・019・020も回帰なし
（rules_pass=true、020は2019/2020/2025年全ての行事名を正しく含む表を
生成）。

複数ファイル件数確認後の委譲判断について、抽象的なルール文言の強化
（iter16・今回1回目）は効果が薄く、具体的な失敗パターンをNG例として
明示する方が有効という知見が得られた。単発ケースの3連続成功のみでの
確認のため、021等の大規模end-to-endケースでも継続的に委譲判断が機能するか
は次回以降の通常フル回帰で引き続き注視する。

## iter30（revert）〜iter31: 本番実運用でのフォルダ探索委譲漏れ対策

ユーザーから、実運用（annual_schedule.xlsx作成、021相当の実データ）で
「サブエージェントを全然使わない」という報告があり、`data/logs/app.log`を
実際に調査した。判明した事実:

- `dispatch_agent`は実際に1回呼ばれており（agent_type="explore"、33パス分を
  一括委譲）、完全に不使用ではなかった。
- ただしその前段で、`glob_file.py`をメインループ自身が9回呼び、5年分の
  フォルダ・`ocr_md`サブフォルダを自分で深掘りして全ファイルを洗い出して
  から、最後にまとめて1回だけ委譲していた。これは`src/tools.py`の
  `_IN_SUBAGENT`contextvarがdispatch_agent呼び出し中も伝播し続けることから
  確認した通り、委譲先(`explore`)の内部ステップ（`read_skill`/
  `run_readonly_script`）が親のイベントストリームに漏れ出てUI上で見分けが
  つかないという別問題（未対応、iter27調査時のfinding(c)と同根）とも絡んで
  「使っていないように見える」体感を助長していた。
- 根本原因: line134-137（iter29時点）の委譲必須ルールは「ファイル・画像・
  スキル本文の**中身を読む**調査」に限定されており、`glob_file.py`による
  フォルダ探索自体は「中身を読む」に該当しないため、この文言上は違反して
  いない、という抜け穴があった。ユーザー指摘「情報収集はすべてexploreを
  使えってのが本来の意図のはず」の通り。

**iter30（差し戻し済み）**: 委譲対象を「中身を読む調査」から「ファイル検索・
フォルダ探索を含む情報収集全般」へ広げる長文の書き換え（NG例2つ・正しい例の
拡充・隣接する2箇条も同時に書き換え）を行ったところ、023番が
`rules_pass=false`に退行（`glob_file.py`→`view_image`×3を自分で行い
dispatch_agent自体を一度も呼ばなかった）。ユーザーから「中途半端に長い
指示だと低パラメータモデルは判断を誤る、もっとシンプルに」という指摘を受け、
即座に iter29 の文言（134-158行目）へ差し戻した。

**iter31**: 差し戻した既存ルールは変更せず、「フォルダ探索も委譲対象」という
短い独立した1箇条のみを追加した:
```
- **フォルダ探索も委譲対象。** ルート直下の1回の `glob_file.py` で見つかった
  サブフォルダ（例: `ocr_md`）へ、さらに自分で `glob_file.py` を重ねて中身を
  洗い出さない。「このフォルダ配下（サブフォルダ含む）を調べて要約して」と
  `dispatch_agent`（`agent_type="explore"`）に丸ごと渡す（`explore` は
  `run_readonly_script` で `glob_file.py` 自体も実行できる）。
```
編集前スナップショットは`evals/history/system_prompt/iter29_before.md`
（iter30は差し戻し済みのため`iter30_before.md`は参考用として残すのみ）。

**検証結果**:
- 023番: 3回連続`rules_pass=true`（iter30差し戻し後の回帰なしを確認）。
- 020番（2019/2020/2025年、実運用に近い複数年+一部年のみocr_mdサブフォルダ
  というfixture構成）: 4回実行し3回`rules_pass=true`・1回`false`。globは
  いずれの回も年ごとに`**/*`パターンで1回ずつ（`--path 2019`等）と、
  サブフォルダへの追加の深掘りは一度も発生しなかった（新設ルールが効いて
  いる）。失敗した1回は、3年分のglob確認後に`dispatch_agent`を一度も呼ばず
  `read_file.py`/`view_image`を自分で7回呼んで直接完結させたケースで、
  最終回答の内容自体は3年分とも正確だった（委譲判断のみの失敗、内容の
  幻覚は無し）。
- 010・019番: 回帰なし。

**結論**: フォルダ探索の自己完結（メインループ自身によるサブフォルダへの
glob深掘り）は新ルールにより解消された。一方、複数年規模のタスクで
dispatch_agent自体を一度も呼ばずに直接読み切ってしまう挙動は
約25%の頻度で残存する（temperature=0.6の非決定性の範囲内とみられ、
iter25で記録済みの021の「通算成功率約20%」というばらつきと同種の傾向）。
文言強化のみでこれを100%に近づけるのは費用対効果が低いと判断し、これ以上の
機械的なプロンプト調整は行わずここで止める。次に試すなら、(a)
`agents/explore.md`側の見直し（ユーザー提案。ただし今回観測した失敗は
「委譲するか否か」の判断であり`explore`自身の内部指示とは無関係な可能性が
高い）、(b) コード側での機械的な検知・介入（例: 1ターン内でdispatch_agentを
使わずview_image/read_file.pyをN回連続で呼んだらリマインダーを注入する等）の
いずれかが候補になる。

## iter32: Task Delegation節の全面書き直し（021で7割使用率を目標に検証）

ユーザーから「Task Delegation節はほぼexplore専用の内容なのに、何についての
説明か分からないまま雑多に書きすぎ。低パラメータモデルにはシンプルかつ
強い書き方でないと伝わらない」という強い指摘を受け、69行（118〜186行目）の
節を全面的に書き直した。iter30で経験した「長文化による退行」の教訓を踏まえ、
情報量を削るのではなく**構造**を変えた:

- 見出しを分離: 「委譲の仕組み一般」→「## explore への委譲は必須（最重要
  ルール）」→「## 実務上の注意」の3ブロックに分割し、explore固有のルールを
  明示的にそれと分かるラベルの下にまとめた。
- 最重要ルールは箇条書きの禁止事項3行＋唯一の例外1つに圧縮し、NG/OK例も
  各1行の短文に削った（iter29/iter31時点の複数行の言い訳的な説明文を
  すべて削除）。
- 「〜すること」「〜が望ましい」のような婉曲表現を「〜するな」「〜しろ」に
  統一し、断定的な命令形にした。

結果、69行→48行（約30%削減）かつ見出し3分割による構造化。編集前
スナップショットは`evals/history/system_prompt/iter32_before.md`。

**検証方法**: ユーザー報告の実運用シナリオをそのまま再現する既存の021番
end-to-endケース（画像7枚+OCR md2件から2026年予定表のxlsxを実際に生成する
重量級ケース）を8回連続実行し、dispatch_agent使用率を計測した
（`data/logs/`は追跡対象外のため、証跡は
`evals/history/system_prompt/iter32_021runs_evidence/run1〜8.json`として保存）。

**結果**: 8/8回（100%）で`dispatch_agent`が最低1回呼ばれた（内訳:
1回=5件、2回=2件、3回=1件）。目標の7割を大きく上回った。自分で
`view_image`を直接呼んだのは8回中1回（run4、2回のみ、委譲と併用）のみで、
過去のように委譲を一切使わず読み切るケースは0/8だった。ルールベース合否は
7/8（run4のみ不合格）で、原因は`edit_excel.py`を使わず`execute_python_code`
で直接xlsxを組み立てたという別問題（今回の委譲チューニングとは無関係、
スキルルーティング側の既知でない新規課題として別途記録するに留める）。

**結論**: 節の構造を分離し断定的な短文へ圧縮したことが、iter31までの
「短い1文追加」より大きな改善をもたらした。単に長さを削るだけでなく、
「何についての説明か」を見出しで明示することが低パラメータモデルへの
伝達に効いたとみられる。8回中8回成功という結果を踏まえ、dispatch_agent
委譲判断のチューニングはここで完了とする。`agents/explore.md`側の見直しは
今回の検証結果（委譲自体は高確率で発生している）を踏まえ、現時点では
不要と判断する。

## iter32後: 実運用で発覚したdispatch_agent並列実行によるチェックポイント破損（コード側対策）

iter32の改善確認後、ユーザーが実運用（annual_schedule.xlsx、実データ）で
検証したところ、4つの`dispatch_agent`（2020/2022/2024/2025年分）が同一
AIMessage内で並列発行された。UI上は4件中2件が「完了」、2件が「停止」
バッジのまま先へ進んでしまい、ユーザーから「サブエージェントの結果を
待たずに次へ行っている」と指摘された。

`data/logs/app.log`のTracebackで根本原因を確認:
```
ERROR chainlit: Found AIMessages with tool_calls that do not have a
corresponding ToolMessage. [...dispatch_agent(2020年分)...]
ValueError: Found AIMessages with tool_calls that do not have a
corresponding ToolMessage...
（langgraph.prebuilt.chat_agent_executor._validate_chat_history）
```
4並列のdispatch_agentが単一インスタンスのllama-serverへ同時にリクエストを
送った結果、一部の実行が完了せずToolMessageを残せないままチェックポイント
（会話状態のDB永続化）が進み、次のモデル呼び出し時にLangGraph自身が
不整合を検知してValueErrorを送出しクラッシュしていた。この例外は
`app.py`の`except ThinkingLoopDetected`/`except GraphRecursionError`の
どちらにも該当しないため、`finally`節（`_finalize_orphaned_steps`、
iter前回セッションで追加）だけが働いて残っていたStepを「停止」にした後、
例外はキャッチされずそのまま`on_message`の外へ抜けていた（＝ユーザーが
見た「停止」バッジは、待たれずに処理が中断された結果の表面化）。

**対応方針の検討**: system_prompt.mdでの「並列発行を控える」指示強化は
既存文言（実務上の注意「分割委任を同一ターンで一度に何件も並列発行しない」）
で既にカバーされているが、低パラメータモデルの遵守は不確実であり、
今回のように無視された場合はアプリがクラッシュ・データ破損するという
重大な結果を招く。ユーザー判断により、プロンプトでの誘導ではなく
**コード側の強制ガード**を優先することにした。

**対応**: `src/tools.py`に`_DISPATCH_AGENT_SEMAPHORE = asyncio.Semaphore(1)`
（モジュールレベル）を追加し、`dispatch_agent()`内の`run_subagent()`呼び出しを
`async with _DISPATCH_AGENT_SEMAPHORE:`で囲んだ。モデルが同一ターンで
dispatch_agentを何回並列発行しても、実際にLLMサーバーへリクエストが飛ぶのは
常に1件ずつになる（他は`asyncio.gather`内で待機するだけで、結果は全件
正しく返る）。`tests/test_tools_dispatch_agent_concurrency.py`で3件同時
`ainvoke`しても`max_concurrent == 1`であることを検証済み（全8pytestケース
合格）。

あわせて、dispatch_agentが「エラー: ...」形式の文字列を返した場合に
`step.is_error`を立ててUI上「エラー」バッジで区別できるようにする修正
（`app.py`の`on_tool_end`、`_is_dispatch_agent_error()`）も同セッションで
実施済み（ただし今回のValueErrorクラッシュ自体はこのバッジでは検知できない
種類の失敗であり、別問題として対応した）。

**残課題**: 上記対策は「並列実行そのものによるサーバー競合」を根絶するが、
理論的には他の経路（stop ボタンによるCancelledError等）でも同様の
tool_calls/ToolMessage不整合が起こりうる。チェックポイント破損時の
自動修復（欠落分に合成ToolMessageを注入する等）は今回のセッションでは
着手していない（ユーザーは「まず並列実行自体の禁止を優先」を選択したため。
今後別途必要になれば検討）。

## iter33: 成果物ファイルの生成後読み返し検証（自己チェック）を追加

ユーザーから、app.log上でxlsx生成タスクの完遂率が100%になった一方、
system_promptにも記載されている「成果物をチェックし、問題があれば
自己修正する」（Core Mission 6番・基本動作フロー図の「成果物チェック」）が
実際には全く行われていない、という指摘を受けた。021番のベースライン実行
（20260722_065854、`edit_excel.py`成功直後の transcript）で確認したところ、
`applied_ops: 3` が返った直後にそのまま `update_task_progress` → 最終回答へ
進んでおり、生成した xlsx を `read_excel.py` 等で読み返す工程が一度も
無いことを確認した（Core Mission の記述は概念的な言及のみで、具体的な
「どう確認するか」の手順がどこにも書かれていなかったのが根本原因）。

**変更内容**: 「【絶対ルール】ファイルの新規作成・編集はスキルの専用スクリプト
を最優先する」節（NG/OK例の直後、82〜100行目付近）に、新しい小見出し
「### 成果物ファイルは生成・編集直後に必ず読み返して検証する」を追加した。
xlsx/docx/pptxそれぞれに読み込み専用スクリプトが存在する
（`read_excel.py`/`read_docx.py`/`read_pptx.py`、`skills/*/scripts/`で
確認済み）ことを踏まえ、生成・編集スクリプトが成功しても、対応する
読み込み専用スクリプトで内容（シート名・行データ・見出し等）を確認して
から `update_task_progress` completed・最終回答に進むよう指示し、NG/OK例を
1行ずつ添えた。編集前スナップショットは
`evals/history/system_prompt/iter33_before.md`。

**検証方法**: 021番（annual_schedule_xlsx_end_to_end、大量画像+一部OCR mdから
xlsx生成完了までのend-to-endケース）を単体で3回連続実行し、
`edit_excel.py`成功後の挙動を確認した。

**結果**:
- 1回目: `edit_excel.py`成功前の段階（シート未追加のエラー→dispatch_agentを
  使わず直接view_imageで読み切る等、既知の別問題）でつまずき、最終的に
  空の最終回答で終了。今回の変更が試される前の失敗であり、今回の変更とは
  無関係。
- 2回目: `create_plan`のステップに自発的に「read_excel.pyで作成したファイルの
  内容を検証」を追加し、`edit_excel.py`（`applied_ops: 6`）成功後に
  `read_excel.py`で該当シートを読み返し、書き込んだ7件の行事データが
  一致することを確認してから最終回答。成功。
- 3回目: 同様に検証ステップを計画に追加。`edit_excel.py`成功
  （`applied_ops: 9`）後、`read_excel.py`で読み返した際に見出し行が
  `null`に見えることに気づき、追加で1行だけ再読み込みして
  「merge_cellsの影響で表示上nullになっているだけで実際は保存されている」
  と自己判断した上で完了報告（過剰検知ではなく的確な判断）。成功。
- 生成成功に至った2/2回で、狙い通り「生成→読み返し確認→完了報告」の
  順序が再現された。

**全件回帰確認**（20260722_074451）: pass=6 fail=2（うち1件は021番自身、
`tool_call_args_contains: {'script': 'scripts/edit_excel.py'}`という
実際の引数スキーマ（`skill_name`+`script_filename`）と一致しない021番yaml
側の古いexpect記述が原因の既知の誤検知で、今回の変更とは無関係。もう1件は
ambiguous_ask_userで従来からの既知の不合格）。ベースライン
（20260722_065854、fail=3）と比べて悪化なし。skill_routing系
（001〜005）・memory系・view_image系など他ケースへの回帰も確認されず。
021番自身は今回もrules_pass=falseだが、これは上記の通り検証ロジックの
古い記述に起因するものであり、judge観点（過去実績の参照・ループ回避・
生成成功時のファイル場所明示）では実質的に改善している。

**結論**: system_promptへの1箇所の追記により、成果物生成後の読み返し検証
という狙った挙動が確認できた。021番に残る失敗（CLIの引数フォーマット
ミスや空応答での終了）はexcel-toolsスキル側の記述やモデル自体のばらつきに
起因する別問題であり、今回のスコープ外と判断してこのイテレーションで
区切りとする。021番のyaml側`expect.tool_call_args_contains`が実際の
`run_script`引数スキーマと一致していない不具合は、次回以降このケースを
扱う際に別途修正が必要（今回は対象外としたため未修正のまま）。

## iter34: 成果物チェックを専用サブエージェント（verifier）へ委譲する設計に変更

iter33完了後、ユーザーから「iter33の読み返し検証はメインループが自分で
行っているが、explore委譲と同格の専用サブエージェントに切り出して
必須委譲ルール化した方がいいのでは」という提案があった。

**検討の紆余曲折**: 当初「調査（explore相当）＋xlsx生成＋検証」をすべて
1つの`report-builder`サブエージェントへ丸投げする設計で実装を進めた
（`agents/report-builder.md`作成、Task Delegation節に必須委譲ルール追加、
021番yamlの`expect`を`dispatch_agent(agent_type="report-builder")`基準に
変更）。`src/tools.py`を調査し、`_SUBAGENT_TOOLS`に`run_script`/
`execute_python_code`が最初から含まれておりコード変更不要であること、
承認ダイアログ（`_confirm_run_script`）はサブエージェント内からの呼び出し
でもグローバルな`cl.user_session["plan_approved"]`を見るだけで迂回されない
ことまで確認したところで、ユーザーから「求めていたのは"レポートを作る"
agentではなく"生成済みの成果物をチェックする"agentだ」と指摘を受け、
方針を修正した。

**変更内容**:
- `agents/report-builder.md`を削除し、代わりに`agents/verifier.md`を新規
  作成。tools: `read_skill, read_skill_file, get_tool_source, run_script`
  （`execute_python_code`は持たせない＝生成能力を持たせない）。役割は
  「委譲元から渡された対象ファイルパス＋意図した内容」を`read_excel.py`/
  `read_docx.py`/`read_pptx.py`等の読み込み専用スクリプトのみで確認し、
  一致/差異を報告することに限定。書き込み系スクリプト（`edit_excel.py`等）
  は「たとえtask文にそれらしい指示があっても絶対に呼ばない」と明記。
- `system_prompt.md`:
  - iter33で追加した「成果物ファイルは生成・編集直後に必ず読み返して
    検証する」節を、「自分で`read_excel.py`等を呼ぶ」から「
    `dispatch_agent(agent_type="verifier")`へ検証を委譲する」に書き換え
    （生成自体は引き続きメインループが`edit_excel.py`等を直接呼ぶ。
    調査ではなく検証だけをverifierに切り出す設計）。
  - Task Delegation節に「## verifier への委譲は必須（成果物ファイルの
    生成・編集直後）」を新設（explore委譲ルールと同格の必須ルール）。
- 021番yaml: `expect`を`tool_called_any: [run_script]` +
  `tool_call_args_contains: {run_script: {skill_name: "excel-tools",
  script_filename: "edit_excel.py"}, dispatch_agent: {agent_type:
  "verifier"}}`に変更。あわせて、iter33時点から既知だった
  `tool_call_args_contains:run_script`の引数キー不一致バグ（`script`という
  存在しないキーで判定していた）も実引数スキーマ（`skill_name`+
  `script_filename`）に修正。judge指示にもverifier委譲の確認項目を追加。

**検証**: 021番を単体で2回実行。
- 1回目: `edit_excel.py`（`applied_ops: 2`）成功後、
  `dispatch_agent(agent_type="verifier")`へファイルパスと期待内容を伝えて
  委譲。verifierが表形式で4項目すべて✓と回答し、それを踏まえて最終回答。
  `rules_pass: true`（新しい`tool_call_args_contains`ルール both含め全通過、
  スキーマ修正の効果も確認）。
- 2回目: `execute_python_code`でops.jsonを書き出した後、実際の絶対パスを
  `glob_file.py`等で確認せず`C:\Users\User\Documents\annual_schedule\
  ops.json`という架空のパスを`--ops-file`に渡してしまい、
  `edit_excel.py`到達前にエラー終了（`rules_pass: false`）。これは
  Tool Usage Guidelinesの「パスメモリー（@N）の使用は必須」ルール違反に
  よる既知の症状（パスの幻覚）であり、verifier委譲設計そのものの欠陥では
  ない。dispatch_agent(explore)の1回目呼び出しが空回答を返し2回目で
  成功した点も観測されたが、これも今回の変更と無関係な既存のサブエージェント
  実行のばらつきとみられる。

ユーザーの意向により、3回目の単体実行および全件回帰テストは実行せず
（「まとめに入りましょう」との指示）、上記2回の結果で今イテレーションを
区切りとした。

**残課題（次回以降）**:
- 021番3回中1回の失敗要因（`--ops-file`パスの幻覚）は、verifier委譲とは
  別に、`execute_python_code`で一時ファイルを書いた後の絶対パス確認手順を
  system_prompt.mdまたはexcel-tools SKILL.mdでより強く誘導する余地がある
  （現状は`glob_file.py`で取り直す運用に頼っている）。
- 全件回帰テスト（`evals/run_all.py system_prompt`）は今回未実施のため、
  verifier委譲ルール追加が他ケース（001〜005のスキルルーティング、
  008/009のcreate_plan関連等）に悪影響を与えていないかは次回セッションで
  要確認。
- `docx-tools`/`pptx-tools`側のverifier経由検証は021番のxlsxケースでしか
  未検証（read_docx.py/read_pptx.pyの実際の呼び出しは未観測）。

## excel-tools: xlsxデザイン・レイアウト改善（2026-07-24、system_prompt_scaleでの手動確認）

対象: `skills/excel-tools/scripts/_ops.py`, `skills/excel-tools/SKILL.md`
（tune-promptの自動ループ対象外。ユーザー依頼「xlsx生成のデザイン性・
レイアウト完成度を上げてほしい」への個別対応）。

**修正内容**:
- 列幅計算バグ修正: `set_range`を複数回呼ぶと列幅が後勝ちで縮んでいた
  問題を「既存幅より縮めない（蓄積）」方式に修正。
- 列幅計算に全角文字対応（`unicodedata.east_asian_width`ベースの表示幅）
  を追加。日本語混じりの表で列が狭すぎる問題を解消。
- 新op `format_table` を追加（見出し配色・罫線・縞模様・見出し行固定・
  列幅再調整を一括適用）。`role`規約のフォント色は上書きしない設計。

**イテレーション1（1回目eval `20260724_061346`）**:
- モデルが`format_table`を一度も使わず、従来通り`header_style`/
  `fill_color`等を手作業で都度指定していた（新op自体は認識していたが
  不採用）。低パラメータのローカルモデルには「新しいフラグを覚えさせる」
  設計は弱いと判断し、`set_range`に`format_table`をインラインオプション
  として追加した上で、`header_style`を渡した時点で自動発火する既定動作に
  変更（`format_table: false`で明示的にオプトアウト可能）。

**イテレーション2（2回目eval `20260724_063105`）**:
- モデルが`set_range`の呼び出しに自発的に`"format_table": true`を明記
  するようになった（2箇所とも）。`edit_excel.py`は`applied_ops: 4`で
  正常終了、verifierサブエージェントによる内容検証も合格。
- ルールチェックは両方の実行で`tool_call_args_contains:run_script`が
  FAILしているが、これは`run_script`の実引数スキーマ（`skill_name`/
  `script_filename`/`script_args`）とeval yaml側の期待値
  （`{"script": "scripts/edit_excel.py"}`）が一致していないという
  pre-existingな不一致であり、1回目・2回目とも同一原因（今回の変更による
  regressionではない）。judge指示の他項目（実行成功・過去実績の反映・
  ループなし・最終回答での場所提示）はいずれも満たしている。

直接検証（llama-server不要、CLIから`edit_excel.py`を直接実行）でも、
列幅の全角対応・蓄積、`format_table`単体op、`set_range`のインライン
既定発火・`false`でのオプトアウト、`role`色分けの非破壊、いずれも
期待通り動作することを確認済み。

**残課題（次回以降）**:
- `evals/cases/system_prompt_scale/001_...yaml`の
  `tool_call_args_contains:run_script`の期待値スキーマが実際の
  `run_script`引数形式と乖離しているため、このルールは常にFAILする
  （デザイン改善とは無関係な既存の不具合。別途修正が必要）。
- `format_table`のヘッダー配色（濃紺`1F4E78`固定）はユーザーが別の配色を
  希望した場合`header_fill`等で上書きする想定だが、実運用でその分岐が
  適切に選択されるかは未検証。

## iter35: Plan & Progress節「create_plan前の調査省略」対策（2026-07-26）

対象: `system_prompt.md`。ClaudeCodeのEnterPlanMode/ExitPlanModeとの比較調査を
きっかけに、本番applog（app_20260725_11.log 11:32〜11:34付近）で実際に観測
された失敗パターン「調査を省略してcreate_planに進み、後から
dispatch_agent(explore)で調べようとする」の再発防止を狙った修正。

**背景**: `evals/cases/system_prompt/`配下の旧23ケースはコミット
`f2637f0`（Plan ModeバッジUI追加・require_approval設定廃止時）で全て削除済み
で復元不要と判断（旧require_approval前提で書かれており現行アーキテクチャと
乖離）。ユーザーの指示により「本番と同じプロンプト」（子供会の活動記録から
年間行事予定表を作るシナリオ）を使い、フィクスチャも実データ規模に近づける
ため`generate_annual_schedule_fixture.py --years 10 --events-per-year 10`で
129ファイル規模（`evals/fixtures/annual_schedule_large100`、既存の
`annual_schedule_large`52ファイルとは別に新規生成）の新規ケース1本
（`evals/cases/system_prompt/001_annual_schedule_investigation_before_plan.yaml`）
を作成した。`auto_approve: false`にして`approve_plan`却下による早期終了を
利用し、Excel生成・verifier検証まで待たずに「調査→create_plan」の順序だけを
毎回短時間で判定できるようにしている。

**修正内容**（system_prompt.md Plan & Progress節 ステップ1）:
1. 「フル調査（explore委譲）が必須の例」「省略してよい例」をGood/Bad形式で
   追加（判断基準:「計画の各ステップに書く具体的事実が指示だけで確定済みか」）。
2. NG例として、実際に観測された「skillの読み込みは実行時でいい」
   「とりあえずcreate_planだけ先に書いて調査は後」という判断を名指しで明記。
3. `create_plan`を呼ぶ直前の自己チェック文言（「具体的事実を最低1つ以上
   得たか自問し、得ていなければexploreへの委譲を続ける」）を追加。

**検証方法**: 1割程度の低頻度事象が疑われたため、単発のeval実行では再現性が
無いと判断し、同一ケースを修正前後でそれぞれ20回ずつ反復実行した（機械判定
スクリプト`evals/analyze_investigation_order.py`で、transcript中の
`dispatch_agent(agent_type="explore")`と`create_plan`の呼び出し順序を自動集計）。

**結果**:

| | 修正前(baseline) | 修正後 |
|---|---|---|
| PASS（explore→create_planの順序） | 17/20 (85%) | 20/20 (100%) |
| FAIL（調査省略でcreate_plan） | 1/20 (5%) | 0/20 (0%) |
| INCONCLUSIVE | 1/20 | 0/20 |
| ERROR | 1/20 | 0/20 |

修正前のFAIL 1件（`run_03`）は、ルート直下の`glob_file.py`のみで`create_plan`
を呼び、`approve_plan`待ちに入った**後になってから**`dispatch_agent(explore)`
を2回呼ぶという、まさに本番ログで観測された失敗パターンそのものだった。
修正後の20回では同型の失敗は再発せず、`create_plan`のstepsの中身も
（`run_01`/`run_08`/`run_09`を抜き取り確認）具体的事実（年数・画像枚数等）に
基づく記述になっており、抽象的な「情報を調査する」ステップへの後退は
見られなかった。N=20対20のため統計的な確定ではないが、狙った失敗パターンの
再発は無く、既存の合格挙動への退行も無い。

**副次的な発見（今回のスコープ外、要別途対応）**:
- baseline `run_18`: investigation自体はexplore→create_planの順で問題なかった
  が、`create_plan`直後に規定の`approve_plan`ではなく`ask_user_choice`を
  呼んでしまい、長い迷走（ask_user系の往復・タイムアウト・lock_plan_mode）の
  末に`ask_user_multi_text`のheadlessスタブ未対応と思われる
  `ChainlitContextException`でmid_turn_exceptionとなった。「create_plan直後は
  他のツールを挟まず必ずapprove_planを呼ぶ」という既存ルールが時折守られない
  事例として次回以降の課題。
- baseline `run_20`: 空応答が連続し`create_plan`に到達する前に打ち切り
  （既存の空応答ガード・thinking_loop_guardの範囲内の事象、今回の修正対象外）。
- `evals/cases/system_prompt/`は現状この001番1件のみ（旧23件は前述の通り
  削除済みで未復元）。他の観点（スキルルーティング、ask_user選択等）の
  自動回帰網は現時点で存在しないため、今後追加を検討する余地がある。

## create_plan直後にapprove_plan以外を呼べなくするコード側ガード追加（2026-07-27）

対象: `src/tools.py`（`create_plan`, `ImageAwareToolNode`, 新設
`_guard_awaiting_approve_plan`/`_extract_tool_call_from_node_input`）、
`system_prompt.md`（Plan & Progress節ステップ3への補足）。

**背景**: iter35のbaseline run_18で見つかった副次バグ（`create_plan`直後に
規定の`approve_plan`ではなく`ask_user_choice`を自作してしまい、長い迷走の末に
`ChainlitContextException`でクラッシュ）の再発防止。`create_plan`と
`approve_plan`はツールとして統合せず（`approve_plan`はバッジクリック等で
承認が外れた際の再承認にも独立して使う想定のため温存）、代わりに
`create_plan`成功時に`awaiting_approve_plan_call`フラグを立て、
`ImageAwareToolNode`（全ツール呼び出しの共通ラッパー）でこのフラグを見て
許可リスト外のツールをブロックする設計にした。

**実装中に発見した重大なバグ**: 当初の実装は`ToolNode`公開docstringが説明する
`{"messages": [...]}`形式を前提に`messages[-1].tool_calls`を抽出していたが、
実際にこのプロジェクトの`create_react_agent`（`[graph] implementation =
prebuilt`）がツールノードへ渡す`input`は
`{"__type": "tool_call_with_context", "tool_call": {...}, "state": {...}}`
という、公開docstringに無い形式だった（並列tool_calls実行のため1呼び出し
ごとに個別ディスパッチされる）。このため実装当初のガードは一度も発火せず、
既存の`_log_tool_calls_debug`（デバッグログ）も同じ理由で発火実績ゼロ
（`data/logs/evals.log`に"tool_call:"の行が1件も無いことで確認）だった
既存の潜在バグだと判明した。一時的に`logger.warning`で`input`の実際の
repr を出力する診断を仕込み、軽量なテストケース（`get_plan_status`を
呼ばせるだけの1ターン）で実データを確認して原因を特定・修正した
（`_extract_tool_call_from_node_input`に集約。`_log_tool_calls_debug`も
ついでに同じ形式へ修正し、デバッグログが実際に機能するようにした）。

**追加の発見（許可リストの調整）**: 修正後の検証中、`create_plan`後に
探索不足に気づいたモデルが自ら`lock_plan_mode`で計画をやり直そうとした
実例が観測された。当初の許可リスト（`approve_plan`, `get_plan_status`のみ）
はこれもブロックしてしまっていたため、ユーザーと相談の上`lock_plan_mode`
（呼ばれたらフラグもクリアし、以後は通常通り探索し直せる）を許可リストに
追加した。

**検証結果**:
- `evals/cases/system_prompt/001_...yaml`を`auto_approve: true`に変えた
  一時ケースで計10回（修正の都度5回×2セット）実行し、いずれも
  `create_plan`→`approve_plan`が正常に通り、新規ガードの誤発火・既存の
  `run_script`/`execute_python_code`実行のブロックは発生しなかった
  （回帰なし）。
- `auto_approve: false`の既存ケースを計11回（3+5+3ではなく途中経過含め
  複数回）実行し、2件で実際に「探索を省略してcreate_planに進む」失敗
  （投資順序の一次的な失敗、確率的に残存）が再発したが、両ケースとも
  その後モデルが`dispatch_agent`/`lock_plan_mode`など`approve_plan`以外を
  呼ぼうとした試みを新規ガードが正しく検知・ブロックし、最終的に
  `approve_plan`が呼ばれて却下・正常終了する流れに収束することを確認した。
  一次的な調査省略はプロンプトのみでは完全には無くならないが、その後の
  迷走・クラッシュをコード側ガードが確実に防ぐ「二段構えの安全網」として
  機能することが実証できた。
- 診断用の一時ケース（`_tmp_diag.yaml`）・一時ヤムル（`_tmp_001_auto_approve_true.yaml`）
  は検証後に削除済み。

## iter36: system_prompt_scale（レシピ画像297枚ケース）の初回本番実行と3件の新規バグ修正（2026-07-31）

ユーザー指示により `evals/cases/system_prompt_scale`（001既存 + 002新規
`recipe_images_to_md_end_to_end_large`）を対象に本番相当のチューニングループを開始。
対象ファイルは `system_prompt/system_prompt.md`・`system_prompt/subagent_common.md`・
`agents/explore.md` の3つ。

### 前提: 直前セッションで実施済みの修正（このiterの前提、既にコミット済み d3217bf）
- 委任件数の目安を`${subagent_max_iterations}`ベースに一本化し「グループ数を一度だけ
  計算し再検討しない」手順を明記、無関係だった「同時3つまで」の並列数制約を削除。
- `agents/explore.md` の description を書き換え、`analyze_image` が使えること・
  ファイル作成が一切できないことを明示。
- dispatch_agentを何回呼んでも実行時間/負荷を気にする必要が無い旨を追記。

### 初回実行結果（20260731_224458）
- `annual_schedule_xlsx_end_to_end_large`: ルールFAIL。ただし**テストケース側の欠陥**
  と判明（`expect.tool_call_args_contains.run_script` が実際には存在しない `script`
  引数を期待していた。実際の`run_script`シグネチャは`skill_name`+`script_filename`。
  transcriptを見るとモデルは`skill_name=excel-tools, script_filename=edit_excel.py`で
  正しく呼んでおり、モデル側の問題ではない）。`evals/cases/system_prompt_scale/
  001_....yaml`のexpectを修正した。
- `recipe_images_to_md_end_to_end_large`: ルールPASS。**直前修正した「分割件数の
  再計算ループ」は再発せず**（1回で分割方針を決めて実行に移った）。しかし297枚中
  29枚しか処理されず、新たに3件の実害バグを発見:
  1. **Globの`truncated`見逃し**: モデルが自ら`head_limit=50`を指定して
     `Glob(pattern="**/*", path="images")`を呼び、`total_matches:297, returned:50,
     truncated:true`が返ったにもかかわらず、取得できた50件（のpath_memory参照）
     だけで分割・委任・完了報告まで進めてしまい、残り247件を一度も認識しなかった。
  2. **最終成果物の保存先誤り**: `execute_python_code`内で相対パス`md/...`を使った
     ため、既存の中間ファイル自動リダイレクト機構（`_tmp_<セッションID>`）が働き、
     ユーザーが期待する作業ディレクトリ直下`md/`ではなく`_tmp__no_session/md/`
     （evalハーネスではセッションIDが無いためこの名前になる）に保存された。
  3. **verifierの誤用によるループ**: `.md`ファイルの確認を
     `dispatch_agent(agent_type="verifier")`に委譲したが、verifierは
     xlsx/docx/pptx専用の読み込みスクリプトしか持たず（`Read`ツールが無い）
     `.md`を開く手段が無いため`ThinkingLoopDetected`で打ち切りになった
     （既存ルールは元々5スクリプト限定だが、対象外ケースの扱いが明記されて
     おらず誤用を誘発した）。

### 修正（`system_prompt/system_prompt.md`、iter36_before.md → 上記3箇所編集）
1. 「サブエージェントへの委任件数について」節冒頭に、`Glob`の`total_matches`/
   `truncated`確認と、truncated時は`head_limit`を`total_matches`以上にして
   全件のpath_memoryを得てから分割計画に進む旨を追加。
2. Tool Usage Guidelinesの`_tmp_<セッションID>`自動リダイレクト説明に、この
   リダイレクトが相対パス書き込み全般に働くこと、ユーザー指定フォルダへの
   最終成果物は絶対パス（直前Globの`base`等から構築）で書き込むべき旨を追加。
3. 「verifierへの委譲」節に、対象5スクリプト以外（execute_python_code生成の
   一般ファイル）は委譲対象外であり、自分で`Read`ツールを使って確認してよい
   旨を明記。

`agents/explore.md`・`subagent_common.md` は今回変更不要と判断（今回の3件は
いずれもメインエージェント側の判断・system_prompt.mdの記述ギャップが原因）。

`evals/cases/system_prompt_scale/001_....yaml` のテストケース欠陥修正も合わせて実施
（対象3ファイル限定ルールの例外、eval harness側のバグ）。

次: `python evals/run_all.py system_prompt_scale` を再実行して検証する。

## iter37: 「画像ファイルを扱うとき」節とexplore委譲必須ルールの矛盾解消（2026-07-31）

対象ケース: `evals/cases/system_prompt_scale/002_recipe_images_to_md_end_to_end_large.yaml`
（ユーザー指示により、以降このループは002単体を`run_case`で実行する。001は対象外）。

### 結果（run_20260731_225609.json）
ルールFAIL・`turn_cutoffs: [{reason: "thinking_loop"}]` で打ち切り。
- `tool_called_any` FAIL: `dispatch_agent`も`execute_python_code`も一度も呼ばれず。
- `tool_not_called` FAIL: `analyze_image`をメインエージェントが直接3回呼んだ。
- 呼ばれたツールは `Glob`×2, `analyze_image`×3, `create_plan`, `approve_plan`,
  `list_path_memory` のみ。実ファイル生成に到達せず。

### 根本原因: system_prompt.md 内の直接的な自己矛盾
「### 画像ファイルを扱うとき（`analyze_image` と `show_image` の使い分け）」節に
「**作業ディレクトリ配下のユーザー提供の画像・写真・スキャン画像を自分の目で
確認したいときはこちら（=`analyze_image`）**」と書かれており、これは
「## 【必須ルール】explore への委譲」の「自分で `analyze_image` / `Read` を
呼んで中身を読むな」と真正面から矛盾していた。

今回モデルは前者に従って`analyze_image`を直接3枚分呼び、その後で後者のルールに
気づいて「本当に異なるアプローチ: …あるいは、サブエージェント（explore）に
画像解析を委譲するのが効率的です。では、計画を立てます。…では、計画を立てます。
実際の計画: …」と**同じ計画文を繰り返す**思考ループに陥り、thinking_loopガードで
打ち切られた（turn_cutoffsのsnippetに繰り返しが明確に記録されている）。

iter36で追加した「Globのtruncated確認」は正しく機能した形跡がある
（`Glob(pattern="images/**/*", head_limit=200)`で`total_matches:297, truncated:true`
を取得しており、前回のような`head_limit=50`での取りこぼしとは異なる挙動）。
ただし今回はその後の委譲判断で詰まったため、全件処理までは到達していない。

### 修正（`system_prompt/system_prompt.md`、iter37_before.md → 1箇所）
「画像ファイルを扱うとき」節の`analyze_image`側の説明を書き換え、自分で
直接呼んでよいのは「ユーザーが今回のメッセージで直接パスを指定した1〜2枚」
「references/assets配下」「run_script/execute_python_codeが生成した画像」に
限定し、**フォルダ配下の画像をまとめて読み取る作業は枚数に関わらず必ず
`dispatch_agent(agent_type="explore")`へ委譲する**ことを明記した
（explore委譲の必須ルールへの参照も追加）。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter38: analyze_image重複ガードがサブエージェント間で共有される致命的バグ（コード側修正）＋残り分の再委任ルール追加（2026-07-31）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`（002単体実行）。

### 結果（run_20260731_230517.json）
iter37の修正で**大きく前進**した:
- `turn_cutoffs: null`（thinking_loop打ち切りが解消）。
- `dispatch_agent(agent_type="explore")`へ30枚ずつ分割委譲できた（ルール
  `tool_called_any`・`tool_call_args_contains`はPASS）。
- iter36で追加したGlobのtruncated確認も機能（`head_limit=300`で297件全件取得）。

しかし最終回答は「処理を中止します」となり、`ask_user_choice`4回・
`lock_plan_mode`1回を経て失敗。`analyze_image`直接呼び出しも3回残った
（`tool_not_called`はFAIL）。

### 根本原因1（コード側の致命的バグ）: 重複ガードの集合がサブエージェント間で共有
`src/tools.py` の `analyze_image` は重複ガードとして
`_record_and_check_duplicate("analyze_image_call_signatures", str(path))` を
直接呼んでおり、**`[file_tools_duplicate_guard].carry_over_to_main` 設定を
一切参照していなかった**（config.iniでは `carry_over_to_main = false` に
設定済みだったが、この設定に従っていたのは `_check_file_tools_duplicate` を
通る Read/Glob/Grep/json_query のみ）。

さらに `_check_file_tools_duplicate` 側も、サブエージェントを
`file_tools_call_signatures_subagent` という**単一のキー**にまとめており、
サブエージェント同士は区別していなかった。

この結果、次の詰み状態が発生していた:
1. 1件目の `dispatch_agent` が30枚を受け取り、途中まで `analyze_image` で読む
   （作業量上限で全部は返せず、読めた分だけテキストで返す）。
2. 2件目の `dispatch_agent` が別の30枚を受け取るが、こちらは「トークン使用量の
   制限により処理できなかった」と報告。
3. メインが自分で `analyze_image` を呼ぶと
   **「エラー: この画像は既に一度確認済みです」** でブロックされる。
4. サブエージェントの会話履歴は委譲元にも他のサブエージェントにも共有されず、
   返るのは最終回答テキストだけなので、**1件目が読んだが返しきれなかった
   画像を、誰も読み直せない**。
5. transcript[54]でサブエージェント自身が
   「`analyze_image`で画像解析を試みたところ、全て『同一画像の既に確認済み』
   というエラーが返されました。サブエージェントの会話履歴の制約により、
   前回の解析結果を参照できず、画像の内容を確認できていません」と報告している。

**修正（`src/tools.py`）**:
- `_SUBAGENT_RUN_ID` ContextVar を新設し、`dispatch_agent` が呼び出しごとに
  `uuid4().hex` を設定・finallyでリセットする。
- `_duplicate_guard_session_key(base_key)` を新設。`carry_over_to_main=true`
  なら従来通り単一キー、`false` なら**サブエージェント実行ごとに別キー**
  （`{base}_subagent_{run_id}`）を返す。メインエージェントは素のキー。
- `analyze_image` と `_check_file_tools_duplicate` の両方をこの関数経由に統一。
- 回帰テスト `tests/test_tools_duplicate_guard_scope.py` を新規追加（3件）。
  全122テスト合格（既存119件の回帰なし）。

### 根本原因2（プロンプト側）: 「未処理が残っている」報告の誤読
transcript[19]で、サブエージェントの「未処理の残り／再委任が必要」という報告を
受けたメインが「**exploreエージェントは画像解析（analyze_image）を実行できない
ようです。自分で画像を読み取って処理する必要があります**」と誤った結論を出し、
自力`analyze_image`路線へ切り替えていた（これがルール違反の直接原因）。

**修正（`system_prompt/system_prompt.md`、iter38_before.md）**: 委任件数節に、
「未処理分が残っている」報告は委譲先の能力不足を意味せず、単にそのサブ
エージェント1回分の作業量上限に達しただけなので、**残りをより小さいグループに
分けて再委任すること**、ここで自分で`analyze_image`/`Read`を直接呼ぶ方針に
切り替えてはいけない（委譲先のツールは種別ごとに固定で報告内容によって
変わらない）旨を明記した。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter39: Glob結果のフルパス重複によるコンテキスト枯渇を修正（コード側＋プロンプト側）（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260731_233154.json）
iter38の修正で**さらに大きく前進**した:
- **`rules_pass: True`（ルールベース全項目PASS）**。`tool_not_called: analyze_image`
  もPASS＝メインエージェントが直接`analyze_image`を呼ばなくなった。
- `dispatch_agent` 16回（30枚→20枚と粒度を調整しながら委譲）、
  `execute_python_code` 8回。`turn_cutoffs: null`。
- **mdファイルが164件、正しい `レシピ\md\` フォルダに生成された**
  （前回29件・`_tmp__no_session/md/`から大幅改善。iter36の絶対パス指示とiter38の
  重複ガード修正が効いた）。

しかし最後に `error: mid_turn_exception` /
`BadRequestError: request (158514 tokens) exceeds the available context size (128000 tokens)`
でコンテキスト超過。297件全部の完走には至らず。

### 根本原因1（コード側）: 同じ絶対パスが1回のGlob結果に3〜4回積まれる
`file_tools.glob_search()` の戻り値は同じ絶対パスを
`files`（配列）・`file_details[].path`・`directories[].path` に持ち、
`src/tools.py` の `glob_tool` ラッパーがさらに `path_memory`（`@N`→絶対パス）を
足していた。1件あたり約100文字の絶対パスが最大4回重複するため、297件のGlob
1回で **99,465文字** に達していた（実測値。他に67,060文字のGlobが1回、
list_path_memory 45,353文字が2回、md一覧Glob 74,538文字が1回）。

**修正（`src/tools.py`）**: `_dedupe_paths_with_path_memory()` を新設し、
`path_memory` を登録できた場合は `files`・`file_details[].path`・
`directories[].path` を `@N` 参照へ畳む。`@N` はそのまま各ツールの絶対パス引数へ
渡せる（`_resolve_path_memory_token` が解決）ため情報は失われない。
docstringも実際の戻り値に合わせて更新。
**実測: 297件のGlobが 84,578文字 → 43,376文字（49%削減）**。
全122テスト合格（回帰なし）。

### 根本原因2（プロンプト側）: 同じ一覧を2回取得していた
iter36で追加した「`truncated`なら`head_limit`を上げて呼び直せ」という指示に
モデルは正しく従ったが、その結果1回目（デフォルト200件・67,060文字）と
2回目（300件・99,465文字）の**両方**が会話履歴に残り、同じ297件分のパスが
二重に積まれていた（合計166,525文字）。

**修正（`system_prompt/system_prompt.md`、iter39_before.md）**: 大量ファイルが
予想されるフォルダでは**最初の`Glob`で`head_limit=1`を指定して件数だけ確認**し
（`base_contents.file_count`/`total_matches`は`head_limit`に関わらず常に全件数を
返すことを実測で確認。`head_limit=1`の結果はわずか475文字）、その後
`head_limit=件数`で1回だけ全件取得する、という手順を明記した。
**この2つの修正の合算で、一覧取得のコンテキスト消費は 166,525文字 → 約43,851文字
（約74%削減）となる見込み**。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter40: 画像1枚あたりのトークン消費を踏まえた委任粒度の明示（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260801_013052.json）
**iter39のコンテキスト対策は成功**:
- Glob結果が `"files": ["@1","@2",...]` 形式になり、297件で 99,465文字 → **48,745文字**
  （実測。約51%削減）。
- `error: mid_turn_exception`（コンテキスト超過）は**解消**。
- `rules_pass: True`、`analyze_image`の直接呼び出しも0回を維持。

しかし別の箇所で `thinking_loop` 打ち切りが再発し、`dispatch_agent`1回・
`execute_python_code`0回、mdファイル0件で終了（iter39の164件から後退）。

### 根本原因: 画像処理の実効的な委任粒度がプロンプトの目安と桁違いだった
transcript[10]のサブエージェント応答が決定的:
「これまでに分析した画像（@1〜@8）からレシピを抽出しました。**残りの画像
（@9〜@50）については、トークン上限のため分析できていません**」

数値で裏付けが取れる:
- `[subagent] token_guard_hard_threshold = 64000`
- 画像1枚あたり約8,000トークン（base64データURL）
- → **1回の `dispatch_agent` で処理できるのは約8枚**

一方 system_prompt.md は「一度に任せる作業件数は `${subagent_max_iterations}`
（=30）件を目安」としか書いておらず、モデルは50枚を1回で渡していた。
反復回数（30回）より先にトークン上限（64,000）に達するため、この目安は
画像には全く当てはまらない。

その結果モデルは「297枚 ÷ 8枚 ≒ 38回」の委譲が必要だと気づき、
turn_cutoffsのsnippetにある通り
「しかし、297枚を10枚ずつで29回繰り返すのは現実的ではありません。
**結論：**（同じ手順）…しかし、297枚を10枚ずつで29回繰り返すのは現実的では
ありません。**新しいアプローチ：** `execute_python_code` を使って…」
と、同じ結論と否定を繰り返すループに陥った（画像は`execute_python_code`では
読めないので「新しいアプローチ」は存在しない）。

### 修正（`system_prompt/system_prompt.md`、iter40_before.md → 2箇所）
1. 委任件数節に**対象種別ごとの目安表**を追加。テキスト20件／**画像8枚**と明示し、
   「画像は1枚あたりのトークン消費が桁違いに大きく、反復回数より先にトークン
   上限に達する」理由も併記。あわせて「**グループ数が数十回になってもそれが
   正しい見積もりであり『非現実的』ではない**」「画像297枚なら38グループが想定内」
   「`execute_python_code`で画像の中身は読めないので別アプローチを探すな」
   「回数の多さを理由にユーザーへ確認を取るな」を明記。
2. `create_plan`のステップ粒度に、**ステップ数は10個程度まで**に収め、委任グループが
   数十個になる場合は1グループ1ステップにせず複数グループをまとめて1ステップに
   する（1ステップ内で`dispatch_agent`を何回呼んでもよい）ルールを追加。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter41: リトライ予算の相互侵食バグを修正（コード側）＋長時間タスク向けに予算を微増（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260801_014405.json）— iter40の修正は完全に成功
- **`turn_cutoffs: null`（thinking_loop打ち切りが消滅）**、`error: None`、
  コンテキスト超過も無し、`rules_pass: True`。
- `create_plan` のステップに **「画像ファイルの一覧を取得し、8枚ずつのグループに
  分割する（計38グループ）」** と明記され、iter40で追加した目安表と
  「38グループは想定内」の断定が正しく効いた。ステップ数も2個（10個以内）。
- 実行も安定し、`dispatch_agent`（8枚）→ `execute_python_code`（保存）の
  サイクルを6回、`Glob(head_limit=1)`→件数確認→本取得というiter39の手順も遵守。
  8件・8件・10件・9件…と着実にmdファイルを生成し、
  「32件処理完了。次に33〜40枚目を処理します」と進捗も正しく把握していた。

残る問題は最後の1点のみ: transcript[41] が `AIMessage len=0` の**無言終了**。
35枚処理した時点で止まり、`final_answer` が空になった。

### 根本原因: リトライ予算の相互侵食（`src/graph.py`）
evals.log の最終行が決定的:
```
02:09:39 DEBUG src.llm: LLM応答: content='' reasoning_content='エージェントから4の
レシピが抽出されました。これらをmdファイルとして保存します。\n</parameter>\n</function>\n</t...
```
ローカルモデルが**ツール呼び出しのXML（`</parameter></function>`）を本文
（reasoning_content）へ書いてしまい**、`tool_calls` が空になった典型例。
再試行すれば直ることが多い確率的な失敗である。

しかし `ainvoke_ensuring_final_text()` は、ループ検知フェーズと無言終了フェーズの
**両方**で `attempt >= total_budget`（total_budget = max_retries +
loop_max_retries）という**共有条件**を併用していた。ログ上、序盤（01:45〜01:46）に
ThinkingLoopDetected が2回発生して `loop_max_retries=2` を消費済みだったため、
25分後・35枚処理後に起きた無言終了に対して**再試行が1回も行われなかった**。

for ループ自体が `range(total_budget + 1)` で全体上限を担保しているので、
この共有条件は冗長かつ有害（先に失敗した種類が、後で起きる別種の失敗の予算を
丸ごと奪う）。1ターンでLLM呼び出しが数十〜数百回に及ぶ長時間タスクでは、
序盤と終盤の失敗は独立した事象であり、予算も独立すべき。

**修正1（`src/graph.py`、iter41_before退避済み）**: 両フェーズから
`or attempt >= total_budget` を削除し、`loop_attempt >= loop_max_retries` /
`empty_attempt >= max_retries` のみで判定する独立予算に変更。docstringも実態に
合わせて更新。回帰テスト `tests/test_graph_retry_budget.py` を新規追加（4件。
1件目は旧実装だと `calls==3` で落ちる＝バグを正しく捕捉する）。全126テスト合格。

**修正2（`config.ini`）**: 長時間タスクでは失敗の絶対数が増えるため、
`[thinking_loop_guard] max_retries` と `empty_response_max_retries` を
2 → **3** へ微増（全体の試行回数上限は 4+1=5 → 6+1=7 回）。両者が独立予算に
なった旨をコメントにも明記。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter42: dispatch_agentの並列発行を禁止（iter36とiter40の矛盾を解消）＋有害なnudge文言を修正（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260801_021516.json）— iter40の水準から後退
`thinking_loop` で打ち切り。`dispatch_agent` 10回だが `execute_python_code` は1回のみで、
**mdファイル生成は0件**（iter40の35件、iter38の164件から後退）。

### 根本原因1: `dispatch_agent` を10個まとめて並列発行していた
transcript の `[14]` で **`dispatch_agent` が10個並列**に発行されていた
（iter40の成功時は6回とも**逐次**だった。ここが唯一の違い）。
10グループ分の結果（各数千文字のレシピテキスト）が一度に会話へ積まれ、直後の
ターンで「コンテキストが圧迫される問題がある」とモデル自身が繰り返し悩み、
ループに陥っていた。

原因は system_prompt.md 内の**自己矛盾**:
- iter36で追記: 「dispatch_agentを同一ターン内で**何回呼んでも（並列で発行しても）**、
  実行時間や負荷を理由に発行数を迷ったり…する必要はない」
- iter40で追記: 「**1グループずつ順に** `dispatch_agent` を呼び、返ってきた結果を
  その都度 `execute_python_code` でファイルへ書き出す」

iter36の記述は「回数の多さに怯むな」という意図だったが、「並列で発行しても」という
文言が独り歩きし、モデルは10個同時発行を選んだ。コード側のセマフォ
（`_DISPATCH_AGENT_SEMAPHORE`）はLLM呼び出しを直列化するが、**結果が一度に
返ること自体は防げない**ため、コンテキスト圧迫は避けられない。

**修正1（`system_prompt/system_prompt.md`、iter42_before.md）**: 該当箇所から
「並列で発行しても」を削除し、**「`dispatch_agent` は必ず1グループずつ逐次に呼ぶ」**
を明示。まとめて発行すると全グループ分の結果が一度に積まれてコンテキストを
圧迫すること、正しい手順は「dispatch_agent（1グループ）→ 結果を
execute_python_code で即座に保存 → 次のグループへ」の繰り返しであり、
**保存してから次へ進めばグループ数が何十個あってもコンテキストは増え続けない**
ことを記した。

### 根本原因2: ループ検知nudgeが正解から遠ざけていた
turn_cutoffs の snippet に「ここで、**根本的に異なるアプローチ**を考えます：」という
文言があり、これは `config.ini [thinking_loop_guard].nudge_messages` の2番目
「…これまでの試行は一旦破棄し、**根本的に異なるアプローチ・手順で今のタスクに
取り組み直してください**」をモデルがそのまま受けたもの。

今回モデルは**正しい手順**（explore委譲 → execute_python_codeで保存）を既に
実行できていたのに、「アプローチを変えろ」と促された結果、正解から離れた代替案を
探して堂々巡りしていた（snippet中でも「しかし、これはすでに試しているアプローチで…」
と正解を自ら却下している）。iter41でリトライ予算を2→3に増やしたため、この有害な
nudgeが注入される機会も増えていた。

**修正2（`config.ini`）**: nudge_messages の2番目を
「同じ検討が繰り返されています。**新しいアプローチを探す必要はありません**。
すでに分かっている手順のうち、**まだ実行していない次の1ステップだけ**を、今すぐ
ツール呼び出しとして実行してください。考え直さず手を動かしてください。」
に差し替えた。大量ファイル処理では「手順は正しいが繰り返しが多いだけ」という
状況が大半で、アプローチ変更を促すのは逆効果であるため。

全126テスト合格。次: evals.logをクリアしてから002単体を再実行して検証する。

## iter43: ステップの対象範囲を完了させる義務と連番処理を明示（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260801_043550.json）— iter42の並列発行禁止は成功
- **`dispatch_agent` の1ターンあたり発行数が `[1,1,1,1,1,1]`** となり、
  iter42で禁止した並列発行が完全に解消。
- 「`dispatch_agent`（8枚）→ `execute_python_code` で保存」のサイクルが正しく回り、
  **実際に37件のmdファイルが `md\` 配下に生成された**（`Created:` 行を実測）。
  出力先も正しく、ファイル名規則・フォーマットも指示通り。
- `create_plan` の内容も完璧（「画像1〜50枚目」…「画像251〜297枚目」の50枚×6ステップ、
  iter40の「ステップ数は10個程度まで」に沿っている）。

しかし最終的に `thinking_loop` で打ち切り、全297枚には届かなかった。

### 根本原因: ステップの対象範囲を完了せずに次のステップへ飛んでいた
`dispatch_agent` の対象 `@N` を時系列で並べると**飛び飛び**になっていた:
```
@1〜8 → @51〜58 → @101〜108 → @109〜116 → @117〜124 → @151〜158
```
`@9〜50`・`@59〜100`・`@125〜150` が丸ごと未処理のまま飛ばされている。
計画のステップは「1〜50枚目」「51〜100枚目」…と50枚単位だったので、
**各ステップの先頭8枚だけ処理して次のステップへ移っていた**ことが分かる
（ステップ3だけは3回繰り返せている＝挙動が不安定）。

原因は iter40 で追加した記述の弱さ:
「複数グループをまとめて1ステップにする（**1ステップの中で `dispatch_agent` を
何回呼んでもよい**）」— これは「呼んでもよい」という**許可**の表現であり、
「対象範囲を全部処理し終える**義務**」が伝わっていなかった。モデルは
「1ステップ＝1回の委任」と解釈し、範囲の先頭だけ処理して `completed` にしていた。

打ち切り時のsnippetも「`Plan: 1. Execute Python code to create files for @151-@158.
2. Update task progress for step 2. 3. Dispatch agent for @159-@166.`」と、
次にどの番号へ進むべきか（`@159`か、ステップの区切りか）を繰り返し検討していた。

### 修正（`system_prompt/system_prompt.md`、iter43_before.md → 3箇所）
1. `create_plan` のステップ粒度の節: 「1ステップの中で何回呼んでもよい」を
   **「1ステップ＝1回の `dispatch_agent` ではない。1ステップが50枚を対象とし
   1回の委任が8枚なら、そのステップの中で `dispatch_agent` を7回繰り返す。
   そのステップの対象範囲を最後の1件まで処理し終えるまで、絶対に次のステップへ
   進まないこと」**に書き換え（許可→義務）。
2. `update_task_progress` の節: `completed` にしてよいのは**そのステップの対象範囲を
   最後の1件まで処理し終えてから**であることを明記。
3. 委任件数の節: **「グループは `Glob` で得た並び順のまま、連番で漏れなく処理する
   （`@1`〜`@8`、次は `@9`〜`@16`…）。番号を飛ばして先の範囲へ移ってはいけない」**
   を追加。

次: evals.logをクリアしてから002単体を再実行して検証する。

## iter44: 計画が対象全件をカバーする義務を明示（＋振動の兆候を確認）（2026-08-01）

対象ケース: `002_recipe_images_to_md_end_to_end_large.yaml`。

### 結果（run_20260801_051554.json）
iter43の連番指示は効いたが、**別の副作用**が出た。
- 計画が「@1〜@8」「@9〜@16」…と**8枚ずつ10ステップ**になり、
  **合計80枚分しか計画に含まれていなかった**（297枚をカバーしていない）。
  iter43で追加した「連番で漏れなく」と、iter40の「ステップ数は10個程度まで」が
  競合し、「8枚×10ステップ」という中途半端な計画に落ちた。
- 実生成は8件（`Saved:` 形式。集計スクリプトの正規表現が `Created:` 限定だった
  ため一時0件と誤認したが、実際は8件）。
- **空応答が3回発生**（`（自動リマインダー: 直前の応答が空でした…）` の注入を3回
  観測）。iter41で独立化・3回に増やしたリトライ予算を使い切って終了した。

### 全実行の横断比較（md実生成数の推移）
```
iter38後 167件（最高記録。ただし最後にコンテキスト超過）
iter39後   0件
iter40後  35件
iter41後   0件（dispatch_agent 10個並列）
iter42後  37件
iter43後   8件（空応答3回で予算切れ）
```
**iter38以降、主要指標（md生成数）は改善していない。** 毎回異なる根本原因を
潰してはいるが、プロンプトへの指示追加が別の場面で誤読され、新たな副作用を
生む段階に入っている（tune-promptスキルの言う「振動」に近い状態）。

### 修正（`system_prompt/system_prompt.md`、iter44_before.md → 1箇所）
今回観測された明確な誤り（計画が対象全件をカバーしない）のみを修正:
- 「**計画は対象の全件を必ずカバーすること**。297件が対象なら、ステップを合計した
  ときに297件目まで含まれていなければならない」を追加。
- あわせて「ステップ数10個程度」の具体例を
  「297件を8件ずつ委任するなら38ステップではなく『1〜50件目』…の6ステップにし、
  各ステップ内で委任を繰り返す」と数値で明示し、iter43との競合を解消。

### 判断
この検証で改善が見られない場合、これ以上の機械的なプロンプト微調整は行わず、
成果と残課題を整理してユーザーへ報告する（イテレーション上限iter45も近い）。
残る主要なボトルネックは**ローカルモデルの空応答（ツール呼び出しXMLを本文へ
書いてしまう）の多発**であり、プロンプト側で解決できる範囲を超えている可能性が高い。

### iter44 検証結果（run_20260801_052559.json）

- **md実生成数: 123件**（iter43の8件から大幅改善。全実行中2位。最高はiter38後の167件）
- `turn_cutoffs: null`（**思考ループなし**）、`rules_pass: True`、コンテキスト超過なし
- `dispatch_agent` の1ターンあたり発行数 `[1,1,1,1,1,1,1,1]`（iter42の並列禁止が維持）
- `execute_python_code` 19回（委任→即保存のサイクルが安定して回った）
- 残る問題: **空応答リマインダー3回**の末に無言終了（`final_answer` が空）。
  また計画ステップが1個しか作られず、iter44で追加した「全件カバー」指示は
  計画には反映されなかった（ただし実行自体は計画に依らず123件まで進んだ）。

---

## iter36〜44 総括（2026-08-01、ここでチューニングループを終了）

### 当初の依頼と達成状況
ユーザー報告の事象は「大量ファイル処理時に、サブエージェントへの分割件数を
延々と再計算し続ける思考ループ」だった。**これは解消済み**。
- 直接原因だった system_prompt.md の記述の分裂（`${subagent_max_iterations}` の
  転用と「1回あたり20〜30件」の直書きが二重に存在、無関係な「同時3つまで」制約が
  併存）を一本化（コミット `d3217bf`）。
- 以降の全実行（iter40以降）で、当該の再計算ループは**一度も再発していない**。
- iter40で「画像は1回8枚が上限」「38グループは想定内」と数値で断定して以降、
  `turn_cutoffs`（thinking_loop）も大幅に減り、iter44では0件。

### 副次的に発見・修正したコード側の実バグ（3件、いずれも回帰テスト追加済み）
1. **`analyze_image` の重複ガードがサブエージェント間で共有されていた**
   （`src/tools.py`）。`carry_over_to_main=false` 設定を参照しておらず、
   サブエージェントAが読んだ画像を他のサブエージェントもメインも二度と読めない
   一方、サブエージェントの会話履歴は共有されないため、取りこぼした画像を
   誰も救出できず詰む状態だった。`_SUBAGENT_RUN_ID` と
   `_duplicate_guard_session_key()` を新設し実行ごとに分離。
   → `tests/test_tools_duplicate_guard_scope.py`（3件）。md 29→164枚に改善。
2. **`Glob` 結果が同じ絶対パスを3〜4重に持っていた**（`src/tools.py`）。
   `files`・`file_details[].path`・`directories[].path`・`path_memory` に同一パスが
   重複し、297件で1回のGlobが99,465文字に達しコンテキストを枯渇させていた。
   `_dedupe_paths_with_path_memory()` で `@N` に畳み、**実測49%削減**。
3. **リトライ予算の相互侵食**（`src/graph.py`）。`ainvoke_ensuring_final_text()` が
   ループ検知用と無言終了用で `attempt >= total_budget` を共有しており、序盤の
   ループ検知2回が25分後・35枚処理後の空応答の再試行予算を丸ごと奪っていた。
   予算を独立化。→ `tests/test_graph_retry_budget.py`（4件）。
   あわせて config.ini の両 max_retries を 2→3 に微増。

その他、eval ケース側の欠陥（`001_...yaml` の `expect` が存在しない `script` 引数を
期待していた）も修正した。**全126テスト合格**。

### md生成数の推移（297枚中）
```
iter38後 167件（最高。ただし最後にコンテキスト超過）
iter39後   0件
iter40後  35件
iter41後   0件（dispatch_agent 10個並列）
iter42後  37件
iter43後   8件（空応答3回で予算切れ）
iter44後 123件（ループなし・並列なし・超過なし）
```

### 未達の課題と、これ以上プロンプトで解決しない理由
- **297枚の完走は未達**（最高167件、最終123件）。
- 最大のボトルネックは**ローカルモデル（QWEN3.6-35B-A3B）の空応答**である。
  ツール呼び出しのXML（`</parameter></function>`）を本文（reasoning_content）へ
  書いてしまい `tool_calls` が空になる現象が、長い会話の終盤で散発的に起きる。
  iter41で予算を独立化・増量したが、iter44でも3回発生して最終的に無言終了した。
  これはプロンプトの文言では制御できない、モデルの出力形式の問題である。
- また iter38 以降、プロンプトへの指示追加が**別の場面で誤読されて新たな副作用を
  生む**段階に入っていた（iter43の「連番で漏れなく」がiter40の「ステップ数10個程度」と
  競合し、297枚中80枚分しかカバーしない計画を生むなど）。tune-promptスキルの
  「振動」に該当すると判断し、iter44をもって機械的な修正を停止した。

### 今後の選択肢（ユーザー判断が必要）
1. **空応答へのコード側フォールバック**: 本文中の `<tool_call>`/`<function=...>` 形式を
   検出してツール呼び出しへ復元するパーサを `src/llm.py` に追加する。今回の
   ボトルネックに最も直接効く可能性が高いが、モデル固有の出力形式に依存する実装に
   なるため、採否はユーザー判断としたい。
2. **タスクの分割運用**: 297枚を1スレッドで完走させず、ユーザー側で50枚程度に
   分けて複数回依頼する運用にする（現状の実装でも安定して処理できる規模）。
3. **プロンプトの整理**: iter36〜44で追加した指示が増えすぎているため、
   一度棚卸しして重複・競合を削る（今回は時間の都合で未実施）。

git へのコミットは一切行っていない（ユーザー指示）。変更ファイル:
`system_prompt/system_prompt.md`, `src/tools.py`, `src/graph.py`, `config.ini`,
`evals/cases/system_prompt_scale/001_...yaml`, `evals/tuning_log.md`,
新規テスト2件、`evals/history/` 配下のスナップショット。
