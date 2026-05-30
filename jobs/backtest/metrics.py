"""
jobs/backtest/metrics.py — return-series performance metrics (Plan §10).

All metrics are pure functions over a list of **periodic simple returns**
(`rets`, e.g. daily r_t = P_t/P_{t-1} - 1) or an **equity curve** (`equity`,
a list of portfolio values). Helpers convert between the two.

Annualization uses `periods_per_year` (ppy): 252 for daily, 12 for monthly,
52 for weekly. The risk-free rate is supplied annualized and converted to a
per-period rate as rf/ppy (the standard simple-compounding convention used in
practitioner Sharpe calculations).

Everything is stdlib (math, statistics). Functions return None — never raise —
when the series is too short to define the metric (e.g. Sharpe needs ≥2
returns and non-zero dispersion), so the runner can mark a result 'unavailable'
rather than crash on sparse data.
"""

from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

RISK_FREE_RATE = 0.043          # ~ current T-bill, annualized; overridable
TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Series conversions
# ---------------------------------------------------------------------------
def returns_from_equity(equity: Sequence[float]) -> list[float]:
    """Simple period returns from an equity curve. len-1 elements."""
    out = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        if prev in (None, 0) or equity[i] is None:
            continue
        out.append(equity[i] / prev - 1.0)
    return out


def equity_from_returns(rets: Sequence[float], start: float = 1.0) -> list[float]:
    """Compound a return series into an equity curve starting at `start`."""
    eq = [start]
    for r in rets:
        eq.append(eq[-1] * (1.0 + r))
    return eq


# ---------------------------------------------------------------------------
# Level metrics
# ---------------------------------------------------------------------------
def total_return(equity: Sequence[float]) -> Optional[float]:
    if not equity or len(equity) < 2 or equity[0] in (None, 0):
        return None
    return equity[-1] / equity[0] - 1.0


def cagr(equity: Sequence[float], periods_per_year: int = TRADING_DAYS) -> Optional[float]:
    """Compound annual growth rate from an equity curve."""
    if not equity or len(equity) < 2 or equity[0] in (None, 0) or equity[-1] is None:
        return None
    n_periods = len(equity) - 1
    if n_periods <= 0 or equity[-1] <= 0:
        return None
    years = n_periods / periods_per_year
    if years <= 0:
        return None
    return (equity[-1] / equity[0]) ** (1.0 / years) - 1.0


def max_drawdown(equity: Sequence[float]) -> Optional[float]:
    """Largest peak-to-trough decline, returned as a negative fraction."""
    vals = [e for e in equity if e is not None]
    if len(vals) < 2:
        return None
    peak = vals[0]
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return round(mdd, 6)


# ---------------------------------------------------------------------------
# Return-distribution metrics
# ---------------------------------------------------------------------------
def volatility(rets: Sequence[float], periods_per_year: int = TRADING_DAYS) -> Optional[float]:
    """Annualized standard deviation of returns (sample stdev × √ppy)."""
    r = [x for x in rets if x is not None]
    if len(r) < 2:
        return None
    sd = statistics.stdev(r)
    return sd * math.sqrt(periods_per_year)


def _excess(rets: Sequence[float], rf_annual: float, ppy: int) -> list[float]:
    rf_period = rf_annual / ppy
    return [x - rf_period for x in rets if x is not None]


def sharpe(rets: Sequence[float], periods_per_year: int = TRADING_DAYS,
           rf_annual: float = RISK_FREE_RATE) -> Optional[float]:
    """Annualized Sharpe ratio: mean(excess)/stdev(excess) × √ppy."""
    e = _excess(rets, rf_annual, periods_per_year)
    if len(e) < 2:
        return None
    sd = statistics.stdev(e)
    if sd == 0:
        return None
    return (statistics.fmean(e) / sd) * math.sqrt(periods_per_year)


def sortino(rets: Sequence[float], periods_per_year: int = TRADING_DAYS,
            rf_annual: float = RISK_FREE_RATE) -> Optional[float]:
    """Annualized Sortino: mean(excess) / target-downside-deviation × √ppy.

    Downside deviation uses target=0 on the excess series, averaging the
    squared shortfalls over ALL periods (target semivariance)."""
    e = _excess(rets, rf_annual, periods_per_year)
    if len(e) < 2:
        return None
    downside_sq = [min(x, 0.0) ** 2 for x in e]
    tdd = math.sqrt(sum(downside_sq) / len(e))
    if tdd == 0:
        return None
    return (statistics.fmean(e) / tdd) * math.sqrt(periods_per_year)


def calmar(cagr_value: Optional[float], max_dd: Optional[float]) -> Optional[float]:
    """CAGR / |max drawdown|."""
    if cagr_value is None or max_dd is None or max_dd == 0:
        return None
    return cagr_value / abs(max_dd)


