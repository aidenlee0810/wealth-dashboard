# ADR 013: Portfolio Stress Test — Factor Model & Scenario Design

**Status:** Accepted (2026-05-30)

## Context

Phase 9 adds a portfolio stress test (Plan §9, §30): given a set of holdings,
estimate total portfolio loss under historical or hypothetical adverse scenarios.

Three core design questions arose:

1. **Factor model depth** — single-factor (market beta only) vs. full
   Fama-French 5-factor regression?
2. **Factor data source** — require a separate FF5 data feed vs. derive
   proxies from data already in the DB?
3. **Vol/R² storage** — where to persist annualised volatility computed during
   OLS so the stress view doesn't need to recompute it?

## Decision

### 1. Single-factor OLS (market beta), with sector/cap proxies for SMB/HML

We regress each ticker's daily returns against SPY using
`statistics.linear_regression` (Python 3.10+, stdlib-only, no numpy/scipy).
This gives `b_mkt`, `alpha`, `r_squared`, and `vol_annual`.

SMB and HML are assigned via lookup tables (sector → HML, market-cap-bucket →
SMB) rather than additional regressions. RMW and CMA are stored as NULL pending
data accumulation.

**Rationale:**
* Market beta accounts for 70–80% of stock-level variance in stress scenarios.
* Full FF5 regression requires a parallel FF5 factor time series, which would
  need a new API source and complicates the stdlib-only constraint.
* Characteristic-based SMB/HML is a standard "fundamental factor model"
  technique used in production (e.g., Barra) and is transparent and auditable.
* The `r_squared` column surfaces model quality: R² < 0.30 → "low_r2" method
  flag in the stress view, warning users not to over-trust beta estimates.

### 2. Weekly anchoring of factor exposures

`week_end_date_for(as_of)` pins every daily run within the same ISO week to the
same Friday. Re-running the snapshot Tuesday–Friday produces an idempotent
upsert to `factor_exposures_weekly`.

**Rationale:**
* Factor exposures from a 2-year OLS do not meaningfully change day-to-day.
* Weekly anchoring avoids storing 5× redundant rows per ticker per week.
* Idempotency: safe to re-run the snapshot without duplicating data.

### 3. `vol_annual` stored in `factor_exposures_weekly` (migration 008)

The OLS regression already computes annualised vol as a by-product. We add a
`vol_annual` column (migration `008_cloud_factor_vol.sql`) rather than querying
`features_daily` (which has no `volatility` column as of this phase).

**Alternatives considered:**
* Add `volatility` to `features_daily` → larger table, duplicates data.
* Recompute from prices_daily on every stress view build → slow for 100+ tickers.

### 4. Two-layer stress view

The `build_stress_view` output separates:
* **`scenarios`** — metadata (label, description, actual or fallback SPY return).
* **`ticker_stress`** — per-ticker beta, R², vol, and per-scenario estimated return.

The frontend (`portfolio-stress.js`) combines portfolio weights with
`ticker_stress` client-side, so the JSON view itself is portfolio-agnostic and
works in both Static Mode (equal-weight candidates) and Local Mode (actual
holdings from personal.sqlite).

### 5. Actual-return fallback logic

For historical scenarios where the ticker existed, we query `prices_daily` for
the actual return over that period. If it's missing (new ticker, pre-IPO),
we fall back to `beta × spy_shock`.

**Priority:** actual > beta > spy_proxy (no beta) > 0.

## Consequences

**Pros:**
* No new API dependency — all factor data comes from prices_daily (SPY already
  tracked as a benchmark).
* Transparent: every estimated return is labelled with its method ("actual",
  "beta", "low_r2", "spy_proxy").
* Idempotent + incremental: only one DB row per (ticker, week_end_date), fast.
* Works in Static Mode: `latest_stress_test.json` is pre-computed.

**Cons / Limitations:**
* R² < 0.3 tickers (niche sectors, low liquidity) have low-confidence
  estimates; the UI warns users with the "low_r2" badge.
* SMB/HML proxies are coarse (sector lookup, no actual size/value regression).
  Full multi-factor model is Phase 11+.
* Historical scenarios older than prices_daily history (2000–2010 for newer
  tickers) must rely on beta proxy.

## Next Review

After 6 months of snapshot accumulation (≥ 126 trading weeks), assess:
* Whether SPY factor R² distribution justifies adding RMW (quality factor).
* Whether adding IWM and IVE/IVW as tracked benchmarks enables real SMB/HML
  regression (no new API source required, just additional rows in prices_daily).

---
*Created: 2026-05-30*
