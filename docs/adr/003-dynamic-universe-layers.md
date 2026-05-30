# ADR 003: Dynamic Universe — 6-Layer Merge Architecture

Status: Accepted (2026-05-28, Phase 2)

## Context

Phase 1 inherited a fixed 67-ticker universe hard-coded in
`research/candidate-universe.js`, organized by theme with hand-assigned
`TIER_1/2/3` fundamental priors. Problems:

1. **Static** — never grows; misses new leaders (IPOs, momentum names, index adds).
2. **Hard-coded tiers** — `TIER_1` is a guess, not data. No way to improve as data accrues.
3. **No provenance** — can't answer "why is this ticker a candidate?" or
   "which index/theme/sector does it belong to?"
4. **No persistence** — no concept of how long a ticker has been a candidate
   (needed to separate durable Core names from one-day momentum spikes).

The Plan (§4, §5) calls for a Dynamic Universe Builder with 6 layers, persistence
rules, and a two-stage scan.

## Decision

Build the universe by **merging 6 source layers** into a deduplicated set, with
each ticker carrying its full set of `source_buckets`:

| Layer | Source | File | Cadence |
|-------|--------|------|---------|
| A. Core Watchlist | user-curated | `data/core_watchlist.json` + localStorage | manual |
| B. Index membership | S&P 500, Nasdaq-100 | `data/sp500.json`, `data/nasdaq100.json` | monthly (scrape) |
| C. Sector holdings | 11 SPDR top-10 | `data/sector_holdings.json` | quarterly |
| D. Theme baskets | 15 themes | `data/theme_baskets.json` | quarterly |
| E. Momentum discovery | dynamic (price/RVOL) | — | Phase 3 (needs prices) |
| F. Event/news | RSS, 13F, insider | — | Phase 3 |

Two parallel implementations, kept in lock-step:
- `jobs/universe_builder.py` — authoritative, writes `ticker_master` +
  `universe_membership` (cloud DB), runs in GitHub Actions.
- `research/universe-builder.js` — browser, merges the same static JSON for
  Local AND Static mode. Verified to produce identical output (555 tickers,
  identical `by_source` counts).

### Index data is scraped, not hand-maintained

`jobs/scrape_indices.py` pulls real S&P 500 / Nasdaq-100 membership from
Wikipedia (stdlib `html.parser`, no pandas/bs4). Validated by
`jobs/contracts/wikipedia_index.py`. A safety guard refuses to overwrite a good
file with a near-empty scrape (network failure protection).

### Persistence rules (noise suppression)

```
new ticker            → first_discovered_at=today, days_active=1
seen again (<=3d gap) → days_active += 1
absent >3 days        → active=0, inactive_reason='N-day absent'
re-discovered         → days_active resets to 1
```

Core promotion requires `days_active >= 5` — a one-day momentum spike can become
Tactical but never Core. In the browser, `getCoreEligible()` decides Local vs
Static **globally** (presence of any `days_active` data), not per-ticker, so the
500-row query cap is never mistaken for "no data".

### discovery_score (Stage 1 ranking)

Phase 2 proxy = source diversity + curation signals:
```
min(sources, 5) * 12  +  (core ? 20)  +  (theme_leader ? 12)  +  (nasdaq100 ? 6)
```
Phase 3 will blend in momentum / RVOL / 52-week-high proximity once price data
exists. The browser scans the **top 60 by discovery_score** (Stage 2 deep scan,
rate-limit safe); the server-side job scans the full universe.

## Rationale

- **Provenance**: every ticker records exactly which layers it came from
  (`source_buckets_json`), surfaced as chips in the candidate UI.
- **Real data**: 555 distinct tickers (vs 67), index membership scraped live.
- **Data over guesses**: `quality_tier` in `core_watchlist.json` is now an
  explicit SEED prior, documented to be replaced by real fundamental scoring in
  Phase 4 (model-type-specific).
- **Two impls, one truth**: keeping JS + Python in sync (verified by tests +
  in-browser eval) means Static-mode users get the same universe as Local.
- **Survivorship-aware**: `universe_membership` is point-in-time. Historical
  membership is preserved (active=0 rather than delete), which Phase 7 backtests
  need to avoid survivorship bias.

## Consequences

- Pros: scalable, provenance-rich, persistence-aware, real index data.
- Cons: two implementations to keep in sync (mitigated by shared JSON + tests);
  Wikipedia scrape is a soft dependency (mitigated by safety guard + commit).
- Migration: `candidate-universe.js` is retained for backward-compatible theme
  labels; `candidate.js` now prefers `UNIVERSE` and falls back to it.

## Follow-ups

- Phase 3: Layers E/F (momentum + event), blend price data into discovery_score.
- Phase 4: replace `quality_tier` seed priors with model-type fundamental scores.
- Reconciliation (§25) for scraped sector/index membership vs a second source.

## Reviewed
- Next review: after Phase 3 (daily snapshot wires Layers E/F).
