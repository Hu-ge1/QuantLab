from __future__ import annotations

import unittest
from unittest.mock import patch

import screener


class ScreenerTests(unittest.TestCase):
    def test_main_board_market_uses_explicit_a_share_prefixes(self):
        for code in ("600000.SH", "601318.SH", "603259.SH", "605499.SH",
                     "000001.SZ", "001289.SZ", "002594.SZ", "003816.SZ"):
            self.assertTrue(screener._market_match(code, "main"), code)
        for code in ("688001.SH", "300001.SZ", "430001.BJ", "900901.SH", "200002.SZ"):
            self.assertFalse(screener._market_match(code, "main"), code)

    @patch("screener.get_snapshot")
    def test_screener_cannot_be_overridden_to_include_non_main_boards(self, snapshot):
        base = {"price": 11, "change_pct": 1, "amount_yi": 20, "turnover": 2,
                "pe_ttm": 18, "pb": 2, "market_cap_yi": 500, "float_cap_yi": 400,
                "amplitude": 2, "volume_ratio": 1.1, "ma60": 10, "ma120": 9, "ma250": 8}
        snapshot.return_value = ([
            {**base, "code": "600001.SH", "name": "主板股"},
            {**base, "code": "688001.SH", "name": "科创股"},
            {**base, "code": "300001.SZ", "name": "创业股"},
        ], "FFD·测试资产", "2026-09-25")
        result = screener.screen({"market": "all", "strict_uptrend": True, "limit": 10})
        self.assertEqual([row["code"] for row in result["results"]], ["600001.SH"])

    def test_trend_features_identify_orderly_medium_term_uptrend(self):
        features = screener._trend_features({
            "price": 11.0, "ma60": 10.0, "ma120": 9.2, "ma250": 8.5,
        })
        self.assertTrue(features["strict_uptrend"])
        self.assertEqual(features["trend_stage"], "强趋势")
        self.assertGreater(features["trend_score"], 80)

    def test_trend_features_penalize_excessive_extension(self):
        orderly = screener._trend_features({
            "price": 11.0, "ma60": 10.0, "ma120": 9.2, "ma250": 8.5,
        })
        overheated = screener._trend_features({
            "price": 15.0, "ma60": 10.0, "ma120": 9.2, "ma250": 8.5,
        })
        self.assertEqual(overheated["trend_stage"], "多头偏热")
        self.assertLess(overheated["trend_score"], orderly["trend_score"])

    @patch("screener.get_snapshot")
    def test_strict_uptrend_filter_requires_full_ma_alignment(self, snapshot):
        snapshot.return_value = ([
            {"code": "600001.SH", "name": "趋势股", "price": 11, "change_pct": 1, "amount_yi": 20,
             "turnover": 2, "pe_ttm": 18, "pb": 2, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 2, "volume_ratio": 1.1, "ma60": 10, "ma120": 9, "ma250": 8},
            {"code": "600002.SH", "name": "反弹股", "price": 11, "change_pct": 5, "amount_yi": 30,
             "turnover": 3, "pe_ttm": 18, "pb": 2, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 8, "volume_ratio": 2.5, "ma60": 10, "ma120": 8, "ma250": 9},
        ], "FFD·测试资产", "2026-09-25")
        result = screener.screen({"profile": "momentum", "strict_uptrend": True, "limit": 10})
        self.assertEqual([row["code"] for row in result["results"]], ["600001.SH"])
        self.assertEqual(result["results"][0]["trend_stage"], "强趋势")

    def test_parse_tencent_batch_fields(self):
        fields = [""] * 53
        fields[1], fields[2], fields[3] = "测试股份", "600001", "12.34"
        fields[32], fields[37], fields[38] = "2.50", "230000", "3.20"
        fields[39], fields[43], fields[44] = "18.0", "4.2", "250.0"
        fields[45], fields[46], fields[49] = "180.0", "2.1", "1.8"
        payload = 'v_sh600001="' + "~".join(fields) + '";'
        rows = screener._parse_tencent_payload(payload)
        self.assertEqual(rows[0]["code"], "600001.SH")
        self.assertEqual(rows[0]["amount_yi"], 23.0)
        self.assertEqual(rows[0]["pe_ttm"], 18.0)

    def test_normalize_ffd_keeps_multi_period_fields_and_units(self):
        row = screener._normalize_ffd_row({
            "ts_code": "000001.SZ", "name": "平安银行", "close": 11.3,
            "amount": 1_200_000_000, "float_market_cap": 220_000_000_000,
            "ma60": 11.1, "ma120": 10.8, "ma250": 10.5,
        })
        self.assertEqual(row["amount_yi"], 12.0)
        self.assertEqual(row["float_cap_yi"], 2200.0)
        self.assertEqual(row["ma250"], 10.5)

    def test_normalize_pit_financial_uses_only_decision_eligible_fields(self):
        item = screener._normalize_ffd_financial_item({
            "stockCode": "600519.SH",
            "reportPeriod": "2026-06-30",
            "values": {"gross_margin": 91.2, "revenue_yoy": 5.5, "net_profit_yoy": 4.2},
            "fieldMetadata": {
                "gross_margin": {"decisionEligible": False, "availableAt": "2026-09-01T00:00:00Z"},
                "revenue_yoy": {"decisionEligible": True, "availableAt": "2026-08-20T00:00:00Z"},
                "net_profit_yoy": {"decisionEligible": True, "availableAt": "2026-08-20T00:00:00Z"},
            },
        })
        self.assertIsNone(item["gross_margin"])
        self.assertEqual(item["revenue_yoy"], 5.5)
        self.assertEqual(item["financial_report_period"], "2026-06-30")

    @patch("screener._load_ffd_financials")
    @patch("screener.get_snapshot")
    def test_financial_quality_enrichment_changes_score_and_reports_coverage(self, snapshot, financials):
        snapshot.return_value = ([
            {"code": "600001.SH", "name": "甲", "price": 10, "change_pct": 1, "amount_yi": 20,
             "turnover": 2, "pe_ttm": 10, "pb": 1, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 2, "volume_ratio": 1.1, "ma60": 9, "ma120": 8, "ma250": 7},
            {"code": "600002.SH", "name": "乙", "price": 10, "change_pct": 1, "amount_yi": 20,
             "turnover": 2, "pe_ttm": 10, "pb": 1, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 2, "volume_ratio": 1.1, "ma60": 9, "ma120": 8, "ma250": 7},
        ], "FFD·测试资产", "2026-09-25")
        financials.return_value = ({
            "600001.SH": {"gross_margin": 50, "revenue_yoy": 20, "net_profit_yoy": 30, "financial_report_period": "2026-06-30"},
            "600002.SH": {"gross_margin": 10, "revenue_yoy": -5, "net_profit_yoy": -8, "financial_report_period": "2026-06-30"},
        }, {"requested": 2, "returned": 2, "report_period": "2026-06-30"})
        result = screener.screen({"profile": "balanced", "use_financial_quality": True, "limit": 10})
        self.assertEqual(result["results"][0]["code"], "600001.SH")
        self.assertGreater(result["results"][0]["factors"]["growth"], result["results"][1]["factors"]["growth"])
        self.assertEqual(result["financial_coverage"]["returned"], 2)

    @patch("screener._load_ffd_financials")
    @patch("screener.get_snapshot")
    def test_financial_filter_fails_closed_when_requested_field_has_zero_coverage(self, snapshot, financials):
        snapshot.return_value = ([
            {"code": "600001.SH", "name": "甲", "price": 10, "change_pct": 1, "amount_yi": 20,
             "turnover": 2, "pe_ttm": 10, "pb": 1, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 2, "volume_ratio": 1.1, "ma60": 9, "ma120": 8, "ma250": 7},
        ], "FFD·测试资产", "2026-09-25")
        financials.return_value = ({
            "600001.SH": {"gross_margin": None, "revenue_yoy": 20, "net_profit_yoy": 30},
        }, {"requested": 1, "returned": 1, "report_period": "2026-06-30"})
        with self.assertRaisesRegex(RuntimeError, "毛利率"):
            screener.screen({
                "profile": "balanced", "use_financial_quality": True,
                "min_gross_margin": 20, "limit": 10,
            })

    @patch("screener.get_snapshot")
    def test_filter_and_factor_ranking_are_deterministic(self, snapshot):
        snapshot.return_value = ([
            {"code": "600001.SH", "name": "价值股", "price": 10, "change_pct": 1, "amount_yi": 20,
             "turnover": 2, "pe_ttm": 8, "pb": 1, "market_cap_yi": 500, "float_cap_yi": 400,
             "amplitude": 2, "volume_ratio": 1.1},
            {"code": "300001.SZ", "name": "成长股", "price": 20, "change_pct": 6, "amount_yi": 30,
             "turnover": 5, "pe_ttm": 60, "pb": 8, "market_cap_yi": 300, "float_cap_yi": 250,
             "amplitude": 9, "volume_ratio": 3.0},
            {"code": "600002.SH", "name": "ST样本", "price": 5, "change_pct": 0, "amount_yi": 1,
             "turnover": 1, "pe_ttm": 12, "pb": 2, "market_cap_yi": 50, "float_cap_yi": 40,
             "amplitude": 3, "volume_ratio": .8},
        ], "FFD·测试资产", "2026-09-25")
        result = screener.screen({"profile": "value", "exclude_st": True, "max_pe": 30, "limit": 10})
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["results"][0]["code"], "600001.SH")
        self.assertEqual(result["source"], "FFD·测试资产")

    @patch("screener._load_ffd_history")
    def test_basket_diagnosis_excludes_incomplete_codes_and_uses_common_dates(self, history):
        dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(100)]
        history.return_value = ({
            "000001.SZ": {d: 20 + i * .2 for i, d in enumerate(dates)},
            "000002.SZ": {},
            "000300.SH": {d: 100 + i * .1 for i, d in enumerate(dates)},
        }, {"status": "complete", "missing_cell_count": 0})
        result = screener.backtest_basket(["000001.SZ", "000002.SZ"], 120)
        self.assertEqual(result["codes"], ["000001.SZ"])
        self.assertEqual(result["skipped"][0]["code"], "000002.SZ")
        self.assertEqual(result["trading_days"], 100)
        self.assertIn("事后选择偏差", result["warning"])


if __name__ == "__main__":
    unittest.main()
