"""
tests/test_scoring.py — Phase 4 model-type scoring + 4-layer pipeline tests.

UNIT (no DB/network):
  TestModelTypes        classification incl. seed corroboration + non-bank fin
  TestBaseHelpers       scale / percentile / zscore / coverage / weighted_blend
  TestFundamentalAnalyst  §6 — ETF N/A (no penalty), operating/bank/reit/biotech BQ
  TestSpecialists       sector_theme / macro_fit / risk analysts
  TestSynthesis         regime weights, N/A exclusion, candidate_type, coverage DQ

INTEGRATION (temp cloud DB; synthetic Fetcher):
  TestFinancialsPipeline  clean → normalize → get_fundamentals, point-in-time
  TestScoringEndToEnd     full synthetic snapshot: all pillars, ETF no BQ

Run:
    python -m pytest tests/test_scoring.py -v
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
class TestModelTypes(unittest.TestCase):
    def setUp(self):
        from scorers import model_types as mt
        self.mt = mt

    def test_basic(self):
        c = self.mt.classify
        self.assertEqual(c("NVDA", sector="Information Technology"), "operating_company")
        self.assertEqual(c("JPM", sector="Financials"), "bank")
        self.assertEqual(c("O", sector="Real Estate"), "reit")
        self.assertEqual(c("SPY", is_etf=True), "etf")
        self.assertEqual(c("TQQQ"), "leveraged_etf")
        self.assertEqual(c("PGR", sector="Financials"), "insurance")

    def test_biotech_revenue_ceiling(self):
        c = self.mt.classify
        self.assertEqual(c("XYZ", sector="Health Care", revenue_ttm=10e6), "biotech_pre_revenue")
        self.assertEqual(c("LLY", sector="Health Care", revenue_ttm=40e9), "operating_company")

    def test_bank_seed_corroboration(self):
        """A whole-sector 'bank' seed (XLF) must not mislabel non-banks."""
        c = self.mt.classify
        # Mastercard/Visa/BlackRock seeded 'bank' by XLF → corrected to operating
        self.assertEqual(c("MA", sector="Financials", seed_model_type="bank"), "operating_company")
        self.assertEqual(c("V", sector="Financials", seed_model_type="bank"), "operating_company")
        self.assertEqual(c("BLK", sector="Financials", seed_model_type="bank"), "operating_company")
        # A real bank with the same seed stays a bank
        self.assertEqual(c("JPM", sector="Financials", seed_model_type="bank"), "bank")
        self.assertEqual(c("SOFI", sector="Financials", seed_model_type="bank"), "bank")

    def test_etf_seed_overrides_to_etf(self):
        self.assertEqual(self.mt.classify("VUG", seed_model_type="etf"), "etf")

    def test_metric_specs_and_has_bq(self):
        self.assertGreater(len(self.mt.metric_specs("operating_company")), 4)
        self.assertEqual(self.mt.metric_specs("etf"), [])
        self.assertFalse(self.mt.has_business_quality("etf"))
        self.assertTrue(self.mt.has_business_quality("bank"))


# ────────────────────────────────────────────────────────────────────────────
class TestBaseHelpers(unittest.TestCase):
    def setUp(self):
        from scorers import base
        self.b = base

    def test_scale_normal_and_inverted(self):
        self.assertAlmostEqual(self.b.scale(15, 0, 30), 50.0)
        self.assertEqual(self.b.scale(50, 0, 30), 100.0)   # clamped
        self.assertEqual(self.b.scale(-5, 0, 30), 0.0)     # clamped
        # inverted (lower better)
        self.assertGreater(self.b.scale(0.5, 3, 0), self.b.scale(2.5, 3, 0))

    def test_coverage(self):
        self.assertEqual(self.b.coverage(["a", "b", "c", "d"], {"a": 1, "b": 2}), 50.0)
        self.assertEqual(self.b.coverage([], {}), 100.0)

    def test_weighted_blend_skips_none(self):
        # None score dropped, weights renormalised
        v = self.b.weighted_blend([(80, 0.5), (None, 0.3), (60, 0.2)])
        self.assertAlmostEqual(v, (80 * 0.5 + 60 * 0.2) / 0.7, places=4)
        self.assertIsNone(self.b.weighted_blend([(None, 0.5)]))

    def test_zscore(self):
        self.assertAlmostEqual(self.b.zscore(10, [2, 4, 6, 8, 10]), 1.414, places=2)
        self.assertIsNone(self.b.zscore(1, [1, 2]))  # population too small

    def test_pillarscore_na(self):
        na = self.b.PillarScore.na("ETF_NO_BQ")
        self.assertTrue(na.is_na)
        self.assertIsNone(na.score)


# ────────────────────────────────────────────────────────────────────────────
class TestExternalFundamentalMappers(unittest.TestCase):
    def test_sec_companyfacts_maps_line_items(self):
        from fetch import _map_sec_companyfacts

        def usd(tag, facts):
            return {tag: {"units": {"USD": facts}}}

        def shares(tag, facts):
            return {tag: {"units": {"shares": facts}}}

        def annual(val, end="2025-09-27", filed="2025-11-01", tag_form="10-K"):
            return {"val": val, "start": "2024-09-28", "end": end, "filed": filed,
                    "form": tag_form, "fy": 2025, "fp": "FY", "accn": "000-a"}

        facts = {}
        facts.update(usd("RevenueFromContractWithCustomerExcludingAssessedTax", [
            annual(391_000_000_000),
            {"val": 383_000_000_000, "start": "2023-09-30", "end": "2024-09-27",
             "filed": "2024-11-01", "form": "10-K", "fy": 2024, "fp": "FY", "accn": "000-b"},
        ]))
        facts.update(usd("GrossProfit", [annual(180_000_000_000)]))
        facts.update(usd("OperatingIncomeLoss", [annual(120_000_000_000)]))
        facts.update(usd("NetIncomeLoss", [annual(95_000_000_000)]))
        facts.update(usd("NetCashProvidedByUsedInOperatingActivities", [annual(110_000_000_000)]))
        facts.update(usd("PaymentsToAcquirePropertyPlantAndEquipment", [annual(12_000_000_000)]))
        facts.update(usd("ShareBasedCompensation", [annual(11_000_000_000)]))
        facts.update(usd("DepreciationDepletionAndAmortization", [annual(8_000_000_000)]))
        facts.update(usd("InterestExpenseNonOperating", [annual(4_000_000_000)]))
        facts.update(usd("CashAndCashEquivalentsAtCarryingValue", [
            {"val": 30_000_000_000, "end": "2025-09-27", "filed": "2025-11-01",
             "form": "10-K", "fy": 2025, "fp": "FY", "accn": "000-a"}]))
        facts.update(usd("ShortTermInvestments", [
            {"val": 20_000_000_000, "end": "2025-09-27", "filed": "2025-11-01",
             "form": "10-K", "fy": 2025, "fp": "FY", "accn": "000-a"}]))
        facts.update(usd("LongTermDebtAndFinanceLeaseObligations", [
            {"val": 90_000_000_000, "end": "2025-09-27", "filed": "2025-11-01",
             "form": "10-K", "fy": 2025, "fp": "FY", "accn": "000-a"}]))
        facts.update(usd("StockholdersEquity", [
            {"val": 75_000_000_000, "end": "2025-09-27", "filed": "2025-11-01",
             "form": "10-K", "fy": 2025, "fp": "FY", "accn": "000-a"}]))
        facts.update(shares("WeightedAverageNumberOfDilutedSharesOutstanding", [
            {"val": 15_000_000_000, "start": "2024-09-28", "end": "2025-09-27",
             "filed": "2025-11-01", "form": "10-K", "fy": 2025, "fp": "FY", "accn": "000-a"}]))

        rec = _map_sec_companyfacts(
            "AAPL", {"cik": 320193, "facts": {"us-gaap": facts}},
            "2026-05-29", "operating_company")
        self.assertEqual(rec["source"], "sec")
        self.assertEqual(rec["fiscal_period"], "2025-FY")
        self.assertEqual(rec["usable_at"], "2025-11-01")
        self.assertEqual(rec["revenue"], 391_000_000_000)
        self.assertEqual(rec["revenue_prior"], 383_000_000_000)
        self.assertEqual(rec["capex"], 12_000_000_000)
        self.assertEqual(rec["cash_and_sti"], 50_000_000_000)
        self.assertEqual(rec["ebitda"], 128_000_000_000)
        self.assertEqual(rec["shares_out"], 15_000_000_000)

    def test_finnhub_metrics_maps_ratio_bundle(self):
        from fetch import _map_finnhub_metric
        rec = _map_finnhub_metric("NVDA", {"metric": {
            "grossMarginTTM": 70.0, "operatingMarginTTM": 55.0,
            "netProfitMarginTTM": 50.0, "roiTTM": 34.0, "roeTTM": 80.0,
            "revenueGrowthTTMYoy": 45.0, "peTTM": 31.0, "psTTM": 12.0,
            "pfcfShareTTM": 35.0,
        }}, "2026-05-29")
        self.assertEqual(rec["source"], "finnhub")
        self.assertAlmostEqual(rec["ratios"]["roic"], 0.34)
        self.assertEqual(rec["ratios"]["pe"], 31.0)

    def test_sec_companyfacts_prefers_ttm_when_quarterly_is_newer(self):
        from fetch import _map_sec_companyfacts

        def fact(val, start, end, filed, form, fy, fp, accn):
            return {"val": val, "start": start, "end": end, "filed": filed,
                    "form": form, "fy": fy, "fp": fp, "accn": accn}

        rev = [
            fact(1000, "2024-01-01", "2024-12-31", "2025-02-01", "10-K", 2024, "FY", "a24"),
            fact(1200, "2025-01-01", "2025-12-31", "2026-02-01", "10-K", 2025, "FY", "a25"),
            fact(250, "2024-01-01", "2024-03-31", "2024-05-01", "10-Q", 2024, "Q1", "q124"),
            fact(350, "2025-01-01", "2025-03-31", "2025-05-01", "10-Q", 2025, "Q1", "q125"),
            fact(420, "2026-01-01", "2026-03-31", "2026-05-01", "10-Q", 2026, "Q1", "q126"),
        ]
        facts = {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
            "NetIncomeLoss": {"units": {"USD": [
                fact(100, "2024-01-01", "2024-12-31", "2025-02-01", "10-K", 2024, "FY", "a24"),
                fact(150, "2025-01-01", "2025-12-31", "2026-02-01", "10-K", 2025, "FY", "a25"),
                fact(20, "2025-01-01", "2025-03-31", "2025-05-01", "10-Q", 2025, "Q1", "q125"),
                fact(30, "2026-01-01", "2026-03-31", "2026-05-01", "10-Q", 2026, "Q1", "q126"),
            ]}},
        }
        rec = _map_sec_companyfacts(
            "TTM", {"cik": 1, "facts": {"us-gaap": facts}}, "2026-05-29")
        self.assertEqual(rec["fiscal_period"], "2026-Q1-TTM")
        self.assertEqual(rec["usable_at"], "2026-05-01")
        self.assertEqual(rec["revenue"], 1270)       # 2025 FY + 2026 Q1 - 2025 Q1
        self.assertEqual(rec["revenue_prior"], 1100) # 2024 FY + 2025 Q1 - 2024 Q1
        self.assertEqual(rec["net_income"], 160)


# ────────────────────────────────────────────────────────────────────────────
class TestFundamentalAnalyst(unittest.TestCase):
    def setUp(self):
        from scorers import fundamental
        self.F = fundamental

    def test_etf_has_no_bq_and_is_not_penalised(self):
        res = self.F.analyze({"anything": 1}, "etf", price=100)
        self.assertTrue(res["bq"].is_na)
        self.assertEqual(res["bq"].na_reason, "ETF_NO_BQ")
        self.assertTrue(res["valuation"].is_na)
        self.assertTrue(res["growth"].is_na)

    def test_operating_bq(self):
        fund = {"coverage_ratio": 100, "roic": 0.20, "gross_margin": 0.60,
                "operating_margin": 0.30, "fcf_margin": 0.25, "nd_ebitda": 0.5,
                "int_cov": 10, "sbc_pct_revenue": 0.02, "revenue_growth_yoy": 0.25,
                "revenue": 1e11, "net_income": 2e10, "fcf": 2.5e10, "total_equity": 5e10,
                "shares_out": 1e9}
        res = self.F.analyze(fund, "operating_company", price=50, shares_out=1e9)
        self.assertIsNotNone(res["bq"].score)
        self.assertGreater(res["bq"].score, 60)   # strong operating profile
        self.assertIsNotNone(res["growth"].score)
        self.assertIsNotNone(res["valuation"].score)

    def test_bank_bq_uses_bank_metrics(self):
        fund = {"coverage_ratio": 100, "roe": 0.16, "roa": 0.018,
                "efficiency_ratio": 0.50, "net_interest_margin": 0.04,
                "capital_ratio": 0.14, "credit_quality": 0.4, "tangible_book": 40}
        res = self.F.analyze(fund, "bank", price=80)
        self.assertIsNotNone(res["bq"].score)
        self.assertGreater(res["bq"].score, 60)

    def test_reit_bq_uses_ffo(self):
        fund = {"coverage_ratio": 100, "ffo_per_share": 4.0, "affo_per_share": 3.6,
                "occupancy": 0.97, "debt_maturity_yrs": 8, "int_cov": 5.0,
                "dividend_safety": 1.4}
        res = self.F.analyze(fund, "reit", price=50)
        self.assertIsNotNone(res["bq"].score)
        self.assertGreater(res["bq"].score, 55)

    def test_biotech_bq_runway(self):
        fund = {"coverage_ratio": 66.7, "cash_runway_quarters": 10.0, "dilution_rate": 0.05}
        res = self.F.analyze(fund, "biotech_pre_revenue")
        self.assertIsNotNone(res["bq"].score)
        # pre-revenue → valuation/growth N/A
        self.assertTrue(res["valuation"].is_na)

    def test_low_coverage_is_insufficient(self):
        fund = {"coverage_ratio": 20, "roic": 0.1}
        res = self.F.analyze(fund, "operating_company", price=50)
        self.assertTrue(res["bq"].is_na)
        self.assertEqual(res["bq"].na_reason, "INSUFFICIENT_DATA")

    def test_missing_fundamentals(self):
        res = self.F.analyze(None, "operating_company", price=50)
        self.assertTrue(res["bq"].is_na)

    def test_synthetic_price_na_valuation_keeps_bq_growth(self):
        """A synthetic/mock close makes any multiple meaningless: valuation is
        N/A (SYNTHETIC_PRICE) while BQ and growth (SEC-only) still score."""
        fund = {"coverage_ratio": 100, "roic": 0.20, "gross_margin": 0.60,
                "operating_margin": 0.30, "fcf_margin": 0.25, "revenue_growth_yoy": 0.25,
                "revenue": 1e11, "net_income": 2e10, "fcf": 2.5e10, "total_equity": 5e10,
                "shares_out": 1e9}
        real = self.F.analyze(fund, "operating_company", price=50, shares_out=1e9,
                              price_is_synthetic=False)
        synth = self.F.analyze(fund, "operating_company", price=50, shares_out=1e9,
                               price_is_synthetic=True)
        self.assertIsNotNone(real["valuation"].score)
        self.assertTrue(synth["valuation"].is_na)
        self.assertEqual(synth["valuation"].na_reason, "SYNTHETIC_PRICE")
        # BQ + growth must be identical (price-independent)
        self.assertEqual(synth["bq"].score, real["bq"].score)
        self.assertEqual(synth["growth"].score, real["growth"].score)

    def test_is_real_price_source(self):
        from scorers.base import is_real_price_source
        self.assertFalse(is_real_price_source("synthetic"))
        self.assertFalse(is_real_price_source("MOCK"))
        self.assertTrue(is_real_price_source("finnhub"))
        self.assertTrue(is_real_price_source("yahoo"))
        self.assertTrue(is_real_price_source(None))    # unknown → trusted (legacy/manual)


# ────────────────────────────────────────────────────────────────────────────
class TestHealthScores(unittest.TestCase):
    """Piotroski / Altman / trends — honest partial computation (Task 3)."""

    def setUp(self):
        from scorers import health_scores
        self.H = health_scores

    def test_piotroski_partial_from_income_cashflow(self):
        # NI>0, CFO>0, CFO>NI → 3 available signals, f_score 3; BS signals None
        f = {"net_income": 1e9, "cfo": 1.3e9}
        r = self.H.piotroski_f_score(f)
        self.assertEqual(r["available_signals"], 3)
        self.assertEqual(r["f_score"], 3)
        self.assertTrue(r["signals"]["positive_net_income"])
        self.assertTrue(r["signals"]["cfo_gt_net_income"])
        self.assertIsNone(r["signals"]["roa_improved"])         # needs total assets
        self.assertEqual(r["interpretation"], "strong")
        self.assertIsNotNone(r["note"])                          # partial coverage flagged

    def test_piotroski_insufficient(self):
        r = self.H.piotroski_f_score({"net_income": 1e9})        # only 1 signal
        self.assertEqual(r["interpretation"], "insufficient_data")

    def test_altman_na_without_balance_sheet(self):
        r = self.H.altman_z_score({"revenue": 1e10, "operating_income": 2e9}, market_cap=5e10)
        self.assertIsNone(r["z_score"])
        self.assertFalse(r["available"])
        self.assertIn("total_assets", r["missing"])

    def test_altman_computes_with_full_balance_sheet(self):
        f = {"total_assets": 1e11, "total_liabilities": 4e10, "retained_earnings": 3e10,
             "current_assets": 5e10, "current_liabilities": 2e10,
             "operating_income": 1.5e10, "revenue": 8e10}
        r = self.H.altman_z_score(f, market_cap=1.2e11)
        self.assertTrue(r["available"])
        self.assertIsNotNone(r["z_score"])
        self.assertIn(r["zone"], ("safe", "grey", "distress"))

    def test_trends_revenue_up_others_na(self):
        r = self.H.financial_trends({"revenue_growth_yoy": 0.30})
        self.assertEqual(r["revenue_trend"], "up")
        self.assertIsNone(r["net_income_trend"])                 # no prior stored
        self.assertIsNotNone(r["note"])


# ────────────────────────────────────────────────────────────────────────────
class TestSpecialists(unittest.TestCase):
    def test_sector_theme(self):
        from scorers import sector_theme
        leading = sector_theme.analyze([
            {"name": "AI", "leadership_score": 85, "phase": "leading"},
            {"name": "XLK", "leadership_score": 70, "phase": "leading"}])
        weak = sector_theme.analyze([
            {"name": "X", "leadership_score": 20, "phase": "breakdown"}])
        self.assertGreater(leading.score, weak.score)
        self.assertEqual(sector_theme.analyze([]).score, 50.0)

    def test_macro_fit(self):
        from scorers import macro_fit
        regime = {"regime": "NARROW_THEME_LEADERSHIP",
                  "favored_themes": ["AI Infrastructure"], "avoided_themes": ["Banks"],
                  "favored_sectors": ["XLK"], "avoided_sectors": ["XLF"]}
        fav = macro_fit.analyze(["AI Infrastructure"], "XLK", regime)
        avo = macro_fit.analyze(["Banks"], "XLF", regime)
        neutral = macro_fit.analyze(["Random"], "XLE", regime)
        self.assertGreater(fav.score, 60)
        self.assertLess(avo.score, 40)
        self.assertEqual(neutral.score, 50.0)

    def test_risk_flags(self):
        from scorers import risk
        clean = risk.analyze(dq_score=95, tech_state="UPTREND", model_type="operating_company")
        downtrend = risk.analyze(dq_score=95, tech_state="DOWNTREND", model_type="operating_company")
        lev = risk.analyze(dq_score=95, tech_state="UPTREND", model_type="leveraged_etf")
        self.assertGreater(clean.score, downtrend.score)
        self.assertIn("DOWNTREND", downtrend.detail["flags"])
        self.assertIn("LEVERAGE_DECAY", lev.detail["flags"])


# ────────────────────────────────────────────────────────────────────────────
class TestSynthesis(unittest.TestCase):
    def setUp(self):
        from scorers import synthesis
        from scorers.base import PillarScore
        self.S = synthesis
        self.PS = PillarScore

    def _pillars(self, **overrides):
        d = {"bq": self.PS(70), "valuation": self.PS(60), "growth": self.PS(65),
             "technical": self.PS(75), "sector_theme": self.PS(80),
             "macro": self.PS(55), "risk": self.PS(90)}
        d.update(overrides)
        return d

    def test_composite_blend(self):
        out = self.S.synthesize(self._pillars(), days_active=10)
        self.assertTrue(40 <= out["composite"] <= 90)

    def test_na_pillar_excluded_not_zeroed(self):
        """ETF BQ N/A must not drag the composite down."""
        full = self.S.synthesize(self._pillars(), days_active=10)["composite"]
        with_na = self.S.synthesize(
            self._pillars(bq=self.PS.na("ETF_NO_BQ")), days_active=10)["composite"]
        # excluding a mid pillar shouldn't crater the score toward 0
        self.assertGreater(with_na, full - 15)

    def test_core_requires_days_active(self):
        strong = self._pillars(bq=self.PS(90), technical=self.PS(90),
                               sector_theme=self.PS(90), growth=self.PS(85))
        core = self.S.synthesize(strong, days_active=10)
        tact = self.S.synthesize(strong, days_active=2)
        self.assertEqual(core["candidate_type"], "Core")
        self.assertEqual(tact["candidate_type"], "Tactical")

    def test_coverage_aware_dq(self):
        # operating low coverage → DQ docked; ETF N/A → not docked
        op = self.S.synthesize(self._pillars(), days_active=5,
                               technical_dq=100, model_type="operating_company",
                               bq_coverage=20)
        etf = self.S.synthesize(self._pillars(bq=self.PS.na("ETF_NO_BQ")), days_active=5,
                                technical_dq=100, model_type="etf", bq_coverage=None)
        self.assertLess(op["dq_score"], 100)
        self.assertEqual(etf["dq_score"], 100)


# ────────────────────────────────────────────────────────────────────────────
class _TempDBTest(unittest.TestCase):
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

    def _fetcher(self):
        from fetch import Fetcher
        return Fetcher(synthetic=True, store_raw=False, as_of=self.MD)


class TestFinancialsPipeline(_TempDBTest):
    def test_clean_normalize_read(self):
        import financials as fin
        mts = {"NVDA": "operating_company", "AAPL": "operating_company",
               "MSFT": "operating_company", "AVGO": "operating_company",
               "JPM": "bank", "O": "reit", "CRSP": "biotech_pre_revenue"}
        secs = {"NVDA": "Information Technology", "AAPL": "Information Technology",
                "MSFT": "Information Technology", "AVGO": "Information Technology",
                "JPM": "Financials", "O": "Real Estate", "CRSP": "Health Care"}
        fin.build_financials(list(mts), fetcher=self._fetcher(), as_of=self.MD,
                             model_types=mts, sectors=secs)
        with self._db.cloud(readonly=True) as c:
            op = fin.get_fundamentals(c, "NVDA", "operating_company", self.MD)
            bk = fin.get_fundamentals(c, "JPM", "bank", self.MD)
            rt = fin.get_fundamentals(c, "O", "reit", self.MD)
            bio = fin.get_fundamentals(c, "CRSP", "biotech_pre_revenue", self.MD)
        self.assertIsNotNone(op["roic"])
        self.assertIsNotNone(op["op_margin_zscore"])  # 4 IT peers → z-score exists
        self.assertEqual(op["coverage_ratio"], 100.0)
        self.assertIsNotNone(bk["roe"])
        self.assertIsNotNone(rt["ffo_per_share"])
        self.assertIsNotNone(bio["cash_runway_quarters"])

    def test_point_in_time(self):
        import financials as fin
        mts = {"NVDA": "operating_company"}
        secs = {"NVDA": "Information Technology"}
        fin.build_financials(["NVDA"], fetcher=self._fetcher(), as_of=self.MD,
                             model_types=mts, sectors=secs)
        with self._db.cloud(readonly=True) as c:
            # before the report's usable_at (~5 weeks earlier) → nothing
            early = fin.get_fundamentals(c, "NVDA", "operating_company", "2026-03-01")
            now = fin.get_fundamentals(c, "NVDA", "operating_company", self.MD)
            cache_now = fin._has_fresh_fundamentals(c, "NVDA", "operating_company", 7, self.MD)
            cache_early = fin._has_fresh_fundamentals(c, "NVDA", "operating_company", 7, "2026-03-01")
        self.assertIsNone(early)
        self.assertIsNotNone(now)
        self.assertTrue(cache_now)
        self.assertFalse(cache_early)

    def test_provider_ratio_fallback_round_trip(self):
        import financials as fin
        from scorers import fundamental

        rec = {
            "source": "finnhub", "fiscal_period": "2026-Q1",
            "report_date": None, "usable_at": self.MD,
            "ratios": {
                "gross_margin": 0.62, "operating_margin": 0.33,
                "net_margin": 0.24, "roic": 0.18, "roe": 0.30,
                "revenue_growth_yoy": 0.22, "pe": 24.0,
                "ps": 8.0, "pfcf": 28.0, "pb": 7.0,
            },
        }
        with self._db.cloud() as c:
            self.assertTrue(fin.clean_and_store(c, "RATIO", "operating_company", rec))
            fin.normalize_operating(
                c, self.MD, {"RATIO": "Information Technology"},
                {"RATIO": "operating_company"})
        with self._db.cloud(readonly=True) as c:
            fund = fin.get_fundamentals(c, "RATIO", "operating_company", self.MD)

        self.assertIsNotNone(fund)
        self.assertAlmostEqual(fund["roic"], 0.18)
        self.assertGreater(fund["coverage_ratio"], 50)
        self.assertEqual(fund["pe"], 24.0)
        res = fundamental.analyze(fund, "operating_company")
        self.assertIsNotNone(res["bq"].score)
        self.assertIsNotNone(res["valuation"].score)


class TestScoringEndToEnd(_TempDBTest):
    def test_phase45_versions_aligned(self):
        import feature_builder, financials, signal_generator, views_builder
        self.assertEqual(feature_builder.FEATURE_VERSION, "4.5.0")
        self.assertEqual(financials.FEATURE_VERSION, "4.5.0")
        self.assertEqual(signal_generator.FEATURE_VERSION, "4.5.0")
        self.assertEqual(views_builder.FEATURE_VERSION, "4.5.0")

    def test_full_snapshot_all_pillars(self):
        import daily_snapshot, views_builder, alerts
        tv = Path(self._tmp) / "views"; tv.mkdir(exist_ok=True)
        views_builder.VIEWS_DIR = tv
        alerts.VIEWS_DIR = tv
        out = daily_snapshot.run(market_date=self.MD, synthetic=True,
                                 limit=50, skip_git=True)
        self.assertEqual(out["result_status"], "success")
        self.assertGreater(out["steps"]["fundamentals"]["stored"], 20)
        self.assertGreater(out["steps"]["features_deep"]["fundamentals_used"], 20)
        with self._db.cloud(readonly=True) as c:
            # Stage-2 rows carry the full pillar set
            withbq = c.execute("SELECT COUNT(*) n FROM features_daily "
                               "WHERE date=? AND bq_score IS NOT NULL", (self.MD,)).fetchone()["n"]
            etfbq = c.execute("SELECT COUNT(*) n FROM features_daily WHERE date=? "
                              "AND model_type IN ('etf','leveraged_etf') AND bq_score IS NOT NULL",
                              (self.MD,)).fetchone()["n"]
        self.assertGreater(withbq, 20)
        self.assertEqual(etfbq, 0, "ETFs must never receive a BQ score (§6)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
