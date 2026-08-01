"""Tavily APIを使ってWeb検索を行い、結果をJSONで出力する。

web-search スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから
    python search_web.py <query> [--max-results N] [--topic general|news|finance]
        [--include-answer] [--time-range day|week|month|year]
の形で呼ばれる。

APIキー（TAVILY_API_KEY）は、プロジェクトルートの .env ではなく、
このスクリプトと同じディレクトリの .env（scripts/.env）から読む
（load_local_env、プロジェクトルートの設定とは独立に管理するため）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from _common import load_local_env, setup_utf8_stdio

API_URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = 30.0


def main() -> int:
    setup_utf8_stdio()
    load_local_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--topic", choices=["general", "news", "finance"], default="general")
    parser.add_argument("--include-answer", action="store_true")
    parser.add_argument("--time-range", choices=["day", "week", "month", "year"], default=None)
    args = parser.parse_args()

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        print(
            "TAVILY_API_KEYが設定されていません。"
            ".locohane/skills/web-search/scripts/.env.example を同じ場所に .env としてコピーし、"
            "TAVILY_API_KEYにAPIキーを設定してください（https://app.tavily.com で取得可能）。",
            file=sys.stderr,
        )
        return 1

    max_results = min(max(args.max_results, 0), 20)

    payload: dict = {
        "query": args.query,
        "max_results": max_results,
        "topic": args.topic,
        "include_answer": args.include_answer,
    }
    if args.time_range:
        payload["time_range"] = args.time_range

    try:
        response = httpx.post(
            API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        print(f"Tavily APIへの接続が{TIMEOUT_SECONDS:.0f}秒でタイムアウトしました。", file=sys.stderr)
        return 1
    except httpx.HTTPError as e:
        print(f"Tavily APIへの接続に失敗しました: {e}", file=sys.stderr)
        return 1

    if response.status_code != 200:
        messages = {
            401: "APIキーが無効です。TAVILY_API_KEYの値を確認してください。",
            429: "レート制限に達しました。しばらく待ってから再試行してください。",
            432: "Tavilyプランの利用上限に達しました。",
            433: "Tavilyの従量課金上限に達しました。",
        }
        detail = messages.get(response.status_code, f"HTTP {response.status_code}")
        print(f"Tavily APIがエラーを返しました: {detail}\n{response.text}", file=sys.stderr)
        return 1

    data = response.json()
    result = {
        "query": data.get("query", args.query),
        "answer": data.get("answer"),
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
            }
            for r in data.get("results", [])
        ],
        "response_time": data.get("response_time"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
