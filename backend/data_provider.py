"""行情数据适配层 — QuantLab 的数据中枢（替代原版对聚宽 jqdatasdk 的依赖）。

统一接口，底层双模式：
  1. akshare 免费公开接口（东方财富/新浪等，无需注册任何账号）—— 默认
  2. demo 模拟数据（确定性几何布朗运动，按代码播种、进程内一致）—— 兜底

akshare 调用失败或超时达冷却阈值时自动降级 demo，并在状态里记录原因；
所有真实结果带 source 标注，模拟数据一律显式标 "demo·模拟数据"，
呼应本项目「让 AI 的每个数字都有来源」的立项目标。

代码规范（对 AI 与前端统一）：
  股票  600519.SH / 000858.SZ / 300750.SZ（6 位 + 交易所后缀）
  ETF   510300.SH / 159915.SZ
  指数  000300.SH（沪深300）、000001.SH（上证指数）、399006.SZ（创业板指）
  兼容  聚宽后缀 .XSHG/.XSHE 自动转换；纯 6 位代码按前缀推断
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import random
import threading
import time
from typing import Optional

import pandas as pd

# ── 常量 ────────────────────────────────────────────────────────────────────

KNOWN_INDICES = {
    "000001": "上证指数",
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "399001": "深证成指",
    "399006": "创业板指",
    "399005": "中小100",
}

# 自进化引擎的默认股票池（大盘蓝筹，demo 模式同样可用）
DEFAULT_UNIVERSE = [
    "600519.SH",  # 贵州茅台
    "601318.SH",  # 中国平安
    "600036.SH",  # 招商银行
    "000858.SZ",  # 五粮液
    "600900.SH",  # 长江电力
    "601166.SH",  # 兴业银行
    "000333.SZ",  # 美的集团
    "600276.SH",  # 恒瑞医药
    "601012.SH",  # 隆基绿能
    "000651.SZ",  # 格力电器
    "600030.SH",  # 中信证券
    "002415.SZ",  # 海康威视
]

RISK_FREE = 0.03

_AK_TIMEOUT = 25          # 单次 akshare 调用软超时（秒）
_FALLBACK_COOLDOWN = 120  # akshare 失败后直走 demo 的冷却期（秒）

_EXEC = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="akshare"
)

_state = {
    "mode": "unknown",        # unknown | akshare | demo
    "last_error": "",
    "fallback_until": 0.0,    # 此时间戳之前直接走 demo
}
_lock = threading.Lock()

_price_cache: dict[tuple, tuple[float, list[dict]]] = {}
_PRICE_TTL = 300.0

_calendar_cache: Optional[list[str]] = None
_securities_cache: Optional[dict[str, list[dict]]] = None


class DataProviderError(Exception):
    pass


# ── 代码规范化 ──────────────────────────────────────────────────────────────

def norm_code(code: str) -> str:
    """把各种写法归一成 6位+后缀。指数识别依赖 KNOWN_INDICES 与后缀。"""
    raw = (code or "").strip().upper()
    if not raw:
        raise DataProviderError("证券代码为空")
    if len(raw) == 8 and raw[:2] in ("SH", "SZ", "BJ") and raw[2:].isdigit():
        raw = f"{raw[2:]}.{raw[:2]}"          # SH600519 → 600519.SH
    if "." in raw:
        six, _, suffix = raw.partition(".")
        suffix = {"XSHG": "SH", "XSHE": "SZ"}.get(suffix, suffix)
        return f"{six}.{suffix}"
    six = raw
    if not six.isdigit() or len(six) != 6:
        raise DataProviderError(f"无法识别的证券代码: {code}")
    if six in KNOWN_INDICES and not six.startswith(("000001",)):
        return f"{six}.{_index_suffix(six)}"
    if six.startswith(("51", "56", "58")):
        return f"{six}.SH"
    if six.startswith(("15", "16", "159")):
        return f"{six}.SZ"
    if six.startswith(("6", "9", "5")):
        return f"{six}.SH"
    if six.startswith(("0", "3")):
        return f"{six}.SZ"
    return f"{six}.BJ"


def _index_suffix(six: str) -> str:
    return "SZ" if six.startswith("39") else "SH"


def is_index(code: str) -> bool:
    six = code.split(".")[0]
    suffix = code.split(".")[-1] if "." in code else ""
    if suffix == "SH":
        return six.startswith("000") and six in KNOWN_INDICES
    if suffix == "SZ":
        return six.startswith("399")
    return six in KNOWN_INDICES


def sec_type(code: str) -> str:
    """stock | etf | index"""
    if is_index(code):
        return "index"
    six = code.split(".")[0]
    return "etf" if six.startswith(("51", "56", "58", "15", "16")) else "stock"


# ── akshare 调用骨架 ────────────────────────────────────────────────────────

def _call_ak(fn, *args, timeout: float = _AK_TIMEOUT, **kwargs):
    """在线程池里跑阻塞的 akshare 调用，带软超时；失败抛 DataProviderError。"""
    try:
        return _EXEC.submit(fn, *args, **kwargs).result(timeout=timeout)
    except Exception as e:  # noqa: BLE001 —— 统一转成数据层错误
        raise DataProviderError(f"{type(e).__name__}: {e}") from e


def _mark_fallback(err: str) -> None:
    with _lock:
        _state["mode"] = "demo"
        _state["last_error"] = err[:300]
        _state["fallback_until"] = time.time() + _FALLBACK_COOLDOWN


def _mark_live() -> None:
    with _lock:
        _state["mode"] = "akshare"
        _state["last_error"] = ""


def _ak_available() -> bool:
    return time.time() >= _state["fallback_until"]


def provider_status() -> dict:
    """当前数据源状态（设置页展示）。probe=True 时主动探测一次。"""
    mode = _state["mode"]
    return {
        "mode": mode,
        "primary": "akshare（东方财富/新浪公开接口）",
        "fallback": "demo（确定性模拟数据）",
        "last_error": _state["last_error"],
        "note": "接口失效或断网时自动切换 demo 模拟数据，功能完整但数字仅为演示",
    }


def probe_akshare() -> dict:
    """主动探测 akshare 连通性（设置页「测试数据源」按钮）。"""
    try:
        days = _call_ak(_ak_trade_days_raw)
        _mark_live()
        return {"ok": True, "message": f"akshare 连接正常，交易日历 {len(days)} 条"}
    except Exception as e:  # noqa: BLE001
        _mark_fallback(str(e))
        return {"ok": False, "message": f"akshare 不可用，已切 demo 模式：{e}"}


# ── 缓存的静态表 ────────────────────────────────────────────────────────────

def _ak_trade_days_raw() -> list[str]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    out = [str(d)[:10] for d in df["trade_date"].tolist()]
    return sorted(out)


def _trade_calendar() -> list[str]:
    global _calendar_cache
    if _calendar_cache is None:
        try:
            _calendar_cache = _call_ak(_ak_trade_days_raw, timeout=30)
            _mark_live()
        except Exception as e:  # noqa: BLE001
            _mark_fallback(str(e))
            _calendar_cache = []
    if not _calendar_cache:
        _calendar_cache = _weekday_calendar()
    return _calendar_cache


def _weekday_calendar() -> list[str]:
    """无日历时的退化实现：周一~周五（不含法定节假日，仅 demo 兜底用）。"""
    today = dt.date.today()
    days = []
    d = today - dt.timedelta(days=1)
    while len(days) < 800:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return sorted(days)


def get_trade_days(start: str = "", end: str = "") -> list[str]:
    cal = _trade_calendar()
    start = start or cal[0]
    end = end or cal[-1]
    return [d for d in cal if start <= d <= end]


def _safe_end() -> str:
    """取数终点：昨天（避免盘中不完整 K 线），并对齐到交易日。"""
    cal = _trade_calendar()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    le = [d for d in cal if d <= yesterday]
    return le[-1] if le else yesterday


def _ak_stock_list_raw() -> list[dict]:
    import akshare as ak

    df = ak.stock_info_a_code_name()
    return [
        {"code": f"{r['code']}.{suffix_of(str(r['code']))}", "name": str(r["name"]), "type": "stock"}
        for _, r in df.iterrows()
    ]


def _ak_etf_list_raw() -> list[dict]:
    import akshare as ak

    df = ak.fund_etf_spot_em()
    return [
        {"code": f"{str(r['代码'])}.{suffix_of(str(r['代码']))}", "name": str(r["名称"]), "type": "etf"}
        for _, r in df.iterrows()
    ]


def suffix_of(six: str) -> str:
    try:
        return norm_code(six).split(".")[1]
    except DataProviderError:
        return "SZ"


def _securities() -> dict[str, list[dict]]:
    global _securities_cache
    if _securities_cache is not None:
        return _securities_cache
    stocks: list[dict] = []
    etfs: list[dict] = []
    try:
        stocks = _call_ak(_ak_stock_list_raw, timeout=120)
    except Exception as e:  # noqa: BLE001
        _mark_fallback(str(e))
    try:
        etfs = _call_ak(_ak_etf_list_raw, timeout=120)
    except Exception:  # noqa: BLE001
        pass
    if stocks:
        _mark_live()
    # 仅在拿到至少一种证券列表时才缓存；空结果不缓存，以便下次重试
    if stocks or etfs:
        _securities_cache = {"stock": stocks, "etf": etfs}
    return _securities_cache or {"stock": [], "etf": []}


def invalidate_static_cache() -> None:
    global _calendar_cache, _securities_cache
    _calendar_cache = None
    _securities_cache = None
    _price_cache.clear()


# ── 证券搜索 ────────────────────────────────────────────────────────────────

def search_securities(query: str, types: str = "stock,etf,index", limit: int = 15) -> list[dict]:
    query = (query or "").strip().upper()
    if not query:
        return []
    results: list[dict] = []
    want = [t.strip() for t in types.split(",") if t.strip()]

    if "index" in want:
        for six, name in KNOWN_INDICES.items():
            if query in six or query in name.upper():
                results.append({"code": f"{six}.{_index_suffix(six)}", "name": name, "type": "index"})
        results = results[:8]

    table = _securities() if (_ak_available() or _securities_cache) else {"stock": [], "etf": []}
    for t in ("stock", "etf"):
        if t not in want:
            continue
        rows = table.get(t, [])
        if not rows:  # 静态表拿不到（demo 兜底）：用内置蓝筹池保证可搜
            rows = [{"code": c, "name": _DEMO_NAMES.get(c, c), "type": t} for c in DEFAULT_UNIVERSE]
        hits = [
            r for r in rows
            if query in r["code"] or query in r["name"].upper()
        ]
        results.extend(hits[:8])
        if len(results) >= limit:
            break
    return results[:limit]


_DEMO_NAMES = {c: n for c, n in zip(DEFAULT_UNIVERSE, [
    "贵州茅台", "中国平安", "招商银行", "五粮液", "长江电力", "兴业银行",
    "美的集团", "恒瑞医药", "隆基绿能", "格力电器", "中信证券", "海康威视",
])}


def get_security_name(code: str) -> str:
    code = norm_code(code)
    table = _securities() if (_ak_available() or _securities_cache) else {"stock": [], "etf": []}
    for t in ("stock", "etf"):
        for r in table.get(t, []):
            if r["code"] == code:
                return r["name"]
    if is_index(code):
        return KNOWN_INDICES.get(code.split(".")[0], code)
    return _DEMO_NAMES.get(code, code)


# ── 行情 K 线 ───────────────────────────────────────────────────────────────

_BAR_RENAME = {"日期": "date", "开盘": "open", "收盘": "close",
               "最高": "high", "最低": "low", "成交量": "volume"}


def _sina_symbol(code: str) -> str:
    six, _, suffix = code.partition(".")
    return {"SH": "sh", "SZ": "sz"}.get(suffix, "bj") + six


def _ak_hist_em(code: str, start: str, end: str, frequency: str) -> list[dict]:
    """东财通道：区间取数，天然支持日/周/月线。"""
    import akshare as ak

    six = code.split(".")[0]
    period = frequency if frequency in ("daily", "weekly", "monthly") else "daily"
    if sec_type(code) == "index":
        df = ak.index_zh_a_hist(symbol=six, period=period,
                                start_date=start.replace("-", ""),
                                end_date=end.replace("-", ""))
    elif sec_type(code) == "etf":
        df = ak.fund_etf_hist_em(symbol=six, period=period, adjust="qfq",
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""))
    else:
        df = ak.stock_zh_a_hist(symbol=six, period=period, adjust="qfq",
                                start_date=start.replace("-", ""),
                                end_date=end.replace("-", ""))
    if df is None or df.empty:
        raise DataProviderError(f"{code} 未取到行情数据")
    df = df.rename(columns=_BAR_RENAME)
    keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].copy()
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    return _df_to_bars(df)


def _ak_hist_sina(code: str, start: str, end: str, frequency: str) -> list[dict]:
    """新浪通道：全量日K取回后按区间切片；周/月线本地重采样。"""
    import akshare as ak

    t = sec_type(code)
    if t == "index":
        df = ak.stock_zh_index_daily(symbol=_sina_symbol(code))
    elif t == "etf":
        df = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
    else:
        df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
    if df is None or df.empty:
        raise DataProviderError(f"{code} 未取到行情数据")
    df = df.rename(columns=_BAR_RENAME)
    keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].copy()
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    if df.empty:
        raise DataProviderError(f"{code} 在 {start}~{end} 区间无数据")
    bars = _df_to_bars(df)
    if frequency == "weekly":
        return _resample_bars(bars, "W")
    if frequency == "monthly":
        return _resample_bars(bars, "ME")
    return bars


def _df_to_bars(df) -> list[dict]:
    bars = []
    for _, r in df.iterrows():
        bars.append({
            "date": str(r["date"]),
            "open": round(float(r["open"]), 3),
            "high": round(float(r["high"]), 3),
            "low": round(float(r["low"]), 3),
            "close": round(float(r["close"]), 3),
            "volume": float(r.get("volume", 0) or 0),
        })
    return bars


def _resample_bars(bars: list[dict], rule: str) -> list[dict]:
    """日线 → 周/月线重采样（W=ISO周, ME=自然月）。"""
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    agg = df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    return [
        {"date": d.strftime("%Y-%m-%d"),
         "open": round(float(r["open"]), 3), "high": round(float(r["high"]), 3),
         "low": round(float(r["low"]), 3), "close": round(float(r["close"]), 3),
         "volume": float(r["volume"] or 0)}
        for d, r in agg.iterrows()
    ]


def _ak_hist_raw(code: str, start: str, end: str, frequency: str) -> tuple[list[dict], str]:
    """双通道取数：东财优先（区间接口），被限流/断连时切新浪（全量+切片）。
    返回 (bars, 来源标签)。"""
    global _last_channel
    try:
        bars = _ak_hist_em(code, start, end, frequency)
        _last_channel = "akshare·东方财富"
        return bars, _last_channel
    except Exception:  # noqa: BLE001
        bars = _ak_hist_sina(code, start, end, frequency)
        _last_channel = "akshare·新浪"
        return bars, _last_channel


_last_channel = "akshare·东方财富"


def _fetch_hist(code: str, start: str, end: str, frequency: str) -> tuple[list[dict], str]:
    return _ak_hist_raw(code, start, end, frequency)


# ── demo 模拟数据（确定性 GBM） ─────────────────────────────────────────────

def _demo_bars(code: str, count: int) -> list[dict]:
    rng = random.Random(f"qlab-demo:{code}")
    n = max(count, 260)
    drift = rng.uniform(0.03, 0.14)          # 年化漂移
    vol = rng.uniform(0.16, 0.34)            # 年化波动
    price = rng.uniform(6, 90)
    base = rng.uniform(0.8, 1.6)             # 日均成交（万手级别，仅演示）
    # 交易日历含未来日期（新浪日历到年底），demo 数据必须截止到昨天
    cal = [d for d in _trade_calendar() if d <= _safe_end()]
    dates = cal[-n:]
    bars = []
    for d in dates:
        shock = rng.gauss(0, 1)
        ret = drift / 252 + vol / (252 ** 0.5) * shock
        open_p = price
        close_p = max(0.5, price * (1 + ret))
        high = max(open_p, close_p) * (1 + abs(rng.gauss(0, 0.006)))
        low = min(open_p, close_p) * (1 - abs(rng.gauss(0, 0.006)))
        bars.append({
            "date": d, "open": round(open_p, 3), "high": round(high, 3),
            "low": round(low, 3), "close": round(close_p, 3),
            "volume": round(max(0.1, base * (1 + rng.gauss(0, 0.3))), 2),
        })
        price = close_p
    return bars[-count:]


def _demo_fundamentals(code: str) -> dict:
    rng = random.Random(f"qlab-demo-fin:{code}")
    name = get_security_name(code)
    return {
        "code": code, "name": name,
        "source": "demo·模拟数据",
        "date": _safe_end(),
        "pe_ttm": round(rng.uniform(8, 42), 2),
        "pe_lyr": round(rng.uniform(9, 46), 2),
        "pb": round(rng.uniform(0.8, 8.0), 2),
        "ps_ttm": round(rng.uniform(0.8, 9.0), 2),
        "market_cap_billion": round(rng.uniform(60, 3200), 1),
        "circulating_cap_billion": round(rng.uniform(40, 2800), 1),
        "turnover_ratio_pct": round(rng.uniform(0.3, 4.5), 2),
    }


# ── 对外主接口 ──────────────────────────────────────────────────────────────

def _cache_get(key: tuple):
    hit = _price_cache.get(key)
    if hit and time.time() - hit[0] < _PRICE_TTL:
        return hit[1]
    return None


def _cache_put(key: tuple, bars: list[dict]) -> None:
    if len(_price_cache) > 300:
        _price_cache.clear()
    _price_cache[key] = (time.time(), bars)


def get_price_bars(code: str, count: int = 60, frequency: str = "daily") -> tuple[list[dict], str]:
    """按根数取 K 线，返回 (bars, source)。akshare 失败自动 demo。"""
    code = norm_code(code)
    count = max(1, min(int(count), 500))
    end = _safe_end()
    key = (code, frequency, f"count{count}", end)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    src = "akshare·东方财富"
    bars: list[dict] = []
    if _ak_available():
        # 多留自然日余量，停牌/复权导致根数不足时再往前补
        span = int(count * (1.8 if frequency == "daily" else 4.5))
        start = (dt.date.today() - dt.timedelta(days=span)).isoformat()
        try:
            bars, src = _call_ak(_fetch_hist, code, start, end, frequency)
            _mark_live()
        except Exception as e:  # noqa: BLE001
            _mark_fallback(str(e))
            bars = []
    if not bars:
        bars = _demo_bars(code, count)
        src = "demo·模拟数据"
    bars = bars[-count:]
    _cache_put(key, (bars, src))
    return bars, src


def get_price_range(code: str, start: str, end: str, frequency: str = "daily") -> tuple[list[dict], str]:
    """按日期区间取 K 线（回测沙箱用），返回 (bars, source)。"""
    code = norm_code(code)
    end = end or _safe_end()
    key = (code, frequency, start, end)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    src = "akshare·东方财富"
    bars: list[dict] = []
    if _ak_available():
        try:
            bars, src = _call_ak(_fetch_hist, code, start, end, frequency)
            _mark_live()
        except Exception as e:  # noqa: BLE001
            _mark_fallback(str(e))
    if not bars:
        cal = _trade_calendar()
        in_range = [d for d in cal if (start or cal[0]) <= d <= end]
        n = max(len(in_range), 260)
        bars = _demo_bars(code, n)
        bars = [b for b in bars if (start or "") <= b["date"] <= end] or bars[-len(in_range) or n:]
        src = "demo·模拟数据"
    _cache_put(key, (bars, src))
    return bars, src


def summarize_bars(code: str, bars: list[dict], frequency: str, src: str) -> dict:
    """把 K 线序列聚合成给 AI/前端看的摘要 + 明细。"""
    closes = [b["close"] for b in bars]
    period_chg = (closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 and closes[0] else 0.0
    ma5 = round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else None
    ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
    return {
        "code": code,
        "name": get_security_name(code),
        "frequency": frequency,
        "count": len(bars),
        "period": f"{bars[0]['date']} ~ {bars[-1]['date']}" if bars else "",
        "source": src,
        "latest_close": closes[-1] if closes else None,
        "period_change_pct": round(period_chg, 2),
        "period_high": max((b["high"] for b in bars), default=None),
        "period_low": min((b["low"] for b in bars), default=None),
        "ma5": ma5,
        "ma20": ma20,
        "data": bars,
    }


def get_price(code: str, count: int = 60, frequency: str = "daily") -> dict:
    bars, src = get_price_bars(code, count, frequency)
    return summarize_bars(norm_code(code), bars, frequency, src)


def get_latest_close(code: str, allow_demo: bool = False) -> Optional[float]:
    try:
        bars, source = get_price_bars(code, 2)
        if not allow_demo and source.startswith("demo"):
            raise DataProviderError(f"{norm_code(code)} 真实行情不可用，拒绝使用模拟价格")
        return bars[-1]["close"] if bars else None
    except DataProviderError:
        if not allow_demo:
            raise
        return None
    except Exception:  # noqa: BLE001
        return None


# ── 基本面 ──────────────────────────────────────────────────────────────────

def _ak_indicator_lg_raw(six: str):
    import akshare as ak

    df = ak.stock_a_indicator_lg(symbol=six)
    if df is None or df.empty:
        raise DataProviderError("indicator_lg 为空")
    return df.iloc[-1]


def _ak_basic_info_raw(six: str):
    import akshare as ak

    df = ak.stock_individual_info_em(symbol=six)
    if df is None or df.empty:
        raise DataProviderError("info_em 为空")
    return {str(r["item"]): r["value"] for _, r in df.iterrows()}


def get_fundamentals(code: str) -> dict:
    code = norm_code(code)
    if sec_type(code) != "stock":
        name = get_security_name(code)
        return {"code": code, "name": name, "source": "akshare·东方财富",
                "note": "指数/ETF 无个股基本面", "date": _safe_end()}
    six = code.split(".")[0]
    result: dict = {"code": code, "name": get_security_name(code),
                    "source": "akshare·东方财富", "date": _safe_end()}
    filled = False
    if _ak_available():
        try:
            row = _call_ak(_ak_indicator_lg_raw, six)
            result["pe_ttm"] = _num(row.get("pe_ttm"))
            result["pe_lyr"] = _num(row.get("pe"))
            result["pb"] = _num(row.get("pb"))
            result["ps_ttm"] = _num(row.get("ps_ttm"))
            total_mv = row.get("total_mv")
            if total_mv is not None and pd.notna(total_mv):
                mv = float(total_mv)
                # 乐咕 total_mv 单位为元，超过 1e6 视作元并换算亿元
                result["market_cap_billion"] = round(mv / 1e8, 1) if mv > 1e6 else round(mv, 1)
            result["date"] = str(row.get("trade_date", result["date"]))[:10]
            filled = True
            _mark_live()
        except Exception as e:  # noqa: BLE001
            _mark_fallback(str(e))
        if "pe_ttm" not in result or result.get("pe_ttm") is None:
            try:
                info = _call_ak(_ak_basic_info_raw, six)
                if "市盈率(动态)" in info:
                    result["pe_ttm"] = _num(info.get("市盈率(动态)"))
                if "总市值" in info:
                    mv = float(info["总市值"])
                    result["market_cap_billion"] = round(mv / 1e8, 1)
                if "行业" in info:
                    result["industry"] = str(info["行业"])
                filled = True
            except Exception:  # noqa: BLE001
                pass
    if not filled:
        return _demo_fundamentals(code)
    return result


def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# ── 指数成分 ────────────────────────────────────────────────────────────────

def _ak_index_cons_csindex(six: str) -> list[dict]:
    import akshare as ak

    df = ak.index_stock_cons_csindex(symbol=six)
    if df is None or df.empty:
        raise DataProviderError("csindex 成分为空")
    # 列名精确优先：表里同时存在「指数代码」和「成分券代码」，模糊匹配会取错
    cols = [str(c) for c in df.columns]

    def pick(*names: str) -> Optional[str]:
        for n in names:
            if n in cols:
                return n
        for n in names:
            for c in cols:
                if n in c:
                    return c
        return None

    code_col = pick("成分券代码", "品种代码", "证券代码")
    name_col = pick("成分券名称", "品种名称", "证券名称")
    if not code_col:
        raise DataProviderError(f"未识别成分代码列，现有列: {cols[:8]}")
    out, seen = [], set()
    for _, r in df.iterrows():
        six_r = str(r[code_col]).zfill(6)
        if not six_r.isdigit() or six_r in seen:
            continue
        seen.add(six_r)
        out.append({"code": f"{six_r}.{suffix_of(six_r)}",
                    "name": str(r[name_col]) if name_col else six_r})
    if not out:
        raise DataProviderError("成分列表为空")
    return out


def get_index_stocks(index_code: str, limit: int = 50) -> dict:
    code = norm_code(index_code) if not is_index(index_code) else index_code
    six = code.split(".")[0]
    name = KNOWN_INDICES.get(six, get_security_name(code))
    stocks: list[dict] = []
    src = "akshare·中证指数"
    if _ak_available():
        try:
            stocks = _call_ak(_ak_index_cons_csindex, six, timeout=30)
            _mark_live()
        except Exception as e:  # noqa: BLE001
            _mark_fallback(str(e))
    if not stocks:
        stocks = [{"code": c, "name": _DEMO_NAMES.get(c, c)} for c in DEFAULT_UNIVERSE]
        src = "demo·模拟数据（蓝筹示例池）"
    return {
        "index": code, "name": name, "source": src, "count": len(stocks),
        "stocks": stocks[:limit],
        "note": "成分股列表可能滞后于指数公司最新调整，仅供研究参考",
    }
