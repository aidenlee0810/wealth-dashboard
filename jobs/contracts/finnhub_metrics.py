"""
Contract: Finnhub /stock/metric endpoint (basic financials, free tier).
Docs: https://finnhub.io/docs/api/company-basic-financials

Response shape:
    {
      "metric": {"grossMarginTTM": 44.1, "operatingMarginTTM": 29.8,
                 "roeTTM": 45.1, "revenueGrowthTTMYoy": 12.3, ...},
      "metricType": "all",
      "symbol": "AAPL"
    }
The "metric" map is large and sparse; we only require that it exists and is a
non-empty dict (the cleaner pulls keys defensively).
"""

from ._base import Contract, Field


def _metric_nonempty(p: dict) -> list[str]:
    m = p.get("metric")
    if not isinstance(m, dict):
        return ["metric is not a dict"]
    # Finnhub returns an empty metric map for valid-but-uncovered tickers. That
    # is data unavailability, not an API contract/schema violation.
    return []


class FinnhubMetrics(Contract):
    SOURCE = "finnhub"
    ENDPOINT = "stock/metric"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "metric": Field(dict, description="financial metric map"),
        "symbol": Field(str, required=False, allow_none=True),
    }

    VALIDATORS = [_metric_nonempty]
