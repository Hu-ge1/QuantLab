"""QMT 桥接协议路由 — 与原 qmt-strategy-studio 完全兼容的 push/pull 协议。

桥接策略（QMT 内运行）用 X-Bridge-Token 头鉴权：
  POST /api/bridge/push     heartbeat / signal / result
  GET  /api/bridge/pull     领取指令 + 自选列表
看板路由（QuantLab 前端）：
  GET  /api/bridge/state    全量状态（在线/账户/持仓/委托/成交/信号/情绪历史/时段）
  GET  /api/bridge/ticks/{code}    分时累积
  GET  /api/bridge/history/{cid}   指令执行结果查询
  GET  /api/bridge/reports         收盘日报
  POST /api/bridge/command            下发指令（order 走软风控链）
  POST /api/bridge/watchlist          自选列表
  POST /api/bridge/install            安装/重装桥接策略到 QMT（注入 URL+token）
"""
from __future__ import annotations

import qmt_bridge
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

router = APIRouter()


class CommandBody(BaseModel):
    action: str
    code: str | None = None
    side: str | None = None
    prType: str | None = None
    price: float | None = None
    volume: int | None = None
    period: str | None = None
    count: int | None = None


class WatchlistBody(BaseModel):
    codes: list[str]


def _require_token(token_header: str | None) -> None:
    if not qmt_bridge.check_bridge_token(token_header):
        raise HTTPException(403, "桥接token不匹配，请重新安装桥接策略")


@router.post("/push")
def bridge_push(payload: dict, x_bridge_token: str | None = Header(default=None)):
    _require_token(x_bridge_token)
    return qmt_bridge.bridge_push(payload)


@router.get("/pull")
def bridge_pull(x_bridge_token: str | None = Header(default=None)):
    _require_token(x_bridge_token)
    return qmt_bridge.bridge_pull()


@router.get("/state")
def bridge_state():
    with qmt_bridge.BRIDGE_LOCK:
        state = dict(qmt_bridge.BRIDGE_STATE.get("state", {}))
        results = qmt_bridge.BRIDGE_RESULTS[:20]
        signals = qmt_bridge.BRIDGE_SIGNALS[:50]
        market_history = qmt_bridge.MARKET_HISTORY[-120:]
        asset_history = qmt_bridge.ASSET_HISTORY[-240:]
    return {
        "online": qmt_bridge.bridge_online(),
        "last_seen": qmt_bridge.BRIDGE_STATE.get("last_seen", ""),
        "state": state,
        "results": results,
        "signals": signals,
        "market_history": market_history,
        "asset_history": asset_history,
        "session": qmt_bridge.trading_phase(),
    }


@router.get("/ticks/{code}")
def bridge_ticks(code: str):
    code = code.upper()
    with qmt_bridge.BRIDGE_LOCK:
        seq = qmt_bridge.TICK_HISTORY.get(code, [])[:]
    return {"code": code, "points": seq}


@router.get("/history/{cid}")
def bridge_command_history(cid: str):
    with qmt_bridge.BRIDGE_LOCK:
        for r in qmt_bridge.BRIDGE_RESULTS:
            if r.get("command_id") == cid:
                return {"done": True, **r}
    return {"done": False}


@router.get("/reports")
def bridge_reports():
    with qmt_bridge.BRIDGE_LOCK:
        reports = list(qmt_bridge.DAILY_REPORTS)
    return {"reports": sorted(reports, key=lambda r: r.get("date", ""), reverse=True)}


@router.post("/command")
def bridge_command(body: CommandBody):
    data, code = qmt_bridge.issue_command(body.model_dump())
    return JSONResponse(status_code=code, content=data)


@router.get("/watchlist")
def watchlist_get():
    return {"codes": qmt_bridge.load_watchlist()}


@router.post("/watchlist")
def bridge_watchlist(body: WatchlistBody):
    return {"ok": True, "codes": qmt_bridge.save_watchlist(body.codes)}


@router.post("/install")
def bridge_install():
    return qmt_bridge.install_bridge()


@router.get("/export/signals.csv")
def export_signals():
    with qmt_bridge.BRIDGE_LOCK:
        rows = list(qmt_bridge.BRIDGE_SIGNALS)
    data = qmt_bridge.csv_bytes(
        ["时间", "策略", "代码", "动作", "价格", "说明"],
        [[s.get("time", ""), s.get("strategy", ""), s.get("code", ""),
          s.get("action", ""), s.get("price", ""), s.get("note", "")] for s in rows],
    )
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=signals.csv"})


@router.get("/export/deals.csv")
def export_deals():
    with qmt_bridge.BRIDGE_LOCK:
        deals = list((qmt_bridge.BRIDGE_STATE.get("state", {}) or {}).get("deals", []))
    data = qmt_bridge.csv_bytes(
        ["时间", "代码", "成交价", "成交量", "成交编号"],
        [[d.get("time", ""), d.get("code", ""), d.get("price", ""),
          d.get("volume", ""), d.get("deal_id", "")] for d in deals],
    )
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=deals.csv"})
