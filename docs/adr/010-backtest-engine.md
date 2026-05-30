# ADR 010: Backtest Engine + Statistical Rigor (Phase 7)

Status: Accepted (2026-05-29, Phase 7)

## Context

Through Phase 6 the system could *describe* realized signal performance
(validation cuts, governor ledger), but it could not *test a strategy* — there
was no equity-curve simulation, no no-lookahead enforcement, and none of the
statistical machinery that separates a real edge from a lucky or overfit
backtest. Plan §10 calls for a Python backtest engine fully decoupled from the
UI, and §28 demands Wall-Street-standard rigor (bootstrap CI, Deflated Sharpe,
PBO, White's reality check) so a pretty CAGR can never masquerade as skill.

A hard reality: the cloud DB has only days of history so far. A multi-year
backtest is not yet *possible* — so the engine must be correct on whatever data
exists and **honest** when the data can't support a conclusion.

## Decision

### A. A stdlib backtest package (`jobs/backtest/`) + runner

```
jobs/backtest/metrics.py   return-series metrics (CAGR, Sharpe, Sortino, ...)
jobs/backtest/stats.py     §28 rigor (bootstrap, deflated Sharpe, PBO, CV, White's)
jobs/backtest/bias.py      no-lookahead guard + survivorship/PIT assessment
jobs/backtest/costs.py     transaction-cost / slippage model
jobs/backtest_runner.py    5 strategies + baselines + persistence + CLI
```

**No numpy/scipy.** Consistent with the rest of `jobs/` (and ADR 007's
constant-correlation choice), the §28 statistics are pure stdlib:
`statistics.NormalDist` provides Φ and Φ⁻¹ for the Deflated Sharpe and expected-
maximum-Sharpe terms; skew/kurtosis are computed by hand; bootstraps use a
**seeded** `random.Random` so every CI and p-value is bit-reproducible in CI.
This keeps the GHA pipeline dependency-free.

### B. No-lookahead is date-granular (the subtle bug)

Features carry `usable_at` as a timestamp (`'2026-05-29T20:00:00Z'` — computed
after the close on the feature date). A naive string compare
`usable_at > decision_date` **falsely flags** the time suffix as a lookahead
(`'…T20:00:00Z' > '2026-05-29'` lexicographically). The guard therefore compares
**calendar dates** (`substr(usable_at,1,10)`): a same-day post-close feature is
usable for a decision dated that day; only a usable_at whose *date* is strictly
later is a violation. This bug was caught by running the engine on the real DB
(53 false positives) and is now pinned by golden scenario 018.

### C. Honest status over optimistic numbers

Every run is stamped with a `results_status` that is the **more cautious** of
two judgments:

1. `assess_bias` — no historical `universe_membership` before the start ⇒
   `survivorship_bias_risk='high'`, status `reference_only`; no point-in-time
   features ⇒ status `unavailable`.
2. `classify_results_status` — PBO > 0.5 ⇒ `overfit_risk`; Deflated Sharpe and
   White's both significant ⇒ `reliable`; one of them ⇒ `marginal`.

So a strategy with a clean Sharpe of 1.04 and White's p=0.023 is still reported
`reference_only` when the DB lacks the point-in-time history to rule out
survivorship — exactly what we want. The trade-distribution strategies
(signal_outcome, candidate_discovery, risk_governor) run on day-1 data; the
equity-curve strategies (portfolio_dca, regime_aware) report `unavailable` until
price spans accrue.

### D. Strategies + baselines (Plan §10)

| Strategy | Source data | Runs today? |
|---|---|---|
| signal_outcome | generic_signals × signal_outcomes (per-trade, multi-horizon → PBO) | yes |
| candidate_discovery | candidate_snapshots forward rel_spy | when 60D outcomes accrue |
| risk_governor | System Pure vs Risk Policy avoided/missed ledger (§7/§8) | yes |
| portfolio_dca | monthly top-N equal-weight, cost-adjusted | needs price spans |
| regime_aware | risk-on vs risk-off trade distributions | when regimes accrue |
| baseline_spy / baseline_qqq | SPY/QQQ adj_close equity curve | needs price history |

Runs persist to `backtest_runs` with a deterministic `run_id`
(sha1 of strategy|start|end|config) so re-runs upsert — the pipeline stays
idempotent like every other job.

Baselines are stored as `reference_only` benchmark runs with
`survivorship_bias_risk='none'` and `pit_features_available=1`. They do not
consume `features_daily`, so the feature no-lookahead guard is intentionally
skipped for them; a benchmark card should not inherit a feature-pipeline PIT
warning from strategies that actually use features.

### D2. Phase 7.1 Data Realism Gate

`latest_backtest_summary.json` carries a top-level `data_realism` object. It
checks `prices_daily.source` across the recent backtest window and classifies:

- `synthetic_smoke` — any `synthetic/mock/test/fixture` price row exists. Metrics
  are engine smoke output only; production interpretation is blocked.
- `unverified_reference` — no synthetic rows, but unknown/manual-style sources
  exist. Interpret as reference only.
- `real_data_ready` — all price rows are known real-market sources. This only
  clears the source gate; each run still needs its own `results_status`,
  survivorship, and PIT warnings checked.

The validation UI renders this as the first banner in the Phase 7 backtest
section, so a synthetic CAGR/Sharpe cannot be mistaken for live-market evidence.

### E. Cost model

Per unit of turnover: `cost = turnover × (slippage + commission + spread/2)/1e4`,
defaults commission 0 bps, slippage 5 bps, spread 10 bps (§10). Turnover is the
L1 weight change at a rebalance.

## Rationale

- **Reproducibility**: seeded bootstraps + deterministic run_id mean a backtest
  is a pure function of (data, code, config) — re-runnable and diff-able.
- **PBO across config variants**: the signal_outcome strategy feeds its own
  horizon variants (5/20/60D) into the CSCV, directly answering "did we overfit
  by picking the best horizon?" — the question PBO exists for.
- **Bias-first status**: the integrity guard dominates the statistics, so the
  system structurally cannot present an unsupported number as reliable.

## Consequences

- Pros: a correct, dependency-free, fully-tested backtest engine; 36 unit/integration
  tests + 2 golden pins; no-lookahead + survivorship guards; §28 rigor wired into
  `backtest_runs` and surfaced in the UI with prominent bias banners.
- Cons: equity-curve strategies are inert until price/feature history accrues
  (honest `unavailable`); the Deflated Sharpe's cross-trial variance is a single-
  strategy proxy (SE²) unless the caller supplies the true trial dispersion;
  PBO/CSCV is O(C(n_blocks, n_blocks/2)) — fine for a daily/on-demand job, not
  interactive.
- Boundary: backend only + a read-only UI card section in validation.js. The
  engine is on-demand (CLI / `run_suite`), not part of the daily snapshot, to
  protect GHA time (§10 "UI와 완전 분리").

## Follow-ups

- Phase 7+: once ≥6 months of point-in-time data exists, the equity strategies
  graduate from `unavailable`; re-baseline SPY/QQQ CAGR vs an external source.
- §30 (factor risk model / historical stress) layers on top of this engine.
- Consider a real per-trial Sharpe dispersion for the Deflated Sharpe once
  shadow-mode (§31) produces multiple model variants.

## Reviewed
- Next review: after 6 months of snapshot history (equity backtests runnable).
