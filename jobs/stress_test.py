"""
jobs/stress_test.py — portfolio stress test scenarios (Plan §9, §30).

Two types of scenarios:

  historical   Use actual SPY return for the period from prices_daily.
               For each ticker: actual return if it traded the full period,
               else fallback to beta × spy_return (beta from factor_model).

  hypothetical Parameterised shock (e.g., SPY -10%) applied via beta:
               estimated_return = alpha + beta × shock_return.

Scenarios defined in STRESS_SCENARIOS (9 total: 7 historical, 2 hypothetical).
All stdlib, no numpy.

Public API
----------
  ticker_stress(conn, tickers, as_of)  → {ticker: {scenario: return_estimate}}
  portfolio_stress(ticker_stress, weights) → {scenario: portfolio_loss_pct}
  build_stress_view(conn, tickers, as_of) → dict suitable for JSON view
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Scenario catalogue
# ---------------------------------------------------------------------------

STRESS_SCENARIOS: dict[str, dict] = {
    # ── Hypothetical ────────────────────────────────────────────────────────
    "hypo_spy_m10": {
        "label": "SPY -10% (가상)",
        "type": "hypothetical",
        "spy_shock": -0.10,
        "qqq_shock": -0.12,
        "description": "시장 조정 시나리오: SPY 10% 하락",
    },
    "hypo_spy_m20": {
        "label": "SPY -20% (가상)",
        "type": "hypothetical",
        "spy_shock": -0.20,
        "qqq_shock": -0.24,
        "description": "약세장 시나리오: SPY 20% 하락",
    },
    # ── Historical ──────────────────────────────────────────────────────────
    "covid_crash_2020": {
        "label": "COVID 급락 2020",
        "type": "historical",
        "period_start": "2020-02-19",
        "period_end":   "2020-03-23",
        "spy_fallback": -0.340,
        "qqq_fallback": -0.285,
        "description":  "2020-02-19 ~ 2020-03-23 (33일, SPY -34%)",
    },
    "rate_shock_2022": {
        "label": "금리 충격 2022",
        "type": "historical",
        "period_start": "2022-01-03",
        "period_end":   "2022-06-16",
        "spy_fallback": -0.230,
        "qqq_fallback": -0.340,
        "description":  "2022-01-03 ~ 2022-06-16 (116일, 연준 급격한 금리 인상)",
    },
    "tech_bubble_2000": {
        "label": "닷컴 버블 붕괴 2000",
        "type": "historical",
        "period_start": "2000-03-24",
        "period_end":   "2002-10-09",
        "spy_fallback": -0.490,
        "qqq_fallback": -0.830,
        "description":  "2000-03-24 ~ 2002-10-09 (2.5년, SPY -49%, QQQ -83%)",
    },
    "gfc_2008": {
        "label": "금융위기 2008–2009",
        "type": "historical",
        "period_start": "2007-10-09",
        "period_end":   "2009-03-09",
        "spy_fallback": -0.570,
        "qqq_fallback": -0.530,
        "description":  "2007-10-09 ~ 2009-03-09 (17개월, SPY -57%)",
    },
    "volpocalypse_2018": {
        "label": "변동성 폭발 2018-02",
        "type": "historical",
        "period_start": "2018-02-02",
        "period_end":   "2018-02-09",
        "spy_fallback": -0.088,
        "qqq_fallback": -0.086,
        "description":  "2018-02-02 ~ 2018-02-09 (5일, VIX +84%)",
    },
    "aug_2024_carry_unwind": {
        "label": "엔 캐리 청산 2024-08",
        "type": "historical",
        "period_start": "2024-08-02",
        "period_end":   "2024-08-05",
        "spy_fallback": -0.085,
        "qqq_fallback": -0.095,
        "description":  "2024-08-02 ~ 2024-08-05 (2일, 일본 엔 캐리 트레이드 청산)",
    },
}


# ---------------------------------------------------------------------------
# Historical spy return from prices_daily
# ---------------------------------------------------------------------------

def _spy_return_for_period(conn, start: str, end: str) -> Optional[float]:
    """Compute SPY total return between start and end using adj_close."""
    rows = conn.execute(
        "SELECT date, adj_close FROM prices_daily "
        "WHERE ticker='SPY' AND date>=? AND date<=? "
        "ORDER BY date",
        (start, end),
    ).fetchall()
    if len(rows) < 2:
        return None
    p0 = rows[0]["adj_close"]
    p1 = rows[-1]["adj_close"]
    if not p0 or p0 <= 0:
        return None
    return round(p1 / p0 - 1.0, 4)


def _ticker_return_for_period(conn, ticker: str,
                               start: str, end: str) -> Optional[float]:
    """Actual return for ticker over the period, or None if not available."""
    rows = conn.execute(
        "SELECT date, adj_close FROM prices_daily "
        "WHERE ticker=? AND date>=? AND date<=? AND adj_close IS NOT NULL "
        "ORDER BY date",
        (ticker, start, end),
    ).fetchall()
    if len(rows) < 2:
        return None
    p0 = rows[0]["adj_close"]
    p1 = rows[-1]["adj_close"]
    if not p0 or p0 <= 0:
        return None
    return round(p1 / p0 - 1.0, 4)


# ---------------------------------------------------------------------------
# Per-ticker stress estimation
# ---------------------------------------------------------------------------

def _estimate_return(scenario: dict, beta: Optional[float],
                     r_squared: Optional[float],
                     alpha: Optional[float],
                     actual: Optional[float]) -> dict:
    """Produce stress return estimate for one ticker / one scenario."""
    stype = scenario["type"]

    if actual is not None:
        return {"estimated_return": round(actual, 4), "method": "actual"}

    if beta is None:
        # No model — use SPY return scaled by 1.0 (market proxy)
        shock = scenario.get("spy_shock") or scenario.get("spy_fallback") or 0.0
        return {"estimated_return": round(shock, 4), "method": "spy_proxy",
                "warning": "no_beta"}

    shock = scenario.get("spy_shock") or scenario.get("spy_fallback") or 0.0
    alpha_d = alpha or 0.0
    # For historical, scale alpha by approximate trading days
    if stype == "historical":
        # Scale alpha: use 0 (unknown holding period for historical)
        est = round(beta * shock, 4)
    else:
        est = round(alpha_d + beta * shock, 4)

    quality = "low_r2" if (r_squared is not None and r_squared < 0.3) else "beta"
    return {"estimated_return": est, "method": quality}


def ticker_stress(conn, tickers: list[str], as_of: str,
                  factor_exposures: Optional[dict] = None) -> dict[str, dict]:
    """Compute stress test estimates for each ticker across all scenarios.

    *factor_exposures*: preloaded dict from factor_model.load_latest_exposures
    (pass it in to avoid re-querying for each ticker).

    Returns:
      {
        ticker: {
          "beta": float | None,
          "r_squared": float | None,
          "vol_annual": float | None,
          "scenarios": {
            scenario_key: {"estimated_return": float, "method": str},
            ...
          }
        },
        ...
      }
    """
    if factor_exposures is None:
        from factor_model import load_latest_exposures
        factor_exposures = load_latest_exposures(conn, tickers, as_of)

    result: dict[str, dict] = {}

    for ticker in tickers:
        exp = factor_exposures.get(ticker, {})
        beta = exp.get("b_mkt")
        r2 = exp.get("r_squared")
        alpha = exp.get("alpha")

        # vol_annual comes from the factor model (stored in factor_exposures_weekly)
        vol = exp.get("vol_annual")

        scenarios_out: dict[str, dict] = {}

        for key, sc in STRESS_SCENARIOS.items():
            actual = None
            if sc["type"] == "historical":
                # Try to get actual return from prices_daily
                actual_spy = _spy_return_for_period(
                    conn, sc["period_start"], sc["period_end"])
                # Use fallback if actual not in DB
                spy_for_est = actual_spy if actual_spy is not None else sc.get("spy_fallback")
                actual = _ticker_return_for_period(
                    conn, ticker, sc["period_start"], sc["period_end"])

                # Enrich scenario with actual spy (for display)
                effective_sc = dict(sc, spy_fallback=spy_for_est)
            else:
                effective_sc = sc

            scenarios_out[key] = _estimate_return(
                effective_sc, beta, r2, alpha, actual)

        result[ticker] = {
            "beta": beta,
            "r_squared": r2,
            "vol_annual": vol,
            "scenarios": scenarios_out,
        }

    return result


# ---------------------------------------------------------------------------
# Portfolio-level aggregation
# ---------------------------------------------------------------------------

def portfolio_stress(ticker_stress_data: dict[str, dict],
                     weights: dict[str, float]) -> dict[str, dict]:
    """Aggregate per-ticker stress into portfolio totals.

    *weights*: {ticker: weight_fraction} — need not sum to 1.0 (raw weights OK).

    Returns:
      {
        scenario_key: {
          "portfolio_return": float,
          "top_contributors": [{"ticker": t, "contribution": c, "weight": w}, ...],
          "beta_weighted": float,
        }
      }
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {}

    out: dict[str, dict] = {}

    for key in STRESS_SCENARIOS:
        contribs: list[dict] = []
        port_ret = 0.0

        for ticker, w in weights.items():
            w_norm = w / total_weight
            td = ticker_stress_data.get(ticker)
            if not td:
                continue
            sc = td["scenarios"].get(key)
            if not sc:
                continue
            er = sc["estimated_return"]
            contribution = w_norm * er
            port_ret += contribution
            contribs.append({
                "ticker": ticker,
                "weight": round(w_norm, 4),
                "estimated_return": round(er, 4),
                "contribution": round(contribution, 4),
                "method": sc.get("method"),
            })

        contribs.sort(key=lambda x: x["contribution"])  # worst first
        out[key] = {
            "portfolio_return": round(port_ret, 4),
            "top_contributors": contribs[:5],
        }

    return out


