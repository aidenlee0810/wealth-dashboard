// research/db-client.js
// ============================================================================
// DB client + execution mode detection for Wealth Dashboard Research Platform
// ============================================================================
//
// Three modes (auto-detected on init):
//   1. LOCAL   — `python server.py` running, /api/db/* available, full features
//   2. STATIC  — GitHub Pages (no Python backend), reads data/views/*.json only
//   3. FALLBACK — neither works, reads browser localStorage only
//
// Public API:
//   DB.init()            -> Promise<{mode, snapshot_age_hours, ...}>
//   DB.mode              -> 'local' | 'static' | 'fallback'
//   DB.health()          -> Promise<health JSON>
//   DB.query(table, opts)-> Promise<{count, rows}>
//   DB.view(name)        -> Promise<view JSON>     (works in static mode)
//   DB.local(resource, method, body)  -> Promise   (LOCAL mode only)
//   DB.snapshot()        -> Promise (POST /api/snapshot, LOCAL only)
//   DB.modeBadge()       -> HTML string for mode indicator
//   DB.on(event, fn)     -> register listener
//
// Mode detection:
//   - tries GET /api/db/health
//   - if 200 -> LOCAL
//   - else tries fetch('data/views/latest_snapshot_health.json')
//   - if 200 -> STATIC
//   - else -> FALLBACK
//
// All DB calls return Promises and have built-in error handling +
// localStorage caching of last successful response.
// ============================================================================

