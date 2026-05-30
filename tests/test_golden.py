"""
tests/test_golden.py — Golden Set regression tests (Plan §27).

Each scenario in tests/golden/*.json freezes a known input → expected output
for a pure function. If code changes alter any of these, the test fails so the
engineer must explicitly review and update the golden files.

Structure per golden file:
  scenario_id   unique ID
  description   human-readable note
  function      dotted path of function under test
  frozen_inputs / test_cases / scenarios   depends on function type

Test classes:
  TestAggStats       gold_001..004 — views_builder._agg_stats math
  TestDqTier         gold_005     — views_builder._dq_tier boundaries
  TestSignalId       gold_006     — signal_generator._signal_id determinism
  TestRiskGovernor   gold_007..009 — risk_governor.evaluate outcomes
  TestTargetWeight   gold_010     — signal_generator._target_weight
  TestClassify       gold_011     — scorers.synthesis.classify_candidate
  TestSizing         gold_012..013 — sizing.inverse_volatility/fractional_kelly
  TestSignalDiff     gold_014     — regression_warning logic
  TestCoreQuestions  gold_015     — _core_questions Q5/Q7 logic

Run:
    python -m pytest tests/test_golden.py -v
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))


def _load(filename: str) -> dict:
    return json.loads((GOLDEN_DIR / filename).read_text())


def _approx_eq(a, b, tol: float) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# ────────────────────────────────────────────────────────────────────────────
class TestAggStats(unittest.TestCase):
    """gold_001..004 — _agg_stats math correctness."""

    def setUp(self):
        from jobs import views_builder
        self.agg = views_builder._agg_stats

    def _check(self, scenario: dict):
        rows = scenario["frozen_inputs"]["rows"]
        expected = scenario["expected_outputs"]
        tol = scenario.get("tolerance", {})
        result = self.agg(rows)

        self.assertEqual(result["n"], expected["n"],
                         f"{scenario['scenario_id']}: n mismatch")
        for key, exp_val in expected.items():
            if key == "n":
                continue
            res_val = result.get(key)
            t = tol.get(key, 1e-9)
            if exp_val is None:
                self.assertIsNone(res_val,
                                  f"{scenario['scenario_id']}: {key} should be None, got {res_val}")
            else:
                self.assertTrue(
                    _approx_eq(res_val, exp_val, t),
                    f"{scenario['scenario_id']}: {key} expected ≈{exp_val} (tol {t}), got {res_val}")

    def test_basic_3wins_2losses(self):
        self._check(_load("gold_001_agg_stats_basic.json"))

    def test_all_wins(self):
        s = _load("gold_002_agg_stats_all_wins.json")
        rows = s["frozen_inputs"]["rows"]
        result = self.agg(rows)
        expected = s["expected_outputs"]
        tol = s.get("tolerance", {})
        self.assertEqual(result["n"], expected["n"])
        self.assertAlmostEqual(result["hit_rate"], 1.0, places=6)
        self.assertIsNone(result.get("profit_factor"),
                          "profit_factor must be None when no losses")
        self.assertIsNone(result.get("payoff_ratio"),
                          "payoff_ratio must be None when no losses")
        self.assertAlmostEqual(result["ev"], expected["ev"],
                               delta=tol.get("ev", 1e-4))

    def test_all_losses(self):
        s = _load("gold_003_agg_stats_all_losses.json")
        rows = s["frozen_inputs"]["rows"]
        result = self.agg(rows)
        expected = s["expected_outputs"]
        tol = s.get("tolerance", {})
        self.assertEqual(result["n"], expected["n"])
        self.assertAlmostEqual(result["hit_rate"], 0.0, places=6)
        self.assertAlmostEqual(result["avg_loss"], expected["avg_loss"],
                               delta=tol.get("avg_loss", 1e-4))
        self.assertAlmostEqual(result["ev"], expected["ev"],
                               delta=tol.get("ev", 1e-4))
        self.assertAlmostEqual(result["profit_factor"], 0.0, places=6)

    def test_empty_rows(self):
        s = _load("gold_004_agg_stats_empty.json")
        result = self.agg(s["frozen_inputs"]["rows"])
        self.assertEqual(result, {"n": 0})


# ────────────────────────────────────────────────────────────────────────────
class TestDqTier(unittest.TestCase):
    """gold_005 — _dq_tier boundary correctness."""

    def setUp(self):
        from jobs import views_builder
        self.tier = views_builder._dq_tier

    def test_boundaries(self):
        s = _load("gold_005_dq_tier_boundaries.json")
        for tc in s["test_cases"]:
            result = self.tier(tc["input"])
            self.assertEqual(result, tc["expected"],
                             f"_dq_tier({tc['input']!r}) → expected '{tc['expected']}', got '{result}'")


# ────────────────────────────────────────────────────────────────────────────
class TestSignalId(unittest.TestCase):
    """gold_006 — _signal_id is deterministic (same hash every run)."""

    def setUp(self):
        from jobs.signal_generator import _signal_id
        self._id = _signal_id

    def test_determinism(self):
        s = _load("gold_006_signal_id_determinism.json")
        for tc in s["test_cases"]:
            result = self._id(tc["date"], tc["ticker"], tc["source"])
            self.assertEqual(result, tc["expected"],
                             f"_signal_id({tc['date']}, {tc['ticker']}, {tc['source']}) "
                             f"expected '{tc['expected']}', got '{result}'")


# ────────────────────────────────────────────────────────────────────────────
class TestRiskGovernor(unittest.TestCase):
    """gold_007..009 — risk_governor.evaluate deterministic gate outcomes."""

    def setUp(self):
        import risk_governor
        self.G = risk_governor

    def _eval(self, inputs: dict) -> dict:
        return self.G.evaluate(**inputs)

    def test_approved(self):
        s = _load("gold_007_risk_governor_approved.json")
        result = self._eval(s["frozen_inputs"])
        exp = s["expected_outputs"]
        self.assertEqual(result["risk_status"], exp["risk_status"])
        self.assertAlmostEqual(result["suggested_size_before_risk"],
                               exp["suggested_size_before_risk"], places=5)
        self.assertAlmostEqual(result["suggested_size_after_risk"],
                               exp["suggested_size_after_risk"], places=5)
        self.assertAlmostEqual(result["size_multiplier"],
                               exp["size_multiplier"], places=5)
        self.assertEqual(result["risk_flags"], exp["risk_flags"])

    def test_compound_reduction(self):
        s = _load("gold_008_risk_governor_compound.json")
        result = self._eval(s["frozen_inputs"])
        exp = s["expected_outputs"]
        self.assertEqual(result["risk_status"], exp["risk_status"])
        self.assertAlmostEqual(result["suggested_size_after_risk"],
                               exp["suggested_size_after_risk"], places=5)
        self.assertAlmostEqual(result["size_multiplier"],
                               exp["size_multiplier"], places=5)
        for flag in s.get("expected_flags_include", []):
            self.assertIn(flag, result["risk_flags"],
                          f"expected flag '{flag}' in {result['risk_flags']}")

    def test_macro_blocked(self):
        s = _load("gold_009_risk_governor_blocked_macro.json")
        result = self._eval(s["frozen_inputs"])
        exp = s["expected_outputs"]
        self.assertEqual(result["risk_status"], exp["risk_status"])
        self.assertAlmostEqual(result["suggested_size_after_risk"], 0.0, places=5)
        for flag in s.get("expected_flags_include", []):
            self.assertIn(flag, result["risk_flags"])


# ────────────────────────────────────────────────────────────────────────────
class TestTargetWeight(unittest.TestCase):
    """gold_010 — _target_weight composite thresholds."""

    def setUp(self):
        from jobs.signal_generator import _target_weight
        self.tw = _target_weight

    def test_all_thresholds(self):
        s = _load("gold_010_target_weight_thresholds.json")
        for tc in s["test_cases"]:
            result = self.tw(tc["composite"], tc["risk_status"])
            self.assertAlmostEqual(
                result, tc["expected"], places=4,
                msg=f"_target_weight({tc['composite']}, {tc['risk_status']!r}) "
                    f"expected {tc['expected']}, got {result}")


# ────────────────────────────────────────────────────────────────────────────
class TestClassify(unittest.TestCase):
    """gold_011 — classify_candidate outcome per ADR 003 rules."""

    def setUp(self):
        from scorers.synthesis import classify_candidate
        self.classify = classify_candidate

    def test_all_cases(self):
        s = _load("gold_011_classify_candidate.json")
        for tc in s["test_cases"]:
            result = self.classify(
                tc["composite"], tc["days_active"],
                tc["risk_status"], tc["dq"])
            self.assertEqual(result, tc["expected"],
                             f"classify_candidate({tc['composite']}, {tc['days_active']}, "
                             f"'{tc['risk_status']}', {tc['dq']}) "
                             f"expected '{tc['expected']}', got '{result}' — {tc['note']}")


# ────────────────────────────────────────────────────────────────────────────
class TestSizing(unittest.TestCase):
    """gold_012..013 — sizing methods."""

    def setUp(self):
        import sizing
        self.S = sizing

    def test_inverse_vol_ordering(self):
        s = _load("gold_012_sizing_inverse_vol.json")
        vols = s["frozen_inputs"]["vols"]
        w = self.S.inverse_volatility(vols)
        # Lower vol → higher weight
        self.assertGreater(w["KO"], w["AAPL"],
                           f"KO weight {w['KO']} should exceed AAPL {w['AAPL']}")
        self.assertGreater(w["AAPL"], w["NVDA"],
                           f"AAPL weight {w['AAPL']} should exceed NVDA {w['NVDA']}")
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_fractional_kelly(self):
        s = _load("gold_013_sizing_fractional_kelly.json")
        for tc in s["test_cases"]:
            result = self.S.fractional_kelly(tc["edge"], tc["variance"])
            self.assertAlmostEqual(result, tc["expected"], places=4,
                                   msg=f"kelly({tc['edge']}, {tc['variance']}) expected "
                                       f"{tc['expected']}, got {result} — {tc['note']}")


# ────────────────────────────────────────────────────────────────────────────
class TestSignalDiffRegressionGuard(unittest.TestCase):
    """gold_014 — regression_warning threshold logic."""

    def _guard(self, common_count: int, state_change_count: int) -> bool:
        """Mirrors the logic in views_builder._signal_diff."""
        return common_count > 0 and (state_change_count / common_count) > 0.20

    def test_regression_guard(self):
        s = _load("gold_014_signal_diff_regression_guard.json")
        for tc in s["test_cases"]:
            result = self._guard(tc["common_count"], tc["state_change_count"])
            self.assertEqual(result, tc["expected_warning"],
                             f"guard({tc['common_count']}, {tc['state_change_count']}) "
                             f"expected {tc['expected_warning']}, got {result} — {tc['note']}")


# ────────────────────────────────────────────────────────────────────────────
class TestCoreQuestions(unittest.TestCase):
    """gold_015 — _core_questions Q5 and Q7 logic."""

    def setUp(self):
        from jobs import views_builder
        self.cq = views_builder._core_questions

    def _q_by_id(self, questions: list, qid: int) -> dict:
        return next((q for q in questions if q["id"] == qid), {})

    def test_core_questions_positive_ev(self):
        """Sufficient data + positive EV → Q5=True, Q7=True."""
        s = _load("gold_015_core_questions_logic.json")
        sc = s["scenarios"][0]
        stats_20 = sc["overall_20d"]
        # Build minimal cut dicts matching what _core_questions expects
        overall = {"20": stats_20}
        questions = self.cq(overall, {}, {}, {}, {})
        q5 = self._q_by_id(questions, 5)
        q7 = self._q_by_id(questions, 7)
        self.assertEqual(q5["answer"], sc["q5_expected_answer"])
        self.assertEqual(q7["answer"], sc["q7_expected_answer"])

    def test_core_questions_negative_ev(self):
        """Sufficient data + negative EV → Q5=False, Q7=False."""
        s = _load("gold_015_core_questions_logic.json")
        sc = s["scenarios"][1]
        stats_20 = sc["overall_20d"]
        overall = {"20": stats_20}
        questions = self.cq(overall, {}, {}, {}, {})
        q5 = self._q_by_id(questions, 5)
        q7 = self._q_by_id(questions, 7)
        self.assertEqual(q5["answer"], sc["q5_expected_answer"])
        self.assertEqual(q7["answer"], sc["q7_expected_answer"])

    def test_core_questions_insufficient_data(self):
        """n<5 → Q5=None (insufficient data)."""
        s = _load("gold_015_core_questions_logic.json")
        sc = s["scenarios"][2]
        stats_20 = sc["overall_20d"]
        overall = {"20": stats_20}
        questions = self.cq(overall, {}, {}, {}, {})
        q5 = self._q_by_id(questions, 5)
        self.assertIsNone(q5["answer"],
                          f"Q5 should be None for n={stats_20['n']}, got {q5['answer']}")

    def test_all_questions_present(self):
        """_core_questions always returns exactly 10 questions with ids 1-10."""
        questions = self.cq({}, {}, {}, {}, {})
        self.assertEqual(len(questions), 10)
        ids = {q["id"] for q in questions}
        self.assertEqual(ids, set(range(1, 11)))

    def test_questions_have_required_fields(self):
        """Every question must have id, question, answer, evidence, sufficient_data."""
        questions = self.cq({}, {}, {}, {}, {})
        required = {"id", "question", "answer", "evidence", "sufficient_data"}
        for q in questions:
            missing = required - set(q.keys())
            self.assertFalse(missing, f"Q{q.get('id')} missing fields: {missing}")


# ────────────────────────────────────────────────────────────────────────────
class TestGovernorLedger(unittest.TestCase):
    """gold_016 — _governor_ledger avoided/missed/net math (System Pure vs Policy)."""

    def setUp(self):
        from jobs import views_builder
        self.ledger = views_builder._governor_ledger

    def test_ledger_math(self):
        s = _load("gold_016_governor_ledger.json")
        rows = s["frozen_inputs"]["rows"]
        result = self.ledger(rows)
        exp = s["expected_outputs"]["20"]
        tol = s.get("tolerance", {})
        g = result["20"]
        self.assertEqual(g["n_governed"], exp["n_governed"])
        self.assertEqual(g["n_blocked"], exp["n_blocked"])
        self.assertEqual(g["governor_verdict"], exp["governor_verdict"])
        for key in ("avoided_drawdown", "missed_upside", "net_governor_value"):
            self.assertTrue(
                _approx_eq(g[key], exp[key], tol.get(key, 1e-5)),
                f"{key}: expected ≈{exp[key]}, got {g[key]}")


# ────────────────────────────────────────────────────────────────────────────
class TestBacktestMetrics(unittest.TestCase):
    """gold_017 — backtest metric math pins (Phase 7)."""

    def setUp(self):
        from backtest import metrics
        self.m = metrics

    def test_metric_pins(self):
        s = _load("gold_017_metrics_core.json")
        cases = s["cases"]
        tol = s.get("tolerance", {})
        dtol = tol.get("default", 1e-6)

        def chk(name, got):
            exp = cases[name]["expected"]
            self.assertTrue(_approx_eq(got, exp, tol.get(name, dtol)),
                            f"{name}: expected {exp}, got {got}")

        chk("max_drawdown", self.m.max_drawdown(cases["max_drawdown"]["equity"]))
        chk("total_return", self.m.total_return(cases["total_return"]["equity"]))
        sc = cases["sharpe"]
        chk("sharpe", self.m.sharpe(sc["returns"], periods_per_year=sc["ppy"],
                                    rf_annual=sc["rf"]))
        chk("profit_factor", self.m.profit_factor(cases["profit_factor"]["returns"]))
        chk("payoff_ratio", self.m.payoff_ratio(cases["payoff_ratio"]["returns"]))
        chk("hit_rate", self.m.hit_rate(cases["hit_rate"]["returns"]))
        chk("expected_value", self.m.expected_value(cases["expected_value"]["returns"]))


# ────────────────────────────────────────────────────────────────────────────
class TestBacktestLookahead(unittest.TestCase):
    """gold_018 — date-granular no-lookahead guard (Phase 7)."""

    def setUp(self):
        from backtest import bias
        self.bias = bias

    def test_same_day_ok_future_raises(self):
        s = _load("gold_018_no_lookahead_same_day.json")
        dd = s["decision_date"]
        n = self.bias.assert_no_lookahead(s["ok_rows"], dd)
        self.assertEqual(n, s["ok_expected_count"])
        with self.assertRaises(self.bias.LookaheadError):
            self.bias.assert_no_lookahead(s["violation_rows"], dd)


# ────────────────────────────────────────────────────────────────────────────
class TestGoldenSetMetadata(unittest.TestCase):
    """Verify all golden files are loadable JSON with required fields."""

    def test_all_files_loadable(self):
        files = list(GOLDEN_DIR.glob("gold_*.json"))
        self.assertGreater(len(files), 0, "No golden files found")
        for f in sorted(files):
            try:
                data = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                self.fail(f"JSON error in {f.name}: {e}")
            self.assertIn("scenario_id", data, f"{f.name} missing 'scenario_id'")
            self.assertIn("description", data, f"{f.name} missing 'description'")
            self.assertIn("function", data, f"{f.name} missing 'function'")

    def test_golden_count(self):
        files = list(GOLDEN_DIR.glob("gold_*.json"))
        self.assertGreaterEqual(len(files), 18,
                                f"Golden set should have ≥18 scenarios, found {len(files)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
