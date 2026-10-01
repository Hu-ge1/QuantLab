"""Disposable worker for running one strategy backtest outside the API process."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# ``python -I`` intentionally removes the working directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from routes.strategy import BacktestParams, _run_backtest_local  # noqa: E402


def _read_stdin_utf8() -> str:
    """Read the parent's payload as UTF-8 regardless of the local code page.

    ``python -I`` implies ``-E``, so ``PYTHONIOENCODING`` is ignored and
    ``sys.stdin`` falls back to the platform ANSI code page (GBK on Chinese
    Windows) with ``surrogateescape``. Reading the raw buffer keeps non-ASCII
    strategy code intact instead of decoding it as the wrong charset.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8")
    return sys.stdin.read()


def _force_utf8_std_streams() -> None:
    """Keep diagnostics readable for the parent, which decodes output as UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - unusual stream
            pass


def main() -> int:
    _force_utf8_std_streams()
    payload = json.loads(_read_stdin_utf8())
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
    text = json.dumps(response, ensure_ascii=False)
    # A lone surrogate can never be encoded to UTF-8. Replace it here so the
    # parent receives a readable error instead of an opaque UnicodeEncodeError
    # (or, worse, an empty result file).
    result_path.write_text(
        text.encode("utf-8", "replace").decode("utf-8"), encoding="utf-8",
    )
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
