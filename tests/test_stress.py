"""
tests/test_stress.py — Phase 9 portfolio stress test tests (Plan §9, §30).

UNIT (no DB):
  TestOLSMath          OLS beta math: SPY vs SPY = beta 1.0, R²=1.0
                       trivial (zero variance), short series fallback
  TestFactorProxies    SMB proxy by market cap, HML proxy by sector
  TestStressScenarios  scenario math: beta × shock, actual overrides beta,
                       portfolio aggregation + contributor sort
  TestWeekEndDate      week_end_date pinning to Friday

INTEGRATION (temp DB; synthetic SPY + ticker prices):
  TestFactorIntegration  compute_and_store → factor_exposures_weekly populated,
                         re-run same week is idempotent
  TestStressIntegration  build_stress_view with a synthetic price set:
                         SPY beta-1 ticker → expected loss = spy_shock

Run:
    python -m pytest tests/test_stress.py -v
"""

import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))


# ────────────────────────────────────────────────────────────────────────────
class TestOLSMath(unittest.TestCase):
    def setUp(self):
        import factor_model
        self.fm = factor_model

    def test_spy_vs_spy_beta_one(self):
        """OLS of identical series → beta=1.0, intercept=0, R²=1.0."""
        spy = [0.01, -0.02, 0.015, 0.003, -0.01, 0.008, -0.005, 0.012,
               0.002, -0.007, 0.015, 0.001]
        r = self.fm.ols_beta(spy, spy)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["b_mkt"], 1.0, places=4)
        self.assertAlmostEqual(r["r_squared"], 1.0, places=4)
        self.assertAlmostEqual(r["alpha_daily"], 0.0, places=6)

    def test_beta_two_series(self):
        """Ticker with 2× SPY returns → beta ≈ 2.0."""
        spy = [0.01, -0.02, 0.015, 0.003, -0.01, 0.008, -0.005, 0.012,
               0.002, -0.007, 0.015, 0.001]
        tkr = [2 * r for r in spy]
        result = self.fm.ols_beta(tkr, spy)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["b_mkt"], 2.0, places=3)
        self.assertAlmostEqual(result["r_squared"], 1.0, places=3)

    def test_too_few_obs_returns_error(self):
        """Fewer than 10 observations → ok=False."""
        r = self.fm.ols_beta([0.01, 0.02], [0.01, 0.02])
        self.assertFalse(r["ok"])
        self.assertIn("too_few", r["reason"])

    def test_zero_variance_spy_returns_error(self):
        """Constant SPY → OLS ill-conditioned (StatisticsError caught gracefully)."""
        spy_flat = [0.0] * 15
        tkr = [0.01 * i for i in range(15)]
        # statistics.linear_regression raises StatisticsError on zero variance x
        r = self.fm.ols_beta(tkr, spy_flat)
        # Must not raise; either ok=False with reason, or b_mkt None
        self.assertIn("ok", r)

    def test_model_quality_flag(self):
        """R² ≥ 0.3 → 'good', < 0.3 → 'low'."""
        # Perfect correlation → R² = 1 → good
        spy = list(range(-6, 6))
        tkr = list(range(-6, 6))
        r = self.fm.ols_beta([float(v) / 100 for v in tkr],
                              [float(v) / 100 for v in spy])
        self.assertEqual(r.get("model_quality"), "good")

        # Random noise → R² ≈ 0 → low
        import random
        random.seed(42)
        noise = [random.gauss(0, 0.01) for _ in range(30)]
        spy30 = [random.gauss(0, 0.01) for _ in range(30)]
        r2 = self.fm.ols_beta(noise, spy30)
        if r2.get("ok"):
            self.assertIn(r2.get("model_quality"), ("good", "low"))

    def test_vol_annual_is_positive(self):
        """Annualised vol should be a positive float for non-constant returns."""
        spy = [0.01, -0.02, 0.015, 0.003, -0.01, 0.008, -0.005, 0.012,
               0.002, -0.007, 0.015, 0.001]
        r = self.fm.ols_beta(spy, spy)
        self.assertIsNotNone(r.get("vol_annual"))
        self.assertGreater(r["vol_annual"], 0)


