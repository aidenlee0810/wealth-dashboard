-- ============================================================================
-- Migration 002: Local Personal DB Initial Schema
-- ============================================================================
-- Target DB: ~/.wealth-dashboard/personal.sqlite  (LOCAL ONLY, .gitignored)
--
-- This DB holds PERSONAL portfolio data. It MUST NEVER:
--   - Be committed to git
--   - Be uploaded to cloud (GitHub Actions, GitHub Pages)
--   - Be exposed via /api/db/query
--
-- Only accessible via /api/local/* endpoints (localhost only).
-- ============================================================================

BEGIN TRANSACTION;

-- Reuse schema_version pattern (same DDL as cloud, but isolated DB file)
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    db_role     TEXT NOT NULL CHECK(db_role IN ('cloud', 'local'))
);

-- ---------------------------------------------------------------------------
-- 1. portfolio_snapshots — daily snapshot of personal holdings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    date            TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    account         TEXT NOT NULL,                      -- 'IRA','Fidelity','Robinhood'
    shares          REAL,
    avg_cost        REAL,
    cost_basis      REAL,
    current_price   REAL,
    current_value   REAL,
    weight          REAL,
    sector          TEXT,
    themes_json     TEXT,
    risk_bucket     TEXT,                               -- 'core','growth','speculative','defensive'
    tax_lot_ref     TEXT,
    source          TEXT,                               -- 'tiller','manual','api'
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker, account)
);
CREATE INDEX IF NOT EXISTS idx_portsnap_date ON portfolio_snapshots(date);

-- ---------------------------------------------------------------------------
-- 2. user_target_weights — user-defined target allocation
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_target_weights (
    ticker          TEXT NOT NULL,
    account         TEXT NOT NULL,
    target_weight   REAL NOT NULL,
    target_amount   REAL,
    rationale       TEXT,
    last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes           TEXT,
    PRIMARY KEY (ticker, account)
);

-- ---------------------------------------------------------------------------
-- 3. user_actions — what user actually did with each signal
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_actions (
    action_id       TEXT PRIMARY KEY,                   -- UUID
    date            TEXT NOT NULL,
    signal_id       TEXT,                               -- references cloud generic_signals.signal_id
    ticker          TEXT,
    action_type     TEXT CHECK(action_type IN ('BUY','SELL','IGNORE','PARTIAL','REJECT','WATCHLIST_ONLY')),
    actual_price    REAL,
    actual_amount   REAL,                               -- dollar amount
    actual_shares   REAL,
    account         TEXT,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_actions_signal ON user_actions(signal_id);
CREATE INDEX IF NOT EXISTS idx_user_actions_ticker_date ON user_actions(ticker, date DESC);

-- ---------------------------------------------------------------------------
-- 4. account_buckets — monthly budget per account
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_buckets (
    account_name      TEXT PRIMARY KEY,
    monthly_budget    REAL,
    tax_status        TEXT,                             -- 'ira','roth','taxable'
    restrictions_json TEXT,                             -- e.g., {"max_speculative": 0.2}
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 5. tax_lots — for tax-loss harvesting + LT/ST decisions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tax_lots (
    lot_id            TEXT PRIMARY KEY,                 -- UUID
    ticker            TEXT NOT NULL,
    account           TEXT NOT NULL,
    qty               REAL,
    cost_basis_per_share REAL,
    total_cost        REAL,
    acquired_date     TEXT,
    lt_eligible_date  TEXT,                             -- 12mo after acquired
    sold_date         TEXT,
    sold_price        REAL,
    realized_gain     REAL,
    status            TEXT CHECK(status IN ('open','closed','partial')),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_taxlots_ticker_status ON tax_lots(ticker, status);

-- ---------------------------------------------------------------------------
-- 6. personal_notes — free-form annotations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS personal_notes (
    note_id     TEXT PRIMARY KEY,
    ticker      TEXT,                                   -- NULL = portfolio-level note
    date        TEXT,
    text        TEXT NOT NULL,
    tags_json   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notes_ticker ON personal_notes(ticker, date DESC);

-- ---------------------------------------------------------------------------
-- 7. allocation_outputs — 3-ledger sizing results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS allocation_outputs (
    date                       TEXT NOT NULL,
    ticker                     TEXT NOT NULL,
    signal_id                  TEXT,                    -- references cloud generic_signals
    ledger                     TEXT NOT NULL CHECK(ledger IN ('pure','policy','actual')),
    suggested_size_before_risk REAL,
    suggested_size_after_risk  REAL,
    actual_size                REAL,
    account                    TEXT,
    risk_status                TEXT,
    risk_flags_json            TEXT,
    sizing_method              TEXT,                    -- 'equal','inv_vol','risk_parity','fractional_kelly'
    notes                      TEXT,
    created_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, ticker, ledger)
);
CREATE INDEX IF NOT EXISTS idx_alloc_signal ON allocation_outputs(signal_id, ledger);

-- NOTE: schema_version is recorded by apply_migrations() — see 001 comment.

COMMIT;
