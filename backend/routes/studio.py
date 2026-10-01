"""策略工坊路由 — AI 生成 QMT 策略（SSE 流式）+ 质检/修复/diff + 历史保存到 QMT。

从 qmt-strategy-studio/server.py 移植合并；LLM 走 QuantLab 统一配置
（设置页 / 环境变量 / 旧 studio config 迁移值），temperature 用 studio_cfg。
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
import qmt_bridge
import checker as code_checker
from md_render import md_to_html
from prompts import build_messages, build_repair_messages, build_revision_messages
from database import get_setting
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

router = APIRouter()

STUDIO_DIR = Path(__file__).parent.parent / "data" / "studio"
HISTORY_DIR = STUDIO_DIR / "history"
KNOWLEDGE_DIR = STUDIO_DIR / "knowledge"


class CheckBody(BaseModel):
    code: str
    form: str = "qmt_builtin"


class DiffBody(BaseModel):
    a: str = ""
    b: str = ""


class SaveBody(BaseModel):
    filename: str
    code: str
    dir: str | None = None
    overwrite: bool = False


class HistoryBody(BaseModel):
    id: str | None = None
    title: str | None = None
    requirement: str = ""
    form: str = ""
    mode: str = ""
    template: str = ""
    output: str = ""
    time: str | None = None


class GenerateBody(BaseModel):
    form: str = "qmt_builtin"
    mode: str = "backtest"
    extra: str = ""
    knowledge: list[str] = []
    meta: dict = {}
    requirement: str = ""
    revision: bool = False
    base_code: str = ""
    base_requirement: str = ""
    note: str = ""


def _llm_config() -> dict:
    """生成/修复用 LLM 配置：QuantLab 统一解析 + studio 的 temperature。"""
    from routes.chat import _resolve_llm_config

    base_url, api_key, model, provider, from_env = _resolve_llm_config()
    if provider == "anthropic" or not api_key:
        return {"ok": False, "error": "策略工坊生成仅支持 OpenAI 兼容接口，请先配置有效 API Key"}
    return {
        "ok": True,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "temperature": float(qmt_bridge.studio_cfg().get("temperature", 0.3)),
    }


def _knowledge_docs(names: list[str]) -> list[str]:
    docs = []
    for name in names or []:
        safe = str(name).replace("\\", "/")
        if ".." in safe or not safe.endswith(".md"):
            continue
        fp = KNOWLEDGE_DIR / safe
        if fp.is_file():
            try:
                docs.append(fp.read_text(encoding="utf-8", errors="replace")[:20000])
            except Exception:  # noqa: BLE001
                pass
    return docs


@router.post("/generate")
async def generate(body: GenerateBody):
    llm = _llm_config()
    if not llm["ok"]:
        raise HTTPException(400, llm["error"])

    if body.revision:
        base_code = body.base_code.strip()
        note = body.note.strip()
        if not base_code or not note:
            raise HTTPException(400, "修订缺少当前代码或修改要求")
        messages = build_revision_messages(
            body.base_requirement.strip(), base_code, note, body.form, body.mode, body.extra.strip())
    else:
        requirement = body.requirement.strip()
        if not requirement:
            raise HTTPException(400, "策略需求不能为空")
        docs = _knowledge_docs(body.knowledge)
        messages = build_messages(requirement, body.form, body.mode, body.extra.strip(),
                                  docs, body.meta)

    url = llm["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": llm["model"],
        "messages": messages,
        "stream": True,
        "temperature": llm["temperature"],
    }
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + llm["api_key"],
               "Accept": "text/event-stream"}

    task_id = str(time.time())
    queue: asyncio.Queue = asyncio.Queue()
    output_ref = {"text": "", "done": False, "error": None}

    async def event_generator():
        try:
            yield f"data: {json.dumps({'task_id': task_id}, ensure_ascii=False)}\n\n"
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        except (ClientDisconnect, asyncio.CancelledError, GeneratorExit):
            # 前端断开，后台继续跑；完成后可从前端重连或历史记录取回
            pass

    async def run_upstream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        output_ref["error"] = f"大模型接口返回 {resp.status_code}: {detail}"
                        await queue.put(f'data: {json.dumps({"error": output_ref["error"]}, ensure_ascii=False)}\n\n')
                        return
                    async for chunk in resp.aiter_bytes(1024):
                        output_ref["text"] += chunk.decode("utf-8", errors="replace")
                        await queue.put(chunk)
        except Exception as e:  # noqa: BLE001
            output_ref["error"] = f"无法连接大模型接口: {e}"
            await queue.put(f'data: {json.dumps({"error": output_ref["error"]}, ensure_ascii=False)}\n\n')
        finally:
            output_ref["done"] = True
            # 落库到历史记录，避免结果丢失
            if output_ref["text"].strip():
                try:
                    m = re.search(r"```(?:python|py)?\r?\n([\s\S]*?)```", output_ref["text"])
                    code = m.group(1).strip() if m else output_ref["text"].strip()
                    hid = qmt_bridge.safe_id("") or time.strftime("%Y%m%d%H%M%S")
                    data = {
                        "id": hid,
                        "title": (body.requirement or "未命名")[:60],
                        "requirement": body.requirement,
                        "form": body.form,
                        "mode": body.mode,
                        "template": body.template,
                        "output": code,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    (HISTORY_DIR / (hid + ".json")).write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
            await queue.put(None)

    asyncio.create_task(run_upstream())
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/check")
def check(body: CheckBody):
    return {"issues": code_checker.check_code(body.code, body.form)}


@router.post("/repair")
async def repair(body: CheckBody):
    llm = _llm_config()
    if not llm["ok"]:
        raise HTTPException(400, llm["error"])
    code = body.code
    issues = code_checker.check_code(code, body.form)
    if not issues:
        return {"ok": True, "code": code, "notes": "无问题，未修改"}
    messages = build_repair_messages(code, issues, body.form)
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(url, headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + llm["api_key"],
            }, json={"model": llm["model"], "messages": messages,
                     "stream": False, "temperature": 0.1})
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"修复请求失败: {e}")
    m = re.search(r"```(?:python|py)?\r?\n([\s\S]*?)```", text)
    fixed = m.group(1).strip() if m else text.strip()
    return {"ok": True, "code": fixed, "remaining_issues": code_checker.check_code(fixed, body.form)}


@router.post("/diff")
def diff(body: DiffBody):
    a = body.a.splitlines(keepends=True)
    b = body.b.splitlines(keepends=True)
    return {"diff": "\n".join(list(difflib.unified_diff(a, b, "旧版", "新版", lineterm=""))[:500])}


@router.get("/templates")
def templates():
    return qmt_bridge.load_templates()


@router.get("/knowledge")
def knowledge_list():
    docs = []
    if KNOWLEDGE_DIR.is_dir():
        for fp in KNOWLEDGE_DIR.rglob("*.md"):
            rel = fp.relative_to(KNOWLEDGE_DIR).as_posix()
            docs.append({"name": rel, "size": fp.stat().st_size})
    docs.sort(key=lambda d: d["name"])
    return {"docs": docs}


@router.get("/knowledge/doc")
def knowledge_doc(name: str):
    safe = name.replace("\\", "/")
    if ".." in safe or not safe.endswith(".md"):
        raise HTTPException(400, "非法文档名")
    fp = KNOWLEDGE_DIR / safe
    if not fp.is_file():
        raise HTTPException(404, "文档不存在")
    return {"name": safe, "html": md_to_html(fp.read_text(encoding="utf-8", errors="replace"))}


@router.get("/history")
def history_list(q: str = Query("")):
    items = []
    if HISTORY_DIR.is_dir():
        for fp in HISTORY_DIR.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            item = {
                "id": data.get("id", fp.stem),
                "title": data.get("title", ""),
                "form": data.get("form", ""),
                "mode": data.get("mode", ""),
                "time": data.get("time", ""),
                "output_len": len(data.get("output", "")),
            }
            if q and q.lower() not in json.dumps(
                    [item["title"], item["form"], item["mode"]]).lower():
                continue
            items.append(item)
    items.sort(key=lambda x: x.get("time", ""), reverse=True)
    return {"items": items[: int(qmt_bridge.studio_cfg().get("max_history", 200))]}


@router.get("/history/{hid}")
def history_get(hid: str):
    fp = HISTORY_DIR / (qmt_bridge.safe_id(hid) + ".json")
    if not fp.exists():
        raise HTTPException(404, "记录不存在")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.delete("/history/{hid}")
def history_delete(hid: str):
    fp = HISTORY_DIR / (qmt_bridge.safe_id(hid) + ".json")
    if fp.exists():
        fp.unlink()
    return {"ok": True}


@router.post("/history")
def history_add(body: HistoryBody):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    hid = qmt_bridge.safe_id(body.id or "") or time.strftime("%Y%m%d%H%M%S")
    data = {
        "id": hid,
        "title": (body.title or body.requirement or "未命名")[:60],
        "requirement": body.requirement,
        "form": body.form,
        "mode": body.mode,
        "template": body.template,
        "output": body.output,
        "time": body.time or time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (HISTORY_DIR / (hid + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"id": hid}


@router.post("/save")
def save_to_qmt(body: SaveBody):
    data, code = qmt_bridge.save_to_qmt(body.model_dump())
    return JSONResponse(status_code=code, content=data)


@router.get("/qmtstatus")
def qmtstatus():
    cfg = qmt_bridge.studio_cfg()
    qdir = cfg.get("qmt_python_dir") or ""
    root = Path(qdir.rstrip("\\/")).parent if qdir else None
    xml_path = root / "config" / "indexUserConfig.xml" if root else None
    return {
        "python_dir": qdir,
        "python_dir_ok": bool(qdir and Path(qdir).is_dir()),
        "xml_found": bool(xml_path and xml_path.is_file()),
        "xml_path": str(xml_path) if xml_path else "",
        "client_running": qmt_bridge.qmt_client_running(),
        "registry": qmt_bridge._load_registry(),
    }


class StudioConfigBody(BaseModel):
    qmt_python_dir: str | None = None
    temperature: float | None = None
    allow_order: bool | None = None
    order_max_volume: int | None = None
    order_max_per_day: int | None = None
    order_allowlist: str | list | None = None
    emotion_stats: bool | None = None
    emotion_interval_min: int | None = None
    max_history: int | None = None


@router.get("/config")
def studio_config_get():
    cfg = qmt_bridge.studio_cfg()
    return {
        "qmt_python_dir": cfg.get("qmt_python_dir"),
        "temperature": cfg.get("temperature"),
        "allow_order": bool(cfg.get("allow_order")),
        "order_max_volume": cfg.get("order_max_volume"),
        "order_max_per_day": cfg.get("order_max_per_day"),
        "order_allowlist": cfg.get("order_allowlist") or [],
        "emotion_stats": bool(cfg.get("emotion_stats")),
        "emotion_interval_min": cfg.get("emotion_interval_min"),
        "max_history": cfg.get("max_history"),
    }


@router.put("/config")
def studio_config_set(body: StudioConfigBody):
    cfg = qmt_bridge.studio_cfg()
    d = body.model_dump(exclude_none=True)
    if "qmt_python_dir" in d:
        cfg["qmt_python_dir"] = str(d["qmt_python_dir"]).strip()
    if "temperature" in d:
        cfg["temperature"] = max(0.0, min(2.0, float(d["temperature"])))
    for k in ("allow_order", "emotion_stats"):
        if k in d:
            cfg[k] = bool(d[k])
    for k in ("order_max_volume", "order_max_per_day", "emotion_interval_min", "max_history"):
        if k in d:
            try:
                cfg[k] = max(1, int(d[k]))
            except (TypeError, ValueError):
                pass
    if "order_allowlist" in d:
        raw = d["order_allowlist"]
        items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
        cfg["order_allowlist"] = [c.strip().upper() for c in items
                                  if re.match(r"^\d{6}\.(SH|SZ|BJ)$", str(c).strip().upper())]
    qmt_bridge.save_studio_cfg(cfg)
    return {"ok": True}
