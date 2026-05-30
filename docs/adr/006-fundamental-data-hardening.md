# ADR 006: Phase 4.5 Fundamental Data Hardening

Date: 2026-05-29

## Status

Accepted

## Context

Phase 4 introduced model-type scoring, but the live-data path was weaker than
the synthetic path. Synthetic fundamentals contained full line items, while
Finnhub `/stock/metric` returns a sparse ratio bundle. Without a ratio fallback
or an official line-item source, live runs could pass tests yet produce empty or
low-coverage business-quality, valuation, and growth pillars.

## Decision

Use this source priority for free fundamentals:

1. SEC EDGAR CompanyFacts for US operating companies, insurers, and pre-revenue
   biotech names. It is official, free, no-key, and line-item based.
2. Finnhub `/stock/metric` as a ratio fallback when SEC mapping fails or line
   items are unavailable.
3. Keep synthetic fundamentals only for deterministic offline tests.

SEC CompanyFacts now maps common US-GAAP concepts into the canonical Layer 1
record: revenue, gross profit, operating income, net income, CFO, capex, SBC,
cash and short-term investments, debt, equity, diluted shares, interest expense,
and estimated EBITDA. For flow metrics it uses TTM when a newer 10-Q is
available (`latest 10-K + current YTD/quarter - prior-year comparable
YTD/quarter`), otherwise it falls back to the latest 10-K. Balance-sheet metrics
use the latest filing end date available at the decision date.

Finnhub ratios are stored as `cleaned_financials.provider_ratios_json`. Layer 2
uses those ratios only to fill missing derived metrics, and valuation can score
from provider P/E, P/S, P/FCF, and P/B when full SEC line items are absent.

## Consequences

- Live fundamentals no longer depend solely on synthetic line items or paid
  provider coverage.
- SEC-derived facts have better auditability, but are still limited by XBRL tag
  availability and company-specific reporting choices.
- Bank and REIT specialist metrics remain partially covered. True bank capital
  quality and REIT FFO/AFFO still need specialized sources or deeper filing
  parsing.
- SEC fair-access rules require a declared User-Agent. Production runs should
  set `SEC_USER_AGENT`.

## Validation

- SEC CompanyFacts fixture maps to canonical line items.
- SEC CompanyFacts fixture prefers TTM when a newer 10-Q is available.
- Finnhub ratio fixture round-trips through Layer 1, Layer 2, `get_fundamentals`,
  and the fundamental analyst.
- Live smoke: AAPL/MSFT/NVDA/GOOGL/AMZN/META/TSLA CompanyFacts fetched
  successfully without an API key and mapped current TTM line items when 10-Qs
  were available.
- Full regression: `107 passed`.
