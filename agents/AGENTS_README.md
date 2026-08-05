# サブエージェント実装仕様（ClaudeCode の `.claude/agents/*.md` 相当）

このディレクトリ（`agents/`）配下に置く各サブエージェント種別の実装仕様をまとめる。
`skills/SKILLS_README.md`（Agent Skills 仕様準拠）の姉妹文書。
準拠元: ClaudeCode の `.claude/agents/*.md`（Anthropic公式のサブエージェント仕様）。

実装の中核は3ファイル:
- `src/agent_types.py` … `agents/` 配下の走査・frontmatter検証・`AgentType` 定義
- `src/tools.py`  … ツール名の解決（`_resolve_agent_types()`）・委譲ツール本体（`dispatch_agent`）・計画承認ガード
- `src/subagent.py` … 委譲されたサブエージェント自身の独立した ReAct ループ

## 1. ディレクトリ構成

```
agents/
  <agent-name>.md   # 1ファイル = 1エージェント種別。frontmatter + システムプロンプト本文
```

`skills/<skill-name>/` のようなフォルダ単位ではなく、**ファイル単位**（`scripts/`や`references/`のようなサブフォルダは存在しない）。

- `<agent-name>` はファイル名（拡張子除く stem）。frontmatterの `name` と**完全一致必須**（不一致は起動時スキャンでスキップされ、警告ログのみで起動は継続する）。
- `.locohane/agents/` にも同名の仕組みでファイルを置けて、`agents_dir`（`config.py` 879行）とマージされる。同名の `name` があれば `.locohane` 側が後勝ちで上書きする（`agent_types.py` 184-193行）。

## 2. \*.md の形式

```markdown
---
name: worker              # 必須。1〜64文字、小文字英数字・ハイフン・アンダースコアのみ、
                            # 先頭末尾は区切り文字不可、区切り文字の連続不可、ファイル名(stem)と一致
description: ...            # 必須。1〜1024文字。「何をする専用エージェントか」を書く
tools: read_skill, Read, Glob, run_script   # 任意。カンマ区切り文字列 or YAMLリストのどちらも可
---

# 本文（システムプロンプトとして丸ごとサブエージェントに渡る Markdown 自由記述）
```

- `name` / `description` の検証ルールは `src/agent_types.py` の `_validate()`（100-126行）が唯一の正。`_NAME_RE`（25行）は `skills.py` の `Skill.name` と同じ正規表現を踏襲。
- 検証に落ちたファイルは黙ってスキップされる（例外で全体を落とさない設計）。ログ（`app.log`）で `仕様違反のためスキップ` を確認できる。成功時は `エージェント種別発見: <name>`（155行）。
- `description` は **メインエージェントが `dispatch_agent` 呼び出し時に `agent_type` を選ぶ唯一の手がかり**（利用可能なエージェント種別一覧としてシステムプロンプトに列挙される）。「何を委譲できる専用エージェントか」を具体的に書くこと（既存4種別を参照）。
- `tools` は省略可能。**省略した場合は `_SUBAGENT_TOOLS`（後述）を丸ごと継承する**（`_resolve_agent_types()` 398-399行。Anthropic仕様の「tools省略時は全ツール継承」を踏襲したと384行のdocstringに明記）。書式はカンマ区切り文字列（Anthropic公式仕様の主形式、`_parse_tools_field()` 81行コメント）・YAMLリストのどちらでも受け付ける。

## 3. `tools:` フィールドとツール名の解決

サブエージェントに渡せるツールの実体は、`tools.py` の `_SUBAGENT_TOOLS` リスト（3340-3366行、メモリー系ツール定義より後に置く必要があるため `_BASE_TOOLS` 直前に配置）に列挙された固定セットのみ:

```
read_skill, read_skill_file, provide_download, show_image,
run_script, run_script_background, check_script_job, stop_script_job,
execute_python_code, execute_python_code_background,
get_tool_source, check_work_dir_status, analyze_image,
read_tool(=Read), glob_tool(=Glob), grep_tool(=Grep),
json_query, list_path_memory, write_scratch_note,
create_memory, update_memory, delete_memory,
read_memory, search_memory, list_memories
```

