"""
jobs/scorers/synthesis.py — the Synthesis Engine (Plan §6, §8).

The specialists each hand in a PillarScore; this engine combines them into one
composite and a candidate classification, the way a PM weighs analyst reports.

Key behaviours:
  * **Regime-aware weights.** Each regime emphasises different pillars (risk-off
    leans on quality + risk; risk-on leans on growth + technical). Weights come
    from market_regime_daily.effective_weights_json.
  * **N/A is not zero (§6).** A pillar that doesn't apply (ETF has no BQ) is
    dropped and the remaining weights renormalise — an ETF isn't punished for
    lacking fundamentals.
  * **Coverage-aware DQ (§6).** Missing fundamentals where they *should* exist
    (operating company, low coverage) dock DQ; ETF N/A does not.
  * **Core needs persistence (ADR 003).** days_active ≥ 5 to be Core; a hot
    one-day name can be Tactical but never Core.
"""

from __future__ import annotations

from typing import Optional

from .base import PillarScore, clamp, weighted_blend, WARN_LOW_COVERAGE

# composite thresholds for candidate classification
CORE_MIN_COMPOSITE = 62.0
WATCH_MIN_COMPOSITE = 50.0
CORE_MIN_DAYS_ACTIVE = 5

# default weights if a regime provides none (sums to 1.0)
DEFAULT_WEIGHTS = {"bq": .22, "val": .12, "growth": .14, "tech": .22,
                   "sec_theme": .15, "macro": .08, "risk": .07}

# pillar key → effective_weights key
_PILLAR_TO_WEIGHT = {
    "bq": "bq", "valuation": "val", "growth": "growth", "technical": "tech",
    "sector_theme": "sec_theme", "macro": "macro", "risk": "risk",
}


def synthesize(pillars: dict, *, regime_weights: Optional[dict] = None,
               days_active: Optional[int] = None, technical_dq: float = 100.0,
               model_type: str = "operating_company",
               bq_coverage: Optional[float] = None,
               risk_status: str = "APPROVED") -> dict:
    """Combine pillar PillarScores → composite + candidate_type + diagnostics."""
    weights = {**DEFAULT_WEIGHTS, **(regime_weights or {})}

    blend_pairs = []
    pillar_scores = {}
    warnings = []
    reasons = []
    for name, ps in pillars.items():
        wkey = _PILLAR_TO_WEIGHT.get(name)
        if wkey is None:
            continue
        score = ps.score if isinstance(ps, PillarScore) else ps
        pillar_scores[name] = score
        if score is not None:
            blend_pairs.append((score, weights.get(wkey, 0.0)))
        if isinstance(ps, PillarScore) and ps.warning == WARN_LOW_COVERAGE:
            warnings.append(f"{name}:low_coverage")

    composite = weighted_blend(blend_pairs)
    if composite is None:
        composite = 0.0
    composite = round(clamp(composite), 1)

    # Coverage-aware DQ (§6)
    final_dq = technical_dq
    if model_type not in ("etf", "leveraged_etf") and bq_coverage is not None:
        if bq_coverage < 30:
            final_dq -= 20
        elif bq_coverage < 60:
            final_dq -= 8
    final_dq = round(clamp(final_dq), 1)

    candidate_type = _classify(composite, days_active, risk_status, final_dq)

    # aggregate top reasons across pillars (best 1 per strong pillar)
    for name in ("bq", "technical", "sector_theme", "valuation", "growth", "macro"):
        ps = pillars.get(name)
        if isinstance(ps, PillarScore) and ps.score is not None and ps.score >= 60 and ps.reasons:
            reasons.append(ps.reasons[0])

    return {
        "composite": composite,
        "candidate_type": candidate_type,
        "dq_score": final_dq,
        "pillars": pillar_scores,
        "weights_used": {k: weights.get(k) for k in DEFAULT_WEIGHTS},
        "bq_coverage_ratio": bq_coverage,
        "coverage_warning": (f"펀더멘털 커버리지 낮음 ({bq_coverage:.0f}%)"
                             if (bq_coverage is not None and bq_coverage < 60
                                 and model_type not in ("etf", "leveraged_etf")) else None),
        "warnings": warnings,
        "reasons": reasons[:5],
    }


def classify_candidate(composite: float, days_active: Optional[int],
                       risk_status: str = "APPROVED", dq: float = 100.0) -> str:
    """Public candidate classifier (Core/Watchlist/Tactical/Reject). Shared by
    the synthesis engine and signal_generator so both agree."""
    return _classify(composite, days_active, risk_status, dq)


def _classify(composite: float, days_active: Optional[int],
              risk_status: str, dq: float) -> str:
    if risk_status == "BLOCKED":
        return "Reject"
    if dq < 40:                       # too unreliable to recommend
        return "Reject"
    da = days_active or 0
    if composite >= CORE_MIN_COMPOSITE and da >= CORE_MIN_DAYS_ACTIVE and risk_status == "APPROVED":
        return "Core"
    if composite >= CORE_MIN_COMPOSITE:
        return "Tactical"            # strong but not yet durable (or size-reduced)
    if composite >= WATCH_MIN_COMPOSITE:
        return "Watchlist"
    return "Reject"