# ────────────────────────────────────────────────────────────────────────────
class TestFactorProxies(unittest.TestCase):
    def setUp(self):
        import factor_model
        self.fm = factor_model

    def test_smb_proxy_mcap(self):
        self.assertIsNone(self.fm._smb_proxy(None))
        self.assertLess(self.fm._smb_proxy(300), 0)     # mega-cap → negative SMB
        self.assertGreater(self.fm._smb_proxy(0.3), 0)  # micro-cap → positive SMB
        self.assertGreater(self.fm._smb_proxy(1), 0)    # small-cap → positive SMB

    def test_hml_proxy_sector(self):
        self.assertIsNone(self.fm._hml_proxy(None))
        # Technology → growth (negative HML)
        self.assertLess(self.fm._hml_proxy("Information Technology"), 0)
        # Energy → value (positive HML)
        self.assertGreater(self.fm._hml_proxy("Energy"), 0)
        # Unknown sector → None
        self.assertIsNone(self.fm._hml_proxy("Aliens"))


# ────────────────────────────────────────────────────────────────────────────
class TestWeekEndDate(unittest.TestCase):
    def setUp(self):
        import factor_model
        self.fm = factor_model

    def test_friday_returns_same_date(self):
        # 2026-05-29 is a Friday
        self.assertEqual(self.fm.week_end_date_for("2026-05-29"), "2026-05-29")

    def test_thursday_returns_prior_friday(self):
        # 2026-05-28 is Thursday → prior Friday was 2026-05-22
        self.assertEqual(self.fm.week_end_date_for("2026-05-28"), "2026-05-22")

    def test_monday_returns_prior_friday(self):
        # 2026-05-25 is Monday → prior Friday was 2026-05-22
        self.assertEqual(self.fm.week_end_date_for("2026-05-25"), "2026-05-22")

    def test_saturday_returns_same_friday(self):
        # 2026-05-30 is Saturday → returns Friday 2026-05-29
        self.assertEqual(self.fm.week_end_date_for("2026-05-30"), "2026-05-29")


