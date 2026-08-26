"""1つの eval ケースをヘッドレスで実行し、結果を1行 JSON として標準出力へ出す。

使い方:
    python -m evals.run_case evals/cases/system_prompt/001_xxx.yaml

Chainlit サーバーは起動しない。src.tools が依存する cl.* は
evals.headless_chainlit.install() でスタブに差し替えてから
src.* をインポート・実行する。ログ・進捗はすべて stderr へ出し、
stdout は run_all.py が json.loads する1行専用にする。

対象プロンプト資産（system_prompt.md 等）はディスク上の現在の内容を
そのまま読む。チューニングループはこのファイルを直接編集する運用のため、
このスクリプト自体は対象ファイルを一切書き換えない（読むだけ）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import aiosqlite
import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

# evals/ から見たプロジェクトルートを sys.path に通す
# （`python -m evals.run_case` を別ディレクトリから呼ばれても import src.* できるように）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Windows のコンソールコードページ（既定 cp932）で標準出力/標準エラーが
# 文字化けするのを防ぐ。run_all.py は subprocess の出力を encoding="utf-8" で
# デコードするため、ここで揃えないと JSON 解析が壊れる。
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from evals.case_schema import EvalCase, Expect, load_case  # noqa: E402
from evals.headless_chainlit import install as install_headless_chainlit  # noqa: E402
from evals.timing_callbacks import LatencyCallbackHandler  # noqa: E402


def _check_llm_reachable(base_url: str) -> str | None:
    """base_url の OpenAI 互換エンドポイントに疎通確認する。

    Args:
        base_url: config.ini の [llm].base_url（例: http://localhost:8080/v1）。

    Returns:
        疎通できれば None、失敗すればエラー内容を表す文字列。
    """
    try:
        httpx.get(f"{base_url.rstrip('/')}/models", timeout=5)
    except httpx.HTTPError as e:
        return str(e)
    return None


def _serialize_messages(messages) -> list[dict]:
    """LangGraph の MessagesState をシリアライズ可能な dict のリストへ変換する。

    Args:
        messages: HumanMessage/AIMessage/ToolMessage 等の列。

    Returns:
        各要素が {"type", "content", "tool_calls"?, "tool_name"?} の dict のリスト。
    """
    out = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        entry: dict = {"type": type(msg).__name__, "content": content}
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [{"name": tc.get("name"), "args": tc.get("args")} for tc in tool_calls]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", None):
            entry["tool_name"] = msg.name
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            entry["usage_metadata"] = dict(usage)
        # dispatch_agent 等、content_and_artifact 形式のツールが artifact に
        # token_usage を載せている場合のみ抜き出す（画像データ等は含めない）。
        artifact = getattr(msg, "artifact", None)
        if isinstance(artifact, dict) and "token_usage" in artifact:
            entry["token_usage"] = artifact["token_usage"]
        out.append(entry)
    return out


_USAGE_TOTAL_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def _sum_usage(transcript: list[dict]) -> dict:
    """transcript 中の usage_metadata / token_usage を合算する。

    Args:
        transcript: _serialize_messages() が返した会話（の一部）。

    Returns:
        {"input_tokens", "output_tokens", "total_tokens"} の合計値 dict。
        トークン数を取得できたメッセージが1件も無ければ全て0になる
        （config.ini [llm].track_token_usage=false の場合等）。
    """
    total = dict.fromkeys(_USAGE_TOTAL_KEYS, 0)
    for entry in transcript:
        for source in (entry.get("usage_metadata"), entry.get("token_usage")):
            if not isinstance(source, dict):
                continue
            for key in _USAGE_TOTAL_KEYS:
                total[key] += source.get(key, 0) or 0
    return total


# 低パラメータモデルで安定して処理を継続できる、1リクエストあたりのトークン数の上限。
# 累計ではなくリクエスト単位で見る必要がある（累計が何百万になっても、1回ずつが
# 小さければ処理は続けられる。逆に累計が小さくても1回がこの値を超えると詰まる）。
_PER_CALL_TOKEN_CEILING = 64000


def _max_usage_per_call(transcript: list[dict]) -> dict:
    """メインエージェントのLLM呼び出し1回あたりの最大トークン数を求める。

    _sum_usage() が返す累計だけでは「1リクエストあたりが上限を超えていないか」を
    判定できない。実際に大量ファイル処理が停止した事例では、累計277万トークンの
    うち34回中23回が1回あたり64000を超えており、最後はコンテキスト上限に張り付いて
    いた。この退行を結果JSONだけで検知できるようにするための集計。

    Args:
        transcript: _serialize_messages() が返した会話全体。

    Returns:
        {"input_tokens", "output_tokens", "total_tokens"} それぞれの最大値と、
        "calls"（usage を取得できた呼び出し回数）、
        "calls_over_ceiling"（total_tokens が _PER_CALL_TOKEN_CEILING 以上だった回数）。
    """
    result = dict.fromkeys(_USAGE_TOTAL_KEYS, 0)
    calls = 0
    calls_over_ceiling = 0
    for entry in transcript:
        usage = entry.get("usage_metadata")
        if not isinstance(usage, dict):
            continue
        calls += 1
        for key in _USAGE_TOTAL_KEYS:
            result[key] = max(result[key], usage.get(key, 0) or 0)
        if (usage.get("total_tokens", 0) or 0) >= _PER_CALL_TOKEN_CEILING:
            calls_over_ceiling += 1
    return {
        **result,
        "calls": calls,
        "calls_over_ceiling": calls_over_ceiling,
        "ceiling": _PER_CALL_TOKEN_CEILING,
    }


def _evaluate_expect(expect: Expect, transcript: list[dict], final_answer: str) -> dict:
    """Expect のルールを transcript / final_answer に照らして判定する。

    Args:
        expect: ケースの期待値定義。
        transcript: _serialize_messages() が返した会話全体。
        final_answer: 最後のツール呼び出しを含まない AIMessage の content。

    Returns:
        ルール名 -> {"pass": bool, "detail": str} の dict
        （定義されていないルールはキー自体を含めない）。
    """
    results: dict = {}
    called_tools: set[str] = set()
    args_by_tool: dict[str, list[dict]] = {}
    for entry in transcript:
        for tc in entry.get("tool_calls") or []:
            name = tc.get("name")
            if not name:
                continue
            called_tools.add(name)
            args_by_tool.setdefault(name, []).append(tc.get("args") or {})

    if expect.tool_called_any:
        ok = any(name in called_tools for name in expect.tool_called_any)
        results["tool_called_any"] = {
            "pass": ok,
            "detail": f"expected any of {expect.tool_called_any}, called={sorted(called_tools)}",
        }

    if expect.tool_not_called:
        violated = [n for n in expect.tool_not_called if n in called_tools]
        results["tool_not_called"] = {
            "pass": not violated,
            "detail": f"unexpectedly called: {violated}" if violated else "ok",
        }

    for tool_name, partial in expect.tool_call_args_contains.items():
        calls = args_by_tool.get(tool_name, [])
        ok = any(all(call.get(k) == v for k, v in partial.items()) for call in calls)
        results[f"tool_call_args_contains:{tool_name}"] = {
            "pass": ok,
            "detail": f"expected {partial} in one of {calls}",
        }

    if expect.response_contains:
        missing = [s for s in expect.response_contains if s not in final_answer]
        results["response_contains"] = {
            "pass": not missing,
            "detail": f"missing: {missing}" if missing else "ok",
        }

    if expect.response_not_contains:
        found = [s for s in expect.response_not_contains if s in final_answer]
        results["response_not_contains"] = {
            "pass": not found,
            "detail": f"unexpectedly present: {found}" if found else "ok",
        }

    return results


async def _run(case: EvalCase) -> dict:
    """対象ファイルを現在の内容のまま読み込み、1ケースを実行する。

    Args:
        case: 実行する eval ケース。

    Returns:
        run_all.py 向けの結果 dict（1件分）。
    """
    # headless_chainlit.install() 適用後にインポートする
    # （src.tools 内の cl.xxx 参照が呼び出し時に解決されるため順序自体は問わないが、
    #  意図を明確にするためここでインポートする）。
    from dataclasses import replace

    from src.agent_types import render_agent_types_block, scan_agent_types
    from src.config import expand_config_vars, load_config
    from src.graph import ainvoke_ensuring_final_text, build_graph
    from src.llm import ThinkingLoopDetected
    from src.memory import render_memory_block
    from src.skills import build_system_prompt, render_skills_block, scan_skills
    from src.tools import init_tools

    # メモリー系ツール（create_memory 等）を評価すると本番の永続メモリーストア
    # （config.ini 既定の ./data/memory）を汚してしまうため、ケース実行のたびに
    # 使い捨ての一時ディレクトリへ隔離する（src/config.py の MEMORY_DIR 環境変数を
    # 利用、config.ini の値より優先される）。1プロセス=1ケースなので、同一ケース内の
    # 複数ターンをまたぐメモリー操作は同じ一時ディレクトリ内で完結する。
    # パスメモリー（skills/path-memory）も同様の理由で使い捨ての一時
    # ディレクトリへ隔離する（本番の ./data/path_memory を汚さない）。
    # ケースが work_dir を指定している場合、フィクスチャそのものを作業
    # ディレクトリにすると、xlsx生成等の副作用がフィクスチャに残ってしまい
    # 次回実行の前提条件を変えてしまう（annual_schedule.xlsx が生成物として
    # 残ったまま次の実行で「既存ファイルがある」状態から始まってしまった
    # 事故が実際に発生した）。使い捨ての一時ディレクトリへ内容をコピーして
    # からそちらを作業ディレクトリにすることで、フィクスチャを常に読み取り
    # 専用のまま保つ。
    workdir_cm = tempfile.TemporaryDirectory(prefix="evals_workdir_") if case.work_dir else contextlib.nullcontext(None)
    with (
        tempfile.TemporaryDirectory(prefix="evals_memory_") as tmp_memory_dir,
        tempfile.TemporaryDirectory(prefix="evals_path_memory_") as tmp_path_memory_dir,
        workdir_cm as tmp_workdir,
    ):
        os.environ["MEMORY_DIR"] = tmp_memory_dir
        os.environ["PATH_MEMORY_DIR"] = tmp_path_memory_dir
        # ケースが work_dir（プロジェクトルート相対パス）を指定していれば、
        # run_script/execute_python_code/view_image の既定作業ディレクトリを
        # そこへ固定する（例: 大量ファイル探索シナリオのフィクスチャ）。
        # 1プロセス=1ケースなので他ケースへの影響はない。
        if case.work_dir:
            src_dir = (_PROJECT_ROOT / case.work_dir).resolve()
            dst_dir = Path(tmp_workdir) / src_dir.name
            shutil.copytree(src_dir, dst_dir)
            os.environ["DEFAULT_WORKDIR"] = str(dst_dir)
        else:
            os.environ.pop("DEFAULT_WORKDIR", None)
        config = load_config()

        # app.py の _setup() と同様、config.log_level に従いログをファイルへ出す。
        # eval実行中はChainlitのUIが無く進捗を外部から確認する手段が無いため
        # （PC再起動後の022番ケース再実行が実際に長時間応答なしとなり、フリーズか
        # ループかを判別できなかった経緯がある）、ツール呼び出し等のINFOログを
        # 本番のapp.logとは別ファイル（evals.log）へ出す。run_all.pyから複数ケースが
        # 直列実行されるため追記モード固定（上書きすると直前のケースの記録が消える）。
        log_level = config.log_level
        if log_level != "none":
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.DEBUG if log_level == "debug" else logging.INFO)
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            file_handler = logging.FileHandler(config.log_dir / "evals.log", mode="a", encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            # aiosqlite（checkpointer）・openai/httpx/httpcore（LLM呼び出し）が
            # DEBUGレベルで生のmsgpackペイロードやリクエスト全文（system_prompt
            # 含む）を大量に出力し、ツール呼び出しの進捗を追いたいという本来の
            # 目的を埋もれさせるため、これらのロガーだけWARNING以上に抑制する。
            for _noisy_logger in ("aiosqlite", "openai", "httpx", "httpcore"):
                logging.getLogger(_noisy_logger).setLevel(logging.WARNING)
            logging.getLogger(__name__).info("=== eval case 開始: %s ===", case.id)

        # main_endpoints の先頭エントリを使って疎通確認する（config.ini の main_url 相当）
        main_url = config.main_endpoints[0].base_url if config.main_endpoints else "http://localhost:8080/v1"
        unreachable = _check_llm_reachable(main_url)
        if unreachable:
            return {
                "case_id": case.id,
                "target": case.target,
                "error": "llm_unreachable",
                "detail": (f"{main_url} に接続できませんでした: {unreachable}。" "llama.cpp server が起動しているか確認してください。"),
            }

        skills = scan_skills([config.skills_dir, *config.locohane_skills_dirs])
        system_prompt = build_system_prompt(skills, config.system_prompt_path)
        # app.py の _setup() と同じ手順で agent_type_defs を構築し、
        # {{agent_types}}/{{memory}} プレースホルダーを置換する
        # （本番と同じシステムプロンプトで評価するため）。
        agent_type_defs = scan_agent_types([config.agents_dir, *config.locohane_agents_dirs])
        skills_block = render_skills_block(skills)
        agent_types_block = render_agent_types_block(agent_type_defs)
        agent_type_defs = [
            replace(
                a,
                system_prompt=a.system_prompt.replace("{{skills}}", skills_block).replace(
                    "{{agent_types}}", agent_types_block
                ),
            )
            for a in agent_type_defs
        ]
        system_prompt = system_prompt.replace("{{memory}}", render_memory_block(config.memory_dir))
        system_prompt = system_prompt.replace("{{agent_types}}", agent_types_block)
        # config.ini の値を ${変数名} として埋め込めるよう展開する（app.py の
        # _setup() と同じ手順、{{...}}置換完了後に行う）。
        system_prompt = expand_config_vars(system_prompt, config)

        # サブエージェント共通の注意事項を各 agent_type の system_prompt 末尾に
        # 連結する（app.py の _setup() と同じ手順）。
        subagent_common = expand_config_vars(
            (config.system_prompt_path.parent / "subagent_common.md").read_text(encoding="utf-8"),
            config,
        )
        agent_type_defs = [replace(a, system_prompt=f"{a.system_prompt}\n\n{subagent_common}") for a in agent_type_defs]

        # キーワード引数で渡す（init_tools は開発中でシグネチャが変わりうるため、
        # 位置引数だとズレて誤った型を渡してしまう事故が起きやすい）。
        init_tools(
            skills_root=[*config.locohane_skills_dirs, config.skills_dir],
            script_python=config.script_python,
            script_timeout=config.script_timeout,
            llm_config=config,
            agent_type_defs=agent_type_defs,
            subagent_max_iterations=config.subagent_max_iterations,
            default_workdir=config.default_workdir,
            memory_root=config.memory_dir,
            help_path=config.help_path,
            path_memory_dir=config.path_memory_dir,
            path_memory_max_entries=config.path_memory_max_entries,
            code_exec_enabled=config.code_exec_enabled,
            approval_timeout_seconds=config.approval_timeout_seconds,
            ask_user_question_timeout_seconds=config.ask_user_question_timeout_seconds,
            ask_user_choice_timeout_seconds=config.ask_user_choice_timeout_seconds,
            dispatch_agent_max_parallel=config.subagent_max_parallel,
            script_background_max_runtime_seconds=config.script_background_max_runtime_seconds,
            script_background_job_retention_seconds=config.script_background_job_retention_seconds,
            script_background_min_poll_interval_seconds=config.script_background_min_poll_interval_seconds,
            script_background_min_poll_message=config.script_background_min_poll_message,
            plans_dir=config.plans_dir,
            plan_approval_exempt_scripts=config.script_plan_approval_exempt_scripts,
            agent_type_run_script_allowlist=config.script_agent_type_run_script_allowlist,
        )

        thread_id = str(uuid.uuid4())
        # recursion_limit を明示指定する。省略すると LangGraph のデフォルト（25）が
        # 使われてしまい、本番 app.py（config.ini [graph].recursion_limit、既定50）
        # より低い上限で GraphRecursionError が発生する eval 固有の誤検知を生んでいた。
        # config_timeouts ターゲット（evals/analyze_timing.py）向けに、LLM呼び出し・
        # run_script/execute_python_code の所要時間をターン単位で実測する。
        # 本番の run_config には無いキーだが、LangGraph の callbacks は
        # prebuilt グラフ（create_react_agent）・ImageAwareToolNode の双方に
        # そのまま伝播するため、本番グラフ実装（src/graph.py）は変更不要。
        timing_handler = LatencyCallbackHandler()
        run_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": config.graph_recursion_limit,
            "callbacks": [timing_handler],
        }

        result = None
        token_usage_by_turn: list[dict] = []
        turn_timings: list[dict] = []
        prev_len = 0
        mid_turn_error: str | None = None
        turn_cutoffs: list[dict] = []

        # checkpointer は本番 app.py と同じ AsyncSqliteSaver（シリアライズ経由）を
        # 使う。MemorySaver はオブジェクトをそのまま保持しシリアライズを経由しない
        # ため、AsyncSqliteSaver でのみ顕在化しうる問題（artifact のシリアライズ
        # 不能等）を検出できていなかった。ファイルI/Oを避けるため :memory: を使う
        # （1プロセス=1ケースなので、接続はこのブロックを抜けると自動的に閉じられる）。
        async with aiosqlite.connect(":memory:") as conn:
            checkpointer = AsyncSqliteSaver(conn)
            await checkpointer.setup()
            graph = await build_graph(config, system_prompt, checkpointer)

            try:
                for turn_index, turn in enumerate(case.turns):
                    timing_handler.reset()
                    try:
                        result = await ainvoke_ensuring_final_text(
                            graph,
                            {"messages": [HumanMessage(content=turn)]},
                            run_config,
                            max_retries=config.thinking_loop_guard_empty_response_max_retries,
                            nudge_messages=config.thinking_loop_guard_nudge_messages,
                            loop_max_retries=config.thinking_loop_guard_max_retries,
                        )
                    except (GraphRecursionError, ThinkingLoopDetected) as e:
                        # 本番 app.py はこの2例外をターンごとに捕捉し、打ち切り
                        # メッセージを出してそのターンだけ終え、会話全体は継続する
                        # （app.py の on_message、GraphRecursionError/ThinkingLoopDetected
                        # ハンドラ）。以前は下の except Exception で会話全体を
                        # mid_turn_exception として中断させていたため、本番なら継続する
                        # はずの会話が eval だけ失敗扱いになる誤検知があった。
                        reason = "recursion_limit" if isinstance(e, GraphRecursionError) else "thinking_loop"
                        cutoff_entry = {"turn_index": turn_index, "reason": reason}
                        snippet = getattr(e, "snippet", "")
                        if snippet:
                            # thinking_loop 検知時にバッファされていた直近テキスト
                            # （src/llm.py の ThinkingLoopDetected.snippet）。真の反復
                            # ループか、構造化テキストによる誤検知かを事後判別するため
                            # results.json に残す。
                            cutoff_entry["snippet"] = snippet
                        turn_cutoffs.append(cutoff_entry)
                        state = await graph.aget_state(run_config)
                        turn_messages = list(state.values.get("messages", [])) if state else []
                        cutoff_text = f"[eval] {reason} に達したため、このターンを打ち切りました。"
                        # 本番同様、グラフの永続状態には残さない（aupdate_state は
                        # 呼ばない）。このケースの transcript/final_answer 用にのみ
                        # 合成メッセージを積む。
                        turn_messages = turn_messages + [AIMessage(content=cutoff_text)]
                        result = {"messages": turn_messages}

                    turn_messages = result["messages"]
                    token_usage_by_turn.append(_sum_usage(_serialize_messages(turn_messages[prev_len:])))
                    prev_len = len(turn_messages)
                    turn_timings.append({"turn_index": turn_index, **timing_handler.summary()})
            except Exception as e:  # noqa: BLE001 - コンテキスト長超過等でも途中経過を残す
                mid_turn_error = f"{type(e).__name__}: {e}"
                # ainvoke が例外で中断しても、checkpointer にはそこまでの状態が
                # ステップごとに残っているため、デバッグ用に部分的な transcript を拾う。
                state = await graph.aget_state(run_config)
                result = {"messages": state.values.get("messages", [])} if state else None

            messages = result["messages"] if result else []
            transcript = _serialize_messages(messages)
            token_usage_total = _sum_usage(transcript)
            token_usage_max_per_call = _max_usage_per_call(transcript)

            final_answer = ""
            for entry in reversed(transcript):
                if entry["type"] == "AIMessage" and not entry.get("tool_calls"):
                    final_answer = entry["content"]
                    break

            rule_results: dict = {}
            if case.expect is not None:
                rule_results = _evaluate_expect(case.expect, transcript, final_answer)

            out = {
                "case_id": case.id,
                "target": case.target,
                "notes": case.notes,
                "final_answer": final_answer,
                "transcript": transcript,
                "rule_results": rule_results,
                "rules_pass": all(r["pass"] for r in rule_results.values()) if rule_results else None,
                "judge": case.judge,
                "token_usage_by_turn": token_usage_by_turn,
                "token_usage_total": token_usage_total,
                "token_usage_max_per_call": token_usage_max_per_call,
                "turn_timings": turn_timings,
            }
            if turn_cutoffs:
                out["turn_cutoffs"] = turn_cutoffs
            if mid_turn_error is not None:
                # transcript は途中経過として残しつつ、正常完了ではないことを明示する。
                out["error"] = "mid_turn_exception"
                out["detail"] = mid_turn_error
            return out


def main() -> int:
    """CLI エントリポイント。結果 JSON を1行 stdout に出す。"""
    if len(sys.argv) != 2:
        print("使い方: python -m evals.run_case <case.yaml>", file=sys.stderr)
        return 2

    case_path = Path(sys.argv[1])
    case = load_case(case_path)
    install_headless_chainlit(case.auto_approve, case.scripted_text_answers)

    try:
        result = asyncio.run(_run(case))
    except Exception as e:  # noqa: BLE001 - モデルの幻覚呼び出し等も診断可能なJSONにする
        result = {
            "case_id": case.id,
            "target": case.target,
            "error": "runtime_exception",
            "detail": f"{type(e).__name__}: {e}",
        }
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
