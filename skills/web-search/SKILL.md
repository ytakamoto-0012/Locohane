---
name: web-search
description: Tavily APIを使ってWeb検索を行い、LLMの学習データにない最新情報（最新ニュース、価格、リリース情報、最新ドキュメント等）を取得する。ユーザーが「調べて」「検索して」「最新の〜を教えて」「〇〇について今の情報を知りたい」等、リアルタイム性が必要な話題やLLMが知らない可能性が高い話題を尋ねてきたときに使う。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# web-search

Tavily APIを使ってWeb検索を行うスキルです。`run_script` ツールで
`search_web.py` を実行して結果を得ます。

## 事前準備（APIキー設定）

このスキルは `TAVILY_API_KEY` を、プロジェクトルートの `.env` ではなく
**このスキル専用の `.locohane/skills/web-search/scripts/.env`** から読み込みます。

導入手順:
1. `.locohane/skills/web-search/scripts/.env.example` を同じ場所に `.env` としてコピーする。
2. https://app.tavily.com でアカウント登録し、APIキーを取得する（無料枠あり）。
3. `.env` の `TAVILY_API_KEY=` に取得したキーを設定する。

`scripts/.env` が存在しない、または `TAVILY_API_KEY` が空の場合、スクリプトは
外部への通信を一切行わず、エラーメッセージを返すだけで終了します。設定手順を
ユーザーに案内してください。

## search_web.py — Web検索

呼び出し例:
```json
{
    "skill_name": "web-search",
    "script_filename": "search_web.py",
    "script_args": ["今日の東京の天気", "--max-results", "5"]
}
```

引数（すべて `query` 以外は省略可）:
- `query`（必須、位置引数）: 検索クエリ。
- `--max-results`: 取得する結果件数（既定5、0〜20にクランプ）。
- `--topic`: `general`（既定）/ `news` / `finance`。話題の種類に応じて指定すると精度が上がる。
- `--include-answer`: 指定するとTavily側が生成した要約回答（`answer`キー）も取得する。
- `--time-range`: `day` / `week` / `month` / `year`。直近の情報に絞りたいときに指定する。

出力例:
```json
{"query": "今日の東京の天気", "answer": "...",
 "results": [
   {"title": "東京都の天気 - 気象庁", "url": "https://...", "content": "抜粋テキスト...", "score": 0.92}
 ],
 "response_time": 0.8}
```

結果の解釈方法:
- `results` の各要素の `title`・`content` を要約してユーザーに報告し、**必ず `url` を
  出典として明記すること**（検索結果はLLMの知識ではなく外部ソースであるため）。
- `--include-answer` を指定した場合、`answer`（Tavilyによる要約回答）があればそれも
  参考情報として伝えてよいが、出典（`results`の`url`）を省略しないこと。
- `results` が空配列の場合はヒットなしとしてユーザーに伝え、クエリを変えて
  再検索するか確認する。

## エッジケース

- `TAVILY_API_KEY` 未設定: 終了コード1。stderrに `scripts/.env` への設定手順が
  出力されるので、その内容をそのままユーザーに案内する。
- APIキーが無効（401）: 終了コード非0。キーの見直しを促す。
- レート制限/プラン上限超過（429/432/433）: 終了コード非0。しばらく待つか
  Tavilyのプランを確認するようユーザーに伝える。
- タイムアウト（30秒): 終了コード非0。ネットワーク状況を確認するよう伝える。
- 依存パッケージ `httpx` が実行環境に無い場合は `ModuleNotFoundError` で
  終了コード非0になります（通常は本プロジェクトの標準依存に含まれるため
  発生しないはずです）。
