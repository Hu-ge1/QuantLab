"""Agentic 工具调用循环 — AI 的大脑与手的连接器。

一轮流程：
  1. 组装 system prompt（身份 + 工具规范 + 长期记忆）
  2. 非流式调用 LLM → 模型决定是否调工具
  3. 执行工具 → SSE 推 tool_call / tool_result，结果回喂模型
  4. 循环（护栏内），最终文本用真流式或伪流式输出

护栏：最大工具轮数 8、错误预算 3、结果截断 8000 字符、
重复调用指纹检测（同参重复调用直接回喂警告，不再真实执行）。

SSE 事件协议（data: {json}\n\n）：
  {"type": "thinking"}
  {"type": "tool_call",   "tool", "args", "call_id"}
  {"type": "tool_result", "tool", "call_id", "ok"}
  {"type": "token",       "text"}
  {"type": "error",       "text"}
  {"type": "done"}
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import AsyncIterator

import httpx

from agent.memory import build_memory_prompt
from agent.tools import TOOL_DEFINITIONS, execute_tool, to_anthropic_tools

MAX_TOOL_ROUNDS = 8
MAX_ERRORS = 3
MAX_RESULT_CHARS = 8000
CHUNK_SIZE = 10  # 伪流式分块字符数

SYSTEM_PROMPT_BASE = """你是 QuantLab 的量化投资研究助手，服务对象是做 A 股研究的个人投资者。

## 你的原则
1. **每个数字都有来源**：行情、估值、成分股等数据必须通过工具获取，绝不凭训练记忆编造。回答时注明数据来源与截止日期。
2. **决策走贝叶斯**：用户问「该不该买/卖/加仓/止盈」这类决策问题时，必须调用 bayesian_decision 工具做概率分析，而不是直接拍脑袋给结论。
3. **先确认代码再取数**：提到具体证券时先用 ql_search 确认代码（注意 000001.SH 是上证指数、000001.SZ 是平安银行），再取行情/基本面。
4. **选股必须真实扫描**：用户问选股、筛选、批量找股票时必须调用 ql_screen_stocks，不得凭印象列股票。必须说明扫描范围、命中数、数据日期、来源和因子定义。

## 可用工具
- ql_search：按名称/代码搜索证券，确认准确代码
- ql_get_price：历史 K 线与区间摘要（最新收盘、涨跌幅、均线）
- ql_get_fundamentals：个股估值（PE/PB/市值等）
- ql_get_index_stocks：指数成分股
- ql_screen_stocks：全 A 股批量筛选与多因子排序（FFD 优先）
- ql_diagnose_basket：候选组合历史收益/波动/回撤体检；必须明确事后选择偏差
- portfolio_get：读取用户在 QuantLab 中录入的持仓与盈亏
- qmt_account：读取 QMT 实盘账户只读快照（真实资产/持仓/委托，QMT 在线时）
- qmt_realtime：QMT 实时 tick 最新价（盘中需要最新价时用）
- bayesian_decision：贝叶斯决策分析（决策类问题必用）
- memory_save：把用户稳定偏好存入长期记忆

## 工具调用规范
- 同一问题需要多组数据时，尽量在一轮里并行调用多个工具
- count 参数按需取：看短期走势 60 根即可，看长周期趋势可取 250 根
- 用户问「我的持仓/账户」时：portfolio_get 是用户手工录入的持仓，qmt_account 是 QMT 实盘快照，注意区分并说明数据来源
- 数据返回的 source 字段是 "demo·模拟数据" 时，必须向用户明确说明当前数据为模拟数据
- ql_screen_stocks 的 strength 只表示当日涨跌幅与量比构成的「短期强度」，不能写成中长期动量或未来收益预测
- ql_diagnose_basket 不是样本外回测；不得用其历史收益宣称筛选模型有效

