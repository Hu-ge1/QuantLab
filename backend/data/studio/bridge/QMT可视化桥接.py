#coding:gbk
"""QMT 可视化桥接策略

在 QMT 客户端中运行本策略(实盘模式选择你的资金账号),它会:
  1. 每 2 秒把 账户资金/持仓/自选行情快照 推送给 QuantLab(http://127.0.0.1:8000)
  2. 接收网页下发的指令:下单(仅当工具端开启允许下单)、拉取K线数据
配合本地工具的「QMT 可视化」页面使用。
"""

import json
import time
import urllib.request

BRIDGE_URL = "http://127.0.0.1:8000"
POLL_SECONDS = 2
BRIDGE_TOKEN = ""         # 由本地工具安装时自动注入
ACCOUNT_ID = ""           # QMT 未暴露界面所选账号时，由本地工具注入
EMOTION_STATS = True      # 每分钟统计全市场涨停/跌停/涨跌家数(情绪面板数据源)
EMOTION_INTERVAL_MIN = 1  # 情绪统计间隔(分钟)
RADAR = True              # 盯盘雷达:自选股 触板/开板/急拉/跳水 推送到信号流
RADAR_PCT = 1.5           # 急拉/跳水阈值(约30秒窗口内的涨跌幅%)
RADAR_COOLDOWN = 180      # 同一股票同一类提醒的冷却(秒)


def init(C):
    C.bridge_watch = []
    C.bridge_market = None
    C.bridge_hist = {}     # code -> [(time, price)...] 雷达用滚动窗口
    C.bridge_alert = {}    # (code, kind) -> 上次提醒时间/状态
    C.bridge_err = 0
    C.bridge_last_poll = 0
    if ACCOUNT_ID:
        try:
            C.set_account(ACCOUNT_ID, "STOCK")
        except Exception as exc:
            print("[桥接] 账号绑定提示: %s" % exc)
    try:
        C.set_universe(C.bridge_watch)
    except Exception:
        pass
    # 本版 QMT 传空起始时间只会初始化一次；使用明确的历史起点，
    # 才会持续触发秒级任务（收盘后也能保持桥接心跳）。
    C.run_time("bridge_loop", "%dnSecond" % POLL_SECONDS, "2019-01-01 09:00:00")
    if EMOTION_STATS:
        C.run_time("bridge_market", "%dnMinute" % max(1, EMOTION_INTERVAL_MIN), "2019-01-01 09:00:00")
    print("[桥接] 启动,上报地址 %s,模式 %s" % (BRIDGE_URL, "回测" if getattr(C, "do_back_test", False) else "实盘"))
    # 初始化后立即握手，不必等待第一个定时周期。
    bridge_loop(C)


def handlebar(C):
    """QMT 标准模型入口；在定时器不可用时作为心跳兜底。"""
    try:
        if hasattr(C, "is_last_bar") and not C.is_last_bar():
            return
    except Exception:
        pass
    now = time.time()
    if now - getattr(C, "bridge_last_poll", 0) >= POLL_SECONDS:
        bridge_loop(C)


def _limit_ratio(code):
    """按代码前缀估算涨跌幅限制(北交所30%、创业板/科创板20%、其余10%)。"""
    c = str(code)
    if c.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if c.startswith(("83", "87", "88", "43", "92")):
        return 0.30
    return 0.10


def _radar_step(C, code, tick, now):
    """单只股票的雷达判定,依赖 C.bridge_hist / C.bridge_alert。"""
    last = tick.get("lastPrice")
    pre = tick.get("lastClose")
    if not last or not pre or last != last or pre <= 0:
        return
    seq = C.bridge_hist.setdefault(code, [])
    seq.append((now, last))
    if len(seq) > 40:
        del seq[:len(seq) - 40]

    ratio = _limit_ratio(code)
    limit = round(pre * (1 + ratio), 2)
    eps = 0.011
    at_limit = last >= limit - eps
    was_limit = bool(C.bridge_alert.get((code, "_at_limit")))
    if at_limit != was_limit:
        if at_limit:
            _alert(C, code, "触板", "价格 %.2f 触及涨停价 %.2f" % (last, limit))
        else:
            _alert(C, code, "开板", "涨停打开,现价 %.2f(涨停价 %.2f)" % (last, limit))
        C.bridge_alert[(code, "_at_limit")] = at_limit

    # 急拉/跳水:与窗口内较早的采样比较(约30秒窗口)
    if seq:
        ref = seq[0]
        if now - ref[0] >= 25:
            pct = (last / ref[1] - 1) * 100 if ref[1] else 0
            if pct >= RADAR_PCT:
                _alert(C, code, "急拉", "%d秒内上涨 %.2f%%,现价 %.2f" % (int(now - ref[0]), pct, last))
            elif pct <= -RADAR_PCT:
                _alert(C, code, "跳水", "%d秒内下跌 %.2f%%,现价 %.2f" % (int(now - ref[0]), pct, last))


