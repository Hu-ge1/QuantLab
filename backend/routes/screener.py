"""批量量化选股 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

import screener

router = APIRouter()


class ScreenRequest(BaseModel):
    keyword: str = ""
    market: Literal["main"] = "main"
    profile: str = "balanced"
    exclude_st: bool = True
    above_ma60: bool = False
    strict_uptrend: bool = False
    use_financial_quality: bool = True
    min_market_cap: float | None = Field(None, ge=0)
    max_market_cap: float | None = Field(None, ge=0)
    min_pe: float | None = None
    max_pe: float | None = None
    max_pb: float | None = Field(None, gt=0)
    min_amount_yi: float | None = Field(None, ge=0)
    min_turnover: float | None = Field(None, ge=0)
    min_change: float | None = None
    max_change: float | None = None
    min_gross_margin: float | None = None
    min_revenue_yoy: float | None = None
    min_net_profit_yoy: float | None = None
    limit: int = Field(100, ge=1, le=300)
    force: bool = False


class BacktestRequest(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=20)
    lookback: int = Field(252, ge=120, le=500)


@router.get("/meta")
def meta():
    return {"profiles": screener.PROFILES, "markets": ["main"]}


@router.post("/run")
def run(payload: ScreenRequest):
    if payload.min_market_cap is not None and payload.max_market_cap is not None and payload.min_market_cap > payload.max_market_cap:
        raise HTTPException(422, "最小市值不能大于最大市值")
    if payload.min_pe is not None and payload.max_pe is not None and payload.min_pe > payload.max_pe:
        raise HTTPException(422, "最小 PE 不能大于最大 PE")
    try:
        return screener.screen(payload.model_dump(exclude={"force"}), force=payload.force)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"批量数据暂不可用：{exc}") from exc


@router.post("/backtest")
def backtest(payload: BacktestRequest):
    try:
        return screener.backtest_basket(payload.codes, payload.lookback)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"历史体检暂不可用：{exc}") from exc
