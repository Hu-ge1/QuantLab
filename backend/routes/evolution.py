"""策略自进化路由 — SSE 流式推送进化过程，结果落库。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re

from agent import evolution
from database import get_db
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class EvolveRequest(BaseModel):
    universe: list[str] | None = None
    rounds: int = 12
    seed: int = 42
    bars: int = 250  # 价格面板回看交易日数（120~750）
    strategy_id: int


def _strategy_row(strategy_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "母策略不存在，请先在策略中心保存策略")
    return dict(row)


@router.get("/meta")
def meta(strategy_id: int | None = None):
    selected = _strategy_row(strategy_id) if strategy_id is not None else None
    param_space = evolution.discover_tunable_params(selected["code"]) if selected else {}
    return {
        "param_space": param_space,
        "strategy": ({"id": selected["id"], "name": selected["name"], "description": selected["description"]} if selected else None),
        "default_universe": evolution.DEFAULT_UNIVERSE,
        "default_config": {key: spec["default"] for key, spec in param_space.items()},
        "split": {"train": 0.5, "val": 0.25, "test": 0.25},
    }


@router.get("/runs")
def list_runs():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, summary_json, created_at FROM evolution_runs "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        out = []
        for r in rows:
            try:
                summary = json.loads(r["summary_json"])
            except (json.JSONDecodeError, TypeError):
                summary = {}
            out.append({"id": r["id"], "created_at": r["created_at"], "summary": summary})
        return out
    finally:
        conn.close()


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM evolution_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": "记录不存在"}
    return {
        "id": row["id"],
        "universe": json.loads(row["universe_json"] or "[]"),
        "best_config": json.loads(row["best_config_json"] or "{}"),
        "history": json.loads(row["history_json"] or "[]"),
        "summary": json.loads(row["summary_json"] or "{}"),
        "created_at": row["created_at"],
    }


@router.post("/run")
async def run_evolution(req: EvolveRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        while True:
            ev = await queue.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    async def run_task():
        def _sse(obj: dict) -> dict:
            return obj

        try:
            strategy = _strategy_row(req.strategy_id)
            code = (strategy.get("code") or "").strip()
            if not code:
                raise ValueError("母策略代码为空，请先在策略中心完善并保存")
            param_space = evolution.discover_tunable_params(code)
            if not param_space:
                raise ValueError("母策略没有可进化参数；请在 initialize 中使用 g.参数名 = 数字 声明参数")
            await queue.put({"type": "status", "text": f"正在准备母策略「{strategy['name']}」的隔离回测窗口…"})
            loop = asyncio.get_event_loop()
            bars, source = await loop.run_in_executor(None, lambda: evolution.dp.get_price_bars("000300.SH", count=max(120, min(req.bars, 750))))
            dates = [bar["date"] for bar in bars]
            if len(dates) < 120:
                raise ValueError("基准行情不足 120 个交易日，无法隔离训练/验证/测试集")
            i_train, i_val = int(len(dates) * 0.5), int(len(dates) * 0.75)
            windows = {
                "train": (dates[0], dates[i_train - 1]),
                "val": (dates[i_train], dates[i_val - 1]),
                "test": (dates[i_val], dates[-1]),
            }
            symbols = sorted(set(re.findall(r"\b\d{6}\.(?:SH|SZ|BJ)\b", code.upper())))
            await queue.put({
                "type": "panel",
                "universe": symbols, "days": len(dates),
                "range": f"{dates[0]} ~ {dates[-1]}", "source": source,
                "strategy_id": strategy["id"], "strategy_name": strategy["name"],
            })

            from routes import strategy as strategy_route

            def evaluate(config: dict, window_name: str) -> dict:
                start, end = windows[window_name]
                evolved_code = evolution.rewrite_strategy_params(code, config)
                params = strategy_route.BacktestParams(start_date=start, end_date=end)
                result = strategy_route._run_backtest_isolated(evolved_code, params)
                stats = result["stats"]
                return {
                    "ann_return": stats["ann_return"], "total_return": stats["total_return"],
                    "sharpe": stats["sharpe"], "max_drawdown": stats["max_drawdown"],
                    "turnover": 0, "final": stats["final_value"],
                    "n_days": stats["trade_days"], "valid": True,
                }

            rng = random.Random(req.seed)
            best_config = {key: spec["default"] for key, spec in param_space.items()}
            metrics = {"train": evaluate(best_config, "train"), "val": evaluate(best_config, "val")}
            history = [{"version": "v0.0", "config": dict(best_config), "metrics": metrics, "kept": True, "change": "母策略初始版本", "parent": None}]
            await queue.put({"type": "init", "version": "v0.0", "config": best_config, "metrics": metrics})
            best_val, kept, fail_streak, rounds_run = metrics["val"]["sharpe"], 0, 0, 0
            for round_no in range(1, min(max(req.rounds, 1), 20) + 1):
                rounds_run = round_no
                neighbors = evolution._neighbors(best_config, param_space)
                if "fast" in best_config and "slow" in best_config:
                    neighbors = [item for item in neighbors if item[2]["fast"] < item[2]["slow"]]
                rng.shuffle(neighbors)
                best_candidate = None
                for key, value, candidate in neighbors[:3]:
                    candidate_metrics = {"train": evaluate(candidate, "train"), "val": evaluate(candidate, "val")}
                    if best_candidate is None or candidate_metrics["val"]["sharpe"] > best_candidate[3]["val"]["sharpe"]:
                        best_candidate = (key, value, candidate, candidate_metrics)
                if best_candidate and best_candidate[3]["val"]["sharpe"] > best_val + 1e-6:
                    key, value, candidate, metrics = best_candidate
                    old = best_config[key]
                    best_config, best_val, fail_streak, kept = candidate, metrics["val"]["sharpe"], 0, kept + 1
                    version = f"v0.{kept}"
                    change = f"g.{key} {old}→{value}"
                    history.append({"version": version, "config": dict(best_config), "metrics": metrics, "kept": True, "change": change, "parent": history[-1]["version"]})
                    await queue.put({"type": "round", "round": round_no, "kept": True, "version": version, "change": change, "val_sharpe": best_val})
                else:
                    fail_streak += 1
                    await queue.put({"type": "round", "round": round_no, "kept": False, "change": "本轮无改进，回滚", "val_sharpe": best_val})
                    if fail_streak >= 3:
                        break
                await asyncio.sleep(0)

            init_test = evaluate(history[0]["config"], "test")
            final_test = evaluate(best_config, "test")
            history[0]["metrics"]["test"] = init_test
            history[-1]["metrics"]["test"] = final_test
            summary = {
                "strategy_id": strategy["id"], "strategy_name": strategy["name"],
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "rounds_run": rounds_run, "versions_kept": kept,
                "init": {"val_sharpe": history[0]["metrics"]["val"]["sharpe"], "test_sharpe": init_test["sharpe"], "test_ann_return": init_test["ann_return"], "test_max_drawdown": init_test["max_drawdown"]},
                "final": {"val_sharpe": best_val, "test_sharpe": final_test["sharpe"], "test_ann_return": final_test["ann_return"], "test_max_drawdown": final_test["max_drawdown"]},
            }
            await queue.put({"type": "done", "best_config": best_config, "history": history, "summary": summary})

            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO evolution_runs "
                    "(universe_json, best_config_json, history_json, summary_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        json.dumps(symbols, ensure_ascii=False),
                        json.dumps(best_config, ensure_ascii=False),
                        json.dumps(history, ensure_ascii=False),
                        json.dumps(summary, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            await queue.put({"type": "error", "text": f"自进化执行失败：{e}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_task())
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
