"""
Contract: SEC EDGAR CompanyFacts endpoint.

Official docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

Response shape:
    {
      "cik": 320193,
      "entityName": "Apple Inc.",
      "facts": {"us-gaap": {"Revenues": {"units": {"USD": [...]}}}}
    }

CompanyFacts is intentionally sparse and company-specific; the contract only
requires the high-level container shape. The mapper handles missing tags
defensively.
"""

from ._base import Contract, Field


def _has_us_gaap(p: dict) -> list[str]:
    facts = p.get("facts")
    if not isinstance(facts, dict):
        return ["facts is not a dict"]
    usgaap = facts.get("us-gaap")
    if not isinstance(usgaap, dict) or not usgaap:
        return ["facts.us-gaap is missing or empty"]
    return []


class SecCompanyFacts(Contract):
    SOURCE = "sec"
    ENDPOINT = "companyfacts"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "cik": Field((int, str), required=False, allow_none=True),
        "entityName": Field(str, required=False, allow_none=True),
        "facts": Field(dict),
    }

    VALIDATORS = [_has_us_gaap]
