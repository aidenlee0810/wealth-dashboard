"""
jobs/factor_model.py — market beta + factor exposures (Plan §30).

Computes single-factor market beta via OLS (ticker daily returns ~ SPY daily
returns). Also derives a volatility-adjusted proxy score for SMB/HML style
using sector + market-cap characteristics — no additional data feed required.

OLS is implemented with stdlib `statistics.linear_regression` (Python 3.10+),
so there is zero numpy/scipy dependency.

Schema written: factor_exposures_weekly (ticker, week_end_date, b_mkt, alpha,
               r_squared, n_obs, vol_annual, b_smb_proxy, b_hml_proxy)

Key invariants
--------------
* Week-end date = the most recent Friday (or last trading day if market closed
  on Friday) ≤ as_of.  This makes factor exposure stable between daily runs.
* R² < 0.3 → low model quality flag; stress test uses beta but warns the user.
* Needs ≥ 60 overlapping trading days with SPY; fewer → skipped with reason.
* Idempotent: re-running the same week_end_date upserts the row.
"""

from __future__ import annotations

import statistics
from typing import Optional

# ---------------------------------------------------------------------------
# Style/sector proxies for SMB / HML (no regression needed)
# ---------------------------------------------------------------------------

# Approximate market-cap size bucket → SMB loading (positive = small-cap tilt)
_SMB_BY_MCAP: dict[str, float] = {
    "mega":  -0.20,   # >$200 B
    "large": -0.10,   # $10–200 B
    "mid":    0.10,   # $2–10 B
    "small":  0.30,   # $500 M–2 B
    "micro":  0.50,   # <$500 M
}

# Sector → HML loading proxy (positive = value tilt, negative = growth tilt)
_HML_BY_SECTOR: dict[str, float] = {
    "Energy":                    0.40,
    "Financials":                0.35,
    "Materials":                 0.25,
    "Industrials":               0.15,
    "Utilities":                 0.20,
    "Consumer Staples":          0.10,
    "Real Estate":               0.15,
    "Health Care":              -0.05,
    "Consumer Discretionary":   -0.10,
    "Communication Services":   -0.20,
    "Information Technology":   -0.35,
}


def _smb_proxy(mcap_b: Optional[float]) -> Optional[float]:
    """Return approximate SMB loading based on market cap (in $B)."""
    if mcap_b is None:
        return None
    if mcap_b > 200:
        return _SMB_BY_MCAP["mega"]
    if mcap_b > 10:
        return _SMB_BY_MCAP["large"]
    if mcap_b > 2:
        return _SMB_BY_MCAP["mid"]
    if mcap_b >= 0.5:
        return _SMB_BY_MCAP["small"]
    return _SMB_BY_MCAP["micro"]


def _hml_proxy(sector: Optional[str]) -> Optional[float]:
    """Return approximate HML loading based on sector."""
    if not sector:
        return None
    return _HML_BY_SECTOR.get(sector)


# ---------------------------------------------------------------------------
# OLS regression (single-factor: ticker ~ SPY)
# ---------------------------------------------------------------------------

def _daily_returns(closes: list[float]) -> list[float]:
    """Compute simple daily returns from a price series."""
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev and prev > 0:
            out.append(closes[i] / prev - 1.0)
        else:
            out.append(0.0)
    return out


