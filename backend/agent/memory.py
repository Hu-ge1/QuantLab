"""长期记忆 — 追加式存 SQLite，构建 system prompt 时按时间正序注入。"""
from __future__ import annotations

from database import get_db


def save_memory(key: str, content: str) -> dict:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO agent_memories (key, content) VALUES (?, ?)",
            (key, content),
        )
        conn.commit()
        return {"ok": True, "saved": f"[{key}] {content[:80]}"}
    finally:
        conn.close()


def get_memories(limit: int = 30) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, key, content, created_at FROM agent_memories "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_memory(memory_id: int) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM agent_memories WHERE id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()


def clear_all_memories() -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM agent_memories")
        conn.commit()
    finally:
        conn.close()


def build_memory_prompt(limit: int = 30) -> str:
    """把最近记忆按时间正序拼进 system prompt。"""
    rows = get_memories(limit=limit)
    if not rows:
        return ""
    rows.reverse()
    lines = [f"- [{r['key']}] {r['content']}" for r in rows]
    return "\n\n## 长期记忆（用户存档信息，自动加载）\n" + "\n".join(lines)
