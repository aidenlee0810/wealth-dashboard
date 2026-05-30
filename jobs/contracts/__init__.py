"""
jobs/contracts/ — Data Contracts & API Schema Validation (Plan §23)

Every external API response (Finnhub, FRED, Yahoo, Wikipedia, SEC) has an
EXPLICIT, VERSIONED schema defined here.  When an upstream silently changes its
response format, validation fails loudly instead of silently corrupting scores.

Framework (jobs/contracts/_base.py):
  - stdlib only (no pydantic dependency required), but pydantic-compatible in spirit
  - Field() with type + range constraints + cross-field validators
  - SCHEMA_VERSION on every contract — bump when upstream format changes
  - log_contract_violation() persists violations to the cloud DB contract_violations table
  - ContractError raised on hard validation failure

Contracts:
  - finnhub_quote.py     FinnhubQuote     (c/h/l/o/pc/t)
  - finnhub_candle.py    FinnhubCandle    (s/t/o/h/l/c/v arrays)
  - fred_series.py       FredSeries       (observations[])
  - yahoo_chart.py       YahooChart       (chart.result[].indicators)
  - wikipedia_index.py   validate_constituents()  (index membership rows)

Usage:
    from contracts.finnhub_quote import FinnhubQuote
    ok, errors, cleaned = FinnhubQuote.validate(raw_json)
    if not ok:
        FinnhubQuote.log_violation(ticker, errors, raw_json)
        raise ContractError(...)
"""

from ._base import (
    Contract,
    ContractError,
    Field,
    log_contract_violation,
)

__all__ = ["Contract", "ContractError", "Field", "log_contract_violation"]
