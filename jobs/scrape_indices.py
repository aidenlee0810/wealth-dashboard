#!/usr/bin/env python3
"""
jobs/scrape_indices.py — S&P 500 / Nasdaq-100 constituent scraper (Layer B)

Scrapes index membership from Wikipedia (the canonical free source) and writes:
    data/sp500.json
    data/nasdaq100.json

Design notes:
  - stdlib only (urllib + html.parser) — no pandas/lxml/bs4 dependency
  - Validated against jobs/contracts/wikipedia_index.py (if pydantic available)
  - Idempotent: overwrites the JSON each run
  - Safe fallback: if the network/scrape fails, the EXISTING JSON is left intact
    (we never write a half-empty file over a good one)

Usage:
    python jobs/scrape_indices.py            # scrape both, write files
    python jobs/scrape_indices.py --sp500    # only S&P 500
    python jobs/scrape_indices.py --nasdaq   # only Nasdaq-100
    python jobs/scrape_indices.py --dry-run  # print counts, don't write

Output schema (per ticker):
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "sector": "Information Technology",
      "sub_industry": "Technology Hardware, Storage & Peripherals"
    }

The wrapper file additionally records:
    {
      "index": "sp500",
      "source": "wikipedia",
      "source_url": "...",
      "scraped_at_utc": "2026-05-28T...",
      "count": 503,
      "constituents": [ ... ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

USER_AGENT = "WealthResearch/1.0 (index scraper; contact research@example.com)"

# Map Wikipedia GICS sector → our 11 SPDR sector ETFs (canonical sector key)
GICS_TO_SPDR = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}


# ---------------------------------------------------------------------------
# Minimal wikitable parser (stdlib only)
# ---------------------------------------------------------------------------
class WikiTableParser(HTMLParser):
    """Extract rows from the FIRST <table class="wikitable ...">.

    Produces self.rows = list of rows, each row = list of cell text strings.
    The first row is the header.
    """

    def __init__(self, target_table_id: str | None = None):
        super().__init__()
        self.target_table_id = target_table_id
        self._in_target_table = False
        self._table_depth = 0
        self._found = False
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "table" and not self._found:
            cls = attrs_d.get("class", "")
            tid = attrs_d.get("id", "")
            is_wikitable = "wikitable" in cls
            id_match = (self.target_table_id is None) or (tid == self.target_table_id)
            if is_wikitable and id_match:
                self._in_target_table = True
                self._found = True
                self._table_depth = 1
                return
        if self._in_target_table:
            if tag == "table":
                self._table_depth += 1
            elif tag == "tr" and self._table_depth == 1:
                self._in_row = True
                self._current_row = []
            elif tag in ("td", "th") and self._in_row:
                self._in_cell = True
                self._cell_parts = []

    def handle_endtag(self, tag):
        if not self._in_target_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            text = "".join(self._cell_parts).strip()
            text = " ".join(text.split())  # collapse whitespace
            self._current_row.append(text)
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                self.rows.append(self._current_row)
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_target_table = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell_parts.append(data)


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _clean_ticker(raw: str) -> str:
    """Normalize a scraped ticker: BRK.B → BRK.B, strip footnote markers."""
    t = raw.strip().upper()
    # Remove footnote artifacts like "AAPL[1]" or trailing punctuation
    for ch in ["[", "(", " "]:
        if ch in t:
            t = t.split(ch)[0]
    t = t.strip().rstrip(".")
    return t


# ---------------------------------------------------------------------------
# S&P 500
# ---------------------------------------------------------------------------
def scrape_sp500() -> list[dict]:
    html = _fetch(SP500_URL)
    parser = WikiTableParser(target_table_id="constituents")
    parser.feed(html)
    if not parser.rows:
        # Fallback: first wikitable on the page
        parser = WikiTableParser()
        parser.feed(html)
    rows = parser.rows
    if len(rows) < 2:
        raise RuntimeError("S&P 500 table not found or empty")

    header = [h.lower() for h in rows[0]]
    # Expected columns: Symbol, Security, GICS Sector, GICS Sub-Industry, ...
    def col(name_options, default=None):
        for opt in name_options:
            for i, h in enumerate(header):
                if opt in h:
                    return i
        return default

    i_sym = col(["symbol"], 0)
    i_name = col(["security"], 1)
    i_sector = col(["gics sector", "sector"], 2)
    i_sub = col(["sub-industry", "sub industry"], 3)

    out = []
    for row in rows[1:]:
        if len(row) <= max(i_sym, i_name):
            continue
        ticker = _clean_ticker(row[i_sym])
        if not ticker or not ticker[0].isalpha():
            continue
        sector = row[i_sector] if i_sector is not None and i_sector < len(row) else ""
        out.append({
            "ticker": ticker,
            "name": row[i_name].strip() if i_name < len(row) else "",
            "sector": sector,
            "sector_etf": GICS_TO_SPDR.get(sector),
            "sub_industry": row[i_sub].strip() if i_sub is not None and i_sub < len(row) else "",
        })
    return out


# ---------------------------------------------------------------------------
# Nasdaq-100
# ---------------------------------------------------------------------------
def scrape_nasdaq100() -> list[dict]:
    html = _fetch(NASDAQ100_URL)
    # The constituents table has id="constituents" on the Nasdaq-100 page too
    parser = WikiTableParser(target_table_id="constituents")
    parser.feed(html)
    if not parser.rows or len(parser.rows) < 2:
        # Fallback: scan all wikitables, pick the one with a "Ticker"/"Symbol" header
        parser2 = _AllTablesParser()
        parser2.feed(html)
        best = None
        for tbl in parser2.tables:
            if len(tbl) < 2:
                continue
            hdr = " ".join(tbl[0]).lower()
            if "ticker" in hdr or "symbol" in hdr:
                best = tbl
                break
        rows = best or []
    else:
        rows = parser.rows

    if len(rows) < 2:
        raise RuntimeError("Nasdaq-100 table not found or empty")

    header = [h.lower() for h in rows[0]]

    def col(name_options, default=None):
        for opt in name_options:
            for i, h in enumerate(header):
                if opt in h:
                    return i
        return default

    i_sym = col(["ticker", "symbol"], 1)
    i_name = col(["company", "security"], 0)
    i_sector = col(["gics sector", "sector"], None)

    out = []
    for row in rows[1:]:
        if len(row) <= i_sym:
            continue
        ticker = _clean_ticker(row[i_sym])
        if not ticker or not ticker[0].isalpha():
            continue
        sector = row[i_sector] if (i_sector is not None and i_sector < len(row)) else ""
        out.append({
            "ticker": ticker,
            "name": row[i_name].strip() if i_name < len(row) else "",
            "sector": sector,
            "sector_etf": GICS_TO_SPDR.get(sector),
        })
    return out


class _AllTablesParser(HTMLParser):
    """Collect ALL wikitables (for Nasdaq fallback)."""
    def __init__(self):
        super().__init__()
        self._depth = 0
        self._in = False
        self._in_row = False
        self._in_cell = False
        self._parts: list[str] = []
        self._row: list[str] = []
        self._cur: list[list[str]] = []
        self.tables: list[list[list[str]]] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table" and "wikitable" in d.get("class", ""):
            if not self._in:
                self._in = True
                self._depth = 1
                self._cur = []
                return
        if self._in:
            if tag == "table":
                self._depth += 1
            elif tag == "tr" and self._depth == 1:
                self._in_row = True
                self._row = []
            elif tag in ("td", "th") and self._in_row:
                self._in_cell = True
                self._parts = []

    def handle_endtag(self, tag):
        if not self._in:
            return
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row.append(" ".join("".join(self._parts).split()).strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row:
                self._cur.append(self._row)
        elif tag == "table":
            self._depth -= 1
            if self._depth == 0:
                self._in = False
                self.tables.append(self._cur)

    def handle_data(self, data):
        if self._in_cell:
            self._parts.append(data)


# ---------------------------------------------------------------------------
# Writer with validation + safe fallback
# ---------------------------------------------------------------------------
def _validate(constituents: list[dict], index_name: str) -> list[str]:
    """Validate via contracts if available; return list of warning strings."""
    warnings: list[str] = []
    try:
        from contracts.wikipedia_index import validate_constituents
        warnings = validate_constituents(constituents, index_name)
    except Exception as e:
        warnings.append(f"contract validation skipped: {e}")
    return warnings


def write_index(index_name: str, url: str, constituents: list[dict], dry_run: bool = False) -> dict:
    path = DATA_DIR / f"{index_name}.json"
    payload = {
        "index": index_name,
        "source": "wikipedia",
        "source_url": url,
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(constituents),
        "constituents": sorted(constituents, key=lambda x: x["ticker"]),
    }
    warnings = _validate(constituents, index_name)
    for w in warnings:
        print(f"  [warn] {w}")

    if dry_run:
        print(f"  [dry-run] {index_name}: {len(constituents)} constituents (not written)")
        return payload

    # Safety: never overwrite a good file with a near-empty scrape
    MIN_EXPECTED = {"sp500": 450, "nasdaq100": 90}
    if len(constituents) < MIN_EXPECTED.get(index_name, 1):
        if path.exists():
            print(f"  [skip] {index_name}: only {len(constituents)} scraped "
                  f"(< {MIN_EXPECTED[index_name]}). Keeping existing {path.name}.")
            return {"skipped": True, "count": len(constituents)}
        else:
            print(f"  [warn] {index_name}: only {len(constituents)} scraped, "
                  f"but no existing file — writing anyway.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"  [ok] wrote {path} ({len(constituents)} constituents)")
    return payload


def main():
    ap = argparse.ArgumentParser(description="Scrape S&P 500 / Nasdaq-100 from Wikipedia")
    ap.add_argument("--sp500", action="store_true", help="scrape only S&P 500")
    ap.add_argument("--nasdaq", action="store_true", help="scrape only Nasdaq-100")
    ap.add_argument("--dry-run", action="store_true", help="don't write files")
    args = ap.parse_args()

    do_both = not (args.sp500 or args.nasdaq)
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # for contracts import

    results = {}
    if args.sp500 or do_both:
        print("Scraping S&P 500…")
        try:
            sp = scrape_sp500()
            results["sp500"] = write_index("sp500", SP500_URL, sp, args.dry_run)
        except Exception as e:
            print(f"  [error] S&P 500 scrape failed: {e}")
            results["sp500"] = {"error": str(e)}

    if args.nasdaq or do_both:
        print("Scraping Nasdaq-100…")
        try:
            nq = scrape_nasdaq100()
            results["nasdaq100"] = write_index("nasdaq100", NASDAQ100_URL, nq, args.dry_run)
        except Exception as e:
            print(f"  [error] Nasdaq-100 scrape failed: {e}")
            results["nasdaq100"] = {"error": str(e)}

    print("\nDone.")
    return results


if __name__ == "__main__":
    main()
