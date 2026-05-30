# ADR 005: Model-Type Scoring + Specialist-Analyst Architecture (Phase 4)

Status: Accepted (2026-05-29, Phase 4)

## Context

Phase 3 scored candidates on the technical pillar only (`feature_version
3.0.0-tech`); fundamentals were NULL and the composite was a technical proxy.
The browser still scored client-side off a hand-assigned `getFundamentalTier()`
(the legacy TIER system the plan wants gone).

Two problems to solve in Phase 4:
1. **One yardstick doesn't fit all businesses (§6).** ROIC is meaningless for a
   bank; FCF margin is meaningless for a pre-revenue biotech; an ETF has no
   business quality at all. Scoring everything as an operating company is wrong.
2. **A monolithic scorer is hard to reason about.** The user's framing — "have
   specialists each analyse their domain, then a synthesis step combine them" —
   is also just good software architecture (separation of concerns) and the
   natural seam for future multi-agent or ML pillars.

## Decision

### A. A desk of specialist analysts + a synthesis engine (`jobs/scorers/`)

| Module | Pillar(s) | Notes |
|--------|-----------|-------|
| `fundamental.py` | bq / valuation / growth | **model-type-specific** (§6) |
| `technical.py` | technical | trend/momentum/setup (owns the Phase 3 math) |
| `sector_theme.py` | sector_theme | leadership fit from `sector_theme_daily` |
| `macro_fit.py` | macro / regime_fit | favored/avoided theme & sector fit |
| `risk.py` | risk | DQ + deterministic flags (pre-governor) |
| `synthesis.py` | composite | regime-weighted blend → candidate_type |

Every analyst returns a uniform **`PillarScore`** `{score, coverage_ratio,
na_reason, warning, reasons, detail}`. This is the crux: it lets the synthesis
engine treat "doesn't apply" (ETF BQ) differently from "missing data"
(operating company, low coverage) — N/A pillars are **dropped and weights
renormalise**, never scored as zero.

### B. model_type classification (`scorers/model_types.py`)

operating_company / bank / insurance / reit / biotech_pre_revenue / etf /
leveraged_etf. Resolution order: ETF/leverage flags → known non-bank financials
→ curated seed (with **bank-seed corroboration**) → known single-names →
industry keywords → sector+revenue heuristics → operating_company.

The bank-seed corroboration matters: `sector_holdings.json` seeds the *entire*
XLF sector as `bank`, which would mislabel Visa/Mastercard/BlackRock. A `bank`
seed is only honored when corroborated by `KNOWN_BANKS` or a bank industry name;
otherwise the ticker falls through to refinement (→ operating_company).

### C. 4-layer fundamental pipeline (`jobs/financials.py`, §24)

```
Layer 0 raw_api_responses → Layer 1 cleaned_financials (typed line items)
        → Layer 2 normalized_financials (ratios + sector z-scores + coverage)
        → Layer 3 features_daily (pillar scores)
```
Bank/REIT route to `bank_fundamentals_q` / `reit_fundamentals_q` (already
ratio-form); biotech runway/dilution is derived on read from cleaned line items
(migration 004 added `interest_expense`, `ebitda`, `revenue_prior`,
`quarterly_burn`, `shares_out_prior`). Every row is point-in-time
(`usable_at <= decision_date`) so backtests can't peek at a report before it
was filed — verified by a test reading "before report → None".

### D. Two-stage scoring in the snapshot

Stage 1 ranks the broad universe on the technical pillar (`active_score`).
Stage 2 takes the top-N, fetches fundamentals, recomputes regime + themes, and
**re-scores with all seven pillars** → the authoritative composite + candidate
classification. This honors the API budget (§5): fundamentals are fetched only
for the deep set.

### E. Coverage-aware DQ + UI surfacing (§6)

`bq_coverage_ratio` flows to `features_daily` + `candidate_snapshots`. Missing
fundamentals where they *should* exist dock DQ (operating <30% → −20, <60% →
−8); an ETF's absent BQ does **not** dock DQ. `coverage_warning` is surfaced in
the candidate views so the browser can show "⚠️ 펀더멘털 커버리지 낮음".

## Rationale

- **Correctness**: a bank is judged on ROE/efficiency/capital, a REIT on
  FFO/occupancy/dividend-safety, a biotech on cash runway — never on the wrong
  metric. ETFs are honestly N/A, not fake-zero.
- **Traceability**: composite → pillar → Layer 2 z-score → Layer 1 line item →
  Layer 0 blob, no API re-call.
- **Extensibility**: each analyst is independently testable and swappable; the
  PillarScore contract is exactly where an ML pillar or a literal sub-agent
  would plug in later.
- **Backward compatible**: `scorers/technical.py` owns the Phase 3 math and
  `feature_builder` re-exports it, so the Phase 3 test-suite stays green.

## Consequences

- Pros: model-type-correct scoring, honest N/A handling, point-in-time
  fundamentals, clean specialist seam, regime-aware weighting.
- Cons: real fundamentals depend on Finnhub `/stock/metric` (free tier, ratio-
  only) — full line-item history (FMP/SEC) is a follow-up; synthetic mode is the
  tested workhorse offline. Insurance currently reuses operating metrics
  (combined-ratio path is Phase 5). Bank/REIT growth pillars are neutral until a
  multi-quarter history accrues.
- Boundary: this work is **backend-only** (`jobs/`). The browser frontend
  (candidate.js / fundamentals.js) is owned by parallel work and will switch
  from `getFundamentalTier()` to consuming `candidate_snapshots` (pillars +
  coverage) — the views now carry everything it needs.

## Follow-ups
- Phase 5: 15-gate Risk Governor consuming `risk.py` flags; full lineage/replay.
- Frontend: candidate.js → read pillar breakdown + coverage_warning from views.
- Fundamentals: FMP/SEC line-item history; insurance combined-ratio; bank/REIT growth.

## Reviewed
- Next review: after Phase 5 (Risk Governor).
