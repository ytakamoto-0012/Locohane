---
name: setup-basic-config
description: Locohane の config.ini のうち、環境依存で変わりやすい基本4項目（[llm]セクションの base_url / api_key / model、[scripts]セクションの python 実行パス）を、ユーザーとの対話で確認・更新する。初回セットアップ時や、別マシン・別llama-server環境への移行時、モデルを切り替えたいとき等に使う。「config.iniをセットアップして」「LLMの接続先を設定して」「使うモデルを変更したい」「pythonのパスを設定して」「/setup-basic-config」等で使う。timeout系数値を実測ベンチマークして自動チューニングする tune-config-timeouts とは別物で、こちらは自動検出を行わずユーザーへの質問ベースで接続情報・パスを設定する。
---

# setup-basic-config: config.ini 基本項目のセットアップ

`Locohane` は llama-server（llama.cpp）をローカルLLMバックエンドとして
使う。接続先や使用モデル、Pythonの実行パスは環境ごとに異なるため、
`config.ini` を手動で開いて書き換える代わりに、対話で確認しながら更新する。

## 対象項目

| セクション | キー | 意味 |
|---|---|---|
| `[llm]` | `base_url` | llama-server のOpenAI互換エンドポイント（例: `http://localhost:8080/v1`） |
| `[llm]` | `api_key` | 認証キー（llama.cpp は認証不要のため通常はダミー値のまま） |
| `[llm]` | `model` | llama-server 起動時の `--alias` と一致させる必要があるモデル名 |
| `[scripts]` | `python` | `run_script`/`execute_python_code` が使う Python 実行ファイルの絶対パス |

**対象外**: 上記4項目以外（timeout系の `[llm].request_timeout_seconds` 等は
`tune-config-timeouts` の担当、`[timeouts]` セクションの人間応答待ち系、
`[paths]`/`[graph]`/`[subagent]` 等その他のセクション）には一切触れない。

## 手順

1. `config.ini` を読み、上記4項目の現在値をユーザーに提示する。
2. 各項目について「現在値のままでよいか、新しい値にするか」を対話で尋ねる。
   - 自由入力形式で確認する。llama-server への疎通確認や、Python実行ファイルの
     自動検出（`where python` 等）は行わない。ユーザーが実際にどう構成している
     かはこちらから推測できないため、必ず本人に値を確認・入力してもらう。
   - 4項目まとめて一度に聞いても、1つずつ聞いてもよい。ユーザーが答えやすい
     形で進める。
   - 変更不要と答えられた項目はそのまま維持する。
3. 変更のあった項目のみ、Edit ツールで該当行を更新する
   （コメント行・他のキー・他のセクションは一切変更しない）。
4. 変更内容を旧値→新値の形で報告する。
   `Locohane` アプリ（またはチャットUI）が起動中の場合、
   `config.ini` の変更を反映するには再起動が必要な旨を案内する。

## 安全策

- `config.ini` 以外のファイルは変更しない。
- git へのコミット・ステージングは一切行わない（変更を実際にコミットするか
  どうかはユーザー判断に委ねる）。
- 上記4項目以外の設定キーは変更しない。
- 値の妥当性（URLの形式、パスの実在確認など）を勝手に検証・補正しない。
  ユーザーが入力した値をそのまま反映する。
