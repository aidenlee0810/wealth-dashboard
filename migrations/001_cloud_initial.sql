-- ============================================================================
-- Migration 001: Cloud DB Initial Schema
-- ============================================================================
-- Target DB: data/db/research_market.sqlite  (PUBLIC, committed to git)
--
-- This DB holds MARKET RESEARCH DATA ONLY.
-- It must NEVER contain personal portfolio data, user actions, account info,
-- or any data that could identify the user's holdings.
--
-- All tables include audit columns (created_at).
-- All time-sensitive features include usable_at (point-in-time guard).
-- ============================================================================

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- Schema versioning
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    db_role     TEXT NOT NULL CHECK(db_role IN ('cloud', 'local'))
);

-- ---------------------------------------------------------------------------
-- 1. ticker_master — canonical ticker registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticker_master (
    ticker             TEXT PRIMARY KEY,
    name               TEXT,
    exchange           TEXT,
    asset_type         TEXT CHECK(asset_type IN ('stock','etf','leveraged_etf','single_stock_lev_etf','adr','pref','unknown')),
    sector             TEXT,
    industry           TEXT,
    country            TEXT DEFAULT 'US',
    currency           TEXT DEFAULT 'USD',
    is_etf             INTEGER DEFAULT 0,
    is_leveraged       INTEGER DEFAULT 0,
    leverage_multiple  REAL,
    model_type         TEXT CHECK(model_type IN ('operating_company','bank','insurance','reit','biotech_pre_revenue','etf','leveraged_etf','single_stock_lev_etf','unknown')),
    active             INTEGER DEFAULT 1,
    days_since_ipo     INTEGER,
    first_seen         DATE,
    last_seen          DATE,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticker_master_sector ON ticker_master(sector);
CREATE INDEX IF NOT EXISTS idx_ticker_master_active ON ticker_master(active);
CREATE INDEX IF NOT EXISTS idx_ticker_master_model_type ON ticker_master(model_type);

-- ---------------------------------------------------------------------------
-- 2. universe_membership — which tickers are in the scan universe on each date
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS universe_membership (
    date                TEXT NOT NULL,                  -- YYYY-MM-DD
    ticker              TEXT NOT NULL,
    source              TEXT NOT NULL,                  -- 'core_watchlist','sp500','nasdaq100','sector_holdings','theme_basket','momentum_discovery','event_news','user_added','13f_guru'
    universe_name       TEXT,                           -- 'theme_AI', 'sp500', etc.
    reason              TEXT,
    first_discovered_at DATE,
    last_seen_at        DATE,
    days_active         INTEGER DEFAULT 1,
    source_buckets_json TEXT,                           -- ['core_watchlist','sp500','theme_AI']
    discovery_score     REAL,                           -- 0-100 (stage 1 ranking)
    active_score        REAL,                           -- 0-100 (continued activity)
    active              INTEGER DEFAULT 1,
    inactive_reason     TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker, source)
);
CREATE INDEX IF NOT EXISTS idx_univ_date ON universe_membership(date);
CREATE INDEX IF NOT EXISTS idx_univ_ticker ON universe_membership(ticker);
CREATE INDEX IF NOT EXISTS idx_univ_active ON universe_membership(active, date);

-- ---------------------------------------------------------------------------
-- 3. prices_daily
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prices_daily (
    date        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    adj_close   REAL,
    volume      INTEGER,
    source      TEXT,                                   -- 'finnhub','yahoo','manual'
    reconciled  INTEGER DEFAULT 0,                      -- 1 if cross-source verified
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices_daily(ticker, date);

-- ---------------------------------------------------------------------------
-- 4. corporate_actions — splits, dividends, M&A
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS corporate_actions (
    ticker        TEXT NOT NULL,
    ex_date       TEXT NOT NULL,
    action_type   TEXT NOT NULL CHECK(action_type IN ('split','dividend','merger','spinoff','rights')),
    split_ratio   REAL,                                 -- e.g., 4.0 for 4:1 split
    dividend      REAL,                                 -- per share USD
    notes         TEXT,
    source        TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, ex_date, action_type)
);

