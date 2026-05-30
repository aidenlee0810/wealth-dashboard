// research/universe-builder.js
// ============================================================================
// Dynamic Universe Builder (browser) — Plan §4
// ============================================================================
// Merges the static universe layers (data/*.json) into one candidate universe,
// mirroring jobs/universe_builder.py. Works in Local AND Static mode (it only
// needs the static JSON files, which are committed to the repo / served by Pages).
//
// Layers:
//   A. core_watchlist     data/core_watchlist.json   (+ user localStorage adds)
//   B. sp500 / nasdaq100  data/sp500.json, nasdaq100.json
//   C. sector_holdings    data/sector_holdings.json
//   D. theme_baskets      data/theme_baskets.json
//
// days_active / persistence comes from the DB (universe_membership) when in
// Local mode; in Static mode it falls back to the static universe summary view.
//
// Public API:
//   UNIVERSE.build()                 -> Promise<{tickers, bySource, count}>
//   UNIVERSE.getAll()                -> array of entries (after build)
//   UNIVERSE.getAllTickers()         -> array of ticker strings (CAND_UNIVERSE-compatible)
//   UNIVERSE.get(ticker)             -> entry or null
//   UNIVERSE.getSourceBreakdown()    -> {sp500: n, nasdaq100: n, ...}
//   UNIVERSE.getBySource(src)        -> entries in that source
//   UNIVERSE.getCoreEligible()       -> entries with days_active >= 5 (Local) or core source
//   UNIVERSE.addToCore(ticker, meta) -> persist user addition (localStorage)
//   UNIVERSE.removeFromCore(ticker)  -> remove user addition
//   UNIVERSE.userCoreAdditions()     -> {ticker: meta}
// ============================================================================

