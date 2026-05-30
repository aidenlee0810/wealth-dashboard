"""
jobs/scorers/sector_theme.py — the Sector/Theme Analyst (Plan §9).

Scores how strong the leadership is in the groups a ticker belongs to. Inputs
are the ticker's matched sector_theme_daily rows (resolved by the caller):
each {name, leadership_score, phase}. The pillar rewards membership in
leading/emerging groups and penalises fading/breakdown.
"""

from __future__ import annotations

from typing import Optional

from .base import PillarScore, clamp, mean

_PHASE_BONUS = {"emerging": 8, "leading": 5, "extended": -2,
                "fading": -8, "breakdown": -12, "neutral": 0}


def analyze(memberships: list[dict]) -> PillarScore:
    """memberships: [{name, leadership_score, phase, type}]."""
    if not memberships:
        return PillarScore(score=50.0, reasons=["섹터/테마 매칭 없음 — 중립"],
                           coverage_ratio=0.0)
    scores = [m["leadership_score"] for m in memberships if m.get("leadership_score") is not None]
    if not scores:
        return PillarScore(score=50.0, reasons=["리더십 점수 없음 — 중립"])
    top = max(scores)
    avg = mean(scores) or top
    base = 0.6 * top + 0.4 * avg          # reward best group, temper by breadth
    # phase adjustment from the strongest group
    strongest = max(memberships, key=lambda m: m.get("leadership_score") or 0)
    base += _PHASE_BONUS.get(strongest.get("phase"), 0)
    reasons = []
    nm = strongest.get("name")
    if nm:
        reasons.append(f"주도 그룹: {nm} ({strongest.get('phase')}, "
                       f"리더십 {strongest.get('leadership_score'):.0f})")
    if len(memberships) > 1:
        reasons.append(f"{len(memberships)}개 그룹 소속")
    return PillarScore(score=round(clamp(base), 1), reasons=reasons,
                       detail={"groups": [m.get("name") for m in memberships]})
