# ADR 015: Alpaca Live-Quote Layer (intraday snapshot via server proxy)

Status: Accepted (2026-05-30)

## Context

The whole pipeline is end-of-day: `prices_daily` and every view are written once
per day by the snapshot job. The dashboard the user actually looks at therefore
showed *yesterday's* close, and the only intraday price path was
`research/api.js` calling Finnhub `/quote` **directly from the browser**, once
per ticker. That has three problems:

1. **Key exposure** — browser-side Finnhub calls require `RESEARCH_CONFIG.FINNHUB_KEY`
   to be present in the page. The deployment direction is the opposite: keys move
   to GitHub Secrets / server env and must never ship to a browser.
2. **N calls for N holdings** — a per-ticker quote loop is slow and rate-limited.
3. **No "today's % change"** — a bare quote has no clean previous-close reference.

We now have Alpaca credentials available server-side. Alpaca's multi-symbol
**snapshot** endpoint returns latest trade + today's forming daily bar +
previous daily bar for a whole watchlist in ONE request — exactly what a live
portfolio strip needs.

## Decision

Add an intraday quote layer that keeps keys server-side and batches the fetch.

### A. `jobs/live_quotes.py` (stdlib only)

Pure parser + thin network wrappers:

- `parse_snapshot(payload) -> {TICKER: quote}` and `parse_one(symbol, snap)` are
  **pure functions** (fully unit-tested, no network). Price preference is
  `latestTrade.p > minuteBar.c > dailyBar.c`; today's change is measured against
  `prevDailyBar.c` (falls back to `dailyBar.o` for fresh IPOs); divide-by-zero on
  a 0 previous close is guarded.
- `fetch_snapshots(symbols, key, secret, feed="iex")` and `fetch_clock(...)` do
  the HTTP. Free Alpaca accounts get the **IEX** feed (default); SIP is opt-in.
  Both short-circuit to `{}` when keys or symbols are missing — no network call.

### B. `server.py` proxy — `/api/price/snapshot`, `/api/price/clock`

- Credentials resolve from **env first** (`ALPACA_KEY`/`ALPACA_SECRET`, or
  Alpaca's native `APCA_API_KEY_ID`/`APCA_API_SECRET_KEY`), then a gitignored
  `~/.wealth-dashboard/secrets.env` (KEY=VALUE) so the user configures once and
  every local shell picks it up. The repo never holds the key.
- A 15 s in-memory cache (30 s for the clock) de-bounces repeated calls; the
  cache self-prunes past 256 entries.
- **Graceful degradation is a contract**: when no keys are configured the proxy
  returns **HTTP 503 `{"error":"no_live_price_provider"}`** instead of failing,
  so the browser can tell "server up but unconfigured" apart from "no server".
- Public CORS — the response is read-only market data; the *secret* never leaves
  the server, only quotes do.

### C. `research/live-price.js` — `LIVE_PRICE`

Browser consumer with its own 15 s cache + in-flight de-dupe:

- `snapshot(tickers)` normalizes to camelCase and tracks a `state()` of
  `live | no_keys | offline | unknown` from the proxy's response (200 / 503 / fetch-fail).
- `mountStrip(containerId, holdings)` renders a self-contained, auto-refreshing
  widget: market-status badge (🟢 장중 / 🔴 마감 from `/api/price/clock`), live
  price + today's % change per holding, and a live portfolio market value +
  today's $/%. Refresh is 25 s while open, 120 s while closed, and **pauses when
  the tab is hidden or the container leaves the DOM**.
- It owns all its DOM, so it cannot break the existing portfolio render. When the
  provider is unavailable it shows a one-line "set ALPACA_KEY" hint in Local Mode
  and **renders nothing** on the static site.

### D. Wiring — portfolio tab

`research/portfolio.js` now fills its price map from **one** `LIVE_PRICE.snapshot`
batch and only falls back to the per-ticker Finnhub loop for tickers the batch
didn't cover. The live strip mounts at the top of the portfolio panel.

## Consequences

- **Pros**: real intraday prices + accurate today's change for the whole
  portfolio in one request; API key stays server-side (aligns with the public
  deploy); per-ticker Finnhub fan-out is now just a fallback; pure parsers are
  trivially testable (`tests/test_live_quotes.py`, 24 cases, no network).
- **Cons**: live quotes need Local Mode + Alpaca keys; the static GitHub Pages
  site has no server, so the strip stays hidden there (acceptable — the public
  site is the EOD research view by design).
- **Feed caveat**: IEX free feed is a subset of consolidated volume; prices track
  closely but volume is partial. SIP can be enabled per-request later.
- **Not built (deliberately)**: no trading/account endpoints, no order entry —
  this is decision-support only, consistent with the project's no-auto-trading rule.

## Reviewed
- Next review: if/when a second live provider (e.g. Tiingo IEX) is added behind
  the same `/api/price/snapshot` contract.
