"""可持久化的 A 股网格模拟引擎。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal


@dataclass
class GridConfig:
    symbol: str = "512880.SH"
    base_price: float = 0.0
    step_pct: float = 0.015
    per_grid_amount: float = 5000.0
    upper_limit: float = 0.0
    lower_limit: float = 0.0
    grid_levels: int = 10
    spacing_type: Literal["arithmetic", "geometric"] = "geometric"
    max_position_amount: float = 50000.0
    status: Literal["idle", "running", "paused", "stopped"] = "idle"


@dataclass
class GridLevel:
    buy_price: float
    sell_price: float
    side: Literal["buy", "sell"] = "buy"
    qty: float = 0.0


class GridEngine:
    def __init__(self, config: GridConfig):
        self.config = config
        self.levels: list[GridLevel] = []
        self.current_price = float(config.base_price)
        self.last_price = float(config.base_price)
        self.position_qty = 0.0
        self.position_cost = 0.0
        self.total_pnl = 0.0
        self.fills: list[dict] = []
        self.logs: list[dict] = []
        self._build_levels()

    def _build_levels(self) -> None:
        cfg = self.config
        if not (cfg.lower_limit > 0 and cfg.upper_limit > cfg.lower_limit):
            raise ValueError("网格上下限无效")
        if cfg.grid_levels < 2:
            raise ValueError("网格层数至少为 2")
        if cfg.spacing_type == "arithmetic":
            step = (cfg.upper_limit - cfg.lower_limit) / cfg.grid_levels
            boundaries = [cfg.lower_limit + i * step for i in range(cfg.grid_levels + 1)]
        else:
            ratio = (cfg.upper_limit / cfg.lower_limit) ** (1.0 / cfg.grid_levels)
            boundaries = [cfg.lower_limit * ratio**i for i in range(cfg.grid_levels + 1)]
        self.levels = [
            GridLevel(round(boundaries[i], 6), round(boundaries[i + 1], 6))
            for i in range(cfg.grid_levels)
        ]

    def _record_fill(self, side: str, price: float, qty: float, pnl: float = 0.0) -> dict:
        fill = {
            "time": datetime.now().isoformat(), "side": side,
            "price": round(price, 6), "qty": qty, "pnl": round(pnl, 6),
        }
        self.fills.append(fill)
        self.logs.append({**fill, "event": side})
        return fill

    def update_price(self, price: float) -> list[dict]:
        price = float(price)
        if price <= 0:
            raise ValueError("价格必须大于 0")
        if self.config.status != "running":
            raise ValueError("网格任务未运行")
        previous = self.last_price or self.current_price or price
        self.current_price = price
        fills: list[dict] = []

        if price < previous:
            for level in sorted(self.levels, key=lambda item: item.buy_price, reverse=True):
                if level.side != "buy" or not (price <= level.buy_price < previous):
                    continue
                qty = int(self.config.per_grid_amount / level.buy_price / 100) * 100
                required = qty * level.buy_price
                if qty <= 0:
                    self.logs.append({"time": datetime.now().isoformat(), "event": "reject", "reason": "每格资金不足一手", "price": level.buy_price})
                    continue
                if self.position_cost + required > self.config.max_position_amount + 1e-9:
                    self.logs.append({"time": datetime.now().isoformat(), "event": "reject", "reason": "达到最大持仓金额", "price": level.buy_price})
                    continue
                level.side = "sell"
                level.qty = qty
                self.position_qty += qty
                self.position_cost += required
                fills.append(self._record_fill("buy", level.buy_price, qty))
        elif price > previous:
            for level in sorted(self.levels, key=lambda item: item.sell_price):
                if level.side != "sell" or level.qty <= 0 or not (previous < level.sell_price <= price):
                    continue
                qty = min(level.qty, self.position_qty)
                average_cost = self.position_cost / self.position_qty if self.position_qty else 0.0
                pnl = (level.sell_price - average_cost) * qty
                self.position_qty -= qty
                self.position_cost = max(0.0, self.position_cost - average_cost * qty)
                if self.position_qty < 1e-9:
                    self.position_qty = 0.0
                    self.position_cost = 0.0
                self.total_pnl += pnl
                level.side = "buy"
                level.qty = 0.0
                fills.append(self._record_fill("sell", level.sell_price, qty, pnl))

        self.last_price = price
        return fills

    def snapshot(self) -> dict:
        return {
            "symbol": self.config.symbol, "status": self.config.status,
            "current_price": self.current_price, "upper_limit": self.config.upper_limit,
            "lower_limit": self.config.lower_limit, "step_pct": self.config.step_pct,
            "grid_levels": self.config.grid_levels, "spacing_type": self.config.spacing_type,
            "per_grid_amount": self.config.per_grid_amount, "position_qty": self.position_qty,
            "position_cost": self.position_cost,
            "avg_entry_price": self.position_cost / self.position_qty if self.position_qty else 0.0,
            "total_pnl": self.total_pnl, "levels": [asdict(level) for level in self.levels],
            "recent_fills": self.fills[-20:], "recent_logs": self.logs[-50:],
        }

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config), "levels": [asdict(level) for level in self.levels],
            "current_price": self.current_price, "last_price": self.last_price,
            "position_qty": self.position_qty, "position_cost": self.position_cost,
            "total_pnl": self.total_pnl, "fills": self.fills[-200:], "logs": self.logs[-200:],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridEngine":
        if not data or not data.get("config"):
            raise ValueError("网格尚未初始化，请先启动任务")
        engine = cls(GridConfig(**data["config"]))
        if data.get("levels"):
            engine.levels = [GridLevel(**item) for item in data["levels"]]
        engine.current_price = float(data.get("current_price", engine.config.base_price) or 0)
        engine.last_price = float(data.get("last_price", engine.current_price) or 0)
        engine.position_qty = float(data.get("position_qty", 0) or 0)
        engine.position_cost = float(data.get("position_cost", 0) or 0)
        engine.total_pnl = float(data.get("total_pnl", 0) or 0)
        engine.fills = list(data.get("fills") or [])
        engine.logs = list(data.get("logs") or [])
        return engine