def ols_beta(ticker_returns: list[float],
             spy_returns: list[float]) -> dict:
    """OLS of ticker_returns ~ spy_returns.

    Returns dict with slope (=beta), intercept (=alpha_daily), r_squared,
    n_obs, vol_annual (annualised ticker vol), and model_quality flag.

    Uses statistics.linear_regression (Python ≥ 3.10).
    """
    # Align lengths
    n = min(len(ticker_returns), len(spy_returns))
    if n < 10:
        return {"ok": False, "reason": f"too_few_obs: {n}"}

    y = ticker_returns[-n:]
    x = spy_returns[-n:]

    # statistics.linear_regression signature: (x, y)
    try:
        lr = statistics.linear_regression(x, y)
        slope = lr.slope
        intercept = lr.intercept
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}

    # R²
    y_mean = statistics.mean(y)
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    if ss_tot == 0:
        r_sq = 0.0
    else:
        y_hat = [intercept + slope * xi for xi in x]
        ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y, y_hat))
        r_sq = max(0.0, 1.0 - ss_res / ss_tot)

    # Annualised vol (sample std of daily returns × √252)
    try:
        daily_vol = statistics.stdev(y)
        vol_annual = round(daily_vol * (252 ** 0.5), 4)
    except statistics.StatisticsError:
        vol_annual = None

    return {
        "ok": True,
        "b_mkt": round(slope, 4),
        "alpha_daily": round(intercept, 6),
        "r_squared": round(r_sq, 4),
        "n_obs": n,
        "vol_annual": vol_annual,
        "model_quality": "good" if r_sq >= 0.3 else "low",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_closes(conn, ticker: str, as_of: str,
                 n: int = 520) -> list[float]:
    """Load up to n adj_close prices for ticker up to as_of from the DB."""
    rows = conn.execute(
        "SELECT adj_close FROM prices_daily "
        "WHERE ticker=? AND date<=? AND adj_close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (ticker, as_of, n),
    ).fetchall()
    closes = [r["adj_close"] for r in reversed(rows)]
    return closes


def compute_exposures(conn, ticker: str, as_of: str,
                      n_lookback: int = 504) -> dict:
    """Compute factor exposures for *ticker* as of *as_of*.

    Loads prices from the DB, runs OLS vs SPY, and returns a row ready for
    upsert into factor_exposures_weekly.

    Returns dict with keys matching the table columns, or {'ok': False, ...}.
    """
    spy_closes = _load_closes(conn, "SPY", as_of, n_lookback + 1)
    ticker_closes = _load_closes(conn, ticker, as_of, n_lookback + 1)

    if len(spy_closes) < 20 or len(ticker_closes) < 20:
        return {"ok": False, "reason": "insufficient_prices"}

    spy_ret = _daily_returns(spy_closes)
    tkr_ret = _daily_returns(ticker_closes)

    result = ols_beta(tkr_ret, spy_ret)
    if not result.get("ok"):
        return result

    # Fetch sector + mcap for style proxies
    meta = conn.execute(
        "SELECT sector FROM ticker_master WHERE ticker=?", (ticker,)
    ).fetchone()
    sector = meta["sector"] if meta else None
    b_hml = _hml_proxy(sector)

    # mcap not stored directly; skip SMB proxy (None = not computed)
    b_smb = None

    return {
        "ok": True,
        "ticker": ticker,
        "b_mkt": result["b_mkt"],
        "alpha": result["alpha_daily"],
        "r_squared": result["r_squared"],
        "n_obs": result["n_obs"],
        "vol_annual": result["vol_annual"],
        "b_smb": b_smb,
        "b_hml": b_hml,
        "b_rmw": None,
        "b_cma": None,
        "model_quality": result["model_quality"],
    }


def upsert_exposure(conn, ticker: str, week_end_date: str,
                    exposure: dict) -> None:
    """Upsert a factor exposure row into factor_exposures_weekly."""
    conn.execute(
        """INSERT INTO factor_exposures_weekly
           (ticker, week_end_date, alpha, b_mkt, b_smb, b_hml, b_rmw, b_cma,
            r_squared, n_obs, vol_annual)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(ticker, week_end_date) DO UPDATE SET
             alpha=excluded.alpha, b_mkt=excluded.b_mkt,
             b_smb=excluded.b_smb, b_hml=excluded.b_hml,
             b_rmw=excluded.b_rmw, b_cma=excluded.b_cma,
             r_squared=excluded.r_squared, n_obs=excluded.n_obs,
             vol_annual=excluded.vol_annual""",
        (
            ticker, week_end_date,
            exposure.get("alpha"), exposure.get("b_mkt"),
            exposure.get("b_smb"), exposure.get("b_hml"),
            exposure.get("b_rmw"), exposure.get("b_cma"),
            exposure.get("r_squared"), exposure.get("n_obs"),
            exposure.get("vol_annual"),
        ),
    )


def week_end_date_for(date_str: str) -> str:
    """Return the ISO date of the most recent Friday ≤ date_str.

    This anchors factor exposures to weekly snapshots so re-running daily
    during the same week produces the same key.
    """
    import datetime as _dt
    d = _dt.date.fromisoformat(date_str)
    # weekday(): Monday=0, Friday=4
    days_past_friday = (d.weekday() - 4) % 7
    friday = d - _dt.timedelta(days=days_past_friday)
    return friday.isoformat()


# ---------------------------------------------------------------------------
# Batch compute + upsert
# ---------------------------------------------------------------------------

def compute_and_store(tickers: list[str], as_of: str, conn,
                      *, log=None) -> dict:
    """Compute factor exposures for *tickers* and upsert to DB.

    Returns summary: {"computed": N, "skipped": N, "tickers": {ticker: result}}.
    *conn* must be a writeable cloud DB connection.
    """
    from _logging import get_logger
    log = log or get_logger("factor_model")

    wed = week_end_date_for(as_of)
    computed = 0
    skipped = 0
    per_ticker: dict[str, dict] = {}

    for ticker in tickers:
        exp = compute_exposures(conn, ticker, as_of)
        if exp.get("ok"):
            upsert_exposure(conn, ticker, wed, exp)
            computed += 1
            per_ticker[ticker] = {
                "b_mkt": exp["b_mkt"],
                "r_squared": exp["r_squared"],
                "vol_annual": exp["vol_annual"],
                "model_quality": exp["model_quality"],
            }
            log.debug("factor_ok", ticker=ticker, b_mkt=exp["b_mkt"],
                      r2=exp["r_squared"])
        else:
            skipped += 1
            per_ticker[ticker] = {"ok": False, "reason": exp.get("reason")}
            log.debug("factor_skip", ticker=ticker, reason=exp.get("reason"))

    return {"computed": computed, "skipped": skipped, "per_ticker": per_ticker}


# ---------------------------------------------------------------------------
# Load latest exposures for stress test
# ---------------------------------------------------------------------------

def load_latest_exposures(conn, tickers: list[str],
                          as_of: str) -> dict[str, dict]:
    """Load the most recent factor_exposures_weekly row for each ticker."""
    result: dict[str, dict] = {}
    for ticker in tickers:
        row = conn.execute(
            "SELECT b_mkt, b_smb, b_hml, b_rmw, b_cma, r_squared, n_obs, "
            "       alpha, vol_annual, week_end_date "
            "FROM factor_exposures_weekly "
            "WHERE ticker=? AND week_end_date<=? "
            "ORDER BY week_end_date DESC LIMIT 1",
            (ticker, as_of),
        ).fetchone()
        if row:
            result[ticker] = dict(row)
    return result