const DB = (() => {
  const STATE = {
    mode: 'unknown',                // 'local' | 'static' | 'fallback'
    health: null,
    snapshotAgeHours: null,
    snapshotDate: null,
    snapshotStatus: null,
    localToken: null,               // X-Local-Token for CSRF protection (LOCAL mode only)
    initialized: false,
    listeners: { ready: [], modeChange: [] },
  };

  const VIEW_DIR = 'data/views';
  const API_HEALTH_URL = '/api/db/health';
  const API_QUERY_URL = '/api/db/query';
  const API_LOCAL_PREFIX = '/api/local/';
  const API_SNAPSHOT_URL = '/api/snapshot';
  const CACHE_PREFIX = 'wr_dbcache_';
  const VIEW_CACHE_TTL_MS = 5 * 60 * 1000;  // 5 min

  // -------------------------------------------------------------------------
  // Cache helpers (memoized views in localStorage)
  // -------------------------------------------------------------------------
  function _cacheSet(key, value) {
    try {
      localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({
        ts: Date.now(),
        value,
      }));
    } catch (e) { /* quota — ignore */ }
  }

  function _cacheGet(key, maxAgeMs = VIEW_CACHE_TTL_MS) {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + key);
      if (!raw) return null;
      const { ts, value } = JSON.parse(raw);
      if (Date.now() - ts > maxAgeMs) return null;
      return value;
    } catch (e) { return null; }
  }

  // -------------------------------------------------------------------------
  // Badge update helper — writes to #db-mode-badge if it exists in the DOM
  // -------------------------------------------------------------------------
  function _updateBadge() {
    if (typeof document === 'undefined') return;
    const el = document.getElementById('db-mode-badge');
    if (!el) return;
    el.innerHTML = modeBadge();
    // Remove the generic loading class; specific class applied inside modeBadge()
    el.className = 'mode-badge';
  }

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------
  async function init() {
    if (STATE.initialized) return STATE;

    // Try LOCAL mode first
    try {
      const resp = await fetch(API_HEALTH_URL, { cache: 'no-store' });
      if (resp.ok) {
        const health = await resp.json();
        STATE.mode = 'local';
        STATE.health = health;
        STATE.snapshotAgeHours = health.snapshot_age_hours;
        STATE.snapshotDate = health.last_snapshot;
        STATE.snapshotStatus = health.last_snapshot_status;
        // Store local token for CSRF-protected endpoints
        if (health.local_token) {
          STATE.localToken = health.local_token;
          try { sessionStorage.setItem('wr_local_token', health.local_token); } catch (_) {}
        }
        STATE.initialized = true;
        _updateBadge();
        _emit('ready', STATE);
        _emit('modeChange', STATE.mode);
        return STATE;
      }
    } catch (e) {
      // not running — try static
    }

    // Try STATIC mode (GitHub Pages)
    try {
      const resp = await fetch(`${VIEW_DIR}/latest_snapshot_health.json`, { cache: 'no-store' });
      if (resp.ok) {
        const health = await resp.json();
        STATE.mode = 'static';
        STATE.health = health;
        STATE.snapshotDate = health.snapshot_date || health.market_date;
        if (health.executed_at_utc) {
          STATE.snapshotAgeHours = (Date.now() - new Date(health.executed_at_utc).getTime()) / 3600000;
        }
        STATE.snapshotStatus = health.result_status;
        STATE.initialized = true;
        _updateBadge();
        _emit('ready', STATE);
        _emit('modeChange', STATE.mode);
        return STATE;
      }
    } catch (e) {
      // no views either
    }

    // FALLBACK
    STATE.mode = 'fallback';
    STATE.initialized = true;
    _updateBadge();
    _emit('ready', STATE);
    _emit('modeChange', STATE.mode);
    return STATE;
  }

  // -------------------------------------------------------------------------
  // Health
  // -------------------------------------------------------------------------
  async function health() {
    if (!STATE.initialized) await init();
    if (STATE.mode === 'local') {
      const resp = await fetch(API_HEALTH_URL, { cache: 'no-store' });
      if (resp.ok) {
        STATE.health = await resp.json();
      }
    } else if (STATE.mode === 'static') {
      STATE.health = await view('latest_snapshot_health');
    }
    return STATE.health;
  }

  // -------------------------------------------------------------------------
  // Query (LOCAL only)
  //   table: required string
  //   opts:
  //     cols      : array of column names
  //     where     : object of filters (key__op=value, op ∈ eq,ne,lt,lte,gt,gte,like)
  //     order_by  : "column ASC|DESC"
  //     limit     : 1-500
  // -------------------------------------------------------------------------
  async function query(table, opts = {}) {
    if (!STATE.initialized) await init();
    if (STATE.mode !== 'local') {
      throw new Error(`DB.query requires LOCAL mode (current: ${STATE.mode}). Use DB.view() for static mode.`);
    }
    const params = new URLSearchParams({ table });
    if (opts.cols) params.set('cols', opts.cols.join(','));
    if (opts.order_by) params.set('order_by', opts.order_by);
    if (opts.limit) params.set('limit', String(opts.limit));
    if (opts.where) {
      for (const [k, v] of Object.entries(opts.where)) {
        params.set(k, String(v));
      }
    }
    const resp = await fetch(`${API_QUERY_URL}?${params}`, { cache: 'no-store' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      throw new Error(`DB.query[${table}] ${resp.status}: ${err.error}`);
    }
    return resp.json();
  }

  // -------------------------------------------------------------------------
  // View (LOCAL or STATIC, both modes)
  //   name: e.g. 'latest_market_regime' (no .json extension)
  // -------------------------------------------------------------------------
  async function view(name) {
    const cacheKey = `view_${name}`;
    const cached = _cacheGet(cacheKey);
    if (cached) return cached;

    try {
      const resp = await fetch(`${VIEW_DIR}/${name}.json`, { cache: 'no-store' });
      if (resp.ok) {
        const data = await resp.json();
        _cacheSet(cacheKey, data);
        return data;
      }
    } catch (e) {
      // fall through to null
    }
    return null;
  }

  // -------------------------------------------------------------------------
  // Local personal DB (LOCAL mode only)
  //   resource: 'portfolio','targets','actions','buckets','lots','notes','allocations'
  //   method: 'GET' | 'POST' | 'DELETE'
  //   body: object for POST
  //   params: object for GET/DELETE query string
  // -------------------------------------------------------------------------
  async function local(resource, method = 'GET', body = null, params = null) {
    if (!STATE.initialized) await init();
    if (STATE.mode !== 'local') {
      throw new Error(`DB.local requires LOCAL mode. Currently in ${STATE.mode}. Personal data is local-only.`);
    }
    let url = API_LOCAL_PREFIX + resource;
    if (params) {
      const qs = new URLSearchParams(params);
      url += '?' + qs.toString();
    }
    // Attach X-Local-Token for CSRF protection (server validates if present)
    const token = STATE.localToken ||
      (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('wr_local_token')) || '';
    const opts = {
      method,
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-Local-Token': token } : {}),
      },
    };
    if (body && method !== 'GET') opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      throw new Error(`DB.local[${resource}] ${resp.status}: ${err.error}`);
    }
    return resp.json();
  }

  // -------------------------------------------------------------------------
  // Phase 8: user actions + 3-ledger comparison (Local Mode only)
  // -------------------------------------------------------------------------
  //   recordAction({date, signal_id, ticker, action_type, actual_price,
  //                 actual_amount, actual_shares, account, notes})
  //   action_type ∈ BUY | SELL | IGNORE | PARTIAL | REJECT | WATCHLIST_ONLY
  async function recordAction(action) {
    if (!action || !action.action_type) {
      throw new Error('recordAction requires {action_type, ...}');
    }
    if (!action.date) action.date = new Date().toISOString().slice(0, 10);
    return local('actions', 'POST', action);
  }

  // 3-ledger comparison: System Pure vs Risk Policy vs User Actual (§8)
  async function ledgerComparison(horizon = 60, account = null) {
    const params = { horizon };
    if (account) params.account = account;
    return local('ledger', 'GET', null, params);
  }

  // -------------------------------------------------------------------------
  // Snapshot trigger
  // -------------------------------------------------------------------------
  async function snapshot() {
    if (!STATE.initialized) await init();
    if (STATE.mode !== 'local') {
      throw new Error('Snapshot requires LOCAL mode. Use git pull to fetch cloud snapshots.');
    }
    const resp = await fetch(API_SNAPSHOT_URL, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      throw new Error(`Snapshot ${resp.status}: ${err.error}`);
    }
    return resp.json();
  }

  // -------------------------------------------------------------------------
  // Mode badge HTML for UI display
  // -------------------------------------------------------------------------
  function modeBadge() {
    if (!STATE.initialized) {
      return `<span class="mode-badge mode-loading">Loading…</span>`;
    }
    const ageHrs = STATE.snapshotAgeHours;
    const ageText = ageHrs == null ? '' : (
      ageHrs < 24 ? ` (${Math.round(ageHrs)}h ago)` :
      ageHrs < 168 ? ` (${Math.round(ageHrs / 24)}d ago)` :
      ` (${Math.round(ageHrs / 24)}d ago — stale)`
    );

    if (STATE.mode === 'local') {
      const fresh = ageHrs == null || ageHrs < 36;
      return `<span class="mode-badge ${fresh ? 'mode-live' : 'mode-stale'}">
                ${fresh ? '🟢 DB live' : '🟠 Snapshot stale'}${ageText}
              </span>`;
    }
    if (STATE.mode === 'static') {
      const fresh = ageHrs == null || ageHrs < 48;
      return `<span class="mode-badge ${fresh ? 'mode-static' : 'mode-stale'}">
                ${fresh ? '🟡 Static snapshot' : '🟠 Snapshot stale'} ${STATE.snapshotDate || ''}${ageText}
              </span>`;
    }
    return `<span class="mode-badge mode-fallback">🔴 Fallback (localStorage only)</span>`;
  }

  // -------------------------------------------------------------------------
  // Event listeners
  // -------------------------------------------------------------------------
  function on(event, fn) {
    if (!STATE.listeners[event]) STATE.listeners[event] = [];
    STATE.listeners[event].push(fn);
  }

  function _emit(event, payload) {
    (STATE.listeners[event] || []).forEach(fn => {
      try { fn(payload); } catch (e) { console.error(e); }
    });
    // Auto-refresh badge on any mode change
    if (event === 'modeChange') _updateBadge();
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------
  return {
    init,
    health,
    query,
    view,
    local,
    recordAction,
    ledgerComparison,
    snapshot,
    modeBadge,
    on,
    // Getters
    get mode() { return STATE.mode; },
    get state() { return STATE; },
    get isLocal()    { return STATE.mode === 'local'; },
    get isStatic()   { return STATE.mode === 'static'; },
    get isFallback() { return STATE.mode === 'fallback'; },
  };
})();

// Auto-init on script load (non-blocking)
if (typeof window !== 'undefined') {
  window.DB = DB;
  DB.init().catch(e => console.warn('[DB] init failed:', e));
}
