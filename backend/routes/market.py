"""市场复盘路由 — 指数看板 + 市场宽度 + 自动复盘要点。

数据源（全部免费，无注册）：
  - 市场活跃度：乐咕（上涨/下跌/涨停/跌停/活跃度，收盘后为当日快照）
  - 指数行情：新浪指数日K（取最近 21 根算最新价/涨跌幅/5日与20日趋势）

聚合结果缓存 5 分钟；单项失败只降级该项，不拖垮整个看板。
"""
from __future__ import annotations

import concurrent.futures
import threading
import time

import akshare as ak
from fastapi import APIRouter

router = APIRouter()

_cache = {"ts": 0.0, "data": None}
_lock = threading.Lock()
_TTL = 300.0
_EXEC = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="market")

# (内部代码, 新浪代码, 名称)
INDEX_LIST = [
    ("000001.SH", "sh000001", "上证指数"),
    ("399001.SZ", "sz399001", "深证成指"),
    ("399006.SZ", "sz399006", "创业板指"),
    ("000300.SH", "sh000300", "沪深300"),
    ("000905.SH", "sh000905", "中证500"),
    ("000688.SH", "sh000688", "科创50"),
]

_index_cache: dict[str, tuple[float, list[dict]]] = {}
_INDEX_TTL = 1800.0  # 指数日K缓存 30 分钟
_sector_cache: dict = {"ts": 0.0, "data": None}


def _fetch_sectors() -> list[dict] | None:
    """新浪行业板块（49 个）：涨跌幅 + 领涨股 + 成交额。"""
    now = time.time()
    if _sector_cache["data"] and now - _sector_cache["ts"] < _INDEX_TTL:
        return _sector_cache["data"]
    try:
        df = ak.stock_sector_spot(indicator="新浪行业")
        rows = []
        for _, r in df.iterrows():
            try:
                rows.append({
                    "name": str(r["板块"]),
                    "change_pct": round(float(r["涨跌幅"]), 2),
                    "count": int(float(r.get("公司家数", 0) or 0)),
                    "leader": str(r.get("股票名称", "")),
                    "leader_code": str(r.get("股票代码", "")).upper(),
                    "leader_chg": round(float(r.get("个股-涨跌幅", 0) or 0), 2),
                    "amount_yi": round(float(r.get("总成交额", 0) or 0) / 1e8, 1),
                })
            except (TypeError, ValueError, KeyError):
                continue
        rows.sort(key=lambda x: x["change_pct"], reverse=True)
        _sector_cache.update({"ts": now, "data": rows})
        return rows
    except Exception:  # noqa: BLE001
        return _sector_cache["data"]


def _fetch_activity() -> dict | None:
    try:
        df = ak.stock_market_activity_legu()
        kv = {str(r["item"]).strip(): r["value"] for _, r in df.iterrows()}
        up = float(kv.get("上涨", 0) or 0)
        down = float(kv.get("下跌", 0) or 0)
        return {
            "up": int(up),
            "down": int(down),
            "limit_up": int(float(kv.get("涨停", 0) or 0)),
            "real_limit_up": int(float(kv.get("真实涨停", 0) or 0)),
            "limit_down": int(float(kv.get("跌停", 0) or 0)),
            "flat": int(float(kv.get("平盘", 0) or 0)),
            "suspended": int(float(kv.get("停牌", 0) or 0)),
            "active_pct": float(str(kv.get("活跃度", "0")).replace("%", "") or 0),
            "date": str(kv.get("统计日期", ""))[:16],
            "breadth": round(up / (up + down), 3) if (up + down) > 0 else None,
        }
    except Exception:  # noqa: BLE001
        return None


