"""
jobs/sizing.py — position sizing methods + risk budgeting (Plan §29).

Four sizing methods the user can choose from, plus risk-contribution analysis.
All stdlib (no numpy) — small N, closed-form or simple iteration.

  equal_weight        1/N — ignores noisy estimates
  inverse_volatility  w ∝ 1/σ — down-weight volatile names
  risk_parity         equal risk contribution (constant-correlation model)
  fractional_kelly    0.25 × edge/variance — statistically optimal, safety-scaled

Correlation model: rather than require a full covariance matrix (Phase 7), we use
a single average pairwise correlation `rho` (0 = independent). Portfolio variance
is then  Σ wᵢ²σᵢ²  +  ρ Σ_{i≠j} wᵢwⱼσᵢσⱼ . This is a defensible, transparent
1-parameter risk model for budgeting; full FF/covariance risk is Phase 7 (§30).
"""

from __future__ import annotations

from typing import Optional

RISK_FREE_RATE = 0.043          # ~ current T-bill; overridable
MAX_POSITION_WEIGHT = 0.08      # single-name cap (mirrors governor §7)


# ---------------------------------------------------------------------------
# Sizing methods
# ---------------------------------------------------------------------------
def equal_weight(tickers: list[str]) -> dict:
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}


def inverse_volatility(vols: dict) -> dict:
    """weight ∝ 1/σ, normalised. vols: {ticker: annualised_vol}."""
    inv = {t: (1.0 / v) for t, v in vols.items() if v and v > 0}
    s = sum(inv.values())
    if s <= 0:
        return equal_weight(list(vols))
    return {t: x / s for t, x in inv.items()}


def fractional_kelly(edge: float, variance: float, *, fraction: float = 0.25,
                     cap: float = MAX_POSITION_WEIGHT) -> float:
    """Fractional-Kelly single-position weight.

    edge = expected_excess_return (over rf), variance = return variance.
    Full Kelly (edge/variance) is too aggressive → scale by `fraction`."""
    if variance is None or variance <= 0 or edge is None or edge <= 0:
        return 0.0
    kelly = edge / variance
    return round(min(kelly * fraction, cap), 4)


def portfolio_vol(weights: dict, vols: dict, rho: float = 0.0) -> float:
    """Annualised portfolio volatility under the constant-correlation model."""
    items = [(t, weights.get(t, 0.0), vols.get(t)) for t in weights]
    var = 0.0
    for _, w, s in items:
        if s is None:
            continue
        var += (w * s) ** 2
    for i in range(len(items)):
        for j in range(len(items)):
            if i == j:
                continue
            _, wi, si = items[i]
            _, wj, sj = items[j]
            if si is None or sj is None:
                continue
            var += rho * wi * wj * si * sj
    return var ** 0.5 if var > 0 else 0.0


def risk_contribution(weights: dict, vols: dict, rho: float = 0.0) -> dict:
    """Each asset's share of total portfolio risk (sums to ~1.0).

    RC_i = w_i · (Σw)_i / σ_p , where (Σw)_i is the marginal covariance term."""
    sigma_p = portfolio_vol(weights, vols, rho)
    if sigma_p <= 0:
        return {t: 0.0 for t in weights}
    out = {}
    for t in weights:
        wi, si = weights.get(t, 0.0), vols.get(t)
        if si is None:
            out[t] = 0.0
            continue
        # marginal contribution: w_i σ_i² + ρ Σ_{j≠i} w_j σ_i σ_j
        marg = wi * si * si
        for u in weights:
            if u == t:
                continue
            sj = vols.get(u)
            if sj is None:
                continue
            marg += rho * weights.get(u, 0.0) * si * sj
        out[t] = round(wi * marg / (sigma_p ** 2), 4)
    return out


def risk_parity(vols: dict, rho: float = 0.0, *, iters: int = 100) -> dict:
    """Equal-risk-contribution weights via fixed-point iteration.

    Under zero correlation this reduces to inverse-volatility; with ρ>0 it
    iterates toward equal RC. Long-only, normalised."""
    tickers = [t for t, v in vols.items() if v and v > 0]
    if not tickers:
        return {}
    w = inverse_volatility({t: vols[t] for t in tickers})
    for _ in range(iters):
        rc = risk_contribution(w, vols, rho)
        target = 1.0 / len(tickers)
        # nudge weights toward equal RC
        new = {}
        for t in tickers:
            adj = (target / rc[t]) ** 0.5 if rc.get(t, 0) > 0 else 1.0
            new[t] = w[t] * adj
        s = sum(new.values())
        w = {t: x / s for t, x in new.items()}
    return {t: round(x, 4) for t, x in w.items()}


def vol_target(weights: dict, vols: dict, *, target_vol: float = 0.15,
               rho: float = 0.0, max_leverage: float = 1.0) -> dict:
    """Scale a weight vector so the portfolio hits target_vol (capped at
    max_leverage gross). Cash is the residual (1 - Σw)."""
    cur = portfolio_vol(weights, vols, rho)
    if cur <= 0:
        return dict(weights)
    scale = min(target_vol / cur, max_leverage / max(sum(weights.values()), 1e-9))
    return {t: round(w * scale, 4) for t, w in weights.items()}


def apply_method(method: str, *, tickers: list[str], vols: Optional[dict] = None,
                 edges: Optional[dict] = None, variances: Optional[dict] = None,
                 rho: float = 0.0) -> dict:
    """Dispatch helper for the UI's sizing-method selector."""
    vols = vols or {}
    if method == "equal":
        return equal_weight(tickers)
    if method == "inverse_vol":
        return inverse_volatility({t: vols.get(t) for t in tickers})
    if method == "risk_parity":
        return risk_parity({t: vols.get(t) for t in tickers if vols.get(t)}, rho)
    if method == "fractional_kelly":
        edges, variances = edges or {}, variances or {}
        raw = {t: fractional_kelly(edges.get(t, 0.0), variances.get(t, 0.0))
               for t in tickers}
        return raw
    raise ValueError(f"unknown sizing method: {method}")


if __name__ == "__main__":
    import json
    vols = {"NVDA": 0.50, "AAPL": 0.25, "KO": 0.15}
    print("equal       :", equal_weight(list(vols)))
    print("inverse_vol :", inverse_volatility(vols))
    print("risk_parity :", risk_parity(vols, rho=0.3))
    rp = risk_parity(vols, rho=0.3)
    print("RC(risk_par):", risk_contribution(rp, vols, rho=0.3))
    print("kelly(0.08,0.25):", fractional_kelly(0.08, 0.25))
    print("vol_target  :", vol_target({"NVDA": .4, "AAPL": .4, "KO": .2}, vols, target_vol=0.15, rho=0.3))
