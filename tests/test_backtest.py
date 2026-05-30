"""
tests/test_backtest.py — Phase 7 backtest engine tests (Plan §10, §28).

UNIT (no DB):
  TestMetrics        hand-computed CAGR / Sharpe / MaxDD / PF / tail-loss
  TestStats          bootstrap CI, deflated Sharpe, PBO, CV splits, White's RC
  TestBias           no-lookahead (incl. same-day-timestamp edge) + cost model

INTEGRATION (temp DB; synthetic signals/outcomes/prices):
  TestRunner         signal_outcome end-to-end, idempotent run_id, persistence,
                     unavailable-on-empty, SPY baseline ±1% CAGR, governor ledger

Correctness lives here: the real DB has little history, so these synthetic
fixtures are the proof that the math is right independent of data volume.

Run:
    python -m pytest tests/test_backtest.py -v
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
class TestMetrics(unittest.TestCase):
    def setUp(self):
        from backtest import metrics
        self.m = metrics

    def test_total_return_and_maxdd(self):
        eq = [100, 110, 90, 95, 120, 60]
        self.assertAlmostEqual(self.m.total_return(eq), -0.4, places=6)
        # peak 120 → trough 60 = -50%
        self.assertAlmostEqual(self.m.max_drawdown(eq), -0.5, places=6)

    def test_cagr_one_year_doubling(self):
        # 253 points = 252 daily returns = 1 year; value doubles → CAGR 100%
        eq = [100 * (2 ** (i / 252)) for i in range(253)]
        cg = self.m.cagr(eq, periods_per_year=252)
        self.assertAlmostEqual(cg, 1.0, places=4)

    def test_sharpe_hand_computed(self):
        # r=[.01,.02,-.01,.03,0], rf=0: mean .01, sample sd .0158114,
        # sharpe_period .632456 × √252 = 10.0397
        r = [0.01, 0.02, -0.01, 0.03, 0.00]
        s = self.m.sharpe(r, periods_per_year=252, rf_annual=0.0)
        self.assertAlmostEqual(s, 10.0397, places=3)

    def test_constant_returns_zero_vol(self):
        # zero dispersion → Sharpe undefined (None), volatility exactly 0
        self.assertIsNone(self.m.sharpe([0.01, 0.01, 0.01], rf_annual=0.0))
        self.assertEqual(self.m.volatility([0.01, 0.01, 0.01]), 0.0)

    def test_win_loss_metrics(self):
        r = [0.01, 0.02, -0.01, 0.03, 0.00]
        self.assertAlmostEqual(self.m.hit_rate(r), 0.6, places=6)
        self.assertAlmostEqual(self.m.avg_win(r), 0.02, places=6)
        self.assertAlmostEqual(self.m.avg_loss(r), -0.01, places=6)
        self.assertAlmostEqual(self.m.profit_factor(r), 6.0, places=6)
        self.assertAlmostEqual(self.m.payoff_ratio(r), 2.0, places=6)
        # EV decomposition: .6*.02 + .4*(-.01) = .008
        self.assertAlmostEqual(self.m.expected_value(r), 0.008, places=6)

    def test_profit_factor_no_losses_inf(self):
        # gains but zero losses → infinite profit factor (None only if all-zero)
        self.assertEqual(self.m.profit_factor([0.01, 0.02]), float("inf"))
        self.assertIsNone(self.m.profit_factor([0.0, 0.0]))

    def test_tail_loss_percentile(self):
        # sorted [-0.10,-0.05,-0.02,0.01,0.03,...]; p5 near the worst
        r = [0.03, -0.10, 0.01, -0.05, 0.02, -0.02, 0.015, 0.008, -0.01, 0.005]
        tl = self.m.tail_loss(r, 5.0)
        self.assertLess(tl, 0)              # left tail is a loss
        self.assertGreaterEqual(tl, -0.10)  # not worse than the worst observed

    def test_sortino_only_penalizes_downside(self):
        # symmetric vs upside-skew: sortino ≥ sharpe when downside is limited
        r = [0.02, 0.02, -0.01, 0.02, 0.02]
        sh = self.m.sharpe(r, rf_annual=0.0)
        so = self.m.sortino(r, rf_annual=0.0)
        self.assertGreater(so, sh)

    def test_summary_keys(self):
        r = [0.01, -0.005, 0.012, 0.008, -0.02, 0.015, 0.003, 0.02]
        s = self.m.summary(r, periods_per_year=252)
        for k in ("cagr", "sharpe", "sortino", "max_drawdown", "calmar",
                  "hit_rate", "profit_factor", "payoff_ratio", "tail_loss_p5",
                  "volatility", "total_return", "n_periods"):
            self.assertIn(k, s)

    def test_empty_series_no_crash(self):
        s = self.m.summary([0.0], periods_per_year=252)
        self.assertEqual(s["n_periods"], 1)


# ────────────────────────────────────────────────────────────────────────────
class TestStats(unittest.TestCase):
    def setUp(self):
        from backtest import stats
        self.s = stats
        import random
        rng = random.Random(123)
        self.strong = [rng.gauss(0.0012, 0.006) for _ in range(504)]
        self.noise = [rng.gauss(0.0, 0.01) for _ in range(504)]
        self.bench = [rng.gauss(0.0002, 0.008) for _ in range(504)]

    def test_bootstrap_deterministic(self):
        a = self.s.bootstrap_sharpe(self.strong, n_iter=1000, seed=5)
        b = self.s.bootstrap_sharpe(self.strong, n_iter=1000, seed=5)
        self.assertEqual(a, b)

    def test_bootstrap_ci_brackets_point(self):
        r = self.s.bootstrap_sharpe(self.strong, n_iter=2000, seed=5)
        self.assertLessEqual(r["ci_lower"], r["sharpe"])
        self.assertLessEqual(r["sharpe"], r["ci_upper"])

    def test_bootstrap_strong_significant_noise_not(self):
        strong = self.s.bootstrap_sharpe(self.strong, n_iter=2000, seed=5)
        noise = self.s.bootstrap_sharpe(self.noise, n_iter=2000, seed=5)
        self.assertTrue(strong["significant"])
        self.assertFalse(noise["significant"])

    def test_bootstrap_insufficient(self):
        r = self.s.bootstrap_sharpe([0.01, 0.02], n_iter=100)
        self.assertEqual(r.get("note"), "insufficient_data")

    def test_deflated_strong_significant(self):
        d = self.s.deflated_sharpe_from_returns(self.strong, n_trials=10)
        self.assertTrue(d["is_significant"])
        self.assertGreater(d["deflated_sharpe"], 0.95)

    def test_deflated_noise_not_significant(self):
        d = self.s.deflated_sharpe_from_returns(self.noise, n_trials=50)
        self.assertFalse(d["is_significant"])

    def test_deflated_more_trials_lowers_dsr(self):
        few = self.s.deflated_sharpe_from_returns(self.strong, n_trials=2)
        many = self.s.deflated_sharpe_from_returns(self.strong, n_trials=1000)
        self.assertGreaterEqual(few["deflated_sharpe"], many["deflated_sharpe"])

    def test_deflated_insufficient(self):
        d = self.s.deflated_sharpe_from_returns([0.01] * 5, n_trials=10)
        self.assertIsNone(d["deflated_sharpe"])

    def test_pbo_noise_high(self):
        import random
        rng = random.Random(9)
        mat = {f"s{i}": [rng.gauss(0, 0.01) for _ in range(240)] for i in range(5)}
        r = self.s.probability_backtest_overfitting(mat, n_blocks=8)
        self.assertIsNotNone(r["pbo"])
        self.assertGreater(r["pbo"], 0.4)        # pure noise → frequent OOS failure

    def test_pbo_dominant_strategy_low(self):
        import random
        rng = random.Random(11)
        # one clearly-best strategy in every block + noise peers
        best = [rng.gauss(0.002, 0.004) for _ in range(240)]
        mat = {"best": best,
               "n1": [rng.gauss(0, 0.01) for _ in range(240)],
               "n2": [rng.gauss(0, 0.01) for _ in range(240)],
               "n3": [rng.gauss(0, 0.01) for _ in range(240)]}
        r = self.s.probability_backtest_overfitting(mat, n_blocks=8)
        self.assertLess(r["pbo"], 0.5)
        self.assertFalse(r["is_overfit_risk"])

    def test_pbo_needs_two_strategies(self):
        r = self.s.probability_backtest_overfitting({"only": [0.01] * 100})
        self.assertIsNone(r["pbo"])

    def test_cv_splits_walk_forward(self):
        folds = self.s.time_series_cv_splits(100, train=60, test=20, step=20)
        self.assertEqual(len(folds), 2)
        for f in folds:                          # test strictly AFTER train
            self.assertEqual(f["train"][1], f["test"][0])
            self.assertLess(f["test"][0], f["test"][1])

    def test_whites_strong_significant(self):
        w = self.s.whites_reality_check(self.strong, self.bench, n_bootstrap=2000, seed=5)
        self.assertTrue(w["significant"])
        self.assertLess(w["p_value"], 0.05)

    def test_whites_no_edge_not_significant(self):
        w = self.s.whites_reality_check(self.bench, self.bench, n_bootstrap=2000, seed=5)
        self.assertFalse(w["significant"])

    def test_classify_status(self):
        good = {"is_significant": True}
        bad_pbo = {"is_overfit_risk": True}
        sig = {"significant": True}
        self.assertEqual(self.s.classify_results_status(
            deflated=good, pbo={"is_overfit_risk": False}, whites=sig, n_periods=200), "reliable")
        self.assertEqual(self.s.classify_results_status(
            deflated=good, pbo=bad_pbo, whites=sig, n_periods=200), "overfit_risk")
        self.assertEqual(self.s.classify_results_status(
            deflated=None, pbo=None, whites=None, n_periods=3), "unavailable")


# ────────────────────────────────────────────────────────────────────────────
class TestBias(unittest.TestCase):
    def setUp(self):
        from backtest import bias, costs
        self.b = bias
        self.c = costs

    def test_no_lookahead_clean(self):
        rows = [{"ticker": "A", "usable_at": "2026-05-10"},
                {"ticker": "B", "usable_at": None}]      # None = always knowable
        self.assertEqual(self.b.assert_no_lookahead(rows, "2026-05-15"), 2)

    def test_no_lookahead_same_day_timestamp_ok(self):
        # THE bug we fixed: 'D 20:00Z' is usable for a decision dated D
        rows = [{"ticker": "A", "usable_at": "2026-05-15T20:00:00Z"}]
        self.assertEqual(self.b.assert_no_lookahead(rows, "2026-05-15"), 1)

    def test_no_lookahead_future_raises(self):
        rows = [{"ticker": "X", "usable_at": "2026-05-16T20:00:00Z"}]
        with self.assertRaises(self.b.LookaheadError):
            self.b.assert_no_lookahead(rows, "2026-05-15")

    def test_cost_model(self):
        # default slippage 5 + spread/2 (5) = 10 bps → 0.001 per unit turnover
        self.assertAlmostEqual(self.c.transaction_cost(1.0), 0.001, places=6)
        self.assertAlmostEqual(self.c.transaction_cost(0.5), 0.0005, places=6)
        self.assertAlmostEqual(self.c.apply_costs(0.02, 1.0), 0.019, places=6)

    def test_turnover(self):
        self.assertAlmostEqual(
            self.c.turnover_from_weights({"A": 0.5, "B": 0.5}, {"C": 0.5, "D": 0.5}), 2.0)
        self.assertAlmostEqual(
            self.c.turnover_from_weights({"A": 0.5}, {"A": 0.5}), 0.0)


# ────────────────────────────────────────────────────────────────────────────
class TestRunner(unittest.TestCase):
    """End-to-end on a temp DB seeded with synthetic signals/outcomes/prices."""

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

    def _seed_signal_outcomes(self, n=80, mean=0.015, seed=1):
        import random
        rng = random.Random(seed)
        with self._db.cloud() as c:
            for i in range(n):
                sid = f"s{i}"
                wq = 0.0 if i % 5 == 0 else 0.04
                self._db.upsert(c, "generic_signals", {
                    "signal_id": sid, "date": "2026-03-02", "ticker": f"T{i}",
                    "source": "snapshot", "state": "UPTREND",
                    "risk_status": "BLOCKED" if wq == 0 else "APPROVED",
                    "market_regime": "BROAD_RISK_ON", "composite_score": 70,
                    "dq_score": 90, "target_weight_generic": 0.04,
                    "risk_adjusted_weight": wq}, conflict_cols=("signal_id",))
                for h in (5, 20, 60):
                    ret = rng.gauss(mean, 0.05)
                    spy = rng.gauss(0.005, 0.03)
                    self._db.upsert(c, "signal_outcomes", {
                        "signal_id": sid, "horizon": h, "abs_ret": round(ret, 4),
                        "spy_ret": round(spy, 4), "rel_spy": round(ret - spy, 4),
                        "outcome_label": "HIT" if ret > 0 else "MISS"},
                        conflict_cols=("signal_id", "horizon"))

    def test_signal_outcome_end_to_end(self):
        import backtest_runner as br
        self._seed_signal_outcomes()
        out = br.run_backtest("signal_outcome", start="2026-03-01", end="2026-03-31",
                              config={"horizon": 20}, persist=True)
        self.assertEqual(out["n_oos_periods"], 80)
        self.assertIsNotNone(out["sharpe"])
        self.assertIsNotNone(out["max_drawdown"])
        self.assertEqual(out["lookahead_check_passed"], 1)
        # multiple horizons seeded → PBO computable across variants
        self.assertIsNotNone(out["pbo"])

    def test_idempotent_run_id(self):
        import backtest_runner as br
        self._seed_signal_outcomes()
        a = br.run_backtest("signal_outcome", start="2026-03-01", end="2026-03-31",
                            config={"horizon": 20})
        b = br.run_backtest("signal_outcome", start="2026-03-01", end="2026-03-31",
                            config={"horizon": 20})
        self.assertEqual(a["run_id"], b["run_id"])
        with self._db.cloud(readonly=True) as c:
            n = c.execute("SELECT COUNT(*) n FROM backtest_runs WHERE run_id=?",
                          (a["run_id"],)).fetchone()["n"]
        self.assertEqual(n, 1)                   # upsert, not duplicate

    def test_persistence_columns(self):
        import backtest_runner as br
        self._seed_signal_outcomes()
        out = br.run_backtest("signal_outcome", start="2026-03-01", end="2026-03-31",
                              config={"horizon": 20})
        with self._db.cloud(readonly=True) as c:
            row = c.execute(
                "SELECT strategy_name, results_status, lookahead_check_passed, "
                "survivorship_bias_risk, sharpe, deflated_sharpe FROM backtest_runs "
                "WHERE run_id=?", (out["run_id"],)).fetchone()
        self.assertEqual(row["strategy_name"], "signal_outcome")
        self.assertIsNotNone(row["results_status"])
        self.assertEqual(row["lookahead_check_passed"], 1)

    def test_unavailable_on_empty(self):
        import backtest_runner as br
        out = br.run_backtest("signal_outcome", start="2020-01-01", end="2020-12-31",
                              config={"horizon": 20})
        self.assertEqual(out["results_status"], "unavailable")
        self.assertEqual(out["n_oos_periods"], 0)

    def test_governor_strategy_ledger(self):
        import backtest_runner as br
        self._seed_signal_outcomes()
        out = br.run_backtest("risk_governor", start="2026-03-01", end="2026-03-31",
                              config={"horizon": 20})
        import json
        meta = json.loads(out["result_json"])["strategy_meta"]
        self.assertIn("avoided_drawdown", meta)
        self.assertIn("missed_upside", meta)
        self.assertIn("net_governor_value", meta)
        self.assertGreater(meta["n_governed"], 0)

    def test_spy_baseline_cagr_within_1pct(self):
        """Acceptance: SPY baseline CAGR matches the constructed truth ±1%."""
        import backtest_runner as br
        from market_calendar import next_trading_day
        # build 252 trading days of SPY prices doubling over exactly one year
        with self._db.cloud() as c:
            d = "2024-01-02"
            for i in range(253):
                price = 100 * (2 ** (i / 252))
                c.execute("INSERT OR REPLACE INTO prices_daily(date,ticker,close,adj_close) "
                          "VALUES (?,?,?,?)", (d, "SPY", price, price))
                d = next_trading_day(d).isoformat()
            last = d
        with self._db.cloud() as c:
            b = br.baseline_buy_and_hold(c, "SPY", "2024-01-01", last)
        from backtest import metrics
        cg = metrics.cagr(b["equity"], periods_per_year=252)
        self.assertAlmostEqual(cg, 1.0, delta=0.01)   # 100% CAGR ±1%

    def test_persisted_spy_baseline_is_reference_only_not_survivorship(self):
        """Benchmark baselines persist, but are not treated as alpha strategies."""
        import backtest_runner as br
        from market_calendar import next_trading_day
        with self._db.cloud() as c:
            d = "2024-01-02"
            for i in range(253):
                price = 100 * (1.2 ** (i / 252))
                c.execute("INSERT OR REPLACE INTO prices_daily(date,ticker,close,adj_close) "
                          "VALUES (?,?,?,?)", (d, "SPY", price, price))
                d = next_trading_day(d).isoformat()
            last = d

        out = br.run_backtest("baseline_spy", start="2024-01-01", end=last, config={})
        self.assertEqual(out["strategy_name"], "baseline_spy")
        self.assertEqual(out["results_status"], "reference_only")
        self.assertEqual(out["survivorship_bias_risk"], "none")
        self.assertEqual(out["pit_features_available"], 1)
        self.assertEqual(out["lookahead_check_passed"], 1)
        self.assertIsNotNone(out["cagr"])
        self.assertIn("시장 기준선", out["bias_warning_message"])

        with self._db.cloud(readonly=True) as c:
            row = c.execute(
                "SELECT strategy_name, results_status, survivorship_bias_risk, "
                "pit_features_available, cagr FROM backtest_runs WHERE run_id=?",
                (out["run_id"],)).fetchone()
        self.assertEqual(row["strategy_name"], "baseline_spy")
        self.assertEqual(row["results_status"], "reference_only")
        self.assertEqual(row["survivorship_bias_risk"], "none")
        self.assertEqual(row["pit_features_available"], 1)
        self.assertIsNotNone(row["cagr"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
