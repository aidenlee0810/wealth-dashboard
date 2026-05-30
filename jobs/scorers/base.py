"""
jobs/scorers/base.py — shared scoring primitives (Plan §6).

PillarScore is the uniform return type for every specialist analyst. The key
idea (§6) is to distinguish three states cleanly:

  * a real score (0-100) with a coverage_ratio telling you how complete the
    inputs were,
  * N/A (na_reason set, score None) — the metric does not apply (e.g. BQ for an
    ETF). This must NOT be penalised as if it were a bad score.
  * insufficient data (na_reason='INSUFFICIENT_DATA') — it applies but we lack
    the inputs; this DOES feed the DQ penalty.

Helpers (clamp / scale / percentile / zscore) are stdlib-only and shared by the
normalizer and the analysts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# N/A reason codes (kept stable — surfaced in UI + tests)
NA_ETF_NO_BQ = "ETF_NO_BQ"
NA_MODEL_UNKNOWN = "MODEL_TYPE_UNKNOWN"
NA_INSUFFICIENT = "INSUFFICIENT_DATA"
NA_NO_PRICES = "NO_PRICE_HISTORY"
NA_NO_FUNDAMENTALS = "NO_FUNDAMENTALS"
NA_SYNTHETIC_PRICE = "SYNTHETIC_PRICE"   # valuation needs a real price; multiple meaningless on mock data

WARN_LOW_COVERAGE = "LOW_COVERAGE"

# Price sources we trust for valuation multiples. A synthetic/mock close makes
# any P/E or P/FCF meaningless, so the valuation pillar is N/A'd (not over-
# trusted) when the most recent bar came from one of these. Mirrors the
# views_builder data-realism gate (Phase 7.1).
SYNTHETIC_PRICE_SOURCES = frozenset({"synthetic", "mock", "fixture", "test"})


def is_real_price_source(source: Optional[str]) -> bool:
    """True if a price from `source` is trustworthy enough for valuation.
    Unknown/None sources are treated as real (legacy/manual prices are real);
    only the explicit synthetic markers are blocked."""
    if not source:
        return True
    return str(source).lower() not in SYNTHETIC_PRICE_SOURCES


@dataclass
class PillarScore:
    """One analyst's read on one pillar."""
    score: Optional[float]                       # 0-100, or None for N/A
    coverage_ratio: Optional[float] = None       # 0-100 (% of required inputs present)
    na_reason: Optional[str] = None              # set iff score is None and pillar is N/A
    warning: Optional[str] = None                # e.g. WARN_LOW_COVERAGE
    reasons: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)   # raw sub-metrics for tracing/lineage

    @property
    def is_na(self) -> bool:
        return self.score is None

    @property
    def is_applicable(self) -> bool:
        """N/A because the pillar doesn't apply (ETF BQ) vs missing data."""
        return self.na_reason not in (NA_ETF_NO_BQ, NA_MODEL_UNKNOWN, None) or self.score is not None

    def to_dict(self) -> dict:
        return {
            "score": self.score, "coverage_ratio": self.coverage_ratio,
            "na_reason": self.na_reason, "warning": self.warning,
            "reasons": self.reasons, "detail": self.detail,
        }

    @classmethod
    def na(cls, reason: str, **kw) -> "PillarScore":
        return cls(score=None, na_reason=reason, **kw)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------
def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def scale(value: Optional[float], lo: float, hi: float,
          out_lo: float = 0.0, out_hi: float = 100.0) -> Optional[float]:
    """Linearly map value in [lo,hi] → [out_lo,out_hi], clamped. None-safe.

    If hi < lo the mapping is inverted (lower input = higher score) — useful for
    'lower is better' metrics like leverage or expense ratio."""
    if value is None:
        return None
    if hi == lo:
        return out_lo
    t = (value - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return out_lo + t * (out_hi - out_lo)


def mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def percentile_rank(value: float, population: list[float]) -> Optional[float]:
    """Percentile (0-100) of value within population. None if population empty."""
    pop = sorted(x for x in population if x is not None)
    if not pop:
        return None
    below = sum(1 for x in pop if x < value)
    equal = sum(1 for x in pop if x == value)
    return (below + 0.5 * equal) / len(pop) * 100.0


def zscore(value: float, population: list[float]) -> Optional[float]:
    pop = [x for x in population if x is not None]
    if len(pop) < 3:
        return None
    mu = sum(pop) / len(pop)
    var = sum((x - mu) ** 2 for x in pop) / len(pop)
    sd = var ** 0.5
    if sd == 0:
        return 0.0
    return (value - mu) / sd


def median(xs: list[float]) -> Optional[float]:
    pop = sorted(x for x in xs if x is not None)
    if not pop:
        return None
    n = len(pop)
    mid = n // 2
    return pop[mid] if n % 2 else (pop[mid - 1] + pop[mid]) / 2.0


def coverage(required: list[str], present: dict) -> float:
    """% of required keys that are non-None in present."""
    if not required:
        return 100.0
    have = sum(1 for k in required if present.get(k) is not None)
    return round(have / len(required) * 100.0, 1)


def weighted_blend(pairs: list[tuple[Optional[float], float]]) -> Optional[float]:
    """Weighted average over (score, weight) pairs, skipping None scores and
    renormalising weights over what's present. None if nothing present."""
    num = den = 0.0
    for score, w in pairs:
        if score is None or w <= 0:
            continue
        num += score * w
        den += w
    return (num / den) if den > 0 else None
