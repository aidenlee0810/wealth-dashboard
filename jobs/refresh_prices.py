"""
jobs/refresh_prices.py — replace synthetic price history with REAL provider bars.

The activation step of the Real Price Provider Layer. For each ticker it fetches
real daily history via the multi-provider dispatcher (Alpaca → Tiingo → Yahoo)
and, only when a *real* provider returns enough bars, **replaces** the synthetic
history so the ticker becomes fully `real` and valuation can turn on.

Safety (the cardinal rule of this phase):
  * A ticker is only refreshed when the source is a real provider AND it returns
    >= MIN_REAL_BARS, with no future bars. A single live quote is never enough.
  * In the default `replace_synthetic=True` mode the synthetic bars are DELETED
    before the real bars are inserted, so the result is pure-real (no silent mix).
  * In `replace_synthetic=False` (append) mode any synthetic dates outside the
    real window remain → the ticker classifies as `mixed` and valuation STAYS
    OFF. We never blend synthetic + real to fake a real valuation.
  * Tickers we can't get real data for are left untouched (still synthetic).

CLI (requires ALPACA_KEY/SECRET or TIINGO_KEY in env):
    python jobs/refresh_prices.py --tickers NVDA,AAPL,MSFT --rerun-features
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from env_loader import load_private_env  # noqa: E402
import price_quality  # noqa: E402
from scorers.base import SYNTHETIC_PRICE_SOURCES  # noqa: E402

load_private_env()

MIN_REAL_BARS = 200            # below this we distrust the fetch; leave ticker as-is
_SYNTH_LIST = tuple(sorted(SYNTHETIC_PRICE_SOURCES))


def _is_real_source(source: Optional[str]) -> bool:
    return bool(source) and str(source).lower() not in SYNTHETIC_PRICE_SOURCES


def _delete_synthetic(conn, ticker: str) -> int:
    """Delete only the synthetic bars for a ticker (real/unknown bars are kept)."""
    placeholders = ",".join("?" for _ in _SYNTH_LIST)
    cur = conn.execute(
        f"DELETE FROM prices_daily WHERE ticker=? AND "
        f"lower(COALESCE(source,'')) IN ({placeholders})",
        (ticker, *_SYNTH_LIST))
    return cur.rowcount


def refresh_prices(tickers: list[str], *, fetcher, market_date: str,
                   range_: str = "2y", replace_synthetic: bool = True,
                   min_bars: int = MIN_REAL_BARS, log=None) -> dict:
    """Fetch real history and (when trustworthy) replace synthetic bars.

    Returns a stats dict: refreshed/skipped counts, per-ticker outcomes, final
    classification counts, and per-provider failure reasons."""
    log = log or get_logger("refresh_prices")
    stats = {
        "tickers": len(tickers), "refreshed": 0, "skipped_no_real": 0,
        "by_class": {}, "provider_failures": {}, "per_ticker": {},
    }

    for ticker in tickers:
        hist = fetcher.fetch_price_history(ticker, range_=range_)
        # capture per-provider failure reasons (transparency)
        for a in hist.get("attempts", []):
            if not a.get("ok"):
                pf = stats["provider_failures"].setdefault(
                    a["provider"], {"count": 0, "reasons": {}})
                pf["count"] += 1
                reason = (a.get("reason") or "unknown")[:120]
                pf["reasons"][reason] = pf["reasons"].get(reason, 0) + 1

        source = hist.get("source")
        bars = [b for b in (hist.get("bars") or []) if b.date <= market_date]  # no future bars

        # Gate: must be a REAL provider with enough history. One quote is not enough.
        if not _is_real_source(source) or len(bars) < min_bars:
            stats["skipped_no_real"] += 1
            stats["per_ticker"][ticker] = {
                "action": "skipped", "source": source, "bars": len(bars),
                "reason": ("not_real_source" if not _is_real_source(source)
                           else f"too_few_bars({len(bars)}<{min_bars})")}
            continue

        with db.cloud() as conn:
            deleted = _delete_synthetic(conn, ticker) if replace_synthetic else 0
            for b in bars:
                db.upsert(conn, "prices_daily", b.as_row(ticker, source),
                          conflict_cols=("date", "ticker"))
            cls = price_quality.classify_history(conn, ticker, market_date)["classification"]

        stats["refreshed"] += 1
        stats["by_class"][cls] = stats["by_class"].get(cls, 0) + 1
        stats["per_ticker"][ticker] = {
            "action": "refreshed", "source": source, "bars": len(bars),
            "deleted_synthetic": deleted, "classification": cls}
        log.info("refreshed", ticker=ticker, source=source, bars=len(bars),
                 deleted_synthetic=deleted, classification=cls)

    log.info("refresh_done", refreshed=stats["refreshed"],
             skipped=stats["skipped_no_real"], by_class=stats["by_class"])
    return stats


def _main():
    import argparse
    import json
    from datetime import datetime, timezone
    from fetch import Fetcher
    import market_calendar as cal

    ap = argparse.ArgumentParser(description="activate real prices for a ticker set")
    ap.add_argument("--tickers", default="NVDA,AAPL,MSFT")
    ap.add_argument("--date", default=None, help="market date (default: most recent trading day)")
    ap.add_argument("--range", default="2y")
    ap.add_argument("--append", action="store_true",
                    help="do NOT delete synthetic first (may leave mixed history)")
    ap.add_argument("--rerun-features", action="store_true",
                    help="deep re-score the refreshed tickers afterwards")
    args = ap.parse_args()

    md = args.date or cal.most_recent_trading_day(
        datetime.now(timezone.utc).date().isoformat()).isoformat()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    fetcher = Fetcher(synthetic=False, store_raw=True, as_of=md)

    out = refresh_prices(tickers, fetcher=fetcher, market_date=md, range_=args.range,
                         replace_synthetic=not args.append)
    if args.rerun_features and out["refreshed"]:
        import feature_builder
        fstats = feature_builder.build_features(tickers, feature_date=md)
        out["feature_rerun"] = {k: fstats[k] for k in
                                ("written", "valuation_enabled", "valuation_na_synthetic",
                                 "mixed_price_history", "by_price_class")}
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _main()