def _fetch_index(code: str, sina_sym: str, name: str) -> dict | None:
    cached = _index_cache.get(code)
    now = time.time()
    if cached and now - cached[0] < _INDEX_TTL:
        bars = cached[1]
    else:
        bars = []
        for attempt in (1, 2):  # 新浪偶发断连，单票重试一次
            try:
                fut = _EXEC.submit(_ak_index_daily, sina_sym)
                bars = fut.result(timeout=30)
                break
            except Exception:  # noqa: BLE001
                if attempt == 1:
                    time.sleep(1.0)
        if bars:
            _index_cache[code] = (now, bars)
        elif not cached:
            return None
        else:
            bars = cached[1]
    if len(bars) < 2:
        return None
    last, prev = bars[-1], bars[-2]

    def chg(n: int) -> float | None:
        if len(bars) < n + 1 or not bars[-n - 1]["close"]:
            return None
        return round((last["close"] / bars[-n - 1]["close"] - 1) * 100, 2)

    return {
        "code": code,
        "name": name,
        "date": last["date"],
        "close": last["close"],
        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2) if prev["close"] else None,
        "chg_5d": chg(5),
        "chg_20d": chg(20),
        # 近 60 日收盘价迷你走势（前端 sparkline）
        "spark": [b["close"] for b in bars[-60:]],
    }


def _ak_index_daily(sina_sym: str) -> list[dict]:
    df = ak.stock_zh_index_daily(symbol=sina_sym)
    out = []
    for _, r in df.tail(61).iterrows():  # 61 根：20日趋势计算 + 60 点迷你走势
        out.append({
            "date": str(r["date"])[:10],
            "close": round(float(r["close"]), 2),
        })
    return out


def _conclusions(activity: dict | None, indices: list[dict], sectors: list[dict] | None) -> list[str]:
    """模板化自动复盘：数据说话，不带主观臆测。"""
    out: list[str] = []
    if activity:
        up, down = activity["up"], activity["down"]
        breadth = activity.get("breadth")
        if breadth is not None:
            if breadth >= 0.6:
                out.append(f"市场宽度偏强：{up} 家上涨 vs {down} 家下跌（上涨占比 {breadth:.0%}），赚钱效应尚可。")
            elif breadth <= 0.4:
                out.append(f"市场宽度偏弱：仅 {up} 家上涨 vs {down} 家下跌（上涨占比 {breadth:.0%}），注意防守。")
            else:
                out.append(f"市场宽度均衡：{up} 涨 / {down} 跌（占比 {breadth:.0%}），多空拉锯。")
        lu, ld = activity["limit_up"], activity["limit_down"]
        if lu >= 3 * max(ld, 1):
            out.append(f"涨停 {lu} 家 vs 跌停 {ld} 家，短线情绪明显偏暖。")
        elif ld > lu:
            out.append(f"跌停 {ld} 家多于涨停 {lu} 家，亏钱效应扩散，谨慎追高。")
        out.append(f"市场活跃度 {activity['active_pct']:.1f}%（统计截至 {activity['date']}）。")
    sh = next((i for i in indices if i["code"] == "000001.SH"), None)
    if sh and sh.get("chg_20d") is not None:
        d20 = sh["chg_20d"]
        trend = "中期趋势向上" if d20 > 1 else ("中期趋势走弱" if d20 < -1 else "中期横盘震荡")
        out.append(f"上证指数 20 日累计 {d20:+.2f}%，{trend}；5 日 {sh.get('chg_5d', 0):+.2f}%。")
    if sectors and len(sectors) >= 10:
        top, bottom = sectors[0], sectors[-1]
        out.append(
            f"板块方面：{top['name']}（{top['change_pct']:+.2f}%，领涨股 {top['leader']} {top['leader_chg']:+.2f}%）最强；"
            f"{bottom['name']}（{bottom['change_pct']:+.2f}%）最弱。"
        )
    return out


@router.get("/overview")
def overview(force: bool = False):
    now = time.time()
    with _lock:
        if not force and _cache["data"] and now - _cache["ts"] < _TTL:
            return _cache["data"]

    activity = _fetch_activity()
    # 串行 + 节流：新浪对并发连接限流，并行会整批失败（失败的单票自带一次重试）
    indices = []
    for pair in INDEX_LIST:
        r = _fetch_index(pair[0], pair[1], pair[2])
        if r:
            indices.append(r)
        time.sleep(0.3)
    sectors = _fetch_sectors()

    data = {
        "activity": activity,
        "indices": indices,
        "sectors": sectors,
        "conclusions": _conclusions(activity, indices, sectors),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "akshare·乐咕/新浪（免费公开数据）",
    }
    with _lock:
        _cache["ts"] = now
        _cache["data"] = data
    return data
