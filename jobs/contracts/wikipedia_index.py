"""
Contract: Wikipedia index-constituent scrape (S&P 500 / Nasdaq-100)

Validates the list-of-dicts produced by jobs/scrape_indices.py before it is
written to data/sp500.json / data/nasdaq100.json.

Each constituent dict:
    {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology", ...}
"""

from ._base import Field, log_contract_violation

SCHEMA_VERSION = "1.0.0"

# Minimum expected counts — guards against a broken scrape silently shrinking the universe
MIN_COUNT = {"sp500": 450, "nasdaq100": 90}
MAX_COUNT = {"sp500": 520, "nasdaq100": 110}

_TICKER_FIELD = Field(str, min_len=1, max_len=8)
_NAME_FIELD = Field(str, min_len=1, max_len=120)


def validate_constituents(constituents: list, index_name: str) -> list[str]:
    """Validate a scraped constituent list. Returns list of warning strings.

    Hard problems (wrong type, empty) are returned as warnings AND logged as
    contract violations so the daily pipeline can alert on schema drift.
    """
    warnings: list[str] = []

    if not isinstance(constituents, list):
        msg = f"{index_name}: constituents is not a list"
        log_contract_violation(
            source="wikipedia", endpoint=index_name, ticker="",
            schema_version=SCHEMA_VERSION, errors=[msg], payload=constituents,
            severity="critical",
        )
        return [msg]

    n = len(constituents)
    lo, hi = MIN_COUNT.get(index_name, 1), MAX_COUNT.get(index_name, 100000)
    if n < lo:
        warnings.append(f"{index_name}: only {n} constituents (expected >= {lo})")
    if n > hi:
        warnings.append(f"{index_name}: {n} constituents (expected <= {hi}) — possible parse error")

    seen = set()
    dup = set()
    bad_rows = 0
    for i, c in enumerate(constituents):
        if not isinstance(c, dict):
            bad_rows += 1
            continue
        t = c.get("ticker")
        terrs = _TICKER_FIELD.validate("ticker", t if t is not None else _missing())
        nerrs = _NAME_FIELD.validate("name", c.get("name") or _missing())
        if terrs or nerrs:
            bad_rows += 1
            if bad_rows <= 3:
                warnings.append(f"{index_name}: row {i} invalid: {terrs + nerrs}")
        if t in seen:
            dup.add(t)
        seen.add(t)

    if dup:
        warnings.append(f"{index_name}: duplicate tickers: {sorted(dup)[:10]}")
    if bad_rows:
        warnings.append(f"{index_name}: {bad_rows} malformed rows")

    # Log a violation if the scrape looks structurally broken
    if n < lo or bad_rows > n * 0.1:
        log_contract_violation(
            source="wikipedia", endpoint=index_name, ticker="",
            schema_version=SCHEMA_VERSION, errors=warnings[:10], payload={"count": n},
            severity="high",
        )

    return warnings


def _missing():
    # Sentinel to force Field.validate to flag a required-missing error
    from ._base import _MISSING
    return _MISSING
