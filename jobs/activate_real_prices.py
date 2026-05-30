"""
jobs/activate_real_prices.py — one-command real-price activation.

Given a real-price key in the environment (ALPACA_KEY+ALPACA_SECRET, or
TIINGO_KEY), this does the whole flip in one shot:

    refresh real price history (delete synthetic, insert real — no mixing)
      → deep re-score features (valuation turns on for fully-real tickers)
      → regenerate signals/candidates so the views reflect real val_scores
      → rebuild static views
      → print the data-realism summary (all_prices_synthetic / valuation count)

Without a key it is a SAFE NO-OP: refresh skips every ticker (no real source),
features/views still rebuild, and the report honestly shows all-synthetic. So
the only thing the user must add is the key — everything else is automated.

CLI:
    export ALPACA_KEY=…  ALPACA_SECRET=…
    python jobs/activate_real_prices.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from env_loader import load_private_env  # noqa: E402
import market_calendar as cal  # noqa: E402
import refresh_prices, feature_builder, views_builder  # noqa: E402
from fetch import Fetcher  # noqa: E402

load_private_env()

BENCHMARKS = ["SPY", "QQQ"]
SECTOR_ETFS = ["XLK", "XLV", "XLF", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLRE", "XLB"]


def _has_real_key() -> bool:
    return bool((os.environ.get("ALPACA_KEY") and os.environ.get("ALPACA_SECRET"))
                or os.environ.get("TIINGO_KEY"))


def activate(*, market_date: str | None = None, tickers: list[str] | None = None,
             range_: str = "2y", log=None) -> dict:
    log = log or get_logger("activate_real_prices")
    db.apply_migrations("cloud")

    with db.cloud(readonly=True) as c:
        md = market_date or c.execute(
            "SELECT MAX(date) d FROM features_daily").fetchone()["d"]
    md = md or cal.most_recent_trading_day(
        datetime.now(timezone.utc).date().isoformat()).isoformat()

    if tickers is None:
        with db.cloud(readonly=True) as c:
            tickers = [r["ticker"] for r in c.execute(
                "SELECT DISTINCT ticker FROM features_daily WHERE date=?", (md,)).fetchall()]
    for extra in BENCHMARKS + SECTOR_ETFS:
        if extra not in tickers:
            tickers.append(extra)

    has_key = _has_real_key()
    log.info("activate_start", market_date=md, tickers=len(tickers),
             real_key_present=has_key)

    fetcher = Fetcher(synthetic=False, store_raw=True, as_of=md, log=log)

    # 1. refresh real prices (replace synthetic — clean, never mixed)
    rstats = refresh_prices.refresh_prices(
        tickers, fetcher=fetcher, market_date=md, range_=range_,
        replace_synthetic=True, log=log)

    # 2. deep re-score features (valuation enabled only for fully-real history)
    fstats = feature_builder.build_features(tickers, feature_date=md, log=log)

    # 3. regenerate signals/candidates so candidate views reflect new val_scores
    try:
        import signal_generator
        scored = [t for t in tickers if t not in BENCHMARKS + SECTOR_ETFS]
        sstats = signal_generator.generate_signals(scored, date=md, log=log)
    except Exception as e:                                   # noqa: BLE001
        log.warning("signals_skipped", err=str(e))
        sstats = {"error": str(e)}

    # 4. rebuild static views
    views_builder.build_all_views(md, log=log)

    # 5. report the data-realism gate
    with db.cloud(readonly=True) as c:
        realism = views_builder._snapshot_health(
            c, md, {"snapshot_date": md, "executed_at_utc": md})["data_realism"]

    out = {
        "market_date": md,
        "real_key_present": has_key,
        "refresh": {"refreshed": rstats["refreshed"],
                    "skipped_no_real": rstats["skipped_no_real"],
                    "by_class": rstats["by_class"],
                    "provider_failures": rstats["provider_failures"]},
        "features": {k: fstats[k] for k in
                     ("written", "valuation_enabled", "valuation_na_synthetic",
                      "mixed_price_history", "by_price_class")},
        "signals": {k: sstats.get(k) for k in ("signals", "candidates")} if isinstance(sstats, dict) else sstats,
        "data_realism": {
            "all_prices_synthetic": realism["all_prices_synthetic"],
            "price_quality": realism["price_quality"],
            "valuation_populated_count": realism["valuation_populated_count"],
            "price_provider_failures": realism["price_provider_failures"],
        },
    }
    if not has_key:
        out["note"] = ("NO real-price key in env — refresh was a safe no-op. "
                       "export ALPACA_KEY+ALPACA_SECRET (or TIINGO_KEY) and re-run.")
    log.info("activate_done", refreshed=rstats["refreshed"],
             valuation_enabled=fstats["valuation_enabled"],
             all_synthetic=realism["all_prices_synthetic"])
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="one-command real-price activation")
    ap.add_argument("--date", default=None)
    ap.add_argument("--tickers", default=None, help="comma list (default: all scored)")
    ap.add_argument("--range", default="2y")
    args = ap.parse_args()
    tks = ([t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None)
    print(json.dumps(activate(market_date=args.date, tickers=tks, range_=args.range),
                     indent=2, ensure_ascii=False, default=str))
