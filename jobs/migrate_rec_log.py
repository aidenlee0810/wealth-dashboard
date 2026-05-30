#!/usr/bin/env python3
"""
jobs/migrate_rec_log.py — One-time backfill: localStorage REC_LOG → SQLite

Reads the exported `wr_recommendation_log` JSON (array of signal entries)
and inserts each into:
  - generic_signals (one row per signal)
  - signal_outcomes (one row per (signal, horizon) — horizons 5/20/60/120 today, 1D added going forward)

Usage:
    # Step 1: In browser, run:
    #   copy(localStorage.getItem('wr_recommendation_log'))
    # Step 2: Paste into file rec_log_export.json
    # Step 3:
    python jobs/migrate_rec_log.py rec_log_export.json

Idempotent: same signal_id upsert.
"""

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from jobs._logging import get_logger  # noqa: E402


def _derive_signal_id(entry: dict) -> str:
    """Stable hash so re-import doesn't create duplicates.

    Uses ticker + asOf (timestamp) + source as the natural key.
    """
    if entry.get("id"):
        return str(entry["id"])
    key_parts = [
        str(entry.get("ticker", "")),
        str(entry.get("asOf", entry.get("dayKey", ""))),
        str(entry.get("source", "")),
    ]
    h = hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]
    return f"legacy_{h}"


def _parse_signal_row(entry: dict, signal_id: str) -> dict:
    """Map localStorage entry → generic_signals row."""
    # Date normalization (asOf may be epoch ms; dayKey is YYYY-MM-DD)
    date = entry.get("dayKey")
    if not date and entry.get("asOf"):
        from datetime import datetime, timezone
        try:
            ts_ms = int(entry["asOf"])
            date = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date = None

    bench = entry.get("benchmarkAtSignal") or {}
    return {
        "signal_id": signal_id,
        "date": date,
        "ticker": entry.get("ticker"),
        "source": entry.get("source"),
        "state": entry.get("state"),
        "action": entry.get("action"),
        "composite_score":         entry.get("compositeScore"),
        "business_quality_score":  entry.get("businessQualityScore") or entry.get("fundamentalScore"),
        "valuation_score":         entry.get("valuationScore"),
        "growth_score":            entry.get("growthScore"),
        "tech_score":              entry.get("technicalTimingScore") or entry.get("technicalScore"),
        "sec_theme_score":         entry.get("sectorThemeScore"),
        "macro_score":             entry.get("macroScore"),
        "risk_score":              entry.get("riskScore"),
        "dq_score":                entry.get("dataQualityScore"),
        "regime_fit_score":        entry.get("regimeFit"),
        "market_regime":           entry.get("marketRegime") or entry.get("macroRegime"),
        "confidence":              entry.get("regimeConfidence"),
        "target_weight_generic":   None,
        "suggested_buy_generic":   entry.get("suggestedMonthlyBuy"),
        "blockers_json":           json.dumps(entry.get("blockers") or []),
        "reasons_json":            json.dumps(entry.get("reasons") or []),
        "next_trigger":            entry.get("nextTrigger"),
        "invalidation":            entry.get("invalidation"),
        "risk_status":             None,
        "risk_flags_json":         None,
        "lineage_json":            json.dumps({"migrated_from": "wr_recommendation_log_v1"}),
        "model_id":                "legacy_pre_v3",
        "feature_version":         "legacy",
        "code_commit_sha":         None,
        "benchmark_at_signal_json": json.dumps(bench),
    }


def _parse_outcome_rows(entry: dict, signal_id: str) -> list[dict]:
    """Map outcomes dict → signal_outcomes rows."""
    outcomes = entry.get("outcomes") or {}
    rows: list[dict] = []
    for horizon_key, outcome in outcomes.items():
        try:
            horizon = int(horizon_key)
        except (ValueError, TypeError):
            continue
        if not isinstance(outcome, dict):
            continue
        rows.append({
            "signal_id": signal_id,
            "horizon": horizon,
            "exit_date":   outcome.get("recordedAt"),
            "abs_ret":     outcome.get("ret"),
            "spy_ret":     outcome.get("spyRet"),
            "qqq_ret":     outcome.get("qqqRet"),
            "sector_ret":  None,
            "theme_ret":   None,
            "rel_spy":     outcome.get("relativeToSpy"),
            "rel_qqq":     outcome.get("relativeToQqq"),
            "rel_sector":  None,
            "rel_theme":   None,
            "mae":         None,
            "mfe":         None,
            "mdd":         None,
            "hit_invalidation": 0,
            "outcome_label":  outcome.get("label"),
            "outcome_reason": None,
        })
    return rows


def migrate(input_path: Path) -> dict:
    log = get_logger("migrate_rec_log")
    if not input_path.exists():
        log.error("input_file_missing", path=str(input_path))
        return {"status": "error", "reason": "input_file_missing"}

    raw = input_path.read_text(encoding="utf-8")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("json_parse_error", error=str(e))
        return {"status": "error", "reason": "json_parse_error"}

    if isinstance(entries, str):
        # Sometimes localStorage returns the JSON string wrapped in quotes
        try:
            entries = json.loads(entries)
        except json.JSONDecodeError:
            pass

    if not isinstance(entries, list):
        log.error("expected_array", got=type(entries).__name__)
        return {"status": "error", "reason": "expected_array"}

    log.info("entries_loaded", count=len(entries))

    signal_count = 0
    outcome_count = 0
    skipped = 0

    with db.cloud() as conn:
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("ticker"):
                skipped += 1
                continue
            sid = _derive_signal_id(entry)
            try:
                signal_row = _parse_signal_row(entry, sid)
                if not signal_row.get("date"):
                    log.warning("missing_date", signal_id=sid, ticker=signal_row.get("ticker"))
                    skipped += 1
                    continue
                db.upsert(conn, "generic_signals", signal_row, ["signal_id"])
                signal_count += 1

                for outcome_row in _parse_outcome_rows(entry, sid):
                    db.upsert(conn, "signal_outcomes", outcome_row, ["signal_id", "horizon"])
                    outcome_count += 1
            except Exception as e:
                log.error("entry_failed", signal_id=sid, ticker=entry.get("ticker"), error=str(e))
                skipped += 1

    log.info("migration_complete",
             signals_imported=signal_count,
             outcomes_imported=outcome_count,
             skipped=skipped,
             total_input=len(entries))

    return {
        "status": "success",
        "signals_imported": signal_count,
        "outcomes_imported": outcome_count,
        "skipped": skipped,
        "total_input": len(entries),
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill REC_LOG → SQLite cloud DB")
    parser.add_argument("input", help="Path to exported wr_recommendation_log JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN: parsing only")
        raw = Path(args.input).read_text()
        entries = json.loads(raw)
        if isinstance(entries, str):
            entries = json.loads(entries)
        print(f"Would import {len(entries)} entries.")
        for e in entries[:3]:
            sid = _derive_signal_id(e)
            row = _parse_signal_row(e, sid)
            print(json.dumps(row, indent=2, default=str)[:500])
        return

    result = migrate(Path(args.input))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
