"""
jobs/reconciliation.py — cross-source data verification (Plan §25).

Single-source data is single-point-of-failure data: if Finnhub (or Yahoo)
silently ships a bad print, a 1% price error can flip a signal. So we verify
the day's close against a second source and record every comparison.

Phase 3 scope: close-price reconciliation (Yahoo in prices_daily vs Finnhub
quote). Volume / market-cap reconciliation are stubbed for Phase 4.

Tolerance band (§25):
    diff <= 0.30%   → pass,   mark prices_daily.reconciled = 1
    0.30% < diff <= 2.0% → flag (log, keep primary value, reconciled stays 0)
    diff > 2.0%     → reject (clear obvious error: leave value but action=rejected)

A run-level failure_rate > 5% is a backstop the orchestrator escalates to an
alert (§32) — it means we've quietly become single-source.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from fetch import Fetcher  # noqa: E402

PASS_TOL = 0.30     # percent
FLAG_TOL = 2.0      # percent


def _log_recon(conn, *, date: str, ticker: str, field: str,
               value_a: Optional[float], value_b: Optional[float],
               diff_pct: Optional[float], source_a: str, source_b: str,
               action: str) -> None:
    conn.execute(
        "INSERT INTO reconciliation_log "
        "(date, ticker, field, value_a, value_b, diff_pct, source_a, source_b, action) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (date, ticker, field, value_a, value_b, diff_pct, source_a, source_b, action),
    )


def reconcile_close_price(conn, ticker: str, date: str, fetcher: Fetcher) -> dict:
    """Compare prices_daily close (source A) with a Finnhub quote (source B).

    Returns {reconciled: bool, action: str, diff_pct: float|None, reason: str}.
    Writes one reconciliation_log row. Updates prices_daily.reconciled on pass.
    """
    row = conn.execute(
        "SELECT close, source FROM prices_daily WHERE ticker=? AND date=?",
        (ticker, date),
    ).fetchone()
    if not row or row["close"] is None:
        return {"reconciled": False, "action": "no_primary", "diff_pct": None,
                "reason": "no primary close to verify"}
    value_a = float(row["close"])
    source_a = row["source"] or "unknown"

    quote = fetcher.fetch_finnhub_quote(ticker)
    if not quote:
        _log_recon(conn, date=date, ticker=ticker, field="close_price",
                   value_a=value_a, value_b=None, diff_pct=None,
                   source_a=source_a, source_b="finnhub", action="single_source")
        return {"reconciled": False, "action": "single_source", "diff_pct": None,
                "reason": "no second source available"}

    # Finnhub 'c' (current) is the most recent trade; on a completed session it
    # equals that day's close. Compare against it.
    value_b = float(quote.get("c") or 0)
    if value_b <= 0:
        return {"reconciled": False, "action": "single_source", "diff_pct": None,
                "reason": "second source returned non-positive price"}

    diff_pct = abs(value_a - value_b) / value_a * 100.0

    if diff_pct <= PASS_TOL:
        action = "used_a"
        conn.execute(
            "UPDATE prices_daily SET reconciled=1 WHERE ticker=? AND date=?",
            (ticker, date))
        reconciled = True
    elif diff_pct <= FLAG_TOL:
        action = "flagged"
        reconciled = False
    else:
        action = "rejected"
        reconciled = False

    _log_recon(conn, date=date, ticker=ticker, field="close_price",
               value_a=value_a, value_b=value_b, diff_pct=round(diff_pct, 4),
               source_a=source_a, source_b="finnhub", action=action)

    return {"reconciled": reconciled, "action": action,
            "diff_pct": round(diff_pct, 4), "reason": f"{action} ({diff_pct:.3f}%)"}


def reconcile_universe(tickers: list[str], *, date: str, fetcher: Fetcher,
                       log=None) -> dict:
    """Reconcile close prices for many tickers. Returns run-level stats.

    Skips entirely when no second source is configured (no Finnhub key and not
    synthetic) — there's nothing to compare against, which is itself recorded
    so the UI can show 'single-source mode'."""
    log = log or get_logger("reconciliation")
    stats = {
        "checked": 0, "passed": 0, "flagged": 0, "rejected": 0,
        "single_source": 0, "no_primary": 0,
        "failure_rate": 0.0, "flagged_tickers": [],
    }

    # Cheap capability probe: if not synthetic and no finnhub key, skip.
    if not fetcher.synthetic and not fetcher.finnhub_key:
        log.warning("reconciliation_skipped", reason="no_second_source")
        stats["skipped"] = True
        return stats

    with db.cloud() as conn:
        for ticker in tickers:
            res = reconcile_close_price(conn, ticker, date, fetcher)
            stats["checked"] += 1
            act = res["action"]
            if act == "used_a":
                stats["passed"] += 1
            elif act == "flagged":
                stats["flagged"] += 1
                stats["flagged_tickers"].append(ticker)
            elif act == "rejected":
                stats["rejected"] += 1
                stats["flagged_tickers"].append(ticker)
            elif act == "single_source":
                stats["single_source"] += 1
            elif act == "no_primary":
                stats["no_primary"] += 1

    comparable = stats["passed"] + stats["flagged"] + stats["rejected"]
    if comparable > 0:
        stats["failure_rate"] = round(
            (stats["flagged"] + stats["rejected"]) / comparable * 100.0, 2)
    log.info("reconciliation_done", **{k: v for k, v in stats.items()
                                       if k != "flagged_tickers"})
    return stats


if __name__ == "__main__":
    import argparse, json
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="reconciliation smoke test")
    ap.add_argument("--tickers", default="NVDA,AAPL,MSFT")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    f = Fetcher(synthetic=args.synthetic, store_raw=False, as_of=args.date)
    out = reconcile_universe([t.strip().upper() for t in args.tickers.split(",")],
                             date=args.date, fetcher=f)
    print(json.dumps(out, indent=2))
