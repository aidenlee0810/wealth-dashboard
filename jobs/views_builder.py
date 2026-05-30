"""
jobs/views_builder.py — render data/views/*.json from the cloud DB (Plan §3).

These static JSON views are the PRIMARY display source (Static Mode / GitHub
Pages) and the fast initial load for Local Mode. Every view carries
snapshot_date / executed_at_utc / feature_version / universe_version so the UI
can detect staleness and version drift.

Builds 12 views here; latest_alerts.json is produced by alerts.py.

  latest_snapshot_health      run health + counts + API stats
  latest_market_regime        regime + evidence + favored/avoided
  latest_universe_summary     counts by source bucket
  latest_candidates           all candidates (compact)
  latest_candidate_core       Core (detail)
  latest_candidate_watchlist  Watchlist
  latest_candidate_tactical   Tactical
  latest_sector_theme         sector + theme leadership board
  latest_validation_summary   EV/PF/hit-rate — multi-cut (Phase 6)
                               by_horizon, by_source, by_candidate_type,
                               by_risk_status, by_dq_tier, by_regime +
                               payoff_ratio, avg MAE/MFE/MDD + governor_ledger
                               (System Pure vs Risk Policy avoided-drawdown /
                               missed-upside §7/§8) + 10 core questions (§9)
  latest_signal_diff          yesterday vs today signal state changes (§27)
  latest_backtest_summary     recent backtest_runs + data realism gate (Phase 7/7.1)
  latest_fundamentals         SEC/provider fundamental metrics by ticker
  latest_failed_jobs          recent failed jobs (UI warning)
  latest_stress_test          factor exposures + per-ticker stress scenarios
                               (Phase 9 §9/§30) — scenarios meta + beta/R²
                               + estimated loss per scenario per ticker
  latest_model_registry       experiment registry snapshot — production /
                               shadow / experimental models (Phase 10 §31)
"""

from __future__ import annotations

import json
import sys
from statistics import median
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from scorers import health_scores  # noqa: E402
import corporate_actions  # noqa: E402  (§33: trailing dividend → yield)

VIEWS_DIR = Path(__file__).resolve().parent.parent / "data" / "views"
UNIVERSE_VERSION = "2.0.0"
FEATURE_VERSION = "4.5.0"

REAL_PRICE_SOURCES = {"finnhub", "yahoo", "stooq", "alpaca", "polygon", "tiingo", "iex", "nasdaq"}
SYNTHETIC_PRICE_SOURCES = {"synthetic", "mock", "fixture", "test"}


def _pct_rank(value: float, population: list[float]) -> float | None:
    vals = sorted(v for v in population if v is not None and v > 0)
    if not vals or value is None or value <= 0:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return round((below + 0.5 * equal) / len(vals), 4)


def _peer_multiple(value: float | None, population: list[float], group_name: str,
                   group_type: str, min_peers: int = 3) -> dict | None:
    vals = [v for v in population if v is not None and v > 0]
    if value is None or value <= 0 or len(vals) < min_peers:
        return None
    med = median(vals)
    pct = _pct_rank(value, vals)
    return {
        "group_type": group_type,
        "group_name": group_name,
        "peer_count": len(vals),
        "median": round(med, 2),
        "percentile": pct,
        "discount_to_median": round((value / med - 1.0), 4) if med else None,
        "interpretation": (
            "cheaper_than_peers" if value < med * 0.9
            else "expensive_vs_peers" if value > med * 1.1
            else "near_peer_median"
        ),
    }


