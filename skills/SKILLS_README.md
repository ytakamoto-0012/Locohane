# スキル実装仕様（Agent Skills 準拠）

このディレクトリ（`skills/`）配下に置く各スキルの実装仕様をまとめる。
準拠元: [Agent Skills specification](https://agentskills.io/specification)

実装の中核は3ファイル:
- `src/skills.py` … 第1段階 Discovery（スキル走査・システムプロンプト注入）
- `src/tools.py`  … 第2・3段階 Read/Execute（`read_skill` / `read_skill_file` / `run_script` / `view_image`）
- `src/graph.py`  … 上記ツールを LLM に `bind_tools` してReActループを回す

## 1. ディレクトリ構成

```
skills/
  <skill-name>/
    SKILL.md            # 必須。frontmatter + 手順本文
    scripts/             # 任意。run_script が実行できるのはここ配下のみ
      xxx.py
    references/           # 任意。read_skill_file で読む補助資料
      notes.md
    assets/               # 任意。read_skill_file で読むテンプレート等
```

- `<skill-name>` はディレクトリ名。`SKILL.md` の `name` と**完全一致必須**（不一致は起動時スキャンでスキップされ、警告ログのみで起動は継続する）。
- `scripts/` / `references/` / `assets/` という名前自体はコードで強制されていない。強制されているのは「`run_script` はスキルフォルダの `scripts/` 配下にあるファイルしか実行できない」という1点のみ（`tools.py` の `_resolve_script_filename`）。呼び出し側は `scripts/` プレフィックスを書く必要はなく、ファイル名のみを渡せば `scripts/` 配下を再帰探索して解決する（同名ファイルが複数階層にある場合は最も浅い階層を採用）。`references/` `assets/` は Agent Skills 仕様上の慣例名であり、`read_skill_file` は skills ルート配下であればどのパスでも読める。

## 2. SKILL.md の形式

```markdown
---
name: word-counter          # 必須。1〜64文字、小文字英数字・ハイフン・アンダースコアのみ、先頭末尾は区切り文字不可、区切り文字の連続不可、ディレクトリ名と一致
description: ...            # 必須。1〜1024文字。「何をするか」+「いつ使うか」を書く
license: MIT                # 任意
metadata:                   # 任意
  author: ytakamoto
  version: "1.0"
---

# 本文（Markdown 自由記述）
```

- `name` / `description` の検証ルールは `src/skills.py` の `_validate()` が唯一の正。
- 検証に落ちたスキルは黙ってスキップされる（例外で全体を落とさない設計）。ログ（`app.log`）で `仕様違反のためスキップ` を確認できる。
- `description` は **LLMがスキルを選ぶ唯一の手がかり**（第1段階でシステムプロンプトに `name: description` 形式で列挙されるのみ、本文は含まれない）。「何をするか」だけでなく「どんなユーザー発話で使うべきか」を書くこと（既存2スキルを参照）。

## 3. progressive disclosure の3段階

| 段階 | 誰が | 何を | LLMに見えるもの |
|---|---|---|---|
| 1. Discovery | `scan_skills()` (起動時1回) | 全スキルの frontmatter を走査 | システムプロンプト内の `name: description` 一覧 |
| 2. Read | `read_skill` ツール | 該当スキルの `SKILL.md` 本文全体 | Markdown本文の生テキスト |
| 3. Execute | `read_skill_file` / `run_script` / `view_image` ツール | 補助資料の読み込み／スクリプト実行／画像閲覧 | ファイル内容、スクリプトの実行結果テキスト、または画像そのもの（Vision入力） |

LLMがどのスキルを読むか・どのスクリプトを叩くかは**すべてLLMの推論に委ねる**。コード側に選択ロジックはない（詳細は各ツールのdocstringとシステムプロンプトの指示文）。

## 4. カスタムツール（scripts/配下）からLLMへ値を渡す方法

**ここがコードに明文化された仕様がなく、今回追記した部分。**

### 4-1. 受け渡しの実体

`run_script` は `subprocess.run(..., capture_output=True, text=True)` でスクリプトを実行し、その結果を次の固定フォーマットの**1本のテキスト**に整形して返す（`tools.py` の `run_script` 末尾）:

```
[終了コード] <returncode>
[標準出力]
<stdout の内容（末尾空白除去）>
[標準エラー]
<stderr の内容（末尾空白除去、あれば）>
```

このテキストが LangChain の `ToolMessage.content` としてそのまま会話履歴に積まれ、次の LLM 呼び出し時にコンテキストとして渡る。つまり **スクリプトが LLM に値を渡す唯一の経路は標準出力（stdout）と終了コード** であり、それ以外（戻り値オブジェクト・グローバル変数・ファイルへの書き込みだけで済ませる、等）は一切LLMに伝わらない。

### 4-2. 制約

- **テキストのみ**。`encoding="utf-8", errors="replace"` でデコードされるため、バイナリ・画像等をそのまま `run_script` の戻り値として返す経路はない。生成物を見せたい場合はファイルに保存し、そのパスを stdout に含めて後続手順で扱わせる — **画像ファイルであれば `view_image` ツールでLLMへ視覚情報として渡せる**（`references/`/`assets/`配下の既存画像だけでなく、`run_script` がその場で生成した画像ファイルも同じ経路で見せられる。対応拡張子: png/jpg/jpeg/gif/webp/bmp）。ただしこれはVision対応モデルが前提であり、テキスト専用モデルでは画像部分は無視される点に注意。
- **タイムアウトあり**（既定60秒、`config.ini` の `script_timeout` で変更可）。超過時は `run_script` が「エラー: スクリプトが N 秒でタイムアウトしました。」を返し、スクリプト側の出力は破棄される。
- **`.py` は設定された Python 実行ファイルで起動**（`config.ini` の `script_python`）。それ以外の拡張子はOSに実行を委ねる（Windowsネイティブ環境のため、shebang行は解釈されない点に注意。`.py` 以外のスクリプトを置く場合は `.bat`/`.exe`等、Windowsで直接実行可能な形式にすること）。
- **作業ディレクトリ（cwd）はスキルフォルダではなく、ユーザーの作業ディレクトリ**（`tools.py` の `_resolve_workdir()`。Chainlit設定の `work_dir`、未設定時は `config.ini` の `default_workdir`）になる。スキル自身のファイル（`scripts/`内の補助モジュール等）を参照する場合は `Path(__file__).resolve().parent` を使い、cwd起点の相対パスに依存しないこと。生成物をスキル実行のたびに使い捨てたいだけなら、cwd配下のセッション専用一時フォルダ `_tmp_<thread_id>/`（環境変数 `AGENT_THREAD_ID` で取得、会話終了時に自動削除される。`pdf-tools` の `render_pdf_pages.py` 参照）に書くと、スキル本体のディレクトリを汚さず済む。
- 呼び出し側は `skill_name` とスクリプトのファイル名（`script_filename`）のみを渡す。`_resolve_script_filename()` が `skill_name/scripts/` 配下（`_safe_path()` により `skills/` ルート配下に強制、ディレクトリトラバーサル対策）を再帰探索して解決するため、`scripts/` プレフィックスや絶対パスを書く必要はない。見つからない場合・`scripts/` ディレクトリ自体が無い場合は実行前にエラーを返す。

### 4-3. 推奨する値渡しの規約（コード非強制・慣例）

既存スキル `word-counter` に倣い、以下を推奨する:

1. スクリプトは**構造化データを1行のJSONとしてstdoutへ**出力する（`print(json.dumps(result, ensure_ascii=False))`）。
2. **正常系は終了コード0**、stdoutにJSON。
3. **異常系は終了コード非0**、エラーメッセージは**stderr**へ（stdoutを汚さない）。
4. **原因が自明でない広い例外捕捉**（`except Exception as e:` でサードパーティ
   ライブラリ（COM、openpyxl、python-pptx/docx等）由来のエラーを丸めて報告する
   箇所）では、`str(e)` 一行だけでなく **`traceback.format_exc()` も併せて
   stderrへ出力する**こと（例: `print(f"...に失敗しました: {e}\n{traceback.format_exc()}", file=sys.stderr)`）。
   `str(e)` 一行しか返らないと、LLM（特にworkerサブエージェント）が原因を
   特定できず手探りの再試行でトークンを浪費する事例が実際に起きたため
   （excel-renderのCOM保存失敗が発端）。逆に、自前のバリデーションが
   `raise ValueError("...")` 等で原因を明示しているエラーは、メッセージ自体が
   既に自明なので traceback の追加は不要。
5. `SKILL.md` の本文に、**JSONのどのキーをどう解釈してユーザーに報告すべきか**を明記する（例: 「その `lines`/`words`/`chars` をユーザーへ日本語で分かりやすく報告する」）。LLMはコードを実行するのではなく**テキストとして返ってきたJSONを読んで解釈するだけ**なので、キーの意味をSKILL.md側で説明しておかないと誤読・幻覚のもとになる。
6. エッジケース（ファイル不在、空入力等）の挙動と、その際にLLMがユーザーに何を伝えるべきかも `SKILL.md` に明記する。

JSON化は必須ではない（`git-commit-style` のように知識のみでスクリプトを持たないスキルもある）が、構造化データを返す場合はこの規約に従うと `SKILL.md` の手順が書きやすい。

### 4-4. ファイルを生成するスキルの追加規約: `output_path` / `output_paths`

`pdf-tools`/`pptx-create` のように、スクリプトが新規ファイル（PDF/PPTX/DOCX/XLSX等）を
生成する場合は、正常終了時のJSON出力に **`output_path` キー（生成した絶対パスの文字列）を
必ず含める**こと。1回の実行で複数ファイルを生成し、それらをまとめて1メッセージの
ダウンロードボタン列として見せたい場合は、代わりに **`output_paths` キー（絶対パスの
文字列のリスト）** を使う（両方存在する場合は `output_paths` が優先される）。

`app.py` の `on_tool_end` はツール結果の文字列から `src/files.py` の
`extract_generated_files()` を使ってこれらのキーを自動検出し、実在するファイルであれば
Chainlit UI上に `cl.File`（または画像なら `cl.Image`）付きメッセージとしてダウンロード
可能な添付を自動送信する（ツール名やスキル名による分岐は行わないため、この規約さえ
守れば新しい生成スキルを追加しても `app.py` 側のコード変更は一切不要）。複数件でも
1つのメッセージにまとめてボタンが並ぶ。

```json
{"output_path": "C:\\foo\\out.pptx", "total_slides": 4, "size_bytes": 34200}
```

```json
{"output_paths": ["C:\\foo\\out1.pdf", "C:\\foo\\out2.pdf"]}
```

`output_path` / `output_paths` 以外のキー名（`path`、`file`、`paths` 等）では検出
されない点に注意。

### 4-5. 画像をチャットメッセージ・Markdownテーブルへ埋め込む規約

スキルが生成・参照する画像をユーザーへ見せる経路は2つあり、`SKILL.md` の
本文（LLMへの指示）にどちらを使わせたいかを明記する必要がある。

1. **画像そのものを独立した1メッセージとして見せるだけでよい場合**:
   `show_image` ツールを呼ばせる（`file_path` に絶対パスを渡す）。表や
   他のテキストと組み合わせる必要が無い、単純な「生成した画像を見せて」
   ケース向け。
2. **回答本文の構造（一覧表など）の一部として画像を組み込みたい場合**:
   ツールは呼ばせず、**LLM自身の回答テキストに直接** Markdown画像記法
   `![説明](絶対パス)` を書かせる。Markdownテーブルのセル内に書いても
   構わない。`app.py` の `_send_answer()`（送信直前フック）が
   `_embed_local_images_as_session_urls()` を通し、実在する画像ファイルを
   指す `![...](絶対パス)` だけを自動でブラウザから取得可能なURLへ変換して
   から送る（ローカル絶対パスのままではブラウザは画像を読み込めないため。
   `show_image` と同じ、Chainlitのセッションファイル配信の仕組みに乗せる）。

いずれの方式でも、`SKILL.md` の指示文には以下を明記すること:

- パスは**必ず絶対パス**（スクリプトのJSON出力にある絶対パスをそのまま使う）。
  相対パスや存在しないパスは変換されず、壊れた画像として表示される。
- 方式2（回答本文への直接埋め込み）では **`@N`（パスメモリー参照）は使えない**。
  `@N` はツール引数専用の解決対象であり、回答本文（Markdown）の中に書いても
  解決されない。`Glob` 結果の `path_memory`（`{"@N": 絶対パス, ...}`）から
  対応する実パスを取り出させること。
- 方式2で埋め込まれるのは**縮小済みのサムネイル**（既定 長辺320px・JPEG品質70、
  `config.ini` の `[images].inline_preview_max_long_side_pixels`/
  `inline_preview_jpeg_quality`）。Vision向けの `analyze_image`/アップロード画像
  縮小設定（`max_long_side_pixels`/`jpeg_quality`）とは別物で、意図的に小さい
  （表示帯域・ディスク使用量を抑える目的。細部を確認させたい／高解像度で
  見せたい場合は方式2ではなく `analyze_image`（LLM自身が解析する場合）または
  `show_image`（独立メッセージとして原寸で見せる場合）を使わせること）。
- 表の**前に同じ内容（対象パスの一覧など）を番号付きリスト等で重複して書かせない**
  こと。表の直前に別のブロックがあり間に空行が無いと、Markdownパーサーが表を
  独立ブロックと認識できず直前のブロックの続きとして飲み込んでしまい、表ごと
  （画像も含めて）描画されなくなることがある（実機検証で確認済みの不具合）。
  `_send_answer()`（`app.py`）が送信直前に空行を自動挿入する保険は入れてある
  が、`SKILL.md` 側でも「一覧は表だけで完結させる」よう指示しておくのが安全。

**テンプレート例**（複数の画像を生成し、一覧表で見せるスキルの `SKILL.md` 本文）:

```markdown
## 出力の解釈方法

`scripts/render_gallery.py` は正常終了時、次のJSONを標準出力へ返す:

    {"images": [
      {"label": "1枚目", "output_path": "C:\\...\\out1.png"},
      {"label": "2枚目", "output_path": "C:\\...\\out2.png"}
    ]}

ユーザーへは、`show_image` を呼ばず、回答本文に直接次の形式でMarkdown表を
書いて画像プレビュー付きの一覧として示すこと（`output_path` は絶対パスの
まま使う。`@N` に置き換えない）:

    | No. | ラベル | プレビュー |
    |---|---|---|
    | 1 | 1枚目 | ![1枚目](C:\...\out1.png) |
    | 2 | 2枚目 | ![2枚目](C:\...\out2.png) |
```

## 5. 新しいスキルを追加する手順

1. `skills/<skill-name>/SKILL.md` を作成（frontmatter必須、`name` はディレクトリ名と一致）。
2. 必要なら `skills/<skill-name>/scripts/` にスクリプトを置く（`.py` 推奨、Windows実行環境のため）。
3. `SKILL.md` 本文に、いつ使うか・手順・`run_script` の呼び出し引数・出力の解釈方法・エッジケースを書く。
4. アプリを再起動して `scan_skills()` に発見させる（ホットリロードなし。起動ログの `スキル発見: <name>` を確認）。
5. 実際にチャットから使ってみて、`read_skill` → （必要なら `read_skill_file` / `view_image`）→ `run_script` の順にツールが呼ばれることを確認する。

## 6. Anthropic互換について

`SKILL.md` のfrontmatter形式・progressive disclosureの3段階構成は [Anthropic の Agent Skills 仕様](https://agentskills.io/specification)（Claude Code / Claude.ai のSkill機能と同じ仕様）に準拠している。そのため **SKILL.md自体の書式はClaude向けスキルと相互互換**であり、他のAgent Skills対応環境（Claude Code等）に同じ `skills/<name>/` フォルダを持っていけばそのまま認識される可能性が高い。

ただし以下は本プロジェクト独自の実装であり、Anthropicのランタイムをそのまま使っているわけではない点に注意:

- LLM本体は **llama.cpp server（OpenAI互換API）** に接続しており、Claude/Anthropic APIは使用していない（`src/graph.py` の `build_model()` 参照）。
- `read_skill` / `read_skill_file` / `run_script` / `view_image` という4ツールはこのプロジェクトが `src/tools.py` に独自実装したものであり、Claude Code本体のSkillツール実装そのものではない（挙動を仕様に沿って再現しているだけ）。
- 仕様のうち本プロジェクトが実装しているのは frontmatter検証・3段階progressive disclosureのみ。仕様に存在しうるその他の付随機能（あれば）は未実装。

つまり「**SKILL.mdのフォーマット・設計思想はAnthropic仕様準拠だが、実行系（LLM・ツール実装）はAnthropicのものではなく完全に自前**」というのが正確な位置づけ。
