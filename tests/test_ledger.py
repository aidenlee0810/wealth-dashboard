"""
tests/test_ledger.py — Phase 8 three-ledger engine tests (Plan §8).

UNIT (no DB):
  TestLedgerStats     return-on-deployed-capital math + None handling

INTEGRATION (temp cloud + local DBs; synthetic signals/outcomes/actions):
  TestThreeLedger     System Pure / Risk Policy / User Actual returns + deltas +
                      action-type breakdown (IGNORE missed upside), account filter

SEPARATION (the whole point of Phase 8):
  TestLedgerSeparation  ledger_engine lives at ROOT (not jobs/); composing
                        cloud+local never writes personal data into the cloud DB

Run:
    python -m pytest tests/test_ledger.py -v
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
class TestLedgerStats(unittest.TestCase):
    def setUp(self):
        import ledger_engine
        self.LE = ledger_engine

    def test_empty(self):
        r = self.LE._ledger_stats([])
        self.assertIsNone(r["portfolio_return"])
        self.assertEqual(r["capital_deployed"], 0.0)

    def test_weighted_return(self):
        # weights 0.04,0.04,0.04; rets .20,.15,-.10 → Σwr=.010 / Σw=.12 = .08333
        pos = [(0.04, 0.20, 0.05), (0.04, 0.15, 0.05), (0.04, -0.10, 0.05)]
        r = self.LE._ledger_stats(pos)
        self.assertEqual(r["n_positions"], 3)
        self.assertAlmostEqual(r["portfolio_return"], 0.083333, places=5)
        self.assertAlmostEqual(r["benchmark_spy_return"], 0.05, places=5)
        self.assertAlmostEqual(r["excess_vs_spy"], 0.033333, places=5)

    def test_none_spy_excluded_from_benchmark(self):
        pos = [(0.04, 0.10, None), (0.04, 0.20, 0.05)]
        r = self.LE._ledger_stats(pos)
        self.assertAlmostEqual(r["portfolio_return"], 0.15, places=5)   # both in port
        self.assertAlmostEqual(r["benchmark_spy_return"], 0.05, places=5)  # only 2nd has spy

    def test_zero_weight_skipped(self):
        pos = [(0.0, 0.99, 0.0), (0.04, 0.10, 0.02)]
        r = self.LE._ledger_stats(pos)
        self.assertEqual(r["n_positions"], 1)
        self.assertAlmostEqual(r["portfolio_return"], 0.10, places=5)


# ────────────────────────────────────────────────────────────────────────────
class _TempBothDB(unittest.TestCase):
    """Spin up BOTH a temp cloud DB and a temp local personal DB."""

    def setUp(self):
        import db
        self._db = db
        self._tmp = tempfile.mkdtemp()
        self._orig_cloud = db.CLOUD_DB_PATH
        self._orig_local = db.LOCAL_DB_PATH
        db.CLOUD_DB_PATH = Path(self._tmp) / "cloud.sqlite"
        db.LOCAL_DB_PATH = Path(self._tmp) / "personal.sqlite"
        db.apply_migrations("cloud")
        db.apply_migrations("local")

    def tearDown(self):
        self._db.CLOUD_DB_PATH = self._orig_cloud
        self._db.LOCAL_DB_PATH = self._orig_local
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed_signal(self, sid, ticker, wp, wq, ret, spy, horizon=60):
        with self._db.cloud() as c:
            self._db.upsert(c, "generic_signals", {
                "signal_id": sid, "date": "2026-03-02", "ticker": ticker,
                "target_weight_generic": wp, "risk_adjusted_weight": wq,
                "risk_status": "APPROVED" if wq and wq > 0 else "BLOCKED"},
                conflict_cols=("signal_id",))
            self._db.upsert(c, "signal_outcomes", {
                "signal_id": sid, "horizon": horizon, "abs_ret": ret, "spy_ret": spy,
                "outcome_label": "HIT" if ret > 0 else "MISS"},
                conflict_cols=("signal_id", "horizon"))

    def _record_action(self, aid, sid, ticker, atype, amount=None, account=None):
        with self._db.local() as loc:
            loc.execute(
                "INSERT INTO user_actions(action_id,date,signal_id,ticker,"
                "action_type,actual_amount,account) VALUES (?,?,?,?,?,?,?)",
                (aid, "2026-03-02", sid, ticker, atype, amount, account))


# ────────────────────────────────────────────────────────────────────────────
class TestThreeLedger(_TempBothDB):
    """The hand-verified §8 scenario: bought A, ignored B (which rallied),
    partial C (which fell)."""

    def _scenario(self, account=None):
        # A: pure&policy full, +20% ; B: pure full / policy blocked, +15% ;
        # C: pure full / policy half, -10%
        self._seed_signal("sA", "NVDA", 0.04, 0.04, 0.20, 0.05)
        self._seed_signal("sB", "JUNK", 0.04, 0.00, 0.15, 0.05)
        self._seed_signal("sC", "AMD", 0.04, 0.02, -0.10, 0.05)
        self._record_action("a1", "sA", "NVDA", "BUY", 0.04, account)
        self._record_action("a2", "sB", "JUNK", "IGNORE", None, account)
        self._record_action("a3", "sC", "AMD", "PARTIAL", 0.02, account)
        import ledger_engine
        return ledger_engine.compute_three_ledger(horizon=60, account=account)

    def test_system_pure_return(self):
        out = self._scenario()
        self.assertAlmostEqual(out["ledgers"]["system_pure"]["portfolio_return"],
                               0.083333, places=5)
        self.assertEqual(out["ledgers"]["system_pure"]["n_positions"], 3)

    def test_risk_policy_excludes_blocked(self):
        out = self._scenario()
        pol = out["ledgers"]["system_risk_policy"]
        self.assertEqual(pol["n_positions"], 2)         # B blocked (wq=0)
        self.assertAlmostEqual(pol["portfolio_return"], 0.10, places=5)

    def test_user_actual(self):
        out = self._scenario()
        act = out["ledgers"]["user_actual"]
        self.assertEqual(act["n_positions"], 2)         # BUY A + PARTIAL C
        self.assertAlmostEqual(act["portfolio_return"], 0.10, places=5)

    def test_deltas(self):
        out = self._scenario()
        d = out["deltas"]
        # governor helped here (avoided half of C's loss, kept winner) → negative cost
        self.assertAlmostEqual(d["governor_cost"], -0.016667, places=5)
        self.assertAlmostEqual(d["behavior_cost"], 0.0, places=5)
        self.assertAlmostEqual(d["total_user_gap"], -0.016667, places=5)

    def test_action_breakdown_missed_upside(self):
        out = self._scenario()
        brk = out["action_breakdown"]
        self.assertAlmostEqual(brk["IGNORE"]["avg_return"], 0.15, places=5)  # missed +15%
        self.assertAlmostEqual(brk["BUY"]["avg_return"], 0.20, places=5)
        self.assertAlmostEqual(brk["PARTIAL"]["avg_return"], -0.10, places=5)
        self.assertEqual(out["n_actions_matched"], 3)

    def test_empty_user_actual_when_no_actions(self):
        self._seed_signal("sA", "NVDA", 0.04, 0.04, 0.20, 0.05)
        import ledger_engine
        out = ledger_engine.compute_three_ledger(horizon=60)
        self.assertIsNone(out["ledgers"]["user_actual"]["portfolio_return"])
        self.assertIsNotNone(out["ledgers"]["system_pure"]["portfolio_return"])

    def test_account_filter(self):
        out = self._scenario(account="roth")
        # actions recorded under 'roth' → matched; default-account query would miss
        self.assertEqual(out["n_actions_matched"], 3)
        self.assertEqual(out["account"], "roth")

    def test_partial_missing_amount_falls_back_to_pure_weight(self):
        self._seed_signal("sX", "AAPL", 0.04, 0.04, 0.10, 0.03)
        self._record_action("ax", "sX", "AAPL", "BUY", None)   # no amount
        import ledger_engine
        out = ledger_engine.compute_three_ledger(horizon=60)
        # weight falls back to pure 0.04 → return = 0.10
        self.assertAlmostEqual(out["ledgers"]["user_actual"]["portfolio_return"],
                               0.10, places=5)


# ────────────────────────────────────────────────────────────────────────────
class TestLedgerSeparation(_TempBothDB):
    """Composing cloud+local must never leak personal data into the cloud DB."""

    def test_ledger_engine_is_root_not_jobs(self):
        self.assertTrue((PROJECT_ROOT / "ledger_engine.py").exists())
        self.assertFalse((PROJECT_ROOT / "jobs" / "ledger_engine.py").exists(),
                         "ledger_engine must be ROOT-level (jobs/ may not touch personal DB)")

    def test_cloud_has_no_personal_tables_after_compute(self):
        self._seed_signal("sA", "NVDA", 0.04, 0.04, 0.20, 0.05)
        self._record_action("a1", "sA", "NVDA", "BUY", 0.04)
        import ledger_engine
        ledger_engine.compute_three_ledger(horizon=60)
        # the canonical cloud-leak guard must still pass
        self._db.assert_no_personal_tables_in_cloud()

    def test_user_actions_only_in_local(self):
        self._record_action("a1", "sA", "NVDA", "BUY", 0.04)
        with self._db.cloud(readonly=True) as c:
            cloud_tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertNotIn("user_actions", cloud_tables)
        with self._db.local(readonly=True) as loc:
            n = loc.execute("SELECT COUNT(*) n FROM user_actions").fetchone()["n"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
