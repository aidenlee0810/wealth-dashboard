"""
tests/test_dividend_yield.py — dividend yield in latest_fundamentals view (§33).

Integration: a temp cloud DB (real migrations) holding one operating company with
SEC financials, a real price, and four trailing quarterly dividends. Asserts the
view exposes a per-share TTM dividend and a yield = TTM / price — and that a
synthetic price suppresses the yield (but keeps the price-independent per-share
figure) so the frontend can recompute against a live quote.

Run:
    python -m pytest tests/test_dividend_yield.py -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

import db                       # noqa: E402
import views_builder            # noqa: E402

MD = "2026-05-29"


def _seed(conn, *, price=200.0, price_source="alpaca", with_divs=True, ticker="AAPL"):
    conn.execute(
        "INSERT INTO cleaned_financials (ticker, fiscal_period, report_date, usable_at, "
        "revenue, net_income, fcf, cfo, operating_income, shares_out, total_equity, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticker, "2026-Q1", "2026-04-20", "2026-05-01",
         100e9, 25e9, 20e9, 25e9, 30e9, 15e9, 60e9, "sec"))
    conn.execute(
        "INSERT INTO normalized_financials (ticker, fiscal_period, usable_at, sector, "
        "model_type, roic, gross_margin, coverage_ratio) VALUES (?,?,?,?,?,?,?,?)",
        (ticker, "2026-Q1", "2026-05-01", "Technology", "operating_company",
         0.30, 0.45, 100.0))
    conn.execute(
        "INSERT INTO prices_daily (date, ticker, close, adj_close, source) VALUES (?,?,?,?,?)",
        ("2026-05-28", ticker, price, price, price_source))
    if with_divs:
        # four quarterly ex-dates all inside the trailing 12 months of MD
        for ex in ("2026-05-10", "2026-02-10", "2025-11-10", "2025-08-10"):
            conn.execute(
                "INSERT INTO corporate_actions (ticker, ex_date, action_type, dividend, source) "
                "VALUES (?,?,?,?,?)", (ticker, ex, "dividend", 0.25, "alpaca"))
    conn.commit()


class TestDividendYieldView(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = db.CLOUD_DB_PATH
        db.CLOUD_DB_PATH = Path(self._tmp) / "cloud.sqlite"
        db.apply_migrations("cloud")
        # isolate from health-score internals — irrelevant to dividend math
        self._patch = mock.patch.object(views_builder.health_scores, "compute_all",
                                        return_value={})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        db.CLOUD_DB_PATH = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _build(self):
        with db.cloud() as conn:
            return views_builder._latest_fundamentals(conn, MD, {})["fundamentals"]

    def test_yield_from_real_price(self):
        with db.cloud() as conn:
            _seed(conn, price=200.0, price_source="alpaca")
        item = self._build()["AAPL"]
        self.assertAlmostEqual(item["ttm_dividend"], 1.0, places=4)     # 4 × 0.25
        self.assertAlmostEqual(item["dividend_yield"], round(1.0 / 200.0, 4))  # 0.005

    def test_synthetic_price_suppresses_yield_keeps_pershare(self):
        with db.cloud() as conn:
            _seed(conn, price=200.0, price_source="synthetic")
        item = self._build()["AAPL"]
        self.assertAlmostEqual(item["ttm_dividend"], 1.0, places=4)     # price-independent
        self.assertIsNone(item["dividend_yield"])                       # gated on synthetic

    def test_no_dividends(self):
        with db.cloud() as conn:
            _seed(conn, with_divs=False)
        item = self._build()["AAPL"]
        self.assertIsNone(item["ttm_dividend"])
        self.assertIsNone(item["dividend_yield"])


if __name__ == "__main__":
    unittest.main()
