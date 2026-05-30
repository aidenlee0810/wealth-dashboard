"""
jobs/scorers/model_types.py — classify a ticker's scoring model (Plan §6).

Different businesses must be judged by different yardsticks. Applying ROIC to a
bank, or FCF margin to a pre-revenue biotech, produces nonsense. This module
assigns each ticker a model_type so the fundamental analyst picks the right
metric set.

    operating_company    default — ROIC, margins, FCF, leverage
    bank                 ROE, efficiency ratio, NIM, capital
    insurance            combined ratio, book value, ROE
    reit                 FFO/AFFO, occupancy, debt maturity
    biotech_pre_revenue  cash runway, dilution (revenue < $50M, Health Care)
    etf                  no BQ — holdings/liquidity only
    leveraged_etf        decay warning, no BQ

Resolution order (most authoritative first):
    1. explicit seed (core_watchlist model_type) — human-curated
    2. ETF / leverage flags (is_etf, known leveraged tickers)
    3. industry keywords (bank / insurance / reit)
    4. sector + revenue heuristics (biotech)
    5. operating_company (fallback)
"""

from __future__ import annotations

from typing import Optional

VALID_MODEL_TYPES = (
    "operating_company", "bank", "insurance", "reit",
    "biotech_pre_revenue", "etf", "leveraged_etf",
)

# Broad-market / sector / style ETFs (no single-name fundamentals).
KNOWN_ETFS = {
    "SPY", "QQQ", "QQQM", "IWM", "DIA", "VUG", "SCHG", "VTI", "VOO", "IVV",
    "XLK", "XLV", "XLF", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLRE", "XLB",
    "SMH", "SOXX", "IBB", "XBI", "ARKK", "VGT", "VTV", "VEA", "VWO", "AGG", "BND",
    "GLD", "SLV", "TLT", "HYG", "LQD", "EEM", "EFA",
}

# 2x/3x leveraged + single-stock leveraged ETFs (decay risk).
KNOWN_LEVERAGED = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "TECL", "TECS", "SPXL", "SPXS", "UPRO",
    "TNA", "FAS", "LABU", "UVXY", "TMF",
    # single-stock leveraged
    "NVDL", "TSLL", "METU", "GGLL", "AMZZ", "AAPU", "MSFU", "CONL", "NVDU",
}

# Known banks / diversified financials judged on the bank model.
KNOWN_BANKS = {
    "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "COF",
    "SCHW", "BK", "STT", "ALLY", "SOFI", "FITB", "MTB", "HBAN", "RF", "KEY",
}

KNOWN_INSURERS = {
    "BRK.B", "PGR", "TRV", "ALL", "CB", "AIG", "MET", "PRU", "AFL", "HIG", "CINF",
}

# Financials-sector names that are NOT banks (payment networks, exchanges, asset
# managers, fintech). XLF sector-holdings seed them 'bank' by default; force
# operating_company so they aren't judged on NIM / efficiency ratio.
KNOWN_NON_BANK_FINANCIALS = {
    "V", "MA", "AXP", "PYPL", "FI", "FIS", "GPN", "COIN", "HOOD",
    "SPGI", "MCO", "MSCI", "ICE", "CME", "NDAQ", "CBOE",
    "BLK", "BX", "KKR", "APO", "ARES", "BAM", "TROW", "AMP",
    "V.US",
}

# Known REITs (sector Real Estate also triggers reit).
KNOWN_REITS = {
    "PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR", "VICI",
    "AVB", "EQR", "SBAC", "ARE", "INVH", "MAA", "EXR", "IRM",
}

# Pre-revenue / clinical-stage biotechs (would otherwise look like broken
# operating companies). Sector=Health Care + tiny revenue also triggers this.
KNOWN_PRE_REV_BIOTECH = {
    "CRSP", "NTLA", "BEAM", "EDIT", "SANA", "VERV", "RXRX", "SDGR",
}

_BANK_KEYWORDS = ("bank", "banc", "bancorp", "bancshares")
_INSURANCE_KEYWORDS = ("insurance", "insurers", "reinsurance")
_REIT_KEYWORDS = ("reit", "real estate investment")

