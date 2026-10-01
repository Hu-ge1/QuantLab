"""SQLite 持久层 — QuantLab 的全部状态都落在本地一个 DB 文件里：
设置（LLM 密钥等）、持仓、策略与回测、会话与消息、长期记忆、进化记录。

访问模式：每次操作开连接、用完即关（check_same_thread=False + WAL），
不做连接池 —— 简单、够用、无跨线程隐患。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "quant_lab.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS positions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT NOT NULL,
                name       TEXT DEFAULT '',
                cost       REAL DEFAULT 0,
                shares     REAL DEFAULT 0,
                cur_price  REAL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                code        TEXT DEFAULT '',
                status      TEXT DEFAULT 'idle',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                params_json TEXT,
                result_json TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (strategy_id) REFERENCES strategies (id)
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT DEFAULT '新对话',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                key        TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS evolution_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                universe_json   TEXT,
                best_config_json TEXT,
                history_json    TEXT,
                summary_json    TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grid_jobs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT NOT NULL,
                name                TEXT DEFAULT '',
                base_price          REAL DEFAULT 0,
                step_pct            REAL DEFAULT 0.015,
                per_grid_amount     REAL DEFAULT 5000,
                upper_limit         REAL DEFAULT 0,
                lower_limit         REAL DEFAULT 0,
                grid_levels         INTEGER DEFAULT 10,
                spacing_type        TEXT DEFAULT 'geometric',
                max_position_amount REAL DEFAULT 50000,
                mode                TEXT DEFAULT 'percent',
                config_json         TEXT DEFAULT '{}',
                submitted_to_qmt    INTEGER DEFAULT 0,
                status              TEXT DEFAULT 'idle',
                current_price       REAL DEFAULT 0,
                position_qty        REAL DEFAULT 0,
                position_cost       REAL DEFAULT 0,
                total_pnl           REAL DEFAULT 0,
                state_json          TEXT DEFAULT '{}',
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grid_fills (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id     INTEGER NOT NULL,
                side       TEXT NOT NULL,
                price      REAL NOT NULL,
                qty        REAL NOT NULL,
                fee        REAL DEFAULT 0,
                pnl        REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES grid_jobs (id)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(grid_jobs)").fetchall()}
        migrations = {
            "mode": "ALTER TABLE grid_jobs ADD COLUMN mode TEXT DEFAULT 'percent'",
            "config_json": "ALTER TABLE grid_jobs ADD COLUMN config_json TEXT DEFAULT '{}'",
            "submitted_to_qmt": "ALTER TABLE grid_jobs ADD COLUMN submitted_to_qmt INTEGER DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


# ── settings KV：LLM 密钥等全部存这里（JSON 序列化） ─────────────────────────

def get_setting(key: str, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]
    finally:
        conn.close()


def set_setting(key: str, value) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_settings() -> dict:
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        out = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                out[row["key"]] = row["value"]
        return out
    finally:
        conn.close()
