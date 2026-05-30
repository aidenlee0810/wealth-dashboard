"""
jobs/daily_snapshot.py — the orchestrator (Plan §11).

Runs the full daily pipeline against the CLOUD db only (never personal):

  precheck → universe → prices → reconcile → macro → features (Stage 1)
          → regime + themes → signals (Stage 2) → outcomes
          → snapshot_metadata → views → alerts → git commit/push

Design guarantees
-----------------
* **Cloud-only.** Held/watchlist personal overlays are a Local-Mode concern;
  this job evaluates the discovered candidate universe only, so no personal
  data ever enters the cloud DB or a commit.
* **Idempotent.** Every write is an upsert; re-running a date is safe.
* **Graceful degradation (§11).** A single ticker failing is a soft fail
  (failed_jobs + continue). result_status is derived from the failure rate:
  success<5% · degraded<30% · partial<50% · failed≥50%.
* **Market-aware.** Non-trading days short-circuit to result_status
  'market_closed' (no wasted API budget).
* **Testable.** run(synthetic=True, limit=N) drives the whole thing offline.

CLI:
    python jobs/daily_snapshot.py                 # live, today
    python jobs/daily_snapshot.py --synthetic --temp-output --limit 40 # offline test
    python jobs/daily_snapshot.py --date 2026-05-28 --force
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))
import db  # noqa: E402

from _logging import get_logger, timed  # noqa: E402
from env_loader import load_private_env  # noqa: E402
import market_calendar as cal  # noqa: E402
from fetch import Fetcher, FRED_DEFAULT_SERIES, prune_raw_responses  # noqa: E402
import universe_builder, prices, reconciliation, feature_builder, financials  # noqa: E402
import regime_builder, signal_generator, outcome_updater, views_builder, alerts  # noqa: E402
import factor_model  # noqa: E402  (Phase 9: factor exposures for stress test)
import corporate_actions  # noqa: E402  (§33: Alpaca splits + dividends)
from scorers.model_types import classify as classify_model_type  # noqa: E402

load_private_env()

BENCHMARKS = ["SPY", "QQQ"]
SECTOR_ETFS = ["XLK", "XLV", "XLF", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLRE", "XLB"]
BUDGET_PATH = PROJECT_ROOT / "data" / "config" / "snapshot_budget.json"
ARCHIVE_POLICY = "phase2_views_primary"


def _activate_temp_output() -> tuple[Path, Path, Path, tuple[Path, Path, Path]]:
    """Redirect DB + view writes to an OS temp dir for smoke tests."""
    temp_root = Path(tempfile.mkdtemp(prefix="wd-snapshot-"))
    temp_db = temp_root / "research_market.sqlite"
    temp_views = temp_root / "views"
    original = (db.CLOUD_DB_PATH, views_builder.VIEWS_DIR, alerts.VIEWS_DIR)
    db.CLOUD_DB_PATH = temp_db
    views_builder.VIEWS_DIR = temp_views
    alerts.VIEWS_DIR = temp_views
    return temp_root, temp_db, temp_views, original


def _restore_output_paths(original: tuple[Path, Path, Path]) -> None:
    db.CLOUD_DB_PATH, views_builder.VIEWS_DIR, alerts.VIEWS_DIR = original


def _load_budget() -> dict:
    try:
        return json.loads(BUDGET_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"stage1_max_tickers": 600, "stage2_max_tickers": 60}


def _result_status(failed: int, total: int) -> str:
    if total <= 0:
        return "success"
    pct = failed / total
    if pct >= 0.50:
        return "failed"
    if pct >= 0.30:
        return "partial"
    if pct >= 0.05:
        return "degraded"
    return "success"


def _prior_consecutive_success(conn) -> int:
    row = conn.execute(
        "SELECT consecutive_success_count, result_status FROM snapshot_metadata "
        "ORDER BY executed_at_utc DESC LIMIT 1").fetchone()
    if not row:
        return 0
    return (row["consecutive_success_count"] or 0) if row["result_status"] == "success" else 0


def _record_failed_jobs(conn, run_date: str, job_name: str, tickers: list[str]) -> None:
    for t in tickers:
        conn.execute(
            "INSERT INTO failed_jobs (run_date, job_name, ticker, error, retry_count) "
            "VALUES (?,?,?,?,0)", (run_date, job_name, t, "fetch/processing failed"))


def _diversified_ticker_order(rows, cap: int, *, date_key: str,
                              score_key: str = "score",
                              sector_key: str = "sector") -> list[str]:
    """Score-first, sector-balanced, lightly rotating ticker order.

    A pure ORDER BY score keeps showing the same mega-cap names. This preserves
    high-conviction names while forcing breadth across sectors and rotating the
    long tail by date so the daily snapshot discovers more of the market over
    time without increasing API budget.
    """
    if cap <= 0:
        return []
    items = [dict(r) for r in rows]
    if not items:
        return []

    def sc(x):
        return float(x.get(score_key) or 0.0)

    items.sort(key=lambda x: (-sc(x), str(x.get("ticker") or "")))
    selected, seen = [], set()

    def add(x):
        t = x.get("ticker")
        if t and t not in seen and len(selected) < cap:
            selected.append(t)
            seen.add(t)

    # Keep a conviction core; diversify the remaining slots.
    seed_n = min(cap, max(8, int(cap * 0.35)))
    for x in items[:seed_n]:
        add(x)

    buckets: dict[str, list[dict]] = {}
    for x in items:
        if x.get("ticker") in seen:
            continue
        key = str(x.get(sector_key) or "UNKNOWN")
        buckets.setdefault(key, []).append(x)

    # Date-based offset changes which same-sector names get inspected first.
    try:
        offset = sum(ord(c) for c in date_key) % max(1, len(buckets))
    except Exception:
        offset = 0
    sector_order = sorted(buckets, key=lambda k: (-sc(buckets[k][0]), k))
    sector_order = sector_order[offset:] + sector_order[:offset]

    max_per_sector = max(4, int(cap / max(1, min(11, len(sector_order)))) + 3)
    sector_counts = {k: sum(1 for t in selected
                            for x in items if x.get("ticker") == t
                            and str(x.get(sector_key) or "UNKNOWN") == k)
                     for k in sector_order}
    progressed = True
    while len(selected) < cap and progressed:
        progressed = False
        for key in sector_order:
            bucket = buckets.get(key) or []
            while bucket and sector_counts.get(key, 0) >= max_per_sector:
                # keep the bucket for the final fill if we run out elsewhere
                break
            if not bucket or sector_counts.get(key, 0) >= max_per_sector:
                continue
            add(bucket.pop(0))
            sector_counts[key] = sector_counts.get(key, 0) + 1
            progressed = True
            if len(selected) >= cap:
                break

    # Fill any remaining slots by score.
    for x in items:
        add(x)
        if len(selected) >= cap:
            break
    return selected


def _macro(fetcher: Fetcher, market_date: str, log) -> dict:
    written = 0
    with db.cloud() as conn:
        for sid in FRED_DEFAULT_SERIES:
            for o in fetcher.fetch_fred_series(sid):
                if o["date"] <= market_date:
                    db.upsert(conn, "macro_daily", {
                        "date": o["date"], "series_id": sid, "value": o["value"],
                        "usable_at": o["date"], "source": "fred",
                    }, conflict_cols=("date", "series_id"))
                    written += 1
    log.info("macro_done", series=len(FRED_DEFAULT_SERIES), rows=written)
    return {"series": len(FRED_DEFAULT_SERIES), "rows": written}


def _git_commit_push(market_date: str, log) -> dict:
    """Best-effort commit + push of cloud artifacts. Personal data never staged."""
    try:
        subprocess.run(["git", "config", "user.name", "snapshot-bot"], cwd=PROJECT_ROOT, check=False)
        subprocess.run(["git", "config", "user.email", "snapshot@local"], cwd=PROJECT_ROOT, check=False)
        subprocess.run(["git", "add", "data/db/research_market.sqlite", "data/views/",
                        "data/snapshots/"], cwd=PROJECT_ROOT, check=False)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_ROOT)
        if diff.returncode == 0:
            log.info("git_nochange")
            return {"committed": False, "reason": "no changes"}
        subprocess.run(["git", "commit", "-m", f"snapshot {market_date}"],
                       cwd=PROJECT_ROOT, check=True)
        for attempt in range(1, 4):
            pull = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=PROJECT_ROOT)
            push = subprocess.run(["git", "push", "origin", "main"], cwd=PROJECT_ROOT)
            if push.returncode == 0:
                log.info("git_pushed", attempt=attempt)
                return {"committed": True, "pushed": True}
            time.sleep(attempt * 5)
        return {"committed": True, "pushed": False}
    except Exception as e:
        log.error("git_failed", err=str(e))
        return {"committed": False, "error": str(e)}


def run(*, market_date: str | None = None, synthetic: bool = False,
        limit: int | None = None, skip_git: bool = False, force: bool = False,
        store_raw: bool | None = None) -> dict:
    """Execute the snapshot. Returns a summary dict (also the metadata source)."""
    started = time.monotonic()
    run_id = str(uuid.uuid4())[:12]
    today = datetime.now(timezone.utc).date().isoformat()
    market_date = market_date or cal.most_recent_trading_day(today).isoformat()
    executed_at = datetime.now(timezone.utc).isoformat()
    log = get_logger("daily_snapshot", run_id=run_id, market_date=market_date)

    # synthetic must never commit or store raw to a real DB
    if synthetic:
        skip_git = True
        store_raw = False if store_raw is None else store_raw
    store_raw = True if store_raw is None else store_raw

    summary: dict = {"run_id": run_id, "market_date": market_date,
                     "executed_at_utc": executed_at, "synthetic": synthetic,
                     "steps": {}}

    # ---- precheck: market open? ----
    if not force and not cal.is_trading_day(market_date):
        log.warning("market_closed", status=cal.market_status(market_date))
        summary["result_status"] = "market_closed"
        _write_metadata(run_id, market_date, executed_at, summary,
                        result_status="market_closed", elapsed=0)
        return summary

    # ---- precheck: migrations ----
    try:
        db.apply_migrations("cloud")
    except Exception as e:
        log.critical("migration_failed", err=str(e))
        summary["result_status"] = "failed"
        summary["error"] = f"migration: {e}"
        return summary

    # ---- precheck: API key presence (live runs) — log booleans only, never values ----
    if not synthetic:
        key_present = {
            "finnhub": bool(os.environ.get("FINNHUB_KEY")),
            "fred": bool(os.environ.get("FRED_KEY")),
            "sec_user_agent": bool(os.environ.get("SEC_USER_AGENT")),
        }
        log.info("api_key_presence", **key_present)
        if not key_present["finnhub"]:
            log.warning("finnhub_key_missing",
                        note="prices fall back to keyless sources; fundamentals favor SEC (keyless)")
        if not key_present["fred"]:
            log.warning("fred_key_missing", note="macro_daily (FRED) will be skipped/empty")
        summary["api_key_presence"] = key_present

    fetcher = Fetcher(synthetic=synthetic, store_raw=store_raw, as_of=market_date, log=log)
    budget = _load_budget()

    # ===== 1. Universe =====
    with timed(log, "universe"):
        ures = universe_builder.build(build_date=market_date)
    summary["steps"]["universe"] = {"total": ures["total_tickers"]}

    # Pick price tickers: top by discovery_score (+ benchmarks + sector ETFs)
    with db.cloud(readonly=True) as conn:
        rows = conn.execute(
            "SELECT um.ticker, MAX(um.discovery_score) score, "
            "COALESCE(tm.sector, 'UNKNOWN') sector "
            "FROM universe_membership um LEFT JOIN ticker_master tm ON tm.ticker=um.ticker "
            "WHERE um.date=? AND um.active=1 "
            "GROUP BY um.ticker ORDER BY score DESC",
            (market_date,)).fetchall()
        model_types = {
            r["ticker"]: classify_model_type(
                r["ticker"], sector=r["sector"],
                is_etf=bool(r["is_etf"]), is_leveraged=bool(r["is_leveraged"]),
                seed_model_type=r["model_type"])
            for r in conn.execute(
                "SELECT ticker, sector, model_type, is_etf, is_leveraged "
                "FROM ticker_master").fetchall()}
    cap = limit or budget.get("stage1_max_tickers", 600)
    universe_tickers = [r["ticker"] for r in rows]
    price_tickers = _diversified_ticker_order(rows, cap, date_key=market_date)
    for extra in BENCHMARKS + SECTOR_ETFS:
        if extra not in price_tickers:
            price_tickers.append(extra)
    summary["steps"]["price_universe"] = len(price_tickers)

    # ===== 2. Prices =====
    with timed(log, "prices", n=len(price_tickers)):
        pstats = prices.update_prices(price_tickers, fetcher=fetcher,
                                      market_date=market_date, log=log)
    summary["steps"]["prices"] = {k: pstats[k] for k in
                                  ("tickers_ok", "tickers_failed", "bars_written",
                                   "splits_detected", "source_counts")}
    # per-provider failure reasons → snapshot_metadata → latest_snapshot_health
    summary["price_provider_failures"] = pstats.get("provider_failures", {})
    if pstats["failed_tickers"]:
        with db.cloud() as conn:
            _record_failed_jobs(conn, market_date, "prices", pstats["failed_tickers"][:200])

    priced = [t for t in price_tickers if t not in pstats["failed_tickers"]]

    # ===== 3. Macro =====
    with timed(log, "macro"):
        summary["steps"]["macro"] = _macro(fetcher, market_date, log)

    # ===== 4. Features (Stage 1) =====
    with timed(log, "features", n=len(priced)):
        fstats = feature_builder.build_features(priced, feature_date=market_date,
                                                model_types=model_types, log=log)
    summary["steps"]["features"] = fstats

    # ===== 5. Stage-1 ranking → Stage-2 candidate set =====
    stage2_target = budget.get("stage2_max_tickers", 60)
    stage2 = feature_builder.diversified_top_candidates(market_date, limit=stage2_target)

    # ===== 6. Reconciliation (Stage 2 only — budget) =====
    with timed(log, "reconciliation", n=len(stage2)):
        rstats = reconciliation.reconcile_universe(stage2, date=market_date,
                                                   fetcher=fetcher, log=log)
    summary["steps"]["reconciliation"] = rstats
    summary["reconciliation"] = rstats

    # ===== 7. Regime + themes (needed by the deep feature pass) =====
    with timed(log, "regime"):
        regime_builder.compute_themes(market_date, log=log)
        reg = regime_builder.compute_regime(market_date, log=log)
    summary["steps"]["regime"] = {"regime": reg["regime"], "confidence": reg["confidence"]}

    # ===== 8. Fundamentals (Stage 2 deep — §6 model-type pipeline) =====
    with db.cloud(readonly=True) as conn:
        sectors = {r["ticker"]: r["sector"] for r in conn.execute(
            "SELECT ticker, sector FROM ticker_master").fetchall()}
    with timed(log, "fundamentals", n=len(stage2)):
        finstats = financials.build_financials(
            stage2, fetcher=fetcher, as_of=market_date,
            model_types=model_types, sectors=sectors, log=log)
    summary["steps"]["fundamentals"] = finstats

    # ===== 8.5. Corporate actions (Alpaca splits + dividends — §33) =====
    # Stage-2 candidate set + benchmarks. Graceful skip when no ALPACA key.
    ca_tickers = list(dict.fromkeys(stage2 + BENCHMARKS))
    with timed(log, "corporate_actions", n=len(ca_tickers)):
        with db.cloud() as ca_conn:
            castats = corporate_actions.sync_universe(
                ca_conn, ca_tickers, market_date, log=log)
    summary["steps"]["corporate_actions"] = castats

    # ===== 9. Stage-2 deep re-score (regime + themes + fundamentals now present) =====
    with timed(log, "features_deep", n=len(stage2)):
        f2 = feature_builder.build_features(
            stage2, feature_date=market_date, model_types=model_types,
            sectors=sectors, log=log)
    summary["steps"]["features_deep"] = {k: f2[k] for k in (
        "written", "by_type", "bq_na_etf", "fundamentals_used",
        "valuation_na_synthetic", "valuation_enabled", "mixed_price_history",
        "by_price_class", "skipped_no_data"
    ) if k in f2}

    # ===== 10. Signals (Stage 2 deep) =====
    with timed(log, "signals", n=len(stage2)):
        sstats = signal_generator.generate_signals(
            stage2, date=market_date, replace_existing=True, log=log)
    summary["steps"]["signals"] = sstats

    # ===== 9.5. Factor exposures (weekly — Phase 9 stress test) =====
    # Only computes once per ISO-week; re-runs on same week are no-ops.
    factor_tickers = list(set(priced + BENCHMARKS))
    with timed(log, "factor_model", n=len(factor_tickers)):
        with db.cloud() as fm_conn:
            fmstats = factor_model.compute_and_store(
                factor_tickers, as_of=market_date, conn=fm_conn, log=log)
    summary["steps"]["factor_model"] = {
        "computed": fmstats["computed"],
        "skipped": fmstats["skipped"],
    }

    # ===== 8. Outcomes =====
    with timed(log, "outcomes"):
        ostats = outcome_updater.update_outcomes(market_date, log=log)
    summary["steps"]["outcomes"] = {k: ostats[k] for k in ("updated", "by_horizon")}

    # ===== 9. Metadata (must precede views — views read snapshot_metadata) =====
    failed_count = pstats["tickers_failed"]
    total_for_status = max(1, len(price_tickers))
    result_status = _result_status(failed_count, total_for_status)
    elapsed = round(time.monotonic() - started, 1)

    summary.update({
        "result_status": result_status,
        "stage1_scanned": fstats["written"],
        "stage2_scanned": sstats["signals"],
        "stage2_target": stage2_target,
        "failed_count": failed_count,
        "elapsed_seconds": elapsed,
        "candidates": sstats.get("by_type", {}),
    })
    _write_metadata(run_id, market_date, executed_at, summary,
                    result_status=result_status, elapsed=elapsed, fetcher=fetcher)

    # ===== 10. Views (now reflect the completed run's metadata) =====
    with timed(log, "views"):
        views_builder.build_all_views(market_date, executed_at_utc=executed_at, log=log)

    # ===== 11. Alerts =====
    alert_list = alerts.evaluate_alerts(market_date, summary, log=log)
    alerts.write_alerts_view(market_date, alert_list, {
        "snapshot_date": market_date, "executed_at_utc": executed_at})
    summary["alerts"] = {"critical": sum(1 for a in alert_list if a["severity"] == "critical"),
                         "warning": sum(1 for a in alert_list if a["severity"] == "warning")}

    # housekeeping: prune expired raw responses
    if not synthetic:
        prune_raw_responses()

    # ===== 11. Git =====
    if not skip_git:
        summary["git"] = _git_commit_push(market_date, log)

    log.info("snapshot_complete", result_status=result_status, elapsed=elapsed,
             critical_alerts=summary["alerts"]["critical"])
    return summary


def _write_metadata(run_id, market_date, executed_at, summary, *,
                    result_status, elapsed, fetcher=None) -> None:
    api_stats = fetcher.stats_summary() if fetcher else {}
    db_size_kb = 0
    try:
        db_size_kb = int(os.path.getsize(db.CLOUD_DB_PATH) / 1024)
    except OSError:
        pass
    with db.cloud() as conn:
        consec = _prior_consecutive_success(conn)
        consec = consec + 1 if result_status == "success" else 0
        db.upsert(conn, "snapshot_metadata", {
            "run_id": run_id, "market_date": market_date,
            "executed_at_utc": executed_at,
            "total_tickers_seen": summary.get("steps", {}).get("universe", {}).get("total", 0),
            "stage1_scanned": summary.get("stage1_scanned", 0),
            "stage2_scanned": summary.get("stage2_scanned", 0),
            "stage2_target": summary.get("stage2_target", 0),
            "api_calls_by_provider_json": json.dumps(api_stats.get("calls_by_provider", {})),
            "rate_limit_hits": api_stats.get("rate_limit_hits", 0),
            "skipped_due_to_budget": 0,
            "retry_count": 0,
            "failed_count": summary.get("failed_count", 0),
            "elapsed_seconds": elapsed,
            "consecutive_success_count": consec,
            "result_status": result_status,
            "db_size_kb": db_size_kb,
            "repo_size_kb": 0,
            "archive_policy": ARCHIVE_POLICY,
            "price_provider_status_json": json.dumps(
                summary.get("price_provider_failures", {})),
        }, conflict_cols=("run_id",))


def main():
    ap = argparse.ArgumentParser(description="Daily snapshot orchestrator")
    ap.add_argument("--date", help="market date YYYY-MM-DD (default: most recent trading day)")
    ap.add_argument("--synthetic", action="store_true", help="offline deterministic data")
    ap.add_argument("--limit", type=int, help="cap price universe (testing)")
    ap.add_argument("--skip-git", action="store_true", help="don't commit/push")
    ap.add_argument("--force", action="store_true", help="run even on a market holiday")
    ap.add_argument("--temp-output", action="store_true",
                    help="write DB/views to an OS temp dir; recommended for synthetic checks")
    args = ap.parse_args()

    temp_info = None
    original_paths = None
    if args.temp_output:
        temp_root, temp_db, temp_views, original_paths = _activate_temp_output()
        temp_info = {
            "temp_output_dir": str(temp_root),
            "temp_db_path": str(temp_db),
            "temp_views_dir": str(temp_views),
        }

    try:
        out = run(market_date=args.date, synthetic=args.synthetic, limit=args.limit,
                  skip_git=args.skip_git or args.temp_output, force=args.force)
        if temp_info:
            out["temp_output"] = temp_info
    finally:
        if original_paths:
            _restore_output_paths(original_paths)

    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    # Non-zero exit on critical alert / failed status (GHA surfaces it)
    if out.get("result_status") in ("failed",) or out.get("alerts", {}).get("critical", 0) > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