def _meta(market_date: str, executed_at_utc: str) -> dict:
    return {
        "snapshot_date": market_date,
        "market_date": market_date,
        "executed_at_utc": executed_at_utc,
        "feature_version": FEATURE_VERSION,
        "universe_version": UNIVERSE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _write(name: str, payload: dict) -> int:
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    p = VIEWS_DIR / name
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    p.write_text(text)
    return len(text)


# ---------------------------------------------------------------------------
def _snapshot_health(conn, market_date, meta) -> dict:
    sm = conn.execute(
        "SELECT * FROM snapshot_metadata WHERE market_date=? ORDER BY executed_at_utc DESC LIMIT 1",
        (market_date,)).fetchone()
    sm = dict(sm) if sm else {}

    ctypes = {r["candidate_type"]: r["n"] for r in conn.execute(
        "SELECT candidate_type, COUNT(*) n FROM candidate_snapshots WHERE date=? "
        "GROUP BY candidate_type", (market_date,)).fetchall()}
    candidate_total = sum(ctypes.values())
    feature_versions = [dict(r) for r in conn.execute(
        "SELECT feature_version, COUNT(*) n, "
        "SUM(CASE WHEN bq_score IS NOT NULL THEN 1 ELSE 0 END) bq_scored, "
        "SUM(CASE WHEN val_score IS NOT NULL THEN 1 ELSE 0 END) valuation_scored, "
        "SUM(CASE WHEN growth_score IS NOT NULL THEN 1 ELSE 0 END) growth_scored "
        "FROM features_daily WHERE date=? GROUP BY feature_version ORDER BY feature_version",
        (market_date,)).fetchall()]
    price_sources = {r["source"] or "unknown": r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM prices_daily GROUP BY source ORDER BY n DESC"
    ).fetchall()}
    # Price-realism rollup over tickers scored today + how many got a real
    # valuation (val_score populated only when price history is fully real).
    pq_counts = {"real": 0, "mixed": 0, "synthetic": 0, "none": 0}
    for r in conn.execute(
        "SELECT COUNT(*) total, "
        "SUM(CASE WHEN lower(COALESCE(source,'')) IN ('synthetic','mock','fixture','test') "
        "THEN 1 ELSE 0 END) synth FROM prices_daily "
        "WHERE ticker IN (SELECT ticker FROM features_daily WHERE date=?) "
        "GROUP BY ticker", (market_date,)).fetchall():
        tot, synth = (r["total"] or 0), (r["synth"] or 0)
        if tot == 0:
            pq_counts["none"] += 1
        elif synth == tot:
            pq_counts["synthetic"] += 1
        elif (tot - synth) / tot >= 0.98:
            pq_counts["real"] += 1
        else:
            pq_counts["mixed"] += 1
    valuation_populated = conn.execute(
        "SELECT COUNT(*) n FROM features_daily WHERE date=? AND val_score IS NOT NULL",
        (market_date,)).fetchone()["n"]
    ca = conn.execute(
        "SELECT COUNT(*) total, "
        "SUM(CASE WHEN action_type='dividend' THEN 1 ELSE 0 END) dividends, "
        "SUM(CASE WHEN action_type='split' THEN 1 ELSE 0 END) splits, "
        "MAX(ex_date) latest_ex_date "
        "FROM corporate_actions WHERE ex_date<=?",
        (market_date,)).fetchone()
    ca_ttm = conn.execute(
        "SELECT COUNT(DISTINCT ticker) n FROM corporate_actions "
        "WHERE action_type='dividend' AND ex_date > date(?, '-12 months') AND ex_date<=?",
        (market_date, market_date)).fetchone()["n"]
    try:
        provider_failures = json.loads(sm.get("price_provider_status_json") or "{}")
    except (ValueError, TypeError):
        provider_failures = {}
    active = conn.execute(
        "SELECT COUNT(DISTINCT ticker) n FROM universe_membership WHERE date=? AND active=1",
        (market_date,)).fetchone()["n"]
    new_today = conn.execute(
        "SELECT COUNT(DISTINCT ticker) n FROM universe_membership "
        "WHERE date=? AND first_discovered_at=?", (market_date, market_date)).fetchone()["n"]
    dq = conn.execute(
        "SELECT SUM(CASE WHEN dq_score<50 THEN 1 ELSE 0 END) b50, "
        "SUM(CASE WHEN dq_score<70 THEN 1 ELSE 0 END) b70 FROM features_daily WHERE date=?",
        (market_date,)).fetchone()

    executed = sm.get("executed_at_utc") or meta["executed_at_utc"]
    try:
        age_h = round((datetime.now(timezone.utc)
                       - datetime.fromisoformat(str(executed).replace("Z", "+00:00"))).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        age_h = None

    api_calls = {}
    try:
        api_calls = json.loads(sm.get("api_calls_by_provider_json") or "{}")
    except (ValueError, TypeError):
        pass

    return {
        **meta,
        "result_status": sm.get("result_status", "unknown"),
        "snapshot_age_hours": age_h,
        "universe": {
            "total_active": active,
            "new_today": new_today,
            "dropped_today": 0,
            "stage1_scanned": sm.get("stage1_scanned", 0),
            "stage2_scanned": max(sm.get("stage2_scanned") or 0, candidate_total),
            "stage2_target": sm.get("stage2_target", 0),
        },
        "candidates": {
            "core": ctypes.get("Core", 0), "watchlist": ctypes.get("Watchlist", 0),
            "tactical": ctypes.get("Tactical", 0), "reject": ctypes.get("Reject", 0),
        },
        "api": {
            "calls_by_provider": api_calls,
            "rate_limit_hits": sm.get("rate_limit_hits", 0),
            "skipped_due_to_budget": sm.get("skipped_due_to_budget", 0),
            "retry_count": sm.get("retry_count", 0),
            "failed_count": sm.get("failed_count", 0),
        },
        "dq": {"below_50_count": dq["b50"] or 0, "below_70_count": dq["b70"] or 0},
        "data_realism": {
            "price_sources": price_sources,
            "all_prices_synthetic": bool(price_sources) and all(
                str(src).lower() in SYNTHETIC_PRICE_SOURCES for src in price_sources),
            "feature_versions": feature_versions,
            "price_quality": pq_counts,                       # real/mixed/synthetic/none
            "valuation_populated_count": valuation_populated,  # val_score not null
            "price_provider_failures": provider_failures,      # {provider:{count,reasons}}
            "corporate_actions": {
                "total_count": ca["total"] or 0,
                "dividend_count": ca["dividends"] or 0,
                "split_count": ca["splits"] or 0,
                "latest_ex_date": ca["latest_ex_date"],
                "tickers_with_ttm_dividend": ca_ttm or 0,
            },
        },
        "system": {
            "elapsed_seconds": sm.get("elapsed_seconds"),
            "consecutive_success_count": sm.get("consecutive_success_count", 0),
            "db_size_kb": sm.get("db_size_kb", 0),
            "repo_size_kb": sm.get("repo_size_kb", 0),
            "archive_policy": sm.get("archive_policy", "phase1_commit"),
        },
    }


def _market_regime(conn, market_date, meta) -> dict:
    r = conn.execute("SELECT * FROM market_regime_daily WHERE date=?", (market_date,)).fetchone()
    if not r:
        return {**meta, "regime": None, "_note": "no regime computed"}
    r = dict(r)
    for k in ("evidence_json", "favored_themes_json", "avoided_themes_json",
              "favored_sectors_json", "avoided_sectors_json",
              "emerging_themes_json", "fading_themes_json", "effective_weights_json"):
        try:
            r[k.replace("_json", "")] = json.loads(r.pop(k) or "null")
        except (ValueError, TypeError):
            r[k.replace("_json", "")] = None
    return {**meta, **{k: v for k, v in r.items() if k != "created_at"}}


def _universe_summary(conn, market_date, meta) -> dict:
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(DISTINCT ticker) n FROM universe_membership "
        "WHERE date=? AND active=1 GROUP BY source", (market_date,)).fetchall()}
    total = conn.execute(
        "SELECT COUNT(DISTINCT ticker) n FROM universe_membership WHERE date=? AND active=1",
        (market_date,)).fetchone()["n"]
    # days_active map for browser getCoreEligible() (Static Mode)
    da = {r["ticker"]: r["da"] for r in conn.execute(
        "SELECT ticker, MAX(days_active) da FROM universe_membership "
        "WHERE date=? AND active=1 GROUP BY ticker", (market_date,)).fetchall()}
    return {**meta, "total_active": total, "by_source": by_source,
            "days_active_by_ticker": da}


