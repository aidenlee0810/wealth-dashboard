"""
jobs/financials.py — 4-layer fundamental pipeline, Layers 1 & 2 (Plan §6, §24).

    Layer 0  raw_api_responses     (fetch.py)
    Layer 1  cleaned_financials    (THIS) — typed, USD line items
             bank_fundamentals_q / reit_fundamentals_q  (model-specific ratios)
    Layer 2  normalized_financials (THIS) — ratios + sector peer z-scores + coverage
    Layer 3  features_daily        (feature_builder.py) — final scores

Why layered? When NVDA's fcf_margin_score is 78 we can trace it back through
Layer 2 (zscore vs peers) → Layer 1 (the cleaned line items) → Layer 0 (the raw
blob) with no API re-call. Each layer is independently testable.

Point-in-time: every row carries usable_at (report_date + 1d). Readers filter
usable_at <= decision_date so a backtest never sees a report before it was filed.

Routing by model_type:
    operating_company / insurance / biotech_pre_revenue → cleaned_financials → normalized_financials
    bank → bank_fundamentals_q  ·  reit → reit_fundamentals_q  (already ratio-form)
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from scorers.base import zscore, coverage  # noqa: E402
from scorers.model_types import metric_specs  # noqa: E402

FEATURE_VERSION = "4.5.0"
TAX_RATE = 0.21
LINE_ITEM_TYPES = ("operating_company", "insurance", "biotech_pre_revenue")


# ---------------------------------------------------------------------------
# Layer 1 — clean + store raw record into the right table
# ---------------------------------------------------------------------------
def clean_and_store(conn, ticker: str, model_type: str, rec: dict,
                    sector: Optional[str] = None) -> bool:
    """Persist a fetched fundamental record to its Layer-1 table. Returns stored?"""
    if rec is None:
        return False
    src = rec.get("source", "unknown")
    fp = rec.get("fiscal_period")
    if not fp:
        return False
    ratios = rec.get("ratios") or {}

    if model_type == "bank":
        db.upsert(conn, "bank_fundamentals_q", {
            "ticker": ticker, "fiscal_period": fp,
            "report_date": rec.get("report_date"), "usable_at": rec.get("usable_at"),
            "net_interest_margin": _first(rec.get("net_interest_margin"), ratios.get("net_interest_margin")),
            "efficiency_ratio": rec.get("efficiency_ratio"),
            "tangible_book": rec.get("tangible_book"),
            "capital_ratio": rec.get("capital_ratio"),
            "credit_quality": rec.get("credit_quality"),
            "roa": _first(rec.get("roa"), ratios.get("roa")),
            "roe": _first(rec.get("roe"), ratios.get("roe")), "source": src,
        }, conflict_cols=("ticker", "fiscal_period"))
        return True

    if model_type == "reit":
        db.upsert(conn, "reit_fundamentals_q", {
            "ticker": ticker, "fiscal_period": fp,
            "report_date": rec.get("report_date"), "usable_at": rec.get("usable_at"),
            "ffo_per_share": rec.get("ffo_per_share"),
            "affo_per_share": rec.get("affo_per_share"),
            "occupancy": rec.get("occupancy"),
            "debt_maturity_yrs": rec.get("debt_maturity_yrs"),
            "int_cov": rec.get("int_cov_reit"),
            "dividend_safety": rec.get("dividend_safety"), "source": src,
        }, conflict_cols=("ticker", "fiscal_period"))
        return True

    # operating_company / insurance / biotech → cleaned_financials (line items)
    fcf = None
    if rec.get("cfo") is not None and rec.get("capex") is not None:
        fcf = rec["cfo"] - rec["capex"]
    db.upsert(conn, "cleaned_financials", {
        "ticker": ticker, "fiscal_period": fp, "source": src,
        "report_date": rec.get("report_date"), "usable_at": rec.get("usable_at"),
        "revenue": rec.get("revenue"), "revenue_prior": rec.get("revenue_prior"),
        "gross_profit": rec.get("gross_profit"),
        "operating_income": rec.get("operating_income"),
        "net_income": rec.get("net_income"),
        "cfo": rec.get("cfo"), "capex": rec.get("capex"), "fcf": fcf,
        "sbc": rec.get("sbc"), "total_debt": rec.get("total_debt"),
        "cash_and_sti": rec.get("cash_and_sti"), "total_equity": rec.get("total_equity"),
        "shares_out": rec.get("shares_out"), "interest_expense": rec.get("interest_expense"),
        "ebitda": rec.get("ebitda"), "quarterly_burn": rec.get("quarterly_burn"),
        "shares_out_prior": rec.get("shares_out_prior"),
        "provider_ratios_json": json.dumps(ratios, sort_keys=True) if ratios else None,
        "schema_version": FEATURE_VERSION,
    }, conflict_cols=("ticker", "fiscal_period", "source"))
    return True


# ---------------------------------------------------------------------------
# Ratio math (operating companies)
# ---------------------------------------------------------------------------
def compute_operating_ratios(row: dict) -> dict:
    """Derive operating ratios from cleaned line items. None-safe."""
    def div(a, b):
        return (a / b) if (a is not None and b not in (None, 0)) else None

    rev = row.get("revenue")
    out = {
        "gross_margin": div(row.get("gross_profit"), rev),
        "operating_margin": div(row.get("operating_income"), rev),
        "net_margin": div(row.get("net_income"), rev),
        "fcf_margin": div(row.get("fcf"), rev),
        "roe": div(row.get("net_income"), row.get("total_equity")),
        "revenue_growth_yoy": (rev / row["revenue_prior"] - 1.0)
            if (rev is not None and row.get("revenue_prior")) else None,
        "sbc_pct_revenue": div(row.get("sbc"), rev),
    }
    # ROIC = NOPAT / invested capital
    oi = row.get("operating_income")
    invested = None
    if row.get("total_debt") is not None and row.get("total_equity") is not None:
        invested = row["total_debt"] + row["total_equity"] - (row.get("cash_and_sti") or 0)
    out["roic"] = (oi * (1 - TAX_RATE) / invested) if (oi is not None and invested and invested > 0) else None
    # Net debt / EBITDA
    if row.get("ebitda") not in (None, 0) and row.get("total_debt") is not None:
        out["nd_ebitda"] = (row["total_debt"] - (row.get("cash_and_sti") or 0)) / row["ebitda"]
    else:
        out["nd_ebitda"] = None
    out["int_cov"] = div(oi, row.get("interest_expense"))
    provider = _load_provider_ratios(row)
    for k in ("gross_margin", "operating_margin", "net_margin", "roic", "roe",
              "revenue_growth_yoy"):
        if out.get(k) is None and provider.get(k) is not None:
            out[k] = provider[k]
    return out


# ---------------------------------------------------------------------------
# Layer 2 — normalize operating/insurance with sector peer z-scores
# ---------------------------------------------------------------------------
def normalize_operating(conn, as_of: str, sectors: dict, model_types: dict,
                        log=None) -> int:
    """Compute ratios + sector-relative z-scores → normalized_financials.

    sectors: {ticker: gics_sector}; model_types: {ticker: model_type}.
    Only operating_company / insurance are normalized here (biotech is computed
    on read; bank/reit live in their own tables)."""
    log = log or get_logger("financials")

    # Latest cleaned row per ticker, point-in-time (usable_at <= as_of)
    rows = conn.execute(
        "SELECT * FROM cleaned_financials WHERE usable_at <= ? "
        "ORDER BY ticker, fiscal_period DESC", (as_of,)).fetchall()
    latest: dict[str, dict] = {}
    for r in rows:
        if r["ticker"] not in latest:
            latest[r["ticker"]] = dict(r)

    # Compute ratios; bucket by sector for peer distributions
    ratios: dict[str, dict] = {}
    by_sector: dict[str, dict[str, list]] = {}
    for tk, row in latest.items():
        mt = model_types.get(tk, "operating_company")
        if mt not in ("operating_company", "insurance"):
            continue
        rr = compute_operating_ratios(row)
        ratios[tk] = rr
        sec = sectors.get(tk) or "Unknown"
        b = by_sector.setdefault(sec, {"operating_margin": [], "fcf_margin": [],
                                       "roic": [], "revenue_growth_yoy": []})
        for k in b:
            if rr.get(k) is not None:
                b[k].append(rr[k])

    written = 0
    for tk, rr in ratios.items():
        sec = sectors.get(tk) or "Unknown"
        pop = by_sector.get(sec, {})
        req = metric_specs(model_types.get(tk, "operating_company"))
        cov = coverage(req, rr)
        db.upsert(conn, "normalized_financials", {
            "ticker": tk, "fiscal_period": latest[tk]["fiscal_period"],
            "usable_at": latest[tk]["usable_at"], "sector": sec,
            "model_type": model_types.get(tk, "operating_company"),
            "gross_margin": rr["gross_margin"], "operating_margin": rr["operating_margin"],
            "fcf_margin": rr["fcf_margin"], "net_margin": rr["net_margin"],
            "roic": rr["roic"], "roe": rr["roe"], "nd_ebitda": rr["nd_ebitda"],
            "int_cov": rr["int_cov"], "revenue_growth_yoy": rr["revenue_growth_yoy"],
            "sbc_pct_revenue": rr["sbc_pct_revenue"],
            "op_margin_zscore": _z(rr["operating_margin"], pop.get("operating_margin")),
            "fcf_margin_zscore": _z(rr["fcf_margin"], pop.get("fcf_margin")),
            "roic_zscore": _z(rr["roic"], pop.get("roic")),
            "revenue_growth_zscore": _z(rr["revenue_growth_yoy"], pop.get("revenue_growth_yoy")),
            "coverage_ratio": cov, "feature_version": FEATURE_VERSION,
            "ratio_source": "provider_fallback" if _load_provider_ratios(latest[tk]) else "line_items",
        }, conflict_cols=("ticker", "fiscal_period"))
        written += 1
    log.info("normalized", as_of=as_of, tickers=written)
    return written


def _z(value, population):
    if value is None or not population:
        return None
    return round(zscore(value, population) or 0.0, 3)


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _load_provider_ratios(row: dict) -> dict:
    raw = row.get("provider_ratios_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Uniform read for the fundamental analyst
# ---------------------------------------------------------------------------
def get_fundamentals(conn, ticker: str, model_type: str, usable_before: str) -> Optional[dict]:
    """Return a uniform fundamental dict for the analyst, point-in-time safe."""
    if model_type == "bank":
        r = conn.execute(
            "SELECT * FROM bank_fundamentals_q WHERE ticker=? AND usable_at<=? "
            "ORDER BY fiscal_period DESC LIMIT 1", (ticker, usable_before)).fetchone()
        if not r:
            return None
        d = dict(r); d["model_type"] = "bank"
        d["coverage_ratio"] = coverage(metric_specs("bank"),
            {"roe": d["roe"], "roa": d["roa"], "efficiency_ratio": d["efficiency_ratio"],
             "capital_ratio": d["capital_ratio"], "credit_quality": d["credit_quality"]})
        return d

    if model_type == "reit":
        r = conn.execute(
            "SELECT * FROM reit_fundamentals_q WHERE ticker=? AND usable_at<=? "
            "ORDER BY fiscal_period DESC LIMIT 1", (ticker, usable_before)).fetchone()
        if not r:
            return None
        d = dict(r); d["model_type"] = "reit"
        d["coverage_ratio"] = coverage(metric_specs("reit"),
            {"ffo_per_share": d["ffo_per_share"], "occupancy": d["occupancy"],
             "debt_maturity_yrs": d["debt_maturity_yrs"], "int_cov": d["int_cov"],
             "dividend_safety": d["dividend_safety"]})
        return d

    if model_type == "biotech_pre_revenue":
        r = conn.execute(
            "SELECT * FROM cleaned_financials WHERE ticker=? AND usable_at<=? "
            "ORDER BY fiscal_period DESC LIMIT 1", (ticker, usable_before)).fetchone()
        if not r:
            return None
        cash, burn = r["cash_and_sti"], r["quarterly_burn"]
        shares, shares_prior = r["shares_out"], r["shares_out_prior"]
        runway = (cash / burn) if (cash is not None and burn not in (None, 0)) else None
        dilution = (shares / shares_prior - 1.0) if (shares is not None and shares_prior) else None
        d = {"model_type": "biotech_pre_revenue", "revenue": r["revenue"],
             "cash_runway_quarters": round(runway, 2) if runway is not None else None,
             "dilution_rate": round(dilution, 4) if dilution is not None else None}
        d["coverage_ratio"] = coverage(metric_specs("biotech_pre_revenue"),
            {"cash_runway_quarters": d["cash_runway_quarters"],
             "dilution_rate": d["dilution_rate"], "rd_intensity": None})
        return d

    # operating_company / insurance — normalized ratios + absolute values for valuation
    r = conn.execute(
        "SELECT * FROM normalized_financials WHERE ticker=? AND usable_at<=? "
        "ORDER BY fiscal_period DESC LIMIT 1", (ticker, usable_before)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["model_type"] = model_type
    # Attach absolute line items (for valuation: P/E, P/FCF, P/S, P/B).
    c = conn.execute(
        "SELECT revenue, net_income, fcf, shares_out, total_equity, provider_ratios_json "
        "FROM cleaned_financials "
        "WHERE ticker=? AND usable_at<=? ORDER BY fiscal_period DESC LIMIT 1",
        (ticker, usable_before)).fetchone()
    if c:
        d.update({"revenue": c["revenue"], "net_income": c["net_income"],
                  "fcf": c["fcf"], "shares_out": c["shares_out"],
                  "total_equity": c["total_equity"]})
        ratios = _load_provider_ratios(dict(c))
        for k in ("pe", "ps", "pfcf", "pb", "debt_to_equity", "current_ratio"):
            if ratios.get(k) is not None:
                d[k] = ratios[k]
    return d


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
_CACHE_TABLE = {
    "operating_company": "cleaned_financials", "insurance": "cleaned_financials",
    "biotech_pre_revenue": "cleaned_financials",
    "bank": "bank_fundamentals_q", "reit": "reit_fundamentals_q",
}
_CACHE_TS_COL = {
    "cleaned_financials": "cleaned_at",
    "bank_fundamentals_q": "created_at", "reit_fundamentals_q": "created_at",
}


def _has_fresh_fundamentals(conn, ticker: str, model_type: str,
                            cache_days: int, as_of: str) -> bool:
    """True if we stored this ticker's fundamentals within the last `cache_days`
    REAL days. Fundamentals only change quarterly, so re-fetching multi-MB SEC
    payloads every daily run is wasteful + risks rate-limiting (Plan §5).

    The usable_at guard is deliberately separate from cleaned_at: a recently
    fetched row must still have been public by the decision date, otherwise a
    backtest could reuse future filing data from the cache."""
    if cache_days <= 0:
        return False
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    table = _CACHE_TABLE.get(model_type)
    if not table:
        return False
    col = _CACHE_TS_COL[table]
    cutoff = (_dt.now(_tz.utc) - _td(days=cache_days)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE ticker=? AND usable_at<=? AND {col} >= ? LIMIT 1",
        (ticker, as_of, cutoff)).fetchone()
    return row is not None


def build_financials(tickers: list[str], *, fetcher, as_of: str,
                     model_types: dict, sectors: dict, cache_days: int = 1,
                     log=None) -> dict:
    """Fetch + Layer1 store + Layer2 normalize for all tickers.

    cache_days>0 skips the network fetch for tickers whose fundamentals were
    stored within that window and were already public by `as_of`. The default is
    a same-day/retry cache so live daily sync does not lag new SEC filings for a
    week. Synthetic runs and fresh DBs never hit the cache, so tests are
    unaffected."""
    log = log or get_logger("financials")
    stats = {"fetched": 0, "stored": 0, "skipped": 0, "cached": 0, "by_model": {}}

    with db.cloud() as conn:
        for tk in tickers:
            mt = model_types.get(tk, "operating_company")
            if mt in ("etf", "leveraged_etf"):
                stats["skipped"] += 1
                continue
            if not fetcher.synthetic and _has_fresh_fundamentals(conn, tk, mt, cache_days, as_of):
                stats["cached"] += 1
                continue
            rec = fetcher.fetch_fundamentals(tk, mt)
            if not rec:
                stats["skipped"] += 1
                continue
            stats["fetched"] += 1
            if clean_and_store(conn, tk, mt, rec, sector=sectors.get(tk)):
                stats["stored"] += 1
                stats["by_model"][mt] = stats["by_model"].get(mt, 0) + 1

    with db.cloud() as conn:
        normalize_operating(conn, as_of, sectors, model_types, log=log)

    log.info("financials_done", **{k: v for k, v in stats.items() if k != "by_model"})
    return stats


if __name__ == "__main__":
    import argparse, json
    from datetime import datetime, timezone
    from fetch import Fetcher
    ap = argparse.ArgumentParser(description="financials pipeline smoke test")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    print("Run via tests/test_scoring.py (needs temp DB).")
