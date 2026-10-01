"""策略自进化引擎 — AutoResearch for stock-selection strategies.

方法论来源（华泰金工《自进化Skill：选股策略的自动迭代》/ Karpathy AutoResearch）：
把量化研究组织成「可执行、可回溯、可约束」的自动迭代循环。核心不是让模型
无约束地乱搜参数，而是把投资逻辑、可调边界、样本隔离、版本保留写进协议，
让策略在明确的研究纪律下持续进化。

三层框架：
  ┌─ 策略协议层  PARAM_SPACE：策略内核固定，只允许在有界参数内调整
  ├─ 版本管理层  每一轮迭代独立成版本，记录配置/指标/父版本/是否保留
  └─ 样本隔离层  训练集生成假设 → 验证集决定保留 → 测试集只做最终复核

数据来自 data_provider（akshare 真实行情或 demo 模拟行情），一次性拉取
价格面板缓存进内存，之后几十次回测全部在内存里完成——快且可复现。
"""
from __future__ import annotations

import math
import random
import ast
from typing import Optional

import data_provider as dp

# ── 策略协议层：策略内核固定，只暴露这些有界可调参数 ──────────────────────
#
# 内核：在一篮子大盘蓝筹中按动量轮动，叠加均线过滤与止损约束。
# 每轮进化只允许动其中一个参数，且必须落在 [min,max] 网格上 —— 保证每次
# 改动都可解释、可归因，而不是黑箱乱跳。
PARAM_SPACE = {
    "momentum_window": {"min": 10, "max": 60, "step": 5, "default": 20,
                        "desc": "动量回看天数"},
    "top_n": {"min": 2, "max": 10, "step": 1, "default": 4,
              "desc": "持仓只数"},
    "ma_filter": {"min": 5, "max": 40, "step": 5, "default": 20,
                  "desc": "均线过滤窗口（价格需站上 MA 才买入）"},
    "rebalance_days": {"min": 3, "max": 20, "step": 1, "default": 10,
                       "desc": "调仓周期（交易日）"},
    "stop_loss_pct": {"min": 3, "max": 20, "step": 1, "default": 10,
                      "desc": "个股止损线（%）"},
}

STRATEGIES = {
    "momentum": {
        "name": "蓝筹动量轮动",
        "short": "强者恒强",
        "description": "在股票池中按区间涨幅排名，选择正动量且站上均线的前 N 只等权持有。",
        "formula": "score = P(t) / P(t-momentum_window) - 1",
    },
    "risk_adjusted_momentum": {
        "name": "低波动动量",
        "short": "收益风险比",
        "description": "用动量除以区间波动率排名，优先选择上涨更平稳、回撤噪声更小的股票。",
        "formula": "score = momentum / volatility",
    },
    "trend_quality": {
        "name": "趋势质量轮动",
        "short": "动量＋均线乖离",
        "description": "同时评价区间动量和价格高于均线的强度，筛选趋势持续性更好的股票。",
        "formula": "score = 0.7 × momentum + 0.3 × (P / MA - 1)",
    },
}

DEFAULT_UNIVERSE = dp.DEFAULT_UNIVERSE
RISK_FREE = dp.RISK_FREE


def default_config() -> dict:
    return {k: v["default"] for k, v in PARAM_SPACE.items()}


def discover_tunable_params(code: str) -> dict:
    """从用户策略 initialize 中发现正数型 g.xxx 参数。"""
    tree = ast.parse(code or "", filename="<strategy>", mode="exec")
    found: dict[str, dict] = {}

    def add(name: str, value) -> None:
        if name in {"day", "counter", "count"} or isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return
        if isinstance(value, int):
            step = max(1, round(value / 5))
            minimum = max(1, value - step * 3)
            maximum = value + step * 5
        else:
            step = max(0.001, round(value / 4, 4))
            minimum = max(0.001, round(value / 4, 4))
            maximum = min(1.0, round(value * 3, 4)) if value <= 1 else round(value * 2, 4)
        found[name] = {
            "min": minimum, "max": maximum, "step": step, "default": value,
            "desc": f"母策略参数 g.{name}",
        }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "g" and isinstance(node.value, ast.Constant):
                add(target.attr, node.value.value)
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
                for item, value in zip(target.elts, node.value.elts):
                    if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "g" and isinstance(value, ast.Constant):
                        add(item.attr, value.value)
    return found