def _alert(C, code, kind, note):
    key = (code, kind)
    if time.time() - C.bridge_alert.get(key, 0) < RADAR_COOLDOWN:
        return
    C.bridge_alert[key] = time.time()
    if not RADAR:
        return
    _push({"type": "signal", "strategy": "盯盘雷达", "code": code,
           "action": kind, "price": None, "note": note})
    print("[雷达] %s %s %s" % (code, kind, note))


def bridge_market(C):
    """每分钟统计一次市场情绪:主板涨停/跌停/涨跌家数 + 上证涨幅。"""
    if not EMOTION_STATS:
        return
    try:
        codes = C.get_stock_list_in_sector("沪深A股")
        mainboard = [c for c in codes if str(c)[:3] in
                     ("600", "601", "603", "605", "000", "001", "002", "003")]
        tick = C.get_full_tick(mainboard)
        limit_up = limit_down = up = down = 0
        for code, t in (tick or {}).items():
            last = t.get("lastPrice")
            pre = t.get("lastClose")
            if not last or not pre or last != last or pre <= 0:
                continue
            if last > pre:
                up += 1
            elif last < pre:
                down += 1
            limit_price = round(pre * 1.1, 2)
            if last >= limit_price - 0.01:
                limit_up += 1
            limit_price_d = round(pre * 0.9, 2)
            if last <= limit_price_d + 0.01:
                limit_down += 1
        index_pct = None
        try:
            idx = C.get_full_tick(["000001.SH"]).get("000001.SH")
            if idx and idx.get("lastPrice") and idx.get("lastClose"):
                index_pct = round((idx["lastPrice"] / idx["lastClose"] - 1) * 100, 2)
        except Exception:
            pass
        C.bridge_market = {"limit_up": limit_up, "limit_down": limit_down,
                           "up_count": up, "down_count": down,
                           "index_pct": index_pct}
        print("[桥接] 情绪统计: 涨停%d 跌停%d 上涨%d 下跌%d 上证%.2f%%" %
              (limit_up, limit_down, up, down, index_pct if index_pct is not None else 0))
    except Exception as exc:
        print("[桥接] 情绪统计失败:", exc)


def bridge_loop(C):
    C.bridge_last_poll = time.time()
    cmds, watch = _pull()
    if watch is not None and watch != C.bridge_watch:
        C.bridge_watch = watch
        try:
            C.set_universe(watch)
        except Exception:
            pass
    payload = _collect(C, watch)
    _push(payload)
    if RADAR and watch:
        now = time.time()
        for code, t in (payload.get("ticks") or {}).items():
            _radar_step(C, code, t, now)
    for cmd in cmds:
        _push(_exec_cmd(C, cmd))


def _http(url, payload=None, timeout=5):
    headers = {"Content-Type": "application/json"}
    if BRIDGE_TOKEN:
        headers["X-Bridge-Token"] = BRIDGE_TOKEN
    if payload is None:
        req = urllib.request.Request(url, headers=headers)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode("utf-8")
    resp.close()
    return json.loads(body)


def _pull():
    try:
        data = _http(BRIDGE_URL + "/api/bridge/pull")
        return data.get("commands", []), data.get("watchlist", [])
    except Exception as exc:
        print("[桥接] 拉取失败: %s" % exc)
        return [], None


def _push(payload):
    try:
        _http(BRIDGE_URL + "/api/bridge/push", payload)
        return True
    except Exception as exc:
        print("[桥接] 推送失败: %s" % exc)
        return False


def _f(v, default=0.0):
    try:
        v = float(v)
        return None if v != v else v
    except Exception:
        return default


def _account_id(C):
    """兼容不同 QMT 版本的账号属性；配置值作为可靠兜底。"""
    for obj in (C, getattr(C, "context", None)):
        if obj is None:
            continue
        for name in ("accountID", "accountid", "account_id"):
            try:
                value = str(getattr(obj, name, "") or "").strip()
                if value:
                    return value
            except Exception:
                pass
    return ACCOUNT_ID


