"""
tests/test_universe.py — Phase 2 Dynamic Universe + Data Contracts tests

Test categories:
  UNIT (always run, no DB/network):
    TestDataFiles          — JSON layer files parse + structural integrity
    TestMerge              — 6-layer merge logic (union, source buckets, dedup)
    TestScoring            — discovery_score + canonical_source priority
    TestContracts          — API schema validation (valid passes, drift caught)
    TestScrapeParser       — Wikipedia wikitable parser on synthetic HTML

  CONDITIONAL:
    TestPersistenceIntegration — days_active persistence (uses a temp DB)

Run:
    python -m pytest tests/test_universe.py -v
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

DATA_DIR = PROJECT_ROOT / "data"


# ────────────────────────────────────────────────────────────────────────────
class TestDataFiles(unittest.TestCase):
    """Layer files exist, parse, and meet minimum structural requirements."""

    def _load(self, name):
        path = DATA_DIR / name
        self.assertTrue(path.exists(), f"{name} missing — run jobs/scrape_indices.py / create layer files")
        return json.loads(path.read_text())

    def test_sp500_structure(self):
        d = self._load("sp500.json")
        self.assertGreaterEqual(d["count"], 450, "S&P 500 should have >= 450 constituents")
        self.assertLessEqual(d["count"], 520)
        tickers = {c["ticker"] for c in d["constituents"]}
        for must in ["AAPL", "MSFT", "NVDA", "BRK.B"]:
            self.assertIn(must, tickers, f"{must} missing from S&P 500")
        # Every constituent has a ticker + name
        for c in d["constituents"][:50]:
            self.assertTrue(c.get("ticker"))
            self.assertTrue(c.get("name"))

    def test_nasdaq100_structure(self):
        d = self._load("nasdaq100.json")
        self.assertGreaterEqual(d["count"], 90)
        self.assertLessEqual(d["count"], 110)
        tickers = {c["ticker"] for c in d["constituents"]}
        self.assertIn("AAPL", tickers)
        self.assertIn("NVDA", tickers)

    def test_theme_baskets_structure(self):
        d = self._load("theme_baskets.json")
        themes = d["themes"]
        self.assertGreaterEqual(len(themes), 15, "should have >= 15 themes")
        for tid, t in themes.items():
            self.assertTrue(t.get("name_ko"), f"theme {tid} missing name_ko")
            self.assertGreaterEqual(len(t["tickers"]), 5, f"theme {tid} has < 5 tickers")
            # Leaders must be in the basket
            for leader in t.get("leaders", []):
                self.assertIn(leader, t["tickers"],
                              f"leader {leader} not in theme {tid} basket")

    def test_sector_holdings_structure(self):
        d = self._load("sector_holdings.json")
        sectors = d["sectors"]
        self.assertEqual(len(sectors), 11, "should have 11 SPDR sectors")
        for etf, meta in sectors.items():
            self.assertTrue(meta.get("name_ko"))
            self.assertTrue(meta.get("model_type"))
            self.assertGreaterEqual(len(meta["holdings"]), 5)

    def test_core_watchlist_structure(self):
        d = self._load("core_watchlist.json")
        tickers = d["tickers"]
        self.assertGreaterEqual(len(tickers), 40)
        for t, meta in tickers.items():
            self.assertIn(meta["model_type"],
                          ["operating_company", "bank", "reit", "biotech_pre_revenue",
                           "etf", "leveraged_etf"],
                          f"{t} has invalid model_type {meta.get('model_type')}")


# ────────────────────────────────────────────────────────────────────────────
class TestMerge(unittest.TestCase):
    """6-layer merge logic from jobs/universe_builder.py."""

    @classmethod
    def setUpClass(cls):
        import universe_builder as ub
        cls.ub = ub
        cls.layers = ub.load_layers()
        cls.entries = ub.merge_universe(cls.layers)

    def test_union_size(self):
        """Union of all layers must exceed the Phase 2 acceptance threshold of 150."""
        self.assertGreaterEqual(len(self.entries), 150,
                                f"universe has only {len(self.entries)} tickers (need >= 150)")

    def test_multi_source_ticker(self):
        """NVDA must appear in multiple sources with correct buckets."""
        nvda = self.entries.get("NVDA")
        self.assertIsNotNone(nvda, "NVDA missing from universe")
        self.assertIn("sp500", nvda.sources)
        self.assertIn("core_watchlist", nvda.sources)
        self.assertIn("theme_basket", nvda.sources)
        self.assertGreaterEqual(len(nvda.sources), 4)
        # source_buckets includes theme:* and sector:* granular tags
        self.assertTrue(any(b.startswith("theme:") for b in nvda.source_buckets))

    def test_no_duplicate_tickers(self):
        """merge_universe returns a dict keyed by ticker — inherently deduped."""
        all_tickers = list(self.entries.keys())
        self.assertEqual(len(all_tickers), len(set(all_tickers)))

    def test_core_metadata_wins(self):
        """core_watchlist model_type overrides sector-derived default."""
        # SOFI is in core_watchlist as model_type=bank
        sofi = self.entries.get("SOFI")
        if sofi:
            self.assertEqual(sofi.model_type, "bank",
                             "core_watchlist model_type should win over sector default")

    def test_etf_detection(self):
        """VUG/SCHG should be classified as ETFs."""
        for etf in ["VUG", "SCHG"]:
            e = self.entries.get(etf)
            if e:
                self.assertEqual(e.model_type, "etf", f"{etf} should be model_type=etf")

    def test_sector_assignment(self):
        """Every ticker should have a GICS sector after merge."""
        no_sector = [t for t, e in self.entries.items() if not e.gics_sector]
        # Allow a tiny tail (newly added theme-only tickers w/o sector ETF), but most should map
        self.assertLessEqual(len(no_sector), len(self.entries) * 0.1,
                             f"{len(no_sector)} tickers without sector: {no_sector[:10]}")


# ────────────────────────────────────────────────────────────────────────────
class TestScoring(unittest.TestCase):
    """discovery_score + canonical_source pure functions."""

    @classmethod
    def setUpClass(cls):
        import universe_builder as ub
        cls.ub = ub

    def _entry(self, **kw):
        e = self.ub.TickerEntry(kw.get("ticker", "TEST"))
        for k, v in kw.items():
            if k == "sources":
                e.sources = set(v)
            elif hasattr(e, k):
                setattr(e, k, v)
        return e

    def test_discovery_score_multi_source(self):
        """A ticker in 5 sources + core + theme leader should score near 100."""
        e = self._entry(sources=["sp500", "nasdaq100", "sector_holdings", "theme_basket", "core_watchlist"],
                        theme_leader=True, is_index_nasdaq100=True)
        score = self.ub.discovery_score(e)
        self.assertGreaterEqual(score, 90)
        self.assertLessEqual(score, 100)

    def test_discovery_score_single_source(self):
        """A ticker only in S&P 500 should score low."""
        e = self._entry(sources=["sp500"])
        score = self.ub.discovery_score(e)
        self.assertLessEqual(score, 30)

    def test_canonical_source_priority(self):
        """core_watchlist should win over sp500 as canonical source."""
        e = self._entry(sources=["sp500", "core_watchlist", "theme_basket"])
        self.assertEqual(self.ub.canonical_source(e), "core_watchlist")
        e2 = self._entry(sources=["sp500", "nasdaq100"])
        self.assertEqual(self.ub.canonical_source(e2), "nasdaq100")


# ────────────────────────────────────────────────────────────────────────────
class TestContracts(unittest.TestCase):
    """Data contract validation (§23)."""

    def test_finnhub_quote_valid(self):
        from contracts.finnhub_quote import FinnhubQuote
        ok, errs, _ = FinnhubQuote.validate(
            {"c": 142.3, "h": 144.1, "l": 140.0, "o": 141.0, "pc": 140.5, "t": 1716000000})
        self.assertTrue(ok, f"valid quote rejected: {errs}")

    def test_finnhub_quote_high_low_inverted(self):
        from contracts.finnhub_quote import FinnhubQuote
        ok, errs, _ = FinnhubQuote.validate(
            {"c": 142.3, "h": 140.0, "l": 144.1, "o": 141.0, "pc": 140.5, "t": 1716000000})
        self.assertFalse(ok, "h<l should be rejected")

    def test_finnhub_quote_negative_price(self):
        from contracts.finnhub_quote import FinnhubQuote
        ok, errs, _ = FinnhubQuote.validate(
            {"c": -5, "h": 144.1, "l": 140.0, "o": 141.0, "pc": 140.5, "t": 1716000000})
        self.assertFalse(ok, "negative price should be rejected")

    def test_finnhub_quote_schema_drift(self):
        """Missing required fields (upstream removed a field) must be caught."""
        from contracts.finnhub_quote import FinnhubQuote
        ok, errs, _ = FinnhubQuote.validate({"c": 142.3, "h": 144.1})
        self.assertFalse(ok)
        self.assertGreaterEqual(len(errs), 3)

    def test_finnhub_candle_length_mismatch(self):
        from contracts.finnhub_candle import FinnhubCandle
        ok, errs, _ = FinnhubCandle.validate(
            {"s": "ok", "t": [1, 2, 3], "o": [1], "h": [2], "l": [1], "c": [1.5], "v": [100]})
        self.assertFalse(ok, "array length mismatch should be caught")

    def test_finnhub_candle_no_data(self):
        from contracts.finnhub_candle import FinnhubCandle
        ok, _, _ = FinnhubCandle.validate({"s": "no_data"})
        self.assertTrue(ok, "no_data is a valid empty response")

    def test_fred_series_valid(self):
        from contracts.fred_series import FredSeries
        ok, errs, _ = FredSeries.validate(
            {"observations": [{"date": "2026-05-01", "value": "4.25"}], "count": 1})
        self.assertTrue(ok, f"valid FRED rejected: {errs}")

    def test_fred_series_empty(self):
        from contracts.fred_series import FredSeries
        ok, _, _ = FredSeries.validate({"observations": []})
        self.assertFalse(ok, "empty observations should be rejected")

    def test_yahoo_chart_valid(self):
        from contracts.yahoo_chart import YahooChart
        ok, errs, _ = YahooChart.validate({"chart": {"error": None, "result": [
            {"timestamp": [1, 2], "indicators": {"quote": [
                {"open": [1, 2], "high": [2, 3], "low": [1, 1], "close": [1.5, 2.5]}]}}]}})
        self.assertTrue(ok, f"valid Yahoo rejected: {errs}")

    def test_yahoo_chart_error_set(self):
        from contracts.yahoo_chart import YahooChart
        ok, _, _ = YahooChart.validate({"chart": {"error": "Not Found", "result": None}})
        self.assertFalse(ok, "chart.error set should be caught")

    def test_schema_versions_present(self):
        from contracts.finnhub_quote import FinnhubQuote
        from contracts.finnhub_candle import FinnhubCandle
        from contracts.fred_series import FredSeries
        from contracts.yahoo_chart import YahooChart
        for c in (FinnhubQuote, FinnhubCandle, FredSeries, YahooChart):
            self.assertRegex(c.SCHEMA_VERSION, r"^\d+\.\d+\.\d+$",
                             f"{c.__name__} missing semver SCHEMA_VERSION")


# ────────────────────────────────────────────────────────────────────────────
class TestScrapeParser(unittest.TestCase):
    """Wikipedia wikitable parser on synthetic HTML (no network)."""

    def test_wikitable_parser(self):
        from scrape_indices import WikiTableParser, _clean_ticker
        html = """
        <table class="wikitable sortable" id="constituents">
          <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
          <tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>
          <tr><td>JPM</td><td>JPMorgan Chase</td><td>Financials</td></tr>
        </table>
        """
        p = WikiTableParser(target_table_id="constituents")
        p.feed(html)
        self.assertEqual(len(p.rows), 3, "should parse header + 2 data rows")
        self.assertEqual(p.rows[1][0], "AAPL")
        self.assertEqual(p.rows[2][1], "JPMorgan Chase")

    def test_clean_ticker(self):
        from scrape_indices import _clean_ticker
        self.assertEqual(_clean_ticker("AAPL"), "AAPL")
        self.assertEqual(_clean_ticker("brk.b"), "BRK.B")
        self.assertEqual(_clean_ticker("AAPL[1]"), "AAPL")


# ────────────────────────────────────────────────────────────────────────────
class TestPersistenceIntegration(unittest.TestCase):
    """days_active persistence using an isolated temp DB."""

    def test_persistence_increments(self):
        """Build twice across two dates → days_active increments to 2."""
        import universe_builder as ub
        import db

        # Use a temp cloud DB by monkeypatching the path
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        orig_path = db.CLOUD_DB_PATH
        try:
            db.CLOUD_DB_PATH = Path(tmpdir) / "test_cloud.sqlite"
            db.apply_migrations("cloud")

            r1 = ub.build(build_date="2026-01-05")
            self.assertGreaterEqual(r1["total_tickers"], 150)
            self.assertEqual(r1["new_tickers"], r1["total_tickers"],
                             "first build: all tickers should be new")

            r2 = ub.build(build_date="2026-01-06")
            self.assertEqual(r2["new_tickers"], 0, "second day: no new tickers")
            self.assertEqual(r2["seen_again"], r2["total_tickers"],
                             "second day: all seen again")

            # Verify days_active == 2 for a known ticker
            with db.cloud(readonly=True) as conn:
                row = conn.execute(
                    "SELECT days_active FROM universe_membership "
                    "WHERE ticker='NVDA' AND date='2026-01-06'").fetchone()
                self.assertEqual(row["days_active"], 2)
        finally:
            db.CLOUD_DB_PATH = orig_path
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