# ---------------------------------------------------------------------------
# Full view builder helper
# ---------------------------------------------------------------------------

def build_stress_view(conn, tickers: list[str], as_of: str,
                      *, log=None) -> dict:
    """Build the complete stress-test view dict for latest_stress_test.json.

    Returns a dict with:
      snapshot_date, scenarios (metadata), ticker_stress (per-ticker results)
    """
    from _logging import get_logger
    from factor_model import load_latest_exposures
    log = log or get_logger("stress_test")

    log.info("stress_view_start", tickers=len(tickers), as_of=as_of)

    exp = load_latest_exposures(conn, tickers, as_of)
    ts = ticker_stress(conn, tickers, as_of, factor_exposures=exp)

    # Enrich scenarios with actual SPY returns from prices_daily
    scenarios_meta: dict[str, dict] = {}
    for key, sc in STRESS_SCENARIOS.items():
        entry = {
            "label": sc["label"],
            "type": sc["type"],
            "description": sc["description"],
        }
        if sc["type"] == "historical":
            actual_spy = _spy_return_for_period(
                conn, sc["period_start"], sc["period_end"])
            entry["period_start"] = sc["period_start"]
            entry["period_end"] = sc["period_end"]
            entry["spy_return"] = actual_spy if actual_spy is not None else sc.get("spy_fallback")
            entry["spy_source"] = "actual" if actual_spy is not None else "fallback"
        else:
            entry["spy_return"] = sc.get("spy_shock")
            entry["qqq_return"] = sc.get("qqq_shock")
        scenarios_meta[key] = entry

    log.info("stress_view_done", tickers=len(ts))

    return {
        "snapshot_date": as_of,
        "scenarios": scenarios_meta,
        "ticker_stress": {
            ticker: {
                "beta": td.get("beta"),
                "r_squared": td.get("r_squared"),
                "vol_annual": td.get("vol_annual"),
                "scenarios": td.get("scenarios", {}),
            }
            for ticker, td in ts.items()
        },
    }