-- ---------------------------------------------------------------------------
-- 5. ticker_changes — FB→META, etc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticker_changes (
    effective_date  TEXT NOT NULL,
    old_ticker      TEXT NOT NULL,
    new_ticker      TEXT NOT NULL,
    reason          TEXT,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (effective_date, old_ticker)
);

-- ---------------------------------------------------------------------------
-- 6. fundamentals_quarterly — operating companies primary model
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    ticker          TEXT NOT NULL,
    fiscal_period   TEXT NOT NULL,                      -- '2026Q1'
    report_date     TEXT,                               -- actual reporting date
    usable_at       TEXT,                               -- typically report_date + 1 day
    revenue         REAL,
    gross_profit    REAL,
    operating_income REAL,
    net_income      REAL,
    cfo             REAL,                               -- cash from ops
    capex           REAL,
    fcf             REAL,
    total_debt      REAL,
    cash            REAL,
    shares_out      REAL,
    sbc             REAL,                               -- stock-based comp
    -- Derived ratios
    gross_margin    REAL,
    operating_margin REAL,
    fcf_margin      REAL,
    roic            REAL,
    roe             REAL,
    nd_ebitda       REAL,                               -- net debt / EBITDA
    int_cov         REAL,                               -- interest coverage
    -- Quality scores (if computed)
    altman_z        REAL,
    piotroski_f     INTEGER,
    -- Source
    source          TEXT,
    raw_payload_hash TEXT,                              -- for contract drift detection
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_period)
);
CREATE INDEX IF NOT EXISTS idx_fund_q_usable_at ON fundamentals_quarterly(usable_at);

-- ---------------------------------------------------------------------------
-- 7. bank_fundamentals_q — banks use different metrics
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bank_fundamentals_q (
    ticker              TEXT NOT NULL,
    fiscal_period       TEXT NOT NULL,
    report_date         TEXT,
    usable_at           TEXT,
    net_interest_margin REAL,
    efficiency_ratio    REAL,
    tangible_book       REAL,
    capital_ratio       REAL,                           -- CET1 if available
    credit_quality      REAL,                           -- npa ratio or similar
    roa                 REAL,
    roe                 REAL,
    source              TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_period)
);

-- ---------------------------------------------------------------------------
-- 8. reit_fundamentals_q
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reit_fundamentals_q (
    ticker              TEXT NOT NULL,
    fiscal_period       TEXT NOT NULL,
    report_date         TEXT,
    usable_at           TEXT,
    ffo_per_share       REAL,
    affo_per_share      REAL,
    occupancy           REAL,
    debt_maturity_yrs   REAL,
    int_cov             REAL,
    dividend_safety     REAL,                           -- AFFO / dividend
    source              TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_period)
);

-- ---------------------------------------------------------------------------
-- 9. macro_daily — FRED + others
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_daily (
    date         TEXT NOT NULL,
    series_id    TEXT NOT NULL,                         -- 'DGS10','DGS2','BAMLH0A0HYM2','T10YIE', etc.
    value        REAL,
    release_date TEXT,                                  -- when FRED published
    usable_at    TEXT,                                  -- typically release_date
    source       TEXT DEFAULT 'fred',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, series_id)
);
CREATE INDEX IF NOT EXISTS idx_macro_series ON macro_daily(series_id, date);

-- ---------------------------------------------------------------------------
-- 10. sector_theme_daily — both sector ETFs and themes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sector_theme_daily (
    date              TEXT NOT NULL,
    name              TEXT NOT NULL,                    -- 'XLK','AI','Semiconductor'
    type              TEXT NOT NULL CHECK(type IN ('sector','theme','industry')),
    ret1m             REAL,
    ret3m             REAL,
    ret6m             REAL,
    ret_ytd           REAL,
    ret1y             REAL,
    trend_score       REAL,                             -- 0-100
    breadth_score     REAL,                             -- % above SMA50
    rel_strength      REAL,                             -- vs SPY
    leader_count      INTEGER,
    persistence       REAL,                             -- N-day persistence
    phase             TEXT CHECK(phase IN ('emerging','leading','extended','fading','breakdown','neutral')),
    leadership_score  REAL,                             -- composite 0-100
    raw_payload_hash  TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, name, type)
);

