"""
jobs/live_quotes.py — Alpaca real-time snapshot layer (stdlib only).

The daily snapshot pipeline is end-of-day. This module adds an INTRADAY layer:
one batched call to Alpaca's multi-symbol snapshot endpoint returns the latest
trade, today's forming daily bar, and yesterday's close for a whole watchlist —
so the dashboard can show live price + today's % change for every holding in a
single request, with the API keys staying server-side (the browser never sees
them; it talks to server.py's /api/price/snapshot proxy).

Design rules:
  * stdlib only (urllib) — consistent with the rest of jobs/, no pip deps.
  * `parse_snapshot()` is a PURE function (payload dict -> normalized dict) so it
    is fully unit-testable without a network or keys.
  * Free Alpaca accounts get the IEX feed; we default to feed=iex. SIP is opt-in.
  * Never raises on a single bad symbol — unknown/missing symbols are simply
    absent from the result map.

Endpoints (data + trading API):
  snapshots : GET https://data.alpaca.markets/v2/stocks/snapshots?symbols=...&feed=iex
  clock     : GET https://api.alpaca.markets/v2/clock
Auth headers: APCA-API-KEY-ID / APCA-API-SECRET-KEY (same as jobs/fetch.py).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DATA_BASE = "https://data.alpaca.markets"
TRADING_BASE = "https://api.alpaca.markets"
DEFAULT_FEED = "iex"            # free-tier feed; "sip" requires a paid subscription
MAX_SYMBOLS = 200              # cap per request (proxy enforces too)
HTTP_TIMEOUT = 12


# ─────────────────────────────────────────────────────────────────────────────
#  Pure parsing (no network) — unit-testable
# ─────────────────────────────────────────────────────────────────────────────
def _num(v):
    """Coerce to float, returning None for missing/non-numeric/non-finite."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf guard
        return None
    return f


def parse_one(symbol: str, snap: dict) -> Optional[dict]:
    """Normalize one Alpaca snapshot object into a flat quote dict.

    Alpaca snapshot shape (per symbol):
        latestTrade  : {"p": price, "t": iso8601, ...}
        latestQuote  : {"bp": bid, "ap": ask, ...}
        minuteBar    : {"o","h","l","c","v","t"}
        dailyBar     : {"o","h","l","c","v","t"}   (today, forming while open)
        prevDailyBar : {"o","h","l","c","v","t"}   (previous session)

    Returns None when there is no usable price at all.
    Price preference: latestTrade.p > minuteBar.c > dailyBar.c.
    Day change is measured against prevDailyBar.c (matches broker "% today").
    """
    if not isinstance(snap, dict):
        return None

    trade = snap.get("latestTrade") or snap.get("t") or {}
    minute = snap.get("minuteBar") or snap.get("m") or {}
    day = snap.get("dailyBar") or snap.get("d") or {}
    prev = snap.get("prevDailyBar") or snap.get("pd") or {}
    quote = snap.get("latestQuote") or snap.get("q") or {}

    price = _num(trade.get("p"))
    if price is None:
        price = _num(minute.get("c"))
    if price is None:
        price = _num(day.get("c"))
    if price is None:
        return None

    prev_close = _num(prev.get("c"))
    # When prevDailyBar is absent (e.g. fresh IPO), fall back to today's open.
    if prev_close is None:
        prev_close = _num(day.get("o"))

    change = change_pct = None
    if prev_close is not None and prev_close != 0:
        change = price - prev_close
        change_pct = change / prev_close * 100.0

    bid = _num(quote.get("bp"))
    ask = _num(quote.get("ap"))

    return {
        "ticker": symbol.upper(),
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "day_open": _num(day.get("o")),
        "day_high": _num(day.get("h")),
        "day_low": _num(day.get("l")),
        "day_close": _num(day.get("c")),
        "day_volume": _num(day.get("v")),
        "bid": bid,
        "ask": ask,
        "ts": trade.get("t") or minute.get("t") or day.get("t"),
        "source": "alpaca",
    }


def parse_snapshot(payload: dict) -> dict:
    """Parse a full /v2/stocks/snapshots response into {TICKER: quote}.

    Alpaca returns either a bare map {"AAPL": {...}} or, on some endpoints,
    {"snapshots": {"AAPL": {...}}}. Both are handled. Symbols that fail to parse
    are dropped silently (never raises)."""
    if not isinstance(payload, dict):
        return {}
    snaps = payload.get("snapshots") if isinstance(payload.get("snapshots"), dict) else payload
    out: dict[str, dict] = {}
    for sym, snap in snaps.items():
        if sym in ("snapshots", "next_page_token") or not isinstance(snap, dict):
            continue
        q = parse_one(sym, snap)
        if q is not None:
            out[sym.upper()] = q
    return out


def parse_clock(payload: dict) -> dict:
    """Normalize Alpaca /v2/clock into {is_open, next_open, next_close, ts}."""
    if not isinstance(payload, dict):
        return {"is_open": None}
    return {
        "is_open": bool(payload.get("is_open")) if "is_open" in payload else None,
        "next_open": payload.get("next_open"),
        "next_close": payload.get("next_close"),
        "ts": payload.get("timestamp"),
        "source": "alpaca",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Network (requires keys) — thin wrappers around the pure parsers above
# ─────────────────────────────────────────────────────────────────────────────
def _http_json(url: str, key: str, secret: str) -> dict:
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "WealthDashboard/1.0",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def fetch_snapshots(symbols: list[str], key: str, secret: str,
                    feed: str = DEFAULT_FEED) -> dict:
    """Fetch live snapshots for up to MAX_SYMBOLS tickers in one call.

    Returns {TICKER: quote}. Empty dict on no symbols / no keys. Raises only on
    a hard transport error so the caller (proxy) can map it to a 502."""
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    # de-dup, preserve order, cap
    seen: set[str] = set()
    uniq = [s for s in syms if not (s in seen or seen.add(s))][:MAX_SYMBOLS]
    if not uniq or not (key and secret):
        return {}
    qs = urllib.parse.urlencode({"symbols": ",".join(uniq), "feed": feed})
    url = f"{DATA_BASE}/v2/stocks/snapshots?{qs}"
    return parse_snapshot(_http_json(url, key, secret))


def fetch_clock(key: str, secret: str) -> dict:
    """Fetch market clock (is_open + next open/close). {} on no keys."""
    if not (key and secret):
        return {}
    return parse_clock(_http_json(f"{TRADING_BASE}/v2/clock", key, secret))


__all__ = ["parse_one", "parse_snapshot", "parse_clock",
           "fetch_snapshots", "fetch_clock", "DEFAULT_FEED", "MAX_SYMBOLS"]
