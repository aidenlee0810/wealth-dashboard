"""
db.py — SQLite helper for Wealth Dashboard Research Platform

Responsibilities:
  - Schema version tracking + migration application
  - Separate cloud DB (data/db/research_market.sqlite) and local DB (~/.wealth-dashboard/personal.sqlite)
  - Read-only connections for /api/db/query
  - Upsert helpers
  - Strict guards against personal data leakage into cloud DB

Cloud/Local separation:
  - cloud DB:  PUBLIC market research, signals, outcomes — committed to git
  - local DB:  PERSONAL portfolio, user actions, accounts — NEVER committed

Both DBs use the same schema_version table pattern but live in different files.
"""

from __future__ import annotations

import os
import re
import sqlite3
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
CLOUD_DB_PATH = PROJECT_ROOT / "data" / "db" / "research_market.sqlite"
LOCAL_DB_PATH = Path.home() / ".wealth-dashboard" / "personal.sqlite"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# ---------------------------------------------------------------------------
# Constants — Personal table names that MUST NEVER exist in cloud DB
# ---------------------------------------------------------------------------
PERSONAL_TABLES = frozenset({
    "portfolio_snapshots",
    "user_target_weights",
    "user_actions",
    "account_buckets",
    "tax_lots",
    "personal_notes",
    "allocation_outputs",
})

