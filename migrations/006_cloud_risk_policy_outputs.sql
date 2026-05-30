-- ============================================================================
-- Migration 006 (cloud): persist Risk Governor policy outputs (Phase 5.1)
-- ============================================================================
-- target_weight_generic remains the System Pure/pre-risk suggestion.
-- These columns expose the Risk Policy/post-governor result separately so views
-- and validation can compare pure signal vs governed signal without personal
-- portfolio data.
-- ============================================================================

BEGIN TRANSACTION;

ALTER TABLE generic_signals ADD COLUMN risk_adjusted_weight REAL;
ALTER TABLE generic_signals ADD COLUMN risk_size_multiplier REAL;
ALTER TABLE generic_signals ADD COLUMN risk_reason TEXT;
ALTER TABLE generic_signals ADD COLUMN risk_manual_checks_json TEXT;

ALTER TABLE candidate_snapshots ADD COLUMN risk_adjusted_weight REAL;
ALTER TABLE candidate_snapshots ADD COLUMN risk_size_multiplier REAL;
ALTER TABLE candidate_snapshots ADD COLUMN risk_reason TEXT;
ALTER TABLE candidate_snapshots ADD COLUMN risk_manual_checks_json TEXT;

COMMIT;
