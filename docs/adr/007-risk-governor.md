# ADR 007: Risk Governor + Position Sizing (Phase 5)

Status: Accepted (2026-05-29, Phase 5)

## Context

Through Phase 4 the snapshot produced a multi-pillar `composite` and a *minimal*
risk flag (`signal_generator._risk_status`: DQ + downtrend only). Real risk logic
(liquidity, valuation blow-ups, macro stress, IPO seasoning, portfolio
concentration) was either absent or scattered. The plan (§7) calls for one
deterministic gate that **every recommendation passes through**, plus explicit
position-sizing methods (§29) instead of an ad-hoc target-gap heuristic.

A hard constraint: some gates need the *personal portfolio* (concentration,
leverage, correlation, drawdown), which must never reach the cloud DB.

## Decision

### A. One governor module, two execution contexts (`jobs/risk_governor.py`)

15 gates with a shared severity model (BLOCKED > REVIEW_REQUIRED > SIZE_REDUCED
> APPROVED). The **same** code serves both:

| Context | Gates run |
|---------|-----------|
| Cloud snapshot (`portfolio=None`) | market/data gates: missing-essentials, DQ critical/low, IPO seasoning, liquidity, downtrend, extreme-valuation-no-FCF, earnings blackout, leveraged-ETF decay, macro-severe |
| Local Mode (`portfolio={…}`) | + high-beta basket, leverage, sector/theme concentration, single-position, correlation cluster, portfolio drawdown |

Portfolio gates simply **no-op when `portfolio` is None**, so the cloud job runs
the market gates with zero personal data. A gate whose inputs are unavailable
(e.g. no earnings calendar) **skips rather than fires** — we never block on
missing data, except the explicit missing-essentials gate. Output mirrors the
planned JS governor (`risk_status`, `risk_flags`, `max_allowed_weight`,
`suggested_size_before/after_risk`, `size_multiplier`, `reason`,
`manual_checks`) so Local Mode `optimizer.js` can share the contract.

### B. Three-ledger seam (§8)

`generic_signals.target_weight_generic` stays the **System Pure** size (pre-risk,
composite-only) — that's the baseline we measure the governor against. The
governor's `suggested_size_after_risk` is the **System Risk Policy** size,
computed in Local Mode (it can depend on the portfolio). `risk_status` +
`risk_flags_json` are stored on every signal so blocked names are first-class:
they are written to `generic_signals` and graded by the outcome updater exactly
like approved ones, which is what lets Phase 6 compute the governor's
**avoided-drawdown vs missed-upside** ledger.

### C. Position sizing (`jobs/sizing.py`, §29)

Four selectable methods — equal, inverse-volatility, risk-parity, fractional
Kelly (0.25×) — plus `risk_contribution` and `vol_target`. To stay stdlib (no
numpy) we use a transparent **constant-correlation** risk model (single ρ)
instead of a full covariance matrix; risk-parity iterates to equal risk
contribution under it (verified: RC equal to 2 dp). Full Fama-French/covariance
risk is deferred to Phase 7 (§30).

## Rationale

- **One gate, one place**: every recommendation is governed by the same 15
  rules; no risk logic hidden in the UI.
- **Cloud/local integrity**: market gates need no personal data; portfolio gates
  activate only with an explicit portfolio argument — the separation is
  structural, not conventional.
- **Honest blocking**: gates skip on missing data, so we don't manufacture
  blocks; but blocked signals are still recorded + graded, so the governor's
  cost/benefit is measurable rather than invisible.
- **Statistically grounded sizing**: fractional Kelly + risk parity replace the
  target-gap heuristic, with risk-contribution transparency.

## Consequences

- Pros: consolidated deterministic risk gate, cloud-safe, measurable governor,
  real sizing methods, 25 unit tests (every gate + sizing).
- Cons: earnings-blackout + some valuation gates are inert until that data is
  wired (earnings calendar — Phase 6+; EV/Sales needs market-cap join — partial).
  The constant-correlation sizing model is a simplification (full covariance is
  Phase 7). Beta/high-beta basket membership for the high-beta gate is supplied
  by the portfolio context (Local), not computed here.
- Boundary: backend only. Local Mode `optimizer.js` (parallel frontend work)
  will call the same logic with portfolio context and render
  `suggested_size_after_risk` + `manual_checks`.

## Follow-ups
- Phase 6: validation cuts by `risk_status`; avoided-drawdown vs missed-upside.
- Earnings calendar fetch → activate the blackout gate live.
- Phase 7: covariance/FF risk model replaces the constant-ρ sizing approximation.

## Reviewed
- Next review: after Phase 6 (validation v2).
