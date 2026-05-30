"""
jobs/prices.py — populate prices_daily + corporate_actions (Plan §11, §33).

Responsibilities
----------------
* For each universe ticker, fetch daily OHLCV history (via Fetcher) and upsert
  into prices_daily. Idempotent: re-running the same date overwrites cleanly.
* Adaptive range: tickers with deep, fresh history get an incremental pull
  ('1mo'); new or stale tickers get a full backfill ('1y') so SMA200 /
  52-week-high features have enough lookback.
* Detect split events (Yahoo events.splits) → corporate_actions, and ensure
  adj_close is stored so downstream returns are split-consistent.
* Never write future bars (> market_date). Returns per-run stats + the list of
  failed tickers so the orchestrator can record failed_jobs and degrade.

This module only ever touches the CLOUD db (never personal).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for `import db`
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from fetch import Fetcher  # noqa: E402
from market_calendar import previous_trading_day  # noqa: E402

# A ticker needs this much history before we switch to incremental pulls.
_MIN_DEEP_BARS = 200
# If the latest stored bar is older than this many calendar days, backfill.
_STALE_DAYS = 7


def _coverage(conn, ticker: str) -> tuple[int, Optional[str]]:
    """Return (bar_count, latest_date) for a ticker in prices_daily."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(date) AS mx FROM prices_daily WHERE ticker=?",
        (ticker,),
    ).fetchone()
    return (row["n"] or 0), row["mx"]


def _choose_range(bar_count: int, latest: Optional[str], market_date: str) -> str:
    """Adaptive fetch range based on existing coverage."""
    if bar_count < _MIN_DEEP_BARS or latest is None:
        return "1y"
    # measure staleness in calendar days
    from datetime import date
    try:
        gap = (date.fromisoformat(market_date) - date.fromisoformat(latest)).days
    except ValueError:
        return "1y"
    if gap > _STALE_DAYS:
        return "1y"
    return "1mo"


def _record_split(conn, ticker: str, ex_date: str, ratio: float, source: str) -> bool:
    """Insert a split into corporate_actions if not already present. Returns True if new."""
    existing = conn.execute(
        "SELECT 1 FROM corporate_actions WHERE ticker=? AND ex_date=? AND action_type='split'",
        (ticker, ex_date),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT INTO corporate_actions (ticker, ex_date, action_type, split_ratio, source) "
        "VALUES (?,?,?,?,?)",
        (ticker, ex_date, "split", ratio, source),
    )
    return True


def update_prices(
    tickers: list[str],
    *,
    fetcher: Fetcher,
    market_date: str,
    log=None,
    progress_every: int = 50,
) -> dict:
    """Fetch + upsert prices for all tickers. Returns a stats dict.

    stats = {
      "tickers_total", "tickers_ok", "tickers_failed", "failed_tickers",
      "bars_written", "splits_detected", "source_counts"
    }"""
    log = log or get_logger("prices")
    stats = {
        "tickers_total": len(tickers),
        "tickers_ok": 0,
        "tickers_failed": 0,
        "failed_tickers": [],
        "bars_written": 0,
        "splits_detected": 0,
        "source_counts": {},
        "provider_failures": {},   # {provider: {count, reasons:{reason:count}}}
    }

    for i, ticker in enumerate(tickers):
        # Decide range from current coverage (read-only peek).
        with db.cloud(readonly=True) as rconn:
            bar_count, latest = _coverage(rconn, ticker)
        rng = _choose_range(bar_count, latest, market_date)

        hist = fetcher.fetch_price_history(ticker, range_=rng)
        bars = hist["bars"]
        source = hist["source"]
        # capture WHY each provider failed (per-provider transparency, RPL §)
        for a in hist.get("attempts", []):
            if not a.get("ok"):
                pf = stats["provider_failures"].setdefault(
                    a["provider"], {"count": 0, "reasons": {}})
                pf["count"] += 1
                reason = (a.get("reason") or "unknown")[:120]
                pf["reasons"][reason] = pf["reasons"].get(reason, 0) + 1
        if not bars:
            stats["tickers_failed"] += 1
            stats["failed_tickers"].append(ticker)
            continue

        # Filter out any bar at/after a future date (no-lookahead on prices).
        bars = [b for b in bars if b.date <= market_date]
        if not bars:
            stats["tickers_failed"] += 1
            stats["failed_tickers"].append(ticker)
            continue

        try:
            with db.cloud() as conn:
                for b in bars:
                    db.upsert(conn, "prices_daily", b.as_row(ticker, source),
                              conflict_cols=("date", "ticker"))
                    stats["bars_written"] += 1
                for sp in hist["splits"]:
                    if sp["ex_date"] <= market_date and _record_split(
                            conn, ticker, sp["ex_date"], sp["split_ratio"], source or "yahoo"):
                        stats["splits_detected"] += 1
            stats["tickers_ok"] += 1
            stats["source_counts"][source] = stats["source_counts"].get(source, 0) + 1
        except Exception as e:
            log.error("price_upsert_failed", ticker=ticker, err=str(e))
            stats["tickers_failed"] += 1
            stats["failed_tickers"].append(ticker)

        if progress_every and (i + 1) % progress_every == 0:
            log.info("prices_progress", done=i + 1, total=len(tickers),
                     ok=stats["tickers_ok"], failed=stats["tickers_failed"])

    log.info("prices_done", **{k: v for k, v in stats.items()
                               if k not in ("failed_tickers",)})
    return stats


def latest_close(conn, ticker: str, on_or_before: str) -> Optional[float]:
    """Most recent adj_close at/<= a date. Used by features/reconciliation."""
    row = conn.execute(
        "SELECT adj_close, close FROM prices_daily "
        "WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
        (ticker, on_or_before),
    ).fetchone()
    if not row:
        return None
    return row["adj_close"] if row["adj_close"] is not None else row["close"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="prices.py smoke test")
    ap.add_argument("--tickers", default="NVDA,AAPL,MSFT")
    ap.add_argument("--date", default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    from datetime import datetime, timezone
    md = args.date or datetime.now(timezone.utc).date().isoformat()
    f = Fetcher(synthetic=args.synthetic, store_raw=False, as_of=md)
    out = update_prices([t.strip().upper() for t in args.tickers.split(",")],
                        fetcher=f, market_date=md)
    import json
    print(json.dumps(out, indent=2))
