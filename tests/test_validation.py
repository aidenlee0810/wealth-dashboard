"""
tests/test_validation.py — Phase 6 Validation v2 tests.

UNIT (no DB):
  TestAggStatsMath        EV / PF / payoff / MAE-MFE-MDD math edge cases
  TestDqTier              tier boundaries
  TestCoreQuestionsShape  always 10 questions; null answers on sparse data

INTEGRATION (temp DB; synthetic signals + outcomes):
  TestMultiCutIsolation   by_risk_status / by_dq_tier / by_candidate_type /
                          by_regime cuts isolate the right rows + math agrees
  TestSignalDiff          state/risk/score change detection, new/dropped
                          tickers, >20% regression warning

Run:
    python -m pytest tests/test_validation.py -v
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
class TestAggStatsMath(unittest.TestCase):
    """_agg_stats: EV, profit factor, payoff, MAE/MFE/MDD."""

    def setUp(self):
        from jobs import views_builder
        self.agg = views_builder._agg_stats

    def test_empty(self):
        self.assertEqual(self.agg([]), {"n": 0})

    def test_all_none_returns_not_skipped(self):
        # rows with abs_ret=None are excluded from n
        rows = [{"abs_ret": None, "rel_spy": None, "mae": None, "mfe": None, "mdd": None}]
        self.assertEqual(self.agg(rows), {"n": 0})

    def test_ev_formula(self):
        # 2 wins (+0.10, +0.06), 2 losses (-0.04, -0.02)
        rows = [
            {"abs_ret": 0.10, "rel_spy": None, "mae": None, "mfe": None, "mdd": None},
            {"abs_ret": 0.06, "rel_spy": None, "mae": None, "mfe": None, "mdd": None},
            {"abs_ret": -0.04, "rel_spy": None, "mae": None, "mfe": None, "mdd": None},
            {"abs_ret": -0.02, "rel_spy": None, "mae": None, "mfe": None, "mdd": None},
        ]
        r = self.agg(rows)
        self.assertEqual(r["n"], 4)
        self.assertAlmostEqual(r["hit_rate"], 0.5, places=6)
        self.assertAlmostEqual(r["avg_win"], 0.08, places=6)
        self.assertAlmostEqual(r["avg_loss"], -0.03, places=6)
        # EV = 0.5*0.08 + 0.5*(-0.03) = 0.025
        self.assertAlmostEqual(r["ev"], 0.025, places=6)
        # PF = sum(wins)/|sum(losses)| = 0.16/0.06 = 2.667
        self.assertAlmostEqual(r["profit_factor"], 2.67, places=2)
        # payoff = 0.08/0.03 = 2.667
        self.assertAlmostEqual(r["payoff_ratio"], 2.67, places=2)

    def test_profit_factor_none_when_no_losses(self):
        rows = [{"abs_ret": 0.05, "rel_spy": None, "mae": None, "mfe": None, "mdd": None}]
        r = self.agg(rows)
        self.assertIsNone(r["profit_factor"])
        self.assertIsNone(r["payoff_ratio"])

    def test_excursions_averaged(self):
        rows = [
            {"abs_ret": 0.05, "rel_spy": 0.02, "mae": -0.03, "mfe": 0.08, "mdd": -0.04},
            {"abs_ret": -0.05, "rel_spy": -0.02, "mae": -0.09, "mfe": 0.01, "mdd": -0.10},
        ]
        r = self.agg(rows)
        self.assertAlmostEqual(r["avg_mae"], -0.06, places=6)
        self.assertAlmostEqual(r["avg_mfe"], 0.045, places=6)
        self.assertAlmostEqual(r["avg_mdd"], -0.07, places=6)
        self.assertAlmostEqual(r["avg_rel_spy"], 0.0, places=6)

    def test_partial_none_excursions_ignored(self):
        # one row missing mae → averaged over the present ones only
        rows = [
            {"abs_ret": 0.05, "rel_spy": None, "mae": -0.03, "mfe": 0.08, "mdd": None},
            {"abs_ret": 0.03, "rel_spy": None, "mae": None, "mfe": 0.04, "mdd": None},
        ]
        r = self.agg(rows)
        self.assertAlmostEqual(r["avg_mae"], -0.03, places=6)   # only first row
        self.assertAlmostEqual(r["avg_mfe"], 0.06, places=6)    # both rows
        self.assertIsNone(r["avg_mdd"])                          # neither row


# ────────────────────────────────────────────────────────────────────────────
class TestDqTier(unittest.TestCase):
    def setUp(self):
        from jobs import views_builder
        self.tier = views_builder._dq_tier

    def test_tiers(self):
        self.assertEqual(self.tier(None), "unknown")
        self.assertEqual(self.tier(49.9), "<50")
        self.assertEqual(self.tier(50), "50-69")
        self.assertEqual(self.tier(69.9), "50-69")
        self.assertEqual(self.tier(70), "70-84")
        self.assertEqual(self.tier(84.9), "70-84")
        self.assertEqual(self.tier(85), "85+")


# ────────────────────────────────────────────────────────────────────────────
class TestCoreQuestionsShape(unittest.TestCase):
    def setUp(self):
        from jobs import views_builder
        self.cq = views_builder._core_questions

    def test_empty_returns_ten_null(self):
        qs = self.cq({}, {}, {}, {}, {})
        self.assertEqual(len(qs), 10)
        for q in qs:
            self.assertIsNone(q["answer"], f"Q{q['id']} should be null with no data")
            self.assertFalse(q["sufficient_data"])

    def test_q5_positive_ev(self):
        qs = self.cq({"20": {"n": 30, "ev": 0.01, "profit_factor": 1.3}}, {}, {}, {}, {})
        q5 = next(q for q in qs if q["id"] == 5)
        self.assertTrue(q5["answer"])
        self.assertTrue(q5["sufficient_data"])

    def test_q4_governor_blocked_underperforms(self):
        by_risk = {
            "APPROVED": {"20": {"n": 20, "ev": 0.02}},
            "BLOCKED": {"20": {"n": 8, "avg_ret": -0.05}},
        }
        qs = self.cq({}, {}, by_risk, {}, {})
        q4 = next(q for q in qs if q["id"] == 4)
        self.assertTrue(q4["answer"], "blocked avg_ret < approved ev → governor added value")
        self.assertTrue(q4["sufficient_data"])


# ────────────────────────────────────────────────────────────────────────────
class _TempCloudDB(unittest.TestCase):
    """Base: spin up a temp cloud DB with the real schema."""

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

    def _add_signal(self, conn, *, sid, date, ticker, state, risk_status,
                dq_score, regime, composite=70.0, source="snapshot",
                w_pure=None, w_policy=None):
        row = {
            "signal_id": sid, "date": date, "ticker": ticker, "source": source,
            "state": state, "composite_score": composite, "dq_score": dq_score,
            "market_regime": regime, "risk_status": risk_status,
        }
        if w_pure is not None:
            row["target_weight_generic"] = w_pure
        if w_policy is not None:
            row["risk_adjusted_weight"] = w_policy
        self._db.upsert(conn, "generic_signals", row, conflict_cols=("signal_id",))

    def _add_candidate(self, conn, *, date, ticker, ctype, score=70.0):
        self._db.upsert(conn, "candidate_snapshots", {
            "date": date, "ticker": ticker, "candidate_type": ctype, "score": score,
        }, conflict_cols=("date", "ticker"))

    def _signal_outcome(self, conn, *, sid, horizon, abs_ret, rel_spy=None,
                 mae=None, mfe=None, mdd=None):
        self._db.upsert(conn, "signal_outcomes", {
            "signal_id": sid, "horizon": horizon, "abs_ret": abs_ret,
            "rel_spy": rel_spy, "mae": mae, "mfe": mfe, "mdd": mdd,
            "outcome_label": "HIT" if abs_ret > 0 else "MISS",
        }, conflict_cols=("signal_id", "horizon"))


# ────────────────────────────────────────────────────────────────────────────
class TestDataRealismGate(_TempCloudDB):
    """Phase 7.1: synthetic price history must block production interpretation."""

    RUNS = [{"strategy_name": "baseline_spy", "start_date": "2026-01-01", "end_date": "2026-01-03"}]

    def _price(self, conn, *, date, ticker="SPY", source="yahoo", price=100.0):
        conn.execute(
            "INSERT OR REPLACE INTO prices_daily(date,ticker,close,adj_close,source) "
            "VALUES (?,?,?,?,?)", (date, ticker, price, price, source))

    def test_synthetic_blocks_real_interpretation(self):
        from jobs import views_builder
        with self._db.cloud() as conn:
            self._price(conn, date="2026-01-01", source="yahoo")
            self._price(conn, date="2026-01-02", source="synthetic")
        with self._db.cloud(readonly=True) as conn:
            g = views_builder._data_realism_gate(conn, self.RUNS)
        self.assertEqual(g["status"], "synthetic_smoke")
        self.assertFalse(g["production_interpretation_allowed"])
        self.assertEqual(g["synthetic_count"], 1)
        self.assertIn("synthetic", g["warning_ko"])

    def test_all_real_sources_allow_data_realism(self):
        from jobs import views_builder
        with self._db.cloud() as conn:
            self._price(conn, date="2026-01-01", source="yahoo")
            self._price(conn, date="2026-01-02", source="finnhub")
        with self._db.cloud(readonly=True) as conn:
            g = views_builder._data_realism_gate(conn, self.RUNS)
        self.assertEqual(g["status"], "real_data_ready")
        self.assertTrue(g["production_interpretation_allowed"])
        self.assertEqual(g["synthetic_count"], 0)

    def test_unknown_source_is_reference_only(self):
        from jobs import views_builder
        with self._db.cloud() as conn:
            self._price(conn, date="2026-01-01", source=None)
        with self._db.cloud(readonly=True) as conn:
            g = views_builder._data_realism_gate(conn, self.RUNS)
        self.assertEqual(g["status"], "unverified_reference")
        self.assertFalse(g["production_interpretation_allowed"])


# ────────────────────────────────────────────────────────────────────────────
class TestLatestFundamentalsView(_TempCloudDB):
    """Fundamentals tab static view exposes ROIC but guards synthetic-price P/E."""

    def test_sec_roic_visible_and_synthetic_price_blocks_backend_pe(self):
        from jobs import views_builder
        md = "2026-05-20"
        with self._db.cloud() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ticker_master(ticker,name,sector,model_type,active) "
                "VALUES ('NVDA','NVIDIA','Information Technology','operating_company',1)")
            conn.execute(
                "INSERT OR REPLACE INTO cleaned_financials("
                "ticker,fiscal_period,report_date,usable_at,revenue,gross_profit,"
                "operating_income,net_income,cfo,capex,fcf,total_debt,cash_and_sti,"
                "total_equity,shares_out,source,schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NVDA", "2026-Q1-TTM", "2026-05-10", "2026-05-10",
                 1000.0, 700.0, 500.0, 400.0, 450.0, 50.0, 400.0,
                 100.0, 50.0, 800.0, 10.0, "sec", "test"))
            conn.execute(
                "INSERT OR REPLACE INTO normalized_financials("
                "ticker,fiscal_period,usable_at,sector,model_type,gross_margin,"
                "operating_margin,fcf_margin,net_margin,roic,roe,coverage_ratio,"
                "feature_version,ratio_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NVDA", "2026-Q1-TTM", "2026-05-10", "Information Technology",
                 "operating_company", 0.70, 0.50, 0.40, 0.40, 0.45, 0.50,
                 100.0, "test", "line_items"))
            conn.execute(
                "INSERT OR REPLACE INTO prices_daily(date,ticker,close,adj_close,source) "
                "VALUES (?,?,?,?,?)", (md, "NVDA", 200.0, 200.0, "synthetic"))
        with self._db.cloud(readonly=True) as conn:
            v = views_builder._latest_fundamentals(conn, md, {"snapshot_date": md})
        f = v["fundamentals"]["NVDA"]
        self.assertEqual(v["count"], 1)
        self.assertAlmostEqual(f["roic"], 0.45)
        self.assertEqual(f["source"], "sec")
        self.assertIsNone(f["pe_ttm"])
        self.assertIn("synthetic", f["valuation_warning"])

    def test_per_pbr_peg_visible_with_real_price(self):
        from jobs import views_builder
        md = "2026-05-20"
        with self._db.cloud() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ticker_master(ticker,name,sector,model_type,active) "
                "VALUES ('NVDA','NVIDIA','Information Technology','operating_company',1)")
            conn.execute(
                "INSERT OR REPLACE INTO cleaned_financials("
                "ticker,fiscal_period,report_date,usable_at,revenue,gross_profit,"
                "operating_income,net_income,cfo,capex,fcf,total_debt,cash_and_sti,"
                "total_equity,shares_out,source,schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NVDA", "2026-Q1-TTM", "2026-05-10", "2026-05-10",
                 1000.0, 700.0, 500.0, 400.0, 450.0, 50.0, 400.0,
                 100.0, 50.0, 800.0, 10.0, "sec", "test"))
            conn.execute(
                "INSERT OR REPLACE INTO normalized_financials("
                "ticker,fiscal_period,usable_at,sector,model_type,gross_margin,"
                "operating_margin,fcf_margin,net_margin,roic,roe,revenue_growth_yoy,"
                "coverage_ratio,feature_version,ratio_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NVDA", "2026-Q1-TTM", "2026-05-10", "Information Technology",
                 "operating_company", 0.70, 0.50, 0.40, 0.40, 0.45, 0.50,
                 0.25, 100.0, "test", "line_items"))
            conn.execute(
                "INSERT OR REPLACE INTO prices_daily(date,ticker,close,adj_close,source) "
                "VALUES (?,?,?,?,?)", (md, "NVDA", 200.0, 200.0, "alpaca"))
        with self._db.cloud(readonly=True) as conn:
            v = views_builder._latest_fundamentals(conn, md, {"snapshot_date": md})
        f = v["fundamentals"]["NVDA"]
        self.assertAlmostEqual(f["per_ttm"], 5.0)
        self.assertAlmostEqual(f["pe_ttm"], 5.0)
        self.assertAlmostEqual(f["pbr_ttm"], 2.5)
        self.assertAlmostEqual(f["pb_ttm"], 2.5)
        self.assertAlmostEqual(f["peg_ttm"], 0.2)
        self.assertEqual(f["peg_source"], "TTM P/E / YoY revenue growth")


# ────────────────────────────────────────────────────────────────────────────
class TestMultiCutIsolation(_TempCloudDB):
    """Synthetic signals across risk/dq/type/regime → cuts isolate correctly."""

    MD = "2026-05-20"

    def _seed(self):
        from jobs import views_builder
        with self._db.cloud() as conn:
            # AAA: APPROVED, dq=90 (85+), Core, BROAD_RISK_ON, +0.10
            self._add_signal(conn, sid="s_aaa", date=self.MD, ticker="AAA",
                         state="UPTREND", risk_status="APPROVED", dq_score=90,
                         regime="BROAD_RISK_ON")
            self._add_candidate(conn, date=self.MD, ticker="AAA", ctype="Core")
            self._signal_outcome(conn, sid="s_aaa", horizon=20, abs_ret=0.10,
                          rel_spy=0.06, mae=-0.02, mfe=0.12, mdd=-0.03)
            # BBB: APPROVED, dq=75 (70-84), Core, BROAD_RISK_ON, +0.04
            self._add_signal(conn, sid="s_bbb", date=self.MD, ticker="BBB",
                         state="UPTREND", risk_status="APPROVED", dq_score=75,
                         regime="BROAD_RISK_ON")
            self._add_candidate(conn, date=self.MD, ticker="BBB", ctype="Core")
            self._signal_outcome(conn, sid="s_bbb", horizon=20, abs_ret=0.04,
                          rel_spy=0.01, mae=-0.01, mfe=0.05, mdd=-0.02)
            # CCC: BLOCKED, dq=40 (<50), Reject, MACRO_RISK_OFF, -0.15
            self._add_signal(conn, sid="s_ccc", date=self.MD, ticker="CCC",
                         state="DOWNTREND", risk_status="BLOCKED", dq_score=40,
                         regime="MACRO_RISK_OFF")
            self._add_candidate(conn, date=self.MD, ticker="CCC", ctype="Reject")
            self._signal_outcome(conn, sid="s_ccc", horizon=20, abs_ret=-0.15,
                          rel_spy=-0.10, mae=-0.18, mfe=0.01, mdd=-0.18)
            # DDD: SIZE_REDUCED, dq=65 (50-69), Tactical, NARROW_THEME_LEADERSHIP, +0.02
            self._add_signal(conn, sid="s_ddd", date=self.MD, ticker="DDD",
                         state="BREAKOUT", risk_status="SIZE_REDUCED", dq_score=65,
                         regime="NARROW_THEME_LEADERSHIP")
            self._add_candidate(conn, date=self.MD, ticker="DDD", ctype="Tactical")
            self._signal_outcome(conn, sid="s_ddd", horizon=20, abs_ret=0.02,
                          rel_spy=0.0, mae=-0.03, mfe=0.04, mdd=-0.03)
        meta = {"snapshot_date": self.MD}
        with self._db.cloud(readonly=True) as conn:
            return views_builder._validation_summary(conn, self.MD, meta)

    def test_total_graded(self):
        v = self._seed()
        self.assertEqual(v["total_graded_outcomes"], 4)

    def test_overall_by_horizon(self):
        v = self._seed()
        o20 = v["overall_by_horizon"]["20"]
        self.assertEqual(o20["n"], 4)
        # 3 wins (0.10, 0.04, 0.02), 1 loss (-0.15)
        self.assertAlmostEqual(o20["hit_rate"], 0.75, places=4)
        # avg_ret = (0.10+0.04-0.15+0.02)/4 = 0.0025
        self.assertAlmostEqual(o20["avg_ret"], 0.0025, places=4)

    def test_by_risk_status_isolation(self):
        v = self._seed()
        rs = v["by_risk_status"]
        self.assertEqual(rs["APPROVED"]["20"]["n"], 2)        # AAA + BBB
        self.assertAlmostEqual(rs["APPROVED"]["20"]["hit_rate"], 1.0, places=4)
        self.assertEqual(rs["BLOCKED"]["20"]["n"], 1)         # CCC
        self.assertAlmostEqual(rs["BLOCKED"]["20"]["avg_ret"], -0.15, places=4)
        self.assertEqual(rs["SIZE_REDUCED"]["20"]["n"], 1)    # DDD

    def test_by_dq_tier_isolation(self):
        v = self._seed()
        dq = v["by_dq_tier"]
        self.assertEqual(dq["85+"]["20"]["n"], 1)        # AAA
        self.assertEqual(dq["70-84"]["20"]["n"], 1)      # BBB
        self.assertEqual(dq["50-69"]["20"]["n"], 1)      # DDD
        self.assertEqual(dq["<50"]["20"]["n"], 1)        # CCC

    def test_by_candidate_type_isolation(self):
        v = self._seed()
        ct = v["by_candidate_type"]
        self.assertEqual(ct["Core"]["20"]["n"], 2)       # AAA + BBB
        self.assertEqual(ct["Tactical"]["20"]["n"], 1)   # DDD
        self.assertEqual(ct["Reject"]["20"]["n"], 1)     # CCC

    def test_by_regime_isolation(self):
        v = self._seed()
        reg = v["by_regime"]
        self.assertEqual(reg["BROAD_RISK_ON"]["20"]["n"], 2)            # AAA + BBB
        self.assertEqual(reg["MACRO_RISK_OFF"]["20"]["n"], 1)          # CCC
        self.assertEqual(reg["NARROW_THEME_LEADERSHIP"]["20"]["n"], 1)  # DDD

    def test_core_question_q4_governor(self):
        v = self._seed()
        q4 = next(q for q in v["core_questions"] if q["id"] == 4)
        # BLOCKED avg_ret=-0.15 < APPROVED ev → governor added value (True)
        self.assertTrue(q4["answer"])

    def test_governor_blocked_is_first_class(self):
        """A BLOCKED signal must be graded + appear in validation (ADR 007 §8)."""
        v = self._seed()
        self.assertIn("BLOCKED", v["by_risk_status"])
        self.assertEqual(v["by_risk_status"]["BLOCKED"]["20"]["n"], 1)


# ────────────────────────────────────────────────────────────────────────────
class TestSignalDiff(_TempCloudDB):
    """Yesterday vs today signal comparison."""

    TODAY = "2026-05-20"   # Wed
    PREV = "2026-05-19"    # Tue (previous trading day)

    def _seed_and_diff(self, today_signals, prev_signals):
        from jobs import views_builder
        with self._db.cloud() as conn:
            for i, sig in enumerate(prev_signals):
                self._add_signal(conn, sid=f"p_{i}", date=self.PREV, **sig)
            for i, sig in enumerate(today_signals):
                self._add_signal(conn, sid=f"t_{i}", date=self.TODAY, **sig)
        meta = {"snapshot_date": self.TODAY}
        with self._db.cloud(readonly=True) as conn:
            return views_builder._signal_diff(conn, self.TODAY, meta)

    def test_unchanged_not_listed(self):
        sig = dict(ticker="SAME", state="UPTREND", risk_status="APPROVED",
                   dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
        prev = dict(sig); prev["composite"] = 71.0   # delta=1 (<5) → unchanged
        d = self._seed_and_diff([sig], [prev])
        self.assertEqual(d["changed_count"], 0)
        self.assertEqual(d["common_tickers"], 1)

    def test_state_change_detected(self):
        today = dict(ticker="ST", state="BREAKOUT", risk_status="APPROVED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
        prev = dict(today); prev["state"] = "BASE"
        d = self._seed_and_diff([today], [prev])
        self.assertEqual(d["changed_count"], 1)
        c = d["changes"][0]
        self.assertEqual(c["ticker"], "ST")
        self.assertTrue(c["state_changed"])
        self.assertEqual(c["state_before"], "BASE")
        self.assertEqual(c["state_after"], "BREAKOUT")

    def test_risk_change_detected(self):
        today = dict(ticker="RK", state="UPTREND", risk_status="BLOCKED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
        prev = dict(today); prev["risk_status"] = "APPROVED"
        d = self._seed_and_diff([today], [prev])
        self.assertEqual(d["changed_count"], 1)
        c = d["changes"][0]
        self.assertTrue(c["risk_changed"])
        self.assertEqual(c["risk_before"], "APPROVED")
        self.assertEqual(c["risk_after"], "BLOCKED")

    def test_score_delta_threshold(self):
        today = dict(ticker="SC", state="UPTREND", risk_status="APPROVED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
        prev = dict(today); prev["composite"] = 60.0   # delta=10 ≥5 → listed
        d = self._seed_and_diff([today], [prev])
        self.assertEqual(d["changed_count"], 1)
        self.assertAlmostEqual(d["changes"][0]["score_delta"], 10.0, places=2)

    def test_new_and_dropped_tickers(self):
        today = [dict(ticker="KEEP", state="UPTREND", risk_status="APPROVED",
                      dq_score=90, regime="BROAD_RISK_ON", composite=70.0),
                 dict(ticker="NEW", state="BREAKOUT", risk_status="APPROVED",
                      dq_score=85, regime="BROAD_RISK_ON", composite=72.0)]
        prev = [dict(ticker="KEEP", state="UPTREND", risk_status="APPROVED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0),
                dict(ticker="GONE", state="BASE", risk_status="APPROVED",
                     dq_score=80, regime="BROAD_RISK_ON", composite=55.0)]
        d = self._seed_and_diff(today, prev)
        self.assertEqual(d["new_tickers"], ["NEW"])
        self.assertEqual(d["dropped_tickers"], ["GONE"])
        self.assertEqual(d["common_tickers"], 1)

    def test_regression_warning_over_20pct(self):
        # 5 common tickers, 2 change state = 40% > 20% → warning
        today, prev = [], []
        for i in range(5):
            t = dict(ticker=f"T{i}", state="UPTREND", risk_status="APPROVED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
            p = dict(t)
            if i < 2:                       # flip 2 of 5 states
                t["state"] = "DOWNTREND"
            today.append(t)
            prev.append(p)
        d = self._seed_and_diff(today, prev)
        self.assertEqual(d["common_tickers"], 5)
        self.assertEqual(d["state_change_count"], 2)
        self.assertTrue(d["regression_warning"])
        self.assertIsNotNone(d["regression_note"])

    def test_no_regression_warning_at_20pct(self):
        # 5 common, 1 change = 20%, not strictly >20% → no warning
        today, prev = [], []
        for i in range(5):
            t = dict(ticker=f"T{i}", state="UPTREND", risk_status="APPROVED",
                     dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
            p = dict(t)
            if i < 1:
                t["state"] = "DOWNTREND"
            today.append(t)
            prev.append(p)
        d = self._seed_and_diff(today, prev)
        self.assertFalse(d["regression_warning"])

    def test_comparison_dates(self):
        sig = dict(ticker="X", state="UPTREND", risk_status="APPROVED",
                   dq_score=90, regime="BROAD_RISK_ON", composite=70.0)
        d = self._seed_and_diff([sig], [dict(sig)])
        self.assertEqual(d["comparison_dates"]["today"], self.TODAY)
        self.assertEqual(d["comparison_dates"]["yesterday"], self.PREV)


# ────────────────────────────────────────────────────────────────────────────
class TestGovernorLedgerUnit(unittest.TestCase):
    """_governor_ledger: System Pure vs Risk Policy avoided/missed math (§7/§8)."""

    def setUp(self):
        from jobs import views_builder
        self.ledger = views_builder._governor_ledger

    def _row(self, w_pure, w_policy, ret, horizon=20):
        return {"horizon": horizon, "abs_ret": ret,
                "target_weight_generic": w_pure, "risk_adjusted_weight": w_policy}

    def test_blocked_fell_is_avoided(self):
        # blocked (policy 0), fell 10% → avoided 0.04*0.10 = 0.004
        rows = [self._row(0.04, 0.0, -0.10)]
        g = self.ledger(rows)["20"]
        self.assertEqual(g["n_governed"], 1)
        self.assertEqual(g["n_blocked"], 1)
        self.assertAlmostEqual(g["avoided_drawdown"], 0.004, places=5)
        self.assertAlmostEqual(g["missed_upside"], 0.0, places=5)
        self.assertAlmostEqual(g["net_governor_value"], 0.004, places=5)
        self.assertEqual(g["governor_verdict"], "beneficial")

    def test_blocked_rose_is_missed(self):
        # blocked, rose 8% → missed 0.04*0.08 = 0.0032 → net negative
        rows = [self._row(0.04, 0.0, 0.08)]
        g = self.ledger(rows)["20"]
        self.assertAlmostEqual(g["missed_upside"], 0.0032, places=5)
        self.assertAlmostEqual(g["net_governor_value"], -0.0032, places=5)
        self.assertEqual(g["governor_verdict"], "costly")

    def test_size_reduced_partial_cut(self):
        # policy halved (0.04→0.02), fell 6% → cut 0.02, avoided 0.0012, not blocked
        rows = [self._row(0.04, 0.02, -0.06)]
        g = self.ledger(rows)["20"]
        self.assertEqual(g["n_governed"], 1)
        self.assertEqual(g["n_blocked"], 0)
        self.assertAlmostEqual(g["avoided_drawdown"], 0.0012, places=5)

    def test_approved_no_cut_skipped(self):
        # pure == policy → cut 0 → not counted
        rows = [self._row(0.04, 0.04, -0.20)]
        g = self.ledger(rows)["20"]
        self.assertEqual(g["n_governed"], 0)
        self.assertEqual(g["governor_verdict"], "no_data")

    def test_none_weights_skipped(self):
        rows = [self._row(None, None, -0.10), self._row(0.04, None, -0.10)]
        g = self.ledger(rows)["20"]
        self.assertEqual(g["n_governed"], 0)

    def test_mixed_net(self):
        # avoided: 0.04*0.10=0.004 + 0.0125*0.06=0.00075 = 0.00475
        # missed:  0.04*0.08=0.0032
        # net = 0.00475 - 0.0032 = 0.00155
        rows = [
            self._row(0.04, 0.0, -0.10),     # blocked, fell
            self._row(0.04, 0.0, 0.08),      # blocked, rose
            self._row(0.025, 0.0125, -0.06), # reduced, fell
            self._row(0.04, 0.04, 0.20),     # approved, skip
        ]
        g = self.ledger(rows)["20"]
        self.assertEqual(g["n_governed"], 3)
        self.assertEqual(g["n_blocked"], 2)
        self.assertAlmostEqual(g["avoided_drawdown"], 0.00475, places=5)
        self.assertAlmostEqual(g["missed_upside"], 0.0032, places=5)
        self.assertAlmostEqual(g["net_governor_value"], 0.00155, places=5)
        self.assertEqual(g["governor_verdict"], "beneficial")

    def test_per_horizon_split(self):
        rows = [self._row(0.04, 0.0, -0.10, horizon=20),
                self._row(0.04, 0.0, 0.05, horizon=60)]
        g = self.ledger(rows)
        self.assertIn("20", g)
        self.assertIn("60", g)
        self.assertEqual(g["20"]["governor_verdict"], "beneficial")
        self.assertEqual(g["60"]["governor_verdict"], "costly")


# ────────────────────────────────────────────────────────────────────────────
class TestGovernorLedgerIntegration(_TempCloudDB):
    """End-to-end: weighted signals + outcomes → governor_ledger in the view."""

    MD = "2026-05-20"

    def _seed(self):
        from jobs import views_builder
        with self._db.cloud() as conn:
            # BLOCKED name that crashed → governor saved it (System Pure would have held 0.04)
            self._add_signal(conn, sid="g_blk", date=self.MD, ticker="BLK",
                             state="DOWNTREND", risk_status="BLOCKED", dq_score=40,
                             regime="MACRO_RISK_OFF", w_pure=0.04, w_policy=0.0)
            self._signal_outcome(conn, sid="g_blk", horizon=20, abs_ret=-0.15)
            # BLOCKED name that rallied → governor cost upside
            self._add_signal(conn, sid="g_mis", date=self.MD, ticker="MIS",
                             state="UPTREND", risk_status="BLOCKED", dq_score=45,
                             regime="MACRO_RISK_OFF", w_pure=0.04, w_policy=0.0)
            self._signal_outcome(conn, sid="g_mis", horizon=20, abs_ret=0.05)
            # APPROVED full size → no governor effect
            self._add_signal(conn, sid="g_ok", date=self.MD, ticker="OKK",
                             state="UPTREND", risk_status="APPROVED", dq_score=95,
                             regime="BROAD_RISK_ON", w_pure=0.04, w_policy=0.04)
            self._signal_outcome(conn, sid="g_ok", horizon=20, abs_ret=0.10)
        meta = {"snapshot_date": self.MD}
        with self._db.cloud(readonly=True) as conn:
            return views_builder._validation_summary(conn, self.MD, meta)

    def test_ledger_present_in_view(self):
        v = self._seed()
        self.assertIn("governor_ledger", v)
        g20 = v["governor_ledger"]["20"]
        # avoided 0.04*0.15=0.006, missed 0.04*0.05=0.002, net=0.004
        self.assertEqual(g20["n_governed"], 2)      # BLK + MIS (OKK has no cut)
        self.assertEqual(g20["n_blocked"], 2)
        self.assertAlmostEqual(g20["avoided_drawdown"], 0.006, places=5)
        self.assertAlmostEqual(g20["missed_upside"], 0.002, places=5)
        self.assertAlmostEqual(g20["net_governor_value"], 0.004, places=5)

    def test_q4_uses_ledger_when_available(self):
        v = self._seed()
        q4 = next(q for q in v["core_questions"] if q["id"] == 4)
        # only 2 governed (<5) → ledger branch not sufficient, falls back to blocked EV
        # blocked avg_ret = (-0.15+0.05)/2 = -0.05 < 0 → governor reduced losers
        self.assertIsNotNone(q4["answer"])
        self.assertIn("governor_ledger_20d", q4["evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
