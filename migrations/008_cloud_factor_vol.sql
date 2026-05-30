-- migrations/008_factor_vol.sql — add vol_annual to factor_exposures_weekly
-- Phase 9: stress test needs annualised volatility from the OLS regression.

ALTER TABLE factor_exposures_weekly ADD COLUMN vol_annual REAL;
