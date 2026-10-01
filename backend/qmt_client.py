"""QMT 只读接入适配层 — 双通道自动合并（不包含任何下单代码）。

通道 A「内置桥接」（推荐，无需额外服务）：
  QuantLab 已合并原 qmt-strategy-studio 的桥接状态机（qmt_bridge.py）——
  QMT 里的桥接策略直接推送到本服务（:8000/api/bridge/push），这里进程内
  直读桥接状态，零网络开销。

通道 B「MiniQMT 直连」：
  QMT 客户端以极简模式运行时，通过自带 xtquant（xtdata 实时行情 +
  xttrader 只读查询接口）直连。仅调用 query_*，绝无下单。

合并策略：账户/持仓优先内置桥接，不可用时回退 MiniQMT；两者都不可用则
返回明确的状态与操作指引。
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import qmt_bridge

from database import get_setting

# QMT 安装根目录不写死在仓库里（避免泄漏券商名与本机目录结构）。
# 优先级：QuantLab 设置里的 qmt_install_dir > 环境变量 > 空（未配置）。
#   QUANTLAB_QMT_INSTALL_DIR    QMT 安装根目录，例如 D:\QMT
DEFAULT_QMT_INSTALL = os.environ.get("QUANTLAB_QMT_INSTALL_DIR", "").strip()

_EXEC_TIMEOUT = 15

_state = {
    "studio_checked_at": 0.0,
    "studio_cache": None,
}
_lock = threading.Lock()


def _run_blocking(fn, *args, timeout: float = _EXEC_TIMEOUT):
    import concurrent.futures

    fut = concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(fn, *args)
    return fut.result(timeout=timeout)


# ── 通道 A：内置桥接（qmt_bridge，进程内直读） ──────────────────────────────

def _studio_cfg() -> Optional[dict]:
    """兼容旧调用（chat.py LLM 兜底）：返回合并后的 studio 配置。"""
    return qmt_bridge.studio_cfg()


def studio_state(force: bool = False) -> dict:
    """通道 A：内置桥接状态（5 秒缓存，进程内直读零网络开销）。"""
    now = time.time()
    if not force and _state["studio_cache"] and now - _state["studio_checked_at"] < 5:
        return _state["studio_cache"]

    with qmt_bridge.BRIDGE_LOCK:
        result = {
            "available": True,
            "online": qmt_bridge.bridge_online(),
            "last_seen": qmt_bridge.BRIDGE_STATE.get("last_seen", ""),
            "state": dict(qmt_bridge.BRIDGE_STATE.get("state", {})),
            "session": qmt_bridge.trading_phase(),
            "market_history": qmt_bridge.MARKET_HISTORY[-120:],
            "error": "",
        }

    with _lock:
        _state["studio_checked_at"] = now
        _state["studio_cache"] = result
    return result


# ── 通道 B：MiniQMT 直连（xtquant，只读） ───────────────────────────────────

_xt = {"loaded": False, "error": "", "site": ""}


def _xtquant_site() -> Optional[str]:
    custom = get_setting("qmt_site_packages", "")
    if custom and Path(custom).exists():
        return custom
    install = str(get_setting("qmt_install_dir", DEFAULT_QMT_INSTALL) or "").strip()
    if not install:
        return None
    candidate = Path(install) / "bin.x64" / "lib" / "site-packages"
    if (candidate / "xtquant").exists():
        return str(candidate)
    return None


def _load_xtquant() -> bool:
    if _xt["loaded"]:
        return True
    site = _xtquant_site()
    if site is None:
        _xt["error"] = "未找到 xtquant（检查 QMT 安装目录设置）"
        return False
    try:
        if site not in sys.path:
            sys.path.insert(0, site)
        xt_dir = str(Path(site) / "xtquant")
        import os

        if os.path.isdir(xt_dir):
            os.add_dll_directory(xt_dir)
        from xtquant import xtdata  # noqa: F401 —— 验证可导入

        _xt["loaded"] = True
        _xt["site"] = site
        _xt["error"] = ""
        return True
    except Exception as e:  # noqa: BLE001
        _xt["error"] = f"{type(e).__name__}: {e}"[:200]
        return False


def xt_realtime(codes: list[str]) -> Optional[dict]:
    """通道 B 行情：xtdata.get_full_tick（QMT 客户端在线时可用）。"""
    if not _load_xtquant():
        return None
    try:
        from xtquant import xtdata

        def _tick():
            return xtdata.get_full_tick(codes)

        raw = _run_blocking(_tick, timeout=15)
        out = {}
        for code, tick in (raw or {}).items():
            if not tick:
                continue
            ts = int(tick.get("lastTime", 0) or 0)
            out[code] = {
                "price": float(tick.get("lastPrice", 0) or 0),
                "time": f"{ts // 10000:02d}:{ts % 10000 // 100:02d}:{ts % 100:02d}" if ts else "",
            }
        return out or None
    except Exception:  # noqa: BLE001
        return None


def xt_account(account_id: str) -> Optional[dict]:
    """通道 B 账户：xttrader 只读查询（MiniQMT 交易权限登录时可用）。"""
    if not _load_xtquant() or not account_id:
        return None
    install = str(get_setting("qmt_install_dir", DEFAULT_QMT_INSTALL) or "").strip()
    if not install:
        return None
    mini_path = Path(install) / "userdata_mini"
    if not mini_path.exists():
        return None
    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        def _query():
            trader = XtQuantTrader(mini_path, random.randint(10**7, 10**8 - 1))
            trader.start()
            try:
                if trader.connect() != 0:
                    raise ConnectionError("MiniQMT 连接失败（客户端需以极简模式登录）")
                acc = StockAccount(account_id)
                asset = trader.query_stock_asset(acc)
                positions = trader.query_stock_positions(acc) or []
                orders = trader.query_stock_orders(acc) or []
                return {
                    "account": {
                        "accountID": account_id,
                        "balance": float(getattr(asset, "balance", 0) or 0),
                        "available": float(getattr(asset, "cash", 0) or 0),
                        "market_value": float(getattr(asset, "market_value", 0) or 0),
                    },
                    "positions": [_norm_xt_position(p) for p in positions],
                    "orders": [
                        {
                            "code": getattr(o, "stock_code", ""),
                            "name": getattr(o, "stock_name", ""),
                            "side": "buy" if getattr(o, "order_type", 0) == 23 else "sell",
                            "price": float(getattr(o, "price", 0) or 0),
                            "volume": int(getattr(o, "volume", 0) or 0),
                            "status": str(getattr(o, "order_status", "")),
                        }
                        for o in orders[:50]
                    ],
                }
            finally:
                trader.stop()

        return _run_blocking(_query, timeout=20)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def _norm_xt_position(p) -> dict:
    return {
        "code": getattr(p, "stock_code", ""),
        "name": getattr(p, "stock_name", ""),
        "volume": int(getattr(p, "volume", 0) or 0),
        "can_use": int(getattr(p, "can_use_volume", 0) or 0),
        "cost": float(getattr(p, "open_price", 0) or 0),
        "market_value": float(getattr(p, "market_value", 0) or 0),
    }


# ── 合并后的统一视图 ─────────────────────────────────────────────────────────

def status(force: bool = False) -> dict:
    studio = studio_state(force=force)
    xt_loaded = _load_xtquant()
    return {
        "studio": {
            "available": studio["available"],
            "online": studio["online"],
            "error": studio["error"],
            "last_seen": studio.get("last_seen", ""),
            "trading_phase": (studio.get("session") or {}).get("label", ""),
            "url": "内置（同进程，QMT 桥接策略推送至 :8000）",
        },
        "xtquant": {
            "installed": xt_loaded,
            "site": _xt.get("site", ""),
            "error": _xt.get("error", ""),
        },
        "readonly": True,
    }


def account_overview(force: bool = False) -> dict:
    """账户/持仓/委托：内置桥接优先，MiniQMT 兜底，均不可用给出指引。"""
    studio = studio_state(force=force)
    if studio["available"] and studio["online"]:
        state = studio.get("state") or {}
        # 市场情绪快照（盯盘统计的涨跌家数/涨停跌停，取最新一点）
        market_history = studio.get("market_history") or []
        return {
            "source": "内置桥接（QMT 桥接策略实时推送）",
            "online": True,
            "last_seen": studio.get("last_seen", ""),
            "trading_phase": (studio.get("session") or {}),
            "account": state.get("account") or {},
            "positions": state.get("positions") or [],
            "orders": state.get("orders") or [],
            "deals": state.get("deals") or [],
            "ticks": state.get("ticks") or {},
            "market": market_history[-1] if market_history else None,
        }

    # 兜底：MiniQMT 直连（需要资金账号配置）
    account_id = str(get_setting("qmt_account_id", "") or "")
    if account_id:
        data = xt_account(account_id)
        if data and "error" not in data:
            return {"source": "MiniQMT直连（xtquant 只读查询）", "online": True,
                    "last_seen": "", "trading_phase": {}, **data}
        if data and "error" in data:
            return {"source": "", "online": False, "error": data["error"],
                    "hint": "确认 QMT 客户端已启动并以极简模式登录"}
        return {"source": "", "online": False,
                "error": "MiniQMT 未连接", "hint": "启动 QMT 客户端并登录后重试"}

    return {
        "source": "", "online": False,
        "error": studio.get("error") or "桥接未在线",
        "hint": "打开 QMT 客户端并运行「QMT可视化桥接」策略（或以极简模式登录并在设置中填写资金账号）",
    }


def realtime_quotes(codes: list[str]) -> Optional[dict]:
    """实时报价：xtdata tick 优先，不可用返回 None（上层回退 akshare）。"""
    if not codes:
        return None
    return xt_realtime(codes)
