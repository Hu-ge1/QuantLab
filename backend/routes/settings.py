"""设置路由 — 密钥存本地 SQLite（不依赖环境变量），敏感键掩码回显。"""
from __future__ import annotations

from database import get_all_settings, get_setting, set_setting
from fastapi import APIRouter
from pydantic import BaseModel

import data_provider as dp

router = APIRouter()

SENSITIVE_KEYS = {"ai_api_key", "ffd_api_key"}
MASK = "••••••••"


class SettingsPayload(BaseModel):
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_provider: str | None = None
    theme: str | None = None
    ffd_api_key: str | None = None


@router.get("")
def get_settings():
    settings = get_all_settings()
    for key in SENSITIVE_KEYS:
        if settings.get(key):
            settings[key] = MASK
    return settings


@router.put("")
def update_settings(payload: SettingsPayload):
    for key, value in payload.model_dump().items():
        if value is None:
            continue
        if key in SENSITIVE_KEYS and value == MASK:
            continue  # 前端未改密钥时回传掩码，不覆盖真值
        set_setting(key, value)
    return {"ok": True}


@router.post("/test-datasource")
def test_datasource():
    """主动探测 akshare 连通性（失败自动降级 demo 并返回原因）。"""
    dp.invalidate_static_cache()
    return dp.probe_akshare()


@router.get("/datasource")
def datasource_status():
    return dp.provider_status()


@router.get("/llm-status")
def llm_status():
    """LLM 配置生效状态（不回传密钥本身）。"""
    from routes.chat import _resolve_llm_config

    base_url, api_key, model, provider, from_env = _resolve_llm_config()
    return {
        "has_key": bool(api_key),
        "from_env": from_env,
        "base_url": base_url,
        "model": model,
        "provider": provider,
    }
