"""QMT 只读接入路由 — 实时行情 + 账户/持仓/委托查询 + 持仓导入。

只读设计：本路由没有任何下单/撤单接口，qmt_client 也只调用 xttrader 的
query_* 只读方法。交易能力留待后续以「人工确认队列」形式单独评审。
"""
from __future__ import annotations

import qmt_client
from database import get_db
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


@router.get("/status")
def status():
    """两通道状态总览（studio 桥接 5 秒缓存）。"""
    return qmt_client.status()


@router.post("/probe")
def probe():
    """强制刷新状态（设置页「测试连接」按钮）。"""
    return qmt_client.status(force=True)


@router.get("/account")
def account():
    """账户/持仓/委托（studio 桥接优先，MiniQMT 兜底，只读）。"""
    return qmt_client.account_overview()


class RealtimeRequest(BaseModel):
    codes: list[str]


@router.post("/realtime")
def realtime(req: RealtimeRequest):
    """实时报价（xtdata tick）；QMT 不在线时返回 available=false 走 akshare 兜底。"""
    ticks = qmt_client.realtime_quotes([c.upper() for c in req.codes])
    if ticks:
        return {"available": True, "source": "xtdata·QMT实时", "ticks": ticks}
    return {"available": False, "source": "", "ticks": {}}


class ImportPositionsRequest(BaseModel):
    only_new: bool = True


@router.post("/import-positions")
def import_positions(req: ImportPositionsRequest):
    """把 QMT 侧持仓导入本地持仓管理表（只写入本地数据库，不涉及任何交易）。"""
    overview = qmt_client.account_overview()
    if not overview.get("online"):
        return {"ok": False, "error": overview.get("error", "QMT 不在线"),
                "hint": overview.get("hint", "")}

    imported, skipped = 0, 0
    conn = get_db()
    try:
        existing = {r["code"] for r in conn.execute("SELECT code FROM positions").fetchall()}
        for p in overview.get("positions") or []:
            code = str(p.get("code", "")).upper()
            volume = float(p.get("volume", 0) or 0)
            if not code or volume <= 0:
                continue
            if req.only_new and code in existing:
                skipped += 1
                continue
            cost = float(p.get("cost", 0) or 0) or float(p.get("market_value", 0) or 0) / volume
            conn.execute(
                "INSERT INTO positions (code, name, cost, shares, cur_price) VALUES (?, ?, ?, ?, ?)",
                (code, str(p.get("name", "")), round(cost, 4), volume, None),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "imported": imported, "skipped": skipped,
            "source": overview.get("source")}