const UNIVERSE = (() => {
  const DATA_DIR = 'data';
  const USER_CORE_KEY = 'wr_core_watchlist_additions';
  const CORE_MIN_DAYS_ACTIVE = 5;

  const SECTOR_ETF_TO_GICS = {
    XLK: 'Information Technology', XLV: 'Health Care', XLF: 'Financials',
    XLY: 'Consumer Discretionary', XLC: 'Communication Services',
    XLI: 'Industrials', XLP: 'Consumer Staples', XLE: 'Energy',
    XLU: 'Utilities', XLRE: 'Real Estate', XLB: 'Materials',
  };
  const ETF_TICKERS = new Set(['VUG', 'SCHG', 'SPY', 'QQQ', 'QQQM', 'IWM', 'DIA',
    'XLK', 'XLV', 'XLF', 'XLY', 'XLC', 'XLI', 'XLP', 'XLE', 'XLU', 'XLRE', 'XLB']);

  const SOURCE_PRIORITY = ['core_watchlist', 'theme_basket', 'sector_holdings', 'nasdaq100', 'sp500'];

  const STATE = {
    built: false,
    entries: new Map(),       // ticker -> entry
    bySource: {},
    daysActive: {},           // ticker -> days_active (from DB if Local)
    hasDaysActive: false,     // true if we loaded ANY persistence data (Local mode)
    builtAt: null,
    regimeCtx: null,          // {regime, favored/avoided/emerging themes+sectors} (daily)
    dynScores: {},            // ticker -> daily discovery score (latest_candidates)
    themeNameById: {},        // theme id -> English name (to match regime theme names)
  };

  // -------------------------------------------------------------------------
  async function _fetchJson(name) {
    try {
      const resp = await fetch(`${DATA_DIR}/${name}`, { cache: 'no-store' });
      if (resp.ok) return await resp.json();
    } catch (e) { /* ignore */ }
    return null;
  }

  function _entry(ticker) {
    let e = STATE.entries.get(ticker);
    if (!e) {
      e = {
        ticker, name: null, sources: new Set(), sourceBuckets: [],
        sectorEtf: null, gicsSector: null, modelType: null,
        themes: [], themeLeader: false, isSP500: false, isNasdaq100: false,
        highBeta: false, qualityTier: null, discoveryScore: 0, daysActive: null,
      };
      STATE.entries.set(ticker, e);
    }
    return e;
  }
  function _addBucket(e, b) { if (!e.sourceBuckets.includes(b)) e.sourceBuckets.push(b); }

  // -------------------------------------------------------------------------
  // Persistence: pull days_active from DB (Local) or static view (Static)
  // -------------------------------------------------------------------------
  async function _loadDaysActive() {
    STATE.daysActive = {};
    STATE.hasDaysActive = false;
    try {
      if (typeof DB !== 'undefined' && DB.isLocal) {
        // ORDER BY days_active DESC: anyone with days_active >= 5 is guaranteed
        // to be within the top 500, so the limit never hides an eligible ticker.
        const res = await DB.query('universe_membership', {
          cols: ['ticker', 'days_active', 'active'],
          where: { active: 1 },
          order_by: 'days_active DESC',
          limit: 500,
        });
        (res.rows || []).forEach(r => { STATE.daysActive[r.ticker] = r.days_active; });
        STATE.hasDaysActive = (res.rows || []).length > 0;
      } else if (typeof DB !== 'undefined') {
        const view = await DB.view('latest_universe_summary');
        if (view && view.days_active_by_ticker) {
          STATE.daysActive = view.days_active_by_ticker;
          STATE.hasDaysActive = true;
        }
      }
    } catch (e) { /* days_active stays empty — degrade gracefully */ }
  }

  // -------------------------------------------------------------------------
  // Dynamic layer (E/F): regime-favored themes/sectors + daily discovery score.
  // This is what makes the candidate list ROTATE with the market instead of
  // showing the same static names every day. Best-effort: no views → no effect.
  // -------------------------------------------------------------------------
  async function _loadDynamicSignals(th) {
    STATE.regimeCtx = null;
    STATE.dynScores = {};
    STATE.themeNameById = {};
    const themes = (th && th.themes) ? th.themes : (th || {});
    Object.entries(themes).forEach(([id, m]) => {
      if (m && m.name_en) STATE.themeNameById[id] = m.name_en;
    });
    if (typeof DB === 'undefined' || typeof DB.view !== 'function') return;
    try {
      const [regime, cands] = await Promise.all([
        DB.view('latest_market_regime'),
        DB.view('latest_candidates'),
      ]);
      if (regime) {
        STATE.regimeCtx = {
          regime: regime.regime || null,
          secondary: regime.secondary || null,
          confidence: regime.confidence ?? null,
          favoredThemes: new Set(regime.favored_themes || []),
          avoidedThemes: new Set(regime.avoided_themes || []),
          emergingThemes: new Set(regime.emerging_themes || []),
          fadingThemes: new Set(regime.fading_themes || []),
          favoredSectors: new Set(regime.favored_sectors || []),
          avoidedSectors: new Set(regime.avoided_sectors || []),
          asOf: regime.market_date || regime.date || null,
        };
      }
      const list = (cands && Array.isArray(cands.candidates)) ? cands.candidates : [];
      list.forEach(c => {
        if (c.ticker && c.score != null) STATE.dynScores[String(c.ticker).toUpperCase()] = c.score;
      });
    } catch (e) { /* dynamic signals are best-effort */ }
  }

  function _annotateDynamic(e) {
    e.dynScore = STATE.dynScores[e.ticker] ?? null;
    e.themeFavored = e.emergingTheme = e.themeAvoided = e.fadingTheme = false;
    e.sectorFavored = e.sectorAvoided = false;
    const ctx = STATE.regimeCtx;
    if (!ctx) return;
    const names = (e.themes || []).map(id => STATE.themeNameById[id]).filter(Boolean);
    e.themeFavored  = names.some(n => ctx.favoredThemes.has(n));
    e.emergingTheme = names.some(n => ctx.emergingThemes.has(n));
    e.themeAvoided  = names.some(n => ctx.avoidedThemes.has(n));
    e.fadingTheme   = names.some(n => ctx.fadingThemes.has(n));
    if (e.sectorEtf) {
      e.sectorFavored = ctx.favoredSectors.has(e.sectorEtf);
      e.sectorAvoided = ctx.avoidedSectors.has(e.sectorEtf);
    }
  }

  // -------------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------------
  async function build(force = false) {
    if (STATE.built && !force) return _summary();
    STATE.entries = new Map();

    const [sp, nq, sec, th, core] = await Promise.all([
      _fetchJson('sp500.json'),
      _fetchJson('nasdaq100.json'),
      _fetchJson('sector_holdings.json'),
      _fetchJson('theme_baskets.json'),
      _fetchJson('core_watchlist.json'),
    ]);

    // Layer B: S&P 500
    if (sp?.constituents) sp.constituents.forEach(c => {
      const e = _entry(c.ticker);
      e.sources.add('sp500'); _addBucket(e, 'sp500'); e.isSP500 = true;
      e.name = e.name || c.name;
      e.gicsSector = e.gicsSector || c.sector;
      e.sectorEtf = e.sectorEtf || c.sector_etf;
    });
    // Layer B: Nasdaq-100
    if (nq?.constituents) nq.constituents.forEach(c => {
      const e = _entry(c.ticker);
      e.sources.add('nasdaq100'); _addBucket(e, 'nasdaq100'); e.isNasdaq100 = true;
      e.name = e.name || c.name;
      if (c.sector) e.gicsSector = e.gicsSector || c.sector;
      e.sectorEtf = e.sectorEtf || c.sector_etf;
    });
    // Layer C: Sector holdings
    if (sec?.sectors) Object.entries(sec.sectors).forEach(([etf, meta]) => {
      (meta.holdings || []).forEach(t => {
        const e = _entry(t);
        e.sources.add('sector_holdings'); _addBucket(e, `sector:${etf}`);
        e.sectorEtf = e.sectorEtf || etf;
        e.gicsSector = e.gicsSector || meta.name_en;
        if (e.modelType == null) e.modelType = meta.model_type;
      });
    });
    // Layer D: Theme baskets
    if (th?.themes) Object.entries(th.themes).forEach(([tid, meta]) => {
      const leaders = new Set(meta.leaders || []);
      (meta.tickers || []).forEach(t => {
        const e = _entry(t);
        e.sources.add('theme_basket'); _addBucket(e, `theme:${tid}`);
        if (!e.themes.includes(tid)) e.themes.push(tid);
        if (leaders.has(t)) e.themeLeader = true;
        if (!e.sectorEtf && meta.sector_etfs?.length) e.sectorEtf = meta.sector_etfs[0];
      });
    });
    // Layer A: Core watchlist (file)
    if (core?.tickers) Object.entries(core.tickers).forEach(([t, meta]) => {
      _applyCore(t, meta);
    });
    // Layer A: Core watchlist (user localStorage additions)
    const userAdds = userCoreAdditions();
    Object.entries(userAdds).forEach(([t, meta]) => _applyCore(t, meta, true));

    // Derived fields + discovery score
    await _loadDaysActive();
    await _loadDynamicSignals(th);          // regime + daily discovery (makes list rotate)
    STATE.entries.forEach(e => {
      if (!e.gicsSector && e.sectorEtf) e.gicsSector = SECTOR_ETF_TO_GICS[e.sectorEtf];
      if (e.modelType == null) e.modelType = ETF_TICKERS.has(e.ticker) ? 'etf' : 'operating_company';
      e.daysActive = STATE.daysActive[e.ticker] ?? null;
      _annotateDynamic(e);                  // dynScore + theme/sector regime flags
      e.discoveryScore = _discoveryScore(e);
    });

    // Source breakdown
    STATE.bySource = {};
    STATE.entries.forEach(e => e.sources.forEach(s => {
      STATE.bySource[s] = (STATE.bySource[s] || 0) + 1;
    }));

    STATE.built = true;
    STATE.builtAt = Date.now();
    return _summary();
  }

  function _applyCore(t, meta, isUser = false) {
    const e = _entry(t);
    e.sources.add('core_watchlist');
    _addBucket(e, isUser ? 'core_watchlist:user' : 'core_watchlist');
    e.name = meta.name || e.name;
    e.sectorEtf = meta.sector_etf || e.sectorEtf;
    e.modelType = meta.model_type || e.modelType;
    if (meta.high_beta != null) e.highBeta = !!meta.high_beta;
    if (meta.quality_tier != null) e.qualityTier = meta.quality_tier;
    if (meta.theme_ko && !e.themeKo) e.themeKo = meta.theme_ko;
  }

  function _discoveryScore(e) {
    let s = 0;
    s += Math.min(e.sources.size, 5) * 12;
    if (e.sources.has('core_watchlist')) s += 20;
    if (e.themeLeader) s += 12;
    if (e.isNasdaq100) s += 6;
    return Math.round(Math.min(100, s) * 10) / 10;
  }

  function _canonicalSource(e) {
    for (const s of SOURCE_PRIORITY) if (e.sources.has(s)) return s;
    return e.sources.values().next().value || 'unknown';
  }

  function _summary() {
    return { count: STATE.entries.size, bySource: { ...STATE.bySource }, builtAt: STATE.builtAt };
  }

  // -------------------------------------------------------------------------
  // Accessors
  // -------------------------------------------------------------------------
  function getAll() {
    return Array.from(STATE.entries.values()).map(e => ({
      ...e, sources: Array.from(e.sources), canonicalSource: _canonicalSource(e),
    }));
  }
  function getAllTickers() { return Array.from(STATE.entries.keys()); }
  function get(ticker) {
    const e = STATE.entries.get((ticker || '').toUpperCase());
    if (!e) return null;
    return { ...e, sources: Array.from(e.sources), canonicalSource: _canonicalSource(e) };
  }
  function getSourceBreakdown() { return { ...STATE.bySource }; }
  function getBySource(src) {
    return getAll().filter(e => e.sources.includes(src));
  }
  // Today's market context driving the dynamic ordering (null in Static w/o views).
  function getRegimeContext() {
    const c = STATE.regimeCtx;
    if (!c) return null;
    return {
      regime: c.regime, secondary: c.secondary, confidence: c.confidence, asOf: c.asOf,
      favoredThemes: [...c.favoredThemes], emergingThemes: [...c.emergingThemes],
      avoidedThemes: [...c.avoidedThemes], fadingThemes: [...c.fadingThemes],
      favoredSectors: [...c.favoredSectors], avoidedSectors: [...c.avoidedSectors],
    };
  }
  function getCoreEligible() {
    // Decide mode GLOBALLY (not per-ticker), so "Local beyond 500 cap" is not
    // confused with "Static no-data":
    //   - persistence data present (Local) → strict days_active >= 5.
    //     ORDER BY days_active DESC LIMIT 500 guarantees eligible tickers are loaded,
    //     so a missing ticker legitimately has days_active < 5 → not eligible.
    //   - no persistence data (Static/Fallback) → core_watchlist membership proxy.
    if (STATE.hasDaysActive) {
      return getAll().filter(e => (e.daysActive ?? 0) >= CORE_MIN_DAYS_ACTIVE);
    }
    return getAll().filter(e => e.sources.includes('core_watchlist'));
  }

  // -------------------------------------------------------------------------
  // User Core watchlist additions (localStorage)
  // -------------------------------------------------------------------------
  function userCoreAdditions() {
    try { return JSON.parse(localStorage.getItem(USER_CORE_KEY) || '{}'); }
    catch (_) { return {}; }
  }
  function addToCore(ticker, meta = {}) {
    ticker = (ticker || '').toUpperCase().trim();
    if (!ticker) return false;
    const adds = userCoreAdditions();
    adds[ticker] = {
      name: meta.name || ticker,
      theme_ko: meta.theme_ko || '사용자 추가',
      sector_etf: meta.sector_etf || null,
      model_type: meta.model_type || 'operating_company',
      quality_tier: meta.quality_tier ?? 2,
      high_beta: !!meta.high_beta,
      added_at: new Date().toISOString(),
    };
    try { localStorage.setItem(USER_CORE_KEY, JSON.stringify(adds)); } catch (_) { return false; }
    STATE.built = false;  // force rebuild on next build()
    return true;
  }
  function removeFromCore(ticker) {
    ticker = (ticker || '').toUpperCase().trim();
    const adds = userCoreAdditions();
    if (adds[ticker]) {
      delete adds[ticker];
      try { localStorage.setItem(USER_CORE_KEY, JSON.stringify(adds)); } catch (_) {}
      STATE.built = false;
      return true;
    }
    return false;
  }

  return {
    build, getAll, getAllTickers, get, getSourceBreakdown, getBySource, getRegimeContext,
    getCoreEligible, userCoreAdditions, addToCore, removeFromCore,
    get isBuilt() { return STATE.built; },
    get count() { return STATE.entries.size; },
  };
})();

if (typeof window !== 'undefined') window.UNIVERSE = UNIVERSE;
