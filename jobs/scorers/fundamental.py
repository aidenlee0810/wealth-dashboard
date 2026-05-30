"""
jobs/scorers/fundamental.py — the Fundamental Analyst (Plan §6).

Produces three pillars from a model-type-aware fundamental record:
    business_quality (BQ)   how good is the business?
    valuation               how cheap is it (price vs fundamentals)?
    growth                  how fast is it growing?

The cardinal rule of §6: judge each business by the right yardstick, and treat
"doesn't apply" (ETF has no BQ) differently from "we lack the data". Every
return is a PillarScore so the synthesis engine can reason about coverage and
N/A uniformly.

Inputs come from financials.get_fundamentals() (uniform dict) plus an optional
market_cap for valuation.
"""

from __future__ import annotations

from typing import Optional

from .base import (PillarScore, clamp, scale, weighted_blend, coverage,
                   NA_ETF_NO_BQ, NA_INSUFFICIENT, NA_NO_FUNDAMENTALS,
                   NA_SYNTHETIC_PRICE, WARN_LOW_COVERAGE)
from .model_types import metric_specs, has_business_quality

COVERAGE_FLOOR = 30.0      # below this, BQ is N/A (INSUFFICIENT_DATA)
COVERAGE_WARN = 60.0       # below this, flag LOW_COVERAGE


# ===========================================================================
# Public entry point
# ===========================================================================
def analyze(fund: Optional[dict], model_type: str, *,
            price: Optional[float] = None, shares_out: Optional[float] = None,
            market_cap: Optional[float] = None,
            price_is_synthetic: bool = False) -> dict:
    """Return {'bq', 'valuation', 'growth'} PillarScores for a ticker.

    price/shares_out are used for valuation (market_cap = price × shares_out if
    not given explicitly; price is also needed for per-share REIT/bank multiples).
    When `price_is_synthetic` the valuation pillar is N/A'd — a P/E built from a
    mock close is meaningless — while BQ and growth (SEC-only) still compute."""
    if model_type in ("etf", "leveraged_etf"):
        na = PillarScore.na(NA_ETF_NO_BQ, reasons=["ETF — 개별기업 펀더멘털 미적용"])
        return {"bq": na, "valuation": na, "growth": na}

    if fund is None:
        na = PillarScore.na(NA_NO_FUNDAMENTALS, reasons=["펀더멘털 데이터 없음"])
        return {"bq": na, "valuation": na, "growth": na}

    if market_cap is None and price and (shares_out or fund.get("shares_out")):
        market_cap = price * (shares_out or fund.get("shares_out"))
    if price is not None:
        fund = {**fund, "_price": price}

    return {
        "bq": business_quality(fund, model_type),
        "valuation": valuation(fund, model_type, market_cap,
                               price_is_synthetic=price_is_synthetic),
        "growth": growth(fund, model_type),
    }


