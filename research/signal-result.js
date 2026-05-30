// research/signal-result.js — Standard Signal Result object + Recommendation Log
//
// SIGNAL_RESULT.build(fields)       → validated signal object
// REC_LOG.save(signal)              → persist to localStorage 'wr_recommendation_log'
// REC_LOG.updateOutcome(id, days, priceThen, priceNow, spyReturn, qqqReturn) → mark outcome
// REC_LOG.getStats()                → hit rate by DQ / state / composite band
// REC_LOG.forTicker(ticker)         → recent signals for this ticker
// REC_LOG.purge(maxAge_days)        → remove entries older than N days

const SIGNAL_RESULT = (() => {

  // Valid state keys (shared with TIMING module)
  const VALID_STATES = new Set([
    'TRIGGER','ADD','SETUP','BASE_BUILDING','EXTENDED',
    'WATCH','AVOID','REDUCE','EXIT','DATA_INSUFFICIENT'
  ]);

  // ── Build a validated Signal Result object ────────────────────────────────
  function build(fields) {
    const now = Date.now();
    const {
      ticker = '',
      asOf   = now,
      price  = null,
      state  = 'WATCH',
      action = '',
      confidenceLabel = '',
      // Scores
      compositeScore   = null,
      dataQualityScore = null,
      buyAllocationScore = null,
      // Sub-scores
      fundamentalScore = null,
      technicalScore   = null,      // legacy alias — use technicalTimingScore
      technicalTimingScore = null,  // 0-15 technical timing score
      riskScore        = null,
      macroScore       = null,
      sectorThemeScore = null,      // 0-20 sector/theme momentum score
      portfolioFitScore = null,
      // Sizing
      suggestedMonthlyBuy = null,
      allocationStatus = '',
      // Narrative
      reasons    = [],
      blockers   = [],
      warnings   = [],
      missingData = [],
      nextTrigger = '',
      invalidation = '',
      // Holder-specific guidance
      existingHolderAction = '',
      newBuyerAction = '',
      // Classification & context
      macroRegime    = '',          // 'RISK_ON' | 'NEUTRAL' | 'CAUTION' | 'RISK_OFF' (simple 4-value)
      sectorThemeState = '',        // 'leader' | 'improving' | 'neutral' | 'lagging' | 'breakdown'
      source = 'timing',            // 'timing' | 'new_candidate' | 'optimizer'
      candidateType = '',           // 'Core' | 'Watchlist' | 'Tactical' | ''
      benchmarkAtSignal = null,     // { spy: price, qqq: price }
      technicalSnapshot = null,     // { sma50, sma200, rsi, ema20 }
      // v2: Business quality + regime classification fields
      businessQualityScore = null,  // 0–100 fundamental quality score
      valuationScore       = null,  // 0–100 valuation attractiveness
      marketRegime         = '',    // 8-value: 'BROAD_RISK_ON' | 'NARROW_THEME_LEADERSHIP' | ...
      secondaryRegime      = '',    // second-highest scoring regime
      regimeConfidence     = null,  // 0–100 confidence in current regime
      effectiveWeights     = null,  // { businessQuality, valuation, macro, sectorTheme, technical }
      regimeFit            = null,  // 0–100 how well this ticker fits current regime
      // Metadata
      dataSources = [],
      backtestStats = null,
      signalHistoryStats = null,
    } = fields;

    // Backwards compat: if technicalTimingScore not provided but technicalScore is, use it
    const _technicalTimingScore = technicalTimingScore ?? technicalScore;

    if (!VALID_STATES.has(state)) console.warn(`SIGNAL_RESULT: unknown state '${state}'`);

    return {
      id: `${ticker}_${asOf}`,
      ticker: ticker.toUpperCase(),
      asOf,
      price,
      state,
      action,
      confidenceLabel,
      compositeScore,
      dataQualityScore,
      buyAllocationScore,
      fundamentalScore,
      technicalScore,                      // legacy alias preserved
      technicalTimingScore: _technicalTimingScore,
      riskScore,
      macroScore,
      sectorThemeScore,
      portfolioFitScore,
      suggestedMonthlyBuy,
      allocationStatus,
      reasons: Array.isArray(reasons) ? reasons : [reasons].filter(Boolean),
      blockers: Array.isArray(blockers) ? blockers : [blockers].filter(Boolean),
      warnings: Array.isArray(warnings) ? warnings : [warnings].filter(Boolean),
      missingData: Array.isArray(missingData) ? missingData : [missingData].filter(Boolean),
      nextTrigger,
      invalidation,
      existingHolderAction,
      newBuyerAction,
      macroRegime,
      sectorThemeState,
      source,
      candidateType,
      benchmarkAtSignal,
      technicalSnapshot,
      // v2 fields
      businessQualityScore,
      valuationScore,
      marketRegime,
      secondaryRegime,
      regimeConfidence,
      effectiveWeights,
      regimeFit,
      dataSources: Array.isArray(dataSources) ? dataSources : [],
      backtestStats,
      signalHistoryStats,
      // Outcome tracking (filled in later by REC_LOG.updateOutcome)
      outcomes: {},
    };
  }

  return { build };
})();


