"""
tests/test_risk_governor.py — Phase 5 Risk Governor + position sizing tests.

UNIT (no DB):
  TestMarketGates     each market/data gate fires correctly + skips on missing data
  TestPortfolioGates  concentration / leverage / single-position / drawdown
  TestSeveritySizing  severity precedence + size_after / max_weight math
  TestSizing          equal / inverse-vol / risk-parity (equal RC) / Kelly / vol-target

INTEGRATION (temp DB; synthetic):
  TestGovernorInSignals  a low-DQ ticker is BLOCKED in generic_signals and tracked

Run:
    python -m pytest tests/test_risk_governor.py -v
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
class TestMarketGates(unittest.TestCase):
    def setUp(self):
        import risk_governor
        self.G = risk_governor

    def _ok(self, **kw):
        base = dict(ticker="X", composite=70, dq_score=95, state="UPTREND",
                    price=100, adv_dollar=5e9, price_vs_sma200=0.1, sma200_slope=1.0,
                    market_regime="BROAD_RISK_ON", base_weight=0.04)
        base.update(kw)
        return self.G.evaluate(**base)

    def test_approved_clean(self):
        r = self._ok()
        self.assertEqual(r["risk_status"], "APPROVED")
        self.assertEqual(r["suggested_size_after_risk"], 0.04)

    def test_missing_essentials(self):
        r = self._ok(price=None, has_prices=False)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("MISSING_ESSENTIALS", r["risk_flags"])
        self.assertEqual(r["suggested_size_after_risk"], 0.0)

    def test_dq_critical_blocks(self):
        r = self._ok(dq_score=42)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("DQ_CRITICAL", r["risk_flags"])

    def test_dq_low_reduces(self):
        r = self._ok(dq_score=60)
        self.assertEqual(r["risk_status"], "SIZE_REDUCED")
        self.assertAlmostEqual(r["suggested_size_after_risk"], 0.04 * 0.4, places=4)

    def test_liquidity_blocks_new_entry(self):
        r = self._ok(adv_dollar=1e6)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("LIQUIDITY_LOW", r["risk_flags"])
        # not a new entry → liquidity gate does not block
        r2 = self._ok(adv_dollar=1e6, is_new_entry=False)
        self.assertNotIn("LIQUIDITY_LOW", r2["risk_flags"])

    def test_downtrend_hard_block(self):
        r = self._ok(price_vs_sma200=-0.2, sma200_slope=-1.0, state="DOWNTREND")
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("DOWNTREND", r["risk_flags"])

    def test_extreme_valuation_no_fcf(self):
        r = self._ok(ev_sales=25, fcf_margin=-0.1)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("EXTREME_VALUATION_NO_FCF", r["risk_flags"])

    def test_earnings_blackout_review(self):
        r = self._ok(earnings_in_days=2)
        self.assertEqual(r["risk_status"], "REVIEW_REQUIRED")
        self.assertIn("EARNINGS_BLACKOUT", r["risk_flags"])

    def test_leveraged_etf_reduced(self):
        r = self._ok(model_type="leveraged_etf")
        self.assertEqual(r["risk_status"], "SIZE_REDUCED")
        self.assertIn("LEVERAGE_DECAY", r["risk_flags"])

    def test_macro_severe_blocks(self):
        r = self._ok(market_regime="MACRO_RISK_OFF", hy_oas=6.0)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("MACRO_SEVERE", r["risk_flags"])
        # not severe when OAS is tight
        r2 = self._ok(market_regime="MACRO_RISK_OFF", hy_oas=3.0)
        self.assertNotIn("MACRO_SEVERE", r2["risk_flags"])

    def test_ipo_unseasoned_review(self):
        r = self._ok(days_since_ipo=30)
        self.assertEqual(r["risk_status"], "REVIEW_REQUIRED")
        self.assertIn("IPO_UNSEASONED", r["risk_flags"])

    def test_missing_data_does_not_fire(self):
        # ev_sales/fcf_margin/earnings unknown → those gates skip, stays APPROVED
        r = self._ok(ev_sales=None, fcf_margin=None, earnings_in_days=None)
        self.assertEqual(r["risk_status"], "APPROVED")


# ────────────────────────────────────────────────────────────────────────────
class TestPortfolioGates(unittest.TestCase):
    def setUp(self):
        import risk_governor
        self.G = risk_governor

    def _base(self, portfolio):
        return self.G.evaluate(
            ticker="NVDA", composite=70, dq_score=95, state="UPTREND", price=100,
            adv_dollar=5e9, price_vs_sma200=0.1, sma200_slope=1.0,
            market_regime="BROAD_RISK_ON", base_weight=0.04, portfolio=portfolio)

    def test_no_portfolio_skips_portfolio_gates(self):
        r = self.G.evaluate(ticker="NVDA", composite=70, dq_score=95, state="UPTREND",
                            price=100, adv_dollar=5e9, market_regime="BROAD_RISK_ON",
                            base_weight=0.04, portfolio=None)
        self.assertEqual(r["risk_status"], "APPROVED")

    def test_single_position_cap(self):
        r = self._base({"position_weights": {"NVDA": 0.09}})
        self.assertEqual(r["risk_status"], "SIZE_REDUCED")
        self.assertIn("SINGLE_POSITION_CAP", r["risk_flags"])
        self.assertLessEqual(r["max_allowed_weight"], 0.08)

    def test_sector_concentration(self):
        r = self._base({"ticker_sector": "Information Technology",
                        "sector_weights": {"Information Technology": 0.40}})
        self.assertEqual(r["risk_status"], "SIZE_REDUCED")
        self.assertIn("SECTOR_CONCENTRATION", r["risk_flags"])

    def test_portfolio_drawdown_blocks(self):
        r = self._base({"portfolio_1m_return": -0.20})
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertIn("PORTFOLIO_DRAWDOWN", r["risk_flags"])

    def test_correlation_cluster_review(self):
        r = self._base({"top5_avg_correlation": 0.90})
        self.assertEqual(r["risk_status"], "REVIEW_REQUIRED")
        self.assertIn("CORRELATION_CLUSTER", r["risk_flags"])


# ────────────────────────────────────────────────────────────────────────────
class TestSeveritySizing(unittest.TestCase):
    def setUp(self):
        import risk_governor
        self.G = risk_governor

    def test_blocked_dominates(self):
        # both a reduce and a block fire → BLOCKED wins, size 0
        r = self.G.evaluate(ticker="X", composite=70, dq_score=60,  # reduce
                            state="UPTREND", price=2, adv_dollar=1e6,  # liquidity block
                            market_regime="BROAD_RISK_ON", base_weight=0.04)
        self.assertEqual(r["risk_status"], "BLOCKED")
        self.assertEqual(r["suggested_size_after_risk"], 0.0)

    def test_multiple_reductions_compound(self):
        r = self.G.evaluate(ticker="X", composite=70, dq_score=60,  # 0.4x
                            state="UPTREND", price=100, adv_dollar=5e9,
                            model_type="leveraged_etf",            # 0.5x
                            market_regime="BROAD_RISK_ON", base_weight=0.04)
        self.assertEqual(r["risk_status"], "SIZE_REDUCED")
        self.assertAlmostEqual(r["size_multiplier"], 0.4 * 0.5, places=3)


# ────────────────────────────────────────────────────────────────────────────
class TestSizing(unittest.TestCase):
    def setUp(self):
        import sizing
        self.S = sizing
        self.vols = {"NVDA": 0.50, "AAPL": 0.25, "KO": 0.15}

    def test_equal(self):
        w = self.S.equal_weight(["A", "B", "C", "D"])
        self.assertAlmostEqual(w["A"], 0.25)

    def test_inverse_vol_favours_low_vol(self):
        w = self.S.inverse_volatility(self.vols)
        self.assertGreater(w["KO"], w["NVDA"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_risk_parity_equal_contribution(self):
        w = self.S.risk_parity(self.vols, rho=0.3)
        rc = self.S.risk_contribution(w, self.vols, rho=0.3)
        # all risk contributions ~ equal
        vals = list(rc.values())
        self.assertAlmostEqual(max(vals), min(vals), places=2)

    def test_fractional_kelly(self):
        # edge 0.08, var 0.25 → full Kelly 0.32, quarter 0.08, capped at 0.08
        self.assertAlmostEqual(self.S.fractional_kelly(0.08, 0.25), 0.08, places=4)
        self.assertEqual(self.S.fractional_kelly(-0.1, 0.25), 0.0)   # negative edge → 0

    def test_apply_fractional_kelly_preserves_cash_residual(self):
        w = self.S.apply_method(
            "fractional_kelly", tickers=["A", "B"],
            edges={"A": 0.08, "B": 0.04}, variances={"A": 0.25, "B": 0.25})
        self.assertAlmostEqual(w["A"], 0.08, places=4)
        self.assertAlmostEqual(w["B"], 0.04, places=4)
        self.assertLess(sum(w.values()), 1.0)  # rest stays cash; do not re-normalise

    def test_vol_target_scales_down(self):
        base = {"NVDA": 0.5, "AAPL": 0.5}
        scaled = self.S.vol_target(base, self.vols, target_vol=0.15, rho=0.3)
        self.assertLess(sum(scaled.values()), sum(base.values()))


# ────────────────────────────────────────────────────────────────────────────
class TestGovernorInSignals(unittest.TestCase):
    """A low-DQ ticker must be BLOCKED in generic_signals (and thus trackable)."""
    MD = "2026-05-28"

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

    def test_blocked_signal_recorded(self):
        import universe_builder as ub, prices, feature_builder as fb
        import regime_builder as rb, signal_generator as sg
        from fetch import Fetcher
        f = Fetcher(synthetic=True, store_raw=False, as_of=self.MD)
        tickers = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
        prices.update_prices(tickers, fetcher=f, market_date=self.MD)
        ub.build(build_date=self.MD)
        with self._db.cloud(readonly=True) as c:
            mts = {r["ticker"]: r["model_type"]
                   for r in c.execute("SELECT ticker, model_type FROM ticker_master").fetchall()}
        fb.build_features(tickers, feature_date=self.MD, model_types=mts)
        rb.compute_themes(self.MD); rb.compute_regime(self.MD)
        # Force NVDA to look critically low-quality
        with self._db.cloud() as c:
            c.execute("UPDATE features_daily SET dq_score=40 WHERE ticker='NVDA' AND date=?",
                      (self.MD,))
        sg.generate_signals(["NVDA", "AAPL", "MSFT"], date=self.MD)
        with self._db.cloud(readonly=True) as c:
            row = c.execute("SELECT risk_status, risk_flags_json FROM generic_signals "
                            "WHERE ticker='NVDA' AND date=?", (self.MD,)).fetchone()
        self.assertEqual(row["risk_status"], "BLOCKED")
        self.assertIn("DQ_CRITICAL", row["risk_flags_json"])

    def test_hard_downtrend_signal_records_policy_weight(self):
        import signal_generator as sg
        from datetime import date, timedelta

        ticker = "DOWN"
        start = date.fromisoformat("2025-08-01")
        with self._db.cloud() as c:
            for i in range(220):
                d = (start + timedelta(days=i)).isoformat()
                close = 300.0 - i
                c.execute(
                    "INSERT INTO prices_daily "
                    "(date,ticker,open,high,low,close,adj_close,volume,source,reconciled) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (d, ticker, close, close + 1, close - 1, close, close,
                     1_000_000, "test", 1))
            c.execute(
                "INSERT INTO features_daily "
                "(date,ticker,tech_score,dq_score,composite_score,state,price_vs_sma200,"
                "model_type,feature_version,usable_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.MD, ticker, 80, 95, 75, "DOWNTREND", -0.20,
                 "operating_company", "4.5.0", self.MD))

        sg.generate_signals([ticker], date=self.MD)
        with self._db.cloud(readonly=True) as c:
            row = c.execute(
                "SELECT risk_status, risk_flags_json, target_weight_generic, "
                "risk_adjusted_weight, risk_size_multiplier, lineage_json "
                "FROM generic_signals WHERE ticker=? AND date=?",
                (ticker, self.MD)).fetchone()
            cand = c.execute(
                "SELECT risk_adjusted_weight FROM candidate_snapshots "
                "WHERE ticker=? AND date=?", (ticker, self.MD)).fetchone()
        self.assertEqual(row["risk_status"], "BLOCKED")
        self.assertIn("DOWNTREND", row["risk_flags_json"])
        self.assertGreater(row["target_weight_generic"], 0)
        self.assertEqual(row["risk_adjusted_weight"], 0.0)
        self.assertEqual(cand["risk_adjusted_weight"], 0.0)
        self.assertIn("risk_governor", row["lineage_json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
