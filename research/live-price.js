/* ─────────────────────────────────────────────────────────────────────────
 * research/live-price.js — LIVE_PRICE
 *
 * Intraday quote layer on top of the EOD snapshot pipeline. Talks to
 * server.py's /api/price/snapshot proxy (Alpaca, keys SERVER-SIDE) so the
 * browser gets a whole watchlist of live prices + today's % change in ONE
 * batched request, without ever holding an API key.
 *
 * Degrades gracefully:
 *   • Local Mode + ALPACA keys → live quotes (🟢).
 *   • Local Mode, no keys      → 503; widget shows a small "enable" hint.
 *   • Static site / no server  → fetch fails; widget hides; callers fall back
 *                                to their existing per-ticker quote source.
 *
 * Public API:
 *   LIVE_PRICE.snapshot(tickers[, {force}])  → Promise<{TICKER:{price,changePct,…}}>
 *   LIVE_PRICE.clock()                       → Promise<{isOpen,…}|null>
 *   LIVE_PRICE.available()                   → boolean (provider served last call)
 *   LIVE_PRICE.state()                       → 'live' | 'no_keys' | 'offline' | 'unknown'
 *   LIVE_PRICE.mountStrip(containerId, holdings[, opts])  → live, auto-refreshing widget
 * ───────────────────────────────────────────────────────────────────────── */