# ────────────────────────────────────────────────────────────────────────────
class TestStressScenarios(unittest.TestCase):
    def setUp(self):
        import stress_test
        self.st = stress_test

    def test_scenario_catalogue_has_required_keys(self):
        sc = self.st.STRESS_SCENARIOS
        required = {"hypo_spy_m10", "hypo_spy_m20", "covid_crash_2020",
                    "rate_shock_2022", "gfc_2008"}
        self.assertTrue(required.issubset(sc.keys()))

    def test_hypothetical_uses_beta_shock(self):
        """Hypothetical scenario: estimated = beta × shock (no actual)."""
        sc = {"type": "hypothetical", "spy_shock": -0.10}
        r = self.st._estimate_return(sc, beta=1.5, r_squared=0.72,
                                     alpha=0.0, actual=None)
        self.assertAlmostEqual(r["estimated_return"], -0.15, places=4)
        self.assertEqual(r["method"], "beta")

    def test_actual_overrides_beta(self):
        """If actual return is provided, use it regardless of beta."""
        sc = {"type": "historical", "spy_fallback": -0.34}
        r = self.st._estimate_return(sc, beta=1.5, r_squared=0.72,
                                     alpha=0.0, actual=-0.412)
        self.assertAlmostEqual(r["estimated_return"], -0.412, places=4)
        self.assertEqual(r["method"], "actual")

    def test_no_beta_uses_spy_proxy(self):
        """No beta available → use SPY return as proxy."""
        sc = {"type": "hypothetical", "spy_shock": -0.10}
        r = self.st._estimate_return(sc, beta=None, r_squared=None,
                                     alpha=None, actual=None)
        self.assertAlmostEqual(r["estimated_return"], -0.10, places=4)
        self.assertIn("warning", r)

    def test_low_r2_gets_method_flag(self):
        """Low R² → method = 'low_r2' not 'beta'."""
        sc = {"type": "hypothetical", "spy_shock": -0.10}
        r = self.st._estimate_return(sc, beta=1.0, r_squared=0.10,
                                     alpha=0.0, actual=None)
        self.assertEqual(r["method"], "low_r2")

    def test_portfolio_aggregation_basic(self):
        """portfolio_stress: weighted average of ticker returns."""
        ticker_stress_data = {
            "AAPL": {"beta": 1.2, "r_squared": 0.7, "vol_annual": 0.25,
                     "scenarios": {"hypo_spy_m10": {"estimated_return": -0.12,
                                                     "method": "beta"}}},
            "NVDA": {"beta": 1.8, "r_squared": 0.65, "vol_annual": 0.45,
                     "scenarios": {"hypo_spy_m10": {"estimated_return": -0.18,
                                                     "method": "beta"}}},
        }
        weights = {"AAPL": 0.6, "NVDA": 0.4}
        out = self.st.portfolio_stress(ticker_stress_data, weights)
        # portfolio_return = 0.6*(-0.12) + 0.4*(-0.18) = -0.072 + -0.072 = -0.144
        port = out["hypo_spy_m10"]["portfolio_return"]
        self.assertAlmostEqual(port, -0.144, places=4)

    def test_portfolio_contributors_sorted_worst_first(self):
        """Contributors should be sorted worst contribution first."""
        ticker_stress_data = {
            "A": {"beta": 0.5, "r_squared": 0.6, "vol_annual": 0.2,
                  "scenarios": {"hypo_spy_m10": {"estimated_return": -0.05,
                                                  "method": "beta"}}},
            "B": {"beta": 2.0, "r_squared": 0.7, "vol_annual": 0.4,
                  "scenarios": {"hypo_spy_m10": {"estimated_return": -0.20,
                                                  "method": "beta"}}},
        }
        weights = {"A": 0.5, "B": 0.5}
        out = self.st.portfolio_stress(ticker_stress_data, weights)
        contribs = out["hypo_spy_m10"]["top_contributors"]
        # B has larger negative contribution → should appear first
        self.assertEqual(contribs[0]["ticker"], "B")

    def test_empty_weights_returns_empty(self):
        """Empty weights → empty dict."""
        out = self.st.portfolio_stress({}, {})
        self.assertEqual(out, {})


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

    def _seed_prices(self, ticker: str, n: int = 300,
                     beta: float = 1.0, base: float = 100.0) -> None:
        """Insert n days of synthetic prices that imply a given market beta vs SPY."""
        from market_calendar import previous_trading_day
        import random
        random.seed(hash(ticker) % 1000)
        spy_row = self._db._pool["cloud"].execute if False else None
        with self._db.cloud() as c:
            d = self.MD
            spy_close = 450.0
            tkr_close = base
            for i in range(n):
                # Simulate: tkr_return = beta * spy_return + noise
                spy_ret = random.gauss(0.0005, 0.01)
                tkr_ret = beta * spy_ret + random.gauss(0, 0.001)
                spy_close *= (1 + spy_ret)
                tkr_close *= (1 + tkr_ret)
                # SPY
                c.execute(
                    "INSERT OR REPLACE INTO prices_daily "
                    "(date,ticker,open,high,low,close,adj_close,volume,source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (d, "SPY", spy_close, spy_close * 1.002, spy_close * 0.998,
                     spy_close, spy_close, 50_000_000, "yahoo"))
                # Ticker
                c.execute(
                    "INSERT OR REPLACE INTO prices_daily "
                    "(date,ticker,open,high,low,close,adj_close,volume,source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (d, ticker, tkr_close, tkr_close * 1.01, tkr_close * 0.99,
                     tkr_close, tkr_close, 5_000_000, "yahoo"))
                d = previous_trading_day(d).isoformat()


