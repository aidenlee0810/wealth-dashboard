"""
jobs/backtest/stats.py — statistical rigor for backtests (Plan §28).

Wall-Street-standard guards against the cardinal sin of backtesting —
mistaking luck (or overfitting) for skill. All stdlib (statistics.NormalDist
for Φ and Φ⁻¹, seeded `random` for bootstraps), no numpy/scipy.

  bootstrap_sharpe          Sharpe + bootstrap confidence interval
  deflated_sharpe           Deflated Sharpe Ratio (Bailey & López de Prado):
                            multiple-testing + skew/kurtosis correction
  probability_backtest_overfitting   PBO via CSCV (Bailey 2014)
  time_series_cv_splits     walk-forward train/test index folds (no random split)
  whites_reality_check      bootstrap test that a strategy beats its benchmark

Every function returns a dict with the metric plus an explicit insufficiency
note when the sample is too small, so the runner records 'unavailable' rather
than reporting a number computed from noise.
"""

from __future__ import annotations

import math
import random
import statistics
from itertools import combinations
from typing import Optional, Sequence

from .metrics import sharpe as _sharpe, _percentile, RISK_FREE_RATE, TRADING_DAYS

_NORM = statistics.NormalDist()
_EULER_MASCHERONI = 0.5772156649015329

# Minimum observations for each estimator to be meaningful.
MIN_BOOTSTRAP = 8
MIN_DSR_OBS = 10


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------
def _moments(xs: Sequence[float]) -> tuple[float, float]:
    """Population skewness and (non-excess) kurtosis. Normal → (0, 3).

    Uses the 1/n moment definitions that the PSR/DSR derivation assumes."""
    n = len(xs)
    if n < 3:
        return (0.0, 3.0)
    m = statistics.fmean(xs)
    var = sum((x - m) ** 2 for x in xs) / n
    if var <= 0:
        return (0.0, 3.0)
    sd = math.sqrt(var)
    skew = sum(((x - m) / sd) ** 3 for x in xs) / n
    kurt = sum(((x - m) / sd) ** 4 for x in xs) / n
    return (skew, kurt)


# ---------------------------------------------------------------------------
# 1. Bootstrap Sharpe CI
# ---------------------------------------------------------------------------
def bootstrap_sharpe(rets: Sequence[float], *, n_iter: int = 10000,
                     ci: float = 0.95, periods_per_year: int = TRADING_DAYS,
                     rf_annual: float = RISK_FREE_RATE,
                     seed: Optional[int] = 7) -> dict:
    """Annualized Sharpe with a bootstrap confidence interval.

    Resamples the return series with replacement `n_iter` times; the CI is the
    empirical quantile band of the resampled Sharpes. If the CI excludes 0 the
    Sharpe is 'significant' at the (1-ci) level."""
    r = [x for x in rets if x is not None]
    point = _sharpe(r, periods_per_year, rf_annual)
    if len(r) < MIN_BOOTSTRAP or point is None:
        return {"sharpe": round(point, 4) if point is not None else None,
                "ci_lower": None, "ci_upper": None, "n": len(r),
                "note": "insufficient_data"}
    rng = random.Random(seed)
    n = len(r)
    sharpes: list[float] = []
    for _ in range(n_iter):
        sample = [r[rng.randrange(n)] for _ in range(n)]
        s = _sharpe(sample, periods_per_year, rf_annual)
        if s is not None:
            sharpes.append(s)
    if len(sharpes) < n_iter // 2:
        return {"sharpe": round(point, 4), "ci_lower": None, "ci_upper": None,
                "n": n, "note": "resample_degenerate"}
    sharpes.sort()
    alpha = 1.0 - ci
    lo = _percentile(sharpes, alpha / 2 * 100)
    hi = _percentile(sharpes, (1 - alpha / 2) * 100)
    return {"sharpe": round(point, 4), "ci_lower": round(lo, 4),
            "ci_upper": round(hi, 4), "ci": ci, "n_bootstrap": len(sharpes),
            "significant": lo > 0}


