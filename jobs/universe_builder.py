#!/usr/bin/env python3
"""
jobs/universe_builder.py — Dynamic Universe Builder (Plan §4, §5 Stage 1)

Merges the static universe layers into a single candidate universe, registers
tickers in ticker_master, and maintains universe_membership with persistence
rules (days_active, first_discovered_at, active flag).

Layers merged (Phase 2 = A-D static; E/F dynamic added in Phase 3):
    A. core_watchlist     data/core_watchlist.json     (user-curated)
    B. sp500 / nasdaq100  data/sp500.json, nasdaq100.json (index membership)
    C. sector_holdings    data/sector_holdings.json    (11 SPDR top holdings)
    D. theme_baskets      data/theme_baskets.json      (15 themes)
    E. momentum_discovery  (Phase 3 — needs price data)
    F. event_news          (Phase 3 — needs news/13F)

Persistence rules (noise suppression, Plan §4):
    - new ticker          → first_discovered_at=today, days_active=1
    - seen again (<=3d gap)→ days_active += 1
    - absent >3 trading d  → active=0, inactive_reason='3-day absent'
    - re-discovered        → days_active resets to 1

Stage 1 discovery_score (Phase 2 proxy = source diversity; Phase 3 adds
momentum/RVOL/52w-high once price data exists).

CLOUD-ONLY: this job never touches the personal DB.

Usage:
    python jobs/universe_builder.py                  # build for today (UTC)
    python jobs/universe_builder.py --date 2026-05-28
    python jobs/universe_builder.py --dry-run        # compute, don't write
    python jobs/universe_builder.py --summary        # print summary JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Project imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

import db  # noqa: E402

try:
    from _logging import get_logger
    log = get_logger("universe_builder")
except Exception:  # pragma: no cover
    import logging
    logging.basicConfig(level=logging.INFO)
    _base = logging.getLogger("universe_builder")

    class _ShimLogger:
        """Adapter so structured-style calls log.info('msg', key=val) work
        even when the structured logger is unavailable."""
        def _fmt(self, msg, kwargs):
            return msg + ("" if not kwargs else " " + " ".join(f"{k}={v}" for k, v in kwargs.items()))
        def debug(self, msg, **k):   _base.debug(self._fmt(msg, k))
        def info(self, msg, **k):    _base.info(self._fmt(msg, k))
        def warning(self, msg, **k): _base.warning(self._fmt(msg, k))
        def error(self, msg, **k):   _base.error(self._fmt(msg, k))

    log = _ShimLogger()

DATA_DIR = PROJECT_ROOT / "data"

# Canonical source priority (highest wins as the single `source` PK value)
SOURCE_PRIORITY = [
    "core_watchlist",
    "theme_basket",
    "sector_holdings",
    "nasdaq100",
    "sp500",
]

# Persistence: how many calendar days of absence before marking inactive.
# (Phase 3 will switch to NYSE trading-day calendar.)
ABSENCE_THRESHOLD_DAYS = 3

# Core promotion: min consecutive days in universe to be eligible for Core.
CORE_MIN_DAYS_ACTIVE = 5

# Sector-ETF → GICS sector name (for tickers where we only know the ETF)
SECTOR_ETF_TO_GICS = {
    "XLK": "Information Technology",
    "XLV": "Health Care",
    "XLF": "Financials",
    "XLY": "Consumer Discretionary",
    "XLC": "Communication Services",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials",
}

ETF_TICKERS = {"VUG", "SCHG", "SPY", "QQQ", "QQQM", "IWM", "DIA"} | set(SECTOR_ETF_TO_GICS.keys())


# ---------------------------------------------------------------------------
# Layer loading
# ---------------------------------------------------------------------------
def _load_json(name: str) -> dict | None:
    path = DATA_DIR / name
    if not path.exists():
        log.warning("layer file missing", file=name)
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.error("failed to parse layer", file=name, error=str(e))
        return None


def load_layers() -> dict:
    """Load all static layer files. Returns dict keyed by layer name."""
    return {
        "core_watchlist": _load_json("core_watchlist.json"),
        "sp500": _load_json("sp500.json"),
        "nasdaq100": _load_json("nasdaq100.json"),
        "sector_holdings": _load_json("sector_holdings.json"),
        "theme_baskets": _load_json("theme_baskets.json"),
    }


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
class TickerEntry:
    __slots__ = ("ticker", "name", "sources", "source_buckets", "sector_etf",
                 "gics_sector", "model_type", "themes", "theme_leader",
                 "is_index_sp500", "is_index_nasdaq100", "high_beta", "quality_tier")

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.name = None
        self.sources: set[str] = set()
        self.source_buckets: list[str] = []
        self.sector_etf = None
        self.gics_sector = None
        self.model_type = None
        self.themes: list[str] = []
        self.theme_leader = False
        self.is_index_sp500 = False
        self.is_index_nasdaq100 = False
        self.high_beta = False
        self.quality_tier = None

    def add_bucket(self, bucket: str):
        if bucket not in self.source_buckets:
            self.source_buckets.append(bucket)


def merge_universe(layers: dict) -> dict[str, TickerEntry]:
    """Merge all layers into {ticker: TickerEntry}. Order matters for metadata
    precedence: core_watchlist (most specific) is applied last so it wins."""
    entries: dict[str, TickerEntry] = {}

    def get(t: str) -> TickerEntry:
        if t not in entries:
            entries[t] = TickerEntry(t)
        return entries[t]

    # Layer B: S&P 500 (authoritative for GICS sector + name)
    sp = layers.get("sp500")
    if sp:
        for c in sp.get("constituents", []):
            e = get(c["ticker"])
            e.sources.add("sp500")
            e.add_bucket("sp500")
            e.is_index_sp500 = True
            e.name = e.name or c.get("name")
            e.gics_sector = e.gics_sector or c.get("sector")
            e.sector_etf = e.sector_etf or c.get("sector_etf")

    # Layer B: Nasdaq-100
    nq = layers.get("nasdaq100")
    if nq:
        for c in nq.get("constituents", []):
            e = get(c["ticker"])
            e.sources.add("nasdaq100")
            e.add_bucket("nasdaq100")
            e.is_index_nasdaq100 = True
            e.name = e.name or c.get("name")
            if c.get("sector"):
                e.gics_sector = e.gics_sector or c.get("sector")
            e.sector_etf = e.sector_etf or c.get("sector_etf")

    # Layer C: Sector holdings (top-weighted constituents)
    sec = layers.get("sector_holdings")
    if sec:
        for etf, meta in sec.get("sectors", {}).items():
            for t in meta.get("holdings", []):
                e = get(t)
                e.sources.add("sector_holdings")
                e.add_bucket(f"sector:{etf}")
                e.sector_etf = e.sector_etf or etf
                e.gics_sector = e.gics_sector or meta.get("name_en")
                # Only set model_type if not yet known (core_watchlist overrides later)
                if e.model_type is None:
                    e.model_type = meta.get("model_type")

    # Layer D: Theme baskets
    th = layers.get("theme_baskets")
    if th:
        for tid, meta in th.get("themes", {}).items():
            leaders = set(meta.get("leaders", []))
            for t in meta.get("tickers", []):
                e = get(t)
                e.sources.add("theme_basket")
                e.add_bucket(f"theme:{tid}")
                if tid not in e.themes:
                    e.themes.append(tid)
                if t in leaders:
                    e.theme_leader = True
                if not e.sector_etf and meta.get("sector_etfs"):
                    e.sector_etf = meta["sector_etfs"][0]

    # Layer A: Core watchlist (most specific — applied last, wins on conflicts)
    core = layers.get("core_watchlist")
    if core:
        for t, meta in core.get("tickers", {}).items():
            e = get(t)
            e.sources.add("core_watchlist")
            e.add_bucket("core_watchlist")
            e.name = meta.get("name") or e.name
            e.sector_etf = meta.get("sector_etf") or e.sector_etf
            e.model_type = meta.get("model_type") or e.model_type  # core wins
            e.high_beta = bool(meta.get("high_beta", e.high_beta))
            e.quality_tier = meta.get("quality_tier", e.quality_tier)

    # Fill derived fields
    for e in entries.values():
        # GICS sector fallback from sector ETF
        if not e.gics_sector and e.sector_etf:
            e.gics_sector = SECTOR_ETF_TO_GICS.get(e.sector_etf)
        # model_type fallback
        if e.model_type is None:
            e.model_type = "etf" if e.ticker in ETF_TICKERS else "operating_company"

    return entries


# ---------------------------------------------------------------------------
# Stage 1 discovery score (Phase 2 proxy: source diversity)
# ---------------------------------------------------------------------------
def discovery_score(e: TickerEntry) -> float:
    """0-100 ranking. Phase 2 uses source diversity + curation signals.
    Phase 3 will blend in momentum / RVOL / 52w-high proximity."""
    score = 0.0
    score += min(len(e.sources), 5) * 12      # up to 60 for breadth
    if "core_watchlist" in e.sources:
        score += 20                            # user-curated conviction
    if e.theme_leader:
        score += 12                            # theme leadership
    if e.is_index_nasdaq100:
        score += 6                             # growth index membership
    return round(min(100.0, score), 1)


def canonical_source(e: TickerEntry) -> str:
    for s in SOURCE_PRIORITY:
        if s in e.sources:
            return s
    return next(iter(e.sources)) if e.sources else "unknown"


# ---------------------------------------------------------------------------
# Persistence + DB write
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def build(build_date: str | None = None, dry_run: bool = False) -> dict:
    """Build the universe for build_date (default: today UTC).
    Returns a summary dict."""
    today = build_date or datetime.now(timezone.utc).date().isoformat()
    today_d = _parse_date(today)

    layers = load_layers()
    entries = merge_universe(layers)
    log.info("merged tickers from layers", count=len(entries))

    if dry_run:
        return _summary(today, entries, layers, written=False)

    new_count = seen_count = reactivated_count = 0

    with db.cloud() as conn:
        # ── 1. Register/update ticker_master ────────────────────────────────
        for e in entries.values():
            is_etf = 1 if e.model_type in ("etf", "leveraged_etf") else 0
            asset_type = "etf" if is_etf else "stock"
            conn.execute(
                """
                INSERT INTO ticker_master
                    (ticker, name, asset_type, sector, model_type, is_etf,
                     active, first_seen, last_seen, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=COALESCE(excluded.name, ticker_master.name),
                    sector=COALESCE(excluded.sector, ticker_master.sector),
                    model_type=COALESCE(excluded.model_type, ticker_master.model_type),
                    is_etf=excluded.is_etf,
                    asset_type=excluded.asset_type,
                    active=1,
                    last_seen=excluded.last_seen,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (e.ticker, e.name, asset_type, e.gics_sector, e.model_type,
                 is_etf, today, today),
            )

        # ── 2. universe_membership with persistence ─────────────────────────
        for e in entries.values():
            src = canonical_source(e)

            # Idempotent re-run: preserve today's existing row metadata
            existing_today = conn.execute(
                "SELECT first_discovered_at, days_active FROM universe_membership "
                "WHERE ticker=? AND date=? ORDER BY days_active DESC LIMIT 1",
                (e.ticker, today),
            ).fetchone()

            if existing_today:
                first_disc = existing_today["first_discovered_at"]
                days_active = existing_today["days_active"]
            else:
                prior = conn.execute(
                    "SELECT first_discovered_at, last_seen_at, days_active "
                    "FROM universe_membership WHERE ticker=? AND date<? "
                    "ORDER BY date DESC LIMIT 1",
                    (e.ticker, today),
                ).fetchone()
                if prior is None:
                    first_disc, days_active = today, 1
                    new_count += 1
                else:
                    last_seen = _parse_date(prior["last_seen_at"]) if prior["last_seen_at"] else today_d
                    gap = (today_d - last_seen).days
                    if gap <= ABSENCE_THRESHOLD_DAYS:
                        first_disc = prior["first_discovered_at"] or today
                        days_active = (prior["days_active"] or 0) + 1
                        seen_count += 1
                    else:
                        first_disc, days_active = today, 1   # re-discovery resets
                        reactivated_count += 1

            dscore = discovery_score(e)
            conn.execute(
                """
                INSERT INTO universe_membership
                    (date, ticker, source, universe_name, reason,
                     first_discovered_at, last_seen_at, days_active,
                     source_buckets_json, discovery_score, active_score,
                     active, inactive_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
                ON CONFLICT(date, ticker, source) DO UPDATE SET
                    universe_name=excluded.universe_name,
                    source_buckets_json=excluded.source_buckets_json,
                    last_seen_at=excluded.last_seen_at,
                    days_active=excluded.days_active,
                    discovery_score=excluded.discovery_score,
                    active=1, inactive_reason=NULL
                """,
                (today, e.ticker, src,
                 src, f"{len(e.sources)} source(s): {','.join(sorted(e.sources))}",
                 first_disc, today, days_active,
                 json.dumps(e.source_buckets), dscore, dscore),
            )

        # ── 3. Mark absent tickers inactive (persistence) ───────────────────
        # Find tickers that were active in the most recent prior snapshot but
        # are absent today, and whose last_seen_at exceeds the threshold.
        today_set = set(entries.keys())
        prior_active = conn.execute(
            "SELECT ticker, MAX(date) AS last_date, MAX(last_seen_at) AS last_seen "
            "FROM universe_membership WHERE date < ? AND active=1 "
            "GROUP BY ticker",
            (today,),
        ).fetchall()

        deactivated = 0
        for row in prior_active:
            t = row["ticker"]
            if t in today_set:
                continue
            last_seen = _parse_date(row["last_seen"]) if row["last_seen"] else today_d
            gap = (today_d - last_seen).days
            if gap > ABSENCE_THRESHOLD_DAYS:
                # Write an inactive marker row for today
                conn.execute(
                    """
                    INSERT INTO universe_membership
                        (date, ticker, source, universe_name, reason,
                         first_discovered_at, last_seen_at, days_active,
                         source_buckets_json, discovery_score, active_score,
                         active, inactive_reason)
                    VALUES (?, ?, 'dropped', 'dropped', ?, NULL, ?, 0, '[]', 0, 0, 0, ?)
                    ON CONFLICT(date, ticker, source) DO UPDATE SET
                        active=0, inactive_reason=excluded.inactive_reason
                    """,
                    (today, t, f"absent {gap}d", row["last_seen"],
                     f"{gap}-day absent"),
                )
                conn.execute(
                    "UPDATE ticker_master SET active=0, updated_at=CURRENT_TIMESTAMP "
                    "WHERE ticker=?",
                    (t,),
                )
                deactivated += 1

    summary = _summary(today, entries, layers, written=True)
    summary.update({
        "new_tickers": new_count,
        "seen_again": seen_count,
        "reactivated": reactivated_count,
        "deactivated": deactivated,
    })
    log.info("universe build complete",
             date=today, total=summary["total_tickers"], new=new_count,
             seen=seen_count, deactivated=deactivated)
    return summary


def _summary(today: str, entries: dict[str, TickerEntry], layers: dict, written: bool) -> dict:
    by_source: dict[str, int] = {}
    core_eligible = 0
    etf_count = 0
    for e in entries.values():
        for s in e.sources:
            by_source[s] = by_source.get(s, 0) + 1
        if e.model_type in ("etf", "leveraged_etf"):
            etf_count += 1
    return {
        "date": today,
        "written": written,
        "total_tickers": len(entries),
        "by_source": by_source,
        "etf_count": etf_count,
        "layers_loaded": {k: (v is not None) for k, v in layers.items()},
    }


def main():
    ap = argparse.ArgumentParser(description="Build the dynamic universe")
    ap.add_argument("--date", help="build date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--dry-run", action="store_true", help="compute without writing")
    ap.add_argument("--summary", action="store_true", help="print summary JSON")
    args = ap.parse_args()

    result = build(build_date=args.date, dry_run=args.dry_run)
    if args.summary or args.dry_run:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps({k: result[k] for k in result if k != "by_source"}, default=str))
        print("by_source:", json.dumps(result["by_source"], indent=2))


if __name__ == "__main__":
    main()
