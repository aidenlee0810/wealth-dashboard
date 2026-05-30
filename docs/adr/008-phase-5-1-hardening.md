# ADR 008: Phase 5.1 Hardening Handoff

Date: 2026-05-29

## Status

Accepted

## Context

Phase 5 added the Risk Governor and position sizing, but review found four
production-readiness gaps:

1. Fractional Kelly sizing was capped per name, then re-normalized to 100%,
   which destroyed the cap and removed the intended cash residual.
2. The hard downtrend gate required `sma200_slope`, but `signal_generator.py`
   did not pass it, so the live signal path only produced `DOWNTREND_SOFT`.
3. Fundamental cache freshness used `cleaned_at` only, so a cached row could be
   reused in a backtest even when its `usable_at` was after the decision date.
4. The governor computed post-risk size, but only the pre-risk generic target
   was persisted in signals/candidate views.

## Decision

- `sizing.apply_method("fractional_kelly")` now returns capped Kelly weights
  directly. The unallocated balance remains cash.
- `signal_generator.py` computes a point-in-time SMA200 slope from the last 220
  bars and passes it to the Risk Governor. Hard downtrend blocks are now live in
  the actual pipeline.
- `financials._has_fresh_fundamentals()` now requires `usable_at <= as_of`, and
  the default cache window is one day. This preserves same-day rerun caching
  without delaying new SEC filings for a week by default.
- Migration 006 adds explicit Risk Policy outputs to `generic_signals` and
  `candidate_snapshots`:
  `risk_adjusted_weight`, `risk_size_multiplier`, `risk_reason`,
  `risk_manual_checks_json`.
- Candidate views expose the post-risk weight separately from the System Pure
  `target_weight`.
- `lineage_json.risk_governor` stores the full governor payload for audit and
  Phase 6 validation cuts.

## Claude Next Actions

1. Do not re-normalize fractional Kelly outputs unless the UI explicitly labels
   the result as "relative allocation among selected Kelly names." For portfolio
   sizing, preserve cash residual.
2. If adding a richer technical feature set later, move `_sma200_slope()` into
   `scorers.technical` and persist it in `features_daily`; keep the current
   signal-generator helper until a migration exists.
3. For SEC freshness, a future improvement is a cheap `submissions/CIK*.json`
   check before reusing the one-day cache. Do not go back to blind 7-day cache
   without a filing-date check.
4. Phase 6 validation should compare:
   - `target_weight_generic` (System Pure)
   - `risk_adjusted_weight` (Risk Policy)
   - `risk_status`
   - blocked/reduced missed-upside vs avoided-drawdown outcomes.

## Validation

- `tests/test_risk_governor.py` covers Kelly cash residual, hard downtrend in
  `generic_signals`, persisted risk-adjusted weights, and governor lineage.
- `tests/test_scoring.py` covers as-of-safe cache reuse.
- Full regression after this ADR: run `python -m pytest -q`.