# ===========================================================================
# Business Quality (model-type specific)
# ===========================================================================
def business_quality(fund: dict, model_type: str) -> PillarScore:
    if not has_business_quality(model_type):
        return PillarScore.na(NA_ETF_NO_BQ)

    # Model-specific coverage: a bank/REIT judged from SEC *generic* facts often
    # has only ROE/ROA (bank) or no FFO/occupancy (REIT) — the normalized
    # coverage_ratio (computed over operating-company line items) overstates how
    # well we actually know it. Use the more conservative of the two so sparse
    # bank/REIT data is honestly flagged (Task 4).
    required = metric_specs(model_type)
    cov = fund.get("coverage_ratio")
    model_cov = coverage(required, fund) if required else None
    eff_cov = cov
    if model_cov is not None:
        eff_cov = model_cov if cov is None else min(cov, model_cov)

    if eff_cov is not None and eff_cov < COVERAGE_FLOOR:
        why = (f"{model_type} 전용 지표 커버리지 {eff_cov:.0f}% (<30%) — SEC generic facts 부족"
               if model_type in ("bank", "reit") else
               f"펀더멘털 커버리지 {eff_cov:.0f}% (<30%)")
        return PillarScore.na(NA_INSUFFICIENT, coverage_ratio=eff_cov, reasons=[why])

    if model_type in ("operating_company", "insurance"):
        score, reasons = _bq_operating(fund)
    elif model_type == "bank":
        score, reasons = _bq_bank(fund)
    elif model_type == "reit":
        score, reasons = _bq_reit(fund)
    elif model_type == "biotech_pre_revenue":
        score, reasons = _bq_biotech(fund)
    else:
        return PillarScore.na(NA_INSUFFICIENT)

    if score is None:
        return PillarScore.na(NA_INSUFFICIENT, coverage_ratio=eff_cov)
    warning = WARN_LOW_COVERAGE if (eff_cov is not None and eff_cov < COVERAGE_WARN) else None
    if warning and model_type in ("bank", "reit"):
        reasons = list(reasons) + [f"⚠️ {model_type} 전용 데이터 부족 (커버리지 {eff_cov:.0f}%)"]
    return PillarScore(score=round(score, 1), coverage_ratio=eff_cov,
                       warning=warning, reasons=reasons,
                       detail={k: fund.get(k) for k in required})


def _bq_operating(f: dict):
    # Each sub-score 0-100; blend, renormalising over what's present.
    roic = scale(f.get("roic"), 0.0, 0.25)            # 25% ROIC → 100
    gm = scale(f.get("gross_margin"), 0.20, 0.70)
    om = scale(f.get("operating_margin"), 0.0, 0.35)
    fcfm = scale(f.get("fcf_margin"), -0.05, 0.30)
    leverage = scale(f.get("nd_ebitda"), 4.0, 0.0)    # inverted: lower ND/EBITDA better
    intcov = scale(f.get("int_cov"), 1.0, 12.0)
    sbc = scale(f.get("sbc_pct_revenue"), 0.10, 0.0)  # inverted: less dilution better
    blended = weighted_blend([
        (roic, 0.28), (fcfm, 0.22), (om, 0.16), (gm, 0.12),
        (leverage, 0.12), (intcov, 0.06), (sbc, 0.04),
    ])
    reasons = []
    if f.get("roic") is not None:
        reasons.append(f"ROIC {f['roic']*100:.1f}%")
    if f.get("fcf_margin") is not None:
        reasons.append(f"FCF 마진 {f['fcf_margin']*100:.1f}%")
    if f.get("nd_ebitda") is not None:
        reasons.append(f"순부채/EBITDA {f['nd_ebitda']:.1f}x")
    # peer z-score nudge (±5)
    if blended is not None and f.get("roic_zscore") is not None:
        blended = clamp(blended + max(-5, min(5, f["roic_zscore"] * 2.5)))
    return blended, reasons


def _bq_bank(f: dict):
    roe = scale(f.get("roe"), 0.05, 0.18)
    eff = scale(f.get("efficiency_ratio"), 0.75, 0.45)   # inverted: lower better
    nim = scale(f.get("net_interest_margin"), 0.015, 0.045)
    cap = scale(f.get("capital_ratio"), 0.08, 0.15)
    credit = scale(f.get("credit_quality"), 3.0, 0.3)    # inverted: lower NPA better
    blended = weighted_blend([
        (roe, 0.30), (eff, 0.22), (cap, 0.20), (credit, 0.18), (nim, 0.10),
    ])
    reasons = []
    if f.get("roe") is not None:
        reasons.append(f"ROE {f['roe']*100:.1f}%")
    if f.get("efficiency_ratio") is not None:
        reasons.append(f"효율성 비율 {f['efficiency_ratio']*100:.0f}%")
    if f.get("capital_ratio") is not None:
        reasons.append(f"자본비율 {f['capital_ratio']*100:.1f}%")
    return blended, reasons