def _candidates(conn, market_date, meta, ctype=None, detail=False) -> dict:
    where = "date=?"
    args = [market_date]
    if ctype:
        where += " AND candidate_type=?"
        args.append(ctype)
    rows = conn.execute(
        f"SELECT ticker, candidate_type, score, sector, themes_json, "
        f"source_buckets_json, dq_score, suggested_target_weight, reasons_json, "
        f"discovery_reason, bq_coverage_ratio, coverage_warning, "
        f"risk_adjusted_weight, risk_size_multiplier, risk_reason, risk_manual_checks_json "
        f"FROM candidate_snapshots WHERE {where} ORDER BY score DESC", args).fetchall()
    items = []
    for r in rows:
        item = {
            "ticker": r["ticker"], "type": r["candidate_type"],
            "score": r["score"], "sector": r["sector"], "dq": r["dq_score"],
            "target_weight": r["suggested_target_weight"],
            "risk_adjusted_weight": r["risk_adjusted_weight"],
            "risk_size_multiplier": r["risk_size_multiplier"],
            "risk_reason": r["risk_reason"],
            "risk_manual_checks": _loads(r["risk_manual_checks_json"]),
            "themes": _loads(r["themes_json"]),
            "source_buckets": _loads(r["source_buckets_json"]),
            "bq_coverage_ratio": r["bq_coverage_ratio"],
            "coverage_warning": r["coverage_warning"],
        }
        if detail:
            item["reasons"] = _loads(r["reasons_json"])
            item["discovery_reason"] = r["discovery_reason"]
            # pillar breakdown from features_daily (model-type-aware §6)
            fr = conn.execute(
                "SELECT bq_score, val_score, growth_score, tech_score, sec_theme_score, "
                "macro_score, risk_score, model_type FROM features_daily "
                "WHERE date=? AND ticker=?", (market_date, r["ticker"])).fetchone()
            if fr:
                item["pillars"] = {
                    "bq": fr["bq_score"], "valuation": fr["val_score"],
                    "growth": fr["growth_score"], "technical": fr["tech_score"],
                    "sector_theme": fr["sec_theme_score"], "macro": fr["macro_score"],
                    "risk": fr["risk_score"],
                }
                item["model_type"] = fr["model_type"]
        items.append(item)
    return {**meta, "count": len(items), "candidates": items}


def _sector_theme(conn, market_date, meta) -> dict:
    rows = conn.execute(
        "SELECT name, type, ret1m, ret3m, ret6m, trend_score, breadth_score, "
        "rel_strength, leader_count, phase, leadership_score FROM sector_theme_daily "
        "WHERE date=? ORDER BY leadership_score DESC", (market_date,)).fetchall()
    themes = [dict(r) for r in rows if r["type"] == "theme"]
    sectors = [dict(r) for r in rows if r["type"] == "sector"]
    return {**meta, "themes": themes, "sectors": sectors}


