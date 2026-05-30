"""
jobs/scorers/macro_fit.py — the Macro/Regime Analyst (Plan §9).

Scores how well a ticker fits the prevailing market regime: a name in a
regime-favored theme/sector gets a tailwind; one in an avoided group gets a
headwind. Neutral (50) when there's no signal either way.
"""

from __future__ import annotations

from typing import Optional

from .base import PillarScore, clamp


def analyze(themes: list[str], sector: Optional[str], regime: dict) -> PillarScore:
    """themes/sector are the ticker's groups (names matching the regime lists)."""
    if not regime:
        return PillarScore(score=50.0, reasons=["레짐 정보 없음 — 중립"])

    favored_t = set(regime.get("favored_themes") or [])
    avoided_t = set(regime.get("avoided_themes") or [])
    favored_s = set(regime.get("favored_sectors") or [])
    avoided_s = set(regime.get("avoided_sectors") or [])
    emerging = set(regime.get("emerging_themes") or [])

    score = 50.0
    reasons = []
    tset = set(themes or [])
    if tset & favored_t:
        score += 18; reasons.append(f"레짐 선호 테마: {', '.join(tset & favored_t)}")
    if tset & emerging:
        score += 8; reasons.append(f"부상 테마: {', '.join(tset & emerging)}")
    if tset & avoided_t:
        score -= 18; reasons.append(f"레짐 회피 테마: {', '.join(tset & avoided_t)}")
    if sector and sector in favored_s:
        score += 10; reasons.append(f"선호 섹터: {sector}")
    if sector and sector in avoided_s:
        score -= 10; reasons.append(f"회피 섹터: {sector}")

    if not reasons:
        reasons.append(f"레짐 {regime.get('regime','?')} — 중립 포지션")
    return PillarScore(score=round(clamp(score), 1), reasons=reasons,
                       detail={"regime": regime.get("regime")})
