"""
jobs/outcome_updater.py — multi-horizon signal outcomes (Plan §9, §11 step 11).

Each generic_signal is graded at 1/5/20/60/120 trading-day horizons. On run
date D we fill horizon h for the signals dated exactly h trading days earlier
(yesterday's 1D, week-ago 5D, …). Per (signal, horizon) we compute:

  abs_ret                    ticker adj-close return entry→exit
  spy_ret / qqq_ret / sector_ret   benchmark returns over the same window
  rel_spy / rel_qqq / rel_sector   excess vs each benchmark
  mae / mfe                  max adverse / favorable excursion (intra-window)
  mdd                        max peak-to-trough drawdown of close in window
  outcome_label              HIT / MISS / FLAT / SKIP_DATA

No-lookahead is structural: exit date == D, entry date == signal date, and
every bar used lies in [entry, D]. assert_no_lookahead() makes that explicit
for tests. Blocked signals are graded too (so Phase 5 can score the Risk
Governor's avoided-drawdown vs missed-upside).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from market_calendar import previous_trading_day  # noqa: E402

HORIZONS = (1, 5, 20, 60, 120)
HIT_THRESHOLD = 0.02   # ±2% defines HIT/MISS vs FLAT

# GICS sector → SPDR ETF (for sector-relative returns).
SECTOR_ETF = {
    "Information Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Communication Services": "XLC",
    "Industrials": "XLI", "Consumer Staples": "XLP", "Energy": "XLE",
    "Utilities": "XLU", "Real Estate": "XLRE", "Materials": "XLB",
}


def _adj_close(conn, ticker: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT COALESCE(adj_close, close) c FROM prices_daily WHERE ticker=? AND date=?",
        (ticker, date)).fetchone()
    return row["c"] if row and row["c"] is not None else None


def _window_bars(conn, ticker: str, start: str, end: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT date, high, low, COALESCE(adj_close, close) c FROM prices_daily "
        "WHERE ticker=? AND date>? AND date<=? ORDER BY date ASC",
        (ticker, start, end)).fetchall()]


def _ret(conn, ticker: str, entry_date: str, exit_date: str) -> Optional[float]:
    e = _adj_close(conn, ticker, entry_date)
    x = _adj_close(conn, ticker, exit_date)
    if e in (None, 0) or x is None:
        return None
    return x / e - 1.0


def _excursions(conn, ticker: str, entry_date: str, exit_date: str,
                entry_price: float) -> dict:
    """mae/mfe (vs entry) + mdd (peak-to-trough of close) over (entry, exit]."""
    bars = _window_bars(conn, ticker, entry_date, exit_date)
    if not bars or entry_price in (None, 0):
        return {"mae": None, "mfe": None, "mdd": None}
    mfe = mae = 0.0
    peak = entry_price
    mdd = 0.0
    for b in bars:
        if b["high"] is not None:
            mfe = max(mfe, b["high"] / entry_price - 1.0)
        if b["low"] is not None:
            mae = min(mae, b["low"] / entry_price - 1.0)
        c = b["c"]
        if c is not None:
            peak = max(peak, c)
            mdd = min(mdd, c / peak - 1.0)
    return {"mae": round(mae, 4), "mfe": round(mfe, 4), "mdd": round(mdd, 4)}


def _label(abs_ret: Optional[float]) -> str:
    if abs_ret is None:
        return "SKIP_DATA"
    if abs_ret >= HIT_THRESHOLD:
        return "HIT"
    if abs_ret <= -HIT_THRESHOLD:
        return "MISS"
    return "FLAT"


def update_outcomes(market_date: str, *, horizons=HORIZONS, log=None) -> dict:
    """Fill outcomes for signals reaching each horizon boundary on market_date."""
    log = log or get_logger("outcome_updater")
    stats = {"market_date": market_date, "updated": 0, "by_horizon": {},
             "skipped_no_data": 0, "by_label": {}}

    with db.cloud() as conn:
        for h in horizons:
            signal_date = previous_trading_day(market_date, h).isoformat()
            sigs = conn.execute(
                "SELECT signal_id, ticker, benchmark_at_signal_json "
                "FROM generic_signals WHERE date=?", (signal_date,)).fetchall()
            if not sigs:
                continue
            spy_exit = _adj_close(conn, "SPY", market_date)
            qqq_exit = _adj_close(conn, "QQQ", market_date)
            count_h = 0
            for s in sigs:
                ticker = s["ticker"]
                entry = _adj_close(conn, ticker, signal_date)
                abs_ret = _ret(conn, ticker, signal_date, market_date)

                # benchmark returns over the same window
                import json as _json
                try:
                    bench = _json.loads(s["benchmark_at_signal_json"] or "{}")
                except (ValueError, TypeError):
                    bench = {}
                spy_entry = bench.get("spy") or _adj_close(conn, "SPY", signal_date)
                qqq_entry = bench.get("qqq") or _adj_close(conn, "QQQ", signal_date)
                spy_ret = (spy_exit / spy_entry - 1.0) if (spy_entry and spy_exit) else None
                qqq_ret = (qqq_exit / qqq_entry - 1.0) if (qqq_entry and qqq_exit) else None

                # sector-relative
                sec_row = conn.execute(
                    "SELECT sector FROM ticker_master WHERE ticker=?", (ticker,)).fetchone()
                etf = SECTOR_ETF.get(sec_row["sector"]) if sec_row else None
                sector_ret = _ret(conn, etf, signal_date, market_date) if etf else None

                exc = _excursions(conn, ticker, signal_date, market_date, entry) \
                    if entry else {"mae": None, "mfe": None, "mdd": None}
                label = _label(abs_ret)
                if label == "SKIP_DATA":
                    stats["skipped_no_data"] += 1

                db.upsert(conn, "signal_outcomes", {
                    "signal_id": s["signal_id"], "horizon": h,
                    "exit_date": market_date,
                    "abs_ret": round(abs_ret, 4) if abs_ret is not None else None,
                    "spy_ret": round(spy_ret, 4) if spy_ret is not None else None,
                    "qqq_ret": round(qqq_ret, 4) if qqq_ret is not None else None,
                    "sector_ret": round(sector_ret, 4) if sector_ret is not None else None,
                    "rel_spy": round(abs_ret - spy_ret, 4) if (abs_ret is not None and spy_ret is not None) else None,
                    "rel_qqq": round(abs_ret - qqq_ret, 4) if (abs_ret is not None and qqq_ret is not None) else None,
                    "rel_sector": round(abs_ret - sector_ret, 4) if (abs_ret is not None and sector_ret is not None) else None,
                    "mae": exc["mae"], "mfe": exc["mfe"], "mdd": exc["mdd"],
                    "outcome_label": label,
                    "outcome_reason": f"{h}D entry {signal_date}→exit {market_date}",
                }, conflict_cols=("signal_id", "horizon"))
                count_h += 1
                stats["updated"] += 1
                stats["by_label"][label] = stats["by_label"].get(label, 0) + 1
            if count_h:
                stats["by_horizon"][h] = count_h

    log.info("outcomes_done", **{k: v for k, v in stats.items() if k != "by_label"})
    return stats


def assert_no_lookahead(market_date: str) -> bool:
    """Sanity: no signal_outcome exit_date may be in the future relative to its
    horizon. Returns True if clean; raises AssertionError otherwise."""
    with db.cloud(readonly=True) as conn:
        bad = conn.execute(
            "SELECT COUNT(*) n FROM signal_outcomes WHERE exit_date > ?",
            (market_date,)).fetchone()["n"]
    assert bad == 0, f"lookahead: {bad} outcomes with exit_date > {market_date}"
    return True


if __name__ == "__main__":
    import argparse, json
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="outcome_updater smoke test")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    out = update_outcomes(args.date)
    print(json.dumps(out, indent=2))
