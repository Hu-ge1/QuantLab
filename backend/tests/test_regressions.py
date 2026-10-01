from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import data_provider as dp
import qmt_bridge
from routes import strategy
from routes.portfolio import PositionCreate


def _bars() -> list[dict]:
    return [
        {
            "date": f"2026-01-{day:02d}",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
        }
        for day in range(1, 13)
    ]


class BacktestRegressionTests(unittest.TestCase):
    def test_run_daily_can_be_registered_inside_initialize(self):
        code = """
def initialize(context):
    run_daily(trade)

def trade(context):
    pass
"""
        days = [item["date"] for item in _bars()]
        with (
            patch.object(strategy.dp, "get_trade_days", return_value=days),
            patch.object(strategy.dp, "get_price_range", return_value=(_bars(), "test-source")),
        ):
            result = strategy._run_backtest_local(
                code,
                strategy.BacktestParams(start_date=days[0], end_date=days[-1]),
            )
        self.assertEqual(result["stats"]["trade_days"], len(days))
        self.assertEqual(result["source"], "test-source")


class TradingSafetyTests(unittest.TestCase):
    def test_invalid_side_is_rejected_in_defensive_core(self):
        config = {
            "trading_enabled": True,
            "allow_order": True,
            "order_max_volume": 2000,
            "order_max_per_day": 20,
            "order_allowlist": [],
        }
        with patch.object(qmt_bridge, "studio_cfg", return_value=config):
            result, status = qmt_bridge.issue_command({
                "action": "order",
                "code": "600519.SH",
                "side": "hold",
                "prType": "limit",
                "price": 100.0,
                "volume": 100,
            })
        self.assertEqual(status, 400)
        self.assertIn("buy", result["error"])

    def test_demo_price_is_rejected_for_real_portfolio_use(self):
        with patch.object(dp, "get_price_bars", return_value=(_bars()[-2:], "demo·模拟数据")):
            with self.assertRaises(dp.DataProviderError):
                dp.get_latest_close("600519.SH", allow_demo=False)

    def test_position_validation_rejects_negative_and_bad_code(self):
        with self.assertRaises(Exception):
            PositionCreate(code="not-a-code", cost=10, shares=100)
        with self.assertRaises(Exception):
            PositionCreate(code="600519.SH", cost=-1, shares=100)

    def test_command_stays_inflight_until_result_acknowledges_it(self):
        config = {
            "trading_enabled": True,
            "allow_order": True,
            "order_max_volume": 2000,
            "order_max_per_day": 20,
            "order_allowlist": [],
        }
        queued_before = list(qmt_bridge.BRIDGE_COMMANDS)
        inflight_before = dict(qmt_bridge.BRIDGE_INFLIGHT)
        count_before = dict(qmt_bridge.ORDER_COUNT)
        try:
            qmt_bridge.BRIDGE_COMMANDS.clear()
            qmt_bridge.BRIDGE_INFLIGHT.clear()
            qmt_bridge.ORDER_COUNT.update({"date": "", "count": 0})
            with tempfile.TemporaryDirectory() as tmpdir:
                with (
                    patch.object(qmt_bridge, "studio_cfg", return_value=config),
                    patch.object(qmt_bridge, "trading_phase", return_value={"trading": True, "label": "测试"}),
                    patch.object(qmt_bridge, "COMMAND_QUEUE_PATH", new=Path(tmpdir) / "commands.json"),
                    patch.object(qmt_bridge, "ORDER_COUNT_PATH", new=Path(tmpdir) / "count.json"),
                    patch.object(qmt_bridge, "audit"),
                    patch.object(qmt_bridge, "_persist_bridge_state"),
                ):
                    result, status = qmt_bridge.issue_command({
                        "action": "order",
                        "code": "600519.SH",
                        "side": "buy",
                        "prType": "limit",
                        "price": 100.0,
                        "volume": 100,
                    })
                    self.assertEqual(status, 200)
                    command_id = result["id"]
                    pulled = qmt_bridge.bridge_pull()["commands"]
                    self.assertEqual([item["id"] for item in pulled], [command_id])
                    self.assertIn(command_id, qmt_bridge.BRIDGE_INFLIGHT)
                    qmt_bridge.bridge_push({
                        "type": "result",
                        "command_id": command_id,
                        "action": "order",
                        "ok": True,
                    })
                    self.assertNotIn(command_id, qmt_bridge.BRIDGE_INFLIGHT)
        finally:
            qmt_bridge.BRIDGE_COMMANDS[:] = queued_before
            qmt_bridge.BRIDGE_INFLIGHT.clear()
            qmt_bridge.BRIDGE_INFLIGHT.update(inflight_before)
            qmt_bridge.ORDER_COUNT.clear()
            qmt_bridge.ORDER_COUNT.update(count_before)


if __name__ == "__main__":
    unittest.main()
