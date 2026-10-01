"""交易执行路由 — L1 手动下单 / L2 信号执行 / L3 全自动配置 + 总闸 + 审计。

安全边界（全部服务端强制）：
  - trading_enabled 总闸关闭 → 一切下单指令 403
  - allow_order 开关关闭 → 下单指令 403
  - auto_trading（L3）默认 False；当日亏损 ≥ circuit_loss_pct 自动停用
  - 每笔指令过完整风控链（时段/白名单/单笔/日次数），全程审计留痕
"""
from __future__ import annotations

import json
from typing import Literal

import qmt_bridge
from database import get_setting, set_setting
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

router = APIRouter()


class OrderBody(BaseModel):
    code: str
    side: Literal["buy", "sell"]
    prType: Literal["limit", "market"] = "limit"
    price: float = -1
    volume: int = Field(gt=0, multiple_of=100)

    @model_validator(mode="after")
    def validate_limit_price(self):
        if self.prType == "limit" and self.price <= 0:
            raise ValueError("限价单价格必须大于 0")
        return self


class SignalExecBody(BaseModel):
    code: str
    action: Literal["buy", "sell"]
    price: float | None = None
    volume: int = Field(gt=0, multiple_of=100)
    strategy: str = ""


class RuleBody(BaseModel):
    strategy: str = "*"
    code: str = "*"
    side: str = "both"  # both | buy | sell
    volume: int


class RulesBody(BaseModel):
    rules: list[RuleBody]


class ConfigBody(BaseModel):
    allow_order: bool | None = None
    auto_trading: bool | None = None
    circuit_loss_pct: float | None = None
    order_max_volume: int | None = None
    order_max_per_day: int | None = None
    order_allowlist: str | list | None = None


@router.get("/status")
def trading_status():
    cfg = qmt_bridge.studio_cfg()
    today = qmt_bridge.time.strftime("%Y%m%d")
    order_count = qmt_bridge.ORDER_COUNT.get("count", 0) if qmt_bridge.ORDER_COUNT.get("date") == today else 0
    return {
        "trading_enabled": bool(cfg.get("trading_enabled", True)),      # 总闸
        "allow_order": bool(cfg.get("allow_order")),                    # 下单开关
        "auto_trading": bool(cfg.get("auto_trading")),                  # L3 全自动
        "circuit_loss_pct": cfg.get("circuit_loss_pct", 2.0),
        "order_max_volume": cfg.get("order_max_volume"),
        "order_max_per_day": cfg.get("order_max_per_day"),
        "order_allowlist": cfg.get("order_allowlist") or [],
        "auto_rules": cfg.get("auto_rules") or [],
        "today_orders": order_count,
        "trading_phase": qmt_bridge.trading_phase(),
        "bridge_online": qmt_bridge.bridge_online(),
        "queued_commands": len(qmt_bridge.BRIDGE_COMMANDS),
        "inflight_commands": len(qmt_bridge.BRIDGE_INFLIGHT),
    }


@router.put("/config")
def trading_config(body: ConfigBody):
    cfg = qmt_bridge.studio_cfg()
    d = body.model_dump(exclude_none=True)
    if "allow_order" in d:
        cfg["allow_order"] = bool(d["allow_order"])
        qmt_bridge.audit("config", {"key": "allow_order", "value": bool(d["allow_order"])})
    if "auto_trading" in d:
        # 开启全自动必须先允许下单
        if d["auto_trading"] and not cfg.get("allow_order"):
            return {"ok": False, "error": "请先打开「允许下单」开关，再开启全自动交易"}
        cfg["auto_trading"] = bool(d["auto_trading"])
        qmt_bridge.audit("config", {"key": "auto_trading", "value": bool(d["auto_trading"])})
    if "circuit_loss_pct" in d:
        cfg["circuit_loss_pct"] = max(0.0, min(20.0, float(d["circuit_loss_pct"])))
    for k in ("order_max_volume", "order_max_per_day"):
        if k in d:
            try:
                cfg[k] = max(1, int(d[k]))
            except (TypeError, ValueError):
                pass
    if "order_allowlist" in d:
        raw = d["order_allowlist"]
        items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
        cfg["order_allowlist"] = [c.strip().upper() for c in items
                                  if __import__("re").match(r"^\d{6}\.(SH|SZ|BJ)$", str(c).strip().upper())]
    qmt_bridge.save_studio_cfg(cfg)
    return {"ok": True}


