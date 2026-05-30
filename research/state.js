// research/state.js — URL parameter parsing + cross-app state
// Reads portfolio positions from wealth dashboard localStorage (wd_holdings_cache)
// Never writes to wd_ keys — read-only contract

const STATE = (() => {
  const params = new URLSearchParams(location.search);

  // Active ticker from URL (?ticker=AAPL)
  let _ticker = (params.get('ticker') || '').toUpperCase().trim() || null;
  let _tab = params.get('tab') || 'fundamentals';

  // Simple event bus
  const _handlers = {};

  function on(event, fn) {
    if (!_handlers[event]) _handlers[event] = [];
    _handlers[event].push(fn);
  }

  function off(event, fn) {
    if (!_handlers[event]) return;
    _handlers[event] = _handlers[event].filter(h => h !== fn);
  }

  function emit(event, data) {
    (_handlers[event] || []).forEach(fn => { try { fn(data); } catch(e) { console.error(e); } });
  }

  // Get current active ticker
  function getTicker() { return _ticker; }

  // Update ticker, update URL without full reload, emit event
  function setTicker(symbol) {
    _ticker = symbol ? symbol.toUpperCase().trim() : null;
    // Persist last viewed ticker
    if (_ticker) {
      try { localStorage.setItem('wr_last_ticker', _ticker); } catch (_) {}
    }
    // Update URL bar
    const url = new URL(location.href);
    if (_ticker) url.searchParams.set('ticker', _ticker);
    else url.searchParams.delete('ticker');
    history.pushState({}, '', url.toString());
    emit('ticker', _ticker);
  }

  // Get active tab
  function getTab() { return _tab; }

  function setTab(name) {
    _tab = name;
    const url = new URL(location.href);
    url.searchParams.set('tab', name);
    history.pushState({}, '', url.toString());
    emit('tab', name);
  }

  // Read my portfolio positions from wealth dashboard cache
  // Handles both legacy array format and new {ts, holdings:[...]} format
  function getMyPositions() {
    try {
      const raw = localStorage.getItem('wd_holdings_cache');
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
      if (parsed && Array.isArray(parsed.holdings)) return parsed.holdings;
      return [];
    } catch (_) {
      return [];
    }
  }

  // Get cache metadata (timestamp)
  function getCacheTs() {
    try {
      const raw = localStorage.getItem('wd_holdings_cache');
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && parsed.ts ? parsed.ts : null;
    } catch (_) { return null; }
  }

  // Check if a given ticker is in my portfolio
  // Returns aggregated position: { ticker, shares, avgCost, costBasis, currentValue, gain, gainPct,
  //                                 isLongTerm, holdingPeriodDays, accounts, currentWeight }
  function myPosition(ticker) {
    if (!ticker) return null;
    const sym = ticker.toUpperCase();
    const positions = getMyPositions();
    const matches = positions.filter(p => (p.ticker || '').toUpperCase() === sym);
    if (!matches.length) return null;

    const totalShares    = matches.reduce((s, p) => s + (parseFloat(p.shares) || 0), 0);
    const totalCostBasis = matches.reduce((s, p) => s + (parseFloat(p.costBasis) || parseFloat(p.avgCost) * parseFloat(p.shares) || 0), 0);
    const totalValue     = matches.reduce((s, p) => s + (parseFloat(p.currentValue) || parseFloat(p.marketVal) || 0), 0);
    const totalGain      = matches.reduce((s, p) => s + (parseFloat(p.gain) || 0), 0);
    const avgCostPerShare = totalShares > 0 ? totalCostBasis / totalShares : (parseFloat(matches[0].avgCost) || 0);
    const gainPct        = totalCostBasis > 0 ? (totalGain / totalCostBasis) * 100 : 0;
    // Use earliest lot to determine LT/ST
    const minDays = matches.reduce((mn, p) => Math.min(mn, parseFloat(p.holdingPeriodDays) || 9999), 9999);
    const maxDays = matches.reduce((mx, p) => Math.max(mx, parseFloat(p.holdingPeriodDays) || 0), 0);
    const currentWeight  = matches.reduce((s, p) => s + (parseFloat(p.currentWeight) || 0), 0);

    return {
      ticker: sym,
      shares: totalShares,
      avgCost: avgCostPerShare,
      costBasis: totalCostBasis,
      currentValue: totalValue,
      gain: totalGain,
      gainPct,
      isLongTerm: maxDays >= 365,
      holdingPeriodDays: maxDays === 0 ? null : maxDays,
      holdingPeriodMin: minDays === 9999 ? null : minDays,
      accounts: [...new Set(matches.map(m => m.account).filter(Boolean))],
      lots: matches,
      currentWeight,
    };
  }

  // Restore last viewed ticker if URL has none
  function init() {
    if (!_ticker) {
      try {
        const last = localStorage.getItem('wr_last_ticker');
        if (last) _ticker = last;
      } catch (_) {}
    }
  }

  init();

  return { getTicker, setTicker, getTab, setTab, getMyPositions, getCacheTs, myPosition, on, off, emit };
})();