# Cloud DB tables that /api/db/query may return.
# Strictly enforced — no SQL injection possible because keys are validated.
CLOUD_QUERY_ALLOWLIST = {
    "ticker_master": {
        "cols": ["ticker", "name", "exchange", "asset_type", "sector", "industry",
                 "model_type", "is_etf", "is_leveraged", "leverage_multiple", "active",
                 "days_since_ipo", "first_seen", "last_seen"],
        "order_by": ["ticker", "sector", "industry", "last_seen"],
        "default_order": "ticker ASC",
    },
    "universe_membership": {
        "cols": ["date", "ticker", "source", "universe_name", "reason",
                 "first_discovered_at", "last_seen_at", "days_active",
                 "source_buckets_json", "discovery_score", "active_score",
                 "active", "inactive_reason"],
        "order_by": ["date", "discovery_score", "days_active", "ticker"],
        "default_order": "date DESC, discovery_score DESC",
    },
    "prices_daily": {
        "cols": ["date", "ticker", "open", "high", "low", "close", "adj_close",
                 "volume", "source", "reconciled"],
        "order_by": ["date", "ticker"],
        "default_order": "date DESC",
    },
    "corporate_actions": {
        "cols": ["ticker", "ex_date", "action_type", "split_ratio", "dividend", "notes", "source"],
        "order_by": ["ex_date", "ticker"],
        "default_order": "ex_date DESC",
    },
    "ticker_changes": {
        "cols": ["effective_date", "old_ticker", "new_ticker", "reason", "notes"],
        "order_by": ["effective_date"],
        "default_order": "effective_date DESC",
    },
    "fundamentals_quarterly": {
        "cols": ["ticker", "fiscal_period", "report_date", "usable_at",
                 "revenue", "operating_income", "net_income", "fcf",
                 "gross_margin", "operating_margin", "fcf_margin",
                 "roic", "roe", "nd_ebitda", "int_cov", "altman_z", "piotroski_f"],
        "order_by": ["ticker", "fiscal_period", "usable_at"],
        "default_order": "usable_at DESC",
    },
    "bank_fundamentals_q": {
        "cols": ["ticker", "fiscal_period", "report_date", "usable_at",
                 "net_interest_margin", "efficiency_ratio", "tangible_book",
                 "capital_ratio", "credit_quality", "roa", "roe"],
        "order_by": ["ticker", "fiscal_period"],
        "default_order": "usable_at DESC",
    },
    "reit_fundamentals_q": {
        "cols": ["ticker", "fiscal_period", "report_date", "usable_at",
                 "ffo_per_share", "affo_per_share", "occupancy",
                 "debt_maturity_yrs", "int_cov", "dividend_safety"],
        "order_by": ["ticker", "fiscal_period"],
        "default_order": "usable_at DESC",
    },
    "macro_daily": {
        "cols": ["date", "series_id", "value", "release_date", "usable_at", "source"],
        "order_by": ["date", "series_id"],
        "default_order": "date DESC",
    },
    "sector_theme_daily": {
        "cols": ["date", "name", "type", "ret1m", "ret3m", "ret6m", "ret_ytd", "ret1y",
                 "trend_score", "breadth_score", "rel_strength", "leader_count",
                 "persistence", "phase", "leadership_score"],
        "order_by": ["date", "leadership_score", "trend_score"],
        "default_order": "date DESC, leadership_score DESC",
    },
    "features_daily": {
        "cols": ["date", "ticker", "bq_score", "bq_coverage_ratio", "val_score",
                 "growth_score", "tech_score", "sec_theme_score", "macro_score",
                 "risk_score", "dq_score", "regime_fit_score", "composite_score",
                 "state", "rsi_14", "price_vs_sma50", "price_vs_sma200",
                 "model_type", "feature_version", "usable_at"],
        "order_by": ["date", "composite_score", "dq_score"],
        "default_order": "date DESC, composite_score DESC",
    },
    "market_regime_daily": {
        "cols": ["date", "regime", "secondary", "confidence",
                 "favored_themes_json", "avoided_themes_json",
                 "favored_sectors_json", "emerging_themes_json", "fading_themes_json",
                 "effective_weights_json", "regime_version"],
        "order_by": ["date", "confidence"],
        "default_order": "date DESC",
    },
    "candidate_snapshots": {
        "cols": ["date", "ticker", "candidate_type", "score", "source_buckets_json",
                 "discovery_reason", "sector", "themes_json", "blockers_json",
                 "reasons_json", "suggested_target_weight", "dq_score",
                 "bq_coverage_ratio", "coverage_warning", "risk_adjusted_weight",
                 "risk_size_multiplier", "risk_reason", "risk_manual_checks_json"],
        "order_by": ["date", "score", "candidate_type"],
        "default_order": "date DESC, score DESC",
    },
    "generic_signals": {
        "cols": ["signal_id", "date", "ticker", "source", "state", "action",
                 "composite_score", "business_quality_score", "valuation_score",
                 "growth_score", "tech_score", "sec_theme_score", "macro_score",
                 "risk_score", "dq_score", "regime_fit_score", "market_regime",
                 "confidence", "target_weight_generic", "suggested_buy_generic",
                 "blockers_json", "reasons_json", "next_trigger", "invalidation",
                 "risk_status", "risk_flags_json", "risk_adjusted_weight",
                 "risk_size_multiplier", "risk_reason", "risk_manual_checks_json",
                 "model_id", "feature_version", "code_commit_sha"],
        "order_by": ["date", "composite_score", "ticker"],
        "default_order": "date DESC",
    },
    "signal_outcomes": {
        "cols": ["signal_id", "horizon", "exit_date", "abs_ret", "spy_ret", "qqq_ret",
                 "sector_ret", "theme_ret", "rel_spy", "rel_qqq", "rel_sector",
                 "rel_theme", "mae", "mfe", "mdd", "hit_invalidation",
                 "outcome_label", "outcome_reason"],
        "order_by": ["horizon"],
        "default_order": "horizon ASC",
    },
    "backtest_runs": {
        "cols": ["run_id", "created_at", "strategy_name", "start_date", "end_date",
                 "cagr", "total_return", "max_drawdown", "volatility",
                 "sharpe", "sharpe_ci_lower", "sharpe_ci_upper", "deflated_sharpe",
                 "sortino", "calmar", "hit_rate", "profit_factor", "expected_value",
                 "pbo", "whites_p_value", "cv_method", "n_oos_periods",
                 "lookahead_check_passed", "survivorship_bias_risk",
                 "pit_features_available", "results_status", "bias_warning_message",
                 "universe_version", "feature_version"],
        "order_by": ["created_at", "sharpe"],
        "default_order": "created_at DESC",
    },
    "failed_jobs": {
        "cols": ["job_id", "run_date", "job_name", "ticker", "error",
                 "retry_count", "last_error_at"],
        "order_by": ["run_date", "retry_count"],
        "default_order": "run_date DESC",
    },
    "snapshot_metadata": {
        "cols": ["run_id", "market_date", "executed_at_utc", "total_tickers_seen",
                 "stage1_scanned", "stage2_scanned", "stage2_target",
                 "api_calls_by_provider_json", "rate_limit_hits",
                 "skipped_due_to_budget", "retry_count", "failed_count",
                 "elapsed_seconds", "consecutive_success_count",
                 "result_status", "db_size_kb", "repo_size_kb", "archive_policy"],
        "order_by": ["market_date", "executed_at_utc"],
        "default_order": "market_date DESC",
    },
    "contract_violations": {
        "cols": ["violation_id", "detected_at", "source", "endpoint", "ticker",
                 "expected_schema_version", "error_message", "severity"],
        "order_by": ["detected_at", "severity"],
        "default_order": "detected_at DESC",
    },
    "reconciliation_log": {
        "cols": ["log_id", "date", "ticker", "field", "value_a", "value_b",
                 "diff_pct", "source_a", "source_b", "action"],
        "order_by": ["date", "diff_pct"],
        "default_order": "date DESC, diff_pct DESC",
    },
    "metrics_timeseries": {
        "cols": ["ts", "metric_name", "value", "tags_json"],
        "order_by": ["ts", "metric_name"],
        "default_order": "ts DESC",
    },
    "model_registry": {
        "cols": ["model_id", "family", "version", "status", "created_at",
                 "promoted_at", "retired_at", "description",
                 "shadow_period_start", "shadow_period_end", "shadow_signal_count",
                 "shadow_hit_rate", "shadow_ev",
                 "shadow_vs_production_correlation", "graduation_decision"],
        "order_by": ["created_at", "status"],
        "default_order": "created_at DESC",
    },
    "factor_exposures_weekly": {
        "cols": ["ticker", "week_end_date", "alpha", "b_mkt", "b_smb",
                 "b_hml", "b_rmw", "b_cma", "r_squared", "n_obs"],
        "order_by": ["week_end_date", "ticker"],
        "default_order": "week_end_date DESC",
    },
    "cleaned_financials": {
        "cols": ["ticker", "fiscal_period", "report_date", "usable_at",
                 "revenue", "cost_of_revenue", "gross_profit", "operating_income",
                 "net_income", "cfo", "capex", "fcf", "sbc", "total_debt",
                 "cash_and_sti", "total_equity", "shares_out", "source",
                 "cleaned_at", "schema_version"],
        "order_by": ["ticker", "fiscal_period", "usable_at"],
        "default_order": "usable_at DESC",
    },
    "normalized_financials": {
        "cols": ["ticker", "fiscal_period", "usable_at", "sector", "model_type",
                 "gross_margin", "operating_margin", "fcf_margin", "net_margin",
                 "roic", "roe", "nd_ebitda", "int_cov", "revenue_growth_yoy",
                 "sbc_pct_revenue", "sector_op_margin_p50", "op_margin_zscore",
                 "sector_fcf_margin_p50", "fcf_margin_zscore", "roic_zscore",
                 "revenue_growth_zscore", "coverage_ratio", "feature_version"],
        "order_by": ["ticker", "fiscal_period", "sector"],
        "default_order": "usable_at DESC",
    },
    "schema_version": {
        "cols": ["version", "applied_at", "description", "db_role"],
        "order_by": ["version"],
        "default_order": "version DESC",
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("db")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def ensure_cloud_dir() -> None:
    CLOUD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def ensure_local_dir() -> None:
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def connect_cloud(readonly: bool = False) -> sqlite3.Connection:
    """Open cloud DB. Use readonly=True for /api/db/query.

    readonly=True  → uri mode=ro + PRAGMA query_only=ON  (no WAL — file is already open elsewhere)
    readonly=False → normal open + PRAGMA journal_mode=WAL
    """
    ensure_cloud_dir()
    if readonly:
        uri = f"file:{CLOUD_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")   # extra guard: any write raises OperationalError
    else:
        conn = sqlite3.connect(str(CLOUD_DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # better read concurrency for writes
    return conn


def connect_local(readonly: bool = False) -> sqlite3.Connection:
    """Open local personal DB. NEVER expose via /api/db/query.

    Same pattern as connect_cloud: readonly uses query_only, writable uses WAL.
    """
    ensure_local_dir()
    if readonly:
        uri = f"file:{LOCAL_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
    else:
        conn = sqlite3.connect(str(LOCAL_DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def cloud(readonly: bool = False):
    conn = connect_cloud(readonly=readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def local(readonly: bool = False):
    conn = connect_local(readonly=readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration system
# ---------------------------------------------------------------------------

# Strict filename pattern: NNN_(cloud|local)_<description>.sql
# Examples:  001_cloud_initial.sql   002_local_personal.sql   003_cloud_features.sql
# Rejected:  001_initial.sql (no role)   003_cloud_local_x.sql (ambiguous)
_MIGRATION_PATTERN = re.compile(r'^(\d{3})_(cloud|local)_.+\.sql$')


def _get_current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        return row["v"] if row and row["v"] is not None else 0
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet
        return 0


def apply_migrations(db_role: str = "cloud") -> int:
    """Apply migrations for the specified role ('cloud' or 'local').

    Migrations are applied idempotently — already-applied filenames are skipped.
    Returns the count of newly applied migrations.

    Filename convention (strictly enforced):
        NNN_(cloud|local)_<description>.sql
        e.g.  001_cloud_initial.sql   002_local_personal.sql

    Files that don't match _MIGRATION_PATTERN are SKIPPED with a warning.
    Files with ambiguous roles (pattern mismatch) are also skipped.
    This ensures 003_cloud_x.sql is NEVER applied to local DB and vice versa.

    Idempotency: tracks applied versions in schema_version.  Re-runs are safe.
    """
    assert db_role in ("cloud", "local"), f"Invalid db_role: {db_role}"

    all_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    role_files: list[tuple[int, Path]] = []

    for f in all_files:
        m = _MIGRATION_PATTERN.match(f.name)
        if not m:
            log.warning(
                "Skipping migration '%s': filename does not match required pattern "
                "NNN_(cloud|local)_<description>.sql", f.name
            )
            continue
        file_version = int(m.group(1))
        file_role = m.group(2)
        if file_role != db_role:
            continue   # belongs to the other DB — skip silently
        role_files.append((file_version, f))

    if not role_files:
        log.warning("No valid migration files found for role=%s in %s", db_role, MIGRATIONS_DIR)
        return 0

    conn_cm = cloud() if db_role == "cloud" else local()
    applied = 0
    with conn_cm as conn:
        # Bootstrap schema_version table if needed
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT,
                db_role     TEXT NOT NULL CHECK(db_role IN ('cloud', 'local'))
            )
        """)
        # Set of already-applied version numbers (for this db_role)
        applied_versions = {
            r["version"] for r in conn.execute(
                "SELECT version FROM schema_version WHERE db_role = ?",
                (db_role,)
            ).fetchall()
        }
        log.info("%s DB: applied versions = %s", db_role, sorted(applied_versions))

        for version, f in role_files:
            if version in applied_versions:
                log.debug("%s: skipping already-applied migration %s", db_role, f.name)
                continue

            log.info("Applying %s migration: %s (version %d)", db_role, f.name, version)
            sql = f.read_text()
            conn.executescript(sql)
            # Record by FILENAME version
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, description, db_role) "
                "VALUES (?, ?, ?)",
                (version, f.stem, db_role)
            )
            applied += 1

    log.info("Applied %d new %s migration(s)", applied, db_role)
    return applied


def init_both_dbs() -> dict[str, int]:
    """Initialize both cloud and local DBs. Returns count of migrations applied to each."""
    cloud_applied = apply_migrations("cloud")
    local_applied = apply_migrations("local")
    return {"cloud": cloud_applied, "local": local_applied}


# ---------------------------------------------------------------------------
# Personal data leak guard
# ---------------------------------------------------------------------------
def assert_no_personal_tables_in_cloud() -> None:
    """Verify cloud DB does not contain any personal-data tables.

    This is the linchpin of Cloud/Local separation.
    Run this from CI and from snapshot job pre-checks.
    """
    with cloud(readonly=True) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        existing = {r["name"] for r in rows}
        leaks = existing & PERSONAL_TABLES
        if leaks:
            raise RuntimeError(
                f"SECURITY VIOLATION: Personal tables found in cloud DB: {leaks}. "
                "These tables must only exist in ~/.wealth-dashboard/personal.sqlite. "
                "Drop them from cloud DB before committing."
            )


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------
def upsert(
    conn: sqlite3.Connection,
    table: str,
    row: dict[str, Any],
    conflict_cols: Sequence[str],
) -> None:
    """Generic INSERT ... ON CONFLICT DO UPDATE."""
    cols = list(row.keys())
    placeholders = ",".join(["?"] * len(cols))
    col_list = ",".join(cols)
    conflict_list = ",".join(conflict_cols)
    update_clause = ",".join(
        f"{c}=excluded.{c}" for c in cols if c not in conflict_cols
    )
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_list}) DO UPDATE SET {update_clause}"
    )
    conn.execute(sql, list(row.values()))


def upsert_many(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
    conflict_cols: Sequence[str],
) -> int:
    count = 0
    for row in rows:
        upsert(conn, table, row, conflict_cols)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Whitelisted query execution (used by /api/db/query)
# ---------------------------------------------------------------------------
class QueryError(Exception):
    """Raised when /api/db/query receives an invalid request."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_and_query(
    table: str,
    *,
    cols: Optional[Sequence[str]] = None,
    where: Optional[dict[str, Any]] = None,
    order_by: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Run a strictly-validated read query against the cloud DB.

    - table must be in CLOUD_QUERY_ALLOWLIST
    - cols (if given) must all be in the allowlist for that table
    - where keys must all be in the allowlist for that table
    - order_by must be in the allowlist for that table (or NULL to use default)
    - limit is clamped to [1, 500]
    - personal tables are explicitly rejected

    Returns list of dicts.
    """
    # Block personal tables explicitly (defense in depth)
    if table in PERSONAL_TABLES:
        raise QueryError(
            f"Table '{table}' is personal and cannot be queried via /api/db/query. "
            "Use /api/local/* endpoints instead.",
            status=403,
        )

    if table not in CLOUD_QUERY_ALLOWLIST:
        raise QueryError(f"Table '{table}' not in allowlist", status=403)

    spec = CLOUD_QUERY_ALLOWLIST[table]
    allowed_cols = set(spec["cols"])
    allowed_order = set(spec["order_by"])

    # Validate cols
    if cols is None:
        cols = spec["cols"]
    else:
        for c in cols:
            if c not in allowed_cols:
                raise QueryError(f"Column '{c}' not allowed for table '{table}'", status=403)

    # Validate where keys
    where_clauses: list[str] = []
    args: list[Any] = []
    if where:
        for key, value in where.items():
            # Support simple operators via "field__op" pattern
            op = "="
            field = key
            if "__" in key:
                field, op_name = key.rsplit("__", 1)
                op_map = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=",
                          "gt": ">", "gte": ">=", "like": "LIKE"}
                if op_name not in op_map:
                    raise QueryError(f"Operator '{op_name}' not allowed", status=403)
                op = op_map[op_name]
            if field not in allowed_cols:
                raise QueryError(f"Filter column '{field}' not allowed", status=403)
            where_clauses.append(f"{field} {op} ?")
            args.append(value)

    # Validate order_by
    if order_by:
        # Allow "column ASC|DESC" but only column name validated
        parts = order_by.strip().split()
        order_col = parts[0]
        if order_col not in allowed_order:
            raise QueryError(f"order_by column '{order_col}' not allowed", status=403)
        if len(parts) > 1 and parts[1].upper() not in {"ASC", "DESC"}:
            raise QueryError(f"order_by direction must be ASC or DESC", status=400)
        order_clause = " ".join(parts[:2]).upper().replace(order_col.upper(), order_col)
        # Reconstruct cleanly
        direction = parts[1].upper() if len(parts) > 1 else "ASC"
        order_clause = f"{order_col} {direction}"
    else:
        order_clause = spec["default_order"]

    # Clamp limit
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise QueryError("limit must be an integer", status=400)
    limit = max(1, min(500, limit))

    # Build query
    col_list = ",".join(cols)
    sql = f"SELECT {col_list} FROM {table}"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += f" ORDER BY {order_clause}"
    sql += f" LIMIT {limit}"

    with cloud(readonly=True) as conn:
        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError as e:
            # Table may not exist yet (pre-migration)
            if "no such table" in str(e).lower():
                return []
            raise QueryError(f"Query error: {e}", status=500)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def health() -> dict:
    """Return summary of cloud DB state for /api/db/health."""
    info: dict[str, Any] = {
        "cloud_db_path": str(CLOUD_DB_PATH),
        "cloud_db_exists": CLOUD_DB_PATH.exists(),
        "cloud_db_size_kb": (
            CLOUD_DB_PATH.stat().st_size // 1024 if CLOUD_DB_PATH.exists() else 0
        ),
        "local_db_path": str(LOCAL_DB_PATH),
        "local_db_exists": LOCAL_DB_PATH.exists(),
        "schema_version_cloud": 0,
        "schema_version_local": 0,
        "tables_cloud": [],
        "row_counts_cloud": {},
        "last_snapshot": None,
        "last_snapshot_status": None,
    }

    if CLOUD_DB_PATH.exists():
        try:
            with cloud(readonly=True) as conn:
                info["schema_version_cloud"] = _get_current_version(conn)
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                info["tables_cloud"] = [t["name"] for t in tables]

                # Row counts for key tables (only if they exist)
                for tbl in ["ticker_master", "universe_membership", "prices_daily",
                            "features_daily", "generic_signals", "signal_outcomes",
                            "snapshot_metadata", "candidate_snapshots"]:
                    if tbl in info["tables_cloud"]:
                        try:
                            row = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()
                            info["row_counts_cloud"][tbl] = row["c"]
                        except sqlite3.OperationalError:
                            info["row_counts_cloud"][tbl] = None

                # Last snapshot info
                if "snapshot_metadata" in info["tables_cloud"]:
                    try:
                        last = conn.execute(
                            "SELECT market_date, executed_at_utc, result_status "
                            "FROM snapshot_metadata ORDER BY market_date DESC LIMIT 1"
                        ).fetchone()
                        if last:
                            info["last_snapshot"] = last["market_date"]
                            info["last_snapshot_executed_at"] = last["executed_at_utc"]
                            info["last_snapshot_status"] = last["result_status"]
                    except sqlite3.OperationalError:
                        pass
        except Exception as e:
            info["cloud_db_error"] = str(e)

    if LOCAL_DB_PATH.exists():
        try:
            with local(readonly=True) as conn:
                info["schema_version_local"] = _get_current_version(conn)
        except Exception as e:
            info["local_db_error"] = str(e)

    return info


# ---------------------------------------------------------------------------
# CLI entry: `python db.py init` to set up both DBs
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python db.py [init|health|verify]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "init":
        result = init_both_dbs()
        print(json.dumps({"applied_migrations": result, "health": health()}, indent=2, default=str))
    elif cmd == "health":
        print(json.dumps(health(), indent=2, default=str))
    elif cmd == "verify":
        try:
            assert_no_personal_tables_in_cloud()
            print("✅ Cloud DB has no personal tables.")
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