## 输出规范
- 用中文回答；结论要有数据支撑；表格化呈现多标的对比
- 不提供具体买卖指令（如「明天开盘买入」），只做研究与决策支持
- 涉及投资风险时提示「不构成投资建议」"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_BASE + build_memory_prompt()


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _call_fingerprint(name: str, args: dict) -> str:
    payload = json.dumps({"n": name, "a": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(payload.encode()).hexdigest()


async def run_agent_turn(history: list[dict], base_url: str, api_key: str,
                         model: str, provider: str) -> AsyncIterator[str]:
    system = build_system_prompt()
    if provider == "anthropic":
        gen = _run_anthropic(history, system, api_key, model)
    else:
        gen = _run_openai(history, system, base_url, api_key, model)
    async for event in gen:
        yield event


# ── OpenAI 兼容 provider（DeepSeek / 通义 / Kimi / OpenAI / ...） ───────────

async def _run_openai(history: list[dict], system: str, base_url: str,
                      api_key: str, model: str) -> AsyncIterator[str]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    msgs: list[dict] = [{"role": "system", "content": system}] + [
        m for m in history if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    error_count = 0
    seen_calls: set[str] = set()
    final_text = ""
    timeout = httpx.Timeout(120.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for round_i in range(1, MAX_TOOL_ROUNDS + 1):
            yield _sse({"type": "thinking"})
            try:
                resp = await client.post(url, headers=headers, json={
                    "model": model,
                    "messages": msgs,
                    "tools": TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "stream": False,
                })
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "text": f"调用大模型失败：{e}"})
                return

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or None
            calls = message.get("tool_calls") or []

            # content 必须是 null 而不是 ""——严格 provider（DeepSeek 等）会拒绝空串
            msgs.append({
                "role": "assistant",
                "content": text,
                "tool_calls": calls or None,
            } if calls else {"role": "assistant", "content": text or ""})

            if not calls:
                final_text = text or ""
                break

            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                call_id = call.get("id") or f"call_{round_i}_{name}"
                fp = _call_fingerprint(name, args)

                yield _sse({"type": "tool_call", "tool": name, "args": args,
                            "call_id": call_id})

                if fp in seen_calls:
                    result = json.dumps({"warning": "重复调用已跳过（相同工具+相同参数刚刚执行过）"},
                                        ensure_ascii=False)
                    ok = True
                else:
                    seen_calls.add(fp)
                    # 阻塞工具放线程池，不卡事件循环
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, execute_tool, name, args)
                    ok = "error" not in result
                    if not ok:
                        error_count += 1

                if len(result) > MAX_RESULT_CHARS:
                    result = result[:MAX_RESULT_CHARS] + "\n...[结果过长已截断]"

                yield _sse({"type": "tool_result", "tool": name,
                            "call_id": call_id, "ok": ok})
                msgs.append({"role": "tool", "tool_call_id": call_id,
                             "content": result})

            if error_count >= MAX_ERRORS:
                yield _sse({"type": "error",
                            "text": "工具连续出错，已停止工具调用，以下基于已有信息回答。"})
                break
        else:
            yield _sse({"type": "error", "text": "已达到最大工具调用轮数，基于已有信息回答。"})

        # ── 最终回答：已有全文用伪流式；没有则真流式补一次调用 ──
        if final_text:
            async for ev in _pseudo_stream(final_text):
                yield ev
        else:
            async for ev in _stream_openai_final(client, url, headers, model, msgs):
                yield ev


async def _pseudo_stream(text: str) -> AsyncIterator[str]:
    for i in range(0, len(text), CHUNK_SIZE):
        yield _sse({"type": "token", "text": text[i:i + CHUNK_SIZE]})
        await asyncio.sleep(0)


async def _stream_openai_final(client: httpx.AsyncClient, url: str, headers: dict,
                               model: str, msgs: list[dict]) -> AsyncIterator[str]:
    try:
        async with client.stream("POST", url, headers=headers, json={
            "model": model, "messages": msgs, "stream": True,
        }) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield _sse({"type": "token", "text": piece})
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "text": f"生成回答时出错：{e}"})


# ── Anthropic provider ──────────────────────────────────────────────────────

async def _run_anthropic(history: list[dict], system: str, api_key: str,
                         model: str) -> AsyncIterator[str]:
    url = "https://api.anthropic.com/v1/messages"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    anth_tools = to_anthropic_tools(TOOL_DEFINITIONS)
    msgs = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in history if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    error_count = 0
    seen_calls: set[str] = set()
    timeout = httpx.Timeout(120.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for round_i in range(1, MAX_TOOL_ROUNDS + 1):
            yield _sse({"type": "thinking"})
            try:
                resp = await client.post(url, headers=headers, json={
                    "model": model, "system": system, "messages": msgs,
                    "tools": anth_tools, "max_tokens": 4096,
                })
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "text": f"调用大模型失败：{e}"})
                return

            blocks = data.get("content") or []
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            text = "".join(text_parts)

            msgs.append({"role": "assistant", "content": blocks})

            if not tool_uses:
                async for ev in _pseudo_stream(text):
                    yield ev
                return

            tool_results = []
            for call in tool_uses:
                name = call.get("name", "")
                args = call.get("input") or {}
                call_id = call.get("id") or f"call_{round_i}_{name}"
                fp = _call_fingerprint(name, args)

                yield _sse({"type": "tool_call", "tool": name, "args": args,
                            "call_id": call_id})

                if fp in seen_calls:
                    result = json.dumps({"warning": "重复调用已跳过"}, ensure_ascii=False)
                    ok = True
                else:
                    seen_calls.add(fp)
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, execute_tool, name, args)
                    ok = "error" not in result
                    if not ok:
                        error_count += 1

                if len(result) > MAX_RESULT_CHARS:
                    result = result[:MAX_RESULT_CHARS] + "\n...[结果过长已截断]"

                yield _sse({"type": "tool_result", "tool": name,
                            "call_id": call_id, "ok": ok})
                tool_results.append({"type": "tool_result", "tool_use_id": call_id,
                                     "content": result})

            msgs.append({"role": "user", "content": tool_results})

            if error_count >= MAX_ERRORS:
                yield _sse({"type": "error",
                            "text": "工具连续出错，已停止工具调用，以下基于已有信息回答。"})
                break
        else:
            yield _sse({"type": "error", "text": "已达到最大工具调用轮数，基于已有信息回答。"})

        # 收尾：再调一次拿纯文本流式回答
        try:
            async with client.stream("POST", url, headers=headers, json={
                "model": model, "system": system, "messages": msgs,
                "max_tokens": 4096, "stream": True,
            }) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "content_block_delta":
                        piece = (ev.get("delta") or {}).get("text")
                        if piece:
                            yield _sse({"type": "token", "text": piece})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "text": f"生成回答时出错：{e}"})
