"""
jobs/price_quality.py — price-history realism classification (Real Price Provider Layer).

The cardinal rule of this phase: a valuation multiple or a technical signal is
only as real as the price *history* behind it — not just the latest bar. Bolting
one live quote onto a synthetic history makes a MIXED series that is more
dangerous than fully-synthetic data, because it *looks* real.

So every ticker's `prices_daily` history is classified by the source mix:

    none       no bars at all
    synthetic  every bar from a synthetic/mock source
    mixed      some real + some synthetic bars (DANGEROUS — do not trust)
    real       (almost) every bar from a real provider

Downstream (feature_builder) gates valuation on `real` only, and lowers
technical reliability on `mixed`. The thresholds are deliberately strict but not
absolute: the 98% floor tolerates tiny legacy noise while still flagging any
meaningful synthetic contamination as `mixed`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scorers.base import SYNTHETIC_PRICE_SOURCES  # noqa: E402

# Real history must be at least this fraction real to be trusted for valuation.
# 1.0 would reject a single legacy bar; 0.98 tolerates tiny edge noise while
# still flagging any meaningful synthetic contamination as 'mixed'.
REAL_FRACTION_FLOOR = 0.98


def _is_synthetic(source: Optional[str]) -> bool:
    return str(source or "").lower() in SYNTHETIC_PRICE_SOURCES


def classify_history(conn, ticker: str, as_of: Optional[str] = None) -> dict:
    """Classify a ticker's point-in-time price history by source realism.

    Returns {ticker, total_bars, real_bars, synthetic_bars, unknown_bars,
             fraction_real, classification, last_source}.
    classification ∈ none | synthetic | mixed | real.
    """
    where = "ticker=?"
    args: list = [ticker]
    if as_of:
        where += " AND date<=?"
        args.append(as_of)
    rows = conn.execute(
        f"SELECT source, COUNT(*) n FROM prices_daily WHERE {where} GROUP BY source",
        args).fetchall()
    last = conn.execute(
        f"SELECT source FROM prices_daily WHERE {where} ORDER BY date DESC LIMIT 1",
        args).fetchone()
    last_source = last["source"] if last else None

    total = sum(r["n"] for r in rows)
    if total == 0:
        return {"ticker": ticker, "total_bars": 0, "real_bars": 0,
                "synthetic_bars": 0, "unknown_bars": 0, "fraction_real": None,
                "classification": "none", "last_source": None}

    synthetic_bars = sum(r["n"] for r in rows if _is_synthetic(r["source"]))
    # 'unknown' (NULL/None source) counts as real-ish: legacy/manual bars are
    # real prices; only the explicit synthetic markers are distrusted.
    real_bars = total - synthetic_bars
    fraction_real = real_bars / total

    if synthetic_bars == total:
        classification = "synthetic"
    elif fraction_real >= REAL_FRACTION_FLOOR:
        classification = "real"
    else:
        classification = "mixed"

    return {
        "ticker": ticker, "total_bars": total, "real_bars": real_bars,
        "synthetic_bars": synthetic_bars, "unknown_bars": 0,
        "fraction_real": round(fraction_real, 4),
        "classification": classification, "last_source": last_source,
    }


def classification_for_sources(source_counts: dict) -> str:
    """Pure helper: classify from a {source: count} map (testable without a DB)."""
    total = sum(source_counts.values())
    if total == 0:
        return "none"
    synth = sum(n for s, n in source_counts.items() if _is_synthetic(s))
    if synth == total:
        return "synthetic"
    return "real" if (total - synth) / total >= REAL_FRACTION_FLOOR else "mixed"


def valuation_allowed(classification: str) -> bool:
    """Valuation multiples are trustworthy only on a fully-real history."""
    return classification == "real"


def technical_reliability(classification: str) -> str:
    """How much to trust technical signals given the price realism."""
    return {"real": "full", "mixed": "low", "synthetic": "none",
            "none": "none"}.get(classification, "none")


def summarize(conn, tickers: list[str], as_of: Optional[str] = None) -> dict:
    """Roll up classification counts across a ticker set (for snapshot health)."""
    counts = {"real": 0, "mixed": 0, "synthetic": 0, "none": 0}
    mixed_tickers: list[str] = []
    for t in tickers:
        c = classify_history(conn, t, as_of)["classification"]
        counts[c] = counts.get(c, 0) + 1
        if c == "mixed":
            mixed_tickers.append(t)
    return {"counts": counts, "valuation_enabled_tickers": counts["real"],
            "mixed_history_tickers": mixed_tickers[:50],
            "mixed_history_count": counts["mixed"]}


if __name__ == "__main__":
    import argparse
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import db  # noqa: E402
    ap = argparse.ArgumentParser(description="price-history realism")
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    with db.cloud(readonly=True) as c:
        print(json.dumps(classify_history(c, args.ticker, args.date), indent=2))
