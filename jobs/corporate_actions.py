"""
jobs/corporate_actions.py — splits + dividends from Alpaca (Plan §33).

`jobs/prices.py` already records *splits* off Yahoo's events feed, but the
corporate-actions ledger was missing **dividends** entirely — so dividend yield,
total-return awareness and REIT dividend-safety had no source. Alpaca's
`/v1/corporate-actions` endpoint gives both, reliably, from datacenter IPs.

This module:
  * `parse_corporate_actions(payload)`  — PURE: Alpaca payload → normalized rows
    (forward/reverse splits → split_ratio; cash dividends → per-share dividend).
  * `fetch_corporate_actions(...)`      — network (paginated, chunked symbols).
  * `sync(conn, rows)`                  — idempotent upsert into corporate_actions.
  * `sync_universe(conn, symbols, …)`   — fetch+sync for a ticker set (graceful
                                          skip when no ALPACA key).
  * `trailing_dividend(conn, ticker, asof)` — TTM cash dividend per share.

stdlib only (urllib), consistent with the rest of jobs/. Never raises on a bad
single event — unparseable rows are dropped.

corporate_actions schema (migration 001):
    PRIMARY KEY (ticker, ex_date, action_type)
    action_type ∈ {split, dividend, merger, spinoff, rights}
    split_ratio REAL   -- e.g. 10.0 for a 10:1 forward split, 0.1 for 1:10 reverse
    dividend    REAL   -- per share USD
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:
    from env_loader import load_private_env
except Exception:  # pragma: no cover - direct import path may vary
    try:
        from .env_loader import load_private_env
    except Exception:
        load_private_env = None

if load_private_env:
    load_private_env()

DATA_BASE = "https://data.alpaca.markets"
HTTP_TIMEOUT = 20
SYMBOL_CHUNK = 50            # corporate-actions endpoint: keep URLs short
MAX_PAGES = 30              # pagination safety cap
CA_TYPES = "forward_split,reverse_split,cash_dividend"


# ─────────────────────────────────────────────────────────────────────────────
#  Pure parsing (no network)
# ─────────────────────────────────────────────────────────────────────────────
def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _fmt_rate(x: float) -> str:
    """Render a split leg as a compact integer when possible (10.0 -> '10')."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _parse_split(ev: dict) -> Optional[dict]:
    sym = (ev.get("symbol") or "").upper()
    ex = ev.get("ex_date") or ev.get("process_date") or ev.get("effective_date")
    new_rate, old_rate = _num(ev.get("new_rate")), _num(ev.get("old_rate"))
    if not (sym and ex and new_rate and old_rate) or old_rate == 0:
        return None
    return {
        "ticker": sym, "ex_date": str(ex)[:10], "action_type": "split",
        "split_ratio": new_rate / old_rate, "dividend": None,
        "notes": f"{_fmt_rate(old_rate)}:{_fmt_rate(new_rate)}", "source": "alpaca",
    }


def _parse_dividend(ev: dict) -> Optional[dict]:
    sym = (ev.get("symbol") or "").upper()
    ex = ev.get("ex_date") or ev.get("process_date")
    rate = _num(ev.get("rate"))
    if not (sym and ex) or rate is None:
        return None
    return {
        "ticker": sym, "ex_date": str(ex)[:10], "action_type": "dividend",
        "split_ratio": None, "dividend": rate,
        "notes": "special" if ev.get("special") else None, "source": "alpaca",
    }


def parse_corporate_actions(payload: dict) -> list[dict]:
    """Alpaca /v1/corporate-actions payload → list of normalized rows.

    Accepts either {"corporate_actions": {...}} or a bare {...}. Splits come from
    forward_splits + reverse_splits (same new_rate/old_rate math handles both);
    dividends from cash_dividends. Order preserved; bad rows dropped."""
    if not isinstance(payload, dict):
        return []
    ca = payload.get("corporate_actions")
    if not isinstance(ca, dict):
        ca = payload
    out: list[dict] = []
    for bucket in ("forward_splits", "reverse_splits"):
        for ev in (ca.get(bucket) or []):
            row = _parse_split(ev) if isinstance(ev, dict) else None
            if row:
                out.append(row)
    for ev in (ca.get("cash_dividends") or []):
        row = _parse_dividend(ev) if isinstance(ev, dict) else None
        if row:
            out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Credentials + network
# ─────────────────────────────────────────────────────────────────────────────
def _env_creds() -> tuple:
    key = os.environ.get("ALPACA_KEY") or os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("ALPACA_SECRET") or os.environ.get("APCA_API_SECRET_KEY")
    return (key, secret) if (key and secret) else (None, None)