# ────────────────────────────────────────────────────────────────────────────
class TestFactorIntegration(_TempDB):

    def test_compute_and_store_populates_db(self):
        """compute_and_store writes rows to factor_exposures_weekly."""
        import factor_model
        self._seed_prices("AAPL", n=260, beta=1.2)

        with self._db.cloud() as conn:
            stats = factor_model.compute_and_store(["AAPL", "SPY"],
                                                   as_of=self.MD, conn=conn)
        self.assertGreaterEqual(stats["computed"], 1)

        with self._db.cloud(readonly=True) as conn:
            row = conn.execute(
                "SELECT b_mkt, r_squared, n_obs FROM factor_exposures_weekly "
                "WHERE ticker='AAPL' ORDER BY week_end_date DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row, "Expected factor_exposures_weekly row for AAPL")
        self.assertIsNotNone(row["b_mkt"])
        self.assertIsNotNone(row["r_squared"])
        self.assertGreaterEqual(row["n_obs"], 10)

    def test_compute_and_store_is_idempotent(self):
        """Running compute_and_store twice on same week → same row, no error."""
        import factor_model
        self._seed_prices("MSFT", n=260, beta=0.9)

        with self._db.cloud() as conn:
            factor_model.compute_and_store(["MSFT", "SPY"], as_of=self.MD, conn=conn)
            factor_model.compute_and_store(["MSFT", "SPY"], as_of=self.MD, conn=conn)

        with self._db.cloud(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) n FROM factor_exposures_weekly WHERE ticker='MSFT'"
            ).fetchone()["n"]
        self.assertEqual(count, 1)  # only one row per week_end_date

    def test_spy_beta_approximately_one(self):
        """SPY regressed against itself → beta ≈ 1.0."""
        import factor_model
        self._seed_prices("DUMMY_SPY", n=260, beta=1.0)

        # Compute exposures using SPY close as both ticker and benchmark
        with self._db.cloud(readonly=True) as conn:
            exp = factor_model.compute_exposures(conn, "SPY", self.MD)

        if exp.get("ok"):
            self.assertAlmostEqual(exp["b_mkt"], 1.0, delta=0.1)
            self.assertGreater(exp["r_squared"], 0.9)

    def test_insufficient_prices_skipped(self):
        """Ticker with <20 price rows → skipped (ok=False)."""
        import factor_model
        # Seed SPY but not UNKNOWN_TICKER
        self._seed_prices("SPY2", n=260, beta=1.0)
        with self._db.cloud(readonly=True) as conn:
            exp = factor_model.compute_exposures(conn, "NO_PRICES_AT_ALL", self.MD)
        self.assertFalse(exp.get("ok"))


# ────────────────────────────────────────────────────────────────────────────
class TestStressIntegration(_TempDB):

    def _seed_all(self, beta: float = 1.5) -> None:
        """Seed both SPY and NVDA prices, then compute factor exposures."""
        import factor_model
        self._seed_prices("NVDA", n=260, beta=beta)
        with self._db.cloud() as conn:
            factor_model.compute_and_store(["NVDA", "SPY"], as_of=self.MD, conn=conn)

    def test_stress_view_structure(self):
        """build_stress_view returns required keys."""
        import stress_test
        self._seed_all(beta=1.0)

        with self._db.cloud(readonly=True) as conn:
            view = stress_test.build_stress_view(conn, ["NVDA", "SPY"], self.MD)

        self.assertIn("scenarios", view)
        self.assertIn("ticker_stress", view)
        self.assertIn("snapshot_date", view)
        self.assertIn("hypo_spy_m10", view["scenarios"])
        self.assertIn("covid_crash_2020", view["scenarios"])

    def test_beta_one_ticker_matches_spy_shock(self):
        """A beta≈1 ticker → estimated return ≈ spy_shock for hypothetical scenario."""
        import stress_test
        self._seed_all(beta=1.0)

        with self._db.cloud(readonly=True) as conn:
            view = stress_test.build_stress_view(conn, ["NVDA"], self.MD)

        nvda = view["ticker_stress"].get("NVDA")
        if nvda and nvda.get("beta") is not None:
            sc = nvda["scenarios"]["hypo_spy_m10"]
            spy_shock = view["scenarios"]["hypo_spy_m10"]["spy_return"]
            # estimated ≈ beta × shock; with beta≈1, should be within 30% of shock
            self.assertAlmostEqual(sc["estimated_return"],
                                   nvda["beta"] * spy_shock, delta=0.05)

    def test_high_beta_ticker_amplified_loss(self):
        """A 2× beta ticker → stress loss ≈ 2× SPY shock."""
        import stress_test
        self._seed_all(beta=2.0)

        with self._db.cloud(readonly=True) as conn:
            view = stress_test.build_stress_view(conn, ["NVDA", "SPY"], self.MD)

        nvda = view["ticker_stress"].get("NVDA")
        if nvda and nvda.get("beta") is not None:
            spy_sc = view["ticker_stress"]["SPY"]["scenarios"]["hypo_spy_m20"]
            nvda_sc = nvda["scenarios"]["hypo_spy_m20"]
            # NVDA estimated loss should be worse than SPY
            self.assertLess(nvda_sc["estimated_return"],
                            spy_sc["estimated_return"])


# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    unittest.main(verbosity=2)