@router.post("/kill")
def kill_switch():
    """总闸：立即拦截一切下单指令（含全自动）。"""
    cfg = qmt_bridge.studio_cfg()
    cfg["trading_enabled"] = False
    cfg["auto_trading"] = False  # 总闸落下时连带停用全自动
    qmt_bridge.save_studio_cfg(cfg)
    qmt_bridge.audit("kill_switch", {"action": "交易总闸已关闭"})
    return {"ok": True, "trading_enabled": False, "auto_trading": False}


@router.post("/enable")
def enable_trading():
    """重新启用总闸（不自动恢复全自动，需单独开启）。"""
    cfg = qmt_bridge.studio_cfg()
    cfg["trading_enabled"] = True
    qmt_bridge.save_studio_cfg(cfg)
    qmt_bridge.audit("enable_switch", {"action": "交易总闸已启用"})
    return {"ok": True, "trading_enabled": True}


@router.post("/order")
def manual_order(body: OrderBody):
    """L1 手动下单（前端二次确认后调用；服务端风控链完整执行）。"""
    payload = body.model_dump()
    payload["action"] = "order"
    data, code = qmt_bridge.issue_command(payload, source="manual")
    return JSONResponse_(status_code=code, content=data)


@router.post("/signal-exec")
def signal_exec(body: SignalExecBody):
    """L2 信号半自动：把某条策略信号按人工指定的股数转成订单（人工已确认）。"""
    data, code = qmt_bridge.issue_command({
        "action": "order",
        "code": body.code,
        "side": body.action,
        "prType": "limit",
        "price": body.price if body.price and body.price > 0 else -1,
        "volume": body.volume,
    }, source="signal_confirm")
    qmt_bridge.audit("signal_confirm_exec", {
        "signal_strategy": body.strategy, "signal_code": body.code,
        "signal_action": body.action, "volume": body.volume,
        "accepted": code == 200, "result": data,
    })
    return JSONResponse_(status_code=code, content=data)


@router.put("/rules")
def set_rules(body: RulesBody):
    """L3 自动执行规则表（* 通配；命中第一条即执行）。"""
    cfg = qmt_bridge.studio_cfg()
    rules = []
    for r in body.rules:
        volume = int(r.volume or 0)
        if volume <= 0:
            continue
        rules.append({
            "strategy": (r.strategy or "*").strip() or "*",
            "code": (r.code or "*").strip().upper() or "*",
            "side": r.side if r.side in ("both", "buy", "sell") else "both",
            "volume": volume,
        })
    cfg["auto_rules"] = rules[:20]
    qmt_bridge.save_studio_cfg(cfg)
    qmt_bridge.audit("auto_rules_set", {"count": len(rules), "rules": rules})
    return {"ok": True, "rules": rules}


@router.get("/audit")
def trading_audit():
    return {"audit": qmt_bridge.TRADING_AUDIT[:100]}


class TestSignalBody(BaseModel):
    code: str = "600519.SH"
    action: str = "buy"  # buy | sell
    price: float | None = None
    strategy: str = "测试信号"
    volume: int = 100


@router.post("/test-signal")
def test_signal(body: TestSignalBody):
    """模拟盘验证工具：注入一条测试信号，走与真实信号完全相同的管道
    （信号流 → L2 可执行 / L3 自动执行规则匹配）。"""
    import data_provider as dp

    code = body.code.strip().upper()
    action = body.action.lower()
    if action not in ("buy", "sell"):
        return {"ok": False, "error": "action 只能是 buy/sell"}
    price = body.price
    if not price or price <= 0:
        try:
            price = dp.get_latest_close(code, allow_demo=False)
        except Exception:  # noqa: BLE001
            price = None
    payload = {
        "type": "signal",
        "strategy": (body.strategy or "测试信号")[:40],
        "code": code,
        "action": action,
        "price": round(float(price), 3) if price else None,
    }
    r = qmt_bridge.bridge_push(payload)
    # 提示当前是否会自动执行（方便验证 L3）
    cfg = qmt_bridge.studio_cfg()
    return {
        "ok": bool(r.get("ok")),
        "signal": payload,
        "auto_will_execute": bool(cfg.get("auto_trading") and cfg.get("allow_order")
                                  and cfg.get("trading_enabled", True)),
    }


def JSONResponse_(status_code: int, content: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=content)
