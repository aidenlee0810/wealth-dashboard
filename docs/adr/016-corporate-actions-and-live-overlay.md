# ADR 016: Corporate Actions (Alpaca) + Live-Price Overlay on Candidate/Optimizer

Status: Accepted (2026-05-30)

## Context

Two gaps, both now closable because Alpaca credentials are available server-side.

1. **Corporate actions (Plan §33).** `jobs/prices.py` records *splits* off Yahoo's
   events feed, but **dividends were never captured** — so dividend yield,
   total-return awareness, and REIT dividend-safety had no data source. The
   `corporate_actions` table (migration 001) already has a `dividend` column and a
   `(ticker, ex_date, action_type)` PK; it just had no dividend feed.
2. **Stale prices in the decision tabs.** The 신규편입 후보 (candidate) and
   매수 최적화 (optimizer) tabs priced everything off `TIMING.getStateForTicker`,
   whose `price` is the last cached candle close. The optimizer divides a dollar
   budget by that price to compute share counts, so a stale price means a wrong
   share count. ADR 015 added a batched live-quote layer (`LIVE_PRICE`); these
   tabs were not yet using it.

## Decision

### A. `jobs/corporate_actions.py` — Alpaca `/v1/corporate-actions` (splits + dividends)

stdlib module, same shape as `jobs/live_quotes.py`:

- `parse_corporate_actions(payload)` is **pure**: forward/reverse splits →
  `split_ratio = new_rate/old_rate` (10:1 → 10.0, 1:10 → 0.1) with a `"old:new"`
  note; cash dividends → per-share `dividend` with a `"special"` note. Bad rows
  (missing symbol/rate, zero old_rate) are dropped; never raises.
- `fetch_corporate_actions(...)` chunks symbols (50/req), follows
  `next_page_token` (capped), and skips a failed chunk rather than aborting.
- `sync(conn, rows)` is an idempotent `INSERT OR IGNORE` on the PK — re-runs add
  only genuinely new rows (split + dividend on the same ex_date coexist because
  `action_type` differs).
- `sync_universe(conn, symbols, market_date)` **gracefully skips** with
  `{"skipped": "no_alpaca_key"}` when unconfigured.
- `trailing_dividend(conn, ticker, asof)` sums the trailing-12mo cash dividend per
  share → feeds dividend yield.

Wired into `jobs/daily_snapshot.py` as **step 8.5** over the Stage-2 candidate set
+ benchmarks. No new migration (the schema already supported it). No double-adjust
risk: this records the corporate-actions *ledger*; price split-adjustment stays in
`prices.py` (providers already return split-adjusted closes).

### B. Live-price overlay on candidate + optimizer (reusing `LIVE_PRICE`)

- **optimizer.js**: after the per-ticker timing loop, one
  `LIVE_PRICE.snapshot(validTickers)` batch overrides `results[t].price` (and
  carries `liveChangePct`) so allocated-share math uses the *true* current price.
  The card shows today's % change next to `N주 × $price`.
- **candidate.js**: after the scan builds `candidates[]`, one batch overlay sets
  each card's `price` + `liveChangePct`; the card shows a colored day-change under
  현재가. The overlaid price is what gets cached.

Both are wrapped in `typeof LIVE_PRICE !== 'undefined'` + try/catch, so with no
server/keys they silently fall back to the existing timing price — zero regression.

## Consequences

- **Pros**: dividend data now exists (yield, total return, REIT safety inputs);
  optimizer share counts are correct at the live price; candidate/optimizer cards
  show today's move. Pure parsers are unit-tested (`tests/test_corporate_actions.py`,
  15 cases, no network).
- **Cons**: corporate-actions sync only runs in the GHA snapshot or local job when
  ALPACA keys are present (graceful skip otherwise); the live overlay needs Local
  Mode + keys (static site keeps the EOD price).
- **Deliberately not done**: automatic historical `adj_close` recomputation from a
  detected split (providers already deliver split-adjusted closes; re-adjusting
  would double-count). Ticker-change canonicalization and delisting auto-handling
  (the rest of §33) remain future work.

## Reviewed
- Next review: when dividend yield is surfaced in the fundamentals tab / candidate
  scoring, or when ticker_changes canonicalization lands.
