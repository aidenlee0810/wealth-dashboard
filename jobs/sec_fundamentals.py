"""
jobs/sec_fundamentals.py — on-demand SEC fundamentals for ANY ticker (§6, free).

The daily snapshot only deep-scans the top ~60 names, so the static
`latest_fundamentals` view covers ~50 tickers. This module computes the SAME
SEC-derived fundamentals (ROIC / FCF / margins / P-multiples) for an ARBITRARY
ticker on demand — with **no API key** (SEC EDGAR companyfacts is public/free).

It REUSES, without modifying, jobs/fetch.py (`Fetcher.fetch_sec_companyfacts`
+ the CIK map) and jobs/financials.py (`compute_operating_ratios`). The return
dict is shaped like a `latest_fundamentals` item so the UI merges it identically
to the static-view rows (fundamentals.js `_mergeMetrics`).

Used by server.py `/api/fundamentals?ticker=…` (Local Mode).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# core line items used for a coverage estimate (mirrors §6 coverage_ratio idea)
_CORE_INPUTS = ("revenue", "net_income", "operating_income", "cfo", "capex",
                "total_equity", "total_debt", "shares_out")


def compute_for_ticker(ticker: str, as_of: str | None = None,
                       price: float | None = None) -> dict:
    """Compute SEC fundamentals for one ticker. Never raises — returns
    {"available": False, "reason": …} on any miss so the caller can 200 it."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ticker": ticker, "available": False, "reason": "no_ticker"}
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()

    try:
        from fetch import Fetcher
        import financials
    except Exception as e:                                          # noqa: BLE001
        return {"ticker": ticker, "available": False, "reason": f"import_error: {e}"}

    try:
        fetcher = Fetcher(synthetic=False, store_raw=False, as_of=as_of)
        # SEC first, Finnhub ratio fallback second. This keeps the official
        # line-item path as the preferred source but still lets ADRs/foreign
        # filers or sparse SEC mappings show useful PER/PBR/ROE-style metrics
        # when FINNHUB_KEY is configured server-side.
        rec = fetcher.fetch_fundamentals(ticker, "operating_company")
    except Exception as e:                                          # noqa: BLE001
        return {"ticker": ticker, "available": False, "reason": f"sec_fetch_error: {e}"}
    if not rec:
        return {"ticker": ticker, "available": False, "reason": "no_sec_data",
                "hint": "No SEC companyfacts or Finnhub ratio fallback for this ticker"}

    # Layer-1 minimal clean: fcf = cfo - capex (mirrors financials.clean_and_store)
    row = dict(rec)
    if row.get("fcf") is None and row.get("cfo") is not None and row.get("capex") is not None:
        row["fcf"] = row["cfo"] - row["capex"]
    ratios = financials.compute_operating_ratios(row)
    # Finnhub-only fallback records carry ratios but no SEC line items.
    provider_ratios = row.get("ratios") or {}
    for key, value in provider_ratios.items():
        if ratios.get(key) is None and value is not None:
            ratios[key] = value

    revenue = row.get("revenue")
    net_income = row.get("net_income")
    fcf = row.get("fcf")
    shares = row.get("shares_out")
    equity = row.get("total_equity")

    present = sum(1 for k in _CORE_INPUTS if row.get(k) is not None)
    coverage = round(present / len(_CORE_INPUTS) * 100, 1)

    item = {
        "ticker": ticker,
        "available": True,
        "on_demand": True,
        "source": "sec",
        "ratio_source": row.get("source"),
        "model_type": rec.get("model_type"),
        "fiscal_period": rec.get("fiscal_period"),
        "report_date": rec.get("report_date"),
        "usable_at": rec.get("usable_at"),
        "coverage_ratio": coverage,
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "shares_out": shares,
        "total_equity": equity,
        "gross_margin": ratios.get("gross_margin"),
        "operating_margin": ratios.get("operating_margin"),
        "fcf_margin": ratios.get("fcf_margin"),
        "net_margin": ratios.get("net_margin"),
        "roic": ratios.get("roic"),
        "roe": ratios.get("roe"),
        "nd_ebitda": ratios.get("nd_ebitda"),
        "int_cov": ratios.get("int_cov"),
        "revenue_growth_yoy": ratios.get("revenue_growth_yoy"),
        "sbc_pct_revenue": ratios.get("sbc_pct_revenue"),
    }
    optional_ratios = {
        "pe_ttm": ratios.get("pe"),
        "per_ttm": ratios.get("pe"),
        "ps_ttm": ratios.get("ps"),
        "pfcf_ttm": ratios.get("pfcf"),
        "pb_ttm": ratios.get("pb"),
        "pbr_ttm": ratios.get("pb"),
        "dividend_yield": ratios.get("div_yield"),
    }
    item.update({k: v for k, v in optional_ratios.items() if v is not None})
    if row.get("source") and row.get("source") != "sec":
        item["source"] = row.get("source")

    # If a price is supplied, compute multiples here; otherwise the browser
    # recomputes them against the live quote (price-independent ratios stay).
    if price and shares:
        mc = price * shares
        item["market_price"] = price
        item["market_cap_from_price"] = mc
        if net_income and net_income > 0:
            item["pe_ttm"] = round(mc / net_income, 2)
        if revenue and revenue > 0:
            item["ps_ttm"] = round(mc / revenue, 2)
        if fcf and fcf > 0:
            item["pfcf_ttm"] = round(mc / fcf, 2)
        if equity and equity > 0:
            item["pb_ttm"] = round(mc / equity, 2)
    return item


def _main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="on-demand SEC fundamentals for a ticker")
    ap.add_argument("ticker")
    ap.add_argument("--price", type=float, default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    print(json.dumps(compute_for_ticker(args.ticker, as_of=args.date, price=args.price),
                     indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _main()
