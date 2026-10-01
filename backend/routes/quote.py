"""行情查询路由 — 前端直连（持仓联想、K线图、模型分析师等），数据走 data_provider。"""
from __future__ import annotations

import data_provider as dp
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/search")
def search(q: str = Query(..., min_length=1), types: str = "stock,etf,index"):
    return dp.search_securities(q, types=types, limit=20)


@router.get("/quote/{code}")
def quote(code: str):
    """最新报价：取近两天日K算涨跌。（注意路由已含 /api/quote 前缀，此处再带一级 quote 以区分 search）"""
    try:
        bars, src = dp.get_price_bars(code, count=2)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"取数失败：{e}")
    if not bars:
        raise HTTPException(404, f"{code} 无行情数据")
    last, prev = bars[-1], (bars[-2] if len(bars) > 1 else bars[-1])
    change = last["close"] - prev["close"]
    return {
        "code": dp.norm_code(code),
        "name": dp.get_security_name(code),
        "source": src,
        "date": last["date"],
        "open": last["open"], "high": last["high"],
        "low": last["low"], "close": last["close"],
        "volume": last["volume"],
        "change": round(change, 3),
        "change_pct": round(change / prev["close"] * 100, 2) if prev["close"] else 0.0,
    }


@router.get("/kline/{code}")
def kline(code: str, frequency: str = "daily", count: int = Query(60, le=500)):
    bars, src = dp.get_price_bars(code, count=count, frequency=frequency)
    return {"code": dp.norm_code(code), "source": src, "bars": bars}


@router.get("/trade-days")
def trade_days(start: str = "", end: str = ""):
    return dp.get_trade_days(start, end)


@router.get("/index-stocks")
def index_stocks(index: str = "000300.SH"):
    return dp.get_index_stocks(index)


@router.get("/analyst/{code}")
def analyst(code: str):
    """模型分析师：聚合东方财富股吧评分、机构参与度、用户关注度。"""
    import akshare as ak
    import concurrent.futures

    six = dp.norm_code(code).split(".")[0]
    result = {
        "code": six,
        "name": dp.get_security_name(code),
        "source": "akshare·东方财富",
        "desire": None,
        "focus": None,
        "score": None,
        "institution": None,
        "note": "这些指标仅展示原始同口径数据；缺失值不会用资金流等异质指标推算",
    }

    def safe_call(fn, timeout=20):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn)
                return future.result(timeout=timeout)
        except Exception:
            return None

    # 1) akshare 主路径
    def get_desire():
        df = ak.stock_comment_detail_scrd_desire_em(symbol=six)
        if df is not None and not df.empty:
            latest = df.sort_values("交易日期").iloc[-1]
            return {
                "date": str(latest["交易日期"])[:10],
                "value": round(float(latest["参与意愿"]), 2),
                "avg_5d": round(float(latest["5日平均参与意愿"]), 2),
                "change": round(float(latest["参与意愿变化"]), 2),
                "avg_change_5d": round(float(latest["5日平均变化"]), 2),
            }
        return None

    def get_focus():
        df = ak.stock_comment_detail_scrd_focus_em(symbol=six)
        if df is not None and not df.empty:
            latest = df.sort_values("交易日").iloc[-1]
            return {
                "date": str(latest["交易日"])[:10],
                "value": round(float(latest["用户关注指数"]), 2),
            }
        return None

    def get_score():
        df = ak.stock_comment_detail_zhpj_lspf_em(symbol=six)
        if df is not None and not df.empty:
            latest = df.sort_values("交易日").iloc[-1]
            return {
                "date": str(latest["交易日"])[:10],
                "value": round(float(latest["评分"]), 2),
            }
        return None

    def get_institution():
        df = ak.stock_comment_detail_zlkp_jgcyd_em(symbol=six)
        if df is not None and not df.empty:
            latest = df.sort_values("交易日").iloc[-1]
            return {
                "date": str(latest["交易日"])[:10],
                "value": round(float(latest["机构参与度"]), 2),
            }
        return None

    desire = safe_call(get_desire)
    focus = safe_call(get_focus)
    score = safe_call(get_score)
    institution = safe_call(get_institution)

    result["desire"] = desire
    result["focus"] = focus
    result["score"] = score
    result["institution"] = institution
    return result
