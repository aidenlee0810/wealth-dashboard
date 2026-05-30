# ADR 004: Daily Snapshot Pipeline (Phase 3)

Status: Accepted (2026-05-29, Phase 3)

## Context

Phase 2 produced a 555-ticker dynamic universe but every table that depends on
*market data* (prices, features, regime, signals, outcomes) was still empty.
Phase 3 builds the engine that fills them daily and publishes static views — the
first phase that makes real network calls and runs unattended in GitHub Actions.

Constraints that shaped the design:

1. **Free-tier, datacenter-hostile data sources.** Yahoo's chart endpoint
   returns HTTP 429 from cloud IPs; Stooq now requires an API key; Finnhub's
   free tier has `/quote` but not historical `/stock/candle`. Whatever we build
   must degrade gracefully and be verifiable without reliable live data.
2. **GHA 60-min budget + cloud/local separation.** The job runs cloud-only
   (never touches personal data) and must finish well within the limit.
3. **Reproducibility + no-lookahead** are non-negotiable for later backtests.

## Decision

A modular, **stdlib-only** pipeline orchestrated by `jobs/daily_snapshot.py`:

```
precheck → universe → prices → macro → features (Stage 1)
        → reconcile → regime+themes → signals (Stage 2) → outcomes
        → metadata → views → alerts → git
```

| Module | Table(s) written | Notes |
|--------|------------------|-------|
| `fetch.py` | `raw_api_responses` | urllib + contracts + token-bucket + circuit breaker + **synthetic mode** |
| `market_calendar.py` | — | NYSE holidays 2024-28; trading-day arithmetic |
| `prices.py` | `prices_daily`, `corporate_actions` | Yahoo primary; adaptive 1y/1mo range; split capture |
| `reconciliation.py` | `reconciliation_log` | close: Yahoo vs Finnhub, 0.3%/2% bands (§25) |
| `feature_builder.py` | `features_daily`, `universe_membership.active_score` | technical pillar only; Stage-1 ranking |
| `regime_builder.py` | `market_regime_daily`, `sector_theme_daily` | 8-state classifier + theme lifecycle |
| `signal_generator.py` | `generic_signals`, `candidate_snapshots` | deterministic IDs, lineage_json stub (§26) |
| `outcome_updater.py` | `signal_outcomes` | 1/5/20/60/120D, MAE/MFE/MDD, no-lookahead |
| `views_builder.py` | `data/views/*.json` | 11 views (12th = alerts) |
| `alerts.py` | `data/views/latest_alerts.json` | 7 rules (§32) |

### Synthetic mode is a first-class feature, not a test hack

`Fetcher(synthetic=True)` generates a deterministic GBM price series from a
**fixed anchor date** (2019-01-01), so the price on any given date is identical
regardless of the requested window. This makes two synthetic "sources" agree,
which lets reconciliation and outcome math be tested meaningfully offline. The
entire pipeline runs end-to-end in ~0.7s with zero network — the whole Phase 3
test suite (27 cases) depends on it.

### Yahoo primary, with honest degradation

We keep Yahoo as the primary price source (it works intermittently from GHA
runners), add 429/5xx retry-with-backoff, and let the circuit breaker trip only
after the final retry. When a fetch fails the ticker is logged to `failed_jobs`
and the run continues. `result_status` is derived from the failure rate
(success<5% · degraded<30% · partial<50% · failed≥50%). Finnhub `/quote`
remains the second source for reconciliation. Historical backfill robustness is
a Phase 4 follow-up (FMP / paid tier evaluation).

### Ordering: metadata before views

Views read `snapshot_metadata` (for `result_status`, counts, API stats), so the
orchestrator writes metadata **before** building views. Alerts run last (they
read the finished DB + the run summary).

### Cloud-only — held/watchlist stay in Local Mode

The plan mentions signals for "held + candidates + watchlist", but held and
watchlist are *personal*. The cloud snapshot evaluates the discovered candidate
universe only (Stage-2 top-N by `active_score`); the personal overlay is applied
in Local Mode by the browser/server. No personal data can enter the cloud DB.

## Rationale

- **stdlib-only** keeps the GHA image light and the local server dependency-free,
  consistent with Phase 1/2.
- **Idempotent upserts** everywhere → safe re-runs (verified: state-table row
  counts identical across two runs; `snapshot_metadata` is the one intentional
  append — a per-execution audit log keyed by `run_id`).
- **Two scores, two columns**: `discovery_score` (source-diversity prior, Phase 2)
  stays put; `active_score` (price-aware Stage-1 rank, Phase 3) is added — exactly
  the schema's two-column intent.
- **Core needs days_active≥5**: on a fresh universe everything is Tactical, never
  Core — the persistence rule from ADR 003, now visible in candidate output.

## Consequences

- Pros: fully testable offline, graceful under flaky data, reproducible, cloud/
  local separation preserved, market-calendar aware (no wasted holiday runs).
- Cons: live historical backfill depends on Yahoo's tolerance from GHA IPs
  (mitigated: retry + circuit breaker + failed_jobs retry queue + synthetic
  fallback for dev); fundamental pillars still NULL until Phase 4, so the
  composite is an explicit technical-only proxy (`feature_version 3.0.0-tech`).

## Follow-ups

- Phase 4: fundamental pillars (bq/val/growth) per `model_type`; replace the
  technical-only composite; evaluate FMP for resilient fundamentals + history.
- Phase 5: the 15-gate Risk Governor plugs into the `risk_status` seam;
  full `lineage_json` + replay.
- Reconciliation coverage: extend beyond close price to volume + market cap.

## Reviewed
- Next review: after Phase 4 (feature store + model-type scoring).
