-- ============================================================================
-- Migration 007 (cloud): per-provider price-fetch status (Real Price Provider Layer)
-- ============================================================================
-- Records WHY each price provider (alpaca/tiingo/yahoo/...) failed on a run, so
-- latest_snapshot_health can show e.g. "yahoo: HTTP 429" / "alpaca: no key".
-- Stored as JSON: {provider: {count, reasons:{reason:count}}}.
-- ============================================================================

BEGIN TRANSACTION;

ALTER TABLE snapshot_metadata ADD COLUMN price_provider_status_json TEXT;

COMMIT;