def _bq_reit(f: dict):
    occ = scale(f.get("occupancy"), 0.85, 0.98)
    divsafe = scale(f.get("dividend_safety"), 1.0, 1.5)  # AFFO/div coverage
    intcov = scale(f.get("int_cov"), 1.5, 6.0)
    maturity = scale(f.get("debt_maturity_yrs"), 2.0, 9.0)
    affo_premium = None
    if f.get("affo_per_share") and f.get("ffo_per_share"):
        affo_premium = scale(f["affo_per_share"] / f["ffo_per_share"], 0.7, 1.0)
    blended = weighted_blend([
        (occ, 0.30), (divsafe, 0.28), (intcov, 0.20),
        (maturity, 0.12), (affo_premium, 0.10),
    ])
    reasons = []
    if f.get("occupancy") is not None:
        reasons.append(f"점유율 {f['occupancy']*100:.1f}%")
    if f.get("dividend_safety") is not None:
        reasons.append(f"배당안전성(AFFO/배당) {f['dividend_safety']:.2f}x")
    return blended, reasons


def _bq_biotech(f: dict):
    # Quality = survival runway + low dilution. Pre-revenue → no margins.
    runway = scale(f.get("cash_runway_quarters"), 2.0, 12.0)   # 3 yrs cash → 100
    dilution = scale(f.get("dilution_rate"), 0.30, 0.0)        # inverted: less dilution better
    blended = weighted_blend([(runway, 0.65), (dilution, 0.35)])
    reasons = []
    if f.get("cash_runway_quarters") is not None:
        reasons.append(f"현금 런웨이 {f['cash_runway_quarters']:.1f}분기")
    if f.get("dilution_rate") is not None:
        reasons.append(f"희석률 {f['dilution_rate']*100:.1f}%")
    return blended, reasons


