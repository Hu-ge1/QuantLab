"""A 股批量筛选器：FFD 全市场日频优先，腾讯批量快照降级。

筛选和评分全部在本地完成。任何缺失字段保持为空，不补零、不伪造。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from pathlib import Path

import data_provider as dp
from database import get_setting

_CACHE: dict[str, Any] = {"at": 0.0, "rows": [], "source": "", "as_of": ""}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300.0
_DISK_CACHE = Path(__file__).parent / "data" / "screener_snapshot.json"
_FINANCIAL_CACHE: dict[str, dict[str, Any]] = {}
_FINANCIAL_CACHE_LOCK = threading.Lock()
_FINANCIAL_CACHE_TTL = 3600.0
_FINANCIAL_POOL_LIMIT = 300

PROFILES = {
    "balanced": {
        "label": "均衡",
        "weights": {"value": .25, "trend": .25, "strength": .15, "liquidity": .20, "stability": .15},
        "financial_weights": {"value": .18, "quality": .17, "growth": .18, "trend": .18, "strength": .08, "liquidity": .12, "stability": .09},
    },
    "value": {
        "label": "价值",
        "weights": {"value": .50, "trend": .15, "strength": .05, "liquidity": .15, "stability": .15},
        "financial_weights": {"value": .38, "quality": .22, "growth": .12, "trend": .10, "strength": .03, "liquidity": .10, "stability": .05},
    },
    "momentum": {
        "label": "中期趋势",
        "weights": {"value": .05, "trend": .58, "strength": .08, "liquidity": .14, "stability": .15},
        "financial_weights": {"value": .04, "quality": .07, "growth": .11, "trend": .50, "strength": .06, "liquidity": .10, "stability": .12},
    },
}


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "-"):
            return None
        value = float(str(value).replace(",", "").replace("%", ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _first(row: dict, *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _records(value: Any) -> list[dict]:
    """兼容 FFD 的 list-of-dicts、dict-of-lists 与 raw_rows 包装。"""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("raw_rows", "rows", "items", "data", "result"):
        nested = value.get(key)
        rows = _records(nested)
        if rows:
            return rows
    list_keys = [k for k, v in value.items() if isinstance(v, list)]
    if list_keys:
        n = max(len(value[k]) for k in list_keys)
        return [{k: (value[k][i] if i < len(value[k]) else None) for k in list_keys} for i in range(n)]
    return []


def _normalize_ffd_row(row: dict) -> dict | None:
    raw_code = str(_first(row, "ts_code", "code", "证券代码", "股票代码", "thscode") or "").upper()
    six = "".join(ch for ch in raw_code.split(".")[0] if ch.isdigit()).zfill(6)
    if len(six) != 6:
        return None
    try:
        code = dp.norm_code(raw_code if "." in raw_code else six)
    except Exception:
        return None
    amount = _num(_first(row, "amount", "amt", "成交额"))
    # FFD 标准 raw 口径通常为元；异常小的展示口径不擅自换算。
    amount_yi = amount / 1e8 if amount is not None and amount > 1e6 else None
    float_cap = _num(_first(row, "circ_mv", "float_market_cap", "流通市值"))
    total_cap = _num(_first(row, "total_mv", "market_cap", "总市值"))
    # FFD market_daily 默认金额口径为人民币元；Tushare 兼容口径仅在显式 unit_mode 时启用。
    float_cap_yi = float_cap / 1e8 if float_cap is not None else None
    total_cap_yi = total_cap / 1e8 if total_cap is not None else None
    return {
        "code": code,
        "name": str(_first(row, "name", "sec_name", "证券简称", "股票简称", "名称") or six),
        "price": _num(_first(row, "close", "latest", "最新价", "收盘价")),
        "change_pct": _num(_first(row, "pct_chg", "changeRatio", "change_pct", "涨跌幅")),
        "amount_yi": amount_yi,
        "turnover": _num(_first(row, "turnover_rate", "turnover", "换手率")),
        "pe_ttm": _num(_first(row, "pe_ttm", "PE_TTM", "市盈率TTM", "动态市盈率")),
        "pb": _num(_first(row, "pb", "PB", "市净率")),
        "market_cap_yi": total_cap_yi if total_cap_yi is not None else float_cap_yi,
        "float_cap_yi": float_cap_yi,
        "market_cap_basis": "总市值" if total_cap_yi is not None else "流通市值",
        "amplitude": _num(_first(row, "amplitude", "振幅")),
        "volume_ratio": _num(_first(row, "volume_ratio", "量比")),
        "ma60": _num(_first(row, "ma60", "MA60")),
        "ma120": _num(_first(row, "ma120", "MA120")),
        "ma250": _num(_first(row, "ma250", "MA250")),
    }


def _load_ffd() -> tuple[list[dict], str, str]:
    """读取一份全市场日频资产；失败交给腾讯降级，不吞掉最终错误。"""
    from ffd import data as ffd_data

    api_key = get_setting("ffd_api_key", "") or os.environ.get("FFD_API_KEY", "")
    ffd_data.login(api_key=api_key or None)
    trade_date = dp._safe_end()  # 与项目其他收盘级功能使用同一安全交易日
    payload = ffd_data.query(
        function="market_daily", trade_date=trade_date, profile="enhanced",
        output_mode="raw", page_size=6000, as_df=False,
    )
    rows = [r for r in (_normalize_ffd_row(x) for x in _records(payload)) if r]
    if len(rows) < 100:
        raise RuntimeError(f"FFD 全市场日频有效记录不足（{len(rows)} 条）")
    # market_daily 的增强档提供均线、量比、换手和流通市值，但不承诺 PE/PB。
    # 用腾讯同一时点的批量快照补齐估值/振幅；补齐失败时保留 FFD 原值与空值。
    source = "FFD·全市场日频"
    try:
        supplements, _, _ = _load_tencent([{"code": r["code"], "name": r["name"]} for r in rows])
        by_code = {r["code"]: r for r in supplements}
        for row in rows:
            extra = by_code.get(row["code"])
            if not extra:
                continue
            for key in ("pe_ttm", "pb", "amplitude"):
                if row.get(key) is None and extra.get(key) is not None:
                    row[key] = extra[key]
            if extra.get("market_cap_yi") is not None:
                row["market_cap_yi"] = extra["market_cap_yi"]
                row["market_cap_basis"] = "总市值"
        source += " + 公开行情估值补全"
    except Exception:
        pass
    return rows, source, trade_date


def _latest_complete_report_period(as_of: str) -> str:
    """Return the latest conservatively completed A-share reporting period."""
    day = dt.date.fromisoformat(as_of[:10])
    if day.month >= 11:
        return f"{day.year}-09-30"
    if day.month >= 9:
        return f"{day.year}-06-30"
    if day.month >= 5:
        return f"{day.year}-03-31"
    return f"{day.year - 1}-09-30"


def _normalize_ffd_financial_item(item: dict) -> dict | None:
    try:
        code = dp.norm_code(str(_first(item, "stockCode", "ts_code", "code") or ""))
    except Exception:
        return None
    values = item.get("values") if isinstance(item.get("values"), dict) else item
    metadata = item.get("fieldMetadata") if isinstance(item.get("fieldMetadata"), dict) else {}
    result: dict[str, Any] = {
        "code": code,
        "financial_report_period": str(_first(item, "reportPeriod", "report_period") or "")[:10] or None,
    }
    available_at: list[str] = []
    for field in ("gross_margin", "revenue_yoy", "net_profit_yoy"):
        field_meta = metadata.get(field) if isinstance(metadata.get(field), dict) else {}
        # PIT facts are used only when the field itself is decision eligible.  A
        # partially covered row may correctly have item-level decisionEligible=false.
        eligible = field_meta.get("decisionEligible")
        value = _num(values.get(field))
        result[field] = value if eligible is not False else None
        if result[field] is not None and field_meta.get("availableAt"):
            available_at.append(str(field_meta["availableAt"]))
    result["financial_available_at"] = max(available_at) if available_at else None
    return result


def _load_ffd_financials(codes: list[str], as_of: str) -> tuple[dict[str, dict], dict]:
    """Load one bounded PIT financial batch; never retry disclosed gaps per stock."""
    unique = list(dict.fromkeys(codes))[:_FINANCIAL_POOL_LIMIT]
    if not unique:
        return {}, {"requested": 0, "returned": 0}
    report_period = _latest_complete_report_period(as_of)
    now = time.time()
    cached: dict[str, dict] = {}
    missing: list[str] = []
    with _FINANCIAL_CACHE_LOCK:
        for code in unique:
            hit = _FINANCIAL_CACHE.get(code)
            if (
                hit and hit.get("as_of") == as_of and hit.get("report_period") == report_period
                and now - float(hit.get("at") or 0) < _FINANCIAL_CACHE_TTL
            ):
                if isinstance(hit.get("data"), dict):
                    cached[code] = dict(hit["data"])
            else:
                missing.append(code)

    if missing:
        api_key = get_setting("ffd_api_key", "") or os.environ.get("FFD_API_KEY", "")
        if not api_key:
            raise RuntimeError("尚未配置 FFD API Key")
        api_base = os.environ.get("FFD_API_BASE", "https://ffd.findesk.cn/api").rstrip("/")
        params = urllib.parse.urlencode({
            "codes": ",".join(missing),
            "fields": "gross_margin,revenue_yoy,net_profit_yoy",
            "report_periods": report_period,
            "as_of": f"{as_of[:10]}T23:59:59+08:00",
            "coverage_policy": "return_available",
        })
        request = urllib.request.Request(
            f"{api_base}/v1/pit/financials?{params}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "QuantLab/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("FFD 历史时点财务请求失败") from exc
        if not isinstance(payload, dict) or int(payload.get("code", 200)) != 200:
            raise RuntimeError("FFD 历史时点财务未返回可用结果")
        delivered = {}
        for raw in _records(payload):
            item = _normalize_ffd_financial_item(raw)
            if item and item["code"] in missing:
                delivered[item["code"]] = item
        with _FINANCIAL_CACHE_LOCK:
            for code in missing:
                data = delivered.get(code)
                _FINANCIAL_CACHE[code] = {
                    "at": now, "as_of": as_of, "report_period": report_period, "data": data,
                }
        cached.update(delivered)

    return cached, {
        "requested": len(unique), "returned": len(cached), "report_period": report_period,
        "fields": ["gross_margin", "revenue_yoy", "net_profit_yoy"],
    }


def _tencent_symbol(code: str) -> str:
    six, suffix = code.split(".")
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix, "") + six


def _parse_tencent_payload(text: str, names: dict[str, str] | None = None) -> list[dict]:
    rows: list[dict] = []
    names = names or {}
    for part in text.split(";"):
        if '="' not in part:
            continue
        left, raw = part.split('="', 1)
        symbol = left.rsplit("_", 1)[-1]
        fields = raw.rstrip('"\r\n').split("~")
        if len(fields) < 50:
            continue
        six = fields[2] if len(fields) > 2 and fields[2].isdigit() else symbol[-6:]
        try:
            code = dp.norm_code(six)
        except Exception:
            continue
        rows.append({
            "code": code, "name": fields[1] or names.get(code, six),
            "price": _num(fields[3]), "change_pct": _num(fields[32]),
            "amount_yi": (_num(fields[37]) or 0) / 10000,  # 腾讯成交额字段单位：万元
            "turnover": _num(fields[38]), "pe_ttm": _num(fields[39]),
            "amplitude": _num(fields[43]), "market_cap_yi": _num(fields[44]),
            "float_cap_yi": _num(fields[45]), "pb": _num(fields[46]),
            "volume_ratio": _num(fields[49]),
        })
    return rows


def _fetch_tencent_chunk(chunk: list[dict]) -> list[dict]:
    symbols = ",".join(_tencent_symbol(x["code"]) for x in chunk)
    req = urllib.request.Request(
        f"https://qt.gtimg.cn/q={symbols}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("gbk", errors="ignore")
    return _parse_tencent_payload(text, {x["code"]: x["name"] for x in chunk})


def _load_tencent(universe: list[dict] | None = None) -> tuple[list[dict], str, str]:
    universe = universe or dp._securities().get("stock", [])
    if len(universe) < 100:
        raise RuntimeError("无法取得完整 A 股证券列表，已拒绝用演示股票池冒充全市场")
    chunks = [universe[i:i + 60] for i in range(0, len(universe), 60)]
    rows: list[dict] = []
    # 腾讯批量端点无需逐股扇出；每个请求含 60 只。16 个并发批次可把
    # 约 5,500 只的补全阶段控制在几十秒，且请求总量仍只有约 93 个。
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="tencent-quote") as pool:
        futures = [pool.submit(_fetch_tencent_chunk, c) for c in chunks]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception:
                continue
    if len(rows) < 100:
        raise RuntimeError(f"腾讯批量行情有效记录不足（{len(rows)} 条）")
    return rows, "公开批量行情（FFD不可用时降级）", dt.datetime.now().astimezone().isoformat(timespec="seconds")


def get_snapshot(force: bool = False) -> tuple[list[dict], str, str]:
    with _CACHE_LOCK:
        if not force and _CACHE["rows"] and time.time() - _CACHE["at"] < _CACHE_TTL:
            return _CACHE["rows"], _CACHE["source"], _CACHE["as_of"]
    if not force and _DISK_CACHE.exists():
        try:
            saved = json.loads(_DISK_CACHE.read_text(encoding="utf-8"))
            # 收盘级资产按安全交易日复用；交易日推进后自动失效。
            if saved.get("as_of") == dp._safe_end() and len(saved.get("rows") or []) >= 100:
                with _CACHE_LOCK:
                    _CACHE.update(at=time.time(), rows=saved["rows"], source=saved["source"], as_of=saved["as_of"])
                return saved["rows"], saved["source"] + "（本地缓存）", saved["as_of"]
        except Exception:
            pass
    errors = []
    for loader in (_load_ffd, _load_tencent):
        try:
            rows, source, as_of = loader()
            with _CACHE_LOCK:
                _CACHE.update(at=time.time(), rows=rows, source=source, as_of=as_of)
            try:
                _DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
                tmp = _DISK_CACHE.with_suffix(".tmp")
                tmp.write_text(json.dumps({"source": source, "as_of": as_of, "rows": rows}, ensure_ascii=False), encoding="utf-8")
                tmp.replace(_DISK_CACHE)
            except Exception:
                pass
            return rows, source, as_of
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{loader.__name__}: {exc}")
    raise RuntimeError("；".join(errors))


def _percentile(values: list[float], value: float, reverse: bool = False) -> float:
    if not values:
        return 50.0
    rank = sum(v <= value for v in values) / len(values) * 100
    return 100 - rank if reverse else rank


def _market_match(code: str, market: str) -> bool:
    six, suffix = code.split(".")
    if market == "main":
        # 沪深 A 股主板：显式使用主板号段，避免把 B 股、科创板、
        # 创业板或北交所代码混入趋势池。
        if suffix == "SH":
            return six.startswith(("600", "601", "603", "605"))
        if suffix == "SZ":
            return six.startswith(("000", "001", "002", "003"))
        return False
    if market == "star": return six.startswith("68")
    if market == "chinext": return six.startswith("30")
    if market == "beijing": return suffix == "BJ"
    return True


def _trend_features(row: dict) -> dict[str, Any]:
    """Score an explainable medium-term trend without rewarding one-day spikes."""
    price = _num(row.get("price"))
    ma60 = _num(row.get("ma60"))
    ma120 = _num(row.get("ma120"))
    ma250 = _num(row.get("ma250"))
    if not all(value is not None and value > 0 for value in (price, ma60, ma120, ma250)):
        return {
            "trend_score": 0.0, "trend_stage": "均线数据不足", "strict_uptrend": False,
            "ma60_distance_pct": None, "ma60_120_spread_pct": None,
            "ma120_250_spread_pct": None, "alignment_score": 0.0,
        }

    distance = (price / ma60 - 1) * 100
    spread_60_120 = (ma60 / ma120 - 1) * 100
    spread_120_250 = (ma120 / ma250 - 1) * 100
    conditions = (price > ma60, ma60 > ma120, ma120 > ma250)
    strict_uptrend = all(conditions)
    alignment_score = sum(conditions) / len(conditions) * 100

    # A healthy trend is above MA60 but not excessively extended.  This keeps
    # a one-day vertical spike from outranking a persistent, orderly advance.
    if distance < 0:
        distance_score = max(0.0, 45 + distance * 3)
    elif distance <= 5:
        distance_score = 70 + distance * 6
    elif distance <= 15:
        distance_score = 100.0
    elif distance <= 30:
        distance_score = 100 - (distance - 15) * 3
    else:
        distance_score = max(0.0, 55 - (distance - 30) * 2)
    slope_score = (
        max(0.0, min(100.0, 50 + spread_60_120 * 7))
        + max(0.0, min(100.0, 50 + spread_120_250 * 5))
    ) / 2
    trend_score = alignment_score * .45 + distance_score * .30 + slope_score * .25

    if strict_uptrend and distance <= 15 and spread_60_120 > 0 and spread_120_250 > 0:
        stage = "强趋势"
    elif strict_uptrend and distance > 15:
        stage = "多头偏热"
    elif price > ma60 and ma60 > ma120:
        stage = "趋势形成"
    elif price > ma60:
        stage = "趋势尝试"
    else:
        stage = "趋势转弱"
    return {
        "trend_score": round(trend_score, 1), "trend_stage": stage,
        "strict_uptrend": strict_uptrend,
        "ma60_distance_pct": round(distance, 2),
        "ma60_120_spread_pct": round(spread_60_120, 2),
        "ma120_250_spread_pct": round(spread_120_250, 2),
        "alignment_score": round(alignment_score, 1),
    }


def screen(filters: dict, force: bool = False) -> dict:
    rows, source, as_of = get_snapshot(force=force)
    keyword = str(filters.get("keyword") or "").strip().lower()
    # 本模块专用于沪深主板趋势选股；后端强制限定，不能被旧前端或
    # 手工 API 参数绕过。
    market = "main"

    def ok(r: dict) -> bool:
        if keyword and keyword not in (r["code"] + r["name"]).lower(): return False
        if not _market_match(r["code"], market): return False
        if filters.get("exclude_st", True) and ("ST" in r["name"].upper() or "退" in r["name"]): return False
        if filters.get("above_ma60") and (r.get("ma60") is None or r["price"] <= r["ma60"]): return False
        if filters.get("strict_uptrend") and not _trend_features(r)["strict_uptrend"]: return False
        checks = [
            ("market_cap_yi", "min_market_cap", lambda a, b: a >= b),
            ("market_cap_yi", "max_market_cap", lambda a, b: a <= b),
            ("pe_ttm", "min_pe", lambda a, b: a >= b), ("pe_ttm", "max_pe", lambda a, b: a <= b),
            ("pb", "max_pb", lambda a, b: a <= b), ("amount_yi", "min_amount_yi", lambda a, b: a >= b),
            ("turnover", "min_turnover", lambda a, b: a >= b),
            ("change_pct", "min_change", lambda a, b: a >= b), ("change_pct", "max_change", lambda a, b: a <= b),
        ]
        for field, key, fn in checks:
            bound = filters.get(key)
            if bound is not None and (r.get(field) is None or not fn(r[field], float(bound))): return False
        return True

    matched = [dict(r) for r in rows if r.get("price") is not None and ok(r)]
    pools = {
        "pe": [r["pe_ttm"] for r in matched if r.get("pe_ttm") and r["pe_ttm"] > 0],
        "pb": [r["pb"] for r in matched if r.get("pb") and r["pb"] > 0],
        "chg": [r["change_pct"] for r in matched if r.get("change_pct") is not None],
        "vr": [r["volume_ratio"] for r in matched if r.get("volume_ratio") is not None],
        "amt": [r["amount_yi"] for r in matched if r.get("amount_yi") is not None],
        "amp": [r["amplitude"] for r in matched if r.get("amplitude") is not None],
        "trend": [],
    }
    for r in matched:
        price = r.get("price")
        ma60 = r.get("ma60")
        if price and ma60 and ma60 > 0:
            pools["trend"].append((price / ma60 - 1) * 100)
    profile = filters.get("profile") if filters.get("profile") in PROFILES else "balanced"
    legacy_weights = PROFILES[profile]["weights"]
    for r in matched:
        value_parts = []
        if r.get("pe_ttm") and r["pe_ttm"] > 0: value_parts.append(_percentile(pools["pe"], r["pe_ttm"], True))
        if r.get("pb") and r["pb"] > 0: value_parts.append(_percentile(pools["pb"], r["pb"], True))
        value = sum(value_parts) / len(value_parts) if value_parts else 0
        strength_parts = []
        if r.get("change_pct") is not None: strength_parts.append(_percentile(pools["chg"], r["change_pct"]))
        if r.get("volume_ratio") is not None: strength_parts.append(_percentile(pools["vr"], r["volume_ratio"]))
        strength = sum(strength_parts) / len(strength_parts) if strength_parts else 0
        liquidity = _percentile(pools["amt"], r["amount_yi"]) if r.get("amount_yi") is not None else 0
        stability = _percentile(pools["amp"], r["amplitude"], True) if r.get("amplitude") is not None else 0
        trend_info = _trend_features(r)
        r.update({key: value for key, value in trend_info.items() if key != "trend_score"})
        trend = float(trend_info["trend_score"])
        factors = {
            "value": value, "quality": 0.0, "growth": 0.0, "trend": trend,
            "strength": strength, "liquidity": liquidity, "stability": stability,
        }
        r["factors"] = {k: round(v, 1) for k, v in factors.items()}
        r["score"] = round(sum(factors[k] * legacy_weights[k] for k in legacy_weights), 1)
    matched.sort(key=lambda r: (-r["score"], r["code"]))

    use_financial = bool(filters.get("use_financial_quality", False))
    financial_coverage: dict[str, Any] = {"requested": 0, "returned": 0}
    financial_field_valid = {"gross_margin": 0, "revenue_yoy": 0, "net_profit_yoy": 0}
    financial_warning = ""
    if use_financial and matched:
        candidate_pool = matched[:_FINANCIAL_POOL_LIMIT]
        try:
            financials, financial_coverage = _load_ffd_financials(
                [r["code"] for r in candidate_pool], as_of[:10]
            )
            for row in candidate_pool:
                row.update(financials.get(row["code"], {}))
            financial_field_valid = {
                field: sum(row.get(field) is not None for row in candidate_pool)
                for field in financial_field_valid
            }
        except Exception:
            financial_warning = "FFD 历史时点财务暂不可用，本次保持行情因子排序"
            financial_coverage = {"requested": len(candidate_pool), "returned": 0, "error": "temporarily_unavailable"}

    financial_filter_keys = (
        ("gross_margin", "min_gross_margin"),
        ("revenue_yoy", "min_revenue_yoy"),
        ("net_profit_yoy", "min_net_profit_yoy"),
    )
    if any(filters.get(key) is not None for _, key in financial_filter_keys):
        if not financial_coverage.get("returned"):
            raise RuntimeError("已请求财务过滤，但 FFD 历史时点财务没有返回可用记录")
        labels = {"gross_margin": "毛利率", "revenue_yoy": "营收同比", "net_profit_yoy": "净利润同比"}
        unavailable = [
            labels[field] for field, key in financial_filter_keys
            if filters.get(key) is not None and financial_field_valid[field] == 0
        ]
        if unavailable:
            raise RuntimeError("已请求财务过滤，但本报告期以下字段覆盖为0：" + "、".join(unavailable))
        matched = [
            row for row in matched
            if all(
                filters.get(key) is None
                or (row.get(field) is not None and row[field] >= float(filters[key]))
                for field, key in financial_filter_keys
            )
        ]

    financial_effective = bool(use_financial and financial_coverage.get("returned"))
    weights = dict(PROFILES[profile]["financial_weights"] if financial_effective else legacy_weights)
    disabled_financial_factors: list[str] = []
    if financial_effective and financial_field_valid["gross_margin"] == 0:
        weights.pop("quality", None)
        disabled_financial_factors.append("质量（毛利率无覆盖）")
    if financial_effective and financial_field_valid["revenue_yoy"] + financial_field_valid["net_profit_yoy"] == 0:
        weights.pop("growth", None)
        disabled_financial_factors.append("增长（营收/净利润同比无覆盖）")
    weight_total = sum(weights.values())
    if weight_total > 0:
        weights = {key: value / weight_total for key, value in weights.items()}
    if financial_effective:
        gross_pool = [r["gross_margin"] for r in matched if r.get("gross_margin") is not None]
        revenue_pool = [r["revenue_yoy"] for r in matched if r.get("revenue_yoy") is not None]
        profit_pool = [r["net_profit_yoy"] for r in matched if r.get("net_profit_yoy") is not None]
        for row in matched:
            quality = _percentile(gross_pool, row["gross_margin"]) if row.get("gross_margin") is not None else 0.0
            growth_parts = []
            if row.get("revenue_yoy") is not None:
                growth_parts.append(_percentile(revenue_pool, row["revenue_yoy"]))
            if row.get("net_profit_yoy") is not None:
                growth_parts.append(_percentile(profit_pool, row["net_profit_yoy"]))
            growth = sum(growth_parts) / len(growth_parts) if growth_parts else 0.0
            row["factors"]["quality"] = round(quality, 1)
            row["factors"]["growth"] = round(growth, 1)
            row["score"] = round(sum(float(row["factors"].get(k, 0)) * weight for k, weight in weights.items()), 1)
        matched.sort(key=lambda r: (-r["score"], r["code"]))

    limit = max(1, min(int(filters.get("limit") or 100), 300))
    coverage = {
        key: {"valid": sum(r.get(key) is not None for r in rows), "total": len(rows), "rate": round(sum(r.get(key) is not None for r in rows) / len(rows) * 100, 1) if rows else 0}
        for key in ("price", "pe_ttm", "pb", "market_cap_yi", "ma60", "ma120", "ma250", "volume_ratio")
    }
    financial_denominator = int(financial_coverage.get("requested") or 0)
    if financial_denominator:
        for key in ("gross_margin", "revenue_yoy", "net_profit_yoy"):
            valid = financial_field_valid[key]
            coverage[key] = {
                "valid": valid, "total": financial_denominator,
                "rate": round(valid / financial_denominator * 100, 1),
            }
    warnings = ["缺失字段不补零，相关因子记为0并保留原始空值", "短期强度不是中长期动量，不构成买卖建议"]
    if financial_effective:
        warnings.append(f"财务质量仅对行情预筛前{financial_denominator}只执行单批PIT补全，报告期为{financial_coverage.get('report_period')}，不是全市场财务穷举")
    if disabled_financial_factors:
        warnings.append("本次自动剔除无覆盖因子：" + "、".join(disabled_financial_factors))
    if financial_warning:
        warnings.append(financial_warning)
    return {
        "source": source, "as_of": as_of, "scanned": len(rows), "matched": len(matched),
        "profile": profile, "profile_label": PROFILES[profile]["label"], "weights": weights,
        "results": matched[:limit],
        "coverage": coverage, "financial_coverage": financial_coverage,
        "factor_note": "趋势=价格相对MA60的位置、多头排列、MA60/120与MA120/250斜率，并惩罚过度偏离；价值=正PE/PB低分位；质量=毛利率；增长=营收与归母净利润同比；短期强度仅低权重参考当日涨跌幅与量比；流动性=成交额；稳定性=低振幅。分数仅用于候选排序，不代表预期收益。",
        "warnings": warnings,
    }


def _curve_metrics(curve: list[dict], key: str) -> dict:
    values = [float(x[key]) for x in curve if x.get(key) is not None]
    if len(values) < 2:
        return {}
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]
    total = values[-1] / values[0] - 1
    years = max(len(returns) / 252, 1 / 252)
    ann = (1 + total) ** (1 / years) - 1 if total > -1 else -1
    avg = sum(returns) / len(returns) if returns else 0
    variance = sum((r - avg) ** 2 for r in returns) / max(len(returns) - 1, 1)
    vol = math.sqrt(variance) * math.sqrt(252)
    sharpe = (ann - 0.02) / vol if vol > 0 else None
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = min(max_dd, value / peak - 1)
    return {
        "total_return": round(total * 100, 2), "annualized_return": round(ann * 100, 2),
        "annualized_volatility": round(vol * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown": round(max_dd * 100, 2),
        "positive_day_ratio": round(sum(r > 0 for r in returns) / len(returns) * 100, 2) if returns else None,
    }


def _cached_security_name(code: str) -> str:
    """只读现有选股快照取名称，绝不为展示名称触发一次全市场外部请求。"""
    for row in _CACHE.get("rows") or []:
        if row.get("code") == code:
            return str(row.get("name") or code)
    if _DISK_CACHE.exists():
        try:
            for row in json.loads(_DISK_CACHE.read_text(encoding="utf-8")).get("rows") or []:
                if row.get("code") == code:
                    return str(row.get("name") or code)
        except Exception:
            pass
    return code


def _load_ffd_history(codes: list[str], lookback: int) -> tuple[dict[str, dict[str, float]], dict]:
    """一次请求读取多标的前复权历史，绝不按股票拆单重试已披露缺口。"""
    from ffd import data as ffd_data

    api_key = get_setting("ffd_api_key", "") or os.environ.get("FFD_API_KEY", "")
    ffd_data.login(api_key=api_key or None)
    end = dt.date.fromisoformat(dp._safe_end())
    start = end - dt.timedelta(days=max(int(lookback * 2.0), 260))
    payload = ffd_data.query(
        function="history", codes=",".join(codes), indicators="close",
        start_date=start.isoformat(), end_date=end.isoformat(), adjust="前复权",
        coverage_policy="complete_rows", output_mode="raw", as_df=False,
    )
    series: dict[str, dict[str, float]] = {code: {} for code in codes}
    for row in _records(payload):
        raw_code = str(_first(row, "code", "ts_code", "证券代码") or "")
        try:
            code = dp.norm_code(raw_code)
        except Exception:
            continue
        date = str(_first(row, "time", "date", "trade_date", "交易日期") or "")[:10]
        close = _num(_first(row, "close", "收盘价"))
        if code in series and date and close and close > 0:
            series[code][date] = close
    raw_coverage = payload.get("coverage") if isinstance(payload, dict) else {}
    allowed = (
        "status", "coverage_policy", "partial", "complete", "requested_codes",
        "accepted_codes", "excluded_codes", "requested_fields", "returned_row_count",
        "complete_row_count", "missing_cell_count", "missing_cells", "retryable",
    )
    coverage = {key: raw_coverage.get(key) for key in allowed if isinstance(raw_coverage, dict) and key in raw_coverage}
    return series, coverage


def backtest_basket(codes: list[str], lookback: int = 252) -> dict:
    """对当前候选做等权买入持有历史体检。

    这不是历史时点重建的选股策略回测：候选集合来自当前截面，存在明显事后选择偏差。
    因此接口用 diagnosis 标签并强制返回醒目警告。
    """
    normalized = []
    for raw in codes:
        code = dp.norm_code(raw)
        if code not in normalized:
            normalized.append(code)
    normalized = normalized[:20]
    if not normalized:
        raise ValueError("codes 不能为空")

    series: dict[str, dict[str, float]] = {}
    sources: dict[str, str] = {}
    skipped: list[dict] = []
    per_stock: list[dict] = []
    ffd_coverage: dict = {}
    benchmark_series: dict[str, float] = {}
    try:
        batch, ffd_coverage = _load_ffd_history(normalized + ["000300.SH"], lookback)
        benchmark_series = batch.pop("000300.SH", {})
        for code in normalized:
            clean = batch.get(code, {})
            if len(clean) < 80:
                skipped.append({"code": code, "reason": f"FFD 完整行情不足80个交易日（{len(clean)}），不对已披露缺口拆单重试"})
                continue
            series[code] = clean
            sources[code] = "FFD·历史行情（前复权）"
    except Exception as exc:
        # 本机已配置 FFD 时失败关闭：不因一次批次错误悄悄扇出逐股请求。
        if get_setting("ffd_api_key", "") or os.environ.get("FFD_API_KEY", ""):
            raise RuntimeError(f"FFD 多标的历史行情暂不可用：{exc}") from exc
        # 未配置 FFD 才退回项目既有行情；逐股串行，且拒绝模拟数据。
        for code in normalized:
            bars, source = dp.get_price_bars(code, count=lookback)
            if source.startswith("demo"):
                skipped.append({"code": code, "reason": "仅取得模拟行情，已从历史体检排除"})
                continue
            clean = {b["date"]: float(b["close"]) for b in bars if b.get("close") and float(b["close"]) > 0}
            if len(clean) < 80:
                skipped.append({"code": code, "reason": f"真实行情不足80个交易日（{len(clean)}）"})
                continue
            series[code] = clean
            sources[code] = source
    if not series:
        raise ValueError("所选股票没有足够的真实历史行情；模拟行情不会用于体检")

    common_dates = sorted(set.intersection(*(set(s.keys()) for s in series.values())))
    if len(common_dates) < 80:
        raise ValueError(f"所选股票共同交易日不足80天（{len(common_dates)}）")
    common_dates = common_dates[-lookback:]
    base = {code: values[common_dates[0]] for code, values in series.items()}
    curve = []
    for date in common_dates:
        levels = [series[code][date] / base[code] * 100 for code in series]
        curve.append({"date": date, "basket": round(sum(levels) / len(levels), 4)})
    for code, values in series.items():
        ret = values[common_dates[-1]] / values[common_dates[0]] - 1
        per_stock.append({"code": code, "name": _cached_security_name(code), "return_pct": round(ret * 100, 2), "source": sources[code]})
    per_stock.sort(key=lambda x: x["return_pct"], reverse=True)

    bench_source = "FFD·历史行情（前复权）" if benchmark_series else None
    try:
        if not benchmark_series:
            benchmark, bench_source = dp.get_price_bars("000300.SH", count=min(lookback + 30, 500))
            benchmark_series = {b["date"]: float(b["close"]) for b in benchmark if b.get("close") and not bench_source.startswith("demo")}
        valid_dates = [d for d in common_dates if d in benchmark_series]
        if len(valid_dates) >= 80:
            b0 = benchmark_series[valid_dates[0]]
            by_date = {d: round(benchmark_series[d] / b0 * 100, 4) for d in valid_dates}
            for point in curve:
                point["benchmark"] = by_date.get(point["date"])
    except Exception:
        bench_source = None

    coverage = len(series) / len(normalized) * 100
    return {
        "kind": "ex_post_diagnosis", "codes": list(series), "requested": normalized,
        "start_date": common_dates[0], "end_date": common_dates[-1], "trading_days": len(common_dates),
        "coverage_rate": round(coverage, 1), "curve": curve,
        "metrics": _curve_metrics(curve, "basket"),
        "benchmark_metrics": _curve_metrics(curve, "benchmark") if any(x.get("benchmark") is not None for x in curve) else None,
        "per_stock": per_stock, "skipped": skipped,
        "sources": sorted(set(sources.values())), "benchmark_source": bench_source,
        "coverage": ffd_coverage,
        "warning": "这是用当前筛选结果回看历史的等权买入持有体检，存在事后选择偏差，不是样本外选股策略回测，也未计交易费用、涨跌停和调仓冲击。",
    }
