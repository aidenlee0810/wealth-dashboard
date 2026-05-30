// research/cache.js — TTL-based localStorage cache for research platform
// All keys use 'wr_' prefix to avoid collision with wealth dashboard 'wd_' keys

const CACHE = (() => {
  const TTL = {
    quote:        5   * 60 * 1000,       // 5 min  — real-time price
    candles:      60  * 60 * 1000,       // 1 hour — OHLCV data
    fundamentals: 24  * 3600 * 1000,    // 24 hours — financial statements
    macro:        6   * 3600 * 1000,    // 6 hours — FRED series
    sec13f:       7   * 24 * 3600 * 1000, // 7 days — 13F filings
    sectors:      3   * 3600 * 1000,    // 3 hours — sector ETF performance
    news:         30  * 60 * 1000,      // 30 min  — news feed
    insider:      6   * 3600 * 1000,    // 6 hours — insider transactions
  };

  const PREFIX = 'wr_';

  function _key(type, id) {
    return PREFIX + type + (id ? '_' + id.replace(/[^a-zA-Z0-9]/g, '_') : '');
  }

  // Get cached value, returns null if missing or expired
  function get(type, id) {
    try {
      const raw = localStorage.getItem(_key(type, id));
      if (!raw) return null;
      const entry = JSON.parse(raw);
      const maxAge = TTL[type] || TTL.fundamentals;
      if (Date.now() - entry.ts > maxAge) {
        localStorage.removeItem(_key(type, id));
        return null;
      }
      return entry.data;
    } catch (_) {
      return null;
    }
  }

  // Store a value with current timestamp
  function set(type, id, data) {
    try {
      localStorage.setItem(_key(type, id), JSON.stringify({ data, ts: Date.now() }));
    } catch (e) {
      // Quota exceeded — evict oldest research entries
      if (e.name === 'QuotaExceededError') {
        _evictOldest();
        try {
          localStorage.setItem(_key(type, id), JSON.stringify({ data, ts: Date.now() }));
        } catch (_) { /* ignore */ }
      }
    }
  }

  // Delete a specific cache entry
  function del(type, id) {
    try { localStorage.removeItem(_key(type, id)); } catch (_) {}
  }

  // Get age of cached entry in ms (returns Infinity if not cached)
  function age(type, id) {
    try {
      const raw = localStorage.getItem(_key(type, id));
      if (!raw) return Infinity;
      const entry = JSON.parse(raw);
      return Date.now() - entry.ts;
    } catch (_) {
      return Infinity;
    }
  }

  // Returns human-readable age string for stale-data badges
  function ageLabel(type, id) {
    const ms = age(type, id);
    if (ms === Infinity) return null;
    if (ms < 60 * 1000) return '방금';
    if (ms < 3600 * 1000) return Math.floor(ms / 60000) + '분 전';
    if (ms < 86400 * 1000) return Math.floor(ms / 3600000) + '시간 전';
    return Math.floor(ms / 86400000) + '일 전';
  }

  // Evict oldest wr_ entries when storage is full
  function _evictOldest() {
    const entries = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key.startsWith(PREFIX)) continue;
      try {
        const entry = JSON.parse(localStorage.getItem(key));
        entries.push({ key, ts: entry.ts || 0 });
      } catch (_) {}
    }
    entries.sort((a, b) => a.ts - b.ts);
    // Evict oldest 20%
    const toEvict = Math.max(1, Math.floor(entries.length * 0.2));
    entries.slice(0, toEvict).forEach(e => localStorage.removeItem(e.key));
  }

  // Clear all research cache entries
  function clearAll() {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith(PREFIX)) keys.push(key);
    }
    keys.forEach(k => localStorage.removeItem(k));
  }

  // Track Alpha Vantage daily call count (25/day free limit)
  function avCallCount() {
    const dateKey = PREFIX + 'av_calls_' + new Date().toISOString().slice(0, 10);
    try {
      return parseInt(localStorage.getItem(dateKey) || '0', 10);
    } catch (_) { return 0; }
  }

  function avIncrCount() {
    const dateKey = PREFIX + 'av_calls_' + new Date().toISOString().slice(0, 10);
    try {
      localStorage.setItem(dateKey, String(avCallCount() + 1));
    } catch (_) {}
  }

  const AV_DAILY_LIMIT = 25;

  return { get, set, del, age, ageLabel, clearAll, avCallCount, avIncrCount, AV_DAILY_LIMIT };
})();
