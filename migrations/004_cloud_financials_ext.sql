-- ============================================================================
-- Migration 004 (cloud): financials line-item extensions (Plan §6, §24, Phase 4)
-- ============================================================================
-- Target DB: data/db/research_market.sqlite (cloud)
--
-- Phase 4's model-type-specific scoring needs a few line items that the
-- original cleaned_financials (migration 003) didn't carry:
--   * interest_expense, ebitda  → interest coverage + net-debt/EBITDA (operating)
--   * revenue_prior             → YoY revenue growth without a second row lookup
--   * quarterly_burn, shares_out_prior → biotech cash runway + dilution (§6)
--
-- SQLite ALTER TABLE ADD COLUMN is safe + idempotent enough here because
-- apply_migrations() only runs versions newer than schema_version. Adding a
-- column that already exists would error, so this migration must run exactly
-- once (the migration runner guarantees that).
-- ============================================================================

BEGIN TRANSACTION;

ALTER TABLE cleaned_financials ADD COLUMN revenue_prior     REAL;
ALTER TABLE cleaned_financials ADD COLUMN interest_expense  REAL;
ALTER TABLE cleaned_financials ADD COLUMN ebitda            REAL;
ALTER TABLE cleaned_financials ADD COLUMN quarterly_burn    REAL;   -- biotech
ALTER TABLE cleaned_financials ADD COLUMN shares_out_prior  REAL;   -- biotech dilution

COMMIT;
