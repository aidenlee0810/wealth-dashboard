"""
tests/test_snapshot.py — Phase 3 daily-snapshot pipeline tests.

UNIT (no DB/network):
  TestMarketCalendar   NYSE holidays, weekends, trading-day arithmetic
  TestTechnicalMath    sma / pct_return / rsi / compute_tech / compute_dq
  TestFetchSynthetic   deterministic synthetic bars (window-invariant) + FRED

INTEGRATION (isolated temp cloud DB; still no network — synthetic Fetcher):
  TestPrices           upsert, idempotency, no future bars
  TestReconciliation   pass + reject paths, reconciliation_log
  TestFeatures         features_daily write + Stage-1 active_score blend
  TestOutcomes         5D outcome, rel_spy, MAE/MFE/MDD, no-lookahead
  TestEndToEnd         full synthetic snapshot success + idempotency

Run:
    python -m pytest tests/test_snapshot.py -v
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))


# ────────────────────────────────────────────────────────────────────────────
class TestMarketCalendar(unittest.TestCase):
    def setUp(self):
        import market_calendar as cal
        self.cal = cal

    def test_holiday_memorial_day(self):
        # 2026-05-25 Memorial Day → closed
        self.assertFalse(self.cal.is_trading_day("2026-05-25"))
        self.assertEqual(self.cal.market_status("2026-05-25"), "holiday")

    def test_weekend(self):
        self.assertFalse(self.cal.is_trading_day("2026-05-30"))  # Saturday
        self.assertEqual(self.cal.market_status("2026-05-31"), "weekend")  # Sunday

    def test_regular_open_day(self):
        self.assertTrue(self.cal.is_trading_day("2026-05-28"))  # Thursday
        self.assertEqual(self.cal.market_status("2026-05-28"), "open")

    def test_previous_trading_day_skips_holiday(self):
        # Day after Memorial Day is Tue 05-26; its previous trading day is Fri 05-22
        self.assertEqual(self.cal.previous_trading_day("2026-05-26").isoformat(), "2026-05-22")

    def test_trading_days_between(self):
        # 05-28(Thu)→06-04(Thu): 05-29,06-01,06-02,06-03,06-04 = 5
        self.assertEqual(self.cal.trading_days_between("2026-05-28", "2026-06-04"), 5)

    def test_half_day_detection(self):
        self.assertTrue(self.cal.is_half_day("2026-11-27"))  # day after Thanksgiving
        self.assertTrue(self.cal.is_trading_day("2026-11-27"))  # still a trading day

    def test_most_recent_trading_day(self):
        self.assertEqual(self.cal.most_recent_trading_day("2026-05-30").isoformat(), "2026-05-29")


# ────────────────────────────────────────────────────────────────────────────
class TestTechnicalMath(unittest.TestCase):
    def setUp(self):
        import feature_builder as fb
        self.fb = fb

    def test_sma(self):
        self.assertEqual(self.fb.sma([1, 2, 3, 4, 5], 5), 3.0)
        self.assertIsNone(self.fb.sma([1, 2], 5))

    def test_pct_return(self):
        self.assertAlmostEqual(self.fb.pct_return([100, 110], 1), 0.10)
        self.assertIsNone(self.fb.pct_return([100], 1))

    def test_rsi_all_gains_is_100(self):
        rising = [float(i) for i in range(1, 30)]
        self.assertEqual(self.fb.rsi(rising, 14), 100.0)

    def test_rsi_midrange(self):
        import random
        rng = random.Random(7)
        series = [100.0]
        for _ in range(60):
            series.append(series[-1] * (1 + rng.gauss(0, 0.01)))
        r = self.fb.rsi(series, 14)
        self.assertTrue(0 <= r <= 100)

    def test_compute_tech_uptrend(self):
        # Monotonic uptrend → above SMAs, positive momentum → high tech score
        closes = [float(100 + i) for i in range(260)]
        highs = [c * 1.01 for c in closes]
        vols = [1_000_000] * 260
        out = self.fb.compute_tech(closes, highs, vols)
        self.assertGreaterEqual(out["tech_score"], 70)
        self.assertIn(out["state"], ("UPTREND", "BREAKOUT"))
        self.assertGreater(out["price_vs_sma50"], 0)

    def test_compute_tech_downtrend(self):
        closes = [float(360 - i) for i in range(260)]  # falling
        highs = [c * 1.01 for c in closes]
        vols = [1_000_000] * 260
        out = self.fb.compute_tech(closes, highs, vols)
        self.assertLessEqual(out["tech_score"], 35)
        self.assertEqual(out["state"], "DOWNTREND")

    def test_compute_dq_short_history_penalised(self):
        full = self.fb.compute_dq(260, "2026-05-28", "2026-05-28")
        ipo = self.fb.compute_dq(15, "2026-05-28", "2026-05-28")
        self.assertEqual(full, 100.0)
        self.assertLess(ipo, full)


# ────────────────────────────────────────────────────────────────────────────
class TestFetchSynthetic(unittest.TestCase):
    def setUp(self):
        from fetch import Fetcher
        self.f = Fetcher(synthetic=True, store_raw=False, as_of="2026-05-28")

    def test_bars_deterministic(self):
        a = self.f.fetch_yahoo_chart("NVDA", range_="3mo")
        b = self.f.fetch_yahoo_chart("NVDA", range_="3mo")
        self.assertEqual([x.close for x in a], [x.close for x in b])
        self.assertGreater(len(a), 40)

    def test_window_invariant_price(self):
        """Last close must match regardless of requested window length —
        critical so price-history and quote 'sources' agree (reconciliation)."""
        short = self.f.fetch_yahoo_chart("AAPL", range_="1mo")
        long = self.f.fetch_yahoo_chart("AAPL", range_="1y")
        self.assertEqual(short[-1].close, long[-1].close)
        self.assertEqual(short[-1].date, long[-1].date)

    def test_no_future_bars(self):
        bars = self.f.fetch_yahoo_chart("MSFT", range_="1y")
        self.assertLessEqual(bars[-1].date, "2026-05-28")

    def test_quote_matches_history(self):
        hist = self.f.fetch_yahoo_chart("NVDA", range_="1y")
        q = self.f.fetch_finnhub_quote("NVDA")
        self.assertAlmostEqual(hist[-1].close, q["c"], places=2)

    def test_fred_synthetic(self):
        obs = self.f.fetch_fred_series("DGS10")
        self.assertGreater(len(obs), 20)
        self.assertTrue(all("date" in o and "value" in o for o in obs))


# ────────────────────────────────────────────────────────────────────────────
class _TempDBTest(unittest.TestCase):
    """Base: isolated temp cloud DB + temp views dir; synthetic fetcher."""

    MD = "2026-05-28"

    def setUp(self):
        import db
        self._db = db
        self._tmp = tempfile.mkdtemp()
        self._orig_path = db.CLOUD_DB_PATH
        db.CLOUD_DB_PATH = Path(self._tmp) / "cloud.sqlite"
        db.apply_migrations("cloud")

    def tearDown(self):
        self._db.CLOUD_DB_PATH = self._orig_path
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fetcher(self, as_of=None):
        from fetch import Fetcher
        return Fetcher(synthetic=True, store_raw=False, as_of=as_of or self.MD)

    def _count(self, table):
        with self._db.cloud(readonly=True) as c:
            return c.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]


class TestPrices(_TempDBTest):
    def test_upsert_and_idempotency(self):
        import prices
        tickers = ["NVDA", "AAPL", "MSFT"]
        r1 = prices.update_prices(tickers, fetcher=self._fetcher(), market_date=self.MD)
        self.assertEqual(r1["tickers_ok"], 3)
        self.assertEqual(r1["tickers_failed"], 0)
        n1 = self._count("prices_daily")
        prices.update_prices(tickers, fetcher=self._fetcher(), market_date=self.MD)
        self.assertEqual(self._count("prices_daily"), n1, "re-run must not add rows")

    def test_no_future_bars(self):
        import prices
        prices.update_prices(["NVDA"], fetcher=self._fetcher(), market_date=self.MD)
        with self._db.cloud(readonly=True) as c:
            fut = c.execute("SELECT COUNT(*) n FROM prices_daily WHERE date > ?",
                            (self.MD,)).fetchone()["n"]
        self.assertEqual(fut, 0)


class TestReconciliation(_TempDBTest):
    def test_pass_and_reject(self):
        import prices, reconciliation as rec
        prices.update_prices(["NVDA", "AAPL"], fetcher=self._fetcher(), market_date=self.MD)
        out = rec.reconcile_universe(["NVDA", "AAPL"], date=self.MD, fetcher=self._fetcher())
        self.assertEqual(out["passed"], 2)
        self.assertEqual(out["failure_rate"], 0.0)
        # corrupt one close → reject
        with self._db.cloud() as c:
            c.execute("UPDATE prices_daily SET close=close*1.05 WHERE ticker='AAPL' AND date=?", (self.MD,))
            res = rec.reconcile_close_price(c, "AAPL", self.MD, self._fetcher())
        self.assertEqual(res["action"], "rejected")
        self.assertGreater(res["diff_pct"], 2.0)


class TestFeatures(_TempDBTest):
    def test_features_and_active_score(self):
        import universe_builder as ub, prices, feature_builder as fb
        ub.build(build_date=self.MD)
        tickers = ["NVDA", "AAPL", "MSFT", "AMD"]
        prices.update_prices(tickers, fetcher=self._fetcher(), market_date=self.MD)
        with self._db.cloud(readonly=True) as c:
            mts = {r["ticker"]: r["model_type"]
                   for r in c.execute("SELECT ticker, model_type FROM ticker_master").fetchall()}
        out = fb.build_features(tickers, feature_date=self.MD, model_types=mts)
        self.assertEqual(out["written"], 4)
        with self._db.cloud(readonly=True) as c:
            row = c.execute("SELECT tech_score, dq_score, feature_version, usable_at "
                            "FROM features_daily WHERE ticker='NVDA' AND date=?", (self.MD,)).fetchone()
            active = c.execute("SELECT MAX(active_score) a FROM universe_membership "
                               "WHERE ticker='NVDA' AND date=?", (self.MD,)).fetchone()["a"]
        self.assertTrue(0 <= row["tech_score"] <= 100)
        self.assertEqual(row["feature_version"], "4.5.0")  # Phase 4.5 SEC/ratio fundamentals
        self.assertTrue(row["usable_at"].startswith(self.MD))
        self.assertIsNotNone(active)


class TestOutcomes(_TempDBTest):
    def test_generate_signals_replace_existing_prunes_stale_candidates(self):
        import universe_builder as ub, prices, feature_builder as fb
        import regime_builder as rb, signal_generator as sg
        tickers = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
        prices.update_prices(tickers, fetcher=self._fetcher(), market_date=self.MD)
        ub.build(build_date=self.MD)
        with self._db.cloud(readonly=True) as c:
            mts = {r["ticker"]: r["model_type"]
                   for r in c.execute("SELECT ticker, model_type FROM ticker_master").fetchall()}
        fb.build_features(tickers, feature_date=self.MD, model_types=mts)
        rb.compute_themes(self.MD); rb.compute_regime(self.MD)
        sg.generate_signals(["NVDA", "AAPL", "MSFT"], date=self.MD)
        out = sg.generate_signals(["NVDA", "AAPL"], date=self.MD, replace_existing=True)
        self.assertEqual(out["stale_candidates_removed"], 1)
        with self._db.cloud(readonly=True) as c:
            rows = c.execute(
                "SELECT ticker FROM candidate_snapshots WHERE date=? ORDER BY ticker",
                (self.MD,)).fetchall()
        self.assertEqual([r["ticker"] for r in rows], ["AAPL", "NVDA"])

    def test_5d_outcome_and_no_lookahead(self):
        import universe_builder as ub, prices, feature_builder as fb
        import regime_builder as rb, signal_generator as sg, outcome_updater as ou
        from market_calendar import previous_trading_day
        E = self.MD
        S = previous_trading_day(E, 5).isoformat()
        tickers = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "XLK", "XLV"]
        # full history through E
        prices.update_prices(tickers, fetcher=self._fetcher(E), market_date=E)
        # build signals as of S
        ub.build(build_date=S)
        with self._db.cloud(readonly=True) as c:
            mts = {r["ticker"]: r["model_type"]
                   for r in c.execute("SELECT ticker, model_type FROM ticker_master").fetchall()}
        fb.build_features(tickers, feature_date=S, model_types=mts)
        rb.compute_themes(S); rb.compute_regime(S)
        sg.generate_signals(["NVDA", "AAPL", "MSFT"], date=S)
        out = ou.update_outcomes(E)
        self.assertGreaterEqual(out["updated"], 1)
        self.assertIn(5, out["by_horizon"])
        self.assertTrue(ou.assert_no_lookahead(E))
        with self._db.cloud(readonly=True) as c:
            row = c.execute("SELECT abs_ret, rel_spy, mae, mfe, mdd, outcome_label "
                            "FROM signal_outcomes WHERE horizon=5 LIMIT 1").fetchone()
        self.assertIsNotNone(row["abs_ret"])
        self.assertIsNotNone(row["mae"])
        self.assertIsNotNone(row["mfe"])
        self.assertIn(row["outcome_label"], ("HIT", "MISS", "FLAT"))


class TestEndToEnd(_TempDBTest):
    def _run(self):
        import daily_snapshot, views_builder, alerts
        tmp_views = Path(self._tmp) / "views"
        tmp_views.mkdir(exist_ok=True)
        views_builder.VIEWS_DIR = tmp_views
        alerts.VIEWS_DIR = tmp_views
        return daily_snapshot.run(market_date=self.MD, synthetic=True,
                                  limit=40, skip_git=True)

    def test_full_snapshot_success(self):
        out = self._run()
        self.assertEqual(out["result_status"], "success")
        self.assertEqual(out["steps"]["universe"]["total"], 555)
        self.assertGreater(out["steps"]["features"]["written"], 30)
        self.assertGreater(out["steps"]["signals"]["signals"], 10)
        self.assertEqual(out["alerts"]["critical"], 0)
        # 14 views_builder + latest_alerts.json (alerts.py)
        # = 15 (Phase 9: + latest_stress_test.json)
        # = 16 (Phase 10: + latest_model_registry.json)
        tmp_views = Path(self._tmp) / "views"
        self.assertEqual(len(list(tmp_views.glob("*.json"))), 16)

    def test_idempotency_state_tables(self):
        self._run()
        before = {t: self._count(t) for t in
                  ("ticker_master", "prices_daily", "features_daily",
                   "generic_signals", "candidate_snapshots", "sector_theme_daily")}
        self._run()
        after = {t: self._count(t) for t in before}
        self.assertEqual(before, after, "state tables must be idempotent on re-run")

    def test_market_closed_skips(self):
        import daily_snapshot, views_builder, alerts
        tmp_views = Path(self._tmp) / "views"
        tmp_views.mkdir(exist_ok=True)
        views_builder.VIEWS_DIR = tmp_views
        alerts.VIEWS_DIR = tmp_views
        out = daily_snapshot.run(market_date="2026-05-25", synthetic=True,  # Memorial Day
                                 limit=10, skip_git=True)
        self.assertEqual(out["result_status"], "market_closed")

    def test_temp_output_helper_redirects_db_and_views(self):
        import daily_snapshot, views_builder, alerts
        original = (self._db.CLOUD_DB_PATH, views_builder.VIEWS_DIR, alerts.VIEWS_DIR)
        temp_root, temp_db, temp_views, saved = daily_snapshot._activate_temp_output()
        try:
            self.assertEqual(saved, original)
            self.assertEqual(self._db.CLOUD_DB_PATH, temp_db)
            self.assertEqual(views_builder.VIEWS_DIR, temp_views)
            self.assertEqual(alerts.VIEWS_DIR, temp_views)
            self.assertTrue(str(temp_root).startswith(tempfile.gettempdir()))
        finally:
            daily_snapshot._restore_output_paths(saved)
            shutil.rmtree(temp_root, ignore_errors=True)
        self.assertEqual((self._db.CLOUD_DB_PATH, views_builder.VIEWS_DIR, alerts.VIEWS_DIR), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