-- ---------------------------------------------------------------------------
-- 11. features_daily — engineered features per ticker per day
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features_daily (
    date              TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    -- Pillar scores 0-100 (or NULL for N/A)
    bq_score          REAL,                             -- Business Quality
    bq_coverage_ratio REAL,                             -- 0-100 metrics available
    val_score         REAL,                             -- Valuation
    growth_score      REAL,
    tech_score        REAL,                             -- Technical
    sec_theme_score   REAL,
    macro_score       REAL,
    risk_score        REAL,
    dq_score          REAL,                             -- Data Quality
    regime_fit_score  REAL,
    composite_score   REAL,                             -- final 0-100
    -- Tech detail
    state             TEXT,                             -- TIMING 9-state
    rsi_14            REAL,
    price_vs_sma50    REAL,
    price_vs_sma200   REAL,
    -- Meta
    model_type        TEXT,
    feature_version   TEXT NOT NULL,
    usable_at         TEXT NOT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_features_composite ON features_daily(date, composite_score);
CREATE INDEX IF NOT EXISTS idx_features_usable_at ON features_daily(usable_at);

-- ---------------------------------------------------------------------------
-- 12. market_regime_daily
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_regime_daily (
    date                    TEXT PRIMARY KEY,
    regime                  TEXT NOT NULL,              -- 8-state regime
    secondary               TEXT,
    confidence              REAL,
    evidence_json           TEXT,
    favored_themes_json     TEXT,                       -- ['AI','Semiconductor']
    avoided_themes_json     TEXT,
    favored_sectors_json    TEXT,
    avoided_sectors_json    TEXT,
    emerging_themes_json    TEXT,
    fading_themes_json      TEXT,
    effective_weights_json  TEXT,                       -- pillar weight overrides
    regime_version          TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 13. candidate_snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_snapshots (
    date                 TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    candidate_type       TEXT CHECK(candidate_type IN ('Core','Watchlist','Tactical','Reject')),
    score                REAL,
    source_buckets_json  TEXT,
    discovery_reason     TEXT,
    sector               TEXT,
    themes_json          TEXT,
    blockers_json        TEXT,
    reasons_json         TEXT,
    suggested_target_weight REAL,                       -- generic, not user-personal
    dq_score             REAL,
    bq_coverage_ratio    REAL,
    coverage_warning     TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_candidates_type_score ON candidate_snapshots(date, candidate_type, score);

-- ---------------------------------------------------------------------------
-- 14. generic_signals — signal_id is global UUID
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generic_signals (
    signal_id              TEXT PRIMARY KEY,             -- UUID
    date                   TEXT NOT NULL,
    ticker                 TEXT NOT NULL,
    source                 TEXT,                         -- 'timing','new_candidate','optimizer','watchlist'
    state                  TEXT,                         -- SETUP, TRIGGER, etc.
    action                 TEXT,                         -- '분할매수','보류','보유 유지', etc.
    composite_score        REAL,
    business_quality_score REAL,
    valuation_score        REAL,
    growth_score           REAL,
    tech_score             REAL,
    sec_theme_score        REAL,
    macro_score            REAL,
    risk_score             REAL,
    dq_score               REAL,
    regime_fit_score       REAL,
    market_regime          TEXT,
    confidence             REAL,
    target_weight_generic  REAL,                         -- generic suggestion, not personal
    suggested_buy_generic  REAL,                         -- generic dollar amount example
    blockers_json          TEXT,
    reasons_json           TEXT,
    next_trigger           TEXT,
    invalidation           TEXT,
    risk_status            TEXT,                         -- APPROVED, SIZE_REDUCED, BLOCKED, REVIEW_REQUIRED
    risk_flags_json        TEXT,
    lineage_json           TEXT,                         -- full reproducibility metadata
    model_id               TEXT,                         -- references model_registry
    feature_version        TEXT,
    code_commit_sha        TEXT,
    benchmark_at_signal_json TEXT,                       -- {spy, qqq} prices
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_date_ticker ON generic_signals(date, ticker);
CREATE INDEX IF NOT EXISTS idx_signals_source ON generic_signals(source, date);
CREATE INDEX IF NOT EXISTS idx_signals_regime ON generic_signals(market_regime, date);

-- ---------------------------------------------------------------------------
-- 15. signal_outcomes — performance per horizon
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id          TEXT NOT NULL,
    horizon            INTEGER NOT NULL,                 -- 1, 5, 20, 60, 120
    exit_date          TEXT,
    abs_ret            REAL,
    spy_ret            REAL,
    qqq_ret            REAL,
    sector_ret         REAL,
    theme_ret          REAL,
    rel_spy            REAL,
    rel_qqq            REAL,
    rel_sector         REAL,
    rel_theme          REAL,
    mae                REAL,                             -- max adverse excursion
    mfe                REAL,                             -- max favorable excursion
    mdd                REAL,                             -- max drawdown during horizon
    hit_invalidation   INTEGER DEFAULT 0,
    outcome_label      TEXT,                             -- HIT, MISS, FLAT, SKIP_DATA
    outcome_reason     TEXT,
    recorded_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id, horizon)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_label ON signal_outcomes(outcome_label, horizon);

-- ---------------------------------------------------------------------------
-- 16. backtest_runs — Python backtest engine results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id                     TEXT PRIMARY KEY,
    created_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strategy_name              TEXT NOT NULL,
    config_json                TEXT,
    universe_version           TEXT,
    feature_version            TEXT,
    start_date                 TEXT,
    end_date                   TEXT,
    -- Metrics
    cagr                       REAL,
    total_return               REAL,
    max_drawdown               REAL,
    volatility                 REAL,
    sharpe                     REAL,
    sharpe_ci_lower            REAL,
    sharpe_ci_upper            REAL,
    deflated_sharpe            REAL,
    sortino                    REAL,
    calmar                     REAL,
    hit_rate                   REAL,
    avg_win                    REAL,
    avg_loss                   REAL,
    profit_factor              REAL,
    expected_value             REAL,
    pbo                        REAL,                     -- probability of backtest overfitting
    whites_p_value             REAL,
    cv_method                  TEXT,
    n_oos_periods              INTEGER,
    -- Bias warnings
    lookahead_check_passed     INTEGER,
    survivorship_bias_risk     TEXT CHECK(survivorship_bias_risk IN ('none','low','medium','high','unknown')),
    pit_features_available     INTEGER,
    results_status             TEXT,                     -- 'reliable','marginal','reference_only','unavailable','overfit_risk'
    bias_warning_message       TEXT,
    -- Full results JSON
    result_json                TEXT
);

-- ---------------------------------------------------------------------------
-- 17. failed_jobs — retry queue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS failed_jobs (
    job_id        TEXT PRIMARY KEY,
    run_date      TEXT,
    job_name      TEXT,
    ticker        TEXT,
    error         TEXT,
    retry_count   INTEGER DEFAULT 0,
    last_error_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_failed_run_date ON failed_jobs(run_date, retry_count);

-- ---------------------------------------------------------------------------
-- 18. snapshot_metadata — per-run summary
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot_metadata (
    run_id                       TEXT PRIMARY KEY,
    market_date                  TEXT NOT NULL,         -- US market date (ET)
    executed_at_utc              TIMESTAMP,
    total_tickers_seen           INTEGER,
    stage1_scanned               INTEGER,
    stage2_scanned               INTEGER,
    stage2_target                INTEGER,
    api_calls_by_provider_json   TEXT,
    rate_limit_hits              INTEGER DEFAULT 0,
    skipped_due_to_budget        INTEGER DEFAULT 0,
    retry_count                  INTEGER DEFAULT 0,
    failed_count                 INTEGER DEFAULT 0,
    elapsed_seconds              REAL,
    consecutive_success_count    INTEGER DEFAULT 0,
    result_status                TEXT CHECK(result_status IN ('success','degraded','partial','failed','market_closed')),
    db_size_kb                   INTEGER,
    repo_size_kb                 INTEGER,
    archive_policy               TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshot_market_date ON snapshot_metadata(market_date DESC);

-- ---------------------------------------------------------------------------
-- 19. contract_violations — schema drift detection
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contract_violations (
    violation_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source                    TEXT,                     -- 'finnhub','fred','yahoo','sec','cnn'
    endpoint                  TEXT,
    ticker                    TEXT,
    expected_schema_version   TEXT,
    actual_payload_hash       TEXT,
    error_message             TEXT,
    severity                  TEXT CHECK(severity IN ('low','medium','high','critical'))
);

-- ---------------------------------------------------------------------------
-- 20. reconciliation_log — cross-source price/data verification
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT,
    ticker      TEXT,
    field       TEXT,                                   -- 'close_price','volume','market_cap'
    value_a     REAL,
    value_b     REAL,
    diff_pct    REAL,
    source_a    TEXT,
    source_b    TEXT,
    action      TEXT,                                   -- 'used_a','flagged','rejected'
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recon_date_ticker ON reconciliation_log(date, ticker);

-- ---------------------------------------------------------------------------
-- 21. metrics_timeseries — observability metrics
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_timeseries (
    ts            TIMESTAMP NOT NULL,
    metric_name   TEXT NOT NULL,
    value         REAL,
    tags_json     TEXT,
    PRIMARY KEY (ts, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics_timeseries(metric_name, ts DESC);

-- ---------------------------------------------------------------------------
-- 22. model_registry — model versioning + shadow mode
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    model_id                          TEXT PRIMARY KEY,
    family                            TEXT,
    version                           TEXT,
    status                            TEXT CHECK(status IN ('experimental','shadow','production','deprecated')),
    created_at                        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    promoted_at                       TIMESTAMP,
    retired_at                        TIMESTAMP,
    config_json                       TEXT,
    code_commit_sha                   TEXT,
    description                       TEXT,
    shadow_period_start               DATE,
    shadow_period_end                 DATE,
    shadow_signal_count               INTEGER,
    shadow_hit_rate                   REAL,
    shadow_ev                         REAL,
    shadow_vs_production_correlation  REAL,
    graduation_decision               TEXT
);

-- ---------------------------------------------------------------------------
-- 23. factor_exposures_weekly — Fama-French 5-factor
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS factor_exposures_weekly (
    ticker         TEXT NOT NULL,
    week_end_date  TEXT NOT NULL,
    alpha          REAL,
    b_mkt          REAL,
    b_smb          REAL,
    b_hml          REAL,
    b_rmw          REAL,
    b_cma          REAL,
    r_squared      REAL,
    n_obs          INTEGER,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, week_end_date)
);

-- ---------------------------------------------------------------------------
-- 24. raw_api_responses — Layer 0 storage (7-day retention)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_api_responses (
    fetch_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source                TEXT,
    endpoint              TEXT,
    ticker                TEXT,
    status_code           INTEGER,
    response_blob         TEXT,                         -- JSON string
    contract_violation_id INTEGER,
    expires_at            TIMESTAMP,
    FOREIGN KEY (contract_violation_id) REFERENCES contract_violations(violation_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_expires ON raw_api_responses(expires_at);

-- NOTE: schema_version is recorded by apply_migrations() in db.py
-- using the filename version (001) — no INSERT here to avoid double-counting.

COMMIT;
