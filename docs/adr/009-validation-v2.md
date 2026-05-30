# ADR 009: Validation v2 — Multi-Cut, Governor Ledger, Signal Diff, Golden Set

Status: Accepted (2026-05-29, Phase 6)

> Renumbered from 008 → 009 to avoid collision with
> `008-phase-5-1-hardening.md` (Phase 5.1 hardening landed concurrently).
> Phase 5.1 wired the `risk_adjusted_weight` (Risk Policy) ledger that this
> ADR's **governor-ledger cut** consumes — see section A2.

## Context

Through Phase 5 the `latest_validation_summary.json` view produced only two
cuts: overall-by-horizon and by-source. That was enough to confirm the pipeline
was wired, but far too coarse to answer the real question: **"Is this a money-
making system?"** The plan (§9) defines 10 specific questions that require
multi-dimensional slicing of `signal_outcomes`. Additionally, §27 mandates a
regression-testing golden set and a daily signal-diff report so code changes
can be caught before they silently alter recommendations.

## Decision

### A. Multi-cut validation (jobs/views_builder.py)

Replace the single-join `_validation_summary` with a richer version that:

1. **Fetches all graded rows** in one query joining `signal_outcomes`,
   `generic_signals`, and `candidate_snapshots`.
2. **Aggregates six cuts** per bucket: overall-by-horizon, by_source,
   by_candidate_type, by_risk_status, by_dq_tier, by_regime.
3. **Adds three new metrics** per cell: `payoff_ratio` (avg_win / |avg_loss|),
   `avg_mae`, `avg_mfe`, `avg_mdd` — so risk/reward shape is visible alongside
   EV and PF.
4. **Auto-answers 10 core questions** (§9 Q1–Q10) in a structured list, each
   with `answer` (bool or null), `evidence` (the raw stats), and
   `sufficient_data` (n ≥ threshold). `answer=null` when data is too sparse —
   never manufactures a yes/no from noise.

Hit definition: `abs_ret > 0` (any positive return). This is more conservative
than a ±2% band — a flat +0.1% counts as a win but is quantitatively negligible.
The payoff ratio and EV tell the full story.

#### A2. Governor ledger — System Pure vs Risk Policy (§7/§8)

Phase 5.1 made `signal_generator` write **two** sizes per signal:
`target_weight_generic` (System Pure, composite-only) and `risk_adjusted_weight`
(Risk Policy, post-governor). `_governor_ledger` turns that pair into the
avoided-drawdown / missed-upside ledger §7 always promised:

```
cut = target_weight_generic − risk_adjusted_weight     # weight the governor removed (≥0)
for each graded outcome (cut > 0):
    ret < 0  → avoided_drawdown += cut · |ret|          # loss the cut spared us
    ret > 0  → missed_upside    += cut ·  ret           # gain the cut cost us
net_governor_value = avoided_drawdown − missed_upside    # >0 ⇒ the governor paid off
```

Units are **weight·return = fraction of portfolio** (0.004 = 40bp of book). The
ledger is computed per horizon and carries `n_governed`, `n_blocked`, and a
`governor_verdict` ∈ {beneficial, costly, neutral, no_data}. Fully-blocked names
(`risk_adjusted_weight = 0`) and partially size-reduced names are both captured —
a `SIZE_REDUCED` 0.04→0.02 contributes half its return to the ledger.

**Q4 prefers this ledger.** When ≥5 positions were governed at 20D, Q4 ("did the
governor net out ahead?") answers on `net_governor_value > 0`; otherwise it falls
back to the APPROVED-vs-BLOCKED EV comparison. This is the direct, realized
answer to §9 Q3 — *"Risk Governor가 손실을 줄였는가, upside만 놓쳤는가?"*

### B. Signal diff report (latest_signal_diff.json)

`_signal_diff` compares yesterday's `generic_signals` to today's for the same
tickers. Any ticker with a state change, risk_status change, or |score_delta|≥5
is included in `changes[]`. A **regression_warning** fires if >20% of common
tickers changed state in one day — that magnitude almost always signals a code
bug rather than genuine market movement.

New tickers (appeared today) and dropped tickers (gone today) are listed
separately so the snapshot-monitoring UI can surface "NVDA newly blocked" or
"TSLA dropped from universe" directly.

### C. Golden Set + regression testing (tests/golden/, tests/test_golden.py)

16 frozen JSON scenarios cover every pure function that computes a
recommendation-visible output:

| Scenario range | Coverage |
|---------------|---------|
| 001–004 | `_agg_stats` math: basic, all-wins, all-losses, empty |
| 005 | `_dq_tier` boundaries (7 cases) |
| 006 | `_signal_id` SHA1 determinism (3 tickers) |
| 007–009 | `risk_governor.evaluate` — approved, compound-reduction, macro-blocked |
| 010 | `_target_weight` composite thresholds (9 cases) |
| 011 | `classify_candidate` Core/Tactical/Watchlist/Reject (5 cases) |
| 012–013 | `sizing` — inverse_vol ordering, fractional_kelly caps |
| 014 | signal_diff regression_warning logic (3 cases) |
| 015 | `_core_questions` Q5/Q7 sufficient/insufficient data |
| 016 | `_governor_ledger` avoided/missed/net (mixed blocked + reduced) |

Scenarios are **pure-function** (no DB, no time, no RNG) — replaying them is
deterministic. If a code change causes a scenario to drift, the engineer must
update the golden file with an explicit commit explaining the intended change.

## Rationale

- **Multi-cut is necessary for Q9 §9**: "Was the Risk Governor worth it?" can
  only be answered by comparing APPROVED vs BLOCKED EV — that requires
  by_risk_status. Same for DQ-tier, regime, and candidate-type cuts.
- **Null answers > forced yes/no**: with a fresh DB (0 graded outcomes), all
  core questions return `answer=null, sufficient_data=false`. This is honest.
  Forcing "yes" on insufficient data would mislead.
- **Signal diff at 20%**: empirically a single market day rarely shifts >10% of
  tickers' state. 20% is a safe canary — conservative enough not to fire on
  genuine volatile days but tight enough to catch formula bugs.
- **Golden set over DB-integration tests**: pure-function golden tests run in
  <100ms with no fixture setup. DB-integration tests (in test_snapshot.py,
  test_risk_governor.py) already cover end-to-end wiring. The golden set adds
  the "this exact numeric output is stable" guarantee.

## Consequences

- **Pros**: 10 core questions answered automatically every snapshot; signal
  regressions detectable within one snapshot cycle; payoff/MAE/MFE visible
  alongside EV/PF; 21 new golden-set CI tests.
- **Cons**: validation summary grows ~10× in size (2KB → 30KB once outcomes
  accumulate). This is still well within Static Mode fetch budget.
- **Boundary**: Phase 6 does not add UI changes — the rich JSON is rendered
  by the existing validation.js (which already reads `latest_validation_summary.json`).
  UI enrichment to display multi-cut tables is a separate frontend task.
- **Data dependency**: by_risk_status / by_dq_tier cuts are only meaningful
  after 30+ days of graded outcomes. Until then the core questions return
  `sufficient_data=false`. The system never blocks on this.

## Follow-ups

- Phase 7: statistical rigor — bootstrap CI, deflated Sharpe, PBO, CSCV on
  `backtest_runs`. Those metrics augment the by-horizon stats in this view.
- Phase 8: add user_actual ledger comparison (requires Local Mode portfolio data).
- 30-day milestone: review core questions with real data; update golden
  scenarios 007–009 with live DB signal_ids for replay test.

## Reviewed

- Next review: after 30 days of live snapshot data (core questions have data).