- メモリー系6ツールは `_SUBAGENT_TOOLS` に含まれてはいるが、実際に各サブエージェントへ渡るかは `agents/*.md` 側の `tools:` 次第。`explore`/`explore-docs` は読み込み系（`read_memory`/`search_memory`/`list_memories`）のみ、`worker` は全6ツール（フルアクセス）、`verifier` は含めていない。

- `Read`/`Glob`/`Grep` のように大文字始まりでfrontmatterに書く名前は、Python側の関数名（`read_tool`等）とは別に `@tool("Read")` のようにデコレータ引数で明示された `.name` 属性。frontmatterには **`.name` の方**（`Read`/`Glob`/`Grep`）を書く。
- `_resolve_agent_types()`（378-417行）が `tool_lookup = {t.name: t for t in _SUBAGENT_TOOLS}`（395行）を作り、frontmatterの `tools:` に書かれた名前と突き合わせて解決する。**未知のツール名は例外を出さず警告してスキップ**（405-409行）— 誤字に気づきにくいので、追加・変更時はアプリ起動ログを必ず確認すること。
- 上記リストに **`dispatch_agent` 自体は含まれていない**。これが「サブエージェントはさらに別のサブエージェントへ委譲できない」という制約の実体（各.md本文にある「委譲する手段を持たない」という注記は、この一点のみで担保される説明であり、それ以外の特別な強制ロジックは無い）。

## 4. `{{skills}}` プレースホルダーと共通注意事項の自動連結

- `app.py` 525-527行。`scan_agent_types()` の後、`render_skills_block(skills)`（`src/skills.py`、`name: description` 形式のスキル一覧）を各エージェントの `system_prompt` 内の `{{skills}}` へ `str.replace` で差し込む（`dataclasses.replace` でイミュータブルに更新）。**スキルの本文そのものは含まれず、一覧のみ**（skills側の progressive disclosure 第1段階と同じ扱い）。
- `app.py` 550行。`system_prompt/subagent_common.md`（作業量・トークン上限に達した際の振る舞いを指示する共通文）を**全エージェントの system_prompt 末尾に自動連結**する。個々の `agents/*.md` 側で同様の注意書きを重複して書く必要はない。

## 5. メインエージェントからの呼び出し方法

メインエージェントは `dispatch_agent(task: str, agent_type: str)`（`tools.py` 2421-2487行）でサブエージェントに委譲する。

- `agent_type` は `agents/*.md` の `name` と一致させる必須引数（既定値なし）。
- 内部で `_AGENT_TYPES.get(agent_type)` を引き、解決済みツール・システムプロンプトを使って `run_subagent(task, resolved.tools, resolved.system_prompt, _LLM_CONFIG, _SUBAGENT_MAX_ITERATIONS)`（`subagent.py` 316行〜）を呼ぶ。サブエージェントは委譲元と**独立した ReAct ループ**（別の会話履歴）で動き、思考過程・途中のツール呼び出しは委譲元と共有されない。
- 委譲元に返るのは、サブエージェントが最後に返す「tool_calls を伴わないメッセージ」の content のみ。各 `agents/*.md` 本文が「最終回答を必ず書け、無言で終わるな」と強調しているのはこのため（空文字で終えると委譲元には何も伝わらない）。
- `dispatch_agent` 実行中は `_IN_SUBAGENT` コンテキスト変数が `True` になる（2458行）。

## 6. 計画承認（`create_plan`/`approve_plan`）による書き込みブロック

`worker.md` が使う `run_script`/`execute_python_code` は、計画未承認だとブロックされる。