def rewrite_strategy_params(code: str, config: dict) -> str:
    """只替换 g.xxx 初始化赋值右侧的数字字面量，保留用户代码其余内容。"""
    tree = ast.parse(code, filename="<strategy>", mode="exec")
    lines = code.splitlines(keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line)
    replacements: list[tuple[int, int, str]] = []

    def queue(name: str, value_node) -> None:
        if name not in config or not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, (int, float)):
            return
        start = offsets[value_node.lineno - 1] + value_node.col_offset
        end = offsets[value_node.end_lineno - 1] + value_node.end_col_offset
        value = config[name]
        replacements.append((start, end, str(int(value)) if isinstance(value_node.value, int) else repr(float(value))))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "g":
                queue(target.attr, node.value)
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
                for item, value in zip(target.elts, node.value.elts):
                    if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "g":
                        queue(item.attr, value)
    result = code
    for start, end, text in sorted(replacements, reverse=True):
        result = result[:start] + text + result[end:]
    return result


# ── 价格面板：一次性拉取，之后所有回测在内存里跑 ──────────────────────────

def fetch_price_panel(universe: Optional[list[str]] = None, count: int = 250) -> dict:
    """拉取股票池日收盘价面板，返回 {"dates","prices","universe","source"}。

    count 为每票回看交易日数（120~750）：窗口越长，训练/验证/测试
    三段样本越充分，进化结论越稳健；代价是首次拉取更慢。
    """
    import time

    count = max(120, min(int(count), 750))
    universe = universe or DEFAULT_UNIVERSE
    codes = [dp.norm_code(c) for c in universe]
    series: dict[str, dict[str, float]] = {}
    source_by_code: dict[str, str] = {}

    for i, code in enumerate(codes):
        bars: list[dict] = []
        for attempt in (1, 2):  # 东财接口偶发断连，单票重试一次
            try:
                bars, src = dp.get_price_bars(code, count=count)
                if len(bars) >= 60:
                    source_by_code[code] = src
                    break
                bars = []
            except Exception:  # noqa: BLE001 —— 单票失败重试/跳过
                bars = []
            if attempt == 1:
                time.sleep(1.0)
        if len(bars) < 60:
            continue
        series[code] = {b["date"]: float(b["close"]) for b in bars}
        if i < len(codes) - 1:
            time.sleep(0.4)  # 节流：避免连续请求被行情源限流断连

    if not series:
        raise ValueError("未能获取任何价格数据，请检查数据源状态")

    # 必须按真实交易日对齐，不能把“等长数组”误认为日期一致。
    common_dates = set.intersection(*(set(values) for values in series.values()))
    dates_ref = sorted(common_dates)[-count:]
    if len(dates_ref) < 60:
        raise ValueError(f"股票池共同有效交易日不足 60 天（当前 {len(dates_ref)} 天）")
    aligned = {
        code: [values[day] for day in dates_ref]
        for code, values in series.items()
    }
    sources = {source_by_code[c] for c in aligned if c in source_by_code}

    return {
        "dates": dates_ref,
        "prices": aligned,
        "universe": list(aligned.keys()),
        "source": " + ".join(sorted(sources)),
    }


def split_panel(panel: dict, train: float = 0.5, val: float = 0.25) -> dict:
    """样本隔离：时间轴切成 训练/验证/测试 三段（按时间顺序，无重叠）。"""
    n = len(panel["dates"])
    i_train = int(n * train)
    i_val = int(n * (train + val))
    return {"train": (0, i_train), "val": (i_train, i_val), "test": (i_val, n)}


# ── 内存回测器 ─────────────────────────────────────────────────────────────

