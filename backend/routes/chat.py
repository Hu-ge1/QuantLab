"""对话路由 — SSE 流式聊天 + 会话管理 + 长期记忆管理。"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from database import get_db, get_setting
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from agent.memory import clear_all_memories, get_memories

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


# ── 会话持久化 ──────────────────────────────────────────────────────────────

def _ensure_session(session_id: str) -> None:
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO chat_sessions (id) VALUES (?)", (session_id,))
            conn.commit()
    finally:
        conn.close()


def _save_message(session_id: str, role: str, content: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        if role == "user":
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chat_messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()
            if row and row["n"] == 1:  # 首条消息作会话标题
                title = content.strip()[:30] or "新对话"
                conn.execute("UPDATE chat_sessions SET title = ? WHERE id = ?",
                             (title, session_id))
        conn.execute("UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (session_id,))
        conn.commit()
    finally:
        conn.close()


def _get_history(session_id: str, limit: int = 40) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        rows.reverse()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


# ── 流式对话 ────────────────────────────────────────────────────────────────

# ── 模型配置：SQLite 设置优先，环境变量兜底（AI_API_KEY / AI_BASE_URL / AI_MODEL / AI_PROVIDER，或 DEEPSEEK_API_KEY 等常见命名） ──

_ENV_FALLBACKS = {
    "ai_api_key": ["AI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY"],
    "ai_base_url": ["AI_BASE_URL"],
    "ai_model": ["AI_MODEL"],
    "ai_provider": ["AI_PROVIDER"],
}


def _resolve_llm_config() -> tuple[str, str, str, str, bool]:
    """返回 (base_url, api_key, model, provider, from_env)。"""
    base_url = get_setting("ai_base_url", "")
    api_key = get_setting("ai_api_key", "")
    model = get_setting("ai_model", "")
    provider = get_setting("ai_provider", "")
    from_env = False
    if not api_key:
        for name in _ENV_FALLBACKS["ai_api_key"]:
            if os.environ.get(name):
                api_key = os.environ[name]
                from_env = True
                break
    if not api_key:
        # 兜底：读取 qmt-strategy-studio 的 config.json（若其中配置了 api_key）
        try:
            import qmt_client

            cfg = qmt_client._studio_cfg() or {}
            studio_key = str(cfg.get("api_key") or "").strip()
            if studio_key:
                api_key = studio_key
                from_env = True  # 同样属于"非设置页"来源
                if not base_url:
                    base_url = str(cfg.get("base_url") or "").strip()
                if not model:
                    model = str(cfg.get("model") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    if from_env and not base_url:
        base_url = os.environ.get("AI_BASE_URL", "")
        # 按密钥形态猜默认端点
        if not base_url:
            if api_key == os.environ.get("DEEPSEEK_API_KEY"):
                base_url = "https://api.deepseek.com/v1"
        base_url = base_url or "https://api.openai.com/v1"
    if base_url and not base_url.rstrip("/").endswith("/v1") and "anthropic" not in base_url:
        # studio 配置里的 base_url 形如 https://api.deepseek.com，补全 OpenAI 兼容路径
        if api_key and not api_key.startswith("sk-ant-"):
            base_url = base_url.rstrip("/") + "/v1"
    if from_env and not model:
        model = os.environ.get("AI_MODEL", "")
    if not model:
        # 按端点形态给默认模型名
        if "deepseek" in base_url:
            model = "deepseek-chat"
        elif "dashscope" in base_url:
            model = "qwen-plus"
        elif "moonshot" in base_url:
            model = "moonshot-v1-32k"
        else:
            model = "gpt-4o-mini"
    if not provider:
        provider = "anthropic" if api_key.startswith("sk-ant-") else "openai"
    if not base_url:
        base_url = "https://api.openai.com/v1"
    return base_url, api_key, model, provider, from_env


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    _ensure_session(session_id)
    _save_message(session_id, "user", req.message)
    history = _get_history(session_id)

    base_url, api_key, model, provider, from_env = _resolve_llm_config()

    async def event_generator():
        # 先回 session_id，前端刷新会话列表
        yield f"data: {json.dumps({'type': 'session_id', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        if not api_key:
            yield f"data: {json.dumps({'type': 'error', 'text': '尚未配置大模型 API Key：可在「设置」页填写，或设置环境变量 AI_API_KEY / DEEPSEEK_API_KEY 后重启'}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
            return

        from agent.loop import run_agent_turn

        full_text = ""
        try:
            async for sse in run_agent_turn(history, base_url, api_key, model, provider):
                # 收集最终全文入库：token 事件携带 text
                if '"type": "token"' in sse or '"type":"token"' in sse:
                    try:
                        ev = json.loads(sse[6:])
                        full_text += ev.get("text", "")
                    except json.JSONDecodeError:
                        pass
                yield sse
        except (ClientDisconnect, asyncio.CancelledError, GeneratorExit):
            # finally 统一保存已生成部分，避免断开时重复入库。
            return
        finally:
            if full_text.strip():
                _save_message(session_id, "assistant", full_text)
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 会话 CRUD ───────────────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chat_sessions "
            "ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/sessions")
def create_session():
    sid = str(uuid.uuid4())
    _ensure_session(sid)
    return {"session_id": sid}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── 长期记忆管理 ────────────────────────────────────────────────────────────

@router.get("/memories")
def list_memories():
    return get_memories(limit=50)


@router.delete("/memories")
def clear_memories():
    clear_all_memories()
    return {"ok": True}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int):
    from agent.memory import delete_memory as _delete

    _delete(memory_id)
    return {"ok": True}