def _agg_stats(rows: list) -> dict:
    """Aggregate a list of outcome row dicts into EV/PF/MAE/MFE/MDD stats.

    Each row must have: abs_ret, rel_spy, mae, mfe, mdd (all may be None).
    Hit is defined as abs_ret > 0 (any positive return), miss as abs_ret < 0.
    """
    rets = [r["abs_ret"] for r in rows if r["abs_ret"] is not None]
    n = len(rets)
    if n == 0:
        return {"n": 0}
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    hit_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0  # negative
    ev = hit_rate * avg_win + (1 - hit_rate) * avg_loss
    pf = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else (float("inf") if avg_win > 0 else 0.0)
    rel_spy = [r["rel_spy"] for r in rows if r.get("rel_spy") is not None]
    maes = [r["mae"] for r in rows if r.get("mae") is not None]
    mfes = [r["mfe"] for r in rows if r.get("mfe") is not None]
    mdds = [r["mdd"] for r in rows if r.get("mdd") is not None]
    return {
        "n": n,
        "hit_rate": round(hit_rate, 3),
        "avg_ret": round(sum(rets) / n, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "ev": round(ev, 4),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "payoff_ratio": round(payoff, 2) if payoff != float("inf") else None,
        "avg_rel_spy": round(sum(rel_spy) / len(rel_spy), 4) if rel_spy else None,
        "avg_mae": round(sum(maes) / len(maes), 4) if maes else None,
        "avg_mfe": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "avg_mdd": round(sum(mdds) / len(mdds), 4) if mdds else None,
    }


def _group_by_horizon(rows: list) -> dict:
    """Split rows by horizon and aggregate each bucket."""
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for r in rows:
        buckets[r["horizon"]].append(r)
    return {str(h): _agg_stats(rs) for h, rs in sorted(buckets.items())}


def _dq_tier(dq) -> str:
    if dq is None:
        return "unknown"
    if dq >= 85:
        return "85+"
    if dq >= 70:
        return "70-84"
    if dq >= 50:
        return "50-69"
    return "<50"


def _governor_ledger(rows: list) -> dict:
    """System Pure vs Risk Policy: avoided-drawdown vs missed-upside (§7, §8).

    The governor's effect on a position is the size it removed:
        cut = target_weight_generic (System Pure) − risk_adjusted_weight (Policy)
    cut ≥ 0 (the governor only ever reduces). For each graded outcome the
    portfolio-return impact of *not* holding that `cut` is −cut·ret, so:

        ret < 0  → the cut position fell  → loss avoided  → avoided_drawdown += cut·|ret|
        ret > 0  → the cut position rose  → gain forgone  → missed_upside    += cut·ret

    net_governor_value = avoided_drawdown − missed_upside  (>0 ⇒ governor paid off).
    Units are weight·return = fraction of portfolio (e.g. 0.004 = 0.4% of book).

    This is the §8 three-ledger seam made measurable: the System Pure ledger is
    `target_weight_generic`, the System Risk Policy ledger is
    `risk_adjusted_weight`, and their realized difference is the governor's P&L.
    """
    from collections import defaultdict
    by_h: dict = defaultdict(list)
    for r in rows:
        by_h[r["horizon"]].append(r)
    out = {}
    for h, rs in sorted(by_h.items()):
        avoided = 0.0
        missed = 0.0
        n_governed = 0
        n_blocked = 0
        for r in rs:
            wp = r.get("target_weight_generic")
            wq = r.get("risk_adjusted_weight")
            ret = r.get("abs_ret")
            if wp is None or wq is None or ret is None:
                continue
            cut = wp - wq
            if cut <= 1e-9:
                continue                       # governor did not reduce this name
            n_governed += 1
            if wq <= 1e-9:
                n_blocked += 1                 # fully blocked (policy size 0)
            if ret < 0:
                avoided += cut * (-ret)
            elif ret > 0:
                missed += cut * ret
        net = avoided - missed
        out[str(h)] = {
            "n_governed": n_governed,
            "n_blocked": n_blocked,
            "avoided_drawdown": round(avoided, 5),
            "missed_upside": round(missed, 5),
            "net_governor_value": round(net, 5),
            "governor_verdict": (
                ("beneficial" if net > 1e-6 else "costly" if net < -1e-6 else "neutral")
                if n_governed else "no_data"
            ),
        }
    return out


def _core_questions(overall: dict, by_ctype: dict, by_risk: dict,
                    by_dq: dict, by_regime: dict, governor_ledger: dict = None) -> list:
    """Answer the 10 core validation questions (§9) using graded outcome slices.

    Returns list of dicts: {id, question, answer, evidence, sufficient_data}.
    'answer' is null when n<10 for the primary cut.
    """
    governor_ledger = governor_ledger or {}
    questions = []

    def h(cut_dict, ctype_key, horizon):
        """Shorthand: get stats for a candidate_type at a horizon."""
        return cut_dict.get(ctype_key, {}).get(str(horizon), {})

    # Q1: Core Candidate 60D/120D performance
    core_60 = h(by_ctype, "Core", 60)
    core_120 = h(by_ctype, "Core", 120)
    q1_ev = core_60.get("ev") or core_120.get("ev")
    questions.append({
        "id": 1,
        "question": "Core Candidate가 60D/120D에서 양의 EV를 냈는가?",
        "answer": (q1_ev > 0) if q1_ev is not None else None,
        "evidence": {
            "core_60d": core_60,
            "core_120d": core_120,
        },
        "sufficient_data": (core_60.get("n", 0) + core_120.get("n", 0)) >= 10,
    })

    # Q2: Tactical Candidate 5D/20D vs 60D
    tact_5 = h(by_ctype, "Tactical", 5)
    tact_20 = h(by_ctype, "Tactical", 20)
    tact_60 = h(by_ctype, "Tactical", 60)
    tact_short_ev = tact_5.get("ev") or tact_20.get("ev")
    tact_long_ev = tact_60.get("ev")
    q2_answer = None
    if tact_short_ev is not None and tact_long_ev is not None:
        q2_answer = tact_short_ev > tact_long_ev
    questions.append({
        "id": 2,
        "question": "Tactical Candidate가 단기(5D/20D)에서 장기(60D)보다 성과가 좋은가?",
        "answer": q2_answer,
        "evidence": {
            "tactical_5d": tact_5,
            "tactical_20d": tact_20,
            "tactical_60d": tact_60,
        },
        "sufficient_data": (tact_5.get("n", 0) + tact_20.get("n", 0)) >= 10,
    })

    # Q3: DQ ≥85 vs DQ <70
    dq_hi = by_dq.get("85+", {}).get("20", {})
    dq_lo_a = by_dq.get("50-69", {}).get("20", {})
    dq_lo_b = by_dq.get("<50", {}).get("20", {})
    dq_hi_ev = dq_hi.get("ev")
    dq_lo_n = dq_lo_a.get("n", 0) + dq_lo_b.get("n", 0)
    dq_lo_ev = None
    if dq_lo_n > 0:
        lo_sum = (
            (dq_lo_a.get("ev") or 0) * dq_lo_a.get("n", 0) +
            (dq_lo_b.get("ev") or 0) * dq_lo_b.get("n", 0)
        )
        dq_lo_ev = lo_sum / dq_lo_n if dq_lo_n else None
    q3_answer = None
    if dq_hi_ev is not None and dq_lo_ev is not None:
        q3_answer = dq_hi_ev > dq_lo_ev
    questions.append({
        "id": 3,
        "question": "DQ ≥85 신호가 DQ <70보다 20D EV가 높은가?",
        "answer": q3_answer,
        "evidence": {
            "dq_85plus_20d": dq_hi,
            "dq_50_69_20d": dq_lo_a,
            "dq_below50_20d": dq_lo_b,
        },
        "sufficient_data": dq_hi.get("n", 0) >= 10,
    })

    # Q4: Risk Governor net value — System Pure vs Risk Policy ledger (§7/§8).
    # Preferred signal: realized avoided_drawdown − missed_upside at 20D.
    # Falls back to APPROVED-vs-BLOCKED EV comparison when the ledger is empty.
    gov_20 = governor_ledger.get("20", {})
    approved_20 = by_risk.get("APPROVED", {}).get("20", {})
    blocked_20 = by_risk.get("BLOCKED", {}).get("20", {})
    q4_answer = None
    q4_sufficient = False
    if gov_20.get("n_governed", 0) >= 5:
        q4_answer = gov_20.get("net_governor_value", 0) > 0
        q4_sufficient = True
    elif approved_20.get("ev") is not None and blocked_20.get("avg_ret") is not None:
        q4_answer = blocked_20.get("avg_ret", 0) < approved_20.get("ev", 0)
        q4_sufficient = blocked_20.get("n", 0) >= 5
    elif blocked_20.get("avg_ret") is not None:
        q4_answer = blocked_20.get("avg_ret", 0) < 0
        q4_sufficient = blocked_20.get("n", 0) >= 5
    questions.append({
        "id": 4,
        "question": "Risk Governor가 순효익을 냈는가? (회피 손실 > 놓친 상승, System Pure vs Risk Policy)",
        "answer": q4_answer,
        "evidence": {
            "governor_ledger_20d": gov_20,
            "approved_20d": approved_20,
            "blocked_20d": blocked_20,
            "size_reduced_20d": by_risk.get("SIZE_REDUCED", {}).get("20", {}),
        },
        "sufficient_data": q4_sufficient,
    })

    # Q5: Overall system positive EV at 20D
    overall_20 = overall.get("20", {})
    questions.append({
        "id": 5,
        "question": "시스템 전체 신호가 20D에서 양의 EV를 냈는가?",
        "answer": (overall_20.get("ev", 0) > 0) if overall_20.get("n", 0) >= 5 else None,
        "evidence": {"overall_20d": overall_20},
        "sufficient_data": overall_20.get("n", 0) >= 20,
    })

    # Q6: NARROW_THEME_LEADERSHIP regime performance
    ntl_20 = by_regime.get("NARROW_THEME_LEADERSHIP", {}).get("20", {})
    broad_20 = by_regime.get("BROAD_RISK_ON", {}).get("20", {})
    q6_answer = None
    if ntl_20.get("ev") is not None and broad_20.get("ev") is not None:
        q6_answer = ntl_20["ev"] >= broad_20["ev"]
    questions.append({
        "id": 6,
        "question": "NARROW_THEME_LEADERSHIP 레짐에서 신호가 BROAD_RISK_ON보다 성과가 좋은가?",
        "answer": q6_answer,
        "evidence": {
            "ntl_20d": ntl_20,
            "broad_20d": broad_20,
        },
        "sufficient_data": ntl_20.get("n", 0) >= 5,
    })

    # Q7: Profit factor > 1.0 at 20D (system profitability)
    pf_20 = overall_20.get("profit_factor")
    questions.append({
        "id": 7,
        "question": "20D 신호의 Profit Factor가 1.0 초과인가? (수익/손실 > 1)",
        "answer": (pf_20 > 1.0) if pf_20 is not None else None,
        "evidence": {"profit_factor_20d": pf_20, "n": overall_20.get("n", 0)},
        "sufficient_data": overall_20.get("n", 0) >= 20,
    })

    # Q8: Watchlist 60D performance
    wl_60 = h(by_ctype, "Watchlist", 60)
    questions.append({
        "id": 8,
        "question": "Watchlist Candidate가 60D에서 양의 EV를 냈는가?",
        "answer": (wl_60.get("ev", 0) > 0) if wl_60.get("n", 0) >= 5 else None,
        "evidence": {"watchlist_60d": wl_60},
        "sufficient_data": wl_60.get("n", 0) >= 10,
    })

    # Q9: MAE vs MFE ratio (risk/reward profile)
    overall_60 = overall.get("60", {})
    mae = overall_60.get("avg_mae")
    mfe = overall_60.get("avg_mfe")
    q9_answer = None
    if mae is not None and mfe is not None and mae != 0:
        q9_answer = abs(mfe) > abs(mae)  # favorable excursion > adverse
    questions.append({
        "id": 9,
        "question": "60D 보유 시 평균 MFE가 MAE보다 큰가? (리스크 대비 보상이 양호한가?)",
        "answer": q9_answer,
        "evidence": {
            "avg_mae_60d": mae,
            "avg_mfe_60d": mfe,
            "avg_mdd_60d": overall_60.get("avg_mdd"),
        },
        "sufficient_data": overall_60.get("n", 0) >= 10,
    })

    # Q10: REVIEW_REQUIRED signals — are manual checks being handled?
    rr_20 = by_risk.get("REVIEW_REQUIRED", {}).get("20", {})
    questions.append({
        "id": 10,
        "question": "REVIEW_REQUIRED 신호 성과: 검토 후 진입이 효과적이었는가?",
        "answer": (rr_20.get("ev", 0) > 0) if rr_20.get("n", 0) >= 5 else None,
        "evidence": {"review_required_20d": rr_20},
        "sufficient_data": rr_20.get("n", 0) >= 5,
    })

    return questions


def _validation_summary(conn, market_date, meta) -> dict:
    """Phase 6 multi-cut validation: EV/PF/payoff/MAE/MFE/MDD + 10 core Q&A.

    Cuts: horizon, source, candidate_type, risk_status, dq_tier, market_regime.
    Blocked signals are included so the governor's cost is visible (§8).
    """
    from collections import defaultdict

    # Fetch all graded outcomes with signal metadata in one JOIN
    all_rows = conn.execute("""
        SELECT
            o.horizon,
            o.abs_ret,
            o.rel_spy,
            o.rel_qqq,
            o.mae,
            o.mfe,
            o.mdd,
            o.outcome_label,
            g.source,
            g.state,
            g.risk_status,
            g.market_regime,
            g.dq_score,
            g.target_weight_generic,
            g.risk_adjusted_weight,
            COALESCE(cs.candidate_type, 'Unknown') AS candidate_type
        FROM signal_outcomes o
        JOIN generic_signals g ON g.signal_id = o.signal_id
        LEFT JOIN candidate_snapshots cs
            ON cs.date = g.date AND cs.ticker = g.ticker
        WHERE o.abs_ret IS NOT NULL
    """).fetchall()
    all_rows = [dict(r) for r in all_rows]

    # ── overall by horizon ─────────────────────────────────────────────────
    overall_by_horizon = _group_by_horizon(all_rows)

    # ── by source ──────────────────────────────────────────────────────────
    by_src: dict = defaultdict(list)
    for r in all_rows:
        if r["source"]:
            by_src[r["source"]].append(r)
    by_source = {src: _group_by_horizon(rows) for src, rows in by_src.items()}

    # ── by candidate_type ──────────────────────────────────────────────────
    by_ct: dict = defaultdict(list)
    for r in all_rows:
        by_ct[r["candidate_type"]].append(r)
    by_candidate_type = {ct: _group_by_horizon(rows) for ct, rows in by_ct.items()}

    # ── by risk_status ─────────────────────────────────────────────────────
    by_rs: dict = defaultdict(list)
    for r in all_rows:
        if r["risk_status"]:
            by_rs[r["risk_status"]].append(r)
    by_risk_status = {rs: _group_by_horizon(rows) for rs, rows in by_rs.items()}

    # ── by DQ tier ─────────────────────────────────────────────────────────
    by_dq: dict = defaultdict(list)
    for r in all_rows:
        by_dq[_dq_tier(r["dq_score"])].append(r)
    by_dq_tier = {tier: _group_by_horizon(rows) for tier, rows in by_dq.items()}

    # ── by market_regime ───────────────────────────────────────────────────
    by_reg: dict = defaultdict(list)
    for r in all_rows:
        if r["market_regime"]:
            by_reg[r["market_regime"]].append(r)
    by_regime = {reg: _group_by_horizon(rows) for reg, rows in by_reg.items()}

    # ── governor ledger: System Pure vs Risk Policy (§7/§8) ────────────────
    governor_ledger = _governor_ledger(all_rows)

    # ── 10 core questions ──────────────────────────────────────────────────
    questions = _core_questions(
        overall_by_horizon, by_candidate_type, by_risk_status, by_dq_tier,
        by_regime, governor_ledger
    )

    return {
        **meta,
        "total_graded_outcomes": len(all_rows),
        "overall_by_horizon": overall_by_horizon,
        "governor_ledger": governor_ledger,
        "by_source": by_source,
        "by_candidate_type": by_candidate_type,
        "by_risk_status": by_risk_status,
        "by_dq_tier": by_dq_tier,
        "by_regime": by_regime,
        "core_questions": questions,
    }


def _signal_diff(conn, market_date, meta) -> dict:
    """Compare yesterday's signals to today's — flag state/risk/score changes (§27).

    Returns a list of changed tickers with before/after fields.
    Over-change detection: if >20% of tickers changed state in one day, a
    'regression_warning' flag is set (possible code bug).
    """
    from market_calendar import previous_trading_day

    prev_date = previous_trading_day(market_date).isoformat()
    today_rows = {r["ticker"]: dict(r) for r in conn.execute(
        "SELECT ticker, state, risk_status, composite_score "
        "FROM generic_signals WHERE date=?", (market_date,)).fetchall()}
    prev_rows = {r["ticker"]: dict(r) for r in conn.execute(
        "SELECT ticker, state, risk_status, composite_score "
        "FROM generic_signals WHERE date=?", (prev_date,)).fetchall()}

    common = set(today_rows) & set(prev_rows)
    changes = []
    for ticker in sorted(common):
        t = today_rows[ticker]
        p = prev_rows[ticker]
        state_changed = t["state"] != p["state"]
        risk_changed = t["risk_status"] != p["risk_status"]
        score_delta = round(
            (t["composite_score"] or 0) - (p["composite_score"] or 0), 2)
        if state_changed or risk_changed or abs(score_delta) >= 5:
            changes.append({
                "ticker": ticker,
                "state_before": p["state"],
                "state_after": t["state"],
                "state_changed": state_changed,
                "risk_before": p["risk_status"],
                "risk_after": t["risk_status"],
                "risk_changed": risk_changed,
                "score_before": p["composite_score"],
                "score_after": t["composite_score"],
                "score_delta": score_delta,
            })

    # Regression guard: >20% state change in one day is suspicious
    state_change_count = sum(1 for c in changes if c["state_changed"])
    regression_warning = (
        len(common) > 0 and (state_change_count / len(common)) > 0.20
    )

    new_tickers = sorted(set(today_rows) - set(prev_rows))
    dropped_tickers = sorted(set(prev_rows) - set(today_rows))

    return {
        **meta,
        "comparison_dates": {"today": market_date, "yesterday": prev_date},
        "common_tickers": len(common),
        "changed_count": len(changes),
        "state_change_count": state_change_count,
        "new_tickers": new_tickers,
        "dropped_tickers": dropped_tickers,
        "regression_warning": regression_warning,
        "regression_note": (
            f"{state_change_count}/{len(common)} tickers changed state (>20% threshold)"
            if regression_warning else None
        ),
        "changes": changes,
    }


def _failed_jobs(conn, market_date, meta) -> dict:
    rows = conn.execute(
        "SELECT job_id, run_date, job_name, ticker, retry_count, error "
        "FROM failed_jobs ORDER BY run_date DESC LIMIT 100").fetchall()
    return {**meta, "count": len(rows), "failed_jobs": [dict(r) for r in rows]}


def _latest_fundamentals(conn, market_date, meta) -> dict:
    """Ticker-level raw fundamentals for the UI fundamentals tab.

    Phase 4.5 wired SEC/Finnhub fundamentals into scoring, but the UI also needs
    the underlying metrics (ROIC, P/E inputs, margins). This view exposes the
    latest point-in-time-safe rows so Static Mode can display them without FMP.
    """
    rows = conn.execute(
        "SELECT nf.*, tm.sector tm_sector, cf.report_date, cf.revenue, cf.net_income, "
        "cf.fcf, cf.cfo, cf.operating_income, cf.shares_out, cf.total_equity, "
        "cf.source line_item_source, cf.provider_ratios_json "
        "FROM normalized_financials nf "
        "LEFT JOIN ticker_master tm ON tm.ticker=nf.ticker "
        "LEFT JOIN cleaned_financials cf ON cf.ticker=nf.ticker "
        "  AND cf.fiscal_period=nf.fiscal_period AND cf.usable_at<=? "
        "WHERE nf.usable_at<=? "
        "ORDER BY nf.ticker, nf.usable_at DESC",
        (market_date, market_date)).fetchall()
    latest = {}
    for r in rows:
        tk = r["ticker"]
        if tk in latest:
            continue
        d = dict(r)
        price_row = conn.execute(
            "SELECT close, adj_close, source FROM prices_daily "
            "WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (tk, market_date)).fetchone()
        price = (price_row["adj_close"] or price_row["close"]) if price_row else None
        price_source = price_row["source"] if price_row else None
        synthetic_price = str(price_source or "").lower() in SYNTHETIC_PRICE_SOURCES
        shares = d.get("shares_out")
        market_cap = price * shares if (price is not None and shares) else None

        pe = ps = pfcf = pb = peg = None
        if market_cap and not synthetic_price:
            if d.get("net_income") and d["net_income"] > 0:
                pe = market_cap / d["net_income"]
            if d.get("revenue") and d["revenue"] > 0:
                ps = market_cap / d["revenue"]
            if d.get("fcf") and d["fcf"] > 0:
                pfcf = market_cap / d["fcf"]
            if d.get("total_equity") and d["total_equity"] > 0:
                pb = market_cap / d["total_equity"]
            # True PEG usually uses forward EPS growth. Free public sources do
            # not reliably provide consensus forward EPS growth, so this is a
            # clearly-labelled fallback: TTM P/E divided by YoY revenue growth
            # percentage. It is still useful for comparing growth valuation,
            # but should not be confused with analyst-consensus PEG.
            if pe is not None and d.get("revenue_growth_yoy") and d["revenue_growth_yoy"] > 0:
                peg = pe / (d["revenue_growth_yoy"] * 100.0)

        # Dividend yield from the corporate-actions ledger: trailing-12mo cash
        # dividend per share / price. The per-share figure is price-independent,
        # so the frontend can recompute yield against a live quote.
        ttm_div = corporate_actions.trailing_dividend(conn, tk, market_date)
        div_yield = (ttm_div / price) if (price and not synthetic_price and ttm_div) else None

        item = {
            "ticker": tk,
            "model_type": d.get("model_type"),
            "sector": d.get("sector") or d.get("tm_sector"),
            "fiscal_period": d.get("fiscal_period"),
            "report_date": d.get("report_date"),
            "usable_at": d.get("usable_at"),
            "source": d.get("line_item_source"),
            "ratio_source": d.get("ratio_source"),
            "coverage_ratio": d.get("coverage_ratio"),
            "revenue": d.get("revenue"),
            "net_income": d.get("net_income"),
            "fcf": d.get("fcf"),
            "shares_out": shares,
            "total_equity": d.get("total_equity"),
            "gross_margin": d.get("gross_margin"),
            "operating_margin": d.get("operating_margin"),
            "fcf_margin": d.get("fcf_margin"),
            "net_margin": d.get("net_margin"),
            "roic": d.get("roic"),
            "roe": d.get("roe"),
            "nd_ebitda": d.get("nd_ebitda"),
            "int_cov": d.get("int_cov"),
            "revenue_growth_yoy": d.get("revenue_growth_yoy"),
            "sbc_pct_revenue": d.get("sbc_pct_revenue"),
            "roic_zscore": d.get("roic_zscore"),
            "revenue_growth_zscore": d.get("revenue_growth_zscore"),
            "market_price": price,
            "market_price_source": price_source,
            "market_cap_from_price": market_cap if not synthetic_price else None,
            "pe_ttm": round(pe, 2) if pe is not None else None,
            "per_ttm": round(pe, 2) if pe is not None else None,
            "ps_ttm": round(ps, 2) if ps is not None else None,
            "pfcf_ttm": round(pfcf, 2) if pfcf is not None else None,
            "pb_ttm": round(pb, 2) if pb is not None else None,
            "pbr_ttm": round(pb, 2) if pb is not None else None,
            "peg_ttm": round(peg, 2) if peg is not None else None,
            "peg_source": "TTM P/E / YoY revenue growth" if peg is not None else None,
            "ttm_dividend": round(ttm_div, 4) if ttm_div else None,
            "dividend_yield": round(div_yield, 4) if div_yield else None,
            "valuation_warning": (
                "price source is synthetic; frontend should recompute valuation multiples from live quote"
                if synthetic_price else None
            ),
            # SEC-based composite health (honest partial / N/A — Task 3)
            **health_scores.compute_all(
                d, market_cap=(market_cap if not synthetic_price else None)),
        }
        latest[tk] = item

    by_source = {}
    for item in latest.values():
        src = item.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

    # Free peer valuation: use our own SEC/Finnhub-derived multiples and the
    # dynamic universe's sector/theme buckets. No paid peer dataset required.
    sector_vals: dict[str, dict[str, list[float]]] = {}
    for item in latest.values():
        sector = item.get("sector") or "Unknown"
        bucket = sector_vals.setdefault(sector, {"pe_ttm": [], "pfcf_ttm": [], "ps_ttm": []})
        for k in bucket:
            if item.get(k) is not None and item[k] > 0:
                bucket[k].append(item[k])

    theme_by_ticker: dict[str, list[str]] = {}
    theme_vals: dict[str, dict[str, list[float]]] = {}
    for r in conn.execute(
        "SELECT ticker, source_buckets_json FROM universe_membership "
        "WHERE date=? AND active=1", (market_date,)).fetchall():
        try:
            buckets = json.loads(r["source_buckets_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            buckets = []
        themes = []
        for b in buckets:
            if isinstance(b, str) and b.startswith("theme:"):
                themes.append(b[6:].replace("_", " "))
        if themes:
            theme_by_ticker.setdefault(r["ticker"], [])
            for theme in themes:
                if theme not in theme_by_ticker[r["ticker"]]:
                    theme_by_ticker[r["ticker"]].append(theme)
                item = latest.get(r["ticker"])
                if item:
                    bucket = theme_vals.setdefault(
                        theme, {"pe_ttm": [], "pfcf_ttm": [], "ps_ttm": []})
                    for k in bucket:
                        if item.get(k) is not None and item[k] > 0:
                            bucket[k].append(item[k])

    for tk, item in latest.items():
        sec = item.get("sector") or "Unknown"
        sec_pop = sector_vals.get(sec, {})
        themes = theme_by_ticker.get(tk, [])
        theme_comps = []
        for theme in themes:
            tv = theme_vals.get(theme, {})
            comp = _peer_multiple(item.get("pe_ttm"), tv.get("pe_ttm", []), theme, "theme")
            if comp:
                comp["pfcf"] = _peer_multiple(
                    item.get("pfcf_ttm"), tv.get("pfcf_ttm", []), theme, "theme")
                theme_comps.append(comp)
        item["themes"] = themes
        item["peer_valuation"] = {
            "sector": {
                "pe": _peer_multiple(item.get("pe_ttm"), sec_pop.get("pe_ttm", []), sec, "sector"),
                "pfcf": _peer_multiple(item.get("pfcf_ttm"), sec_pop.get("pfcf_ttm", []), sec, "sector"),
                "ps": _peer_multiple(item.get("ps_ttm"), sec_pop.get("ps_ttm", []), sec, "sector"),
            },
            "themes": theme_comps[:3],
        }
    return {**meta, "count": len(latest), "by_source": by_source,
            "fundamentals": latest}


def _data_realism_gate(conn, runs: list[dict]) -> dict:
    """Classify whether backtest numbers are interpretable as real-market results.

    Phase 7 can compute correct math on any return series, including synthetic
    smoke data. This gate prevents those numbers from being presented as real
    evidence by summarizing price sources over the windows covered by recent
    backtest runs.
    """
    dated = [r for r in runs if r.get("start_date") and r.get("end_date")]
    if not dated:
        return {
            "status": "no_backtests",
            "production_interpretation_allowed": False,
            "warning_ko": "백테스트 실행 기록이 없어 데이터 현실성 판단 불가.",
            "price_source_counts": {},
            "synthetic_share": None,
            "real_share": None,
        }

    start = min(str(r["start_date"]) for r in dated)
    end = max(str(r["end_date"]) for r in dated)
    rows = conn.execute(
        "SELECT COALESCE(source,'unknown') source, COUNT(*) n FROM prices_daily "
        "WHERE date>=? AND date<=? GROUP BY COALESCE(source,'unknown')",
        (start, end)).fetchall()
    counts = {r["source"]: int(r["n"]) for r in rows}
    total = sum(counts.values())
    synthetic = sum(n for s, n in counts.items() if str(s).lower() in SYNTHETIC_PRICE_SOURCES)
    real = sum(n for s, n in counts.items() if str(s).lower() in REAL_PRICE_SOURCES)
    unknown = max(total - synthetic - real, 0)
    synthetic_share = (synthetic / total) if total else None
    real_share = (real / total) if total else None

    if total == 0:
        status = "no_price_history"
        warning = "백테스트 기간에 가격 이력이 없어 실전 해석 불가."
    elif synthetic > 0:
        status = "synthetic_smoke"
        warning = (
            f"백테스트 가격 이력 중 synthetic 데이터가 {synthetic}/{total}건 "
            f"({synthetic_share:.1%}) 포함되어 있습니다. 현재 숫자는 엔진 smoke 검증용이며 "
            "실제 투자 판단 근거로 해석하면 안 됩니다.")
    elif unknown > 0:
        status = "unverified_reference"
        warning = (
            f"백테스트 가격 이력 중 검증되지 않은 source가 {unknown}/{total}건 포함되어 "
            "있습니다. 참고용으로만 해석하세요.")
    else:
        status = "real_data_ready"
        warning = "가격 이력 source가 모두 실데이터 계열입니다. 단, survivorship/PIT/status 경고는 별도로 확인하세요."

    return {
        "status": status,
        "production_interpretation_allowed": status == "real_data_ready",
        "warning_ko": warning,
        "checked_window": {"start": start, "end": end},
        "price_source_counts": counts,
        "synthetic_count": synthetic,
        "real_count": real,
        "unknown_count": unknown,
        "synthetic_share": round(synthetic_share, 4) if synthetic_share is not None else None,
        "real_share": round(real_share, 4) if real_share is not None else None,
        "minimum_realism_requirements": [
            "prices_daily.source에 synthetic/mock/test/fixture가 없어야 함",
            "주요 benchmark와 전략 종목 가격 source가 실데이터 계열이어야 함",
            "각 run의 results_status, survivorship_bias_risk, pit_features_available 경고를 통과해야 함",
        ],
    }


def _backtest_summary(conn, market_date, meta) -> dict:
    """Recent backtest_runs with metrics + §28 rigor + bias warnings (Phase 7).

    The UI renders each run as a card; the bias banner + results_status make a
    pretty CAGR honest about whether the data can support it. Phase 7.1 adds a
    data-realism gate so synthetic smoke results cannot masquerade as live
    market evidence."""
    rows = conn.execute(
        "SELECT run_id, strategy_name, start_date, end_date, created_at, "
        "cagr, total_return, max_drawdown, volatility, sharpe, sortino, calmar, "
        "hit_rate, profit_factor, expected_value, "
        "sharpe_ci_lower, sharpe_ci_upper, deflated_sharpe, pbo, whites_p_value, "
        "cv_method, n_oos_periods, lookahead_check_passed, survivorship_bias_risk, "
        "pit_features_available, results_status, bias_warning_message "
        "FROM backtest_runs ORDER BY created_at DESC, run_id DESC LIMIT 20").fetchall()
    runs = []
    for r in rows:
        d = dict(r)
        d["lookahead_check_passed"] = bool(d.get("lookahead_check_passed"))
        d["pit_features_available"] = bool(d.get("pit_features_available"))
        runs.append(d)
    by_status: dict = {}
    for d in runs:
        s = d.get("results_status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    realism = _data_realism_gate(conn, runs)
    return {**meta, "count": len(runs), "by_status": by_status,
            "data_realism": realism, "runs": runs}


def _loads(s):
    try:
        return json.loads(s) if s else []
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------------------
# latest_stress_test.json — Phase 9 (§9 / §30)
# ---------------------------------------------------------------------------

def _stress_test(conn, market_date: str, meta: dict) -> dict:
    """Build the stress-test view (factor exposures + per-ticker scenario returns)."""
    try:
        import stress_test as st
    except ImportError:
        return {**meta, "error": "stress_test module not available"}

    # Gather tickers that have factor exposures (from this week or earlier)
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM factor_exposures_weekly "
        "WHERE week_end_date <= ? ORDER BY ticker",
        (market_date,),
    ).fetchall()
    tickers = [r["ticker"] for r in rows]

    # Also include any scored tickers even without exposures yet (will use fallbacks)
    scored = conn.execute(
        "SELECT DISTINCT ticker FROM features_daily WHERE date=?",
        (market_date,),
    ).fetchall()
    ticker_set = set(tickers) | {r["ticker"] for r in scored}
    tickers = sorted(ticker_set)

    if not tickers:
        return {**meta, "scenarios": {}, "ticker_stress": {}}

    view = st.build_stress_view(conn, tickers, market_date)
    view.update(meta)
    return view


# latest_model_registry.json — Phase 10 (§31)
# ---------------------------------------------------------------------------

def _model_registry_view(conn, market_date: str, meta: dict) -> dict:
    """Snapshot of the model registry for UI display."""
    try:
        import model_registry as mr
        reg = mr.build_registry_view(conn)
    except Exception as exc:
        reg = {"error": str(exc)}
    return {**meta, **reg}


# ---------------------------------------------------------------------------

def build_all_views(market_date: str, executed_at_utc: str | None = None,
                    log=None) -> dict:
    log = log or get_logger("views_builder")
    db.apply_migrations("cloud")
    executed_at_utc = executed_at_utc or datetime.now(timezone.utc).isoformat()
    meta = _meta(market_date, executed_at_utc)
    written = {}
    with db.cloud(readonly=True) as conn:
        written["latest_snapshot_health.json"] = _write(
            "latest_snapshot_health.json", _snapshot_health(conn, market_date, meta))
        written["latest_market_regime.json"] = _write(
            "latest_market_regime.json", _market_regime(conn, market_date, meta))
        written["latest_universe_summary.json"] = _write(
            "latest_universe_summary.json", _universe_summary(conn, market_date, meta))
        written["latest_candidates.json"] = _write(
            "latest_candidates.json", _candidates(conn, market_date, meta))
        written["latest_candidate_core.json"] = _write(
            "latest_candidate_core.json", _candidates(conn, market_date, meta, "Core", detail=True))
        written["latest_candidate_watchlist.json"] = _write(
            "latest_candidate_watchlist.json", _candidates(conn, market_date, meta, "Watchlist", detail=True))
        written["latest_candidate_tactical.json"] = _write(
            "latest_candidate_tactical.json", _candidates(conn, market_date, meta, "Tactical", detail=True))
        written["latest_sector_theme.json"] = _write(
            "latest_sector_theme.json", _sector_theme(conn, market_date, meta))
        written["latest_validation_summary.json"] = _write(
            "latest_validation_summary.json", _validation_summary(conn, market_date, meta))
        written["latest_signal_diff.json"] = _write(
            "latest_signal_diff.json", _signal_diff(conn, market_date, meta))
        written["latest_backtest_summary.json"] = _write(
            "latest_backtest_summary.json", _backtest_summary(conn, market_date, meta))
        written["latest_fundamentals.json"] = _write(
            "latest_fundamentals.json", _latest_fundamentals(conn, market_date, meta))
        written["latest_failed_jobs.json"] = _write(
            "latest_failed_jobs.json", _failed_jobs(conn, market_date, meta))
        written["latest_stress_test.json"] = _write(
            "latest_stress_test.json", _stress_test(conn, market_date, meta))
        written["latest_model_registry.json"] = _write(
            "latest_model_registry.json", _model_registry_view(conn, market_date, meta))
    log.info("views_built", count=len(written), bytes=sum(written.values()))
    return written


if __name__ == "__main__":
    import argparse
    from datetime import datetime as _dt, timezone as _tz
    ap = argparse.ArgumentParser(description="views_builder smoke test")
    ap.add_argument("--date", default=_dt.now(_tz.utc).date().isoformat())
    args = ap.parse_args()
    out = build_all_views(args.date)
    print(json.dumps({k: f"{v}B" for k, v in out.items()}, indent=2))
