"""
jobs/scorers/ — specialist analyst modules + synthesis engine (Plan §6, §24).

The Phase 4 scoring architecture mirrors a desk of specialists, each an expert
in one domain, whose independent reads are combined by a synthesis engine:

    fundamental.py   Fundamental Analyst  — BQ / valuation / growth (model-type aware)
    technical.py     Technical Analyst    — trend / momentum / setup
    sector_theme.py  Sector/Theme Analyst — leadership fit
    macro_fit.py     Macro/Regime Analyst — regime fit
    risk.py          Risk Analyst         — data quality + basic risk flags
    synthesis.py     Synthesis Engine     — regime-weighted composite + candidate_type

Every analyst returns a PillarScore (base.py): a uniform {score, coverage_ratio,
na_reason, warning, reasons, detail} so the synthesis engine can treat them
identically and reason about missing data honestly (e.g. ETFs have no BQ — that
is N/A, not a zero).
"""

from .base import PillarScore  # noqa: F401
