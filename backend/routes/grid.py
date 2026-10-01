"""网格交易路由：任务 CRUD、状态控制和人工 tick 模拟。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

import data_provider as dp
import qmt_bridge
from database import get_db
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from grid_engine import GridConfig, GridEngine

router = APIRouter()

GRID_MODES = {"percent", "fixed", "trailing", "atr", "autot", "rolling", "td9"}


class GridCreate(BaseModel):
    symbol: str = "512880.SH"
    name: str = ""
    base_price: float = Field(0.0, ge=0)
    step_pct: float = Field(0.015, gt=0, le=0.2)
    per_grid_amount: float = Field(5000.0, ge=0)
    upper_limit: float = Field(0.0, ge=0)
    lower_limit: float = Field(0.0, ge=0)
    grid_levels: int = Field(10, ge=2, le=200)
    spacing_type: Literal["arithmetic", "geometric"] = "geometric"
    max_position_amount: float = Field(50000.0, ge=0)
    mode: str = "percent"
    config: dict = Field(default_factory=dict)


class GridUpdate(BaseModel):
    name: str | None = None
    step_pct: float | None = Field(None, gt=0, le=0.2)
    per_grid_amount: float | None = Field(None, gt=0)
    upper_limit: float | None = Field(None, ge=0)
    lower_limit: float | None = Field(None, ge=0)
    grid_levels: int | None = Field(None, ge=2, le=200)
    spacing_type: Literal["arithmetic", "geometric"] | None = None
    max_position_amount: float | None = Field(None, gt=0)
    mode: str | None = None
    config: dict | None = None


class TickBody(BaseModel):
    price: float = Field(gt=0)


def _decode_row(row) -> dict:
    data = dict(row)
    try:
        data["state"] = json.loads(data.get("state_json") or "{}")
    except json.JSONDecodeError:
        data["state"] = {}
    data.pop("state_json", None)
    try:
        data["config"] = json.loads(data.get("config_json") or "{}")
    except json.JSONDecodeError:
        data["config"] = {}
    data.pop("config_json", None)
    data["submitted_to_qmt"] = bool(data.get("submitted_to_qmt"))
    return data


def td9_setup(closes: list[float]) -> dict:
    """TD Sequential setup：收盘价连续高/低于 4 根前，计数到 9 后重新开始。"""
    up = down = 0
    series = []
    for index, close in enumerate(closes):
        if index < 4:
            series.append({"up": 0, "down": 0})
            continue
        reference = closes[index - 4]
        if close > reference:
            up, down = min(up + 1, 9), 0
        elif close < reference:
            down, up = min(down + 1, 9), 0
        else:
            up = down = 0
        series.append({"up": up, "down": down})
        if up == 9:
            up = 0
        if down == 9:
            down = 0
    latest = series[-1] if series else {"up": 0, "down": 0}
    signal = "sell" if latest["up"] == 9 else "buy" if latest["down"] == 9 else "wait"
    return {"signal": signal, "up_count": latest["up"], "down_count": latest["down"], "series": series}


def _get_row(conn, job_id: int):
    row = conn.execute("SELECT * FROM grid_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "网格任务不存在")
    return row


def _latest_price(symbol: str) -> float:
    bars, source = dp.get_price_bars(symbol, count=5)
    if source.startswith("demo"):
        raise HTTPException(503, f"{symbol} 真实行情不可用，拒绝用模拟价格启动网格")
    if not bars:
        raise HTTPException(400, f"无法取得 {symbol} 最新价格，请手工填写基准价")
    price = float(bars[-1].get("close") or 0)
    if price <= 0:
        raise HTTPException(400, f"{symbol} 最新价格无效，请手工填写基准价")
    return price


@router.get("")
def list_grids():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM grid_jobs ORDER BY updated_at DESC").fetchall()
        return [_decode_row(row) for row in rows]
    finally:
        conn.close()


@router.post("")
def create_grid(body: GridCreate):
    symbol = dp.norm_code(body.symbol)
    if body.mode not in GRID_MODES:
        raise HTTPException(400, "不支持的网格模式")
    if body.upper_limit and body.lower_limit and body.upper_limit <= body.lower_limit:
        raise HTTPException(400, "上限必须大于下限")
    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO grid_jobs (symbol, name, base_price, step_pct, per_grid_amount,
                                   upper_limit, lower_limit, grid_levels, spacing_type,
                                   max_position_amount, mode, config_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle')
            """,
            (symbol, body.name or f"{symbol} 网格", body.base_price, body.step_pct,
             body.per_grid_amount, body.upper_limit, body.lower_limit, body.grid_levels,
             body.spacing_type, body.max_position_amount, body.mode,
             json.dumps(body.config, ensure_ascii=False)),
        )
        conn.commit()
        return _decode_row(_get_row(conn, int(cur.lastrowid)))
    finally:
        conn.close()


