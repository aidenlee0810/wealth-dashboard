"""
jobs/alerts.py — production alert rules (Plan §32).

Evaluates a small set of deterministic rules against the run's metrics + DB
state and writes data/views/latest_alerts.json for the UI. Critical alerts are
also returned so the orchestrator can set a non-zero exit (GHA emails on
failure) when something is genuinely broken.

Severity:
  critical → escalate (workflow failure / email); shown red in UI
  warning  → shown amber in UI, run still succeeds
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402

VIEWS_DIR = Path(__file__).resolve().parent.parent / "data" / "views"


def _contract_violations_24h(conn) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) n FROM contract_violations WHERE detected_at >= ?",
        (cutoff,)).fetchone()
    return row["n"] or 0


def evaluate_alerts(market_date: str, snapshot_stats: dict, *, log=None) -> list[dict]:
    """Return a list of alert dicts. snapshot_stats is the orchestrator summary."""
    log = log or get_logger("alerts")
    alerts: list[dict] = []

    def add(severity, rule, message, **ctx):
        alerts.append({"severity": severity, "rule": rule, "message": message,
                       "context": ctx, "at": datetime.now(timezone.utc).isoformat()})

    # --- failure rate (critical >30%, warning >5%) ---
    total = max(1, snapshot_stats.get("stage2_scanned", 0)
                or snapshot_stats.get("price_tickers_total", 1))
    failed = snapshot_stats.get("failed_count", 0)
    fail_pct = failed / total * 100 if total else 0
    if fail_pct > 30:
        add("critical", "snapshot.failed_rate",
            f"Snapshot failure rate {fail_pct:.0f}% (>30%)", failed=failed, total=total)
    elif fail_pct > 5:
        add("warning", "snapshot.failed_rate",
            f"Snapshot failure rate {fail_pct:.0f}% (>5%)", failed=failed, total=total)

    # --- reconciliation failure rate (critical >5%) ---
    rec = snapshot_stats.get("reconciliation", {}) or {}
    rec_rate = rec.get("failure_rate", 0) or 0
    if rec_rate > 5:
        add("critical", "reconciliation.failure_rate",
            f"Reconciliation failure rate {rec_rate:.1f}% (>5%) — verify price sources",
            flagged=rec.get("flagged_tickers", [])[:10])

    # --- duration (warning >3000s) ---
    elapsed = snapshot_stats.get("elapsed_seconds") or 0
    if elapsed > 3000:
        add("warning", "snapshot.duration",
            f"Snapshot took {elapsed:.0f}s (>3000s target)")

    with db.cloud(readonly=True) as conn:
        # --- contract violations 24h (critical >5) ---
        cv = _contract_violations_24h(conn)
        if cv > 5:
            add("critical", "contract_violation.count_24h",
                f"{cv} contract violations in 24h (>5) — possible API schema drift")

        # --- DQ distribution (warning >30% below 50) ---
        row = conn.execute(
            "SELECT AVG(CASE WHEN dq_score < 50 THEN 1.0 ELSE 0.0 END)*100 pct, COUNT(*) n "
            "FROM features_daily WHERE date=?", (market_date,)).fetchone()
        if row and row["n"] >= 20 and (row["pct"] or 0) > 30:
            add("warning", "dq.below_50",
                f"{row['pct']:.0f}% of tickers have DQ<50 (>30%)")

        # --- signal state churn vs yesterday (warning >20%) ---
        from market_calendar import previous_trading_day
        prev = previous_trading_day(market_date).isoformat()
        churn = conn.execute(
            "SELECT COUNT(*) changed FROM generic_signals t "
            "JOIN generic_signals y ON y.ticker=t.ticker AND y.date=? "
            "WHERE t.date=? AND t.state != y.state", (prev, market_date)).fetchone()
        tot_today = conn.execute(
            "SELECT COUNT(*) n FROM generic_signals WHERE date=?", (market_date,)).fetchone()["n"]
        if tot_today >= 20:
            pct = (churn["changed"] or 0) / tot_today * 100
            if pct > 20:
                add("warning", "signal.state_churn",
                    f"{pct:.0f}% of signals changed state vs {prev} (>20%) — possible regression")

    crit = sum(1 for a in alerts if a["severity"] == "critical")
    warn = sum(1 for a in alerts if a["severity"] == "warning")
    log.info("alerts_evaluated", critical=crit, warning=warn)
    return alerts


def write_alerts_view(market_date: str, alerts: list[dict], meta: dict) -> None:
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        **meta,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "critical_count": sum(1 for a in alerts if a["severity"] == "critical"),
        "warning_count": sum(1 for a in alerts if a["severity"] == "warning"),
        "alerts": alerts,
        "all_healthy": len(alerts) == 0,
    }
    (VIEWS_DIR / "latest_alerts.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
