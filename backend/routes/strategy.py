"""策略回测路由 — 在浏览器里写聚宽语法 Python 策略，隔离子进程执行。

隔离实现：一次性 Python 子进程 + 受限内置函数 + AST 检查 + 硬超时。
这能保护 API 主进程免受策略崩溃或死循环影响，但不是容器/虚拟机级安全边界。
执行器提供「简化版交易 API + exec 命名空间注入」——
实现 order / order_value / order_target / order_target_value / get_price /
attribute_history / run_daily / g / Context 这套聚宽兼容 API，让用户代码
原样跑起来。数据按证券整段预取进内存缓存（对比原版逐日逐票拉取快得多），
策略异常会终止回测并返回准确交易日期，避免生成不完整却貌似有效的结果。
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import data_provider as dp
from database import get_db
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

router = APIRouter()
BACKTEST_TIMEOUT_SECONDS = 120

# 后台回测任务注册表：task_id -> {future, result, error, status, created_at}
_BACKTEST_TASKS: dict[str, dict] = {}
_BACKTEST_LOCK = asyncio.Lock()


_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "range": range,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "Exception": Exception, "RuntimeError": RuntimeError, "ValueError": ValueError,
}
_BLOCKED_CALLS = {
    "__import__", "breakpoint", "compile", "delattr", "dir", "eval", "exec",
    "getattr", "globals", "help", "input", "locals", "open", "setattr", "type", "vars",
}


def _validate_strategy_code(code: str) -> None:
    """Reject filesystem/network imports and Python object-introspection escape hatches."""
    try:
        tree = ast.parse(code, filename="<strategy>", mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"策略语法错误（第 {exc.lineno} 行）：{exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("回测策略不允许 import；请使用平台提供的安全 API")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"回测策略不允许访问内部属性：{node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"回测策略不允许访问内部名称：{node.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            raise ValueError(f"回测策略不允许调用：{node.func.id}")


class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    code: str = ""


class StrategyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    code: str | None = None
    status: str | None = None


class BacktestParams(BaseModel):
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 100000.0
    benchmark: str = "000300.SH"
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 2.0


# ── 策略 CRUD ───────────────────────────────────────────────────────────────

@router.get("")
def list_strategies():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("")
def create_strategy(s: StrategyCreate):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO strategies (name, description, code) VALUES (?, ?, ?)",
            (s.name, s.description, s.code),
        )
        conn.commit()
        sid = cur.lastrowid
    finally:
        conn.close()
    return {"ok": True, "id": sid}


@router.get("/{strategy_id}")
def get_strategy(strategy_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "策略不存在")
    return dict(row)


@router.put("/{strategy_id}")
def update_strategy(strategy_id: int, s: StrategyUpdate):
    fields = {k: v for k, v in s.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "没有要更新的字段")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    try:
        conn.execute(
            f"UPDATE strategies SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*fields.values(), strategy_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM backtest_results WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/{strategy_id}/live/toggle")
def toggle_live(strategy_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT status FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "策略不存在")
        new_status = "idle" if row["status"] == "live" else "live"
        conn.execute("UPDATE strategies SET status = ? WHERE id = ?", (new_status, strategy_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "status": new_status}


# ── 回测沙箱 ────────────────────────────────────────────────────────────────

class _GlobVars:
    """聚宽的全局 g 对象。"""


class _Position:
    def __init__(self, amount: float = 0, cost: float = 0.0):
        self.amount = amount
        self.cost = cost
        self.sellable = amount


class _Portfolio:
    def __init__(self, starting_cash: float):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, _Position] = {}
        self.portfolio_value = starting_cash

    def update_value(self, prices: dict[str, float]) -> None:
        self.portfolio_value = self.cash + sum(
            p.amount * prices.get(code, p.cost)
            for code, p in self.positions.items()
        )


class _Context:
    def __init__(self, portfolio: _Portfolio):
        self.portfolio = portfolio
        self.current_dt = ""


def _run_backtest_local(code: str, params: BacktestParams) -> dict:
    _validate_strategy_code(code)
    end = params.end_date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    start = params.start_date or (dt.date.fromisoformat(end) - dt.timedelta(days=365)).isoformat()

    trade_days = dp.get_trade_days(start, end)
    if len(trade_days) < 10:
        raise ValueError("回测区间内交易日不足（不足 10 天），请调整起止日期")

    # 每证券整段预取（含 120 自然日回看余量，供均线/动量计算），缓存到内存
    bar_cache: dict[str, list[dict]] = {}
    data_sources: set[str] = set()
    fetch_start = (dt.date.fromisoformat(start) - dt.timedelta(days=120)).isoformat()

    def _get_bars(security: str) -> list[dict]:
        sec = dp.norm_code(security)
        if sec not in bar_cache:
            bars, src = dp.get_price_range(sec, fetch_start, end)
            data_sources.add(src)
            bar_cache[sec] = bars
        return bar_cache[sec]

    def _close_at(security: str, day: str) -> float | None:
        """day 当日（或之前最近一天）的收盘价。"""
        bars = _get_bars(security)
        for b in reversed(bars):
            if b["date"] <= day:
                return b["close"]
        return None

    def _bar_at(security: str, day: str) -> dict | None:
        """只返回指定交易日的 bar；成交不能借用前一日价格。"""
        for bar in _get_bars(security):
            if bar["date"] == day:
                return bar
        return None

    portfolio = _Portfolio(params.initial_capital)
    ctx = _Context(portfolio)
    g = _GlobVars()
    scheduled: list = []
    pending_orders: list[dict] = []
    trades: list[dict] = []
    rejected_orders: list[dict] = []
    total_fees = 0.0

    def _price_of(security: str, day: str) -> float:
        p = _close_at(security, day)
        if p is None:
            raise ValueError(f"{security} 在 {day} 无价格数据")
        return p

    def _reject(order_data: dict, day: str, reason: str) -> None:
        rejected_orders.append({**order_data, "date": day, "reason": reason})

    def _fill_order(order_data: dict, day: str) -> None:
        """T 日信号在下一交易日开盘撮合，避免同日收盘未来数据。"""
        nonlocal total_fees
        security = dp.norm_code(order_data["security"])
        bar = _bar_at(security, day)
        if not bar:
            _reject(order_data, day, "当日无行情，订单未成交")
            return
        raw_price = float(bar.get("open") or bar.get("close") or 0)
        if raw_price <= 0:
            _reject(order_data, day, "开盘价无效，订单未成交")
            return

        pos = portfolio.positions.get(security)
        current = pos.amount if pos else 0.0
        kind = order_data["kind"]
        requested = float(order_data["value"])
        if kind == "amount":
            amount = requested
        elif kind == "value":
            amount = (1 if requested >= 0 else -1) * int(abs(requested) / raw_price / 100) * 100
        elif kind == "target_amount":
            amount = requested - current
        else:  # target_value
            target = int(max(0.0, requested) / raw_price / 100) * 100
            amount = target - current

        if abs(amount) < 1e-9:
            return
        slip = max(0.0, float(params.slippage_bps)) / 10000.0
        if amount > 0:
            amount = int(amount / 100) * 100
            if amount <= 0:
                _reject(order_data, day, "买入数量不足一手")
                return
            fill_price = raw_price * (1 + slip)
            while amount > 0:
                notional = fill_price * amount
                commission = max(float(params.min_commission), notional * max(0.0, float(params.commission_rate)))
                if notional + commission <= portfolio.cash + 1e-9:
                    break
                amount -= 100
            if amount <= 0:
                _reject(order_data, day, "可用资金不足")
                return
            notional = fill_price * amount
            fee = max(float(params.min_commission), notional * max(0.0, float(params.commission_rate)))
            pos = portfolio.positions.setdefault(security, _Position())
            old_cost_value = pos.cost * pos.amount
            pos.amount += amount
            pos.cost = (old_cost_value + notional + fee) / pos.amount
            # 当日买入数量不进入 sellable，落实 A 股 T+1。
            portfolio.cash -= notional + fee
            side = "buy"
        else:
            if pos is None or pos.sellable <= 0:
                _reject(order_data, day, "无可卖持仓（含 T+1 限制）")
                return
            sell = min(-amount, pos.sellable)
            if sell <= 0:
                return
            fill_price = raw_price * (1 - slip)
            notional = fill_price * sell
            commission = max(float(params.min_commission), notional * max(0.0, float(params.commission_rate)))
            fee = commission + notional * max(0.0, float(params.stamp_tax_rate))
            portfolio.cash += notional - fee
            pos.amount -= sell
            pos.sellable -= sell
            amount = -sell
            side = "sell"
            if pos.amount <= 1e-9:
                portfolio.positions.pop(security, None)

        total_fees += fee
        trades.append({
            "date": day, "signal_date": order_data.get("signal_date", ""),
            "code": security, "side": side, "amount": abs(amount),
            "price": round(fill_price, 4), "fee": round(fee, 2),
        })

    def _queue(kind: str, security, value):
        pending_orders.append({
            "kind": kind, "security": dp.norm_code(str(security)),
            "value": float(value), "signal_date": ctx.current_dt,
        })
        return None

    def order(security, amount, **kwargs):
        return _queue("amount", security, amount)

    def order_value(security, value, **kwargs):
        return _queue("value", security, value)

    def order_target(security, amount, **kwargs):
        return _queue("target_amount", security, amount)

    def order_target_value(security, value, **kwargs):
        return _queue("target_value", security, value)

    def get_price(security, start_date=None, end_date=None, count=None,
                  frequency="daily", fields=None, **kwargs):
        # 日频回测在当日决策时只能看到前一交易日及更早的数据。
        bars = [b for b in _get_bars(security) if b["date"] < ctx.current_dt]
        if end_date:
            bars = [b for b in bars if b["date"] <= str(end_date)[:10]]
        if start_date:
            bars = [b for b in bars if b["date"] >= str(start_date)[:10]]
        if count:
            bars = bars[-int(count):]
        if fields:
            return [{f: b.get(f) for f in fields} for b in bars]
        return bars

    def attribute_history(security, count, unit="1d", fields=("close",), **kwargs):
        bars = [b for b in _get_bars(security) if b["date"] < ctx.current_dt]
        bars = bars[-int(count):]
        return [{f: b.get(f) for f in fields} for b in bars]

    def run_daily(func, time="9:30", **kwargs):
        scheduled.append(func)

    def get_current_data():
        class _CD:
            def __getitem__(self, security):
                class _Snap:
                    last_price = next(
                        (b["close"] for b in reversed(_get_bars(security)) if b["date"] < ctx.current_dt),
                        0.0,
                    )
                return _Snap()
        return _CD()

    # 聚宽环境函数的 no-op 兼容：让常见策略代码不必删改即可运行
    def set_benchmark(security):
        g.__dict__.setdefault("_benchmark", security)

    def set_option(*args, **kwargs):
        pass

    def set_order_cost(*args, **kwargs):
        pass

    def set_universe(securities):
        g.__dict__["_universe"] = list(securities)

    class _Log:
        def info(self, msg, *a, **k):
            pass

        def warn(self, msg, *a, **k):
            pass

        def error(self, msg, *a, **k):
            pass

    ns = {
        "g": g, "order": order, "order_value": order_value,
        "order_target": order_target, "order_target_value": order_target_value,
        "get_price": get_price, "attribute_history": attribute_history,
        "run_daily": run_daily, "get_current_data": get_current_data,
        "set_benchmark": set_benchmark, "set_option": set_option,
        "set_order_cost": set_order_cost, "set_universe": set_universe,
        "log": _Log(), "Context": _Context, "__builtins__": _SAFE_BUILTINS,
    }
    exec(compile(code, "<strategy>", "exec"), ns)  # noqa: S102 —— 沙箱见模块注释
    initialize_fn = ns.get("initialize")
    handle_fn = ns.get("handle_data") or ns.get("handle")
    if initialize_fn is not None:
        initialize_fn(ctx)
    if not scheduled and handle_fn is None:
        raise ValueError("策略代码需要定义 handle_data(context, data)，或在 initialize 中注册 run_daily")
    if scheduled:
        handle_fn = scheduled[-1]  # 与聚宽习惯一致：取最后注册的 run_daily 函数
    if handle_fn is None:
        raise ValueError("未找到 handle_data 或 run_daily 注册的处理函数")

    # 兼容两种签名：handle(context) / handle_data(context, data)
    import inspect

    def _call_handle(day: str):
        try:
            n_params = len(inspect.signature(handle_fn).parameters)
        except (TypeError, ValueError):
            n_params = 1
        if n_params >= 2:

            class _Data:
                pass

            handle_fn(ctx, _Data())
        else:
            handle_fn(ctx)

    # ── 逐交易日模拟 ──
    curve: list[dict] = []
    for day in trade_days:
        ctx.current_dt = day
        # 昨日及更早持仓在今日变为可卖；今日开盘成交的买单仍受 T+1 限制。
        for pos in portfolio.positions.values():
            pos.sellable = pos.amount
        orders_to_fill = list(pending_orders)
        pending_orders.clear()
        for order_data in orders_to_fill:
            _fill_order(order_data, day)
        try:
            _call_handle(day)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"策略在 {day} 执行失败：{type(exc).__name__}: {exc}") from exc
        prices = {}
        for sec in list(portfolio.positions.keys()):
            p = _close_at(sec, day)
            if p is not None:
                prices[sec] = p
        portfolio.update_value(prices)
        curve.append({"date": day, "value": round(portfolio.portfolio_value, 2)})

    if len(curve) < 2:
        raise ValueError("回测未产生有效净值序列，请检查策略逻辑")

    values = [c["value"] for c in curve]
    total = values[-1] / values[0] - 1
    ann = (values[-1] / values[0]) ** (252 / len(values)) - 1
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    mean_r = sum(rets) / len(rets)
    std = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
    sharpe = (mean_r - dp.RISK_FREE / 252) / std * (252 ** 0.5) if std > 0 else 0.0
    peak, max_dd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)

    # ── 基准：同区间归一化对比 ──
    bench_bars, bench_src = dp.get_price_range(dp.norm_code(params.benchmark), start, end)
    data_sources.add(bench_src)
    bench_map = {b["date"]: b["close"] for b in bench_bars}
    bench_curve = []
    base = None
    for c in curve:
        p = bench_map.get(c["date"])
        if p is not None:
            if base is None:
                base = p
            bench_curve.append({"date": c["date"], "value": round(p / base, 4)})
    bench_return = (bench_curve[-1]["value"] - 1) * 100 if bench_curve else 0.0

    norm_curve = [
        {"date": c["date"], "strategy": round(c["value"] / values[0], 4)}
        for c in curve
    ]
    bench_by_date = {b["date"]: b["value"] for b in bench_curve}
    for row in norm_curve:
        if row["date"] in bench_by_date:
            row["benchmark"] = bench_by_date[row["date"]]

    return {
        "curve": curve,
        "norm_curve": norm_curve,
        "bench_curve": bench_curve,
        "source": " + ".join(sorted(data_sources)) or "unknown",
        "trades": trades,
        "rejected_orders": rejected_orders,
        "pending_orders": pending_orders,
        "stats": {
            "total_return": round(total * 100, 2),
            "ann_return": round(ann * 100, 2),
            "benchmark_return": round(bench_return, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd * 100, 2),
            "final_value": round(values[-1], 2),
            "initial_value": round(values[0], 2),
            "trade_days": len(curve),
            "trade_count": len(trades),
            "total_fees": round(total_fees, 2),
        },
    }


def _state_source() -> str:
    return dp.provider_status()["mode"]


def _run_backtest_isolated(code: str, params: BacktestParams) -> dict:
    """Run user strategy in a disposable interpreter with a hard wall-clock timeout."""
    worker = Path(__file__).parent.parent / "backtest_worker.py"
    result_path: Path | None = None
    started = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(
            prefix="quantlab-backtest-", suffix=".json", delete=False,
        ) as handle:
            result_path = Path(handle.name)
        payload = json.dumps({
            "code": code,
            "params": params.model_dump(),
            "result_path": str(result_path),
        }, ensure_ascii=False)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(worker)],
                input=payload, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=BACKTEST_TIMEOUT_SECONDS,
                cwd=str(worker.parent), creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"回测超过 {BACKTEST_TIMEOUT_SECONDS} 秒，已终止隔离进程") from exc
        if not result_path.exists() or result_path.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "隔离进程未返回结果")[-800:]
            raise RuntimeError(f"回测隔离进程异常退出：{detail}")
        response = json.loads(result_path.read_text(encoding="utf-8"))
        if not response.get("ok"):
            message = str(response.get("error") or "回测执行失败")
            if response.get("error_type") == "ValueError":
                raise ValueError(message)
            raise RuntimeError(message)
        result = response["result"]
        result["run_meta"] = {
            "engine": "isolated-python",
            "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "params": params.model_dump(),
            "python": sys.version.split()[0],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        return result
    finally:
        if result_path is not None:
            result_path.unlink(missing_ok=True)


# ── 回测执行与历史 ──────────────────────────────────────────────────────────

@router.post("/{strategy_id}/backtest")
def run_backtest(strategy_id: int, params: BacktestParams):
    conn = get_db()
    try:
        row = conn.execute("SELECT code FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "策略不存在")
    if not (row["code"] or "").strip():
        raise HTTPException(400, "策略代码为空，请先编写策略")
    try:
        result = _run_backtest_isolated(row["code"], params)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"回测执行失败：{str(e)[:500]}")

    result_json = json.dumps(result, ensure_ascii=False)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO backtest_results (strategy_id, params_json, result_json) VALUES (?, ?, ?)",
            (strategy_id, params.model_dump_json(), result_json),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "result": result}


@router.get("/{strategy_id}/backtest/history")
def backtest_history(strategy_id: int):
    """回测历史列表（声明顺序必须在 /{result_id} 之前，避免 history 被当作 int 解析）。"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, params_json, result_json, created_at FROM backtest_results "
            "WHERE strategy_id = ? ORDER BY id DESC LIMIT 10",
            (strategy_id,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            stats = json.loads(r["result_json"]).get("stats", {})
        except json.JSONDecodeError:
            stats = {}
        out.append({"id": r["id"], "created_at": r["created_at"], "stats": stats})
    return out


@router.get("/{strategy_id}/backtest/{result_id}")
def backtest_detail(strategy_id: int, result_id: int):
    """回测历史完整结果（前端点击历史记录加载净值曲线）。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, params_json, result_json, created_at FROM backtest_results "
            "WHERE id = ? AND strategy_id = ?",
            (result_id, strategy_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "回测记录不存在")
    try:
        result = json.loads(row["result_json"])
    except json.JSONDecodeError:
        raise HTTPException(500, "回测结果数据损坏")
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "params": json.loads(row["params_json"] or "{}"),
        "result": result,
    }


# ── 异步回测（后台任务 + 状态查询） ─────────────────────────────────────────

@router.post("/{strategy_id}/backtest/async")
async def run_backtest_async(strategy_id: int, params: BacktestParams):
    """发起异步回测，返回 task_id，前端可轮询状态或等待完成通知。"""
    conn = get_db()
    try:
        row = conn.execute("SELECT code FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "策略不存在")
    if not (row["code"] or "").strip():
        raise HTTPException(400, "策略代码为空，请先编写策略")

    task_id = str(uuid.uuid4())
    task_info = {
        "task_id": task_id,
        "strategy_id": strategy_id,
        "status": "running",
        "result": None,
        "error": None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    async with _BACKTEST_LOCK:
        completed = [
            key for key, value in _BACKTEST_TASKS.items()
            if value.get("status") in {"done", "error"}
        ]
        for old_id in completed[:-99]:
            _BACKTEST_TASKS.pop(old_id, None)
        _BACKTEST_TASKS[task_id] = task_info

    async def _run():
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _run_backtest_isolated, row["code"], params
            )
            # 落库
            result_json = json.dumps(result, ensure_ascii=False)
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO backtest_results (strategy_id, params_json, result_json) VALUES (?, ?, ?)",
                    (strategy_id, params.model_dump_json(), result_json),
                )
                conn.commit()
            finally:
                conn.close()
            async with _BACKTEST_LOCK:
                task_info["status"] = "done"
                task_info["result"] = result
        except Exception as e:  # noqa: BLE001
            async with _BACKTEST_LOCK:
                task_info["status"] = "error"
                task_info["error"] = str(e)

    asyncio.create_task(_run())
    return {"ok": True, "task_id": task_id, "status": "running"}


@router.get("/backtest/task/{task_id}")
async def get_backtest_task(task_id: str):
    """查询异步回测任务状态。"""
    async with _BACKTEST_LOCK:
        task = _BACKTEST_TASKS.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "result": task["result"],
        "error": task["error"],
        "created_at": task["created_at"],
    }