def _http_json(url: str, key: str, secret: str) -> dict:
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json", "User-Agent": "WealthDashboard/1.0",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_corporate_actions(symbols: list[str], key: str, secret: str,
                            start: str, end: str) -> list[dict]:
    """Fetch splits + dividends for `symbols` between start/end (YYYY-MM-DD).

    Chunks symbols and follows next_page_token. Returns normalized rows; a failed
    chunk is skipped (best-effort), never aborting the whole pull."""
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    seen: set[str] = set()
    uniq = [s for s in syms if not (s in seen or seen.add(s))]
    if not uniq or not (key and secret):
        return []

    rows: list[dict] = []
    for chunk in _chunks(uniq, SYMBOL_CHUNK):
        page_token = None
        for _ in range(MAX_PAGES):
            params = {"symbols": ",".join(chunk), "types": CA_TYPES,
                      "start": start, "end": end, "limit": 1000}
            if page_token:
                params["page_token"] = page_token
            url = f"{DATA_BASE}/v1/corporate-actions?{urllib.parse.urlencode(params)}"
            try:
                data = _http_json(url, key, secret)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
                break          # skip this chunk on transport error; keep going
            rows.extend(parse_corporate_actions(data))
            page_token = data.get("next_page_token")
            if not page_token:
                break
    return rows


# ─────────────────────────────────────────────────────────────────────────────
#  Persistence
# ─────────────────────────────────────────────────────────────────────────────
def sync(conn, rows: list[dict]) -> dict:
    """Idempotent upsert into corporate_actions. INSERT OR IGNORE on the
    (ticker, ex_date, action_type) PK, so re-runs add only genuinely new rows."""
    stats = {"seen": len(rows), "new": 0, "splits_new": 0, "dividends_new": 0}
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO corporate_actions "
            "(ticker, ex_date, action_type, split_ratio, dividend, notes, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["ticker"], r["ex_date"], r["action_type"],
             r.get("split_ratio"), r.get("dividend"), r.get("notes"), r.get("source")))
        if cur.rowcount:
            stats["new"] += 1
            if r["action_type"] == "split":
                stats["splits_new"] += 1
            elif r["action_type"] == "dividend":
                stats["dividends_new"] += 1
    return stats


def sync_universe(conn, symbols: list[str], market_date: str,
                  lookback_days: int = 400, log=None) -> dict:
    """Fetch + sync corporate actions for a ticker set. Gracefully skips when no
    ALPACA key is configured (returns {'skipped': 'no_alpaca_key'})."""
    key, secret = _env_creds()
    if not (key and secret):
        if log:
            log.info("corporate_actions_skipped", reason="no_alpaca_key")
        return {"skipped": "no_alpaca_key", "seen": 0, "new": 0}
    try:
        start = (date.fromisoformat(market_date[:10]) -
                 timedelta(days=lookback_days)).isoformat()
    except ValueError:
        start = "2000-01-01"
    rows = fetch_corporate_actions(symbols, key, secret, start, market_date[:10])
    stats = sync(conn, rows)
    stats["fetched"] = len(rows)
    if log:
        log.info("corporate_actions_synced", fetched=len(rows),
                 new=stats["new"], splits=stats["splits_new"],
                 dividends=stats["dividends_new"])
    return stats


def trailing_dividend(conn, ticker: str, asof: str, months: int = 12) -> float:
    """Sum of cash dividends per share over the trailing `months` ending at asof.
    Feeds dividend-yield = trailing_dividend / price."""
    try:
        start = (date.fromisoformat(asof[:10]) -
                 timedelta(days=int(months * 30.44))).isoformat()
    except ValueError:
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(dividend), 0) AS s FROM corporate_actions "
        "WHERE ticker=? AND action_type='dividend' AND ex_date > ? AND ex_date <= ?",
        (ticker.upper(), start, asof[:10])).fetchone()
    return float(row["s"] if hasattr(row, "keys") else row[0]) if row else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────
def _main():
    import argparse
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import db  # noqa: E402

    ap = argparse.ArgumentParser(description="sync splits + dividends from Alpaca")
    ap.add_argument("--tickers", default="AAPL,MSFT,NVDA,JNJ,KO")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--lookback-days", type=int, default=400)
    args = ap.parse_args()

    syms = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    with db.cloud() as conn:
        out = sync_universe(conn, syms, args.date, lookback_days=args.lookback_days)
        if not out.get("skipped"):
            for t in syms:
                out.setdefault("ttm_dividend", {})[t] = trailing_dividend(conn, t, args.date)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _main()