# ===========================================================================
# Valuation
# ===========================================================================
def valuation(fund: dict, model_type: str, market_cap: Optional[float], *,
              price_is_synthetic: bool = False) -> PillarScore:
    if not has_business_quality(model_type):
        return PillarScore.na(NA_ETF_NO_BQ)

    # A valuation multiple is only as real as its price. On a synthetic/mock
    # close (e.g. offline smoke runs) the multiple is noise — N/A it rather than
    # publish a confident P/E nobody should trust.
    if price_is_synthetic:
        return PillarScore.na(NA_SYNTHETIC_PRICE, coverage_ratio=0.0,
                              reasons=["합성(synthetic) 가격 — 밸류에이션 신뢰 불가"])

    if model_type == "reit":
        # P/FFO (annualised: ffo_per_share is quarterly → ×4)
        ffo = fund.get("ffo_per_share")
        price = fund.get("_price")
        if ffo and price:
            p_ffo = price / (ffo * 4)
            score = scale(p_ffo, 30.0, 10.0)   # lower P/FFO cheaper
            return PillarScore(score=round(score, 1),
                               reasons=[f"P/FFO {p_ffo:.1f}x"], detail={"p_ffo": p_ffo})
        return PillarScore.na(NA_INSUFFICIENT, reasons=["P/FFO 계산 불가"])

    if model_type == "biotech_pre_revenue":
        # mcap vs cash (lower = more downside support). Pre-revenue → rough proxy.
        return PillarScore.na(NA_INSUFFICIENT, reasons=["선매출 바이오 — 전통적 밸류에이션 미적용"])

    if model_type == "bank":
        # P/TBV if tangible book per share available
        tbv, price = fund.get("tangible_book"), fund.get("_price")
        if tbv and price:
            p_tbv = price / tbv
            score = scale(p_tbv, 2.5, 0.8)    # lower P/TBV cheaper
            return PillarScore(score=round(score, 1),
                               reasons=[f"P/TBV {p_tbv:.2f}x"], detail={"p_tbv": p_tbv})
        return PillarScore(score=50.0, reasons=["밸류에이션 데이터 부족 — 중립"])

    # operating_company / insurance
    ni, fcf, rev = fund.get("net_income"), fund.get("fcf"), fund.get("revenue")
    eq = fund.get("total_equity")
    subs, reasons, detail = [], [], {}
    if market_cap and fcf and fcf > 0:
        p_fcf = market_cap / fcf
        subs.append((scale(p_fcf, 40.0, 12.0), 0.45)); reasons.append(f"P/FCF {p_fcf:.1f}x")
        detail["p_fcf"] = round(p_fcf, 1)
    elif fcf is not None and fcf <= 0:
        subs.append((10.0, 0.45)); reasons.append("FCF 적자 — 밸류에이션 부담")
    if market_cap and ni and ni > 0:
        pe = market_cap / ni
        subs.append((scale(pe, 45.0, 12.0), 0.30)); reasons.append(f"P/E {pe:.1f}x")
        detail["pe"] = round(pe, 1)
    if market_cap and rev and rev > 0:
        ps = market_cap / rev
        subs.append((scale(ps, 15.0, 1.0), 0.15)); reasons.append(f"P/S {ps:.1f}x")
    if market_cap and eq and eq > 0:
        pb = market_cap / eq
        subs.append((scale(pb, 12.0, 1.0), 0.10))

    # Provider-ratio fallback (e.g. Finnhub /stock/metric) when full SEC line
    # items or shares are unavailable. These are lower-traceability than SEC
    # line items, but better than dropping valuation entirely.
    if not subs:
        pfcf, pe, ps, pb = fund.get("pfcf"), fund.get("pe"), fund.get("ps"), fund.get("pb")
        if pfcf and pfcf > 0:
            subs.append((scale(pfcf, 40.0, 12.0), 0.45)); reasons.append(f"P/FCF {pfcf:.1f}x")
            detail["p_fcf"] = round(pfcf, 1)
        if pe and pe > 0:
            subs.append((scale(pe, 45.0, 12.0), 0.30)); reasons.append(f"P/E {pe:.1f}x")
            detail["pe"] = round(pe, 1)
        if ps and ps > 0:
            subs.append((scale(ps, 15.0, 1.0), 0.15)); reasons.append(f"P/S {ps:.1f}x")
        if pb and pb > 0:
            subs.append((scale(pb, 12.0, 1.0), 0.10))

    blended = weighted_blend(subs)
    if blended is None:
        msg = "시가총액/비율 데이터 부족 — 밸류에이션 불가"
        return PillarScore.na(NA_INSUFFICIENT, reasons=[msg])
    return PillarScore(score=round(blended, 1), reasons=reasons, detail=detail)


# ===========================================================================
# Growth
# ===========================================================================
def growth(fund: dict, model_type: str) -> PillarScore:
    if not has_business_quality(model_type):
        return PillarScore.na(NA_ETF_NO_BQ)

    if model_type in ("operating_company", "insurance"):
        g = fund.get("revenue_growth_yoy")
        if g is None:
            return PillarScore.na(NA_INSUFFICIENT, reasons=["매출성장률 없음"])
        score = scale(g, -0.05, 0.35)   # 35% YoY → 100, -5% → 0
        if fund.get("revenue_growth_zscore") is not None:
            score = clamp(score + max(-5, min(5, fund["revenue_growth_zscore"] * 2.5)))
        return PillarScore(score=round(score, 1),
                           reasons=[f"매출성장 YoY {g*100:.1f}%"],
                           detail={"revenue_growth_yoy": g})

    if model_type == "biotech_pre_revenue":
        # Growth ~ pipeline; proxy with inverse dilution + runway (already in BQ).
        return PillarScore.na(NA_INSUFFICIENT, reasons=["선매출 — 성장은 파이프라인 기반(Phase 5+)"])

    # bank / reit — growth needs loan/FFO growth history (Phase 5)
    return PillarScore(score=50.0, reasons=["성장 데이터 제한 — 중립"], coverage_ratio=fund.get("coverage_ratio"))