@router.get("/{job_id}")
def get_grid(job_id: int):
    conn = get_db()
    try:
        return _decode_row(_get_row(conn, job_id))
    finally:
        conn.close()


@router.put("/{job_id}")
def update_grid(job_id: int, body: GridUpdate):
    conn = get_db()
    try:
        row = _get_row(conn, job_id)
        if row["status"] == "running":
            raise HTTPException(409, "运行中的任务不能修改，请先暂停或停止")
        fields = body.model_dump(exclude_none=True)
        if fields.get("mode") and fields["mode"] not in GRID_MODES:
            raise HTTPException(400, "不支持的网格模式")
        if "config" in fields:
            fields["config_json"] = json.dumps(fields.pop("config"), ensure_ascii=False)
        if fields:
            set_clause = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(
                f"UPDATE grid_jobs SET {set_clause}, state_json = '{{}}', updated_at = ? WHERE id = ?",
                [*fields.values(), datetime.now().isoformat(), job_id],
            )
            conn.commit()
        return _decode_row(_get_row(conn, job_id))
    finally:
        conn.close()


@router.delete("/{job_id}")
def delete_grid(job_id: int):
    conn = get_db()
    try:
        _get_row(conn, job_id)
        conn.execute("DELETE FROM grid_fills WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM grid_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/{job_id}/start")
def start_grid(job_id: int):
    conn = get_db()
    try:
        row = _get_row(conn, job_id)
        if row["status"] == "running":
            return _decode_row(row)
        state = json.loads(row["state_json"] or "{}")
        if state.get("engine") and row["status"] == "paused":
            engine = GridEngine.from_dict(state["engine"])
            engine.config.status = "running"
        else:
            base = float(row["base_price"] or 0) or _latest_price(row["symbol"])
            band = max(float(row["step_pct"]) * max(int(row["grid_levels"]) / 2, 2), 0.02)
            lower = float(row["lower_limit"] or 0) or base * (1 - band)
            upper = float(row["upper_limit"] or 0) or base * (1 + band)
            if not (0 < lower < base < upper):
                raise HTTPException(400, "需满足：下限 < 基准价 < 上限")
            engine = GridEngine(GridConfig(
                symbol=row["symbol"], base_price=base, step_pct=row["step_pct"],
                per_grid_amount=row["per_grid_amount"], upper_limit=upper,
                lower_limit=lower, grid_levels=row["grid_levels"],
                spacing_type=row["spacing_type"],
                max_position_amount=float(row["max_position_amount"] or 0) or 1e15,
                status="running",
            ))
        state = {"engine": engine.to_dict()}
        conn.execute(
            """UPDATE grid_jobs SET status = 'running', base_price = ?, upper_limit = ?,
               lower_limit = ?, current_price = ?, state_json = ?, updated_at = ? WHERE id = ?""",
            (engine.config.base_price, engine.config.upper_limit, engine.config.lower_limit,
             engine.current_price, json.dumps(state, ensure_ascii=False),
             datetime.now().isoformat(), job_id),
        )
        conn.commit()
        return _decode_row(_get_row(conn, job_id))
    finally:
        conn.close()


