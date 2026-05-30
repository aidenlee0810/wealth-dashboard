"""
jobs/scorers/health_scores.py — SEC-based composite health scores (Plan §6 enrichment).

Piotroski F-score and Altman Z-score from whatever the SEC CompanyFacts mapping
provides, computed HONESTLY: each sub-signal is True / False / None(unavailable),
and the headline is N/A when too few inputs exist. SEC *generic* facts give us
the income statement + cash flow + a little balance sheet (debt, equity, shares),
but not total assets / current ratio / retained earnings — so several Piotroski
signals and all of Altman Z are "데이터 없음" until those concepts are mapped.
No fabrication: a number only appears when its inputs are real.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Piotroski F-score (0–9) — partial-aware
# ---------------------------------------------------------------------------
def piotroski_f_score(f: dict) -> dict:
    """Compute the Piotroski signals the data supports; mark the rest None.

    Computable from SEC generic facts (income + cash flow):
        positive_net_income, positive_cfo, cfo_gt_net_income (accrual quality)
    Computable iff prior-period stored:
        no_dilution (shares vs prior), gross_margin_up
    Needs balance-sheet items we don't yet map → None:
        roa_improved, lower_leverage, higher_current_ratio, higher_asset_turnover
    """
    ni = f.get("net_income")
    cfo = f.get("cfo")

    signals: dict[str, Optional[bool]] = {}
    signals["positive_net_income"] = (ni > 0) if ni is not None else None
    signals["positive_cfo"] = (cfo > 0) if cfo is not None else None
    signals["cfo_gt_net_income"] = (
        (cfo > ni) if (cfo is not None and ni is not None) else None)

    sh, shp = f.get("shares_out"), f.get("shares_out_prior")
    signals["no_dilution"] = (sh <= shp) if (sh is not None and shp) else None

    gm, gmp = f.get("gross_margin"), f.get("gross_margin_prior")
    signals["gross_margin_up"] = (gm > gmp) if (gm is not None and gmp is not None) else None

    # balance-sheet-dependent → unavailable until mapped
    signals["roa_improved"] = None
    signals["lower_leverage"] = None
    signals["higher_current_ratio"] = None
    signals["higher_asset_turnover"] = None

    available = {k: v for k, v in signals.items() if v is not None}
    score = sum(1 for v in available.values() if v)
    n_avail = len(available)
    if n_avail == 0:
        interp = "insufficient_data"
    elif n_avail < 3:
        interp = "insufficient_data"
    elif score >= n_avail * 0.75:
        interp = "strong"
    elif score <= n_avail * 0.34:
        interp = "weak"
    else:
        interp = "mixed"

    return {
        "f_score": score,
        "available_signals": n_avail,
        "max_signals": 9,
        "signals": signals,
        "interpretation": interp,
        "note": (None if n_avail >= 9 else
                 "잔액표 항목(총자산/유동비율/부채 추세) 부족 — 9개 중 일부만 계산"),
    }


# ---------------------------------------------------------------------------
# Altman Z-score (manufacturing model)
# ---------------------------------------------------------------------------
def altman_z_score(f: dict, market_cap: Optional[float] = None) -> dict:
    """Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA.

    Needs total assets, total liabilities, working capital and retained earnings.
    SEC generic facts don't give these yet → honest N/A with the missing list."""
    ta = f.get("total_assets")
    tl = f.get("total_liabilities")
    re = f.get("retained_earnings")
    ca, cl = f.get("current_assets"), f.get("current_liabilities")
    wc = (ca - cl) if (ca is not None and cl is not None) else None
    ebit = f.get("operating_income")
    sales = f.get("revenue")

    missing = [n for n, v in (("total_assets", ta), ("total_liabilities", tl),
                              ("working_capital", wc), ("retained_earnings", re))
               if v is None]
    if missing or not ta or ta <= 0:
        return {"z_score": None, "available": False,
                "missing": missing or ["total_assets"],
                "note": "Altman Z는 잔액표 항목(총자산·총부채·운전자본·이익잉여금) 필요 — 데이터 없음"}

    z = (1.2 * (wc / ta) + 1.4 * (re / ta) + 3.3 * ((ebit or 0) / ta)
         + 0.6 * ((market_cap or 0) / tl if tl else 0) + 1.0 * ((sales or 0) / ta))
    zone = "safe" if z > 2.99 else "distress" if z < 1.81 else "grey"
    return {"z_score": round(z, 2), "available": True, "zone": zone, "missing": []}


# ---------------------------------------------------------------------------
# Revenue / income / FCF trend
# ---------------------------------------------------------------------------
def financial_trends(f: dict) -> dict:
    """Trend direction from the precomputed YoY growth (revenue) + prior-period
    line items where stored; honest N/A for the rest."""
    rg = f.get("revenue_growth_yoy")

    def _trend(curr, prior):
        if curr is None or prior in (None, 0):
            return None
        ch = curr / prior - 1.0
        return "up" if ch > 0.02 else "down" if ch < -0.02 else "flat"

    rev_trend = ("up" if (rg is not None and rg > 0.02) else
                 "down" if (rg is not None and rg < -0.02) else
                 "flat" if rg is not None else None)
    return {
        "revenue_growth_yoy": rg,
        "revenue_trend": rev_trend,
        "net_income_trend": _trend(f.get("net_income"), f.get("net_income_prior")),
        "fcf_trend": _trend(f.get("fcf"), f.get("fcf_prior")),
        "note": (None if f.get("net_income_prior") is not None
                 else "순이익/FCF 추세는 이전기 데이터 미저장 시 계산 불가"),
    }


def compute_all(f: dict, market_cap: Optional[float] = None) -> dict:
    """Bundle: Piotroski + Altman + trends for one ticker's fundamentals dict."""
    return {
        "piotroski": piotroski_f_score(f),
        "altman_z": altman_z_score(f, market_cap),
        "trends": financial_trends(f),
    }