def _collect(C, watch):
    mode = "backtest" if getattr(C, "do_back_test", False) else "live"
    accid = _account_id(C)
    account = {"accountID": accid, "mode": mode}
    positions = []
    orders = []
    deals = []
    if accid:
        try:
            for a in get_trade_detail_data(accid, "stock", "ACCOUNT"):
                account["balance"] = _f(getattr(a, "m_dBalance", 0))
                account["available"] = _f(getattr(a, "m_dAvailable", 0))
                account["market_value"] = _f(getattr(a, "m_dInstrumentValue", 0))
                account["profit"] = _f(getattr(a, "m_dPositionProfit", 0))
                break
        except Exception as exc:
            account["error"] = str(exc)
        try:
            for p in get_trade_detail_data(accid, "stock", "POSITION"):
                code = str(p.m_strInstrumentID) + "." + str(p.m_strMarket)
                item = {
                    "code": code,
                    "name": "",
                    "volume": int(getattr(p, "m_nVolume", 0)),
                    "canUse": int(getattr(p, "m_nCanUseVolume", 0)),
                    "cost": _f(getattr(p, "m_dOpenPrice", 0)),
                    "market_value": _f(getattr(p, "m_dInstrumentValue", 0)),
                    "profit": _f(getattr(p, "m_dPositionProfit", 0)),
                }
                try:
                    item["name"] = C.get_stock_name(code)
                except Exception:
                    pass
                positions.append(item)
        except Exception as exc:
            account["position_error"] = str(exc)
        # 当日委托
        try:
            for o in get_trade_detail_data(accid, "stock", "ORDER"):
                orders.append({
                    "code": str(getattr(o, "m_strInstrumentID", "")) + "." + str(getattr(o, "m_strMarket", "")),
                    "price": _f(getattr(o, "m_dPrice", 0)),
                    "volume": int(getattr(o, "m_nOrderVolume", getattr(o, "m_nVolume", 0)) or 0),
                    "traded": int(getattr(o, "m_nVolumeTraded", getattr(o, "m_nTradedVolume", 0)) or 0),
                    "status": int(getattr(o, "m_nOrderStatus", 0) or 0),
                    "time": str(getattr(o, "m_strOrderTime", getattr(o, "m_nOrderTime", ""))),
                    "id": str(getattr(o, "m_strOrderID", getattr(o, "m_nOrderID", ""))),
                })
            orders.reverse()
            del orders[50:]
        except Exception:
            pass
        # 当日成交
        try:
            for d in get_trade_detail_data(accid, "stock", "DEAL"):
                deals.append({
                    "code": str(getattr(d, "m_strInstrumentID", "")) + "." + str(getattr(d, "m_strMarket", "")),
                    "price": _f(getattr(d, "m_dPrice", 0)),
                    "volume": int(getattr(d, "m_nVolume", 0) or 0),
                    "time": str(getattr(d, "m_strDealTime", getattr(d, "m_strTime", ""))),
                    "deal_id": str(getattr(d, "m_strDealID", "")),
                })
            deals.reverse()
            del deals[50:]
        except Exception:
            pass
    ticks = {}
    if watch:
        try:
            raw = C.get_full_tick(watch)
            for code, t in (raw or {}).items():
                ticks[code] = {
                    "lastPrice": _f(t.get("lastPrice")),
                    "lastClose": _f(t.get("lastClose")),
                    "open": _f(t.get("open")),
                    "high": _f(t.get("high")),
                    "low": _f(t.get("low")),
                    "volume": _f(t.get("volume")),
                    "amount": _f(t.get("amount")),
                }
        except Exception as exc:
            print("[桥接] 行情获取失败:", exc)
    return {"type": "heartbeat", "mode": mode, "account": account,
            "positions": positions, "ticks": ticks, "orders": orders, "deals": deals,
            "market": getattr(C, "bridge_market", None)}


def _exec_cmd(C, cmd):
    result = {"type": "result", "command_id": cmd.get("id", ""), "action": cmd.get("action", "")}
    try:
        if cmd.get("action") == "order":
            accid = _account_id(C)
            if not accid:
                raise Exception("策略未运行在实盘模式(无资金账号)")
            side = cmd.get("side", "buy")
            op = 23 if side == "buy" else 24
            pr = int(cmd.get("prType", 14))
            price = _f(cmd.get("price", -1), -1.0)
            vol = int(cmd.get("volume", 0))
            if vol <= 0:
                raise Exception("股数必须大于0")
            passorder(op, 1101, accid, cmd.get("code"), pr, price, vol, C)
            result["ok"] = True
            result["detail"] = "已提交委托: %s %s %d股" % (cmd.get("code"), "买入" if side == "buy" else "卖出", vol)
        elif cmd.get("action") == "history":
            code = cmd.get("code", "")
            period = cmd.get("period", "1d")
            count = int(cmd.get("count", 120))
            data = C.get_market_data_ex(["open", "high", "low", "close", "volume", "amount"],
                                        [code], period=period, count=count, dividend_type="front")
            df = data.get(code) if data else None
            rows = []
            if df is not None and len(df) > 0:
                idx = [str(x) for x in df.index.tolist()]
                cols = {c: df[c].tolist() for c in ("open", "high", "low", "close", "volume", "amount")}
                for i in range(len(df)):
                    rows.append({
                        "time": idx[i],
                        "open": _f(cols["open"][i]),
                        "high": _f(cols["high"][i]),
                        "low": _f(cols["low"][i]),
                        "close": _f(cols["close"][i]),
                        "volume": _f(cols["volume"][i]),
                        "amount": _f(cols["amount"][i]),
                    })
            result["ok"] = True
            result["data"] = {"code": code, "period": period, "rows": rows}
            result["detail"] = "K线 %d 根" % len(rows)
        else:
            raise Exception("未知指令: %s" % cmd.get("action"))
    except Exception as exc:
        result["ok"] = False
        result["detail"] = str(exc)
    return result
