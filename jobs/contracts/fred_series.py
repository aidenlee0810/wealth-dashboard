"""
Contract: FRED /series/observations endpoint
Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

Response shape:
    {
      "observations": [
        {"date": "2026-05-01", "value": "4.25", "realtime_start": "...", ...},
        ...
      ],
      "count": 260, ...
    }
Note: FRED returns value as a STRING, and uses "." for missing values.
"""

from ._base import Contract, Field


def _observations_well_formed(p: dict) -> list[str]:
    errs = []
    obs = p.get("observations")
    if not isinstance(obs, list):
        return ["observations is not a list"]
    if len(obs) == 0:
        return ["observations empty"]
    sample = obs[0]
    if not isinstance(sample, dict):
        return ["observation[0] not a dict"]
    if "date" not in sample or "value" not in sample:
        errs.append("observation missing date/value keys")
    # Spot-check that at least one value is numeric (FRED uses '.' for NA)
    numeric_count = 0
    for o in obs[:50]:
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                float(v)
                numeric_count += 1
            except (ValueError, TypeError):
                pass
    if numeric_count == 0:
        errs.append("no numeric values in first 50 observations")
    return errs


class FredSeries(Contract):
    SOURCE = "fred"
    ENDPOINT = "series/observations"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "observations": Field(list, min_len=1, description="time series rows"),
        "count": Field(int, required=False, allow_none=True),
    }

    VALIDATORS = [_observations_well_formed]
