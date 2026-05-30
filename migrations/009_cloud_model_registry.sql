-- Migration 009: model_registry table (Plan §31, Phase 10-B)
-- Tracks every scoring model version through experimental → shadow → production.
-- All writes go through jobs/model_registry.py; /api/db/query exposes read-only view.

CREATE TABLE IF NOT EXISTS model_registry (
    model_id               TEXT PRIMARY KEY,   -- e.g. 'candidate_scorer_v4.5'
    family                 TEXT NOT NULL,       -- 'candidate_scorer' | 'regime_classifier' | 'risk_governor' | 'factor_model'
    version                TEXT NOT NULL,       -- semver string e.g. '4.5'
    status                 TEXT NOT NULL        -- 'experimental' | 'shadow' | 'production' | 'deprecated'
                           DEFAULT 'experimental'
                           CHECK (status IN ('experimental','shadow','production','deprecated')),
    created_at             TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    promoted_at            TIMESTAMP,           -- NULL until shadow → production
    retired_at             TIMESTAMP,           -- NULL until deprecated
    code_commit_sha        TEXT,                -- git SHA when model was registered
    config_json            TEXT,               -- JSON blob: weights, thresholds, params
    description            TEXT,
    -- Shadow-mode evaluation stats (populated after ≥30d in shadow)
    shadow_period_start    TEXT,               -- ISO date
    shadow_period_end      TEXT,
    shadow_signal_count    INTEGER,
    shadow_hit_rate        REAL,
    shadow_ev              REAL,               -- expected-value vs SPY
    shadow_vs_production_correlation REAL,    -- 0-1 output similarity score
    graduation_decision    TEXT                -- 'promoted' | 'rejected' | 'pending'
);

-- Seed the currently-live candidate scorer so the registry is non-empty from day 1.
-- config_json reflects Phase 4 synthesis weights (scorers/synthesis.py).
INSERT OR IGNORE INTO model_registry
    (model_id, family, version, status, description, config_json)
VALUES (
    'candidate_scorer_v4.5',
    'candidate_scorer',
    '4.5',
    'production',
    'Phase-4 multi-pillar scorer: BQ(25%) + Val(10%) + Growth(15%) + Tech(20%) + Theme(10%) + Macro(10%) + Risk(10%). Model-type-aware (operating/bank/reit/biotech/etf).',
    '{"weights":{"bq":0.25,"val":0.10,"growth":0.15,"tech":0.20,"sec_theme":0.10,"macro":0.10,"risk":0.10},"r2_low_threshold":0.30,"dq_floor":50,"regime_boost":5.0}'
);

-- Seed the regime classifier
INSERT OR IGNORE INTO model_registry
    (model_id, family, version, status, description, config_json)
VALUES (
    'regime_classifier_v1.3',
    'regime_classifier',
    '1.3',
    'production',
    '8-state market regime classifier. States: BROAD_RISK_ON, NARROW_THEME_LEADERSHIP, ROTATION_MARKET, TECH_LEADERSHIP, DEFENSIVE, MACRO_RISK_OFF, VOLATILE_CHOP, RATE_SENSITIVE. Whipsaw prevention: requires 2-day persistence before state change.',
    '{"states":8,"persistence_days":2,"confidence_threshold":0.55}'
);

-- Seed the risk governor
INSERT OR IGNORE INTO model_registry
    (model_id, family, version, status, description, config_json)
VALUES (
    'risk_governor_v1.0',
    'risk_governor',
    '1.0',
    'production',
    '15-gate risk governor (Phase 5). Outputs APPROVED | SIZE_REDUCED | BLOCKED | REVIEW_REQUIRED.',
    '{"gates":15,"high_beta_cap":0.20,"sector_concentration_cap":0.35,"theme_concentration_cap":0.30,"single_position_cap":0.08,"leverage_cap":1.3}'
);

-- Seed the factor model
INSERT OR IGNORE INTO model_registry
    (model_id, family, version, status, description, config_json)
VALUES (
    'factor_model_v1.0',
    'factor_model',
    '1.0',
    'production',
    'Single-factor market beta (OLS vs SPY, 2-year lookback). SMB/HML via characteristic proxies. RMW/CMA NULL pending data accumulation. R²<0.30 triggers low_r2 warning.',
    '{"lookback_days":504,"min_obs":60,"r2_low_threshold":0.30,"smb_via":"mcap_bucket","hml_via":"sector_lookup"}'
);
