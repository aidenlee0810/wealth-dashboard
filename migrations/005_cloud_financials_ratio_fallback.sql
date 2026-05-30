-- ============================================================================
-- Migration 005 (cloud): provider ratio fallback for Phase 4.5 fundamentals
-- ============================================================================
-- Finnhub /stock/metric can return useful ratios even when full line items are
-- unavailable. Store that sparse ratio bundle at Layer 1 so Layer 2 can fill
-- missing metrics without pretending it has SEC-quality source line items.
-- ============================================================================

BEGIN TRANSACTION;

ALTER TABLE cleaned_financials ADD COLUMN provider_ratios_json TEXT;
ALTER TABLE normalized_financials ADD COLUMN ratio_source TEXT;

COMMIT;
