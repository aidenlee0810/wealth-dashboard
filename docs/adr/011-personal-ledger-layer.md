# ADR 011: Personal Portfolio Layer + 3-Ledger Comparison (Phase 8)

Status: Accepted (2026-05-29, Phase 8)

## Context

Through Phase 7 the system measured the *signals* (validation cuts, governor
ledger, backtests), all from cloud data. Plan §8 demands a third, personal
dimension: what the user *actually did*, compared against what the system
recommended (System Pure) and what the Risk Governor allowed (Risk Policy). The
hard constraint (ADR 002) is absolute: personal portfolio data lives only in
`~/.wealth-dashboard/personal.sqlite` and may never enter the cloud DB or a
committed view.

Phase 1.1 had already built most of the *infrastructure* — `db.local()`, the 7
personal tables (migration 002), and a CSRF-guarded `/api/local/*` CRUD handler.
What was missing is the analytical heart: the engine that **composes** cloud +
local to produce the 3-ledger comparison.

## Decision

### A. The composing engine lives at the repo ROOT, never in jobs/

`tests/test_data_separation.py` Test 4 forbids any `jobs/*.py` from referencing
the personal DB — the snapshot pipeline must be runnable in the cloud (GitHub
Actions) with zero personal data. But the 3-ledger comparison *requires* both
DBs. The resolution: `ledger_engine.py` sits at the repo root (alongside
`db.py`, `server.py`), which only the Local-Mode server imports. This keeps the
separation structural: cloud jobs can't touch personal data; only the local
server composes the two.

### B. Compare on **return-on-deployed-capital**

The three ledgers deploy different amounts of capital (Pure ignores the
governor; Policy zeroes blocked names; Actual reflects what the user bought). To
compare them fairly, each ledger's headline is `Σ(wᵢ·retᵢ) / Σwᵢ` — the
weighted-average return of the capital it actually deployed. This normalizes the
capital differences so the deltas are meaningful:

```
governor_cost  = pure_return  − policy_return    (> 0 ⇒ the governor gave up return)
behavior_cost  = policy_return − actual_return   (> 0 ⇒ the user's behavior lost return)
total_user_gap = pure_return  − actual_return
```

Note `governor_cost` is frequently **negative** — the governor often *adds*
return by avoiding losers, even while "missing" some upside. The avoided-vs-
missed tradeoff (Phase 6's governor ledger) and this cross-ledger return both
tell that story.

### C. Action-type breakdown surfaces behavior cost

Every `user_action` (BUY / PARTIAL / IGNORE / REJECT / WATCHLIST_ONLY) is joined
to its cloud signal's realized outcome. The breakdown shows, per action type,
the average return of the signals the user treated that way — so an `IGNORE`
bucket averaging +15% is the explicit, quantified cost of having skipped those
signals (the §8 "놓친 upside").

### D. Surfaced Local-Mode-only, never persisted

The comparison is computed on demand via `GET /api/local/ledger?horizon=`
(behind the existing CSRF guard) and rendered in the validation tab's 3-ledger
section. It is **never** written to a `data/views/*.json` — the result contains
personal-action aggregates and would leak if committed. Static Mode simply
doesn't show the section.

## Rationale

- **Structural separation > conventional**: root-level engine + cloud-only jobs
  means the separation is enforced by the import graph and the CI test, not by
  developer discipline.
- **Deployed-capital normalization** is the only apples-to-apples way to compare
  ledgers with different gross exposure.
- **Honest emptiness**: with no recorded actions the User Actual ledger is
  `null` and the section hides — consistent with every other phase's "no
  fabrication on sparse data" stance.

## Consequences

- Pros: the full §8 three-ledger system works end-to-end; 15 tests including
  explicit cloud-leak guards; reuses the Phase 1.1 secured `/api/local/*` plumbing.
- Cons: User Actual requires the user to manually record actions (BUY/IGNORE/…),
  so the ledger is only as complete as that logging. Tiller holdings
  (`wd_holdings_cache`) seed the portfolio but not the per-signal actions.
- Boundary: backend `ledger_engine.py` + one server endpoint + a read-only UI
  section + two db-client helpers (`recordAction`, `ledgerComparison`).
- Deferred: §31 model registry + shadow mode (a separate, cloud-side concern)
  is not part of this phase.

## Follow-ups
- Phase 9: portfolio stress test (also Local-Mode, also reads personal DB).
- §31 model registry / shadow mode.
- A UI affordance on signal cards to record BUY/IGNORE in one click (the engine
  + endpoint already support it).

## Reviewed
- Next review: after the first month of real recorded user_actions.
