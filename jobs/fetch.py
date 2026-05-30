"""
jobs/fetch.py — stdlib HTTP fetch layer for the daily snapshot pipeline.

Plan refs: §5 (two-stage scan / budget), §23 (contracts), §24 (raw Layer 0),
§32 (provider circuit breaker, metrics).

Design principles
-----------------
* **stdlib only** — urllib, no requests/httpx. The GHA pipeline stays light.
* **contract-validated** — every response is checked against its Phase 2
  contract (jobs/contracts/) before use; violations are logged, not swallowed.
* **Layer 0 persistence** — raw responses optionally stored to
  raw_api_responses with a 7-day TTL (debug + replay, §24).
* **budget aware** — token-bucket RateLimiter + per-provider CircuitBreaker,
  with running counters the orchestrator reads into snapshot_metadata.
* **keys from env** — FINNHUB_KEY / FRED_KEY, never hardcoded.
  SEC is keyless; SEC_USER_AGENT can be set for fair-access compliance.
* **synthetic mode** — deterministic offline bars so the whole pipeline can
  run end-to-end in CI / locally with no network and no API keys.

Public surface
--------------
    f = Fetcher(synthetic=False)            # or synthetic=True for tests/CI
    bars = f.fetch_yahoo_chart("NVDA")      # list[Bar], newest last
    q    = f.fetch_finnhub_quote("NVDA")    # dict | None
    obs  = f.fetch_fred_series("DGS10")     # list[{date, value}]
    f.stats                                 # dict for snapshot_metadata
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from _logging import get_logger

# ---------------------------------------------------------------------------
# Providers + tuning
# ---------------------------------------------------------------------------
PROVIDERS = ("yahoo", "finnhub", "fred", "sec", "alpaca", "tiingo")

# Conservative per-minute request budgets (free tiers).
RATE_PER_MIN = {
    "yahoo": 120,
    "finnhub": 55,
    "fred": 110,
    "sec": 300,
    "alpaca": 180,
    "tiingo": 50,
}

# CircuitBreaker: open after this many *consecutive* failures (§11/§32).
BREAKER_THRESHOLD = 10

# Layer-0 raw retention (§24): keep 7 days for debugging/replay.
RAW_TTL_DAYS = 7

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "WealthDashboard/1.0 contact@example.com",
)

FRED_DEFAULT_SERIES = (
    "DGS10", "DGS2", "T10Y2Y", "T10YIE", "BAMLH0A0HYM2",
    "VIXCLS", "DFF", "UNRATE", "CPIAUCSL", "DTWEXBGS",
)


# ---------------------------------------------------------------------------
# Data holder
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    """One daily OHLCV bar. adj_close falls back to close when unavailable."""
    date: str           # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int

    def as_row(self, ticker: str, source: str) -> dict:
        return {
            "date": self.date, "ticker": ticker,
            "open": self.open, "high": self.high, "low": self.low,
            "close": self.close, "adj_close": self.adj_close,
            "volume": self.volume, "source": source,
        }


class FetchError(Exception):
    """Network / HTTP / parse failure (distinct from a contract violation)."""


# ---------------------------------------------------------------------------
# Rate limiting + circuit breaking
# ---------------------------------------------------------------------------
class RateLimiter:
    """Simple monotonic token bucket. acquire() blocks only when empty."""

    def __init__(self, rate_per_min: int):
        self.capacity = float(rate_per_min)
        self.tokens = float(rate_per_min)
        self.refill_per_sec = rate_per_min / 60.0
        self.last = time.monotonic()

    def acquire(self) -> float:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens < 1.0:
            wait = (1.0 - self.tokens) / self.refill_per_sec
            time.sleep(wait)
            self.tokens = 0.0
            return wait
        self.tokens -= 1.0
        return 0.0


class CircuitBreaker:
    """Opens after N consecutive failures; closes on first success."""

    def __init__(self, provider: str, threshold: int = BREAKER_THRESHOLD):
        self.provider = provider
        self.threshold = threshold
        self.consecutive_failures = 0
        self.is_open = False

    def allow(self) -> bool:
        return not self.is_open

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.is_open = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.is_open = True


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class Fetcher:
    """Stateful fetch client. One per snapshot run so budget counters accrue.

    Args:
        synthetic:  if True, never touch the network — generate deterministic
                    bars/observations. Used by tests + CI.
        store_raw:  persist raw payloads to raw_api_responses (Layer 0).
        as_of:      anchor date (YYYY-MM-DD) for synthetic series; default today.
        seed:       base seed for synthetic determinism.
    """

    def __init__(
        self,
        *,
        synthetic: bool = False,
        store_raw: bool = True,
        as_of: Optional[str] = None,
        seed: int = 1729,
        log=None,
    ):
        self.synthetic = synthetic
        self.store_raw = store_raw and not synthetic
        self.as_of = as_of or datetime.now(timezone.utc).date().isoformat()
        self.seed = seed
        self.log = log or get_logger("fetch")
        self.finnhub_key = os.environ.get("FINNHUB_KEY", "")
        self.fred_key = os.environ.get("FRED_KEY", "")
        self.limiters = {p: RateLimiter(RATE_PER_MIN[p]) for p in PROVIDERS}
        self.breakers = {p: CircuitBreaker(p) for p in PROVIDERS}
        self._sec_ticker_map: Optional[dict[str, int]] = None
        self.stats: dict[str, Any] = {
            "calls_by_provider": defaultdict(int),
            "errors_by_provider": defaultdict(int),
            "rate_limit_hits": 0,
            "raw_stored": 0,
        }

    # -- snapshot_metadata helper -------------------------------------------
    def stats_summary(self) -> dict:
        return {
            "calls_by_provider": dict(self.stats["calls_by_provider"]),
            "errors_by_provider": dict(self.stats["errors_by_provider"]),
            "rate_limit_hits": self.stats["rate_limit_hits"],
            "raw_stored": self.stats["raw_stored"],
            "breakers_open": [p for p, b in self.breakers.items() if b.is_open],
        }

    # -- low-level HTTP ------------------------------------------------------
    # Statuses worth retrying: 429 (rate limit) + transient 5xx.
    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
    _RETRY_BACKOFF = (2.0, 5.0, 12.0)   # seconds between attempts

    def _http_json(self, provider: str, url: str, timeout: float = 20.0,
                   retries: int = 2, extra_headers: Optional[dict] = None) -> tuple[int, Any]:
        """GET url, return (status_code, parsed_json). Raises FetchError on failure.

        Retries on 429/5xx with exponential-ish backoff (helps Yahoo, which
        429s intermittently from datacenter IPs). The circuit breaker only
        trips after the final attempt fails, so a single 429 doesn't open it.
        extra_headers carries provider auth (e.g. Alpaca APCA-API-KEY-ID)."""
        if not self.breakers[provider].allow():
            raise FetchError(f"{provider} circuit open")
        last_err: Optional[Exception] = None
        attempts = retries + 1
        for attempt in range(attempts):
            waited = self.limiters[provider].acquire()
            if waited > 0:
                self.stats["rate_limit_hits"] += 1
            self.stats["calls_by_provider"][provider] += 1
            ua = _SEC_USER_AGENT if provider == "sec" else _USER_AGENT
            headers = {"User-Agent": ua, "Accept": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = resp.getcode()
                    body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                self.breakers[provider].record_success()
                return status, data
            except urllib.error.HTTPError as e:
                last_err = FetchError(f"{provider} HTTP {e.code}: {e.reason}")
                self.stats["errors_by_provider"][provider] += 1
                if e.code in self._RETRY_STATUS and attempt < attempts - 1:
                    if e.code == 429:
                        self.stats["rate_limit_hits"] += 1
                    time.sleep(self._RETRY_BACKOFF[min(attempt, len(self._RETRY_BACKOFF) - 1)])
                    continue
                self.breakers[provider].record_failure()
                raise last_err from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
                last_err = FetchError(f"{provider} fetch failed: {e}")
                self.stats["errors_by_provider"][provider] += 1
                if attempt < attempts - 1:
                    time.sleep(self._RETRY_BACKOFF[min(attempt, len(self._RETRY_BACKOFF) - 1)])
                    continue
                self.breakers[provider].record_failure()
                raise last_err from e
        # Should not reach here, but satisfy type checker
        raise last_err or FetchError(f"{provider} fetch failed")

    # -- Layer 0 persistence ------------------------------------------------
    def _store_raw(self, source: str, endpoint: str, ticker: str,
                   status: int, payload: Any) -> None:
        if not self.store_raw:
            return
        try:
            import db
            expires = (datetime.now(timezone.utc) + timedelta(days=RAW_TTL_DAYS)).isoformat()
            blob = json.dumps(payload, default=str)[:200_000]  # cap blob size
            # Raw Layer-0 persistence is best-effort debug data. It is often
            # called while a higher-level step has an open write transaction
            # (for example macro/fundamental upserts), so using the default
            # 30s SQLite timeout can stall GitHub Actions for minutes. Use a
            # very short, independent connection and skip on lock contention.
            db.ensure_cloud_dir()
            conn = sqlite3.connect(str(db.CLOUD_DB_PATH), timeout=0.2)
            try:
                conn.execute("PRAGMA busy_timeout = 200")
                conn.execute(
                    "INSERT INTO raw_api_responses "
                    "(source, endpoint, ticker, status_code, response_blob, expires_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (source, endpoint, ticker or None, status, blob, expires),
                )
                conn.commit()
            finally:
                conn.close()
            self.stats["raw_stored"] += 1
        except Exception as e:  # never let logging break the pipeline
            self.log.debug("raw_store_skipped", source=source, ticker=ticker, err=str(e))

    # ======================================================================
    # Yahoo Finance — daily chart (primary price history)
    # ======================================================================
    def fetch_yahoo_chart(self, ticker: str, range_: str = "1y",
                          interval: str = "1d") -> list[Bar]:
        """Return daily Bars (oldest→newest). Empty list on hard failure."""
        if self.synthetic:
            return self._synthetic_bars(ticker, _range_to_days(range_))

        from contracts.yahoo_chart import YahooChart
        sym = ticker.replace(".", "-")  # Yahoo uses BRK-B not BRK.B
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?range={range_}&interval={interval}&events=split,div")
        try:
            status, data = self._http_json("yahoo", url)
        except FetchError as e:
            self.log.warning("yahoo_fetch_failed", ticker=ticker, err=str(e))
            return []

        ok, errs, _ = YahooChart.validate(data)
        if not ok:
            YahooChart.log_violation(ticker, errs, data)
            self.log.warning("yahoo_contract_violation", ticker=ticker, errs=errs[:3])
            return []

        self._store_raw("yahoo", "chart/v8", ticker, status, data)
        return _parse_yahoo_bars(data)

    def price_provider_order(self) -> list[str]:
        """Real price-history providers to try, in priority order, gated by key
        presence. Keyed providers (Alpaca/Tiingo) work from datacenter IPs where
        Yahoo 429s, so they're tried first when configured. Yahoo is keyless and
        is the always-available fallback."""
        order = []
        if os.environ.get("ALPACA_KEY") and os.environ.get("ALPACA_SECRET"):
            order.append("alpaca")
        if os.environ.get("TIINGO_KEY"):
            order.append("tiingo")
        order.append("yahoo")
        return order

    def fetch_price_history(self, ticker: str, range_: str = "1y") -> dict:
        """Fetch bars + split events from the first real provider that succeeds.

        Returns {"bars": list[Bar], "splits": [...], "source": provider|None,
                 "attempts": [{"provider","ok","reason","bars"}]}. Never raises —
                 `attempts` records WHY each provider failed (for snapshot health)."""
        if self.synthetic:
            return {"bars": self._synthetic_bars(ticker, _range_to_days(range_)),
                    "splits": [], "source": "synthetic",
                    "attempts": [{"provider": "synthetic", "ok": True, "reason": None}]}

        attempts: list[dict] = []
        for provider in self.price_provider_order():
            try:
                bars, splits = self._fetch_bars_from(provider, ticker, range_)
                if bars:
                    attempts.append({"provider": provider, "ok": True,
                                     "reason": None, "bars": len(bars)})
                    return {"bars": bars, "splits": splits, "source": provider,
                            "attempts": attempts}
                attempts.append({"provider": provider, "ok": False, "reason": "no_data"})
            except FetchError as e:
                attempts.append({"provider": provider, "ok": False, "reason": str(e)})
            except Exception as e:                                 # noqa: BLE001
                attempts.append({"provider": provider, "ok": False,
                                 "reason": f"unexpected: {e}"})
        self.log.warning("price_history_all_providers_failed", ticker=ticker,
                         attempts=[a["provider"] + ":" + (a["reason"] or "ok") for a in attempts])
        return {"bars": [], "splits": [], "source": None, "attempts": attempts}

    def _fetch_bars_from(self, provider: str, ticker: str, range_: str):
        """Dispatch to one provider → (bars, splits). Raises FetchError on failure."""
        if provider == "yahoo":
            return self._yahoo_bars_splits(ticker, range_)
        if provider == "alpaca":
            return self._alpaca_bars(ticker, range_), []
        if provider == "tiingo":
            return self._tiingo_bars(ticker, range_), []
        raise FetchError(f"unknown price provider: {provider}")

    def _yahoo_bars_splits(self, ticker: str, range_: str):
        from contracts.yahoo_chart import YahooChart
        sym = ticker.replace(".", "-")
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?range={range_}&interval=1d&events=split,div")
        status, data = self._http_json("yahoo", url)        # raises FetchError on HTTP/429
        ok, errs, _ = YahooChart.validate(data)
        if not ok:
            YahooChart.log_violation(ticker, errs, data)
            raise FetchError(f"yahoo contract violation: {errs[:2]}")
        self._store_raw("yahoo", "chart/v8", ticker, status, data)
        return _parse_yahoo_bars(data), parse_yahoo_splits(data)

    def _alpaca_bars(self, ticker: str, range_: str) -> list:
        """Alpaca historical daily bars (env ALPACA_KEY / ALPACA_SECRET)."""
        key = os.environ.get("ALPACA_KEY")
        secret = os.environ.get("ALPACA_SECRET")
        if not (key and secret):
            raise FetchError("alpaca: no key (set ALPACA_KEY/ALPACA_SECRET)")
        start = self._history_start(range_)
        url = (f"https://data.alpaca.markets/v2/stocks/{ticker.upper()}/bars"
               f"?timeframe=1Day&adjustment=split&feed=iex&limit=10000&start={start}"
               + (f"&end={self.as_of}" if self.as_of else ""))
        status, data = self._http_json("alpaca", url, extra_headers={
            "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._store_raw("alpaca", "v2/bars", ticker, status, data)
        out = []
        for b in (data.get("bars") or []):
            d = str(b.get("t", ""))[:10]
            c = b.get("c")
            if not d or c is None:
                continue
            out.append(Bar(date=d, open=b.get("o", c), high=b.get("h", c),
                           low=b.get("l", c), close=c, adj_close=c,
                           volume=int(b.get("v") or 0)))
        out.sort(key=lambda x: x.date)
        return out

    def _tiingo_bars(self, ticker: str, range_: str) -> list:
        """Tiingo EOD daily prices (env TIINGO_KEY)."""
        key = os.environ.get("TIINGO_KEY")
        if not key:
            raise FetchError("tiingo: no key (set TIINGO_KEY)")
        start = self._history_start(range_)
        url = (f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices"
               f"?startDate={start}&token={key}&format=json")
        status, data = self._http_json("tiingo", url)
        self._store_raw("tiingo", "daily/prices", ticker, status, data)
        out = []
        for b in (data or []):
            d = str(b.get("date", ""))[:10]
            c = b.get("close")
            if not d or c is None:
                continue
            adj = b.get("adjClose")
            out.append(Bar(date=d, open=b.get("open", c), high=b.get("high", c),
                           low=b.get("low", c), close=c,
                           adj_close=adj if adj is not None else c,
                           volume=int(b.get("volume") or 0)))
        out.sort(key=lambda x: x.date)
        return out

    def _history_start(self, range_: str) -> str:
        """ISO start date `range_` days before as_of (×1.5 buffer for non-trading days)."""
        from datetime import date as _date, timedelta as _td
        days = int(_range_to_days(range_) * 1.5)
        try:
            anchor = _date.fromisoformat(self.as_of) if self.as_of else _date.today()
        except (ValueError, TypeError):
            anchor = _date.today()
        return (anchor - _td(days=days)).isoformat()

    # ======================================================================
    # Finnhub — realtime quote (reconciliation + fallback)
    # ======================================================================
    def fetch_finnhub_quote(self, ticker: str) -> Optional[dict]:
        """Return validated quote dict {c,h,l,o,pc,t} or None."""
        if self.synthetic:
            bars = self._synthetic_bars(ticker, 2)
            last = bars[-1]
            return {"c": last.close, "h": last.high, "l": last.low,
                    "o": last.open, "pc": bars[-2].close if len(bars) > 1 else last.open,
                    "t": int(time.time())}
        if not self.finnhub_key:
            return None
        from contracts.finnhub_quote import FinnhubQuote
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={self.finnhub_key}"
        try:
            status, data = self._http_json("finnhub", url)
        except FetchError as e:
            self.log.warning("finnhub_fetch_failed", ticker=ticker, err=str(e))
            return None
        ok, errs, cleaned = FinnhubQuote.validate(data)
        if not ok:
            FinnhubQuote.log_violation(ticker, errs, data)
            return None
        self._store_raw("finnhub", "quote", ticker, status, data)
        return data

    # ======================================================================
    # FRED — macro series
    # ======================================================================
    def fetch_fred_series(self, series_id: str,
                          start: Optional[str] = None) -> list[dict]:
        """Return [{date, value(float)}], oldest→newest. Skips NA ('.') values."""
        if self.synthetic:
            return self._synthetic_fred(series_id)
        if not self.fred_key:
            self.log.warning("fred_no_key", series=series_id)
            return []
        from contracts.fred_series import FredSeries
        start = start or (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={self.fred_key}"
               f"&file_type=json&observation_start={start}")
        try:
            status, data = self._http_json("fred", url)
        except FetchError as e:
            self.log.warning("fred_fetch_failed", series=series_id, err=str(e))
            return []
        ok, errs, _ = FredSeries.validate(data)
        if not ok:
            FredSeries.log_violation(series_id, errs, data)
            return []
        self._store_raw("fred", "series/observations", series_id, status, data)
        out = []
        for o in data["observations"]:
            v = o.get("value", ".")
            if v in (".", "", None):
                continue
            try:
                out.append({"date": o["date"], "value": float(v)})
            except (ValueError, TypeError):
                continue
        return out

    # ======================================================================
    # Fundamentals (Finnhub /stock/metric; synthetic for offline)
    # ======================================================================
    def fetch_fundamentals(self, ticker: str, model_type: str = "operating_company") -> Optional[dict]:
        """Return a canonical fundamental record (line items + model-specific
        metrics) or None.

        Real path tries SEC EDGAR CompanyFacts first for US operating companies
        because it is official, free, and line-item based. Finnhub metrics remain
        a ratio fallback, mostly for coverage when SEC cannot map the ticker."""
        if self.synthetic:
            return self._synthetic_fundamentals(ticker, model_type)

        if model_type in ("operating_company", "insurance", "biotech_pre_revenue"):
            rec = self.fetch_sec_companyfacts(ticker, model_type)
            if rec:
                return rec

        if not self.finnhub_key:
            return None
        from contracts.finnhub_metrics import FinnhubMetrics
        url = (f"https://finnhub.io/api/v1/stock/metric"
               f"?symbol={ticker}&metric=all&token={self.finnhub_key}")
        try:
            status, data = self._http_json("finnhub", url)
        except FetchError as e:
            self.log.warning("finnhub_metrics_failed", ticker=ticker, err=str(e))
            return None
        ok, errs, _ = FinnhubMetrics.validate(data)
        if not ok:
            FinnhubMetrics.log_violation(ticker, errs, data)
            return None
        self._store_raw("finnhub", "stock/metric", ticker, status, data)
        return _map_finnhub_metric(ticker, data, self.as_of, model_type)

    def fetch_sec_companyfacts(self, ticker: str, model_type: str = "operating_company") -> Optional[dict]:
        """Fetch SEC CompanyFacts and map common US-GAAP concepts to line items.

        SEC is no-key and official, but not every ticker has a mapping and not
        every company uses every standard tag. Failures intentionally fall back
        to the next provider."""
        cik = self._sec_cik_for_ticker(ticker)
        if not cik:
            return None
        from contracts.sec_companyfacts import SecCompanyFacts
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        try:
            status, data = self._http_json("sec", url, timeout=30.0, retries=2)
        except FetchError as e:
            self.log.warning("sec_companyfacts_failed", ticker=ticker, err=str(e))
            return None
        ok, errs, _ = SecCompanyFacts.validate(data)
        if not ok:
            SecCompanyFacts.log_violation(ticker, errs, data)
            return None
        self._store_raw("sec", "companyfacts", ticker, status, data)
        return _map_sec_companyfacts(ticker, data, self.as_of, model_type)

    def _sec_cik_for_ticker(self, ticker: str) -> Optional[int]:
        if self._sec_ticker_map is None:
            try:
                status, data = self._http_json(
                    "sec", "https://www.sec.gov/files/company_tickers.json",
                    timeout=30.0, retries=2)
            except FetchError as e:
                self.log.warning("sec_ticker_map_failed", err=str(e))
                self._sec_ticker_map = {}
                return None
            mapping: dict[str, int] = {}
            if isinstance(data, dict):
                for row in data.values():
                    if not isinstance(row, dict):
                        continue
                    tk = str(row.get("ticker") or "").upper()
                    cik = row.get("cik_str")
                    if tk and isinstance(cik, int):
                        mapping[tk] = cik
            self._store_raw("sec", "company_tickers", "", status, data)
            self._sec_ticker_map = mapping
        t = (ticker or "").upper()
        return self._sec_ticker_map.get(t) or self._sec_ticker_map.get(t.replace(".", "-"))

    # ======================================================================
    # Synthetic generators (deterministic, offline)
    # ======================================================================
    def _ticker_seed(self, ticker: str) -> int:
        h = 0
        for ch in ticker:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        return (h ^ self.seed) & 0xFFFFFFFF

    # Canonical synthetic history anchor — far enough back to cover 5y windows.
    _SYNTH_START = "2019-01-01"

    def _synthetic_bars(self, ticker: str, n_days: int) -> list[Bar]:
        """Deterministic GBM-ish daily bars, last n_days ending at self.as_of.

        The full canonical series (from _SYNTH_START to as_of) is generated and
        then sliced, so the price on any given date is identical regardless of
        the requested window length. This makes two synthetic "sources" (price
        history vs quote) agree on the same date — required for reconciliation
        and outcome tests to be meaningful."""
        import random
        rng = random.Random(self._ticker_seed(ticker))
        base = 20 + rng.random() * 480           # $20–$500 start
        drift = (rng.random() - 0.40) * 0.0016   # slight upward bias
        vol = 0.010 + rng.random() * 0.030       # 1%–4% daily
        start = datetime.fromisoformat(self._SYNTH_START).date()
        end = datetime.fromisoformat(self.as_of).date()

        bars: list[Bar] = []
        price = base
        d = start
        while d <= end:
            if d.weekday() < 5:  # weekdays only (holidays ignored for synthetic)
                ret = drift + rng.gauss(0, vol)
                prev = price
                price = max(0.5, price * (1 + ret))
                hi = max(prev, price) * (1 + abs(rng.gauss(0, vol / 2)))
                lo = min(prev, price) * (1 - abs(rng.gauss(0, vol / 2)))
                vlm = int(1_000_000 + rng.random() * 9_000_000)
                bars.append(Bar(d.isoformat(), round(prev, 2), round(hi, 2),
                                round(lo, 2), round(price, 2), round(price, 2), vlm))
            d += timedelta(days=1)
        return bars[-n_days:] if n_days < len(bars) else bars

    def _synthetic_fundamentals(self, ticker: str, model_type: str) -> dict:
        """Deterministic, model-type-varied fundamentals for offline testing.

        Per-ticker seed gives a realistic spread of quality so the fundamental
        analyst produces a distribution (not a constant). Returns line items for
        operating/insurance/biotech and ratio bundles for bank/reit."""
        import random
        rng = random.Random(self._ticker_seed(ticker) ^ 0xF00D)
        end = datetime.fromisoformat(self.as_of).date()
        # most recent completed quarter
        q = (end.month - 1) // 3 or 4
        fy = end.year if end.month > 3 else end.year - 1
        fiscal_period = f"{fy}-Q{q}"
        report_date = (end - timedelta(days=35)).isoformat()
        usable_at = (end - timedelta(days=34)).isoformat()
        base = {"source": "synthetic", "fiscal_period": fiscal_period,
                "report_date": report_date, "usable_at": usable_at,
                "model_type": model_type}

        if model_type == "bank":
            base.update({
                "roe": round(0.06 + rng.random() * 0.12, 4),
                "roa": round(0.004 + rng.random() * 0.016, 4),
                "efficiency_ratio": round(0.45 + rng.random() * 0.25, 4),   # lower better
                "net_interest_margin": round(0.020 + rng.random() * 0.022, 4),
                "capital_ratio": round(0.09 + rng.random() * 0.07, 4),
                "credit_quality": round(0.3 + rng.random() * 2.2, 3),       # NPA %, lower better
                "tangible_book": round(10 + rng.random() * 60, 2),
            })
            return base
        if model_type == "reit":
            ffo = round(1.5 + rng.random() * 5.0, 2)
            base.update({
                "ffo_per_share": ffo,
                "affo_per_share": round(ffo * (0.80 + rng.random() * 0.15), 2),
                "occupancy": round(0.85 + rng.random() * 0.13, 4),
                "debt_maturity_yrs": round(3 + rng.random() * 6, 1),
                "int_cov_reit": round(2.0 + rng.random() * 4.0, 2),
                "dividend_safety": round(1.0 + rng.random() * 0.6, 3),      # AFFO/div
            })
            return base
        if model_type == "biotech_pre_revenue":
            cash = round((50 + rng.random() * 800) * 1e6, 0)
            burn = round((20 + rng.random() * 120) * 1e6, 0)               # per quarter
            shares = round((50 + rng.random() * 300) * 1e6, 0)
            base.update({
                "revenue": round(rng.random() * 40e6, 0),                  # < $50M
                "cash_and_sti": cash, "quarterly_burn": burn,
                "shares_out": shares,
                "shares_out_prior": round(shares / (1 + rng.random() * 0.25), 0),
            })
            return base

        # operating_company / insurance — full line items
        revenue = round((1 + rng.random() * 199) * 1e9, 0)
        growth = round(-0.10 + rng.random() * 0.45, 4)
        gm = 0.25 + rng.random() * 0.45
        om = 0.05 + rng.random() * 0.35
        nm = om * (0.65 + rng.random() * 0.25)
        capex_pct = 0.02 + rng.random() * 0.10
        sbc_pct = rng.random() * 0.08
        op_income = revenue * om
        base.update({
            "revenue": revenue,
            "revenue_prior": round(revenue / (1 + growth), 0),
            "gross_profit": round(revenue * gm, 0),
            "operating_income": round(op_income, 0),
            "net_income": round(revenue * nm, 0),
            "cfo": round(op_income * (1.0 + rng.random() * 0.25), 0),
            "capex": round(revenue * capex_pct, 0),
            "sbc": round(revenue * sbc_pct, 0),
            "total_debt": round(revenue * rng.random() * 0.8, 0),
            "cash_and_sti": round(revenue * (0.05 + rng.random() * 0.45), 0),
            "total_equity": round(revenue * (0.5 + rng.random() * 1.5), 0),
            "shares_out": round((0.5 + rng.random() * 9) * 1e9, 0),
            "interest_expense": None,   # filled below
            "ebitda": round(op_income + revenue * 0.05, 0),
        })
        base["interest_expense"] = round(base["total_debt"] * 0.05, 0)
        return base

    def _synthetic_fred(self, series_id: str) -> list[dict]:
        import random
        rng = random.Random(self._ticker_seed(series_id))
        anchors = {"DGS10": 4.3, "DGS2": 4.6, "T10Y2Y": -0.3, "T10YIE": 2.3,
                   "BAMLH0A0HYM2": 3.1, "VIXCLS": 16.0, "DFF": 4.3,
                   "UNRATE": 4.0, "CPIAUCSL": 315.0, "DTWEXBGS": 121.0}
        level = anchors.get(series_id, 1.0 + rng.random() * 5)
        end = datetime.fromisoformat(self.as_of).date()
        out = []
        for i in range(120, -1, -1):
            d = end - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            level = max(0.01, level + rng.gauss(0, abs(level) * 0.004))
            out.append({"date": d.isoformat(), "value": round(level, 4)})
        return out


# ---------------------------------------------------------------------------
# Module-level parse helpers
# ---------------------------------------------------------------------------
def _range_to_days(range_: str) -> int:
    return {"1mo": 22, "3mo": 66, "6mo": 130, "1y": 260,
            "2y": 520, "5y": 1300}.get(range_, 260)


def _parse_yahoo_bars(data: dict) -> list[Bar]:
    """Convert a validated Yahoo chart payload into a list[Bar] (oldest→newest)."""
    result = data["chart"]["result"][0]
    ts = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    adj_block = result["indicators"].get("adjclose")
    adjs = adj_block[0].get("adjclose") if adj_block else None

    bars: list[Bar] = []
    for i, epoch in enumerate(ts):
        c = _at(closes, i)
        if c is None:
            continue  # Yahoo pads missing sessions with null
        o = _at(opens, i, c)
        h = _at(highs, i, c)
        lo = _at(lows, i, c)
        v = _at(volumes, i, 0) or 0
        adj = _at(adjs, i, c) if adjs else c
        d = datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()
        bars.append(Bar(d, float(o), float(h), float(lo), float(c),
                        float(adj), int(v)))
    return bars


def parse_yahoo_splits(data: dict) -> list[dict]:
    """Extract split events from a Yahoo chart payload (events.splits).

    Returns [{ex_date, split_ratio}] where split_ratio>1 means a forward split
    (e.g. 4.0 for a 4:1 split)."""
    out = []
    try:
        events = data["chart"]["result"][0].get("events", {})
    except (KeyError, IndexError, TypeError):
        return out
    for _, ev in (events.get("splits") or {}).items():
        try:
            num = float(ev.get("numerator"))
            den = float(ev.get("denominator"))
            ratio = num / den if den else None
            ex_date = datetime.fromtimestamp(
                int(ev["date"]), tz=timezone.utc).date().isoformat()
            if ratio:
                out.append({"ex_date": ex_date, "split_ratio": round(ratio, 6)})
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return out


def _at(arr: list, i: int, default=None):
    if arr is None or i >= len(arr):
        return default
    v = arr[i]
    return default if v is None else v


def _map_finnhub_metric(ticker: str, data: dict, as_of: str,
                        model_type: str = "operating_company") -> dict:
    """Map Finnhub /stock/metric → canonical record (ratios path).

    Finnhub returns TTM ratios in percent. We pass them through as fractions in
    a `ratios` bundle; the normalizer prefers line-item-derived ratios and falls
    back to these when line items are unavailable (free-tier reality)."""
    m = data.get("metric", {}) or {}

    def pct(key):
        v = m.get(key)
        return (v / 100.0) if isinstance(v, (int, float)) else None

    def num(key):
        v = m.get(key)
        return v if isinstance(v, (int, float)) else None

    end = datetime.fromisoformat(as_of).date()
    q = (end.month - 1) // 3 or 4
    fy = end.year if end.month > 3 else end.year - 1
    ratios = {
        "gross_margin": pct("grossMarginTTM"),
        "operating_margin": pct("operatingMarginTTM"),
        "net_margin": pct("netProfitMarginTTM"),
        "roic": pct("roiTTM"),
        "roe": pct("roeTTM"),
        "roa": pct("roaTTM"),
        "revenue_growth_yoy": pct("revenueGrowthTTMYoy"),
        "debt_to_equity": num("totalDebt/totalEquityQuarterly"),
        "current_ratio": num("currentRatioQuarterly"),
        "net_interest_margin": pct("netInterestMarginTTM"),
        "pe": num("peTTM"), "ps": num("psTTM"),
        "pfcf": num("pfcfShareTTM"), "pb": num("pbQuarterly"),
        "div_yield": num("dividendYieldIndicatedAnnual"),
    }
    rec = {
        "source": "finnhub", "fiscal_period": f"{fy}-Q{q}",
        "report_date": None, "usable_at": as_of,
        "ratios": ratios,
    }
    if model_type == "bank":
        rec.update({
            "roe": ratios.get("roe"),
            "roa": ratios.get("roa"),
            "net_interest_margin": ratios.get("net_interest_margin"),
            "tangible_book": ratios.get("pb"),
        })
    return rec


# ---------------------------------------------------------------------------
# SEC CompanyFacts mapper
# ---------------------------------------------------------------------------
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
_GROSS_PROFIT_TAGS = ("GrossProfit",)
_OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
_NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss")
_CFO_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)
_SBC_TAGS = ("ShareBasedCompensation", "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsGrantsInPeriodTotal")
_CASH_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
_SHORT_INVEST_TAGS = ("ShortTermInvestments", "MarketableSecuritiesCurrent")
_EQUITY_TAGS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
_SHARES_TAGS = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    "WeightedAverageNumberOfSharesOutstandingDiluted",
)
_SHARES_INSTANT_TAGS = ("EntityCommonStockSharesOutstanding",)
_INTEREST_EXPENSE_TAGS = (
    "InterestExpenseNonOperating",
    "InterestExpense",
    "InterestExpenseDebt",
)
_DDA_TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationDepletionAndAmortizationExpense",
)
_TOTAL_DEBT_TAGS = (
    "LongTermDebtAndFinanceLeaseObligations",
    "LongTermDebt",
)
_DEBT_CURRENT_TAGS = (
    "ShortTermBorrowings",
    "ShortTermDebt",
    "LongTermDebtCurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
)
_DEBT_NONCURRENT_TAGS = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
)


def _map_sec_companyfacts(ticker: str, data: dict, as_of: str,
                          model_type: str = "operating_company") -> Optional[dict]:
    """Map SEC CompanyFacts JSON to the canonical line-item record.

    Flow items use TTM when a newer 10-Q is available:
        latest 10-K + current YTD 10-Q - prior-year comparable YTD 10-Q.
    If that cannot be built safely, the mapper falls back to the latest annual
    10-K. Balance-sheet items use the latest filing end date."""
    usgaap = ((data.get("facts") or {}).get("us-gaap") or {})
    if not usgaap:
        return None

    annual_revenue = _sec_latest_annual(usgaap, _REVENUE_TAGS, as_of, unit="USD")
    if not annual_revenue:
        return None
    current_revenue_q = _sec_latest_quarterly_after(
        usgaap, _REVENUE_TAGS, as_of, annual_revenue.get("end"), unit="USD")
    ttm_context = None
    revenue = _sec_val(annual_revenue)
    prior_revenue = _sec_val(_sec_latest_annual(
        usgaap, _REVENUE_TAGS, as_of, unit="USD", before_end=annual_revenue.get("end")))
    base_fact = annual_revenue

    if current_revenue_q:
        annual_prior_revenue = _sec_latest_annual(
            usgaap, _REVENUE_TAGS, as_of, unit="USD", before_end=annual_revenue.get("end"))
        revenue_ttm, prior_ttm = _sec_ttm_pair(
            usgaap, _REVENUE_TAGS, as_of, annual_revenue, current_revenue_q,
            annual_prior_revenue, unit="USD")
        if revenue_ttm is not None:
            revenue = revenue_ttm
            prior_revenue = prior_ttm or prior_revenue
            base_fact = current_revenue_q
            ttm_context = current_revenue_q

    base_end = base_fact.get("end")
    fp = base_fact.get("fp") or ("TTM" if ttm_context else "FY")
    fy = base_fact.get("fy") or (base_end or as_of)[:4]
    fiscal_period = f"{fy}-{fp}-TTM" if ttm_context else f"{fy}-{fp}"

    def flow(tags):
        if ttm_context:
            annual = _sec_latest_annual(usgaap, tags, as_of, unit="USD")
            annual_prior = _sec_latest_annual(
                usgaap, tags, as_of, unit="USD",
                before_end=annual.get("end") if annual else None)
            value, _prior = _sec_ttm_pair(
                usgaap, tags, as_of, annual, ttm_context, annual_prior, unit="USD")
            if value is not None:
                return value
        f = _sec_pick_fact(usgaap, tags, as_of, unit="USD",
                           preferred_end=annual_revenue.get("end"),
                           preferred_accn=annual_revenue.get("accn"),
                           prefer_annual=True)
        return _sec_val(f)

    def instant(tags):
        f = _sec_pick_fact(usgaap, tags, as_of, unit="USD",
                           preferred_end=base_end, instant=True,
                           prefer_annual=False)
        return _sec_val(f)

    cfo = flow(_CFO_TAGS)
    capex = flow(_CAPEX_TAGS)
    if capex is not None:
        capex = abs(capex)
    op_income = flow(_OPERATING_INCOME_TAGS)
    dda = flow(_DDA_TAGS)
    ebitda = (op_income + dda) if (op_income is not None and dda is not None) else None

    cash = instant(_CASH_TAGS)
    sti = instant(_SHORT_INVEST_TAGS)
    if cash is not None and sti is not None:
        cash_and_sti = cash + sti
    else:
        cash_and_sti = cash if cash is not None else sti

    total_debt = instant(_TOTAL_DEBT_TAGS)
    if total_debt is None:
        current_debt = instant(_DEBT_CURRENT_TAGS)
        noncurrent_debt = instant(_DEBT_NONCURRENT_TAGS)
        if current_debt is not None or noncurrent_debt is not None:
            total_debt = (current_debt or 0) + (noncurrent_debt or 0)

    shares = _sec_val(_sec_pick_fact(
        usgaap, _SHARES_TAGS, as_of, unit="shares",
        preferred_end=base_end, preferred_accn=base_fact.get("accn"), prefer_annual=False))
    if shares is None:
        shares = _sec_val(_sec_pick_fact(
            usgaap, _SHARES_TAGS, as_of, unit="shares",
            preferred_end=annual_revenue.get("end"), preferred_accn=annual_revenue.get("accn"),
            prefer_annual=True))
    if shares is None:
        shares = _sec_val(_sec_pick_fact(
            usgaap, _SHARES_INSTANT_TAGS, as_of, unit="shares",
            preferred_end=base_end, instant=True, prefer_annual=False))

    return {
        "source": "sec",
        "fiscal_period": fiscal_period,
        "report_date": base_fact.get("filed"),
        "usable_at": base_fact.get("filed") or as_of,
        "model_type": model_type,
        "revenue": revenue,
        "revenue_prior": prior_revenue,
        "gross_profit": flow(_GROSS_PROFIT_TAGS),
        "operating_income": op_income,
        "net_income": flow(_NET_INCOME_TAGS),
        "cfo": cfo,
        "capex": capex,
        "sbc": flow(_SBC_TAGS),
        "total_debt": total_debt,
        "cash_and_sti": cash_and_sti,
        "total_equity": instant(_EQUITY_TAGS),
        "shares_out": shares,
        "interest_expense": flow(_INTEREST_EXPENSE_TAGS),
        "ebitda": ebitda,
        "sec_cik": data.get("cik"),
        "is_ttm": bool(ttm_context),
    }


def _sec_latest_annual(usgaap: dict, tags: tuple[str, ...], as_of: str, *,
                       unit: str = "USD", before_end: Optional[str] = None) -> Optional[dict]:
    fact = _sec_pick_fact(usgaap, tags, as_of, unit=unit, before_end=before_end,
                          prefer_annual=True)
    if fact and fact.get("form") in _ANNUAL_FORMS and _is_annual(fact):
        return fact
    return None


def _sec_latest_quarterly_after(usgaap: dict, tags: tuple[str, ...], as_of: str,
                                after_end: Optional[str], *,
                                unit: str = "USD") -> Optional[dict]:
    candidates: list[dict] = []
    for tag in tags:
        facts = (((usgaap.get(tag) or {}).get("units") or {}).get(unit) or [])
        for f in facts:
            if not isinstance(f, dict) or not isinstance(f.get("val"), (int, float)):
                continue
            if f.get("form") not in _QUARTERLY_FORMS:
                continue
            if f.get("filed") and f["filed"] > as_of:
                continue
            if after_end and f.get("end") and f["end"] <= after_end:
                continue
            ff = {**f, "_tag": tag, "_duration": _sec_duration_days(f)}
            if _is_quarterly(ff) or _is_ytd_quarterly(ff):
                candidates.append(ff)
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get("end") or "", f.get("filed") or ""), reverse=True)
    return candidates[0]


def _sec_ttm_pair(usgaap: dict, tags: tuple[str, ...], as_of: str,
                  annual: Optional[dict], current_q: dict,
                  annual_prior: Optional[dict], *,
                  unit: str = "USD") -> tuple[Optional[float], Optional[float]]:
    if not annual or not current_q:
        return (None, None)
    current = _sec_match_quarterly(usgaap, tags, as_of, current_q, unit=unit)
    prior = _sec_match_quarterly(usgaap, tags, as_of, current_q, prior_year=True, unit=unit)
    if not current or not prior:
        return (None, None)
    value = _sec_val(annual)
    cv = _sec_val(current)
    pv = _sec_val(prior)
    if value is None or cv is None or pv is None:
        return (None, None)
    ttm = value + cv - pv

    prev_ttm = None
    if annual_prior:
        prior2 = _sec_match_quarterly(
            usgaap, tags, as_of, prior, prior_year=True, unit=unit)
        avp = _sec_val(annual_prior)
        p2v = _sec_val(prior2)
        if avp is not None and p2v is not None:
            prev_ttm = avp + pv - p2v
    return (ttm, prev_ttm)


def _sec_match_quarterly(usgaap: dict, tags: tuple[str, ...], as_of: str,
                         context: dict, *, prior_year: bool = False,
                         unit: str = "USD") -> Optional[dict]:
    target_fp = context.get("fp")
    target_fy = context.get("fy")
    target_duration = context.get("_duration")
    if prior_year and isinstance(target_fy, int):
        target_fy -= 1
    candidates: list[dict] = []
    for tag in tags:
        facts = (((usgaap.get(tag) or {}).get("units") or {}).get(unit) or [])
        for f in facts:
            if not isinstance(f, dict) or not isinstance(f.get("val"), (int, float)):
                continue
            if f.get("form") not in _QUARTERLY_FORMS:
                continue
            if f.get("filed") and f["filed"] > as_of:
                continue
            if target_fp and f.get("fp") != target_fp:
                continue
            if target_fy and f.get("fy") != target_fy:
                continue
            ff = {**f, "_tag": tag, "_duration": _sec_duration_days(f)}
            if target_duration is not None and ff.get("_duration") is not None:
                if abs(ff["_duration"] - target_duration) > 20:
                    continue
            candidates.append(ff)
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get("end") or "", f.get("filed") or ""), reverse=True)
    return candidates[0]


def _sec_pick_fact(usgaap: dict, tags: tuple[str, ...], as_of: str, *,
                   unit: str = "USD", preferred_end: Optional[str] = None,
                   preferred_accn: Optional[str] = None, before_end: Optional[str] = None,
                   instant: bool = False, prefer_annual: bool = True) -> Optional[dict]:
    candidates: list[dict] = []
    for tag in tags:
        units = ((usgaap.get(tag) or {}).get("units") or {})
        facts = units.get(unit) or []
        for f in facts:
            if not isinstance(f, dict):
                continue
            if not isinstance(f.get("val"), (int, float)):
                continue
            filed = f.get("filed")
            if filed and filed > as_of:
                continue
            end = f.get("end")
            if before_end and end and end >= before_end:
                continue
            form = f.get("form")
            if prefer_annual and form not in (_ANNUAL_FORMS | _QUARTERLY_FORMS):
                continue
            duration = _sec_duration_days(f)
            if instant:
                # Instant balance-sheet facts usually omit start. If they do
                # include a start, keep only very short contexts.
                if duration is not None and duration > 10:
                    continue
            candidates.append({**f, "_tag": tag, "_duration": duration})

    if not candidates:
        return None
    if preferred_accn:
        same_accn = [f for f in candidates if f.get("accn") == preferred_accn]
        if same_accn:
            candidates = same_accn
    if preferred_end:
        same_end = [f for f in candidates if f.get("end") == preferred_end]
        if same_end:
            candidates = same_end
    if prefer_annual and not preferred_end:
        annual = [f for f in candidates if f.get("form") in _ANNUAL_FORMS and _is_annual(f)]
        if annual:
            candidates = annual
        else:
            quarterly = [f for f in candidates if f.get("form") in _QUARTERLY_FORMS and _is_quarterly(f)]
            if quarterly:
                candidates = quarterly

    candidates.sort(key=lambda f: (f.get("end") or "", f.get("filed") or ""), reverse=True)
    return candidates[0]


def _sec_duration_days(fact: dict) -> Optional[int]:
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start).date()
        e = datetime.fromisoformat(end).date()
        return (e - s).days
    except (TypeError, ValueError):
        return None


def _is_annual(fact: dict) -> bool:
    d = fact.get("_duration")
    return d is None or 300 <= d <= 430


def _is_quarterly(fact: dict) -> bool:
    d = fact.get("_duration")
    return d is None or 70 <= d <= 115


def _is_ytd_quarterly(fact: dict) -> bool:
    d = fact.get("_duration")
    return d is not None and 70 <= d <= 300


def _sec_val(fact: Optional[dict]) -> Optional[float]:
    if not fact:
        return None
    v = fact.get("val")
    return float(v) if isinstance(v, (int, float)) else None


def prune_raw_responses() -> int:
    """Delete expired Layer-0 rows. Returns count deleted. Safe if DB absent."""
    try:
        import db
        now = datetime.now(timezone.utc).isoformat()
        with db.cloud() as conn:
            cur = conn.execute(
                "DELETE FROM raw_api_responses WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,))
            return cur.rowcount or 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="fetch.py smoke test")
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    f = Fetcher(synthetic=args.synthetic, store_raw=False)
    bars = f.fetch_yahoo_chart(args.ticker, range_="3mo")
    print(f"{args.ticker}: {len(bars)} bars")
    if bars:
        print("  first:", bars[0])
        print("  last :", bars[-1])
    obs = f.fetch_fred_series("DGS10")
    print(f"DGS10: {len(obs)} observations; last={obs[-1] if obs else None}")
    print("stats:", json.dumps(f.stats_summary(), indent=2))
