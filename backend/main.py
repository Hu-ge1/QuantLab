"""QuantLab API 入口。

路由总览（统一前缀 /api）：
  /chat        SSE 流式对话 + 会话/记忆管理
  /portfolio   持仓 CRUD + 刷新价格
  /strategy    策略 CRUD + 聚宽语法回测沙箱
  /decision    贝叶斯决策引擎
  /evolve      策略自进化（SSE）
  /settings    密钥/数据源/QMT 设置
  /quote       行情直连查询
  /qmt         QMT 只读接入（内置桥接 + MiniQMT）
  /bridge      QMT 桥接协议（push/pull/state/指令/风控/安装）
  /studio      策略工坊（AI 生成/质检/修复/历史/保存到 QMT）
  /grid        网格交易任务、模拟 tick 与成交记录
"""
from __future__ import annotations

import threading
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as starlette_exceptions
from starlette.middleware.trustedhost import TrustedHostMiddleware

import database
import qmt_bridge
from routes import bridge, chat, decision, evolution, grid, market, portfolio, qmt, quote, screener, settings, studio, strategy, trading

app = FastAPI(title="QuantLab API", version="1.0.0")

LOCAL_BROWSER_ORIGINS = {
    "http://127.0.0.1:8000", "http://localhost:8000",
}
if os.environ.get("QUANTLAB_DEV_CORS") == "1":
    LOCAL_BROWSER_ORIGINS.update({
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:5174", "http://localhost:5174",
        "http://localhost:3000",
    })

# 本项目包含策略代码执行和真实交易接口，默认部署边界就是本机。
# 拒绝伪造 Host，并拦截来自任意网页的跨站 API 请求；QMT/CLI 客户端通常
# 不发送 Origin，仍可按既有协议访问。
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)


@app.middleware("http")
async def local_browser_guard(request, call_next):
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api/") and origin and origin not in LOCAL_BROWSER_ORIGINS:
        return JSONResponse({"detail": "拒绝非本机网页访问 API"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(LOCAL_BROWSER_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

database.init_db()

# 合并 qmt-strategy-studio：迁移旧配置/资产 → 加载桥接持久化 → 启动注册表看门狗
_migrate_info = qmt_bridge.migrate_from_legacy()
qmt_bridge.load_persisted()
threading.Thread(target=qmt_bridge.registry_watchdog, daemon=True).start()

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(strategy.router, prefix="/api/strategy", tags=["strategy"])
app.include_router(decision.router, prefix="/api/decision", tags=["decision"])
app.include_router(evolution.router, prefix="/api/evolve", tags=["evolution"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(quote.router, prefix="/api/quote", tags=["quote"])
app.include_router(qmt.router, prefix="/api/qmt", tags=["qmt"])
app.include_router(bridge.router, prefix="/api/bridge", tags=["bridge"])
app.include_router(studio.router, prefix="/api/studio", tags=["studio"])
app.include_router(grid.router, prefix="/api/grid", tags=["grid"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(screener.router, prefix="/api/screener", tags=["screener"])
app.include_router(trading.router, prefix="/api/trading", tags=["trading"])


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": "quant-lab",
        "bridge": {"online": qmt_bridge.bridge_online(), "readonly_orders_default": False},
    }


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
def api_not_found(path: str):
    """Keep unknown API paths out of the SPA fallback and never return index.html as success."""
    raise HTTPException(404, f"API endpoint not found: /api/{path}")


# 生产模式：托管前端构建产物（npm run build 后单端口 8000 即可访问）
# SPA history 路由回退：文件不存在时回落 index.html
DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except starlette_exceptions as e:
                # API misspellings must stay 404; only browser routes receive SPA fallback.
                if e.status_code == 404 and not path.lstrip("/").startswith("api/"):
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", SPAStaticFiles(directory=str(DIST), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    # 本应用包含策略代码执行与交易接口，默认只允许本机访问。
    # 如确需局域网部署，应在受控反向代理后显式配置认证，而不是直接暴露端口。
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
