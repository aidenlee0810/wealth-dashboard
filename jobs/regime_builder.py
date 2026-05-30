"""
jobs/regime_builder.py — market regime + sector/theme leadership (Plan §11 steps 8-9).

Two outputs, both computed from prices_daily + macro_daily that the snapshot
already populated (no extra network):

  compute_themes(date)  → sector_theme_daily   (15 themes + 11 sectors)
      member-average returns (1m/3m/6m), breadth (% > SMA50), relative strength
      vs SPY, and a lifecycle phase (emerging/leading/extended/fading/breakdown).

  compute_regime(date)  → market_regime_daily  (1 row)
      an 8-state classifier from SPY trend + market breadth + macro stress
      (VIX, HY OAS, yield curve), plus favored/avoided themes & sectors and the
      pillar-weight overrides Phase 4 scoring will consume.

This is a deliberately lightweight Python regime — the browser
market-regime.js remains the richer interactive classifier. They share the
same 8-state vocabulary so validation cuts line up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402

REGIME_VERSION = "3.0.0"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Pillar-weight overrides per regime (consumed by Phase 4 scoring).
REGIME_WEIGHTS = {
    "BROAD_RISK_ON":          {"bq": .20, "val": .10, "growth": .20, "tech": .25, "sec_theme": .15, "macro": .05, "risk": .05},
    "NARROW_THEME_LEADERSHIP":{"bq": .18, "val": .07, "growth": .20, "tech": .25, "sec_theme": .22, "macro": .03, "risk": .05},
    "ROTATION_MARKET":        {"bq": .22, "val": .15, "growth": .12, "tech": .18, "sec_theme": .20, "macro": .05, "risk": .08},
    "CHOPPY_RANGE":           {"bq": .25, "val": .15, "growth": .12, "tech": .15, "sec_theme": .15, "macro": .08, "risk": .10},
    "DEFENSIVE_ROTATION":     {"bq": .28, "val": .15, "growth": .08, "tech": .12, "sec_theme": .12, "macro": .10, "risk": .15},
    "MACRO_RISK_OFF":         {"bq": .28, "val": .12, "growth": .07, "tech": .10, "sec_theme": .08, "macro": .15, "risk": .20},
    "BROAD_RISK_OFF":         {"bq": .25, "val": .12, "growth": .06, "tech": .10, "sec_theme": .07, "macro": .15, "risk": .25},
    "RECOVERY_REBOUND":       {"bq": .18, "val": .15, "growth": .18, "tech": .25, "sec_theme": .14, "macro": .05, "risk": .05},
}


# ---------------------------------------------------------------------------
def _load(name: str) -> dict:
    p = DATA_DIR / name
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _ret(conn, ticker: str, date: str, lookback: int) -> Optional[float]:
    """Adj-close return over `lookback` trading rows ending at date."""
    rows = conn.execute(
        "SELECT COALESCE(adj_close, close) c FROM prices_daily "
        "WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT ?",
        (ticker, date, lookback + 1)).fetchall()
    if len(rows) <= lookback or rows[lookback]["c"] in (None, 0):
        return None
    return rows[0]["c"] / rows[lookback]["c"] - 1.0


def _above_sma50(conn, ticker: str, date: str) -> Optional[bool]:
    rows = conn.execute(
        "SELECT COALESCE(adj_close, close) c FROM prices_daily "
        "WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 50",
        (ticker, date)).fetchall()
    if len(rows) < 50:
        return None
    last = rows[0]["c"]
    sma = sum(r["c"] for r in rows) / len(rows)
    return last > sma if last and sma else None


def _basket_metrics(conn, tickers: list[str], date: str, spy_ret3m: Optional[float]) -> dict:
    r1, r3, r6, above, n = [], [], [], 0, 0
    for t in tickers:
        a = _ret(conn, t, date, 21)
        b = _ret(conn, t, date, 63)
        c = _ret(conn, t, date, 126)
        if a is not None:
            r1.append(a)
        if b is not None:
            r3.append(b)
        if c is not None:
            r6.append(c)
        sa = _above_sma50(conn, t, date)
        if sa is not None:
            n += 1
            above += 1 if sa else 0

    def avg(x):
        return sum(x) / len(x) if x else None
    ret1m, ret3m, ret6m = avg(r1), avg(r3), avg(r6)
    breadth = (above / n * 100.0) if n else None
    rel = (ret3m - spy_ret3m) if (ret3m is not None and spy_ret3m is not None) else None
    leader_count = sum(1 for x in r1 if x is not None and x > 0.05)
    return {"ret1m": ret1m, "ret3m": ret3m, "ret6m": ret6m,
            "breadth": breadth, "rel": rel, "leader_count": leader_count,
            "n_with_data": len(r1)}


def _phase(ret1m, ret3m, breadth) -> str:
    if ret1m is None or ret3m is None:
        return "neutral"
    if ret1m > 0.03 and ret3m <= 0.02:
        return "emerging"
    if ret1m > 0 and ret3m > 0.05:
        return "extended" if (breadth is not None and breadth < 45) else "leading"
    if ret1m > 0 and ret3m > 0:
        return "leading"
    if ret1m < 0 and ret3m > 0:
        return "fading"
    if ret1m < 0 and ret3m < 0:
        return "breakdown"
    return "neutral"


def _leadership_score(m: dict) -> float:
    s = 50.0
    if m["ret3m"] is not None:
        s += max(-20, min(20, m["ret3m"] * 80))
    if m["rel"] is not None:
        s += max(-15, min(15, m["rel"] * 80))
    if m["breadth"] is not None:
        s += (m["breadth"] - 50) / 50 * 10
    return round(max(0, min(100, s)), 1)


def compute_themes(date: str, log=None) -> dict:
    log = log or get_logger("regime_builder")
    themes = _load("theme_baskets.json").get("themes", {})
    sectors = _load("sector_holdings.json").get("sectors", {})
    written = 0
    with db.cloud() as conn:
        spy_ret3m = _ret(conn, "SPY", date, 63)
        rows_out = []
        for tid, meta in themes.items():
            m = _basket_metrics(conn, meta.get("tickers", []), date, spy_ret3m)
            rows_out.append(("theme", meta.get("name_en", tid), m))
        for etf, meta in sectors.items():
            m = _basket_metrics(conn, meta.get("holdings", []), date, spy_ret3m)
            rows_out.append(("sector", etf, m))

        for typ, name, m in rows_out:
            phase = _phase(m["ret1m"], m["ret3m"], m["breadth"])
            lead = _leadership_score(m)
            trend = round(max(0, min(100, 50 + (m["ret3m"] or 0) * 100)), 1)
            db.upsert(conn, "sector_theme_daily", {
                "date": date, "name": name, "type": typ,
                "ret1m": _r(m["ret1m"]), "ret3m": _r(m["ret3m"]), "ret6m": _r(m["ret6m"]),
                "trend_score": trend,
                "breadth_score": _r(m["breadth"], 1),
                "rel_strength": _r(m["rel"]),
                "leader_count": m["leader_count"],
                "phase": phase, "leadership_score": lead,
            }, conflict_cols=("date", "name", "type"))
            written += 1
    log.info("themes_done", date=date, rows=written)
    return {"rows": written}


def _r(x, nd=4):
    return round(x, nd) if x is not None else None


# ---------------------------------------------------------------------------
def _macro_latest(conn, series_id: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM macro_daily WHERE series_id=? AND date<=? "
        "ORDER BY date DESC LIMIT 1", (series_id, date)).fetchone()
    return row["value"] if row else None


def _market_breadth(conn, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT AVG(CASE WHEN price_vs_sma200 > 0 THEN 1.0 ELSE 0.0 END)*100 AS b, "
        "COUNT(*) n FROM features_daily WHERE date=? AND price_vs_sma200 IS NOT NULL",
        (date,)).fetchone()
    return row["b"] if row and row["n"] >= 20 else None


def compute_regime(date: str, log=None) -> dict:
    log = log or get_logger("regime_builder")
    with db.cloud() as conn:
        spy_ret20 = _ret(conn, "SPY", date, 20)
        spy_above50 = _above_sma50(conn, "SPY", date)
        spy_r1 = _ret(conn, "SPY", date, 21)
        spy_r3 = _ret(conn, "SPY", date, 63)
        breadth = _market_breadth(conn, date)
        vix = _macro_latest(conn, "VIXCLS", date)
        hy_oas = _macro_latest(conn, "BAMLH0A0HYM2", date)
        curve = _macro_latest(conn, "T10Y2Y", date)

        regime, secondary, confidence = _classify_regime(
            spy_ret20, spy_above50, spy_r1, spy_r3, breadth, vix, hy_oas)

        # favored/avoided/emerging/fading from leadership
        lead_rows = conn.execute(
            "SELECT name, type, phase, leadership_score FROM sector_theme_daily "
            "WHERE date=? ORDER BY leadership_score DESC", (date,)).fetchall()
        themes = [(r["name"], r["phase"], r["leadership_score"]) for r in lead_rows if r["type"] == "theme"]
        sectors = [(r["name"], r["phase"], r["leadership_score"]) for r in lead_rows if r["type"] == "sector"]

        favored_themes = [n for n, _, _ in themes[:4]]
        avoided_themes = [n for n, _, _ in themes[-3:]] if len(themes) > 4 else []
        favored_sectors = [n for n, _, _ in sectors[:3]]
        avoided_sectors = [n for n, _, _ in sectors[-2:]] if len(sectors) > 3 else []
        emerging = [n for n, ph, _ in themes if ph == "emerging"]
        fading = [n for n, ph, _ in themes if ph in ("fading", "breakdown")]

        evidence = {
            "spy_ret20": _r(spy_ret20), "spy_above_sma50": spy_above50,
            "spy_ret1m": _r(spy_r1), "spy_ret3m": _r(spy_r3),
            "market_breadth_pct": _r(breadth, 1),
            "vix": vix, "hy_oas": hy_oas, "yield_curve_t10y2y": curve,
        }
        db.upsert(conn, "market_regime_daily", {
            "date": date, "regime": regime, "secondary": secondary,
            "confidence": confidence,
            "evidence_json": json.dumps(evidence),
            "favored_themes_json": json.dumps(favored_themes),
            "avoided_themes_json": json.dumps(avoided_themes),
            "favored_sectors_json": json.dumps(favored_sectors),
            "avoided_sectors_json": json.dumps(avoided_sectors),
            "emerging_themes_json": json.dumps(emerging),
            "fading_themes_json": json.dumps(fading),
            "effective_weights_json": json.dumps(REGIME_WEIGHTS.get(regime, {})),
            "regime_version": REGIME_VERSION,
        }, conflict_cols=("date",))
    log.info("regime_done", date=date, regime=regime, confidence=confidence)
    return {"regime": regime, "secondary": secondary, "confidence": confidence,
            "evidence": evidence}


def _classify_regime(spy_ret20, spy_above50, spy_r1, spy_r3, breadth, vix, hy_oas):
    """Map evidence → one of 8 regime states + confidence (0-1)."""
    # Macro stress flags
    high_vix = vix is not None and vix >= 28
    wide_credit = hy_oas is not None and hy_oas >= 5.0
    elevated_vix = vix is not None and vix >= 20

    up = (spy_above50 is True) and (spy_ret20 is not None and spy_ret20 > 0)
    down = (spy_above50 is False) or (spy_ret20 is not None and spy_ret20 < -0.02)
    broad = breadth is not None and breadth >= 55
    narrow = breadth is not None and breadth < 40

    # Risk-off family
    if down and (high_vix and wide_credit):
        return "MACRO_RISK_OFF", "broad_selloff", 0.85
    if down and (high_vix or wide_credit):
        return "BROAD_RISK_OFF", "elevated_stress", 0.7
    if down and not elevated_vix:
        return "DEFENSIVE_ROTATION", "orderly_pullback", 0.6

    # Rebound: SPY just turned up after weakness
    if up and (spy_r3 is not None and spy_r3 < 0) and (spy_r1 is not None and spy_r1 > 0.03):
        return "RECOVERY_REBOUND", "rebound_off_lows", 0.55

    # Risk-on family
    if up and broad and not elevated_vix:
        return "BROAD_RISK_ON", "healthy_trend", 0.8
    if up and narrow:
        return "NARROW_THEME_LEADERSHIP", "mega_cap_led", 0.65
    if up:
        return "ROTATION_MARKET", "mixed_leadership", 0.55

    # Default: range-bound chop
    return "CHOPPY_RANGE", "no_clear_trend", 0.5


if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="regime_builder smoke test")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    compute_themes(args.date)
    out = compute_regime(args.date)
    print(json.dumps(out, indent=2, default=str))
