"""Disposable worker for running one strategy backtest outside the API process."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# ``python -I`` intentionally removes the working directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from routes.strategy import BacktestParams, _run_backtest_local  # noqa: E402


def main() -> int:
    payload = json.loads(sys.stdin.read())
    result_path = Path(payload["result_path"])
    try:
        result = _run_backtest_local(
            str(payload["code"]),
            BacktestParams(**payload["params"]),
        )
        response = {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        response = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
    result_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
