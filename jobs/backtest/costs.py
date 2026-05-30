"""
jobs/backtest/costs.py — transaction-cost & slippage model (Plan §10).

Modern brokers charge zero commission, but trading still costs: you cross the
spread and move the price (slippage). A backtest that ignores this overstates
turnover-heavy strategies. We charge, per unit of turnover:

    cost = turnover × (slippage_bps + commission_bps + spread_bps/2) / 10_000

`turnover` is the fraction of the portfolio traded at a rebalance (0.0–2.0;
1.0 = fully replace the book). Half the spread is charged because you pay the
half-spread on each side and turnover already counts both legs of a rebalance.

Defaults (Plan §10): commission 0 bps, slippage 5 bps, spread 10 bps (used
when a ticker's actual bid-ask spread is unknown).
"""

from __future__ import annotations

COMMISSION_BPS = 0.0     # modern zero-commission brokers
SLIPPAGE_BPS = 5.0       # 0.05% price impact per trade (placeholder, §10)
DEFAULT_SPREAD_BPS = 10.0  # 0.10% when the ticker's real spread is unknown


def transaction_cost(turnover: float, *, slippage_bps: float = SLIPPAGE_BPS,
                     commission_bps: float = COMMISSION_BPS,
                     spread_bps: float = DEFAULT_SPREAD_BPS) -> float:
    """Cost as a fraction of portfolio value for a rebalance of `turnover`."""
    if turnover <= 0:
        return 0.0
    per_unit_bps = slippage_bps + commission_bps + spread_bps / 2.0
    return turnover * per_unit_bps / 10_000.0


def apply_costs(gross_return: float, turnover: float, **kw) -> float:
    """Net period return after deducting the cost of `turnover`."""
    return gross_return - transaction_cost(turnover, **kw)


def turnover_from_weights(prev: dict, target: dict) -> float:
    """L1 turnover = Σ |w_target − w_prev| across the union of tickers.

    A fresh entry of weight w contributes w; a full exit contributes w; a
    rebalance contributes the absolute change. Cash (the residual) is ignored."""
    keys = set(prev) | set(target)
    return sum(abs(target.get(k, 0.0) - prev.get(k, 0.0)) for k in keys)