- `_prepare_script_execution()` 内、`tools.py` 1531-1539行で `cl.user_session.get("plan_approved")` を判定する（`(skill_name, script_filename)` が `_PLAN_APPROVAL_EXEMPT_SCRIPTS` に含まれる場合のみ免除）。`execute_python_code` も同様の判定が2210-2212行にある。
- `cl.user_session` は Chainlit のセッションスコープ。メインエージェントの `create_plan`/`approve_plan`（同ファイル2563行/2607行、`plan_approved` を `cl.user_session.set`）が更新した値を、サブエージェント内から呼ばれるツールもそのまま参照する。**サブエージェント専用の特別なロジックは無く、セッション状態の共有のみで実現されている。**
- したがって `explore`/`explore-docs`/`verifier` のように `execute_python_code`/`run_script` を持たない（または読み込み専用スクリプトしか呼ばない前提の）エージェントはこの制約と無関係だが、`worker` のように書き込み系ツールを持つエージェントは、委譲元で計画承認が済んでいないと途中でブロックされる。ブロックされた場合、サブエージェント自身は `approve_plan` を呼ぶ手段を持たないため、`worker.md` はリトライせず「計画未承認のため書き込みができなかった」旨を最終回答に明記するよう指示している。

## 7. 新しいサブエージェント種別を追加する手順

1. `agents/<agent-name>.md` を作成（frontmatter必須、`name` はファイル名(stem)と一致）。
2. `tools:` に必要なツール名を `_SUBAGENT_TOOLS`（3節参照）の中から選んでカンマ区切りで列挙する（省略時は全ツール継承）。
3. 本文に、委譲元から見た役割・使ってよい/いけないツールの区別・手順・最終回答で書くべき内容（および書いてはいけない内容）を明記する。既存4種別（`explore`＝読み取り専用の汎用調査、`explore-docs`＝Office文書/PDF調査に特化、`verifier`＝成果物の検証専用、`worker`＝計画承認後の書き込み実作業）を参考にする。
4. **アプリを再起動する**（`app.py` の `_setup()` は起動後1回しか `scan_agent_types()` を呼ばない冪等関数のため、ホットリロードは無い。新規チャットセッションを開いただけでは再スキャンされない）。起動ログの `エージェント種別発見: <name>` を確認する。
5. 実際にチャットから、メインエージェントが `dispatch_agent(agent_type="<agent-name>", ...)` を正しく呼び出し、サブエージェントが意図した最終回答を返すことを確認する。

## 8. Anthropic（ClaudeCode）仕様との関係

`agent_types.py` 冒頭コメント（3行）に「ClaudeCode の `.claude/agents/*.md` 相当」と明記されている通り、frontmatter形式（`name`/`description`/`tools`）・`tools` 省略時の全ツール継承・カンマ区切りを主形式とする書式は、Anthropic公式のサブエージェント仕様の挙動を踏襲している。

ただし以下は本プロジェクト独自の実装であり、ClaudeCode本体のSubagentランタイムをそのまま使っているわけではない点に注意（`SKILLS_README.md` 6節と同様の位置づけ）:

- LLM本体は **llama.cpp server（OpenAI互換API）** に接続しており、Claude/Anthropic APIは使用していない（`src/graph.py` の `build_model()` 参照）。
- `dispatch_agent` はこのプロジェクトが `src/tools.py` に独自実装した委譲ツールであり、ClaudeCode本体のTaskツール実装そのものではない。
- サブエージェントは委譲元と別の独立した ReAct ループ（`src/subagent.py`）で動く自前実装であり、さらに別のサブエージェントへ再委譲する経路は `_SUBAGENT_TOOLS` に `dispatch_agent` を含めないことで意図的に塞いでいる（ClaudeCode本体でのネスト委譲可否とは無関係に、本プロジェクトの設計判断）。
- `SKILLS_README.md` が言及する「公式仕様URLへの準拠宣言」に相当する記述は `agents/` 側には無く、「`.claude/agents/*.md` 相当」というコード内コメントのみが根拠。

つまり「**frontmatter形式・tools解決ルールの設計思想はAnthropic（ClaudeCode）仕様に倣っているが、実行系（LLM・委譲の仕組み）はAnthropicのものではなく完全に自前**」というのが正確な位置づけ。