// ── Recommendation Log ────────────────────────────────────────────────────────
const REC_LOG = (() => {
  const KEY = 'wr_recommendation_log';
  const MAX_ENTRIES = 1000;  // cap to avoid localStorage bloat

  function _load() {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
    catch (_) { return []; }
  }
  function _save(entries) {
    try { localStorage.setItem(KEY, JSON.stringify(entries.slice(-MAX_ENTRIES))); }
    catch (_) { console.warn('REC_LOG: localStorage write failed'); }
  }

  // ── Save a new signal entry ───────────────────────────────────────────────
  // Deduplicates: if same ticker + same day already logged, updates it
  function save(signal) {
    if (!signal?.ticker || !signal?.asOf) return;
    const entries = _load();
    const dayKey = new Date(signal.asOf).toISOString().split('T')[0];
    const existing = entries.findIndex(e => e.ticker === signal.ticker && e.dayKey === dayKey);

    const entry = {
      id: signal.id || `${signal.ticker}_${signal.asOf}`,
      ticker: signal.ticker,
      asOf: signal.asOf,
      dayKey,
      price: signal.price,
      state: signal.state,
      action: signal.action,
      compositeScore: signal.compositeScore,
      dataQualityScore: signal.dataQualityScore,
      buyAllocationScore: signal.buyAllocationScore,
      technicalScore: signal.technicalScore,
      fundamentalScore: signal.fundamentalScore,
      macroScore: signal.macroScore,
      sectorThemeScore: signal.sectorThemeScore,
      technicalTimingScore: signal.technicalTimingScore ?? signal.technicalScore,
      macroRegime: signal.macroRegime,
      sectorThemeState: signal.sectorThemeState,
      source: signal.source || 'timing',
      candidateType: signal.candidateType || '',
      benchmarkAtSignal: signal.benchmarkAtSignal,
      technicalSnapshot: signal.technicalSnapshot,
      suggestedMonthlyBuy: signal.suggestedMonthlyBuy,
      // v2 fields
      businessQualityScore: signal.businessQualityScore ?? null,
      valuationScore:       signal.valuationScore       ?? null,
      marketRegime:         signal.marketRegime         || '',
      secondaryRegime:      signal.secondaryRegime      || '',
      regimeConfidence:     signal.regimeConfidence     ?? null,
      effectiveWeights:     signal.effectiveWeights     ?? null,
      regimeFit:            signal.regimeFit            ?? null,
      outcomes: signal.outcomes || {},
    };

    if (existing >= 0) entries[existing] = entry;
    else entries.push(entry);
    _save(entries);
    return entry;
  }

  // ── Update outcome for a logged entry ────────────────────────────────────
  // days: 5 | 20 | 60 | 120
  // spyReturn / qqqReturn: optional decimal returns for benchmark-relative calc
  // Returns updated entry or null if not found
  function updateOutcome(id, days, priceAtSignal, priceNow, spyReturn, qqqReturn) {
    if (!priceAtSignal || !priceNow) return null;
    const entries = _load();
    const idx = entries.findIndex(e => e.id === id);
    if (idx < 0) return null;
    const ret = (priceNow - priceAtSignal) / priceAtSignal;
    const state = entries[idx].state;
    // Outcome label: bullish states should have positive return to be "correct"
    const bullishStates = new Set(['TRIGGER','ADD','SETUP','BASE_BUILDING']);
    const bearishStates = new Set(['AVOID','REDUCE','EXIT']);
    let outcomeLabel = 'NEUTRAL';
    if (bullishStates.has(state))  outcomeLabel = ret >= 0.02 ? 'HIT' : ret <= -0.02 ? 'MISS' : 'FLAT';
    if (bearishStates.has(state))  outcomeLabel = ret <= -0.02 ? 'HIT' : ret >= 0.02 ? 'MISS' : 'FLAT';
    // Special state overrides
    if (state === 'EXTENDED') {
      outcomeLabel = priceNow < priceAtSignal * 1.05 ? 'HIT' : ret > 0.15 ? 'MISS' : 'NEUTRAL';
    }
    if (state === 'WATCH') {
      outcomeLabel = (spyReturn != null && ret < spyReturn - 0.02) ? 'HIT' : 'NEUTRAL';
    }
    if (state === 'DATA_INSUFFICIENT') {
      outcomeLabel = 'SKIP';
    }
    const outcome = { priceAtSignal, priceNow, ret: +ret.toFixed(4), label: outcomeLabel, recordedAt: Date.now() };
    // Benchmark-relative outcomes
    if (spyReturn != null) {
      outcome.relativeToSpy = +(ret - spyReturn).toFixed(4);
      outcome.relativeToQqq = +(ret - qqqReturn).toFixed(4);
    }
    entries[idx].outcomes[days] = outcome;
    _save(entries);
    return entries[idx];
  }

  // ── Trading-day accurate outcome from candles ────────────────────────────
  // Uses actual nth candle AFTER the signal date — no ms approximation.
  // candles: array of {t (unix timestamp), c} in ascending order
  // targetTradingDays: 5 | 20 | 60 | 120
  function updateOutcomeFromCandles(id, targetTradingDays, candles) {
    if (!candles || candles.length < 2) return null;
    const entries = _load();
    const idx = entries.findIndex(e => e.id === id);
    if (idx < 0) return null;
    if (entries[idx].outcomes?.[targetTradingDays]) return entries[idx]; // already done

    const signalTs = Math.floor(entries[idx].asOf / 1000);  // signal timestamp in seconds
    // Find the candle on or immediately after the signal date
    let signalIdx = candles.findIndex(c => c.t >= signalTs);
    if (signalIdx < 0) signalIdx = candles.length - 1;

    // Find nth candle after signalIdx (each candle = 1 trading day for daily candles)
    const targetIdx = signalIdx + targetTradingDays;
    if (targetIdx >= candles.length) return null; // not enough data yet

    const priceAtSignal = candles[signalIdx].c;
    const priceNow      = candles[targetIdx].c;

    if (!priceAtSignal || !priceNow) return null;
    const ret   = (priceNow - priceAtSignal) / priceAtSignal;
    const state = entries[idx].state;

    const bullishStates = new Set(['TRIGGER','ADD','SETUP','BASE_BUILDING']);
    const bearishStates = new Set(['AVOID','REDUCE','EXIT']);
    let outcomeLabel = 'NEUTRAL';
    if (bullishStates.has(state)) outcomeLabel = ret >= 0.02 ? 'HIT' : ret <= -0.02 ? 'MISS' : 'FLAT';
    if (bearishStates.has(state)) outcomeLabel = ret <= -0.02 ? 'HIT' : ret >= 0.02 ? 'MISS' : 'FLAT';
    if (state === 'EXTENDED')    outcomeLabel = priceNow < priceAtSignal * 1.05 ? 'HIT' : ret > 0.15 ? 'MISS' : 'NEUTRAL';
    if (state === 'WATCH')       outcomeLabel = 'NEUTRAL';
    if (state === 'DATA_INSUFFICIENT') outcomeLabel = 'SKIP';

    entries[idx].outcomes[targetTradingDays] = {
      priceAtSignal, priceNow,
      ret: +ret.toFixed(4),
      label: outcomeLabel,
      recordedAt: Date.now(),
      method: 'candles',  // mark as candle-based (not approximation)
      actualTradingDays: targetTradingDays,
    };
    _save(entries);
    return entries[idx];
  }

  // ── Scan all entries and auto-fill any due outcomes ───────────────────────
  // Requires current prices map: { AAPL: 195.3, ... }
  async function scanAndUpdateOutcomes(pricesMap) {
    const entries = _load();
    const TRADING_DAY_MS = 86400000 * (5 / 7);  // approx
    const targets = [5, 20, 60, 120];
    let updated = 0;
    entries.forEach(e => {
      const currentPrice = pricesMap?.[e.ticker];
      if (!currentPrice || !e.price) return;
      targets.forEach(d => {
        if (e.outcomes?.[d]) return;  // already recorded
        const msElapsed = Date.now() - e.asOf;
        const tradingDaysElapsed = msElapsed / TRADING_DAY_MS;
        if (tradingDaysElapsed >= d) {
          const ret = (currentPrice - e.price) / e.price;
          const state = e.state;
          const bullishStates = new Set(['TRIGGER','ADD','SETUP','BASE_BUILDING']);
          const bearishStates = new Set(['AVOID','REDUCE','EXIT']);
          let outcomeLabel = 'NEUTRAL';
          if (bullishStates.has(state))  outcomeLabel = ret >= 0.02 ? 'HIT' : ret <= -0.02 ? 'MISS' : 'FLAT';
          if (bearishStates.has(state))  outcomeLabel = ret <= -0.02 ? 'HIT' : ret >= 0.02 ? 'MISS' : 'FLAT';
          e.outcomes[d] = {
            priceAtSignal: e.price, priceNow: currentPrice,
            ret: +ret.toFixed(4), label: outcomeLabel, recordedAt: Date.now(),
          };
          updated++;
        }
      });
    });
    if (updated > 0) _save(entries);
    return updated;
  }

  // ── Get all entries for a single ticker ───────────────────────────────────
  function forTicker(ticker) {
    return _load().filter(e => e.ticker === ticker.toUpperCase())
      .sort((a, b) => b.asOf - a.asOf);
  }

  // ── Get global performance stats ──────────────────────────────────────────
  function getStats() {
    const entries = _load();
    const total   = entries.length;
    if (!total) return { total: 0, byDays: {}, byDQ: {}, byState: {}, byComposite: {},
      byFundamental: {}, byMacro: {}, bySectorTheme: {}, bySource: {}, byCandidateType: {} };

    const SKIP_LABELS = new Set(['DATA_INSUFFICIENT', 'SKIP']);

    const _hitRate = (arr) => {
      const filtered = arr.filter(o => !SKIP_LABELS.has(o.label));
      const hits  = filtered.filter(o => o.label === 'HIT').length;
      const total = filtered.filter(o => ['HIT','MISS','FLAT'].includes(o.label)).length;
      return total > 0 ? +(hits / total * 100).toFixed(1) : null;
    };

    // By time horizon (5/20/60/120 days)
    const byDays = {};
    [5, 20, 60, 120].forEach(d => {
      const outcomes = entries.map(e => e.outcomes?.[d]).filter(Boolean);
      byDays[d] = { n: outcomes.length, hitRate: _hitRate(outcomes) };
    });

    // By DQ band
    const dqBands = { 'HIGH(85+)': [], 'MEDIUM(70-84)': [], 'LOW(50-69)': [], 'VERY_LOW(<50)': [] };
    entries.forEach(e => {
      const dq = e.dataQualityScore ?? 0;
      const band = dq >= 85 ? 'HIGH(85+)' : dq >= 70 ? 'MEDIUM(70-84)' : dq >= 50 ? 'LOW(50-69)' : 'VERY_LOW(<50)';
      [5, 20].forEach(d => {
        if (e.outcomes?.[d]) dqBands[band].push(e.outcomes[d]);
      });
    });
    const byDQ = {};
    Object.entries(dqBands).forEach(([k, arr]) => { byDQ[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By state
    const byState = {};
    entries.forEach(e => {
      if (!byState[e.state]) byState[e.state] = [];
      [5, 20].forEach(d => { if (e.outcomes?.[d]) byState[e.state].push(e.outcomes[d]); });
    });
    const byStateStats = {};
    Object.entries(byState).forEach(([k, arr]) => { byStateStats[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By composite score band
    const compBands = { '80+(HIGH)': [], '60-79(MED)': [], '40-59(LOW)': [], '<40(POOR)': [] };
    entries.forEach(e => {
      const cs = e.compositeScore ?? 0;
      const band = cs >= 80 ? '80+(HIGH)' : cs >= 60 ? '60-79(MED)' : cs >= 40 ? '40-59(LOW)' : '<40(POOR)';
      [20].forEach(d => { if (e.outcomes?.[d]) compBands[band].push(e.outcomes[d]); });
    });
    const byComposite = {};
    Object.entries(compBands).forEach(([k, arr]) => { byComposite[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By fundamental score band (20D outcomes only)
    const fundBands = { '80+(HIGH)': [], '65-79(SOLID)': [], '50-64(FAIR)': [], '<50(WEAK)': [] };
    entries.forEach(e => {
      const fs = e.fundamentalScore ?? 0;
      const band = fs >= 80 ? '80+(HIGH)' : fs >= 65 ? '65-79(SOLID)' : fs >= 50 ? '50-64(FAIR)' : '<50(WEAK)';
      if (e.outcomes?.[20]) fundBands[band].push(e.outcomes[20]);
    });
    const byFundamental = {};
    Object.entries(fundBands).forEach(([k, arr]) => { byFundamental[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By macro regime (20D outcomes only)
    const macroBands = { 'RISK_ON': [], 'NEUTRAL': [], 'CAUTION': [], 'RISK_OFF': [] };
    entries.forEach(e => {
      const regime = e.macroRegime || 'NEUTRAL';
      if (macroBands[regime] && e.outcomes?.[20]) macroBands[regime].push(e.outcomes[20]);
    });
    const byMacro = {};
    Object.entries(macroBands).forEach(([k, arr]) => { byMacro[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By sector/theme state (20D outcomes only)
    const sectorBands = { 'leader': [], 'improving': [], 'neutral': [], 'lagging': [], 'breakdown': [] };
    entries.forEach(e => {
      const st = e.sectorThemeState || 'neutral';
      if (sectorBands[st] && e.outcomes?.[20]) sectorBands[st].push(e.outcomes[20]);
    });
    const bySectorTheme = {};
    Object.entries(sectorBands).forEach(([k, arr]) => { bySectorTheme[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By source (20D outcomes only)
    const sourceBands = { 'timing': [], 'new_candidate': [], 'optimizer': [] };
    entries.forEach(e => {
      const src = e.source || 'timing';
      if (sourceBands[src] && e.outcomes?.[20]) sourceBands[src].push(e.outcomes[20]);
    });
    const bySource = {};
    Object.entries(sourceBands).forEach(([k, arr]) => { bySource[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By candidate type (20D outcomes only)
    const ctBands = { 'Core': [], 'Watchlist': [], 'Tactical': [] };
    entries.forEach(e => {
      const ct = e.candidateType || '';
      if (ct && ctBands[ct] && e.outcomes?.[20]) ctBands[ct].push(e.outcomes[20]);
    });
    const byCandidateType = {};
    Object.entries(ctBands).forEach(([k, arr]) => { byCandidateType[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    // By 8-regime (20D outcomes)
    const regime8Bands = {
      BROAD_RISK_ON: [], NARROW_THEME_LEADERSHIP: [], ROTATION_MARKET: [],
      MACRO_RISK_OFF: [], DEFENSIVE_QUALITY_MARKET: [],
      RATE_PRESSURE_GROWTH_COMPRESSION: [], EARLY_RECOVERY: [], DATA_INSUFFICIENT: [],
    };
    entries.forEach(e => {
      const r8 = e.marketRegime || '';
      if (r8 && regime8Bands[r8] && e.outcomes?.[20]) regime8Bands[r8].push(e.outcomes[20]);
    });
    const byRegime8 = {};
    Object.entries(regime8Bands).forEach(([k, arr]) => { byRegime8[k] = { n: arr.length, hitRate: _hitRate(arr) }; });

    return { total, byDays, byDQ, byState: byStateStats, byComposite,
      byFundamental, byMacro, bySectorTheme, bySource, byCandidateType, byRegime8 };
  }

  // ── Remove entries older than maxAge days ─────────────────────────────────
  function purge(maxAge_days = 365) {
    const cutoff = Date.now() - maxAge_days * 86400000;
    const entries = _load().filter(e => e.asOf >= cutoff);
    _save(entries);
    return entries.length;
  }

  // ── Get recent entries (for display) ─────────────────────────────────────
  function recent(limit = 30) {
    return _load().sort((a, b) => b.asOf - a.asOf).slice(0, limit);
  }

  return { save, updateOutcome, updateOutcomeFromCandles, scanAndUpdateOutcomes, forTicker, getStats, purge, recent };
})();