const LIVE_PRICE = (() => {
  'use strict';

  const SNAPSHOT_URL = '/api/price/snapshot';
  const CLOCK_URL    = '/api/price/clock';
  const CACHE_TTL_MS = 15000;     // mirror server cache; avoid redundant calls
  const MAX_SYMBOLS  = 150;
  const REFRESH_OPEN_MS   = 25000;   // poll cadence while market is open
  const REFRESH_CLOSED_MS = 120000;  // slow poll when closed

  // state: 'unknown' before first call; 'live' served by Alpaca;
  // 'no_keys' server up but unconfigured (503); 'offline' no server / fetch fail
  let _state = 'unknown';
  let _clock = null;
  let _clockTs = 0;

  const _cache = new Map();        // key(sorted tickers) -> {ts, quotes}
  const _inflight = new Map();     // key -> Promise (dedupe concurrent calls)

  // ── helpers ───────────────────────────────────────────────────────────────
  function _norm(q) {
    if (!q) return null;
    return {
      ticker:    q.ticker,
      price:     q.price,
      prevClose: q.prev_close,
      change:    q.change,
      changePct: q.change_pct,
      dayOpen:   q.day_open,
      dayHigh:   q.day_high,
      dayLow:    q.day_low,
      dayVolume: q.day_volume,
      bid:       q.bid,
      ask:       q.ask,
      ts:        q.ts,
      source:    q.source || 'alpaca',
    };
  }

  function _key(tickers) {
    return tickers.slice().sort().join(',');
  }

  function _clean(tickers) {
    const seen = new Set();
    const out = [];
    for (const t of (tickers || [])) {
      const s = String(t || '').trim().toUpperCase();
      if (s && !seen.has(s)) { seen.add(s); out.push(s); }
      if (out.length >= MAX_SYMBOLS) break;
    }
    return out;
  }

  // ── core fetch ──────────────────────────────────────────────────────────
  async function snapshot(tickers, opts = {}) {
    const syms = _clean(tickers);
    if (!syms.length) return {};
    const key = _key(syms);
    const now = Date.now();

    if (!opts.force) {
      const hit = _cache.get(key);
      if (hit && now - hit.ts < CACHE_TTL_MS) return hit.quotes;
      if (_inflight.has(key)) return _inflight.get(key);
    }

    const p = (async () => {
      try {
        const url = SNAPSHOT_URL + '?symbols=' + encodeURIComponent(syms.join(','));
        const res = await fetch(url, { headers: { Accept: 'application/json' } });
        if (res.status === 503) { _state = 'no_keys'; return {}; }
        if (!res.ok) { _state = 'offline'; return {}; }
        const data = await res.json();
        const quotes = {};
        for (const [t, q] of Object.entries(data.quotes || {})) {
          const n = _norm(q);
          if (n) quotes[t] = n;
        }
        _state = 'live';
        _cache.set(key, { ts: Date.now(), quotes });
        return quotes;
      } catch (_e) {
        _state = 'offline';        // no server (static site) or network error
        return {};
      } finally {
        _inflight.delete(key);
      }
    })();

    _inflight.set(key, p);
    return p;
  }

  async function clock(opts = {}) {
    const now = Date.now();
    if (!opts.force && _clock && now - _clockTs < 30000) return _clock;
    try {
      const res = await fetch(CLOCK_URL, { headers: { Accept: 'application/json' } });
      if (!res.ok) { if (res.status === 503) _state = 'no_keys'; return null; }
      const d = await res.json();
      _clock = {
        isOpen:    d.is_open,
        nextOpen:  d.next_open,
        nextClose: d.next_close,
        ts:        d.ts,
      };
      _clockTs = now;
      return _clock;
    } catch (_e) {
      return null;
    }
  }

  function available() { return _state === 'live'; }
  function state() { return _state; }

  // ── formatting (self-contained; uses UI.fmt when present) ────────────────
  function _usd(v) {
    if (v == null || isNaN(v)) return '—';
    const abs = Math.abs(v);
    const digits = abs >= 1000 ? 0 : 2;
    return '$' + Number(v).toLocaleString('en-US',
      { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function _pct(v) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
  }
  function _chgColor(v) {
    if (v == null || isNaN(v)) return 'text-slate-400';
    if (v > 0) return 'text-emerald-400';
    if (v < 0) return 'text-rose-400';
    return 'text-slate-300';
  }

  function _badge() {
    if (_state === 'no_keys')
      return '<span class="text-amber-400/90 text-[11px]">⚪ 실시간 OFF · ALPACA 키 필요</span>';
    if (_state === 'offline')
      return '<span class="text-slate-500 text-[11px]">⚪ 실시간 미연결</span>';
    if (_clock && _clock.isOpen === true)
      return '<span class="text-emerald-400 text-[11px]">🟢 장중 (IEX)</span>';
    if (_clock && _clock.isOpen === false)
      return '<span class="text-slate-400 text-[11px]">🔴 장 마감</span>';
    return '<span class="text-slate-400 text-[11px]">🟢 실시간</span>';
  }

  // ── live strip widget ─────────────────────────────────────────────────────
  // holdings: [{ticker, shares?}]. shares enables market-value + portfolio totals.
  function mountStrip(containerId, holdings, opts = {}) {
    const root = document.getElementById(containerId);
    if (!root) return;

    const list = (holdings || [])
      .map(h => ({ ticker: String(h.ticker || '').toUpperCase(), shares: Number(h.shares) || 0 }))
      .filter(h => h.ticker);
    if (!list.length) { root.innerHTML = ''; return; }

    const tickers = list.map(h => h.ticker);
    let timer = null;

    function _renderHint() {
      // Only nag in Local Mode (server present but unconfigured). Hide otherwise.
      if (_state === 'no_keys') {
        root.innerHTML = `
          <div class="bg-slate-800/40 border border-slate-700/60 rounded-xl px-4 py-2.5 text-[11px] text-slate-400">
            ⚡ 실시간 시세 비활성 — <span class="text-slate-300">ALPACA_KEY</span> 설정 시 보유종목 현재가·당일 변동을 실시간 표시합니다
            <span class="text-slate-500">(env 또는 ~/.wealth-dashboard/secrets.env)</span>
          </div>`;
      } else {
        root.innerHTML = '';   // static site / offline → stay out of the way
      }
    }

    function _render(quotes) {
      const haveAny = Object.keys(quotes).length > 0;
      if (!haveAny) { _renderHint(); return; }

      let totVal = 0, totPrev = 0, anyShares = false;
      const rows = list.map(h => {
        const q = quotes[h.ticker];
        const price = q ? q.price : null;
        const chgPct = q ? q.changePct : null;
        let val = null;
        if (q && h.shares > 0 && price != null) {
          val = price * h.shares;
          totVal += val;
          if (q.prevClose != null) totPrev += q.prevClose * h.shares;
          anyShares = true;
        }
        return { t: h.ticker, price, chgPct, change: q ? q.change : null, val };
      }).sort((a, b) => (b.val || 0) - (a.val || 0));

      const dayChg = anyShares ? (totVal - totPrev) : null;
      const dayChgPct = anyShares && totPrev > 0 ? (dayChg / totPrev * 100) : null;

      const cells = rows.map(r => `
        <div class="flex items-center justify-between gap-3 px-2.5 py-1.5 rounded-lg hover:bg-slate-700/30">
          <span class="font-semibold text-slate-200 text-xs w-14 shrink-0">${r.t}</span>
          <span class="text-slate-300 text-xs tabular-nums">${_usd(r.price)}</span>
          <span class="text-xs tabular-nums ${_chgColor(r.chgPct)} w-16 text-right">${_pct(r.chgPct)}</span>
          ${r.val != null
            ? `<span class="text-slate-400 text-xs tabular-nums w-20 text-right">${_usd(r.val)}</span>`
            : '<span class="w-20"></span>'}
        </div>`).join('');

      const totalBlock = anyShares ? `
        <div class="flex items-baseline gap-3 mt-1">
          <span class="text-2xl font-bold text-white tabular-nums">${_usd(totVal)}</span>
          <span class="text-sm font-medium tabular-nums ${_chgColor(dayChg)}">
            ${dayChg >= 0 ? '▲' : '▼'} ${_usd(Math.abs(dayChg))} (${_pct(dayChgPct)})
          </span>
          <span class="text-[11px] text-slate-500">오늘</span>
        </div>` : '';

      const asof = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

      root.innerHTML = `
        <div class="bg-gradient-to-br from-slate-800/70 to-slate-900/40 border border-slate-700/60 rounded-2xl p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2">
              <span class="text-sm font-semibold text-slate-200">⚡ 실시간 시세</span>
              ${_badge()}
            </div>
            <div class="flex items-center gap-2">
              <span class="text-[10px] text-slate-500">${asof} 기준</span>
              <button id="${containerId}-refresh" class="text-[11px] text-slate-400 hover:text-emerald-300 px-1.5 py-0.5 rounded">↻</button>
            </div>
          </div>
          ${totalBlock}
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5 mt-2">
            ${cells}
          </div>
        </div>`;

      const btn = document.getElementById(`${containerId}-refresh`);
      if (btn) btn.addEventListener('click', () => tick(true));
    }

    async function tick(force) {
      // stop the loop if the widget has been removed from the DOM
      if (!document.getElementById(containerId)) { if (timer) clearTimeout(timer); return; }
      if (document.hidden) { schedule(); return; }   // pause when tab not visible
      const [quotes] = await Promise.all([
        snapshot(tickers, { force: !!force }),
        clock().catch(() => null),
      ]);
      _render(quotes);
      schedule();
    }

    function schedule() {
      if (timer) clearTimeout(timer);
      const open = _clock && _clock.isOpen === true;
      const ms = open ? REFRESH_OPEN_MS : REFRESH_CLOSED_MS;
      timer = setTimeout(() => tick(false), ms);
    }

    // initial paint
    root.innerHTML = `<div class="text-[11px] text-slate-500 px-2 py-1">⚡ 실시간 시세 불러오는 중…</div>`;
    tick(true);
  }

  return { snapshot, clock, available, state, mountStrip };
})();

if (typeof window !== 'undefined') window.LIVE_PRICE = LIVE_PRICE;