# ---------------------------------------------------------------------------
# 2. Deflated Sharpe Ratio
# ---------------------------------------------------------------------------
def deflated_sharpe(sharpe_annual: float, n_obs: int, *, n_trials: int,
                    skew: float = 0.0, kurtosis: float = 3.0,
                    periods_per_year: int = TRADING_DAYS,
                    sr_variance_across_trials: Optional[float] = None) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    Corrects an observed Sharpe for (a) the number of trials that produced it
    (selection bias) and (b) the return distribution's skew/kurtosis. Returns
    the probability that the true Sharpe exceeds the deflation benchmark SR*.

    sharpe_annual : the achieved annualized Sharpe.
    n_obs         : number of return observations T.
    n_trials      : number of independent strategy configurations tried (N).
    skew/kurtosis : of the (per-period) returns; kurtosis non-excess (normal=3).
    sr_variance_across_trials : Var of the per-period Sharpes across the N
        trials. If unknown we substitute the analytic variance of the Sharpe
        estimator (a defensible, slightly conservative proxy)."""
    if n_obs < MIN_DSR_OBS or n_trials < 1:
        return {"deflated_sharpe": None, "sr_star": None,
                "is_significant": None, "note": "insufficient_data"}
    sr = sharpe_annual / math.sqrt(periods_per_year)        # de-annualize
    denom_term = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr ** 2
    if denom_term <= 0 or n_obs <= 1:
        return {"deflated_sharpe": None, "sr_star": None,
                "is_significant": None, "note": "degenerate_denominator"}

    # Expected maximum Sharpe of N trials under the null (LdP eq.)
    if n_trials >= 2:
        z1 = _NORM.inv_cdf(1.0 - 1.0 / n_trials)
        z2 = _NORM.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
        emax = (1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2
    else:
        emax = 0.0                                          # single trial → no deflation
    var_trials = (sr_variance_across_trials
                  if sr_variance_across_trials is not None
                  else denom_term / (n_obs - 1))            # SE² proxy
    sr_star = math.sqrt(max(var_trials, 0.0)) * emax

    # Probabilistic Sharpe Ratio at the deflated benchmark
    z = (sr - sr_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_term)
    dsr = _NORM.cdf(z)
    return {"deflated_sharpe": round(dsr, 4),
            "sr_star": round(sr_star, 6),
            "sr_observed_per_period": round(sr, 6),
            "is_significant": dsr > 0.95,
            "n_trials": n_trials}


def deflated_sharpe_from_returns(rets: Sequence[float], *, n_trials: int,
                                 periods_per_year: int = TRADING_DAYS,
                                 rf_annual: float = RISK_FREE_RATE) -> dict:
    """Convenience: compute skew/kurtosis from the series, then deflate."""
    r = [x for x in rets if x is not None]
    sa = _sharpe(r, periods_per_year, rf_annual)
    if sa is None or len(r) < MIN_DSR_OBS:
        return {"deflated_sharpe": None, "sr_star": None,
                "is_significant": None, "note": "insufficient_data"}
    sk, ku = _moments(r)
    return deflated_sharpe(sa, len(r), n_trials=n_trials, skew=sk, kurtosis=ku,
                           periods_per_year=periods_per_year)


# ---------------------------------------------------------------------------
# 3. Probability of Backtest Overfitting (PBO) via CSCV
# ---------------------------------------------------------------------------
def probability_backtest_overfitting(returns_by_strategy: dict, *,
                                     n_blocks: int = 8,
                                     periods_per_year: int = TRADING_DAYS,
                                     rf_annual: float = RISK_FREE_RATE) -> dict:
    """Combinatorially-Symmetric Cross-Validation PBO (Bailey et al. 2014).

    returns_by_strategy : {name: [periodic returns]}, all equal length T.

    Partition the T periods into `n_blocks` contiguous blocks. For every way of
    choosing half the blocks as in-sample (IS): pick the strategy with the best
    IS Sharpe, then look at that strategy's out-of-sample (OOS) rank. PBO is the
    fraction of splits where the IS-best strategy lands below the OOS median —
    i.e. how often "the winner" was just overfit. PBO > 0.5 ⇒ overfit risk."""
    names = list(returns_by_strategy.keys())
    S = len(names)
    if S < 2:
        return {"pbo": None, "note": "need_>=2_strategies", "n_strategies": S}
    lengths = {len(returns_by_strategy[n]) for n in names}
    if len(lengths) != 1:
        return {"pbo": None, "note": "unequal_series_lengths"}
    T = lengths.pop()
    if n_blocks % 2 != 0:
        n_blocks -= 1
    if T < n_blocks or n_blocks < 2:
        return {"pbo": None, "note": "insufficient_periods", "T": T}

    # contiguous block index ranges
    bounds = [round(i * T / n_blocks) for i in range(n_blocks + 1)]
    blocks = [list(range(bounds[i], bounds[i + 1])) for i in range(n_blocks)]

    def _sh(name, idx):
        sub = [returns_by_strategy[name][i] for i in idx]
        s = _sharpe(sub, periods_per_year, rf_annual)
        return s if s is not None else float("-inf")

    half = n_blocks // 2
    n_below = 0
    n_combos = 0
    logits = []
    for is_blocks in combinations(range(n_blocks), half):
        is_idx = [i for b in is_blocks for i in blocks[b]]
        oos_blocks = [b for b in range(n_blocks) if b not in is_blocks]
        oos_idx = [i for b in oos_blocks for i in blocks[b]]

        is_sharpes = {nm: _sh(nm, is_idx) for nm in names}
        best = max(names, key=lambda nm: is_sharpes[nm])
        oos_sharpes = {nm: _sh(nm, oos_idx) for nm in names}

        # OOS rank of the IS-best strategy: 1 = worst, S = best
        ordered = sorted(names, key=lambda nm: oos_sharpes[nm])
        rank = ordered.index(best) + 1
        omega = rank / (S + 1)                              # relative rank in (0,1)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        lam = math.log(omega / (1 - omega))                # logit
        logits.append(lam)
        if lam <= 0:                                        # OOS rank ≤ median
            n_below += 1
        n_combos += 1

    pbo = n_below / n_combos if n_combos else None
    return {"pbo": round(pbo, 4) if pbo is not None else None,
            "n_combinations": n_combos, "n_strategies": S, "n_blocks": n_blocks,
            "median_logit": round(statistics.median(logits), 4) if logits else None,
            "is_overfit_risk": (pbo > 0.5) if pbo is not None else None}


# ---------------------------------------------------------------------------
# 4. Time-series cross-validation (walk-forward — NO random split)
# ---------------------------------------------------------------------------
def time_series_cv_splits(n_periods: int, *, train: int, test: int,
                          step: Optional[int] = None) -> list[dict]:
    """Sliding walk-forward folds over [0, n_periods). Each fold's test window
    lies strictly AFTER its train window — the structural anti-lookahead split.
    Returns [{train:(a,b), test:(b,c)}, ...]."""
    if train <= 0 or test <= 0:
        raise ValueError("train and test must be positive")
    step = step or test
    folds = []
    start = 0
    while start + train + test <= n_periods:
        a, b, c = start, start + train, start + train + test
        folds.append({"train": (a, b), "test": (b, c)})
        start += step
    return folds


# ---------------------------------------------------------------------------
# 5. White's Reality Check
# ---------------------------------------------------------------------------
def whites_reality_check(strategy_rets: Sequence[float],
                         benchmark_rets: Sequence[float], *,
                         n_bootstrap: int = 2000,
                         seed: Optional[int] = 7) -> dict:
    """Bootstrap test of H0: strategy ≤ benchmark (Σ excess ≤ 0).

    The null distribution of the mean excess return is approximated by the
    *centered* bootstrap (resampled mean minus the observed mean). The p-value
    is the share of centered resamples at least as large as the observed mean.
    p < 0.05 ⇒ the outperformance is unlikely to be luck."""
    pairs = [(s, b) for s, b in zip(strategy_rets, benchmark_rets)
             if s is not None and b is not None]
    excess = [s - b for s, b in pairs]
    T = len(excess)
    if T < MIN_BOOTSTRAP:
        return {"p_value": None, "significant": None, "note": "insufficient_data"}
    mu = statistics.fmean(excess)
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_bootstrap):
        sample_mean = statistics.fmean(excess[rng.randrange(T)] for _ in range(T))
        if (sample_mean - mu) >= mu:                        # centered ≥ observed
            ge += 1
    p = ge / n_bootstrap
    return {"p_value": round(p, 4), "observed_excess_mean": round(mu, 6),
            "significant": p < 0.05, "n_bootstrap": n_bootstrap}


# ---------------------------------------------------------------------------
# Results-status classifier (feeds backtest_runs.results_status)
# ---------------------------------------------------------------------------
def classify_results_status(*, deflated: Optional[dict], pbo: Optional[dict],
                            whites: Optional[dict], n_periods: int) -> str:
    """Map the rigor metrics to one of the backtest_runs.results_status values:
    'reliable' | 'marginal' | 'overfit_risk' | 'unavailable'."""
    if n_periods < MIN_DSR_OBS:
        return "unavailable"
    if pbo and pbo.get("is_overfit_risk"):
        return "overfit_risk"
    dsr_ok = bool(deflated and deflated.get("is_significant"))
    white_ok = bool(whites and whites.get("significant"))
    if dsr_ok and white_ok:
        return "reliable"
    if dsr_ok or white_ok:
        return "marginal"
    return "marginal"


if __name__ == "__main__":
    import json
    rng = random.Random(0)
    # a genuinely positive-drift strategy vs a flat benchmark
    strat = [rng.gauss(0.0008, 0.01) for _ in range(252)]
    bench = [rng.gauss(0.0002, 0.009) for _ in range(252)]
    print("bootstrap:", json.dumps(bootstrap_sharpe(strat, n_iter=2000), indent=2))
    print("deflated :", json.dumps(deflated_sharpe_from_returns(strat, n_trials=20), indent=2))
    print("whites   :", json.dumps(whites_reality_check(strat, bench, n_bootstrap=2000), indent=2))
    mat = {"strat": strat, "bench": bench,
           "noise": [rng.gauss(0, 0.01) for _ in range(252)]}
    print("pbo      :", json.dumps(probability_backtest_overfitting(mat, n_blocks=8), indent=2))
