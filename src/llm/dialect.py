"""推論サーバーごとの reasoning（thinking）方言の差を吸収する純粋関数群。

llama-server と vLLM は、どちらも OpenAI 互換 API だが thinking まわりの
フィールド名・拡張パラメータが異なる。tools/probe_reasoning_dialect.py で
実測した結果（llama-server b10437 / Qwen3.8、vLLM 0.30.0 / Qwen3.5-9B-AWQ、
2026-09-26）に基づく対応は次のとおり。

- 応答の thinking: llama-server は `reasoning_content`、vLLM は `reasoning`
  のみ（vLLM の旧版・互換モードでは両方に同じ内容が入りうる）。受信側は
  provider に関係なく両方読み、内部では additional_kwargs["reasoning_content"]
  に正規化する（extract_reasoning）。
- 履歴へ戻す thinking: llama-server は `reasoning_content` のみ、vLLM は
  `reasoning` のみ反映し、もう一方は無視する。両方送っても重複・エラーは
  無い（両サーバーで実測）。
- 拡張パラメータ: 対応表は build_extra_body() の docstring 参照。

送信側は接続先ごとの LLMEndpoint.provider で切り替える。"openai_compatible"
は後方互換のため llama-server と同じパラメータを送り、履歴の thinking は
どちらのサーバーでも読まれるよう両方のキーで送る。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config

logger = logging.getLogger(__name__)

# 応答の thinking を探すキー（先勝ち）。vLLM の互換モードは両方に同じ内容を
# 入れるため、連結せず先に見つかった方だけを採用する。
_RESPONSE_REASONING_KEYS = ("reasoning", "reasoning_content")

# 履歴の assistant メッセージへ thinking を載せ直す際のキー。
_HISTORY_REASONING_KEYS: dict[str, tuple[str, ...]] = {
    "llama_cpp": ("reasoning_content",),
    "vllm": ("reasoning",),
    "openai_compatible": ("reasoning_content", "reasoning"),
}

# vLLM に相当パラメータが無いため送らない config 項目。
_VLLM_UNSUPPORTED_KEYS = (
    "dry_multiplier",
    "dry_base",
    "dry_allowed_length",
    "dry_penalty_last_n",
    "dry_sequence_breakers",
    "reasoning_format",
    "reasoning_budget_message",
)

# 同じ警告は build_model() のたびに出さず1回だけにする（キーは (provider, 項目)）。
_warned_unsupported: set[tuple[str, str]] = set()


def extract_reasoning(fields: dict[str, Any] | None) -> str | None:
    """delta / message / additional_kwargs から thinking テキストを取り出す。

    Args:
        fields: ストリームの delta、非ストリームの message、または
            AIMessage.additional_kwargs に相当する dict。

    Returns:
        `reasoning` → `reasoning_content` の順で最初に見つかった空でない
        文字列。どちらも無ければ None。
    """
    if not fields:
        return None
    for key in _RESPONSE_REASONING_KEYS:
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def history_reasoning_keys(provider: str) -> tuple[str, ...]:
    """履歴の assistant メッセージへ thinking を載せるキーを返す。

    Args:
        provider: LLMEndpoint.provider。

    Returns:
        書き込むキーのタプル。未知の provider は "openai_compatible" 扱い。
    """
    return _HISTORY_REASONING_KEYS.get(provider, _HISTORY_REASONING_KEYS["openai_compatible"])


def _warn_once(warn_key: tuple[str, str], message: str) -> None:
    if warn_key in _warned_unsupported:
        return
    _warned_unsupported.add(warn_key)
    logger.warning(message)


def _warn_unsupported_once(provider: str, key: str) -> None:
    _warn_once((provider, key), f"[llm].{key} は provider={provider} の接続先では対応するパラメータが無いため送信しません")


def build_extra_body(config: Config, provider: str) -> dict[str, Any]:
    """OpenAI 標準 API に無い拡張パラメータ（extra_body）を provider 別に組み立てる。

    None（config.ini で空欄）の項目は送らずサーバー既定に委ねる。

    | config.ini [llm]          | llama_cpp / openai_compatible | vllm                    |
    |---------------------------|-------------------------------|-------------------------|
    | top_k                     | top_k                         | top_k                   |
    | repeat_penalty            | repeat_penalty                | repetition_penalty      |
    | dry_*                     | dry_*                         | 送らない（警告）        |
    | enable_thinking           | chat_template_kwargs          | chat_template_kwargs    |
    | reasoning_preserve        | chat_template_kwargs の preserve_reasoning / preserve_thinking（両方）|
    | reasoning_effort          | reasoning_effort（default は送らない。none なら enable_thinking=false も送る）| 同左 |
    | reasoning_format          | reasoning_format              | 送らない（警告）        |
    | reasoning_budget          | reasoning_budget_tokens       | thinking_token_budget（-1 は送らない）|
    | reasoning_budget_message  | reasoning_budget_message      | 送らない（警告）        |

    llama-server はリクエストの `reasoning_budget` を黙って無視し、
    `reasoning_budget_tokens` のみ反映する（実測）。`reasoning_effort=default`
    はテンプレートへ文字列 "default" がそのまま渡り、対応していない
    テンプレートでは HTTP 500 になる（実測）ため、どの provider でも送らない。

    Args:
        config: アプリ設定。
        provider: 送信先の LLMEndpoint.provider。

    Returns:
        ChatOpenAI の extra_body に渡す dict（空の場合もある）。
    """
    is_vllm = provider == "vllm"
    extra_body: dict[str, Any] = {}

    if config.top_k is not None:
        extra_body["top_k"] = config.top_k
    if config.repeat_penalty is not None:
        extra_body["repetition_penalty" if is_vllm else "repeat_penalty"] = config.repeat_penalty

    llama_only: dict[str, Any] = {
        "dry_multiplier": config.dry_multiplier,
        "dry_base": config.dry_base,
        "dry_allowed_length": config.dry_allowed_length,
        "dry_penalty_last_n": config.dry_penalty_last_n,
        "dry_sequence_breakers": config.dry_sequence_breakers,
        "reasoning_format": config.reasoning_format,
        "reasoning_budget_message": config.reasoning_budget_message,
    }
    for key, value in llama_only.items():
        if value is None:
            continue
        if is_vllm:
            _warn_unsupported_once(provider, key)
        else:
            extra_body[key] = value

    chat_template_kwargs: dict[str, Any] = {}
    if config.reasoning_effort == "none":
        # vLLM は chat_template_kwargs.enable_thinking=true を top-level の
        # reasoning_effort=none より優先し、thinking が止まらない（実測）。
        # enable_thinking だけを読むテンプレートでも止まるよう false で送る。
        if config.enable_thinking:
            _warn_once(
                ("*", "reasoning_effort=none"),
                "[llm].reasoning_effort=none のため [llm].enable_thinking=true を無視し、enable_thinking=false で送信します",
            )
        chat_template_kwargs["enable_thinking"] = False
    elif config.enable_thinking is not None:
        chat_template_kwargs["enable_thinking"] = config.enable_thinking
    if config.reasoning_preserve is not None:
        # テンプレートにより参照する変数名が異なる（llama.cpp標準は
        # preserve_reasoning、Qwen/Gemma系は preserve_thinking）ため両方送る。
        chat_template_kwargs["preserve_reasoning"] = config.reasoning_preserve
        chat_template_kwargs["preserve_thinking"] = config.reasoning_preserve
    if chat_template_kwargs:
        extra_body["chat_template_kwargs"] = chat_template_kwargs

    if config.reasoning_effort is not None and config.reasoning_effort != "default":
        extra_body["reasoning_effort"] = config.reasoning_effort

    if config.reasoning_budget is not None:
        if not is_vllm:
            extra_body["reasoning_budget_tokens"] = config.reasoning_budget
        elif config.reasoning_budget >= 0:
            extra_body["thinking_token_budget"] = config.reasoning_budget

    return extra_body
