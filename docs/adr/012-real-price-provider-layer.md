# ADR 012: Real Price Provider Layer + Mixed-History Safety

Status: Accepted (2026-05-30)

## Context

Every price in `prices_daily` is currently `synthetic` (13,780 bars) because the
free price sources failed: Yahoo 429s from datacenter IPs, Stooq's free CSV is
now captcha/key-gated, and Finnhub candles 403 on the free plan. With synthetic
prices, valuation multiples, technical signals and backtests are all meaningless
— which is why Phase F gated valuation off on synthetic prices.

To make those real, we need a real historical-price provider. But the dangerous
trap (called out explicitly) is **partial realness**: bolting one live quote on
top of a synthetic history produces a *mixed* series that *looks* real and is
more deceptive than fully-synthetic data. So a real price provider is only safe
alongside a realism classifier that refuses to trust mixed history.

## Decision

### A. Multi-provider dispatch with per-provider failure transparency (jobs/fetch.py)

`fetch_price_history` became a dispatcher that tries real providers in priority
order, gated by key presence:

```
price_provider_order():  alpaca (if ALPACA_KEY+SECRET) → tiingo (if TIINGO_KEY) → yahoo (keyless)
```

Keyed providers go first because they work from datacenter IPs where Yahoo 429s.
It returns `attempts: [{provider, ok, reason}]` so the snapshot records *why each
provider failed* ("yahoo: HTTP 429", "alpaca: no key"). New fetchers
`_alpaca_bars` / `_tiingo_bars` are env-key-driven and contract-light;
`_http_json` gained an `extra_headers` arg for Alpaca auth. Yahoo's UA is already
a real browser string — the 429 is IP reputation, not UA, so the fix is a keyed
provider, not a header tweak.

### B. Price-history realism classifier (jobs/price_quality.py)

`classify_history(conn, ticker)` classifies a ticker's WHOLE history by source
mix:

| class | rule | meaning |
|-------|------|---------|
| none | 0 bars | nothing to score |
| synthetic | 100% synthetic | known offline mode |
| **mixed** | <98% real | **deceptive — do NOT trust** |
| real | ≥98% real | trustworthy |

The 98% floor tolerates a stray legacy bar but flags any meaningful synthetic
contamination as `mixed`.

### C. Gating is on the FULL history, not the last bar (jobs/feature_builder.py)

Phase F gated valuation on the *last bar's* source. That's insufficient — a real
last bar on synthetic history is exactly the mixed trap. Gating now uses
`classify_history`:

- **valuation** is N/A unless classification is `real` (mixed *and* synthetic
  both block it).
- **mixed** additionally caps `technical_dq` at 40 — mixed technicals look real
  but aren't, so they're distrusted. Pure `synthetic` is the known offline mode
  and is left to the snapshot data-realism gate, not penalised per row.

`val_score` therefore populates only when a ticker's price history is fully real
— the structural guarantee that "valuation is on only after real history."

### D. Snapshot-health transparency (migration 007 + views_builder)

`snapshot_metadata.price_provider_status_json` (migration 007) stores the
per-provider failure map. `latest_snapshot_health.data_realism` now carries:
`price_sources`, `all_prices_synthetic`, `price_quality` (real/mixed/synthetic/
none counts), `valuation_populated_count`, and `price_provider_failures`.

### E. Refresh discipline

When real prices arrive for a ticker they upsert over the synthetic bars on the
same dates (PK date,ticker), so a fully-refreshed ticker becomes `real`; any
residual synthetic dates outside the real window keep it `mixed` until refreshed
— and `mixed` is honestly distrusted, never silently blended.

## Rationale

- **Mixed > synthetic in danger**: the classifier exists precisely because a
  half-real series is the worst case. Strict 98% floor + valuation-only-on-real
  makes the safe path the default.
- **Key-gated, datacenter-ready**: Alpaca/Tiingo work where Yahoo 429s, so the
  GHA pipeline has a real path once a key is set.
- **Honest until real**: with today's all-synthetic DB, `valuation_populated_count`
  is 0 and `all_prices_synthetic` is true — the system states plainly that it has
  no real prices yet, rather than faking them.

## Consequences

- Pros: real provider path (Alpaca/Tiingo/Yahoo) with per-provider failure
  visibility; a strict mixed-history classifier; valuation/technical gated on
  full-history realism; 11 new tests proving real→valuation-on / mixed→off /
  synthetic→off and the health flip.
- Cons: Alpaca/Tiingo fetchers are untested against the live API (no keys in
  CI) — they're structurally correct and unit-tested for the no-key path. Real
  prices won't flow until a key is configured or Yahoo is reachable from a
  non-throttled IP. Until then valuation stays off (correct).
- Boundary: backend only (fetch/prices/feature_builder/price_quality/views) +
  migration 007. No UI change; the existing fundamentals tab already reads the
  gated `val_score`.

## Follow-ups
- Configure a real provider key (Alpaca free tier recommended) in GHA secrets;
  re-run the snapshot; confirm `all_prices_synthetic=false` and
  `valuation_populated_count>0` on live data.
- A `refresh_prices` job that fully replaces synthetic history per ticker
  (delete-then-insert) to avoid lingering `mixed` states after first real fetch.

## Reviewed
- Next review: after the first real-price snapshot.
