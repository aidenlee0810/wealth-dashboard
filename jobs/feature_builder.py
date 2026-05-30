"""
jobs/feature_builder.py — Phase 4 multi-pillar feature builder (Plan §6, §24).

This is the integration seam where the specialist analysts (jobs/scorers/) are
run per ticker and combined by the synthesis engine into features_daily — the
Layer-3 scoring table. Each row now carries every pillar:

    bq · valuation · growth · technical · sector_theme · macro · risk
    → composite, candidate_type, dq, coverage  (feature_version 4.5.0)

Phase 3's technical-only math lives in scorers/technical.py and is re-exported
here so the Phase 3 test-suite (feature_builder.sma / compute_tech / …) stays
green.

Stage-1 ranking (active_score) is still written back to universe_membership.
No-lookahead: prices and fundamentals are read with date/usable_at <= feature_date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
import financials  # noqa: E402
import price_quality  # noqa: E402
from scorers import fundamental, technical, sector_theme, macro_fit, risk, synthesis  # noqa: E402
from scorers.model_types import classify  # noqa: E402
from scorers.base import PillarScore  # noqa: E402

# Re-export Phase 3 technical math (backward-compat with tests/test_snapshot.py)
from scorers.technical import sma, pct_return, rsi, compute_tech, compute_dq  # noqa: E402,F401

FEATURE_VERSION = "4.5.0"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GICS_TO_ETF = {
    "Information Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Communication Services": "XLC",
    "Industrials": "XLI", "Consumer Staples": "XLP", "Energy": "XLE",
    "Utilities": "XLU", "Real Estate": "XLRE", "Materials": "XLB",
}


# ---------------------------------------------------------------------------
# Preload helpers
# ---------------------------------------------------------------------------
def _theme_membership() -> dict:
    """ticker → [theme name_en] from theme_baskets.json."""
    try:
        themes = json.loads((DATA_DIR / "theme_baskets.json").read_text()).get("themes", {})
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    for meta in themes.values():
        name = meta.get("name_en")
        for t in meta.get("tickers", []):
            out.setdefault(t, [])
            if name and name not in out[t]:
                out[t].append(name)
    return out


def _leadership_map(conn, date: str) -> dict:
    rows = conn.execute(
        "SELECT name, type, leadership_score, phase FROM sector_theme_daily WHERE date=?",
        (date,)).fetchall()
    return {r["name"]: {"name": r["name"], "type": r["type"],
                        "leadership_score": r["leadership_score"], "phase": r["phase"]}
            for r in rows}


def _series(conn, ticker: str, upto: str) -> dict:
    rows = conn.execute(
        "SELECT date, COALESCE(adj_close, close) AS c, high, volume, source "
        "FROM prices_daily WHERE ticker=? AND date<=? ORDER BY date ASC",
        (ticker, upto)).fetchall()
    return {
        "dates": [r["date"] for r in rows],
        "closes": [float(r["c"]) for r in rows if r["c"] is not None],
        "highs": [float(r["high"]) for r in rows if r["high"] is not None],
        "volumes": [float(r["volume"] or 0) for r in rows],
        "last_close": float(rows[-1]["c"]) if rows and rows[-1]["c"] is not None else None,
        "last_source": rows[-1]["source"] if rows else None,
    }


def _regime(conn, date: str) -> dict:
    r = conn.execute("SELECT * FROM market_regime_daily WHERE date=?", (date,)).fetchone()
    if not r:
        return {}
    out = dict(r)
    for k in ("favored_themes_json", "avoided_themes_json", "favored_sectors_json",
              "avoided_sectors_json", "emerging_themes_json", "effective_weights_json"):
        try:
            out[k.replace("_json", "")] = json.loads(out.get(k) or "null")
        except (ValueError, TypeError):
            out[k.replace("_json", "")] = None
    return out


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_features(tickers: list[str], *, feature_date: str,
                   model_types: Optional[dict] = None,
                   sectors: Optional[dict] = None, log=None) -> dict:
    """Run all analysts per ticker → features_daily (all pillars) + active_score."""
    log = log or get_logger("feature_builder")
    usable_at = f"{feature_date}T20:00:00Z"
    stats = {"tickers": len(tickers), "written": 0, "skipped_no_data": 0,
             "by_type": {}, "dq_below_50": 0, "dq_below_70": 0,
             "bq_na_etf": 0, "fundamentals_used": 0, "valuation_na_synthetic": 0,
             "mixed_price_history": 0, "valuation_enabled": 0,
             "by_price_class": {}}

    theme_map = _theme_membership()

    with db.cloud(readonly=True) as rconn:
        # model types + sectors from ticker_master if not supplied
        tm = {r["ticker"]: r for r in rconn.execute(
            "SELECT ticker, sector, model_type, is_etf, is_leveraged FROM ticker_master").fetchall()}
        seeds = {t: (tm.get(t, {})["model_type"] if t in tm else None) for t in tickers}
        sectors = sectors or {t: (tm[t]["sector"] if t in tm else None) for t in tickers}
        if model_types is None:
            model_types = {}
            for t in tickers:
                row = tm.get(t)
                model_types[t] = classify(
                    t, sector=(row["sector"] if row else None),
                    is_etf=bool(row["is_etf"]) if row else False,
                    is_leveraged=bool(row["is_leveraged"]) if row else False,
                    seed_model_type=(row["model_type"] if row else None))
        disc = {r["ticker"]: (r["ds"] or 0.0) for r in rconn.execute(
            "SELECT ticker, MAX(discovery_score) ds FROM universe_membership "
            "WHERE date=? GROUP BY ticker", (feature_date,)).fetchall()}
        da_map = {r["ticker"]: r["da"] for r in rconn.execute(
            "SELECT ticker, MAX(days_active) da FROM universe_membership "
            "WHERE date=? GROUP BY ticker", (feature_date,)).fetchall()}
        leadership = _leadership_map(rconn, feature_date)
        regime = _regime(rconn, feature_date)

    regime_weights = regime.get("effective_weights") or {}

    with db.cloud() as conn:
        for ticker in tickers:
            mt = model_types.get(ticker, "operating_company")
            s = _series(conn, ticker, feature_date)
            if len(s["closes"]) < 2:
                stats["skipped_no_data"] += 1
                continue

            # --- Price-history realism (gates valuation + technical trust) ---
            # Classify the WHOLE history, not just the last bar: a real quote on
            # top of synthetic history is a deceptive 'mixed' series (RPL §).
            pqual = price_quality.classify_history(conn, ticker, feature_date)
            price_class = pqual["classification"]
            stats["by_price_class"][price_class] = stats["by_price_class"].get(price_class, 0) + 1

            # --- Technical Analyst ---
            tech_res = technical.analyze(s["closes"], s["highs"], s["volumes"])
            tech_pillar = tech_res["pillar"]
            state = tech_res["state"]
            technical_dq = compute_dq(len(s["closes"]), s["dates"][-1], feature_date)
            # MIXED history deceives technicals (looks real, isn't) → distrust.
            # Pure 'synthetic' is the known offline mode and is left to the
            # snapshot data-realism gate, not penalised per-row.
            if price_class == "mixed":
                stats["mixed_price_history"] += 1
                technical_dq = min(technical_dq, 40.0)

            # --- Fundamental Analyst ---
            fund = None
            if mt not in ("etf", "leveraged_etf"):
                fund = financials.get_fundamentals(conn, ticker, mt, feature_date)
            # valuation needs a FULLY-real history (not just a real last bar)
            price_is_synthetic = not price_quality.valuation_allowed(price_class)
            f_res = fundamental.analyze(fund, mt, price=s["last_close"],
                                        shares_out=(fund or {}).get("shares_out"),
                                        price_is_synthetic=price_is_synthetic)
            bq, val, gr = f_res["bq"], f_res["valuation"], f_res["growth"]
            if bq.na_reason == "ETF_NO_BQ":
                stats["bq_na_etf"] += 1
            if fund is not None:
                stats["fundamentals_used"] += 1
            if val.na_reason == "SYNTHETIC_PRICE":
                stats["valuation_na_synthetic"] += 1
            if val.score is not None:
                stats["valuation_enabled"] += 1
            bq_coverage = bq.coverage_ratio if bq.coverage_ratio is not None else (
                fund.get("coverage_ratio") if fund else None)

            # --- Sector/Theme Analyst ---
            ticker_themes = theme_map.get(ticker, [])
            etf = GICS_TO_ETF.get(sectors.get(ticker) or "")
            memberships = [leadership[n] for n in ticker_themes if n in leadership]
            if etf and etf in leadership:
                memberships.append(leadership[etf])
            st_pillar = sector_theme.analyze(memberships)

            # --- Macro/Regime Analyst ---
            macro_pillar = macro_fit.analyze(ticker_themes, etf, regime)

            # --- Risk Analyst ---
            risk_pillar = risk.analyze(dq_score=technical_dq, tech_state=state,
                                       model_type=mt, bq_coverage=bq_coverage,
                                       bq_na_reason=bq.na_reason)

            # --- Synthesis ---
            syn = synthesis.synthesize(
                {"bq": bq, "valuation": val, "growth": gr, "technical": tech_pillar,
                 "sector_theme": st_pillar, "macro": macro_pillar, "risk": risk_pillar},
                regime_weights=regime_weights, days_active=da_map.get(ticker),
                technical_dq=technical_dq, model_type=mt, bq_coverage=bq_coverage)

            dq = syn["dq_score"]
            if dq < 50:
                stats["dq_below_50"] += 1
            if dq < 70:
                stats["dq_below_70"] += 1
            ctype = syn["candidate_type"]
            stats["by_type"][ctype] = stats["by_type"].get(ctype, 0) + 1

            db.upsert(conn, "features_daily", {
                "date": feature_date, "ticker": ticker,
                "bq_score": bq.score, "bq_coverage_ratio": bq_coverage,
                "val_score": val.score, "growth_score": gr.score,
                "tech_score": tech_pillar.score,
                "sec_theme_score": st_pillar.score, "macro_score": macro_pillar.score,
                "risk_score": risk_pillar.score, "regime_fit_score": macro_pillar.score,
                "dq_score": dq, "composite_score": syn["composite"],
                "state": state, "rsi_14": tech_pillar.detail.get("rsi_14"),
                "price_vs_sma50": tech_pillar.detail.get("price_vs_sma50"),
                "price_vs_sma200": tech_pillar.detail.get("price_vs_sma200"),
                "model_type": mt, "feature_version": FEATURE_VERSION, "usable_at": usable_at,
            }, conflict_cols=("date", "ticker"))
            stats["written"] += 1

            active = round(0.55 * (syn["composite"] or 0) + 0.45 * disc.get(ticker, 0.0), 1)
            conn.execute(
                "UPDATE universe_membership SET active_score=? WHERE date=? AND ticker=?",
                (active, feature_date, ticker))

    log.info("features_done", **{k: v for k, v in stats.items() if k != "by_type"})
    return stats


def top_by_active_score(feature_date: str, limit: int = 60) -> list[str]:
    with db.cloud(readonly=True) as conn:
        rows = conn.execute(
            "SELECT ticker, MAX(COALESCE(active_score, discovery_score, 0)) AS sc "
            "FROM universe_membership WHERE date=? AND active=1 "
            "GROUP BY ticker ORDER BY sc DESC LIMIT ?",
            (feature_date, limit)).fetchall()
    return [r["ticker"] for r in rows]


def diversified_top_candidates(feature_date: str, limit: int = 60) -> list[str]:
    """Stage-2 selector: high score, but sector/theme diversified.

    This avoids the classic screener failure where the daily candidate set is
    always the same handful of mega-cap tech names. It keeps a conviction core,
    then fills remaining slots round-robin across sectors.
    """
    with db.cloud(readonly=True) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT f.ticker, f.composite_score, f.tech_score, f.sec_theme_score, "
            "f.dq_score, COALESCE(tm.sector, 'UNKNOWN') sector, "
            "MAX(COALESCE(um.active_score, um.discovery_score, 0)) discovery "
            "FROM features_daily f "
            "LEFT JOIN ticker_master tm ON tm.ticker=f.ticker "
            "LEFT JOIN universe_membership um ON um.ticker=f.ticker AND um.date=f.date "
            "WHERE f.date=? "
            "GROUP BY f.ticker "
            "ORDER BY f.composite_score DESC",
            (feature_date,)).fetchall()]
    if not rows:
        return top_by_active_score(feature_date, limit)

    def score(r):
        return (
            (r.get("composite_score") or 0) * 0.55
            + (r.get("sec_theme_score") or 0) * 0.18
            + (r.get("tech_score") or 0) * 0.17
            + (r.get("discovery") or 0) * 0.10
        )

    rows.sort(key=lambda r: (-score(r), r["ticker"]))
    selected, seen = [], set()

    def add(r):
        if r["ticker"] not in seen and len(selected) < limit:
            selected.append(r["ticker"])
            seen.add(r["ticker"])

    seed = min(limit, max(8, int(limit * 0.4)))
    for r in rows[:seed]:
        add(r)

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if r["ticker"] in seen:
            continue
        buckets.setdefault(r.get("sector") or "UNKNOWN", []).append(r)
    order = sorted(buckets, key=lambda k: (-score(buckets[k][0]), k))
    max_per_sector = max(4, int(limit / max(1, min(11, len(order)))) + 3)
    counts: dict[str, int] = {}
    for t in selected:
        row = next((r for r in rows if r["ticker"] == t), None)
        if row:
            k = row.get("sector") or "UNKNOWN"
            counts[k] = counts.get(k, 0) + 1

    progressed = True
    while len(selected) < limit and progressed:
        progressed = False
        for k in order:
            if counts.get(k, 0) >= max_per_sector:
                continue
            bucket = buckets.get(k) or []
            if not bucket:
                continue
            add(bucket.pop(0))
            counts[k] = counts.get(k, 0) + 1
            progressed = True
            if len(selected) >= limit:
                break
    for r in rows:
        add(r)
        if len(selected) >= limit:
            break
    return selected


if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="feature_builder smoke test")
    ap.add_argument("--tickers", default="NVDA,AAPL,MSFT")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    out = build_features([t.strip().upper() for t in args.tickers.split(",")],
                         feature_date=args.date)
    print(json.dumps(out, indent=2))