BIOTECH_REVENUE_CEILING = 50_000_000  # $50M (§6)


def classify(
    ticker: str,
    *,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    is_etf: bool = False,
    is_leveraged: bool = False,
    revenue_ttm: Optional[float] = None,
    seed_model_type: Optional[str] = None,
) -> str:
    """Return the model_type for a ticker. Always returns a valid type."""
    t = (ticker or "").upper()
    ind = (industry or "").lower()

    # 0. ETF / leverage flags take priority over any seed.
    if t in KNOWN_LEVERAGED or is_leveraged:
        return "leveraged_etf"
    if t in KNOWN_ETFS or is_etf or seed_model_type in ("etf", "leveraged_etf"):
        return "etf"

    # Known non-bank financials must never inherit a sector-wide 'bank' seed.
    if t in KNOWN_NON_BANK_FINANCIALS:
        return "operating_company"

    # 1. Human-curated seed wins — except a 'bank' seed (often a whole-XLF
    #    default) must be corroborated by a known bank or a bank industry name.
    if seed_model_type in VALID_MODEL_TYPES and seed_model_type not in ("etf", "leveraged_etf"):
        if seed_model_type == "bank":
            if t in KNOWN_BANKS or any(k in ind for k in _BANK_KEYWORDS):
                return "bank"
            # else fall through to refine
        else:
            return seed_model_type

    # 3. Known single-name overrides
    if t in KNOWN_BANKS:
        return "bank"
    if t in KNOWN_INSURERS:
        return "insurance"
    if t in KNOWN_REITS:
        return "reit"
    if t in KNOWN_PRE_REV_BIOTECH:
        return "biotech_pre_revenue"

    # 4. Industry keywords
    ind = (industry or "").lower()
    if any(k in ind for k in _BANK_KEYWORDS):
        return "bank"
    if any(k in ind for k in _INSURANCE_KEYWORDS):
        return "insurance"
    if any(k in ind for k in _REIT_KEYWORDS):
        return "reit"

    # 5. Sector heuristics
    sec = (sector or "").lower()
    if "real estate" in sec:
        return "reit"
    if "health" in sec and revenue_ttm is not None and revenue_ttm < BIOTECH_REVENUE_CEILING:
        return "biotech_pre_revenue"

    # 6. fallback
    return "operating_company"


def metric_specs(model_type: str) -> list[str]:
    """Required fundamental metrics per model type (drives coverage_ratio, §6)."""
    return {
        "operating_company": ["roic", "gross_margin", "operating_margin",
                              "fcf_margin", "revenue_growth_yoy", "nd_ebitda", "int_cov"],
        "bank": ["roe", "roa", "efficiency_ratio", "capital_ratio", "credit_quality"],
        "insurance": ["combined_ratio", "book_value_growth", "investment_yield", "roe"],
        "reit": ["ffo_per_share", "occupancy", "debt_maturity_yrs", "int_cov", "dividend_safety"],
        "biotech_pre_revenue": ["cash_runway_quarters", "dilution_rate", "rd_intensity"],
        "etf": [],
        "leveraged_etf": [],
    }.get(model_type, [])


def has_business_quality(model_type: str) -> bool:
    """ETFs / leveraged ETFs have no single-name business quality (§6)."""
    return model_type not in ("etf", "leveraged_etf")


if __name__ == "__main__":
    samples = [
        ("NVDA", {"sector": "Information Technology"}),
        ("JPM", {"sector": "Financials"}),
        ("SOFI", {"sector": "Financials", "seed_model_type": "bank"}),
        ("O", {"sector": "Real Estate"}),
        ("SPY", {}),
        ("TQQQ", {}),
        ("CRSP", {"sector": "Health Care", "revenue_ttm": 10_000_000}),
        ("PGR", {"sector": "Financials"}),
        ("AAPL", {"sector": "Information Technology"}),
    ]
    for tk, kw in samples:
        print(f"{tk:6} -> {classify(tk, **kw)}")