def _set_status(job_id: int, status: Literal["paused", "stopped"]):
    conn = get_db()
    try:
        row = _get_row(conn, job_id)
        state = json.loads(row["state_json"] or "{}")
        if state.get("engine"):
            engine = GridEngine.from_dict(state["engine"])
            engine.config.status = status
            state["engine"] = engine.to_dict()
        conn.execute(
            "UPDATE grid_jobs SET status = ?, state_json = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(state, ensure_ascii=False), datetime.now().isoformat(), job_id),
        )
        conn.commit()
        return _decode_row(_get_row(conn, job_id))
    finally:
        conn.close()


@router.post("/{job_id}/pause")
def pause_grid(job_id: int):
    return _set_status(job_id, "paused")


@router.post("/{job_id}/stop")
def stop_grid(job_id: int):
    return _set_status(job_id, "stopped")


@router.post("/{job_id}/tick")
def tick_grid(job_id: int, body: TickBody):
    conn = get_db()
    try:
        row = _get_row(conn, job_id)
        if row["status"] != "running":
            raise HTTPException(409, "网格任务未运行")
        state = json.loads(row["state_json"] or "{}")
        engine = GridEngine.from_dict(state.get("engine") or {})
        fills = engine.update_price(body.price)
        for fill in fills:
            conn.execute(
                """INSERT INTO grid_fills (job_id, side, price, qty, fee, pnl, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (job_id, fill["side"], fill["price"], fill["qty"], fill.get("pnl", 0), fill["time"]),
            )
        state["engine"] = engine.to_dict()
        conn.execute(
            """UPDATE grid_jobs SET state_json = ?, current_price = ?, position_qty = ?,
               position_cost = ?, total_pnl = ?, updated_at = ? WHERE id = ?""",
            (json.dumps(state, ensure_ascii=False), body.price, engine.position_qty,
             engine.position_cost, engine.total_pnl, datetime.now().isoformat(), job_id),
        )
        conn.commit()
        return {"fills": fills, "snapshot": engine.snapshot()}
    finally:
        conn.close()


@router.get("/{job_id}/fills")
def list_fills(job_id: int, limit: int = 100):
    conn = get_db()
    try:
        _get_row(conn, job_id)
        rows = conn.execute(
            "SELECT * FROM grid_fills WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, min(max(limit, 1), 1000)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@router.get("/preview/td9/{symbol}")
def preview_td9(symbol: str):
    code = dp.norm_code(symbol)
    bars, source = dp.get_price_bars(code, count=80)
    closes = [float(bar.get("close") or 0) for bar in bars if float(bar.get("close") or 0) > 0]
    if len(closes) < 5:
        raise HTTPException(400, f"{code} 行情不足，无法计算九转")
    result = td9_setup(closes)
    result.pop("series", None)
    return {"symbol": code, "source": source, "last_close": closes[-1], **result}


@router.post("/{job_id}/submit-qmt")
def submit_qmt(job_id: int):
    """登记自动执行规则；不改变总闸、允许下单或全自动开关。"""
    conn = get_db()
    try:
        row = _get_row(conn, job_id)
        config = json.loads(row["config_json"] or "{}")
        volume = int(config.get("per_grid_shares") or 0)
        if volume <= 0 or volume % 100:
            raise HTTPException(400, "提交 QMT 前，每格股数必须是 100 的正整数倍")
        cfg = qmt_bridge.studio_cfg()
        strategy = f"grid:{job_id}:{row['mode']}"
        rules = [rule for rule in (cfg.get("auto_rules") or []) if rule.get("strategy") != strategy]
        rules.append({"strategy": strategy, "code": row["symbol"], "side": "both", "volume": volume})
        cfg["auto_rules"] = rules[:20]
        qmt_bridge.save_studio_cfg(cfg)
        qmt_bridge.audit("grid_rule_submit", {"job_id": job_id, "strategy": strategy, "code": row["symbol"], "volume": volume})
        conn.execute(
            "UPDATE grid_jobs SET submitted_to_qmt = 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), job_id),
        )
        conn.commit()
        return {
            "ok": True,
            "rule": rules[-1],
            "execution_enabled": bool(cfg.get("trading_enabled", True) and cfg.get("allow_order") and cfg.get("auto_trading")),
            "message": "规则已提交；是否执行仍由 QMT 交易总闸与全自动开关控制",
        }
    finally:
        conn.close()
