"""
Contract: Finnhub /stock/candle endpoint (or our Yahoo-backed equivalent)
Docs: https://finnhub.io/docs/api/stock-candles

Response shape:
    {"s": "ok", "t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}
    s = status, arrays are parallel time series.
"""

from ._base import Contract, Field


def _arrays_same_length(p: dict) -> list[str]:
    errs = []
    keys = ["t", "o", "h", "l", "c", "v"]
    present = {k: p[k] for k in keys if isinstance(p.get(k), list)}
    if not present:
        return ["no array fields present"]
    lengths = {k: len(v) for k, v in present.items()}
    n = len(next(iter(present.values())))
    mismatched = {k: ln for k, ln in lengths.items() if ln != n}
    if mismatched:
        errs.append(f"array length mismatch: {lengths}")
    if n == 0:
        errs.append("empty candle arrays")
    return errs


def _ohlc_sane(p: dict) -> list[str]:
    """Spot-check first/last bars: h >= l, all positive."""
    errs = []
    h, l, c = p.get("h"), p.get("l"), p.get("c")
    if not (isinstance(h, list) and isinstance(l, list) and isinstance(c, list)):
        return errs
    n = min(len(h), len(l), len(c))
    if n == 0:
        return errs
    for idx in {0, n - 1}:
        try:
            if h[idx] < l[idx]:
                errs.append(f"bar[{idx}] high {h[idx]} < low {l[idx]}")
            if c[idx] is not None and c[idx] <= 0:
                errs.append(f"bar[{idx}] close {c[idx]} <= 0")
        except (IndexError, TypeError):
            pass
    return errs


class FinnhubCandle(Contract):
    SOURCE = "finnhub"
    ENDPOINT = "stock/candle"
    SCHEMA_VERSION = "1.0.0"

    FIELDS = {
        "s": Field(str, choices=["ok", "no_data"], description="status"),
        "t": Field(list, required=False, allow_none=True, description="epoch timestamps"),
        "o": Field(list, required=False, allow_none=True),
        "h": Field(list, required=False, allow_none=True),
        "l": Field(list, required=False, allow_none=True),
        "c": Field(list, required=False, allow_none=True),
        "v": Field(list, required=False, allow_none=True),
    }

    VALIDATORS = [_arrays_same_length, _ohlc_sane]

    @classmethod
    def validate(cls, payload):
        # "no_data" status is a valid (empty) response — skip array checks
        if isinstance(payload, dict) and payload.get("s") == "no_data":
            return True, [], {"s": "no_data"}
        return super().validate(payload)
