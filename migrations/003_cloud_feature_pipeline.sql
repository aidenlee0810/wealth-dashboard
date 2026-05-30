-- ============================================================================
-- Migration 003 (cloud): 4-Layer Feature Pipeline — Layer 1 & Layer 2 (Plan §24)
-- ============================================================================
-- Target DB: data/db/research_market.sqlite  (cloud, committed to git)
--
-- The feature pipeline has 4 layers:
--   Layer 0  raw_api_responses        (already created in migration 001)
--   Layer 1  cleaned_financials       (THIS migration) — typed, NULL-handled, USD-normalized
--   Layer 2  normalized_financials    (THIS migration) — sector-adjusted, cross-comparable
--   Layer 3  features_daily           (already created in migration 001) — final scores
--
-- Why separate layers? Debuggability + replay. When NVDA's op_margin_score is 78,
-- we can trace Layer 3 → Layer 2 → Layer 1 → Layer 0 without re-calling the API.
-- Each layer is independently testable and has its own retention policy.
-- ============================================================================

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- Layer 1: cleaned_financials — raw API blobs parsed into typed, USD-normalized values
-- Retention: 90 days (debugging window). NULLs explicitly allowed (missing data).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cleaned_financials (
    ticker          TEXT NOT NULL,
    fiscal_period   TEXT NOT NULL,                  -- '2026-Q1'
    report_date     TEXT,                           -- when company reported
    usable_at       TEXT,                           -- point-in-time guard (>= report_date)
    -- Income statement (USD, integer-ish REAL)
    revenue         REAL,
    cost_of_revenue REAL,
    gross_profit    REAL,
    operating_income REAL,
    net_income      REAL,
    -- Cash flow
    cfo             REAL,                            -- cash from operations
    capex           REAL,
    fcf             REAL,                            -- cfo - capex
    sbc             REAL,                            -- stock-based comp
    -- Balance sheet
    total_debt      REAL,
    cash_and_sti    REAL,                            -- cash + short-term investments
    total_equity    REAL,
    shares_out      REAL,                            -- diluted shares outstanding
    -- Provenance
    source          TEXT,                            -- 'finnhub','fmp','sec'
    raw_fetch_id    INTEGER,                         -- FK to raw_api_responses
    cleaned_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    schema_version  TEXT,                            -- contract schema version used
    PRIMARY KEY (ticker, fiscal_period, source),
    FOREIGN KEY (raw_fetch_id) REFERENCES raw_api_responses(fetch_id)
);
CREATE INDEX IF NOT EXISTS idx_cleaned_fin_ticker ON cleaned_financials(ticker, usable_at);
CREATE INDEX IF NOT EXISTS idx_cleaned_fin_usable ON cleaned_financials(usable_at);

-- ---------------------------------------------------------------------------
-- Layer 2: normalized_financials — ratios + sector-relative z-scores
-- Cross-comparable across tickers. Retention: 1 year.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized_financials (
    ticker              TEXT NOT NULL,
    fiscal_period       TEXT NOT NULL,
    usable_at           TEXT,
    sector              TEXT,                        -- GICS sector for peer grouping
    model_type          TEXT,                        -- operating_company/bank/reit/...
    -- Absolute ratios (0-1 or x)
    gross_margin        REAL,
    operating_margin    REAL,
    fcf_margin          REAL,
    net_margin          REAL,
    roic                REAL,
    roe                 REAL,
    nd_ebitda           REAL,                        -- net debt / EBITDA
    int_cov             REAL,                        -- interest coverage
    revenue_growth_yoy  REAL,
    sbc_pct_revenue     REAL,
    -- Sector-relative (peer percentile + z-score)
    sector_op_margin_p50    REAL,
    op_margin_zscore        REAL,
    sector_fcf_margin_p50   REAL,
    fcf_margin_zscore       REAL,
    roic_zscore             REAL,
    revenue_growth_zscore   REAL,
    -- Coverage / quality
    coverage_ratio      REAL,                        -- % of required metrics present (0-100)
    -- Provenance
    normalized_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    feature_version     TEXT,
    PRIMARY KEY (ticker, fiscal_period)
);
CREATE INDEX IF NOT EXISTS idx_norm_fin_ticker ON normalized_financials(ticker, usable_at);
CREATE INDEX IF NOT EXISTS idx_norm_fin_sector ON normalized_financials(sector, fiscal_period);

-- ---------------------------------------------------------------------------
-- universe_membership already exists (migration 001). This migration adds a
-- convenience view-like index for the days_active persistence queries used by
-- the universe builder (Phase 2).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_universe_active_seen
    ON universe_membership(active, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_universe_ticker_seen
    ON universe_membership(ticker, last_seen_at DESC);

COMMIT;
