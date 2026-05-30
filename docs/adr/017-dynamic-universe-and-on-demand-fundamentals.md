# ADR 017: Dynamic Candidate Universe + On-Demand SEC Fundamentals

Status: Accepted (2026-05-30)

## Context

Two complaints about the research platform, both addressable by wiring data we
already produce but weren't surfacing:

1. **"신규편입 후보가 매번 똑같은 종목만 나온다."** The browser candidate scan ranked its
   universe with `_scanPriority()`, which used only **structural** terms (source
   count, theme-leader flag, Nasdaq membership, days_active). Nothing about the
   *current market* entered the ranking, so the scanned list never rotated — same
   names regardless of regime, theme leadership, or trend. Meanwhile the daily
   snapshot already computes a momentum/quality discovery score
   (`latest_candidates.json`) and a regime with favored/avoided/emerging themes
   and sectors (`latest_market_regime.json`) — the browser just ignored them.

2. **"펀더멘털이 56개밖에 안 된다 — 내가 치는 종목은 다 나왔으면."** The static
   `latest_fundamentals` view only covers the daily Stage-2 deep-scan set (~50
   names). Any other ticker fell back to Finnhub-only (limited on the free tier),
   with no SEC-computed ROIC/FCF/PER.

## Decision

### A. Dynamic universe layer (research/universe-builder.js + candidate.js)

`universe-builder.js` gained a best-effort **dynamic layer** (`_loadDynamicSignals`)
that loads `latest_market_regime` + `latest_candidates` views and annotates every
entry with:

- `dynScore` — the daily discovery score (momentum + quality), keyed by ticker.
- `themeFavored / emergingTheme / themeAvoided / fadingTheme` — by matching the
  entry's theme ids → `name_en` against the regime's theme-name sets.
- `sectorFavored / sectorAvoided` — by matching `sectorEtf` against the regime's
  favored/avoided sector ETFs.

`candidate.js` `_scanPriority()` adds market-driven terms on top of the structural
base, so the scanned set — and the diversified Top-N — **rotates daily with the tape**:

```
+ dynScore * 0.6        (daily momentum/quality; dominates the static ~40-pt base)
+ 18 emergingTheme      (catch new leadership early)
+ 14 themeFavored       − 16 themeAvoided/fading
+  8 sectorFavored      − 10 sectorAvoided
```

The candidate-tab header now shows today's regime + favored/emerging theme chips
(`UNIVERSE.getRegimeContext()`), so the rotation is **visible**. All of it is
best-effort: no views (Static site) → no dynamic terms, falls back to the prior
static ordering. Sector diversification in `_diversifiedTopN` is unchanged, so the
list stays sector-diverse while rotating.

### B. On-demand SEC fundamentals for ANY ticker (jobs/sec_fundamentals.py + server.py + fundamentals.js)

`jobs/sec_fundamentals.py.compute_for_ticker(ticker, price=None)` reuses —
**without modifying** — `Fetcher.fetch_sec_companyfacts` (SEC EDGAR companyfacts,
free, no key) and `financials.compute_operating_ratios`, then returns a dict
shaped exactly like a `latest_fundamentals` item (ROIC, FCF, margins, coverage,
and P-multiples when a price is supplied).

`server.py` exposes it at `GET /api/fundamentals?ticker=…[&price=…]` with a 24h
cache (SEC updates quarterly) and graceful `{available:false}` on a miss.

`fundamentals.js._staticFundamentals` now: (1) tries the static view, then
(2) **falls back to `/api/fundamentals`** for any ticker not in it. So in Local
Mode every typed US ticker gets SEC-computed fundamentals; Static-site behavior
(Finnhub-only) is unchanged when there's no server.

Verified end-to-end: `WMT` (outside the static set) → revenue $718B, FCF $12.5B,
ROIC 19.6%, coverage 100%, `source:sec`; `ZZZZ` → graceful `available:false`.

## Consequences

- **Pros**: the candidate list now reflects regime/theme/trend instead of a fixed
  56-name list; fundamentals work for *any* US filer for free; no API key needed
  for fundamentals at all (SEC is public); no edits to GPT's in-flight `fetch.py`
  (only imported).
- **Cons**: dynamic rotation and on-demand fundamentals need Local Mode + the
  daily views/server; the static GitHub Pages site keeps the prior static ordering
  and Finnhub-only fundamentals. On-demand SEC adds ~1 companyfacts fetch per new
  ticker (cached 24h). Foreign filers / ETFs have no US-GAAP companyfacts →
  honest `available:false`.
- **Not done**: after-tax P&L in the *research* portfolio tab (the wealth
  dashboard `js/app.js` + `tax-engine.js` already compute NRA/RA after-tax returns
  per lot; porting that view to the research platform is a follow-up).

## Reviewed
- Next review: when the daily snapshot's Stage-2 coverage is widened (more
  fundamentals baked into the static view) or after-tax is ported to research.
