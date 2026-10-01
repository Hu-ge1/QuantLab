"""工具定义与执行 — AI 的「手」。

约定：AI 不准凭训练记忆编数字，行情/基本面/成分股一律通过这里的工具
从 data_provider（akshare 真实数据或 demo 模拟数据）取，返回结果自带
source 标注，让每个数字都有来源。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # 保证可从 agent/ 内导入顶层模块

import data_provider as dp  # noqa: E402
import screener  # noqa: E402
from agent import bayesian, memory  # noqa: E402
from database import get_db  # noqa: E402

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "ql_diagnose_basket",
            "description": "对最多20只候选股票做252日等权买入持有历史体检，返回收益、波动、回撤、夏普和个股贡献。仅用于当前候选的事后诊断，不能冒充样本外策略回测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "lookback": {"type": "integer", "minimum": 120, "maximum": 500, "default": 252},
                },
                "required": ["codes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ql_screen_stocks",
            "description": "批量扫描全 A 股并按可审计因子排序。用户问选股、筛选、批量找股票时必须调用。FFD 全市场数据优先，结果包含来源、日期、筛选数量和因子拆解。",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string", "enum": ["balanced", "value", "momentum"], "description": "均衡/价值/短期强势"},
                    "market": {"type": "string", "enum": ["all", "main", "star", "chinext", "beijing"], "default": "all"},
                    "keyword": {"type": "string", "description": "代码或名称关键词；全市场留空"},
                    "min_market_cap": {"type": "number", "description": "最低总市值，亿元"},
                    "max_market_cap": {"type": "number", "description": "最高总市值，亿元"},
                    "min_pe": {"type": "number"}, "max_pe": {"type": "number"},
                    "max_pb": {"type": "number"}, "min_amount_yi": {"type": "number", "description": "最低成交额，亿元"},
                    "min_turnover": {"type": "number", "description": "最低换手率，%"},
                    "min_change": {"type": "number"}, "max_change": {"type": "number"},
                    "exclude_st": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ql_search",
            "description": "按名称或代码搜索 A 股证券（股票/ETF/指数）。回答任何行情问题前，先用本工具确认准确的证券代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "名称关键词或代码片段，如 茅台 / 600519"},
                    "types": {"type": "string", "description": "搜索类型，默认 stock,etf,index", "default": "stock,etf,index"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ql_get_price",
            "description": "获取证券历史 K 线（开高低收量），返回聚合摘要（最新收盘、区间涨跌幅、高低点、均线）与全部K线数据。数据来自 akshare 公开接口或 demo 模拟数据，见返回的 source 字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "security": {"type": "string", "description": "证券代码，如 600519.SH；指数用 000300.SH / 399006.SZ"},
                    "count": {"type": "integer", "description": "K线根数，默认 60，最多 500", "default": 60},
                    "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "K线周期，默认 daily", "default": "daily"},
                },
                "required": ["security"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ql_get_fundamentals",
            "description": "获取 A 股个股基本面估值：PE(TTM)、PB、PS、总市值、流通市值、换手率等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "security": {"type": "string", "description": "股票代码，如 600519.SH"},
                },
                "required": ["security"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ql_get_index_stocks",
            "description": "获取指数成分股列表（如沪深300、上证50）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "指数代码，如 000300.SH、000016.SH、399006.SZ"},
                },
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "portfolio_get",
            "description": "读取用户当前持仓明细（成本、最新价、盈亏）。用户询问自己的持仓/盈亏时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bayesian_decision",
            "description": "贝叶斯决策分析：对「假设是否成立」做概率更新并对比各行动期望值 EV，给出推荐行动与敏感性分析。凡是买/卖/加仓/止盈类决策问题必须调用本工具，而不是直接给结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string", "description": "待检验假设，如 未来一个月茅台跑赢沪深300"},
                    "prior": {"type": "number", "description": "先验概率 0~1"},
                    "evidences": {
                        "type": "array",
                        "description": "证据列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "证据描述"},
                                "direction": {"type": "string", "enum": ["support", "against"], "description": "支持/反对假设"},
                                "quality": {"type": "string", "enum": ["strong", "medium", "weak"], "description": "证据强度"},
                                "note": {"type": "string", "description": "补充说明"},
                            },
                            "required": ["name", "direction", "quality"],
                        },
                    },
                    "actions": {
                        "type": "array",
                        "description": "候选行动（不传用默认四档）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "payoff_if_true": {"type": "number", "description": "假设成立时的相对收益%（正负）"},
                                "payoff_if_false": {"type": "number", "description": "假设不成立时的相对收益%（正负）"},
                            },
                            "required": ["name", "payoff_if_true", "payoff_if_false"],
                        },
                    },
                    "timeframe": {"type": "string", "description": "时间范围，如 未来一个月"},
                    "success_criteria": {"type": "string", "description": "成功标准"},
                },
                "required": ["hypothesis", "prior", "evidences"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qmt_account",
            "description": "读取用户 QMT 实盘账户的只读快照：资产（总资产/可用/市值）、持仓明细、当日委托。QMT 客户端在线时可用；离线时返回错误与指引。用户询问「我的持仓/账户/盈亏」时优先调用本工具。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qmt_realtime",
            "description": "通过 QMT (xtdata) 获取若干证券的实时 tick 最新价（QMT 客户端在线时可用）。需要盘中最新价时调用；离线时返回错误。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "证券代码列表，如 [\"600519.SH\", \"000858.SZ\"]，最多 20 个",
                    },
                },
                "required": ["codes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "把用户的偏好、关注标的、风险偏好等信息存入长期记忆，下次对话自动加载。用户表达稳定偏好时主动调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "分类标签，如 关注标的 / 风险偏好"},
                    "content": {"type": "string", "description": "要记住的内容"},
                },
                "required": ["key", "content"],
            },
        },
    },
]


def to_anthropic_tools(tools: list[dict]) -> list[dict]:
    return [
        {"name": t["function"]["name"], "description": t["function"]["description"],
         "input_schema": t["function"]["parameters"]}
        for t in tools
    ]


# ── 各工具实现（同步、返回 JSON 字符串） ────────────────────────────────────

def _ql_search(query: str, types: str = "stock,etf,index") -> str:
    results = dp.search_securities(query, types=types)
    return json.dumps({"query": query, "count": len(results), "results": results},
                      ensure_ascii=False)


def _ql_get_price(security: str, count: int = 60, frequency: str = "daily") -> str:
    return json.dumps(dp.get_price(security, count=count, frequency=frequency),
                      ensure_ascii=False)


def _ql_get_fundamentals(security: str) -> str:
    return json.dumps(dp.get_fundamentals(security), ensure_ascii=False)


def _ql_get_index_stocks(index: str) -> str:
    return json.dumps(dp.get_index_stocks(index), ensure_ascii=False)


def _ql_screen_stocks(**args) -> str:
    args["limit"] = min(max(int(args.get("limit", 10)), 1), 20)
    return json.dumps(screener.screen(args), ensure_ascii=False)


def _ql_diagnose_basket(codes: list[str], lookback: int = 252) -> str:
    result = screener.backtest_basket(codes, lookback)
    result.pop("curve", None)  # AI 只需统计结果，曲线留给页面展示，避免工具上下文膨胀。
    return json.dumps(result, ensure_ascii=False)


def _portfolio_get() -> str:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM positions ORDER BY id").fetchall()
    finally:
        conn.close()
    positions = []
    total_cost = total_value = 0.0
    has_prices = False
    for r in rows:
        cost_value = (r["cost"] or 0) * (r["shares"] or 0)
        cur = r["cur_price"]
        cur_value = cur * r["shares"] if cur is not None else None
        pnl = (cur - r["cost"]) * r["shares"] if cur is not None else None
        pnl_pct = ((cur / r["cost"]) - 1) * 100 if (cur is not None and r["cost"]) else None
        if cur is not None:
            has_prices = True
            total_value += cur_value
        else:
            total_value += cost_value
        total_cost += cost_value
        positions.append({
            "code": r["code"], "name": r["name"], "cost": r["cost"],
            "shares": r["shares"], "cost_value": round(cost_value, 2),
            "cur_price": cur,
            "cur_value": round(cur_value, 2) if cur_value is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        })
    return json.dumps({
        "positions": positions,
        "summary": {
            "total_cost": round(total_cost, 2), "total_value": round(total_value, 2),
            "total_pnl": round(total_value - total_cost, 2),
            "total_pnl_pct": round((total_value / total_cost - 1) * 100, 2) if total_cost else 0.0,
            "has_prices": has_prices,
        },
    }, ensure_ascii=False)


def _bayesian_decision(**args) -> str:
    result = bayesian.run_bayesian_decision(
        hypothesis=args["hypothesis"],
        prior=float(args.get("prior", 0.5)),
        evidences=args.get("evidences", []),
        actions=args.get("actions"),
        timeframe=args.get("timeframe", ""),
        success_criteria=args.get("success_criteria", ""),
    )
    return json.dumps(result, ensure_ascii=False)


def _memory_save(key: str, content: str) -> str:
    return json.dumps(memory.save_memory(key, content), ensure_ascii=False)


def _qmt_account() -> str:
    """QMT 实盘账户只读快照（studio 桥接优先，MiniQMT 兜底）。"""
    import qmt_client

    overview = qmt_client.account_overview()
    if not overview.get("online"):
        return json.dumps(
            {"error": "QMT 未连接", "hint": overview.get("hint", "")},
            ensure_ascii=False,
        )
    positions = overview.get("positions") or []
    return json.dumps(
        {
            "source": overview.get("source"),
            "account": overview.get("account") or {},
            "position_count": len(positions),
            "positions": positions[:20],
            "orders_count": len(overview.get("orders") or []),
            "note": "只读快照，延迟约 2 秒；不包含任何下单操作",
        },
        ensure_ascii=False,
    )


def _qmt_realtime(codes: list[str]) -> str:
    """QMT 实时 tick 报价（最多 20 个代码）。"""
    import qmt_client

    codes = [str(c).upper() for c in (codes or [])][:20]
    if not codes:
        return json.dumps({"error": "codes 不能为空"}, ensure_ascii=False)
    ticks = qmt_client.realtime_quotes(codes)
    if not ticks:
        return json.dumps(
            {"error": "QMT 不在线，无法取实时 tick", "hint": "启动 QMT 客户端后重试，或使用 ql_get_price 取收盘级数据"},
            ensure_ascii=False,
        )
    return json.dumps({"source": "xtdata·QMT实时", "ticks": ticks}, ensure_ascii=False)


_DISPATCH = {
    "ql_search": _ql_search,
    "ql_get_price": _ql_get_price,
    "ql_get_fundamentals": _ql_get_fundamentals,
    "ql_get_index_stocks": _ql_get_index_stocks,
    "ql_screen_stocks": _ql_screen_stocks,
    "ql_diagnose_basket": _ql_diagnose_basket,
    "portfolio_get": _portfolio_get,
    "bayesian_decision": _bayesian_decision,
    "memory_save": _memory_save,
    "qmt_account": _qmt_account,
    "qmt_realtime": _qmt_realtime,
}


def execute_tool(name: str, args: dict) -> str:
    """统一入口：任何工具异常都兜成 {"error": ...}，由循环层计入错误预算。"""
    fn = _DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        return fn(**args)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
