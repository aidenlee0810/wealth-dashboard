"""
Contract: Yahoo Finance chart/v8 endpoint
URL: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}

Response shape:
    {
      "chart": {
        "result": [
          {
            "meta": {...},
            "timestamp": [...],
            "indicators": {"quote": [{"open": [...], "close": [...], ...}],
                           "adjclose": [{"adjclose": [...]}]}
          }
        ],
        "error": null
      }
    }
"""

from ._base import Contract, Field


def _chart_structure(p: dict) -> list[str]:
    errs = []
    chart = p.get("chart")
    if not isinstance(chart, dict):
        return ["chart key missing or not dict"]
    if chart.get("error") is not None:
        errs.append(f"chart.error is set: {chart['error']}")
    result = chart.get("result")
    if not isinstance(result, list) or len(result) == 0:
        return errs + ["chart.result missing or empty"]
    r0 = result[0]
    if not isinstance(r0, dict):
        return errs + ["chart.result[0] not a dict"]
    if "timestamp" not in r0:
        errs.append("result[0].timestamp missing")
    indicators = r0.get("indicators", {})
    if not isinstance(indicators, dict) or "quote" not in indicators:
        errs.append("result[0].indicators.quote missing")
    else:
        quote = indicators.get("quote")
        if not isinstance(quote, list) or len(quote) == 0:
            errs.append("indicators.quote empty")
        else:
            q0 = quote[0]
            for k in ("open", "high", "low", "close"):
                if k not in q0:
                    errs.append(f"indicators.quote[0].{k} missing")
    return errs


class YahooChart(Contract):
    SOURCE = "yahoo"
    ENDPOINT = "chart/v8"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "chart": Field(dict, description="top-level chart wrapper"),
    }

    VALIDATORS = [_chart_structure]
