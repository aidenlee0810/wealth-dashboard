# ADR 014: Theme Discovery UI & Model Registry Design

**Status:** Accepted (2026-05-30)

## Context

Phase 10 closes two remaining gaps identified in Plan §10 and §31:

1. **Theme lifecycle data already exists but has no UI.** `regime_builder.py`
   computes a 5-phase lifecycle (emerging / leading / neutral / fading /
   breakdown) and stores it in `sector_theme_daily` + `latest_sector_theme.json`
   every snapshot. `market_regime_daily` records which themes the current regime
   favours or avoids.  Neither `sectors.js` nor any other frontend file
   consumed this data before Phase 10.

2. **No model versioning.** Every snapshot uses the same scoring logic with no
   record of when it was introduced, what it replaced, or how a shadow-mode
   candidate performed before promotion.

---

## Decision

### 1. `research/theme-discovery.js` — pure view consumer

The module reads two pre-built JSON views:
- `data/views/latest_sector_theme.json` (themes + scores, built every snapshot)
- `data/views/latest_market_regime.json` (favored / avoided lists)

It renders three sections appended to the **sectors tab** (below the existing
Economic Cycle Guide):

| Section | Purpose |
|---------|---------|
| **Regime overlay strip** | Colour-coded pills — ✓ favoured, ✗ avoided, ↑ emerging, ↓ fading — from `market_regime_daily` |
| **Phase lifecycle board** | Kanban-style columns by phase; cards show name, leadership score, 1M/3M returns, relative strength |
| **Leadership ranking table** | All 15 themes sorted by `leadership_score`, with phase badge, return cols, score bar |

**Rationale:**
- No new backend work: all data already exists in the view files.
- Works in Static Mode (GitHub Pages) — no server required.
- Appended to the sectors tab (not a new tab) to keep the tab count stable.
- `THEME_DISCOVERY.init()` is called from `SECTORS._render()` only if the
  module is loaded (`typeof THEME_DISCOVERY !== 'undefined'`), preserving
  graceful degradation if the script fails to load.

**Design details:**
- Phase order displayed: Emerging → Leading → (Extended) → Neutral → Fading → Breakdown.
  "Extended" is reserved for future use; the current backend emits at most
  the five phases above.
- Regime overlay adds ring highlights on phase-board cards and ✓/✗ badges in
  the ranking table — these are purely informational.
- `getThemesByPhase(phase)` and `getRegimeThemeSets()` are exported for
  optional consumption by other modules (e.g., `macro.js` could show "today's
  leading themes" without duplicating data loading).

### 2. `jobs/model_registry.py` + `migrations/009_cloud_model_registry.sql`

A lightweight experiment registry (Plan §31) backed by a single
`model_registry` table.

**Status lifecycle:**
```
experimental → shadow → production → deprecated
```

**Seeded at migration time:** four production models reflecting the actual Phase 4–9
implementations (candidate scorer, regime classifier, risk governor, factor
model).  The seed uses `INSERT OR IGNORE` so re-running migrations is safe.

**Shadow mode** fields (`shadow_period_start`, `shadow_period_end`,
`shadow_signal_count`, `shadow_hit_rate`, `shadow_ev`,
`shadow_vs_production_correlation`, `graduation_decision`) are populated by
`update_shadow_stats()` after a shadow period ends.  In Phase 10 no model is
currently in shadow; these columns are NULL and that is expected.

**`promote(conn, family, new_model_id)`** atomically:
1. Marks the current production model of that family as `deprecated`.
2. Marks `new_model_id` as `production`, setting `promoted_at`.

**`build_registry_view(conn)`** returns a snapshot dict suitable for
`latest_model_registry.json`.  `config_json` blobs are stripped; the parsed
`config` dict is included instead.

### 3. `/api/db/query` allowlist (already present in `db.py`)

`model_registry` was pre-added to `ALLOWED_TABLES` in the existing allowlist
with the expected column set.  No changes needed.

---

## Consequences

**Pros:**
* Theme lifecycle is now visible in the UI with zero new backend work.
* Model decisions are recorded and queryable — future shadow-mode evaluation
  (Phase 11+) has a home.
* All four production models (scorer, regime, governor, factor) are documented
  with their config snapshots in the DB itself.
* `latest_model_registry.json` can be consumed by a future A/B testing UI.

**Cons / Limitations:**
* Phase lifecycle classifications are rule-based (threshold on `trend_score`,
  `breadth_score`, `rel_strength`).  More nuanced ML classification is
  deferred to Phase 11+.
* Shadow mode UI (the A/B promotion workflow shown in Plan §31) is not yet
  implemented — only the data layer exists.
* Model registry is currently write-only from Python (`model_registry.py`);
  there is no REST endpoint for promotion.  A future `POST /api/model/promote`
  endpoint with explicit admin auth is the Phase 11+ path.

## Next Review

After the first full shadow period concludes (30+ days):
- Assess whether `candidate_scorer_v5.x` shadow performance warrants
  promotion.
- Add `research/model-registry.js` widget to the validation tab showing the
  A/B comparison card described in Plan §31.

---
*Created: 2026-05-30*
