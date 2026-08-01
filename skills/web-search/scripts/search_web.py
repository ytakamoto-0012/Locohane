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
import ast
import json
import os
import sys
from urllib.parse import urlparse

import httpx
from _common import load_local_env, setup_utf8_stdio

API_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_DOMAIN_FILTER_ENTRIES = 150  # Tavily API側の上限


def _parse_domain_list(value: str) -> list[str]:
    """ドメイン指定文字列をリストに変換する。

    2つの形式を許容する:
    - config.ini の project_locohane_dir 等と同じJSON/Python風リスト形式
      （例: '["a.com", "b.com"]'。角カッコ＋改行複数行OK。.env側の値はこちら）。
    - シンプルなカンマ区切り文字列（例: "a.com,b.com"。CLI引数側で使う想定）。
    """
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"ドメイン指定はJSON/Pythonのリスト形式で指定してください: {text!r}") from e
        if not isinstance(parsed, list):
            raise ValueError(f"ドメイン指定はリスト形式で指定してください: {text!r}")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [d.strip() for d in text.split(",") if d.strip()]


def _domain_matches(netloc: str, domain: str) -> bool:
    """netlocがdomain自身、またはそのサブドメインかどうかを判定する。"""
    netloc = netloc.lower()
    domain = domain.lower()
    return netloc == domain or netloc.endswith("." + domain)


def main() -> int:
    setup_utf8_stdio()
    load_local_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--topic", choices=["general", "news", "finance"], default="general")
    parser.add_argument("--include-answer", action="store_true")
    parser.add_argument("--time-range", choices=["day", "week", "month", "year"], default=None)
    parser.add_argument(
        "--exclude-domains",
        default="",
        help="検索結果から除外するドメイン（カンマ区切り）。.envのWEB_SEARCH_BLOCKED_DOMAINSと合算される。",
    )
    parser.add_argument(
        "--include-domains",
        default="",
        help="検索結果をこのドメインのみに限定する（カンマ区切り）。"
        "指定時は.envのWEB_SEARCH_ALLOWED_DOMAINSより優先される。",
    )
    args = parser.parse_args()

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        print(
            "TAVILY_API_KEYが設定されていません。"
            "skills/web-search/scripts/.env.example を同じ場所に .env としてコピーし、"
            "TAVILY_API_KEYにAPIキーを設定してください（https://app.tavily.com で取得可能）。",
            file=sys.stderr,
        )
        return 1

    max_results = min(max(args.max_results, 0), 20)

    env_blocked = _parse_domain_list(os.environ.get("WEB_SEARCH_BLOCKED_DOMAINS", ""))
    env_allowed = _parse_domain_list(os.environ.get("WEB_SEARCH_ALLOWED_DOMAINS", ""))
    cli_excluded = _parse_domain_list(args.exclude_domains)
    cli_included = _parse_domain_list(args.include_domains)

    # exclude系は.env設定とCLI指定の和集合、include系はCLI指定があればそちらを優先
    blocked_domains = list(dict.fromkeys(env_blocked + cli_excluded))[:MAX_DOMAIN_FILTER_ENTRIES]
    allowed_domains = (cli_included or env_allowed)[:MAX_DOMAIN_FILTER_ENTRIES]

    try:
        timeout_seconds = float(os.environ.get("WEB_SEARCH_TIMEOUT_SECONDS", "").strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    payload: dict = {
        "query": args.query,
        "max_results": max_results,
        "topic": args.topic,
        "include_answer": args.include_answer,
    }
    if args.time_range:
        payload["time_range"] = args.time_range
    if blocked_domains:
        payload["exclude_domains"] = blocked_domains
    if allowed_domains:
        payload["include_domains"] = allowed_domains

    try:
        response = httpx.post(
            API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
    except httpx.TimeoutException:
        print(f"Tavily APIへの接続が{timeout_seconds:.0f}秒でタイムアウトしました。", file=sys.stderr)
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
    raw_results = data.get("results", [])

    # Tavily側のexclude_domains/include_domainsに加えて、ローカルでも同じルールを
    # 再適用する（Tavily側のフィルタ仕様変更・不具合があっても安全側に倒すための二重チェック）。
    filtered_results = []
    filtered_domain_count = 0
    for r in raw_results:
        netloc = urlparse(r.get("url", "")).netloc
        if allowed_domains and not any(_domain_matches(netloc, d) for d in allowed_domains):
            filtered_domain_count += 1
            continue
        if blocked_domains and any(_domain_matches(netloc, d) for d in blocked_domains):
            filtered_domain_count += 1
            continue
        filtered_results.append(r)

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
            for r in filtered_results
        ],
        "filtered_domain_count": filtered_domain_count,
        "response_time": data.get("response_time"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