def simulate(panel: dict, config: dict, window: tuple) -> dict:
    """在 [start,end) 时间窗内按 config 跑动量轮动回测（纯内存）。"""
    lo, hi = window
    dates = panel["dates"][lo:hi]
    codes = panel["universe"]
    px = {c: panel["prices"][c][lo:hi] for c in codes}
    n = len(dates)
    if n < 30:
        return {"ann_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "turnover": 0.0, "final": 1.0, "n_days": n, "valid": False}

    mom_w = int(config["momentum_window"])
    top_n = int(config["top_n"])
    ma_w = int(config["ma_filter"])
    reb = int(config["rebalance_days"])
    stop = float(config["stop_loss_pct"]) / 100.0
    strategy_id = panel.get("strategy_id", "momentum")

    cash = 1.0
    holdings: dict[str, dict] = {}  # code -> {"shares", "entry"}
    nav: list[float] = []
    rebalance_count = 0
    turnover_acc = 0.0
    prev_weights: dict[str, float] = {}

    # t-1 收盘后生成信号，t 日收盘成交；避免使用 t 日收盘价同时决策和成交。
    start_i = max(mom_w, ma_w) + 1
    for t in range(start_i, n):
        signal_i = t - 1
        # 前一交易日触发止损，下一交易日成交。
        for c in list(holdings.keys()):
            entry = holdings[c]["entry"]
            if entry > 0 and px[c][signal_i] / entry - 1 <= -stop:
                cash += holdings[c]["shares"] * px[c][t]
                del holdings[c]

        # 调仓日：每 reb 个交易日一次
        if (t - start_i) % reb == 0:
            rebalance_count += 1
            scored = []
            for c in codes:
                p_now, p_past = px[c][signal_i], px[c][signal_i - mom_w]
                if p_past <= 0:
                    continue
                mom = p_now / p_past - 1
                ma = sum(px[c][signal_i - ma_w + 1:signal_i + 1]) / ma_w
                if p_now >= ma:  # 均线过滤：站上均线才入选
                    score = mom
                    if strategy_id == "risk_adjusted_momentum":
                        window_prices = px[c][signal_i - mom_w:signal_i + 1]
                        returns = [window_prices[i] / window_prices[i - 1] - 1 for i in range(1, len(window_prices)) if window_prices[i - 1] > 0]
                        if returns:
                            mean = sum(returns) / len(returns)
                            volatility = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
                            score = mom / volatility if volatility > 1e-9 else mom
                    elif strategy_id == "trend_quality":
                        score = 0.7 * mom + 0.3 * (p_now / ma - 1)
                    scored.append((c, score, mom))
            scored.sort(key=lambda x: x[1], reverse=True)
            picks = [c for c, _score, mom in scored[:top_n] if mom > 0]

            port_val = cash + sum(h["shares"] * px[c][t] for c, h in holdings.items())
            new_weights = {c: 1.0 / len(picks) for c in picks} if picks else {}
            allc = set(new_weights) | set(prev_weights)
            turnover_acc += sum(
                abs(new_weights.get(c, 0.0) - prev_weights.get(c, 0.0)) for c in allc
            ) / 2.0
            prev_weights = new_weights

            holdings = {}
            cash = port_val
            for c in picks:
                alloc = port_val * new_weights[c]
                holdings[c] = {"shares": alloc / px[c][t], "entry": px[c][t]}
                cash -= alloc

        nav.append(cash + sum(h["shares"] * px[c][t] for c, h in holdings.items()))

    if len(nav) < 2:
        return {"ann_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "turnover": 0.0, "final": 1.0, "n_days": len(nav), "valid": False}

    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    sharpe = ((mean_r - RISK_FREE / 252) / std * math.sqrt(252)) if std > 0 else 0.0
    total = nav[-1] / nav[0] - 1
    ann = (nav[-1] / nav[0]) ** (252 / len(nav)) - 1

    peak, max_dd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)

    years = len(nav) / 252
    ann_turnover = (turnover_acc * 2) / years if years > 0 else 0.0

    return {
        "ann_return": round(ann * 100, 2),
        "total_return": round(total * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd * 100, 2),
        "turnover": round(ann_turnover, 2),
        "final": round(nav[-1] / nav[0], 4),
        "n_days": len(nav),
        "rebalances": rebalance_count,
        "valid": True,
    }


# ── 进化主循环 ─────────────────────────────────────────────────────────────

def _neighbors(config: dict, param_space: dict | None = None) -> list[tuple[str, object, dict]]:
    """生成「只动一个参数、一步网格」的候选邻居 —— 每次改动可解释、可归因。"""
    out = []
    for key, spec in (param_space or PARAM_SPACE).items():
        cur = config[key]
        for delta in (-spec["step"], spec["step"]):
            nv = cur + delta
            if spec["min"] <= nv <= spec["max"]:
                nc = dict(config)
                nc[key] = nv
                out.append((key, nv, nc))
    return out


def evolve(panel: dict, rounds: int = 12, seed: int = 42,
           start_config: Optional[dict] = None):
    """同步生成器：约束化贪心爬山，每轮 yield 一个进度事件。

    每轮在验证集上评估若干「单参数变异」，保留验证夏普提升最大的一个，
    否则回滚（计为一次失败尝试）。测试集只在最后做复核，绝不进调参闭环。
    """
    rng = random.Random(seed)
    strategy_id = panel.get("strategy_id", "momentum")
    if strategy_id not in STRATEGIES:
        raise ValueError("未知的进化策略")
    splits = split_panel(panel)

    def eval_development(cfg):
        return {
            "train": simulate(panel, cfg, splits["train"]),
            "val": simulate(panel, cfg, splits["val"]),
        }

    config = start_config or default_config()
    metrics = eval_development(config)
    best_val_sharpe = metrics["val"]["sharpe"]

    version = 0
    history = [{
        "version": "v0.0", "config": dict(config), "metrics": metrics,
        "kept": True, "change": "初始版本", "parent": None,
    }]
    yield {"type": "init", "version": "v0.0", "config": dict(config),
           "metrics": metrics}

    fail_streak = 0
    r = 0
    for r in range(1, rounds + 1):
        neighbors = _neighbors(config)
        rng.shuffle(neighbors)
        candidates = neighbors[:5]  # 每轮最多评估 5 个邻居，避免穷举

        best_cand = None
        for key, nv, nc in candidates:
            m = eval_development(nc)
            if best_cand is None or m["val"]["sharpe"] > best_cand[3]["val"]["sharpe"]:
                best_cand = (key, nv, nc, m)

        if best_cand and best_cand[3]["val"]["sharpe"] > best_val_sharpe + 1e-6:
            key, nv, nc, m = best_cand
            version += 1
            old = config[key]
            config = nc
            best_val_sharpe = m["val"]["sharpe"]
            metrics = m
            fail_streak = 0
            ver = f"v0.{version}"
            change = f"{PARAM_SPACE[key]['desc']} {old}→{nv}"
            history.append({
                "version": ver, "config": dict(config), "metrics": m,
                "kept": True, "change": change, "parent": history[-1]["version"],
            })
            yield {"type": "round", "round": r, "tried": len(candidates),
                   "kept": True, "version": ver, "change": change,
                    "val_sharpe": m["val"]["sharpe"]}
        else:
            fail_streak += 1
            yield {"type": "round", "round": r, "tried": len(candidates),
                   "kept": False, "change": "本轮无改进，回滚",
                   "val_sharpe": best_val_sharpe, "fail_streak": fail_streak}
            # loop-until-dry：连续 4 轮无改进则收敛提前结束
            if fail_streak >= 4:
                break

    # 测试集在参数搜索完全结束后才解封；中间候选从未计算测试指标。
    init = history[0]["metrics"]
    init_test = simulate(panel, history[0]["config"], splits["test"])
    final_test = simulate(panel, config, splits["test"])
    history[0]["metrics"]["test"] = init_test
    history[-1]["metrics"]["test"] = final_test
    summary = {
        "strategy_id": strategy_id,
        "strategy_name": STRATEGIES[strategy_id]["name"],
        "rounds_run": r,
        "versions_kept": version,
        "init": {
            "val_sharpe": init["val"]["sharpe"], "test_sharpe": init_test["sharpe"],
            "test_ann_return": init_test["ann_return"],
            "test_max_drawdown": init_test["max_drawdown"],
        },
        "final": {
            "val_sharpe": metrics["val"]["sharpe"], "test_sharpe": final_test["sharpe"],
            "test_ann_return": final_test["ann_return"],
            "test_max_drawdown": final_test["max_drawdown"],
        },
    }
    yield {"type": "done", "best_config": dict(config),
           "history": history, "summary": summary}
