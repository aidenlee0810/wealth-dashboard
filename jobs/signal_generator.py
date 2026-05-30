"""
jobs/signal_generator.py — generic_signals + candidate_snapshots (Plan §11 step 10).

For each evaluated ticker (Stage-1 top candidates + held + watchlist) this turns
the day's features + regime into:

  generic_signals      one row per ticker: state, action, scores, a *generic*
                       (non-personal) target weight, reasons/blockers, a basic
                       risk_status, and a lineage_json stub (§26 groundwork — the
                       full Risk Governor + lineage land in Phase 5).

  candidate_snapshots  Core / Watchlist / Tactical / Reject classification.
                       Core requires days_active >= 5 (ADR 003): a one-day
                       momentum spike can be Tactical but never Core.

Signal IDs are deterministic (sha1 of date|ticker|source) so re-running a date
upserts instead of duplicating — the whole pipeline stays idempotent.

risk_status comes from the 15-gate Risk Governor (jobs/risk_governor.py, §7).
The cloud snapshot runs the market/data gates (portfolio=None); blocked signals
are still recorded + graded so the governor's cost is measurable (§8).
target_weight_generic is the System Pure (pre-risk) size; the risk-policy size
lives in Local Mode.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402

MODEL_ID = "candidate_scorer_v4.5"
FEATURE_VERSION = "4.5.0"

_ACTION_BY_STATE = {
    "BREAKOUT": "분할매수 후보(돌파)",
    "UPTREND": "보유/추세추종",
    "PULLBACK": "분할매수 후보(눌림목)",
    "BASE": "관찰",
    "NEUTRAL": "관찰",
    "DOWNTREND": "회피/보류",
}


def _git_sha() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      cwd=str(Path(__file__).resolve().parent.parent),
                                      stderr=subprocess.DEVNULL, timeout=5)
        return out.decode().strip()[:12]
    except Exception:
        return "unknown"


def _signal_id(date: str, ticker: str, source: str) -> str:
    h = hashlib.sha1(f"{date}|{ticker}|{source}".encode()).hexdigest()[:12]
    return f"sig_{h}"


def _target_weight(composite: float, risk_status: str) -> float:
    if risk_status == "BLOCKED":
        return 0.0
    if composite >= 70:
        w = 0.040
    elif composite >= 58:
        w = 0.025
    elif composite >= 48:
        w = 0.012
    else:
        w = 0.0
    if risk_status == "SIZE_REDUCED":
        w *= 0.5
    return round(w, 4)


def _adv_dollar(conn, ticker: str, date: str) -> Optional[float]:
    """20-day average dollar volume (close × volume), point-in-time."""
    rows = conn.execute(
        "SELECT close, volume FROM prices_daily WHERE ticker=? AND date<=? "
        "ORDER BY date DESC LIMIT 20", (ticker, date)).fetchall()
    vals = [r["close"] * r["volume"] for r in rows
            if r["close"] is not None and r["volume"] is not None]
    return (sum(vals) / len(vals)) if vals else None


def _bar_count(conn, ticker: str, date: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) n FROM prices_daily WHERE ticker=? AND date<=?",
        (ticker, date)).fetchone()["n"]


def _sma200_slope(conn, ticker: str, date: str) -> Optional[float]:
    """Current SMA200 minus SMA200 from 20 bars ago, point-in-time."""
    rows = conn.execute(
        "SELECT COALESCE(adj_close, close) c FROM prices_daily "
        "WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 220",
        (ticker, date)).fetchall()
    closes = [float(r["c"]) for r in reversed(rows) if r["c"] is not None]
    if len(closes) < 220:
        return None
    current = sum(closes[-200:]) / 200
    prior = sum(closes[-220:-20]) / 200
    return round(current - prior, 6)


def _reasons(state: str, tech: float, dq: float, regime: str,
             buckets: list[str], days_active: Optional[int]) -> list[str]:
    out = [f"기술 점수 {tech:.0f}/100 · 상태 {state}"]
    leaders = [b for b in buckets if b.startswith("theme:")]
    if leaders:
        out.append(f"테마 소속: {', '.join(b.split(':',1)[1] for b in leaders[:3])}")
    if "core_watchlist" in buckets:
        out.append("Core watchlist 멤버")
    if days_active is not None:
        out.append(f"universe {days_active}일 연속 활성")
    out.append(f"레짐 {regime}")
    if dq is not None and dq < 70:
        out.append(f"⚠️ 데이터 품질 {dq:.0f}")
    return out


def _candidate_type(composite: float, days_active: Optional[int],
                    risk_status: str, dq: float = 100.0) -> str:
    """Delegate to the synthesis engine so signals + features agree (Phase 4)."""
    from scorers.synthesis import classify_candidate
    return classify_candidate(composite, days_active, risk_status, dq)


def generate_signals(tickers: list[str], *, date: str, source: str = "snapshot",
                     replace_existing: bool = False, log=None) -> dict:
    """Create generic_signals + candidate_snapshots for the given tickers."""
    log = log or get_logger("signal_generator")
    sha = _git_sha()
    stats = {"tickers": len(tickers), "signals": 0, "candidates": 0,
             "by_type": {}, "by_state": {}, "by_risk": {},
             "stale_candidates_removed": 0, "stale_signals_removed": 0}

    import risk_governor

    with db.cloud() as conn:
        if replace_existing:
            # Keep same-date views faithful to the current Stage-2 set on reruns.
            # Generic signals with graded outcomes are retained for validation,
            # but stale ungraded signal rows are pruned.
            if tickers:
                placeholders = ",".join("?" for _ in tickers)
                args = [date, *tickers]
                cur = conn.execute(
                    f"DELETE FROM candidate_snapshots "
                    f"WHERE date=? AND ticker NOT IN ({placeholders})", args)
                stats["stale_candidates_removed"] = cur.rowcount if cur.rowcount != -1 else 0
                args = [date, source, *tickers]
                cur = conn.execute(
                    f"DELETE FROM generic_signals "
                    f"WHERE date=? AND source=? AND ticker NOT IN ({placeholders}) "
                    f"AND signal_id NOT IN (SELECT signal_id FROM signal_outcomes)",
                    args)
                stats["stale_signals_removed"] = cur.rowcount if cur.rowcount != -1 else 0
            else:
                cur = conn.execute("DELETE FROM candidate_snapshots WHERE date=?", (date,))
                stats["stale_candidates_removed"] = cur.rowcount if cur.rowcount != -1 else 0
                cur = conn.execute(
                    "DELETE FROM generic_signals WHERE date=? AND source=? "
                    "AND signal_id NOT IN (SELECT signal_id FROM signal_outcomes)",
                    (date, source))
                stats["stale_signals_removed"] = cur.rowcount if cur.rowcount != -1 else 0

        regime_row = conn.execute(
            "SELECT regime, confidence FROM market_regime_daily WHERE date=?",
            (date,)).fetchone()
        regime = regime_row["regime"] if regime_row else "UNKNOWN"
        regime_conf = regime_row["confidence"] if regime_row else None
        hy_row = conn.execute(
            "SELECT value FROM macro_daily WHERE series_id='BAMLH0A0HYM2' AND date<=? "
            "ORDER BY date DESC LIMIT 1", (date,)).fetchone()
        hy_oas = hy_row["value"] if hy_row else None

        spy = conn.execute("SELECT close FROM prices_daily WHERE ticker='SPY' AND date=?", (date,)).fetchone()
        qqq = conn.execute("SELECT close FROM prices_daily WHERE ticker='QQQ' AND date=?", (date,)).fetchone()
        benchmark = {"spy": spy["close"] if spy else None,
                     "qqq": qqq["close"] if qqq else None}

        for ticker in tickers:
            feat = conn.execute(
                "SELECT tech_score, dq_score, composite_score, state, model_type, "
                "usable_at, bq_score, bq_coverage_ratio, val_score, growth_score, "
                "sec_theme_score, macro_score, risk_score, price_vs_sma200 "
                "FROM features_daily WHERE date=? AND ticker=?",
                (date, ticker)).fetchone()
            if not feat:
                continue
            tech = feat["tech_score"] or 0.0
            dq = feat["dq_score"]
            composite = feat["composite_score"] or tech
            state = feat["state"] or "NEUTRAL"
            bq_cov = feat["bq_coverage_ratio"]
            coverage_warning = (f"펀더멘털 커버리지 낮음 ({bq_cov:.0f}%)"
                                if (bq_cov is not None and bq_cov < 60
                                    and feat["model_type"] not in ("etf", "leveraged_etf"))
                                else None)

            # universe context
            um = conn.execute(
                "SELECT MAX(days_active) da, source_buckets_json sb "
                "FROM universe_membership WHERE date=? AND ticker=?",
                (date, ticker)).fetchone()
            days_active = um["da"] if um else None
            try:
                buckets = json.loads(um["sb"]) if (um and um["sb"]) else []
            except (json.JSONDecodeError, TypeError):
                buckets = []

            # Risk Governor (Plan §7) — cloud runs market/data gates (portfolio=None)
            last_close_row = conn.execute(
                "SELECT close FROM prices_daily WHERE ticker=? AND date<=? "
                "ORDER BY date DESC LIMIT 1", (ticker, date)).fetchone()
            last_close = last_close_row["close"] if last_close_row else None
            base_w = _target_weight(composite, "APPROVED")   # System Pure (pre-risk)
            sma200_slope = _sma200_slope(conn, ticker, date)
            gov = risk_governor.evaluate(
                ticker=ticker, model_type=feat["model_type"], composite=composite,
                dq_score=dq, state=state, price_vs_sma200=feat["price_vs_sma200"],
                sma200_slope=sma200_slope, price=last_close,
                adv_dollar=_adv_dollar(conn, ticker, date),
                has_prices=last_close is not None, market_regime=regime, hy_oas=hy_oas,
                days_since_ipo=_bar_count(conn, ticker, date),
                base_weight=base_w, is_new_entry=True)
            risk_status = gov["risk_status"]
            flags = gov["risk_flags"]
            blockers = flags if risk_status in ("BLOCKED", "REVIEW_REQUIRED") else []
            action = _ACTION_BY_STATE.get(state, "관찰")
            tw = base_w   # generic_signals = System Pure ledger; policy size = gov['suggested_size_after_risk']
            reasons = _reasons(state, tech, dq, regime, buckets, days_active)

            lineage = {
                "model_id": MODEL_ID, "feature_version": FEATURE_VERSION,
                "regime_version": "3.0.0", "code_commit_sha": sha,
                "decision_date": date,
                "feature_refs": {
                    "tech_score": {"table": "features_daily", "pk": f"{date}:{ticker}",
                                   "value": tech, "usable_at": feat["usable_at"]},
                    "composite": {"table": "features_daily", "pk": f"{date}:{ticker}",
                                  "value": composite},
                },
                "regime": {"table": "market_regime_daily", "pk": date, "value": regime},
                "risk_governor": gov,
            }

            sid = _signal_id(date, ticker, source)
            db.upsert(conn, "generic_signals", {
                "signal_id": sid, "date": date, "ticker": ticker, "source": source,
                "state": state, "action": action,
                "composite_score": composite, "tech_score": tech, "dq_score": dq,
                "business_quality_score": feat["bq_score"],
                "valuation_score": feat["val_score"],
                "growth_score": feat["growth_score"],
                "sec_theme_score": feat["sec_theme_score"],
                "macro_score": feat["macro_score"],
                "risk_score": feat["risk_score"],
                "market_regime": regime, "confidence": regime_conf,
                "target_weight_generic": tw,
                "blockers_json": json.dumps(blockers),
                "reasons_json": json.dumps(reasons, ensure_ascii=False),
                "risk_status": risk_status,
                "risk_flags_json": json.dumps(flags),
                "risk_adjusted_weight": gov["suggested_size_after_risk"],
                "risk_size_multiplier": gov["size_multiplier"],
                "risk_reason": gov["reason"],
                "risk_manual_checks_json": json.dumps(gov["manual_checks"], ensure_ascii=False),
                "lineage_json": json.dumps(lineage, ensure_ascii=False),
                "model_id": MODEL_ID, "feature_version": FEATURE_VERSION,
                "code_commit_sha": sha,
                "benchmark_at_signal_json": json.dumps(benchmark),
            }, conflict_cols=("signal_id",))
            stats["signals"] += 1
            stats["by_state"][state] = stats["by_state"].get(state, 0) + 1
            stats["by_risk"][risk_status] = stats["by_risk"].get(risk_status, 0) + 1

            # candidate snapshot
            ctype = _candidate_type(composite, days_active, risk_status, dq)
            themes = [b.split(":", 1)[1] for b in buckets if b.startswith("theme:")]
            sector_row = conn.execute(
                "SELECT sector FROM ticker_master WHERE ticker=?", (ticker,)).fetchone()
            db.upsert(conn, "candidate_snapshots", {
                "date": date, "ticker": ticker, "candidate_type": ctype,
                "score": composite,
                "source_buckets_json": json.dumps(buckets),
                "discovery_reason": reasons[0] if reasons else None,
                "sector": sector_row["sector"] if sector_row else None,
                "themes_json": json.dumps(themes),
                "blockers_json": json.dumps(blockers),
                "reasons_json": json.dumps(reasons, ensure_ascii=False),
                "suggested_target_weight": tw,
                "risk_adjusted_weight": gov["suggested_size_after_risk"],
                "risk_size_multiplier": gov["size_multiplier"],
                "risk_reason": gov["reason"],
                "risk_manual_checks_json": json.dumps(gov["manual_checks"], ensure_ascii=False),
                "dq_score": dq,
                "bq_coverage_ratio": bq_cov,
                "coverage_warning": coverage_warning,
            }, conflict_cols=("date", "ticker"))
            stats["candidates"] += 1
            stats["by_type"][ctype] = stats["by_type"].get(ctype, 0) + 1

    log.info("signals_done", **{k: v for k, v in stats.items()
                                if k not in ("by_state",)})
    return stats


if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="signal_generator smoke test")
    ap.add_argument("--tickers", default="NVDA,AAPL,MSFT")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    out = generate_signals([t.strip().upper() for t in args.tickers.split(",")],
                           date=args.date)
    print(json.dumps(out, indent=2, ensure_ascii=False))
