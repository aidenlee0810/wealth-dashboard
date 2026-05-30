"""
tests/test_price_quality.py — Real Price Provider Layer tests (RPL).

UNIT (no DB):
  TestClassification   real/mixed/synthetic/none from source mix + gating helpers
  TestProviderLayer    fetch_price_history dispatcher: provider order, attempts,
                       no-key Alpaca/Tiingo raise FetchError

INTEGRATION (temp DB; synthetic + injected-real prices):
  TestGating           feature_builder valuation gated on FULL-history realism:
                       synthetic → off, mixed → off + distrust, real → ON
  TestHealthRealism    injecting real bars flips all_prices_synthetic=false and
                       lights valuation_populated_count (the phase acceptance)

Run:
    python -m pytest tests/test_price_quality.py -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))


# ────────────────────────────────────────────────────────────────────────────
class TestClassification(unittest.TestCase):
    def setUp(self):
        import price_quality
        self.pq = price_quality

    def test_classification_for_sources(self):
        self.assertEqual(self.pq.classification_for_sources({}), "none")
        self.assertEqual(self.pq.classification_for_sources({"synthetic": 260}), "synthetic")
        self.assertEqual(self.pq.classification_for_sources({"yahoo": 260}), "real")
        # one synthetic bar in 260 real → still real (≥98% floor)
        self.assertEqual(self.pq.classification_for_sources({"yahoo": 259, "synthetic": 1}), "real")
        # meaningful contamination → mixed
        self.assertEqual(self.pq.classification_for_sources({"yahoo": 200, "synthetic": 60}), "mixed")
        self.assertEqual(self.pq.classification_for_sources({"yahoo": 130, "synthetic": 130}), "mixed")

    def test_gating_helpers(self):
        self.assertTrue(self.pq.valuation_allowed("real"))
        self.assertFalse(self.pq.valuation_allowed("mixed"))
        self.assertFalse(self.pq.valuation_allowed("synthetic"))
        self.assertFalse(self.pq.valuation_allowed("none"))
        self.assertEqual(self.pq.technical_reliability("real"), "full")
        self.assertEqual(self.pq.technical_reliability("mixed"), "low")
        self.assertEqual(self.pq.technical_reliability("synthetic"), "none")


# ────────────────────────────────────────────────────────────────────────────
class TestProviderLayer(unittest.TestCase):
    def setUp(self):
        import os
        for k in ("ALPACA_KEY", "ALPACA_SECRET", "TIINGO_KEY"):
            os.environ.pop(k, None)
        from fetch import Fetcher, FetchError
        self.Fetcher = Fetcher
        self.FetchError = FetchError

    def test_provider_order_no_keys(self):
        f = self.Fetcher(synthetic=False, store_raw=False, as_of="2026-05-29")
        self.assertEqual(f.price_provider_order(), ["yahoo"])

    def test_provider_order_with_keys(self):
        import os
        os.environ["ALPACA_KEY"] = "x"; os.environ["ALPACA_SECRET"] = "y"
        os.environ["TIINGO_KEY"] = "z"
        try:
            f = self.Fetcher(synthetic=False, store_raw=False, as_of="2026-05-29")
            self.assertEqual(f.price_provider_order(), ["alpaca", "tiingo", "yahoo"])
            self.assertIn("alpaca", f.limiters)
            self.assertIn("tiingo", f.limiters)
            self.assertIn("alpaca", f.breakers)
            self.assertIn("tiingo", f.breakers)
        finally:
            for k in ("ALPACA_KEY", "ALPACA_SECRET", "TIINGO_KEY"):
                os.environ.pop(k, None)

    def test_synthetic_dispatch_attempts(self):
        f = self.Fetcher(synthetic=True, store_raw=False, as_of="2026-05-29")
        r = f.fetch_price_history("NVDA", range_="3mo")
        self.assertEqual(r["source"], "synthetic")
        self.assertEqual(r["attempts"][0]["provider"], "synthetic")
        self.assertTrue(r["attempts"][0]["ok"])

    def test_alpaca_no_key_raises(self):
        f = self.Fetcher(synthetic=False, store_raw=False, as_of="2026-05-29")
        with self.assertRaises(self.FetchError):
            f._alpaca_bars("AAPL", "1y")

    def test_tiingo_no_key_raises(self):
        f = self.Fetcher(synthetic=False, store_raw=False, as_of="2026-05-29")
        with self.assertRaises(self.FetchError):
            f._tiingo_bars("AAPL", "1y")


# ────────────────────────────────────────────────────────────────────────────
class _TempDB(unittest.TestCase):
    MD = "2026-05-29"

    def setUp(self):
        import db
        self._db = db
        self._tmp = tempfile.mkdtemp()
        self._orig = db.CLOUD_DB_PATH
        db.CLOUD_DB_PATH = Path(self._tmp) / "cloud.sqlite"
        db.apply_migrations("cloud")

    def tearDown(self):
        self._db.CLOUD_DB_PATH = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed_prices(self, ticker, n=80, real_n=None, base=100.0):
        """n bars ending at MD; first real_n are 'yahoo', rest 'synthetic'.
        real_n=None → all real; real_n=0 → all synthetic."""
        from market_calendar import previous_trading_day
        if real_n is None:
            real_n = n
        with self._db.cloud() as c:
            d = self.MD
            dates = []
            for _ in range(n):
                dates.append(d)
                d = previous_trading_day(d).isoformat()
            dates.reverse()
            for i, dt in enumerate(dates):
                px = base * (1 + 0.002 * i)
                src = "yahoo" if i < real_n else "synthetic"
                c.execute("INSERT OR REPLACE INTO prices_daily(date,ticker,open,high,low,"
                          "close,adj_close,volume,source) VALUES (?,?,?,?,?,?,?,?,?)",
                          (dt, ticker, px, px * 1.01, px * 0.99, px, px, 5_000_000, src))

    def _seed_fundamentals(self, ticker):
        with self._db.cloud() as c:
            self._db.upsert(c, "ticker_master", {
                "ticker": ticker, "sector": "Information Technology",
                "model_type": "operating_company"}, conflict_cols=("ticker",))
            # usable_at must be a plain date <= feature_date as a RAW string
            # (get_fundamentals compares usable_at<=usable_before); a 'D 00:00Z'
            # timestamp would sort greater than the plain date and be skipped.
            ua = "2026-05-28"
            self._db.upsert(c, "cleaned_financials", {
                "ticker": ticker, "fiscal_period": "2026-Q1-TTM", "source": "sec",
                "usable_at": ua, "report_date": ua,
                "revenue": 1e11, "net_income": 2e10, "fcf": 2.5e10,
                "total_equity": 5e10, "shares_out": 1e9, "operating_income": 3e10,
                "cfo": 2.8e10}, conflict_cols=("ticker", "fiscal_period", "source"))
            self._db.upsert(c, "normalized_financials", {
                "ticker": ticker, "fiscal_period": "2026-Q1-TTM",
                "usable_at": ua, "model_type": "operating_company",
                "roic": 0.20, "roe": 0.18, "gross_margin": 0.6, "operating_margin": 0.3,
                "net_margin": 0.2, "fcf_margin": 0.25, "revenue_growth_yoy": 0.25,
                "coverage_ratio": 90.0}, conflict_cols=("ticker", "fiscal_period"))


# ────────────────────────────────────────────────────────────────────────────
class TestGating(_TempDB):
    def _score(self, ticker):
        import feature_builder as fb
        return fb.build_features([ticker], feature_date=self.MD)

    def test_real_history_enables_valuation(self):
        self._seed_prices("REAL", n=80, real_n=80)      # all yahoo
        self._seed_fundamentals("REAL")
        out = self._score("REAL")
        self.assertEqual(out["by_price_class"].get("real"), 1)
        self.assertEqual(out["valuation_enabled"], 1)
        self.assertEqual(out["valuation_na_synthetic"], 0)

    def test_synthetic_history_blocks_valuation(self):
        self._seed_prices("SYN", n=80, real_n=0)        # all synthetic
        self._seed_fundamentals("SYN")
        out = self._score("SYN")
        self.assertEqual(out["by_price_class"].get("synthetic"), 1)
        self.assertEqual(out["valuation_enabled"], 0)
        self.assertEqual(out["valuation_na_synthetic"], 1)

    def test_mixed_history_blocks_valuation_and_flags(self):
        self._seed_prices("MIX", n=80, real_n=40)       # half real / half synthetic
        self._seed_fundamentals("MIX")
        out = self._score("MIX")
        self.assertEqual(out["by_price_class"].get("mixed"), 1)
        self.assertEqual(out["valuation_enabled"], 0)   # mixed is NOT trusted
        self.assertEqual(out["mixed_price_history"], 1)


# ────────────────────────────────────────────────────────────────────────────
class TestHealthRealism(_TempDB):
    """The phase acceptance: real bars flip all_prices_synthetic + valuation count."""

    def test_health_reflects_real_prices(self):
        import feature_builder as fb
        from jobs import views_builder
        # one real ticker (valuation should populate) + one synthetic ticker
        self._seed_prices("REAL", n=80, real_n=80)
        self._seed_fundamentals("REAL")
        self._seed_prices("SYN", n=80, real_n=0)
        self._seed_fundamentals("SYN")
        fb.build_features(["REAL", "SYN"], feature_date=self.MD)

        meta = {"snapshot_date": self.MD, "executed_at_utc": self.MD}
        with self._db.cloud(readonly=True) as conn:
            health = views_builder._snapshot_health(conn, self.MD, meta)
        dr = health["data_realism"]
        self.assertFalse(dr["all_prices_synthetic"])        # mixed corpus now has real bars
        self.assertIn("yahoo", dr["price_sources"])
        self.assertEqual(dr["price_quality"]["real"], 1)
        self.assertEqual(dr["price_quality"]["synthetic"], 1)
        self.assertGreaterEqual(dr["valuation_populated_count"], 1)


# ────────────────────────────────────────────────────────────────────────────
class _MockFetcher:
    """Stand-in for Fetcher that returns canned real bars (no network/keys)."""

    def __init__(self, source="alpaca", n=250, fail_providers=None):
        self.source = source
        self.n = n
        self.fail_providers = fail_providers or []

    def fetch_price_history(self, ticker, range_="2y"):
        from fetch import Bar
        from market_calendar import previous_trading_day
        attempts = [{"provider": p, "ok": False, "reason": "HTTP 429"}
                    for p in self.fail_providers]
        if self.source is None:
            return {"bars": [], "splits": [], "source": None, "attempts": attempts}
        d = "2026-05-29"
        bars = []
        for i in range(self.n):
            px = 100.0 * (1 + 0.001 * i)
            bars.append(Bar(date=d, open=px, high=px * 1.01, low=px * 0.99,
                            close=px, adj_close=px, volume=4_000_000))
            d = previous_trading_day(d).isoformat()
        bars.reverse()
        attempts.append({"provider": self.source, "ok": True, "reason": None, "bars": len(bars)})
        return {"bars": bars, "splits": [], "source": self.source, "attempts": attempts}


class TestRefreshPrices(_TempDB):
    """refresh_prices replaces synthetic history with real bars — no silent mix."""

    def test_replace_flips_synthetic_to_real_and_enables_valuation(self):
        import refresh_prices as rp
        import feature_builder as fb
        self._seed_prices("NVDA", n=120, real_n=0)          # all synthetic
        self._seed_fundamentals("NVDA")
        # before: synthetic, valuation off
        pre = fb.build_features(["NVDA"], feature_date=self.MD)
        self.assertEqual(pre["valuation_enabled"], 0)

        out = rp.refresh_prices(["NVDA"], fetcher=_MockFetcher("alpaca", 250),
                                market_date=self.MD, replace_synthetic=True)
        self.assertEqual(out["refreshed"], 1)
        self.assertEqual(out["per_ticker"]["NVDA"]["classification"], "real")
        self.assertGreater(out["per_ticker"]["NVDA"]["deleted_synthetic"], 0)

        with self._db.cloud(readonly=True) as c:
            cls = self._price_class(c, "NVDA")
        self.assertEqual(cls, "real")
        # after: valuation turns ON (real history)
        post = fb.build_features(["NVDA"], feature_date=self.MD)
        self.assertEqual(post["by_price_class"].get("real"), 1)
        self.assertEqual(post["valuation_enabled"], 1)

    def test_append_leaves_mixed_keeps_valuation_off(self):
        import refresh_prices as rp
        import feature_builder as fb
        # 120 synthetic bars on dates the 60-bar real fetch won't fully cover
        self._seed_prices("AMD", n=120, real_n=0)
        self._seed_fundamentals("AMD")
        out = rp.refresh_prices(["AMD"], fetcher=_MockFetcher("alpaca", 60),
                                market_date=self.MD, replace_synthetic=False,
                                min_bars=50)
        # 60 real bars overwrite 60 synthetic dates; 60 synthetic remain → mixed
        self.assertEqual(out["per_ticker"]["AMD"]["classification"], "mixed")
        post = fb.build_features(["AMD"], feature_date=self.MD)
        self.assertEqual(post["by_price_class"].get("mixed"), 1)
        self.assertEqual(post["valuation_enabled"], 0)      # mixed → valuation OFF
        self.assertEqual(post["mixed_price_history"], 1)

    def test_skip_when_source_not_real(self):
        import refresh_prices as rp
        self._seed_prices("SYN", n=120, real_n=0)
        out = rp.refresh_prices(["SYN"], fetcher=_MockFetcher(None),  # all providers failed
                                market_date=self.MD)
        self.assertEqual(out["refreshed"], 0)
        self.assertEqual(out["skipped_no_real"], 1)
        self.assertEqual(out["per_ticker"]["SYN"]["action"], "skipped")

    def test_skip_when_too_few_bars(self):
        import refresh_prices as rp
        self._seed_prices("X", n=120, real_n=0)
        out = rp.refresh_prices(["X"], fetcher=_MockFetcher("alpaca", 50),
                                market_date=self.MD, min_bars=200)
        self.assertEqual(out["refreshed"], 0)
        self.assertIn("too_few_bars", out["per_ticker"]["X"]["reason"])

    def test_provider_failures_captured(self):
        import refresh_prices as rp
        self._seed_prices("T", n=120, real_n=0)
        out = rp.refresh_prices(
            ["T"], fetcher=_MockFetcher("tiingo", 250, fail_providers=["alpaca"]),
            market_date=self.MD)
        self.assertIn("alpaca", out["provider_failures"])
        self.assertEqual(out["per_ticker"]["T"]["source"], "tiingo")

    def _price_class(self, conn, ticker):
        import price_quality
        return price_quality.classify_history(conn, ticker, self.MD)["classification"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
