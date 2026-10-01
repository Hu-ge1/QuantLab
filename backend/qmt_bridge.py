"""QMT 桥接状态机 — 从 qmt-strategy-studio/server.py 移植合并。

职责（与原 studio 完全一致的协议与持久化）：
  - 桥接协议：heartbeat / signal / result 推送（token 鉴权）、指令 pull 队列
  - 软风控：allow_order 总开关、交易时段拦截、白名单、单笔股数、每日次数
  - 持久化：bridge_state.json / signals.json / daily_reports.json /
    ticks_history.json（每 30s 落盘、每日重置、上限 7200 点）/ order_count.json
  - 收盘日报：15:05 自动生成
  - QMT 模型注册表：indexUserConfig.xml 注册 + 后台看门狗补注册
  - 桥接策略安装：注入 BRIDGE_URL（指向本服务）与 BRIDGE_TOKEN

只读/风控边界：本模块只提供「指令队列」，指令是否包含下单由
allow_order 开关与风控链决定；默认关闭（studio 迁移值保留）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from database import get_setting, set_setting

BASE_DIR = Path(__file__).parent
STUDIO_DIR = BASE_DIR / "data" / "studio"
BRIDGE_STRATEGY_PATH = STUDIO_DIR / "bridge" / "QMT可视化桥接.py"
HISTORY_DIR = STUDIO_DIR / "history"
KNOWLEDGE_DIR = STUDIO_DIR / "knowledge"
TEMPLATES_PATH = STUDIO_DIR / "templates.json"
BRIDGE_STATE_PATH = STUDIO_DIR / "bridge_state.json"
SIGNALS_PATH = STUDIO_DIR / "signals.json"
REPORTS_PATH = STUDIO_DIR / "daily_reports.json"
TICKS_PATH = STUDIO_DIR / "ticks_history.json"
ORDER_COUNT_PATH = STUDIO_DIR / "order_count.json"
COMMAND_QUEUE_PATH = STUDIO_DIR / "command_queue.json"
RISK_STATE_PATH = STUDIO_DIR / "risk_state.json"
WATCHLIST_PATH = STUDIO_DIR / "watchlist.json"
BACKUP_DIR = STUDIO_DIR / "backups"
REGISTRY_PATH = STUDIO_DIR / "qmt_models.json"

# QMT 安装路径不写死在仓库里（避免泄漏券商名与本机目录结构）。
# 优先级：QuantLab 设置里的值 > 环境变量 > 空（未配置）。
#   QUANTLAB_QMT_PYTHON_DIR    QMT 策略目录，例如 D:\QMT\python
DEFAULT_QMT_PYTHON_DIR = os.environ.get("QUANTLAB_QMT_PYTHON_DIR", "").strip()

BRIDGE_LOCK = threading.Lock()
BRIDGE_COMMANDS: list[dict] = []
BRIDGE_INFLIGHT: dict[str, dict] = {}
BRIDGE_STATE: dict = {"last_seen": "", "state": {}}
BRIDGE_RESULTS: list[dict] = []
BRIDGE_SIGNALS: list[dict] = []
MARKET_HISTORY: list[dict] = []
ASSET_HISTORY: list[dict] = []   # 当日资产曲线 {t, total, avail, mv}，每心跳一点
TICK_HISTORY: dict = {}
TICKS_LAST_SAVE = 0.0
TICKS_DATE = ""
DAILY_REPORTS: list[dict] = []
ORDER_COUNT = {"date": "", "count": 0}

MODEL_NODE_FMT = ('<catalog scriptType="1" formulaCatalogModelType="4" '
                  'systemProvidedStrategy="0" strategymall="0" name="{name}" '
                  'type="2" simpleRun="0"/>')
NODE_INDENT = "\n            "


# ── studio 配置（存 QuantLab settings 的 studio_cfg 键，迁移自旧 config.json） ──

DEFAULT_STUDIO_CFG = {
    "temperature": 0.3,
    "qmt_python_dir": DEFAULT_QMT_PYTHON_DIR,
    "qmt_client_process": "XtItClient.exe",
    "qmt_account_id": "",
    "allow_order": False,
    "order_max_volume": 2000,
    "order_max_per_day": 20,
    "order_allowlist": [],
    "bridge_token": "",
    "emotion_stats": True,
    "emotion_interval_min": 1,
    "max_history": 200,
    # ── 交易执行层（分级）──
    "trading_enabled": True,   # 总闸（kill-switch 反向）：False 时拦截一切下单指令
    "auto_trading": False,     # L3 全自动：信号自动触发下单（默认关闭）
    "auto_rules": [],          # L3 规则表：[{strategy, code, side, volume}]，* 为通配
    "circuit_loss_pct": 2.0,   # 当日账户亏损 ≥ 该百分比 → 自动停用全自动（0 = 关闭熔断）
}

AUDIT_PATH = STUDIO_DIR / "trading_audit.json"
TRADING_AUDIT: list[dict] = []
DAY_ANCHOR = {"date": "", "balance": 0.0}


def qmt_strategy_dir(cfg: Optional[dict] = None) -> str:
    """解析 QMT 策略目录：设置值优先，其次环境变量，都没有则返回空串。

    不要直接对空字符串调 Path(...).is_dir()——Path("") 等价于当前目录，
    会被误判为有效，从而把策略文件写进 backend/ 或仓库根目录。
    """
    cfg = cfg if isinstance(cfg, dict) else studio_cfg()
    return str(cfg.get("qmt_python_dir") or DEFAULT_QMT_PYTHON_DIR or "").strip()


def studio_cfg() -> dict:
    cfg = dict(DEFAULT_STUDIO_CFG)
    stored = get_setting("studio_cfg", {})
    if isinstance(stored, dict):
        cfg.update(stored)
    if not cfg.get("bridge_token"):
        cfg["bridge_token"] = secrets.token_hex(12)
        save_studio_cfg(cfg)
    return cfg


def save_studio_cfg(cfg: dict) -> None:
    set_setting("studio_cfg", cfg)


def migrate_from_legacy() -> dict:
    """一次性迁移：导入旧 qmt-strategy-studio 的 config.json。

    源目录由环境变量 QUANTLAB_LEGACY_STUDIO_DIR 指定（不写死在仓库里），
    未设置时直接跳过。以 QuantLab settings 里尚无 studio_cfg 为条件，
    重复启动无副作用。返回迁移说明。
    """
    info = {"migrated": False, "bridge_token_kept": False, "notes": []}
    legacy_root = os.environ.get("QUANTLAB_LEGACY_STUDIO_DIR", "").strip()
    if not legacy_root:
        return info
    legacy_cfg_path = Path(legacy_root) / "config.json"
    if get_setting("studio_cfg") is None and legacy_cfg_path.exists():
        try:
            legacy = json.loads(legacy_cfg_path.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_STUDIO_CFG)
            for k in cfg.keys():
                if k in legacy and legacy[k] not in ("", None):
                    cfg[k] = legacy[k]
            for k in ("api_key", "base_url", "model", "access_password", "lan_access"):
                if k in legacy:
                    cfg[k] = legacy[k]
            if not cfg.get("bridge_token"):
                cfg["bridge_token"] = secrets.token_hex(12)
            else:
                info["bridge_token_kept"] = True
            save_studio_cfg(cfg)
            info["migrated"] = True
            info["notes"].append("已导入旧 studio 配置（含桥接 token，存量桥接策略仍可鉴权）")
        except Exception as e:  # noqa: BLE001
            info["notes"].append(f"旧配置读取失败: {e}")
    return info


def load_templates() -> dict:
    try:
        return json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"categories": [], "templates": []}


def trading_phase() -> dict:
    """A股交易时段判定（仅按自然时间，不含节假日）。"""
    now = time.localtime()
    if now.tm_wday >= 5:
        return {"trading": False, "label": "周末休市"}
    hm = time.strftime("%H:%M", now)
    if hm < "09:15":
        return {"trading": False, "label": "盘前"}
    if hm < "09:30":
        return {"trading": True, "label": "集合竞价"}
    if hm < "11:30":
        return {"trading": True, "label": "上午盘"}
    if hm < "13:00":
        return {"trading": False, "label": "午间休市"}
    if hm < "15:00":
        return {"trading": True, "label": "下午盘"}
    return {"trading": False, "label": "已收盘"}


def qmt_client_running() -> bool:
    cfg = studio_cfg()
    exe = cfg.get("qmt_client_process") or "XtItClient.exe"
    try:
        # Windows 的 tasklist 输出是本地编码（GBK），显式指定避免 UTF-8 环境下解码崩溃
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq " + exe, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="replace", timeout=10,
        ).stdout
        return exe.lower() in (out or "").lower()
    except Exception:  # noqa: BLE001
        return False


# ── QMT 模型注册表（indexUserConfig.xml） ───────────────────────────────────

def _registered_names(text: str) -> set[str]:
    names = set()
    for node in re.findall(r'<catalog [^>]*type="2"[^>]*/>', text):
        m = re.search(r'name="([^"]+)"', node)
        if m:
            names.add(m.group(1))
    return names


def _insert_model_node(text: str, model_name: str):
    esc_name = (model_name.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    node = MODEL_NODE_FMT.format(name=esc_name)
    m = re.search(r'<catalog [^>]*name="我的策略"[^>]*type="1"[^>]*>', text)
    if not m:
        return None, "XML 中未找到「我的策略」分类节点"
    depth, close_idx = 1, None
    for mt in re.finditer(r'<catalog\b[^>]*?(/?)>|</catalog>', text[m.end():]):
        tag = mt.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth == 0:
                close_idx = m.end() + mt.start()
                break
        elif mt.group(1) == "/":
            continue
        else:
            depth += 1
    if close_idx is None:
        return None, "XML 结构解析失败（未找到闭合标签）"
    return text[:close_idx] + NODE_INDENT + node + text[close_idx:], "ok"


def register_model(qmt_python_dir: str, model_name: str):
    """注册模型到 indexUserConfig.xml，返回 (registered, msg)。"""
    if not str(qmt_python_dir or "").strip():
        return False, "QMT 策略目录未配置"
    root = Path(qmt_python_dir.rstrip("\\/")).parent
    xml_path = root / "config" / "indexUserConfig.xml"
    if not xml_path.is_file():
        return False, f"未找到注册表: {xml_path}"
    text = xml_path.read_bytes().decode("utf-8", errors="replace")
    if model_name in _registered_names(text):
        return True, "already"
    new_text, msg = _insert_model_node(text, model_name)
    if new_text is None:
        return False, msg
    backup = xml_path.with_name(xml_path.name + ".bak_" + time.strftime("%Y%m%d%H%M%S"))
    try:
        shutil.copyfile(xml_path, backup)
        xml_path.write_bytes(new_text.encode("utf-8"))
    except PermissionError:
        return False, "无权限写入注册表（可能被 QMT 占用）"
    except Exception as e:  # noqa: BLE001
        return False, f"写入注册表失败: {e}"
    return True, "registered"


def _load_registry() -> list:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save_registry(items: list) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_registry() -> list:
    """把历史保存过的模型重新注册一遍（补偿被 QMT 客户端覆盖的注册表）。"""
    qdir = qmt_strategy_dir()
    if not qdir:
        return []
    results = []
    for item in _load_registry():
        name = item.get("name", "")
        if not name:
            continue
        py = Path(qdir) / (name + ".py")
        if not py.is_file():
            continue
        ok, msg = register_model(qdir, name)
        results.append((name, ok, msg))
    return results


def registry_watchdog() -> None:
    """后台每 60s 补偿注册：QMT 客户端退出时可能回写注册表覆盖我们的注册。"""
    while True:
        time.sleep(60)
        try:
            for name, ok, msg in sync_registry():
                if ok and msg == "registered":
                    print("[watchdog] 已自动补注册模型:", name)
        except Exception:  # noqa: BLE001
            pass


# ── 持久化 ──────────────────────────────────────────────────────────────────

def load_persisted() -> None:
    global TICKS_DATE, ORDER_COUNT
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if BRIDGE_STATE_PATH.is_file():
            data = json.loads(BRIDGE_STATE_PATH.read_text(encoding="utf-8"))
            BRIDGE_STATE["last_seen"] = data.get("last_seen", "")
            BRIDGE_STATE["state"] = data.get("state", {})
            BRIDGE_RESULTS.extend(data.get("results", [])[:50])
            MARKET_HISTORY.extend(data.get("market_history", [])[-240:])
            ASSET_HISTORY.extend(data.get("asset_history", [])[-7200:])
    except Exception:  # noqa: BLE001
        pass
    try:
        if SIGNALS_PATH.is_file():
            BRIDGE_SIGNALS.extend(json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))[:200])
    except Exception:  # noqa: BLE001
        pass
    try:
        if REPORTS_PATH.is_file():
            DAILY_REPORTS.extend(json.loads(REPORTS_PATH.read_text(encoding="utf-8"))[-60:])
    except Exception:  # noqa: BLE001
        pass
    try:
        if TICKS_PATH.is_file():
            data = json.loads(TICKS_PATH.read_text(encoding="utf-8"))
            if data.get("date") == time.strftime("%Y-%m-%d"):
                TICK_HISTORY.update(data.get("codes", {}))
                TICKS_DATE = data["date"]
    except Exception:  # noqa: BLE001
        pass
    try:
        if ORDER_COUNT_PATH.is_file():
            ORDER_COUNT = json.loads(ORDER_COUNT_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    try:
        if COMMAND_QUEUE_PATH.is_file():
            data = json.loads(COMMAND_QUEUE_PATH.read_text(encoding="utf-8"))
            BRIDGE_COMMANDS.extend(data.get("queued", []))
            BRIDGE_INFLIGHT.update({
                str(item.get("id")): item for item in data.get("inflight", []) if item.get("id")
            })
    except Exception:  # noqa: BLE001
        pass
    try:
        if RISK_STATE_PATH.is_file():
            data = json.loads(RISK_STATE_PATH.read_text(encoding="utf-8"))
            DAY_ANCHOR.update({"date": str(data.get("date", "")),
                               "balance": float(data.get("balance", 0) or 0)})
    except Exception:  # noqa: BLE001
        pass
    _load_audit()


def _persist_bridge_state() -> None:
    try:
        BRIDGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BRIDGE_STATE_PATH.write_text(json.dumps(
            {"last_seen": BRIDGE_STATE.get("last_seen", ""),
             "state": BRIDGE_STATE.get("state", {}),
             "results": BRIDGE_RESULTS[:50],
             "market_history": MARKET_HISTORY[-240:],
             "asset_history": ASSET_HISTORY[-7200:]}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _persist_signals() -> None:
    try:
        SIGNALS_PATH.write_text(json.dumps(BRIDGE_SIGNALS[:200], ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _persist_command_queue() -> None:
    """Persist queued and delivered-but-unacknowledged commands atomically enough for local use."""
    try:
        COMMAND_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = COMMAND_QUEUE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "queued": BRIDGE_COMMANDS,
            "inflight": list(BRIDGE_INFLIGHT.values()),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(COMMAND_QUEUE_PATH)
    except Exception:  # noqa: BLE001
        pass


def _persist_risk_state() -> None:
    try:
        RISK_STATE_PATH.write_text(json.dumps(DAY_ANCHOR, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _accumulate_ticks(ticks: Optional[dict]) -> None:
    global TICKS_LAST_SAVE, TICKS_DATE
    today = time.strftime("%Y-%m-%d")
    if TICKS_DATE != today:
        TICKS_DATE = today
        TICK_HISTORY.clear()
    now = time.strftime("%H:%M:%S")
    for code, t in (ticks or {}).items():
        p = t.get("lastPrice")
        if p is None or p != p:  # NaN 检查
            continue
        seq = TICK_HISTORY.setdefault(code, [])
        seq.append({"t": now, "p": round(float(p), 4)})
        if len(seq) > 7200:
            del seq[:len(seq) - 7200]
    if time.time() - TICKS_LAST_SAVE > 30:
        TICKS_LAST_SAVE = time.time()
        try:
            TICKS_PATH.write_text(json.dumps({"date": today, "codes": TICK_HISTORY}, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _maybe_daily_report() -> None:
    now = time.localtime()
    t = time.strftime("%H:%M", now)
    date = time.strftime("%Y-%m-%d", now)
    if now.tm_wday >= 5 or t < "15:05":
        return
    if any(r.get("date") == date for r in DAILY_REPORTS):
        return
    state = BRIDGE_STATE.get("state", {})
    if not state.get("account"):
        return
    report = {
        "date": date,
        "account": state.get("account", {}),
        "positions": state.get("positions", []),
        "market": state.get("market"),
        "signals": [s for s in BRIDGE_SIGNALS if s.get("time", "").startswith(date)][:100],
        "deals": state.get("deals", [])[:50],
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    DAILY_REPORTS.append(report)
    del DAILY_REPORTS[:-60]
    try:
        REPORTS_PATH.write_text(json.dumps(DAILY_REPORTS, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def bridge_online() -> bool:
    try:
        last = time.mktime(time.strptime(BRIDGE_STATE.get("last_seen", ""), "%Y-%m-%d %H:%M:%S"))
        return (time.time() - last) < 10
    except Exception:  # noqa: BLE001
        return False


def bridge_push(payload: dict) -> dict:
    """桥接策略推送入口：heartbeat / signal / result。"""
    ptype = str(payload.get("type", ""))
    if ptype == "heartbeat":
        with BRIDGE_LOCK:
            BRIDGE_STATE["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            BRIDGE_STATE["state"] = {
                "mode": payload.get("mode", ""),
                "account": payload.get("account", {}),
                "positions": payload.get("positions", []),
                "ticks": payload.get("ticks", {}),
                "market": payload.get("market"),
                "orders": payload.get("orders", []),
                "deals": payload.get("deals", []),
            }
            market = payload.get("market")
            if market and market.get("limit_up") is not None:
                sample = {"time": time.strftime("%H:%M"),
                          "limit_up": market.get("limit_up"),
                          "limit_down": market.get("limit_down"),
                          "up": market.get("up_count"),
                          "down": market.get("down_count"),
                          "index_pct": market.get("index_pct")}
                if not MARKET_HISTORY or MARKET_HISTORY[-1]["time"] != sample["time"]:
                    MARKET_HISTORY.append(sample)
                    if len(MARKET_HISTORY) > 240:
                        del MARKET_HISTORY[:len(MARKET_HISTORY) - 240]
            # 当日资产曲线：每心跳一点（上限 7200）
            acc = payload.get("account") or {}
            bal = acc.get("balance")
            if bal and float(bal) > 0:
                ASSET_HISTORY.append({
                    "t": time.strftime("%H:%M:%S"),
                    "total": round(float(bal), 2),
                    "avail": round(float(acc.get("available", 0) or 0), 2),
                    "mv": round(float(acc.get("market_value", 0) or 0), 2),
                })
                if len(ASSET_HISTORY) > 7200:
                    del ASSET_HISTORY[:len(ASSET_HISTORY) - 7200]
            _accumulate_ticks(payload.get("ticks"))
            _persist_bridge_state()
            _maybe_daily_report()
            if isinstance(payload.get("account"), dict):
                bal = payload["account"].get("balance")
                if bal and float(bal) > 0:
                    _circuit_check(float(bal))
        return {"ok": True}
    if ptype == "signal":
        sig = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": str(payload.get("strategy", ""))[:40],
            "code": str(payload.get("code", ""))[:16],
            "action": str(payload.get("action", ""))[:8],
            "price": payload.get("price"),
            "note": str(payload.get("note", ""))[:200],
        }
        with BRIDGE_LOCK:
            BRIDGE_SIGNALS.insert(0, sig)
            del BRIDGE_SIGNALS[200:]
            _persist_signals()
        # L3 全自动：在锁外执行（内部含风控链，可能再拿锁）
        try:
            _maybe_auto_execute(payload)
        except Exception as exc:  # noqa: BLE001
            audit("auto_error", {"error": str(exc)[:200]})
        return {"ok": True}
    if ptype == "result":
        with BRIDGE_LOCK:
            BRIDGE_RESULTS.insert(0, {
                "command_id": payload.get("command_id", ""),
                "action": payload.get("action", ""),
                "ok": bool(payload.get("ok")),
                "detail": str(payload.get("detail", ""))[:300],
                "data": payload.get("data"),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            del BRIDGE_RESULTS[50:]
            BRIDGE_INFLIGHT.pop(str(payload.get("command_id", "")), None)
            _persist_command_queue()
            _persist_bridge_state()
        return {"ok": True}
    return {"ok": False, "error": "unknown push type"}


def bridge_pull() -> dict:
    """Move queued commands to inflight; results explicitly acknowledge completion."""
    with BRIDGE_LOCK:
        cmds = BRIDGE_COMMANDS[:]
        del BRIDGE_COMMANDS[:]
        delivered_at = time.strftime("%Y-%m-%d %H:%M:%S")
        for cmd in cmds:
            item = dict(cmd)
            item["delivered_at"] = delivered_at
            BRIDGE_INFLIGHT[str(item["id"])] = item
        _persist_command_queue()
    return {"commands": cmds, "watchlist": load_watchlist()}


def load_watchlist() -> list:
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ["600000.SH", "000001.SZ", "510300.SH"]


def save_watchlist(items: list) -> list:
    cleaned = []
    for c in items:
        c = str(c).strip().upper()
        if re.match(r"^\d{6}\.(SH|SZ|BJ)$", c):
            cleaned.append(c)
    cleaned = cleaned[:50]
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(cleaned), encoding="utf-8")
    return cleaned


# ── 交易执行层：总闸 / 熔断 / 审计 / 信号自动执行 ───────────────────────────

def _load_audit() -> None:
    global TRADING_AUDIT
    try:
        if AUDIT_PATH.is_file():
            TRADING_AUDIT = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))[:500]
    except Exception:  # noqa: BLE001
        TRADING_AUDIT = []


def audit(event: str, detail: dict) -> None:
    """交易审计：每条指令的来源、参数、风控判定全程留痕。"""
    TRADING_AUDIT.insert(0, {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **detail,
    })
    del TRADING_AUDIT[500:]
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(TRADING_AUDIT[:500], ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _circuit_check(balance: float) -> None:
    """每日锚定首次心跳资产；当日亏损超阈值 → 自动停用全自动并审计。"""
    today = time.strftime("%Y-%m-%d")
    if DAY_ANCHOR["date"] != today:
        DAY_ANCHOR["date"], DAY_ANCHOR["balance"] = today, balance
        _persist_risk_state()
        return
    cfg = studio_cfg()
    threshold = float(cfg.get("circuit_loss_pct", 2.0) or 0)
    if threshold <= 0 or not cfg.get("auto_trading") or DAY_ANCHOR["balance"] <= 0:
        return
    loss_pct = (DAY_ANCHOR["balance"] - balance) / DAY_ANCHOR["balance"] * 100
    if loss_pct >= threshold:
        cfg["auto_trading"] = False
        save_studio_cfg(cfg)
        audit("circuit_break", {
            "reason": f"当日资产回撤 {loss_pct:.2f}% ≥ 阈值 {threshold}%，已自动停用全自动交易",
            "day_start_balance": DAY_ANCHOR["balance"], "balance": balance,
        })
        BRIDGE_SIGNALS.insert(0, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": "风控引擎", "code": "—", "action": "halt",
            "price": None,
            "note": f"熔断触发：当日回撤 {loss_pct:.2f}%，全自动交易已停用",
        })
        _persist_signals()


def _maybe_auto_execute(payload: dict) -> None:
    """L3 全自动：信号 → 匹配规则表 → 走同一风控链生成下单指令。"""
    cfg = studio_cfg()
    if not (cfg.get("auto_trading") and cfg.get("allow_order") and cfg.get("trading_enabled", True)):
        return
    action = str(payload.get("action", "")).lower()
    if action not in ("buy", "sell"):
        return
    code = str(payload.get("code", "")).upper()
    strategy = str(payload.get("strategy", ""))
    for rule in cfg.get("auto_rules") or []:
        s_pat, c_pat = str(rule.get("strategy", "*")), str(rule.get("code", "*"))
        side = str(rule.get("side", "both")).lower()
        if s_pat not in ("*", "") and s_pat not in strategy:
            continue
        if c_pat not in ("*", "") and c_pat.upper() != code:
            continue
        if side not in ("both", "", action):
            continue
        volume = int(rule.get("volume", 0) or 0)
        if volume <= 0:
            continue
        body = {"action": "order", "code": code, "side": action,
                "prType": "limit", "price": float(payload.get("price") or -1),
                "volume": volume}
        data, http_code = issue_command(body, source=f"auto:{strategy}")
        audit("auto_signal_exec", {
            "signal_strategy": strategy, "signal_code": code, "signal_action": action,
            "rule_volume": volume, "accepted": http_code == 200, "result": data,
        })
        return  # 命中第一条规则即止
    audit("auto_signal_skip", {"signal_strategy": strategy, "signal_code": code,
                               "signal_action": action, "reason": "无匹配规则"})


def issue_command(body: dict, source: str = "manual") -> tuple[dict, int]:
    """下发指令（order 带全链路软风控；history 为取 K 线数据指令）。

    风控链顺序（任一不过即拒绝，全部服务端强制）：
      总闸 trading_enabled → allow_order 开关 → 代码格式 → 整手校验 →
      交易时段 → 白名单 → 单笔股数 → 每日次数
    """
    action = str(body.get("action", ""))
    cfg = studio_cfg()
    cmd = {"id": "c_" + secrets.token_hex(12), "action": action}
    if action == "order":
        if not cfg.get("trading_enabled", True):
            audit("order_reject", {"source": source, "reason": "总闸关闭（kill-switch）"})
            return {"error": "交易总闸已关闭（kill-switch），请在「交易」页重新启用"}, 403
        if not cfg.get("allow_order"):
            audit("order_reject", {"source": source, "reason": "allow_order 未开启"})
            return {"error": "下单未开启：请先在「交易」页打开「允许下单」开关"}, 403
        code = str(body.get("code", "")).strip().upper()
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", code):
            return {"error": "代码格式应为 600000.SH 等"}, 400
        volume = int(body.get("volume", 0) or 0)
        if volume <= 0 or volume % 100 != 0:
            return {"error": "股数必须为 100 的整数倍"}, 400
        side = str(body.get("side", "")).lower()
        if side not in ("buy", "sell"):
            return {"error": "买卖方向只能是 buy 或 sell"}, 400
        price_type = str(body.get("prType", "limit")).lower()
        if price_type not in ("limit", "market"):
            return {"error": "订单类型只能是 limit 或 market"}, 400
        price = float(body.get("price", -1) or -1)
        if price_type == "limit" and price <= 0:
            return {"error": "限价单价格必须大于 0"}, 400
        phase = trading_phase()
        if not phase["trading"]:
            audit("order_reject", {"source": source, "code": code,
                                   "reason": f"非交易时段（{phase['label']}）"})
            return {"error": f"非交易时段（{phase['label']}），已拦截下单"}, 403
        allowlist = cfg.get("order_allowlist") or []
        if allowlist and code not in allowlist:
            audit("order_reject", {"source": source, "code": code, "reason": "不在白名单"})
            return {"error": "代码不在下单白名单: " + code}, 403
        if volume > int(cfg.get("order_max_volume", 2000) or 2000):
            audit("order_reject", {"source": source, "code": code,
                                   "reason": f"超单笔上限 {cfg.get('order_max_volume')}"})
            return {"error": f"单笔股数超过上限 {cfg.get('order_max_volume')}"}, 403
        today = time.strftime("%Y%m%d")
        cmd.update({
            "code": code,
            "side": side,
            "prType": 11 if price_type == "limit" else 14,
            "price": price,
            "volume": volume,
        })
    elif action == "history":
        cmd.update({
            "code": str(body.get("code", "")).strip(),
            "period": str(body.get("period", "1d")),
            "count": max(10, min(800, int(body.get("count", 120) or 120))),
        })
    else:
        return {"error": "未知指令类型"}, 400
    with BRIDGE_LOCK:
        if action == "order":
            today = time.strftime("%Y%m%d")
            if ORDER_COUNT.get("date") != today:
                ORDER_COUNT["date"], ORDER_COUNT["count"] = today, 0
            limit = int(cfg.get("order_max_per_day", 20) or 20)
            if ORDER_COUNT["count"] >= limit:
                audit("order_reject", {"source": source, "code": cmd["code"], "reason": "超每日次数"})
                return {"error": f"已达今日最大下单次数（{limit}），可在设置页调整"}, 403
            ORDER_COUNT["count"] += 1
            try:
                ORDER_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
                ORDER_COUNT_PATH.write_text(json.dumps(ORDER_COUNT), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        BRIDGE_COMMANDS.append(cmd)
        _persist_command_queue()
    if action == "order":
        audit("order_accepted", {
            "source": source, "id": cmd["id"], "code": cmd["code"], "side": cmd["side"],
            "price": cmd["price"], "volume": cmd["volume"],
            "prType": "限价" if cmd["prType"] == 11 else "市价",
        })
    return {"ok": True, "id": cmd["id"]}, 200


def check_bridge_token(token_header: Optional[str]) -> bool:
    token = studio_cfg().get("bridge_token") or ""
    if not token:
        return True
    return token_header == token


CODING_RE = re.compile(r"^#\s*coding[:=]\s*([\w-]+)", re.I)


def encode_strategy_for_qmt(code: str) -> tuple[bytes, str]:
    """把策略源码转成 QMT 能正确解析的字节。

    仓库里的策略模板一律存 UTF-8（便于 diff、阅读与语法检查），但 QMT 的
    策略解释器需要 GBK：源文件若没有编码声明，Python 3 会按 UTF-8 解码，
    GBK 中文会变成乱码甚至直接 SyntaxError。

    所以这里做两件事——尊重源码自带的 coding 声明；没有声明就补一条 GBK
    声明再转码。这样模板可以在仓库里保持 UTF-8，装进 QMT 后仍然可运行。
    """
    m = CODING_RE.match(code.lstrip()[:60])
    if m:
        try:
            return code.encode(m.group(1)), m.group(1)
        except (UnicodeEncodeError, LookupError):
            pass  # 声明与实际内容不符，退回 GBK 兜底
    return ("#coding:gbk\n" + code).encode("gbk", errors="replace"), "gbk"


def install_bridge() -> dict:
    """把桥接策略写入 QMT 策略目录：注入 BRIDGE_URL（本服务）与 BRIDGE_TOKEN。"""
    cfg = studio_cfg()
    target_dir = qmt_strategy_dir(cfg)
    if not target_dir:
        return {"ok": False, "error": "QMT 策略目录未配置：请在策略工坊设置中填写，"
                                      "或设置环境变量 QUANTLAB_QMT_PYTHON_DIR"}
    if not Path(target_dir).is_dir():
        return {"ok": False, "error": f"QMT 策略目录无效: {target_dir}（可在设置页修改）"}
    if not BRIDGE_STRATEGY_PATH.is_file():
        return {"ok": False, "error": "桥接策略源文件缺失"}
    code = BRIDGE_STRATEGY_PATH.read_text(encoding="utf-8")
    token = cfg.get("bridge_token") or ""
    account_id = str(cfg.get("qmt_account_id") or "")
    code = code.replace('BRIDGE_TOKEN = ""', f'BRIDGE_TOKEN = "{token}"', 1)
    code = code.replace('ACCOUNT_ID = ""', f'ACCOUNT_ID = "{account_id}"', 1)
    # URL 指向本服务（QuantLab, 8000），替换桥接脚本内的默认地址
    code = code.replace('BRIDGE_URL = "http://127.0.0.1:17321"',
                        'BRIDGE_URL = "http://127.0.0.1:8000"', 1)
    code = code.replace("EMOTION_STATS = True",
                        "EMOTION_STATS = %s" % ("True" if cfg.get("emotion_stats", True) else "False"), 1)
    code = code.replace("EMOTION_INTERVAL_MIN = 1",
                        "EMOTION_INTERVAL_MIN = %d" % max(1, int(cfg.get("emotion_interval_min", 1) or 1)), 1)
    filename = "QMT可视化桥接.py"
    fp = Path(target_dir) / filename
    backup_path = ""
    if fp.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = str(BACKUP_DIR / ("QMT可视化桥接_" + time.strftime("%Y%m%d_%H%M%S") + ".py"))
        try:
            shutil.copyfile(fp, backup_path)
        except Exception:  # noqa: BLE001
            backup_path = ""
    data, _encoding = encode_strategy_for_qmt(code)
    fp.write_bytes(data)
    ok, msg = register_model(target_dir, filename[:-3])
    if ok:
        # 记入看门狗清单：QMT 客户端退出时会回写注册表覆盖我们的注册，
        # 看门狗每 60s 用这份清单自动补注册
        reg = [x for x in _load_registry() if x.get("name") != filename[:-3]]
        reg.append({"name": filename[:-3], "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        _save_registry(reg)
    return {"ok": True, "path": str(fp), "registered": ok, "register_msg": msg,
            "client_running": qmt_client_running(), "backup": backup_path,
            "note": "桥接已指向 QuantLab(:8000)。请在 QMT 中停止旧桥接策略并重新运行"}


def save_to_qmt(body: dict) -> tuple[dict, int]:
    """保存生成的策略代码到 QMT 策略目录 + 注册模型 + 备份旧版。"""
    cfg = studio_cfg()
    target_dir = str(body.get("dir") or "").strip() or qmt_strategy_dir(cfg)
    filename = str(body.get("filename") or "").strip()
    code = str(body.get("code") or "")
    overwrite = bool(body.get("overwrite"))

    if not target_dir or not Path(target_dir).is_dir():
        return {"error": f"QMT 策略目录无效: {target_dir}（可在设置页修改）"}, 400
    filename = re.sub(r'[\\/:*?"<>|]', "", filename)
    if not filename:
        return {"error": "文件名不能为空"}, 400
    if not filename.endswith(".py"):
        filename += ".py"
    fp = Path(target_dir) / filename
    if fp.exists() and not overwrite:
        return {"error": "文件已存在: " + filename, "exists": True}, 409

    token = cfg.get("bridge_token") or ""
    if token and 'BRIDGE_TOKEN = ""' in code:
        code = code.replace('BRIDGE_TOKEN = ""', f'BRIDGE_TOKEN = "{token}"', 1)

    backup_path = ""
    if fp.exists() and overwrite:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = str(BACKUP_DIR / (filename[:-3] + "_" + time.strftime("%Y%m%d_%H%M%S") + ".py"))
        try:
            shutil.copyfile(fp, backup_path)
        except Exception:  # noqa: BLE001
            backup_path = ""

    data, encoding = encode_strategy_for_qmt(code)
    fp.write_bytes(data)

    model_name = filename[:-3]
    ok, msg = register_model(target_dir, model_name)
    if ok and msg == "registered":
        reg = _load_registry()
        reg = [x for x in reg if x.get("name") != model_name]
        reg.append({"name": model_name, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        _save_registry(reg)
    return {
        "ok": True, "path": str(fp), "encoding": encoding,
        "registered": ok, "register_msg": msg,
        "client_running": qmt_client_running(), "backup": backup_path,
    }, 200


def csv_bytes(headers: list, rows: list) -> bytes:
    def esc(v):
        v = "" if v is None else str(v)
        return '"' + v.replace('"', '""') + '"' if ("," in v or '"' in v or "\n" in v) else v
    lines = [",".join(esc(h) for h in headers)]
    for r in rows:
        lines.append(",".join(esc(x) for x in r))
    return ("\ufeff" + "\n".join(lines)).encode("utf-8")


def safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", text)[:64]
