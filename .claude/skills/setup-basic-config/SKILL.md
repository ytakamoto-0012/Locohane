---
name: setup-basic-config
description: Locohane の環境依存パス設定一式（config.ini の [llm]セクションの main_url/sub_url（各々 base_url/api_key/model の1接続先）・[scripts]セクションの python 実行パス、app.bat の PYTHON_DIR、プロジェクト直下 CLAUDE.md の「Python実行環境」「Node.jsパス」）を、ユーザーとの対話で確認・更新する。初回セットアップ時や、別マシン・別llama-server環境への移行時、モデルを切り替えたいとき、Python/Node.jsの仮想環境を作り直したときなどに使う。「config.iniをセットアップして」「LLMの接続先を設定して」「使うモデルを変更したい」「pythonのパスを設定して」「環境パスをまとめて設定して」「/setup-basic-config」等で使う。timeout系数値を実測ベンチマークして自動チューニングする tune-config-timeouts とは別物で、こちらは自動検出を行わずユーザーへの質問ベースで接続情報・パスを設定する。main_url/sub_urlの複数接続先指定やルーティング方式(main_routing_strategy/sub_routing_strategy)の変更は対象外（手動編集）。
---

# setup-basic-config: 環境依存パス設定一式のセットアップ

`Locohane` は llama-server（llama.cpp）をローカルLLMバックエンドとして
使う。接続先や使用モデル、Python/Node.jsの実行パスは環境ごとに異なり、
かつ `config.ini` / `app.bat` / プロジェクト `CLAUDE.md` の3ファイルに
分散している。それぞれを手動で開いて書き換える代わりに、対話で
確認しながらまとめて更新する。

## 対象項目

**`config.ini`**（LLM接続先・エージェントが実行時に使うPython）

| セクション | キー | 意味 |
|---|---|---|
| `[llm]` | `main_url` | メインエージェント用のLLM接続先（1件のみ変更対象。`base_url`＝llama-server のOpenAI互換エンドポイント例: `http://localhost:8080/v1`、`api_key`＝認証キー（llama.cpp は認証不要のため通常はダミー値のまま）、`model`＝llama-server 起動時の `--alias` と一致させる必要があるモデル名） |
| `[llm]` | `sub_url` | サブエージェント（`dispatch_agent`）用のLLM接続先。形式は `main_url` と同じ。通常は `main_url` と同じ値にする |
| `[scripts]` | `python` | `run_script`/`execute_python_code`（LLMが実行時に呼び出す）が使う Python 実行ファイルの絶対パス |

**`app.bat`**（アプリ本体＝chainlitサーバーを起動する仮想環境）

| 変数 | 意味 |
|---|---|
| `PYTHON_DIR` | `chainlit run app.py` を実行する Python 仮想環境のディレクトリ |

**プロジェクト直下 `CLAUDE.md`**（Claude Code がこのプロジェクトを開発・テストする際に使う実行環境。`##` 見出しのコードブロック内のパスのみが対象）

| 見出し | 意味 |
|---|---|
| `Python実行環境` | Claude Code がスクリプト実行・動作確認に使う Python 実行ファイルの絶対パス |
| `Node.jsパス` | `frontend/` のビルド・テストに Claude Code が使う Node.js のディレクトリ |

**対象外**: 上記6項目以外（`config.ini` の timeout系 `[llm].request_timeout_seconds` 等は
`tune-config-timeouts` の担当、`[timeouts]`/`[paths]`/`[graph]`/`[subagent]` 等その他のセクション、
`CLAUDE.md` の「Python実行環境」「Node.jsパス」以外の見出し、`app.bat` の `PYTHON_DIR` 以外の行）
には一切触れない。ユーザー個人用の `~/.claude/CLAUDE.md`（グローバル設定）も対象外。

## 手順

1. `config.ini`・`app.bat`・プロジェクト直下 `CLAUDE.md` を読み、上記6項目の現在値をユーザーに提示する。
2. 各項目について「現在値のままでよいか、新しい値にするか」を対話で尋ねる。
   - 自由入力形式で確認する。llama-server への疎通確認や、Python/Node.js実行ファイルの
     自動検出（`where python` 等）は行わない。ユーザーが実際にどう構成している
     かはこちらから推測できないため、必ず本人に値を確認・入力してもらう。
   - 6項目まとめて一度に聞いても、ファイル単位・1つずつ聞いてもよい。ユーザーが
     答えやすい形で進める。
   - `app.bat` の `PYTHON_DIR` とプロジェクト `CLAUDE.md` の「Python実行環境」は
     同じ仮想環境を指すことが多いが、必ず別々に確認する（同一と決めつけない）。
   - 変更不要と答えられた項目はそのまま維持する。
3. 変更のあった項目のみ、Edit ツールで該当行を更新する
   （コメント行・他のキー・他の見出し・他のセクションは一切変更しない）。
4. 変更内容を旧値→新値の形で、ファイルごとに報告する。
   `Locohane` アプリ（またはチャットUI）が起動中の場合、
   変更を反映するには再起動が必要な旨を案内する。

## 安全策

- `config.ini`・`app.bat`・プロジェクト直下 `CLAUDE.md` 以外のファイルは変更しない。
- git へのコミット・ステージングは一切行わない（変更を実際にコミットするか
  どうかはユーザー判断に委ねる）。
- 上記6項目以外の設定キー・行・見出しは変更しない。
- 値の妥当性（URLの形式、パスの実在確認など）を勝手に検証・補正しない。
  ユーザーが入力した値をそのまま反映する。
