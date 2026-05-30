"""
jobs/scorers/technical.py — the Technical Analyst (Plan §5).

Owns the price-based pillar: trend (SMA50/200), momentum (20/60/120D returns),
52-week-high proximity, RVOL, RSI(14) → a 0-100 tech score, a TIMING-style
state label, and a data-quality score from history depth/freshness.

The pure math (sma/pct_return/rsi/compute_tech/compute_dq) lives here as the
single source of truth; feature_builder re-exports it for backward compatibility
with the Phase 3 test-suite.
"""

from __future__ import annotations

from typing import Optional

from .base import PillarScore, NA_NO_PRICES


# ---------------------------------------------------------------------------
# Pure technical math (stdlib only)
# ---------------------------------------------------------------------------
def sma(values: list[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def pct_return(values: list[float], n: int) -> Optional[float]:
    if len(values) <= n or values[-1 - n] == 0:
        return None
    return values[-1] / values[-1 - n] - 1.0


def rsi(values: list[float], n: int = 14) -> Optional[float]:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain, avg_loss = gains / n, losses / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_tech(closes: list[float], highs: list[float],
                 volumes: list[float]) -> dict:
    """Technical feature bundle from aligned series (oldest→newest)."""
    last = closes[-1]
    s50 = sma(closes, 50)
    s200 = sma(closes, 200)
    r20 = pct_return(closes, 20)
    r60 = pct_return(closes, 60)
    r120 = pct_return(closes, 120)
    rsi14 = rsi(closes, 14)

    pv50 = (last / s50 - 1.0) if s50 else None
    pv200 = (last / s200 - 1.0) if s200 else None
    slope200 = None
    if len(closes) >= 220:
        s200_prev = sum(closes[-220:-20]) / 200
        slope200 = (s200 - s200_prev) if s200 else None

    win52 = highs[-252:] if len(highs) >= 252 else highs
    hi52 = max(win52) if win52 else last
    proximity = last / hi52 if hi52 else 1.0

    avgvol20 = sma(volumes, 20)
    rvol = (volumes[-1] / avgvol20) if (avgvol20 and avgvol20 > 0) else None

    score = 50.0
    if pv50 is not None:
        score += 12 if pv50 > 0 else -12
    if pv200 is not None:
        score += 12 if pv200 > 0 else -12
    if slope200 is not None:
        score += 6 if slope200 > 0 else -6
    if r60 is not None:
        score += _clamp(r60 * 60, -16, 16)
    if proximity is not None:
        score += _clamp((proximity - 0.85) / 0.15 * 10, -6, 10)
    if rvol is not None and rvol > 1.5 and (r20 or 0) > 0:
        score += 4
    if rsi14 is not None and rsi14 > 80:
        score -= 5
    score = round(_clamp(score, 0, 100), 1)

    state = _classify_state(pv50, pv200, slope200, r20, proximity, rvol)
    return {
        "tech_score": score, "state": state, "rsi_14": rsi14,
        "price_vs_sma50": round(pv50, 4) if pv50 is not None else None,
        "price_vs_sma200": round(pv200, 4) if pv200 is not None else None,
        "ret_20d": round(r20, 4) if r20 is not None else None,
        "ret_60d": round(r60, 4) if r60 is not None else None,
        "ret_120d": round(r120, 4) if r120 is not None else None,
        "proximity_52w": round(proximity, 4) if proximity is not None else None,
        "rvol": round(rvol, 3) if rvol is not None else None,
    }


def _classify_state(pv50, pv200, slope200, r20, proximity, rvol) -> str:
    above50 = pv50 is not None and pv50 > 0
    above200 = pv200 is not None and pv200 > 0
    if proximity is not None and proximity >= 0.985 and rvol is not None and rvol > 1.5:
        return "BREAKOUT"
    if above50 and above200 and (r20 or 0) > 0:
        return "UPTREND"
    if above200 and not above50:
        return "PULLBACK"
    if (pv200 is not None and pv200 < 0) and (slope200 is not None and slope200 < 0):
        return "DOWNTREND"
    if above200:
        return "BASE"
    return "NEUTRAL"


def compute_dq(n_bars: int, latest_date: str, feature_date: str) -> float:
    """Data-quality score from history depth + freshness (§33 IPO handling)."""
    from market_calendar import trading_days_between
    dq = 100.0
    if n_bars < 200:
        dq -= 8
    if n_bars < 60:
        dq -= 15
    if n_bars < 20:
        dq -= 30
    try:
        gap = abs(trading_days_between(latest_date, feature_date))
        if gap > 3:
            dq -= 20
    except Exception:
        pass
    return round(_clamp(dq, 0, 100), 1)


# ---------------------------------------------------------------------------
# Analyst entry point → PillarScore
# ---------------------------------------------------------------------------
def analyze(closes: list[float], highs: list[float], volumes: list[float]) -> dict:
    """Return {'pillar': PillarScore, 'state': str, 'tech': dict}."""
    if not closes or len(closes) < 2:
        na = PillarScore.na(NA_NO_PRICES, reasons=["가격 이력 부족"])
        return {"pillar": na, "state": "NEUTRAL", "tech": {}}
    tech = compute_tech(closes, highs, volumes)
    reasons = [f"상태 {tech['state']}"]
    if tech["ret_60d"] is not None:
        reasons.append(f"60일 수익률 {tech['ret_60d']*100:.1f}%")
    if tech["proximity_52w"] is not None:
        reasons.append(f"52주 고점 대비 {tech['proximity_52w']*100:.0f}%")
    pillar = PillarScore(score=tech["tech_score"], reasons=reasons,
                         detail={k: tech[k] for k in
                                 ("rsi_14", "price_vs_sma50", "price_vs_sma200",
                                  "ret_20d", "ret_60d", "proximity_52w", "rvol")})
    return {"pillar": pillar, "state": tech["state"], "tech": tech}
