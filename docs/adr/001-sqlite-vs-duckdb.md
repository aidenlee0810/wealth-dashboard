# ADR 001: SQLite vs DuckDB for Phase 1

**Status:** Accepted  
**Date:** 2026-05-28  
**Decider:** User + AI assistant  
**Phase:** 1 (DB Foundation)

---

## Context

The platform needs a persistent database for daily snapshots of market data, signals, candidate scores, and backtest outcomes. Target volume: ~5–20 MB per snapshot, ~1M rows/year accumulated.

Options considered:
- **SQLite** — Python stdlib, single-file, ACID, no install required
- **DuckDB** — analytical (columnar), Parquet-native, fast aggregations, pip install required
- **PostgreSQL** — full server, multi-user, requires daemon

## Decision

**SQLite for Phase 1.**

## Rationale

| Factor | SQLite | DuckDB | PostgreSQL |
|--------|--------|--------|------------|
| Install effort | Zero (stdlib) | `pip install duckdb` | Full server setup |
| Single-file portability | ✅ | ✅ (with caveats) | ❌ |
| Git-committable | ✅ (small enough) | ✅ | ❌ |
| Concurrency | OK for low-volume snapshots | Limited concurrent writes | Best |
| Backtest aggregations | OK | Faster | Best |
| GitHub Actions setup | None | pip install | Service container |
| Migration complexity later | Low (DuckDB can attach SQLite) | — | Significant |

For Phase 1's needs (single-user, daily writes, ad-hoc reads), SQLite is the simplest path. Migration to DuckDB or PostgreSQL is a defined fallback if:
- DB size >500MB (then DuckDB Parquet)
- Concurrent writes needed (then PostgreSQL)
- Backtest aggregations >5 seconds (then DuckDB read-only)

## Consequences

**Pros:**
- Zero external dependencies (`import sqlite3` is built-in)
- DB ships with the repo (`data/db/research_market.sqlite`)
- Same DB file works locally and in GitHub Actions
- Pre-existing Python knowledge required

**Cons:**
- Single-writer at a time (only one process can write); mitigated via `concurrency` in GHA + UI doesn't write to cloud DB
- Slower aggregation queries (acceptable for our volume)
- Binary git commits (mitigated by monthly VACUUM + archive policy in Phase 3+)

**Migration path:**
- Phase 1–2: SQLite committed to git
- Phase 3+: Evaluate DB size + query latency; if either exceeds threshold:
  - Option A: SQLite → GHA artifact (90-day retention)
  - Option B: SQLite + DuckDB layer (DuckDB attaches SQLite read-only for fast aggregations)
  - Option C: SQLite + Parquet snapshots (DuckDB queries Parquet)

## Reviewed

- Next review: after Phase 5 completion (Risk Governor) — re-evaluate DB size growth
- Trigger early review: if any backtest takes >10 seconds
