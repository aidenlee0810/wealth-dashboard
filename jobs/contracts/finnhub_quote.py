"""
Contract: Finnhub /quote endpoint
Docs: https://finnhub.io/docs/api/quote

Response shape:
    {"c": 142.3, "h": 144.1, "l": 140.0, "o": 141.0, "pc": 140.5, "t": 1716...}
    c = current, h = high, l = low, o = open, pc = previous close, t = epoch
"""

from ._base import Contract, Field


def _high_ge_low(p: dict) -> list[str]:
    errs = []
    if p.get("h") is not None and p.get("l") is not None and p["h"] < p["l"]:
        errs.append(f"h ({p['h']}) < l ({p['l']})")
    return errs


def _current_within_range(p: dict) -> list[str]:
    """Current price should be within the day's [l, h] band (allow tiny epsilon)."""
    errs = []
    c, h, l = p.get("c"), p.get("h"), p.get("l")
    if None not in (c, h, l) and h >= l:
        eps = max(h * 0.02, 0.01)  # 2% tolerance for stale/odd ticks
        if c < l - eps or c > h + eps:
            errs.append(f"c ({c}) outside [l-eps, h+eps] = [{l-eps:.2f}, {h+eps:.2f}]")
    return errs


class FinnhubQuote(Contract):
    SOURCE = "finnhub"
    ENDPOINT = "quote"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "c":  Field(float, gt=0, description="current price"),
        "h":  Field(float, gt=0, description="day high"),
        "l":  Field(float, gt=0, description="day low"),
        "o":  Field(float, gt=0, description="day open"),
        "pc": Field(float, gt=0, description="previous close"),
        "t":  Field(int, gt=1_400_000_000, description="epoch seconds (>2014)"),
        # d/dp (change) are optional, not required
        "d":  Field((int, float), required=False, allow_none=True),
        "dp": Field((int, float), required=False, allow_none=True),
    }

    VALIDATORS = [_high_ge_low, _current_within_range]
