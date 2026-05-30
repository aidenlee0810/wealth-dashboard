"""
jobs/scorers/risk.py — the Risk Analyst (Plan §7, pre-governor).

A lightweight risk pillar: data quality + a few deterministic risk flags. The
score is "safety" (higher = lower risk) so the synthesis engine can blend it
like any other pillar. The full 15-gate Risk Governor (sizing caps,
concentration, earnings blackout, …) arrives in Phase 5 and consumes these
flags as its starting point.
"""

from __future__ import annotations

from typing import Optional

from .base import PillarScore, clamp


def analyze(*, dq_score: Optional[float], tech_state: Optional[str],
            model_type: str, bq_coverage: Optional[float] = None,
            bq_na_reason: Optional[str] = None) -> PillarScore:
    score = 100.0
    flags: list[str] = []
    reasons: list[str] = []

    if dq_score is not None:
        if dq_score < 50:
            score -= 40; flags.append("DQ_CRITICAL"); reasons.append(f"데이터품질 {dq_score:.0f} (위험)")
        elif dq_score < 70:
            score -= 15; flags.append("DQ_LOW"); reasons.append(f"데이터품질 {dq_score:.0f} (주의)")

    if tech_state == "DOWNTREND":
        score -= 20; flags.append("DOWNTREND"); reasons.append("추세 하락 — 신규진입 위험")

    if model_type == "leveraged_etf":
        score -= 25; flags.append("LEVERAGE_DECAY")
        reasons.append("레버리지 ETF — 변동성 손실(decay) 위험")

    # Missing fundamentals where they *should* exist (not ETF N/A)
    if bq_na_reason == "INSUFFICIENT_DATA" or (bq_coverage is not None and bq_coverage < 60
                                              and model_type not in ("etf", "leveraged_etf")):
        score -= 10; flags.append("LOW_FUNDAMENTAL_COVERAGE")
        reasons.append("펀더멘털 커버리지 낮음")

    if not reasons:
        reasons.append("주요 리스크 플래그 없음")

    return PillarScore(score=round(clamp(score), 1), reasons=reasons,
                       detail={"flags": flags})