# ---------------------------------------------------------------------------
# Trade / win-loss metrics (also valid on a periodic return series)
# ---------------------------------------------------------------------------
def hit_rate(rets: Sequence[float]) -> Optional[float]:
    r = [x for x in rets if x is not None]
    if not r:
        return None
    return sum(1 for x in r if x > 0) / len(r)


def avg_win(rets: Sequence[float]) -> Optional[float]:
    wins = [x for x in rets if x is not None and x > 0]
    return statistics.fmean(wins) if wins else 0.0


def avg_loss(rets: Sequence[float]) -> Optional[float]:
    losses = [x for x in rets if x is not None and x < 0]
    return statistics.fmean(losses) if losses else 0.0   # negative or 0


def profit_factor(rets: Sequence[float]) -> Optional[float]:
    r = [x for x in rets if x is not None]
    gains = sum(x for x in r if x > 0)
    losses = sum(-x for x in r if x < 0)
    if losses == 0:
        return None if gains == 0 else float("inf")
    return gains / losses


def payoff_ratio(rets: Sequence[float]) -> Optional[float]:
    aw, al = avg_win(rets), avg_loss(rets)
    if al is None or al == 0:
        return None if (aw or 0) == 0 else float("inf")
    return aw / abs(al)


def expected_value(rets: Sequence[float]) -> Optional[float]:
    """EV per trade = hit·avg_win + (1-hit)·avg_loss, using the same win(>0)/
    loss(<0) decomposition as the Phase 6 validation EV (views_builder._agg_stats)
    so the two ledgers agree. Note: with exact-zero returns present this differs
    slightly from the arithmetic mean, by construction."""
    hr = hit_rate(rets)
    if hr is None:
        return None
    return hr * (avg_win(rets) or 0.0) + (1 - hr) * (avg_loss(rets) or 0.0)


def _percentile(sorted_xs: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in [0,100]), numpy 'linear' default."""
    if not sorted_xs:
        return float("nan")
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    rank = (p / 100.0) * (len(sorted_xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_xs[int(rank)]
    frac = rank - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def tail_loss(rets: Sequence[float], pct: float = 5.0) -> Optional[float]:
    """Return at the pth percentile (left tail). For pct=5 this is the loss
    such that 5% of outcomes were worse — a simple historical VaR."""
    r = sorted(x for x in rets if x is not None)
    if len(r) < 2:
        return None
    return round(_percentile(r, pct), 6)


# ---------------------------------------------------------------------------
# Bundled summary
# ---------------------------------------------------------------------------
def summary(rets: Optional[Sequence[float]] = None, *,
            equity: Optional[Sequence[float]] = None,
            periods_per_year: int = TRADING_DAYS,
            rf_annual: float = RISK_FREE_RATE) -> dict:
    """Compute the full metric bundle from either a return series or an equity
    curve (whichever is given; equity is derived from rets if only rets given)."""
    if rets is None and equity is None:
        raise ValueError("summary() needs rets or equity")
    if equity is None:
        equity = equity_from_returns(rets)
    if rets is None:
        rets = returns_from_equity(equity)

    cg = cagr(equity, periods_per_year)
    mdd = max_drawdown(equity)
    pf = profit_factor(rets)
    po = payoff_ratio(rets)
    return {
        "n_periods": len(rets),
        "total_return": (round(total_return(equity), 6)
                         if total_return(equity) is not None else None),
        "cagr": round(cg, 6) if cg is not None else None,
        "volatility": (round(volatility(rets, periods_per_year), 6)
                       if volatility(rets, periods_per_year) is not None else None),
        "sharpe": (round(sharpe(rets, periods_per_year, rf_annual), 4)
                   if sharpe(rets, periods_per_year, rf_annual) is not None else None),
        "sortino": (round(sortino(rets, periods_per_year, rf_annual), 4)
                    if sortino(rets, periods_per_year, rf_annual) is not None else None),
        "max_drawdown": mdd,
        "calmar": (round(calmar(cg, mdd), 4) if calmar(cg, mdd) is not None else None),
        "hit_rate": (round(hit_rate(rets), 4) if hit_rate(rets) is not None else None),
        "avg_win": round(avg_win(rets), 6) if avg_win(rets) is not None else None,
        "avg_loss": round(avg_loss(rets), 6) if avg_loss(rets) is not None else None,
        "profit_factor": (round(pf, 4) if pf not in (None, float("inf")) else
                          ("inf" if pf == float("inf") else None)),
        "payoff_ratio": (round(po, 4) if po not in (None, float("inf")) else
                         ("inf" if po == float("inf") else None)),
        "expected_value": (round(expected_value(rets), 6)
                           if expected_value(rets) is not None else None),
        "tail_loss_p5": tail_loss(rets, 5.0),
    }


if __name__ == "__main__":
    import json
    # A simple upward-drifting series with noise
    sample = [0.01, -0.005, 0.012, 0.008, -0.02, 0.015, 0.003, -0.001, 0.02, 0.005]
    print(json.dumps(summary(sample, periods_per_year=TRADING_DAYS), indent=2))
