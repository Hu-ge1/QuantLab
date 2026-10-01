"""持仓路由 — CRUD + 一键刷新最新价 + 汇总。"""
from __future__ import annotations

import datetime as dt

import data_provider as dp
from database import get_db
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter()


class PositionCreate(BaseModel):
    code: str
    name: str = ""
    cost: float = Field(ge=0)
    shares: float = Field(gt=0)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        try:
            return dp.norm_code(value)
        except dp.DataProviderError as exc:
            raise ValueError(str(exc)) from exc


class PositionUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    cost: float | None = Field(None, ge=0)
    shares: float | None = Field(None, gt=0)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return dp.norm_code(value)
        except dp.DataProviderError as exc:
            raise ValueError(str(exc)) from exc


def _row_to_dict(r, with_value: bool = True) -> dict:
    d = dict(r)
    if with_value:
        cost = d.get("cost") or 0
        shares = d.get("shares") or 0
        d["cost_value"] = round(cost * shares, 2)
        cur = d.get("cur_price")
        d["cur_value"] = round(cur * shares, 2) if cur is not None else None
        d["pnl"] = round((cur - cost) * shares, 2) if cur is not None else None
        d["pnl_pct"] = round((cur / cost - 1) * 100, 2) if (cur is not None and cost) else None
    return d


@router.get("")
def list_positions():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM positions ORDER BY id").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.post("")
def add_position(p: PositionCreate):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO positions (code, name, cost, shares) VALUES (?, ?, ?, ?)",
            (p.code, p.name, p.cost, p.shares),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    return {"ok": True, "id": pid}


@router.put("/{pos_id}")
def update_position(pos_id: int, p: PositionUpdate):
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "没有要更新的字段")
    if "code" in fields:
        fields["code"] = dp.norm_code(fields["code"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    try:
        conn.execute(
            f"UPDATE positions SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*fields.values(), pos_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/{pos_id}")
def delete_position(pos_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/refresh-prices")
def refresh_prices():
    """遍历持仓刷新最新价：QMT 实时 tick（xtdata）优先，akshare 收盘价兜底。"""
    import qmt_client

    conn = get_db()
    try:
        rows = conn.execute("SELECT id, code FROM positions ORDER BY id").fetchall()
        codes = [r["code"] for r in rows]
        ticks = qmt_client.realtime_quotes(codes) if codes else None
        source = "xtdata·QMT实时" if ticks else "akshare/数据源兜底"
        updated, errors = 0, []
        for r in rows:
            code = r["code"]
            try:
                price = None
                if ticks and code in ticks:
                    price = ticks[code].get("price") or None
                if price is None:
                    price = dp.get_latest_close(code, allow_demo=False)
                if price is None:
                    raise ValueError("未取到价格")
                conn.execute(
                    "UPDATE positions SET cur_price = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (price, r["id"]),
                )
                updated += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{code}: {str(e)[:60]}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "updated": updated, "errors": errors, "source": source}


@router.get("/summary")
def portfolio_summary():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM positions").fetchall()
    finally:
        conn.close()
    total_cost = total_value = 0.0
    priced_positions = 0
    for r in rows:
        cost = (r["cost"] or 0) * (r["shares"] or 0)
        total_cost += cost
        cur = r["cur_price"]
        if cur is not None:
            priced_positions += 1
            total_value += cur * (r["shares"] or 0)
        else:
            total_value += cost
    pnl = total_value - total_cost
    return {
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / total_cost * 100, 2) if total_cost else 0.0,
        "has_live_prices": bool(rows) and priced_positions == len(rows),
        "priced_position_count": priced_positions,
        "position_count": len(rows),
        "as_of": dt.date.today().isoformat(),
    }
