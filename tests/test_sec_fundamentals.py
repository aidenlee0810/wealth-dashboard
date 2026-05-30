"""
tests/test_sec_fundamentals.py — on-demand SEC fundamentals (#2, no network).

The real path hits SEC EDGAR; here we mock Fetcher.fetch_sec_companyfacts so the
shaping/ratio logic is tested deterministically:
  - guards (no ticker / no SEC data)
  - fcf = cfo - capex, ROIC/margins computed, coverage_ratio
  - P-multiples only when a price is supplied

Run:
    python -m pytest tests/test_sec_fundamentals.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

import sec_fundamentals as sf  # noqa: E402

REC = {
    "source": "sec", "fiscal_period": "2025-Q4-TTM", "report_date": "2026-01-30",
    "usable_at": "2026-01-30", "model_type": "operating_company",
    "revenue": 1000.0, "revenue_prior": 900.0, "gross_profit": 400.0,
    "operating_income": 250.0, "net_income": 200.0, "cfo": 300.0, "capex": 50.0,
    "sbc": 20.0, "total_debt": 100.0, "cash_and_sti": 80.0, "total_equity": 500.0,
    "shares_out": 100.0, "interest_expense": 10.0, "ebitda": 300.0,
}


def _patch(rec):
    import fetch
    return mock.patch.object(fetch.Fetcher, "fetch_sec_companyfacts", return_value=rec)


class TestGuards(unittest.TestCase):
    def test_no_ticker(self):
        out = sf.compute_for_ticker("")
        self.assertFalse(out["available"])
        self.assertEqual(out["reason"], "no_ticker")

    def test_no_sec_data(self):
        with _patch(None):
            out = sf.compute_for_ticker("ZZZZ")
        self.assertFalse(out["available"])
        self.assertEqual(out["reason"], "no_sec_data")


class TestCompute(unittest.TestCase):
    def test_ratios_and_coverage(self):
        with _patch(dict(REC)):
            out = sf.compute_for_ticker("TEST")
        self.assertTrue(out["available"])
        self.assertEqual(out["source"], "sec")
        self.assertAlmostEqual(out["fcf"], 250.0)                 # cfo - capex
        self.assertAlmostEqual(out["gross_margin"], 0.4)          # 400/1000
        self.assertAlmostEqual(out["net_margin"], 0.2)            # 200/1000
        self.assertIsNotNone(out["roic"])
        self.assertGreater(out["roic"], 0)
        self.assertEqual(out["coverage_ratio"], 100.0)           # all 8 core inputs present
        # no price → no multiples
        self.assertNotIn("pe_ttm", out)

    def test_multiples_with_price(self):
        with _patch(dict(REC)):
            out = sf.compute_for_ticker("TEST", price=20.0)
        # mc = 20 * 100 = 2000
        self.assertAlmostEqual(out["pe_ttm"], 10.0)              # 2000 / 200
        self.assertAlmostEqual(out["ps_ttm"], 2.0)               # 2000 / 1000
        self.assertAlmostEqual(out["pfcf_ttm"], 8.0)            # 2000 / 250
        self.assertAlmostEqual(out["pb_ttm"], 4.0)             # 2000 / 500

    def test_partial_coverage(self):
        rec = dict(REC)
        for k in ("cfo", "capex", "total_debt"):                # drop 3 of 8 core
            rec[k] = None
        with _patch(rec):
            out = sf.compute_for_ticker("TEST")
        self.assertLess(out["coverage_ratio"], 100.0)
        self.assertIsNone(out["fcf"])                            # can't compute w/o cfo/capex


if __name__ == "__main__":
    unittest.main()
