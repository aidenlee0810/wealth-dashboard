// research/ui.js — Tab engine, KPI renderer, loading overlays, shared UI utilities
// Matches the dark theme and component style of the wealth dashboard

const UI = (() => {
  // ── Color palette (matches wealth dashboard) ─────────────────────────────
  const C = {
    surface: '#0f172a', card: '#1e293b', border: '#334155',
    muted: '#64748b', text: '#e2e8f0', accent: '#10b981',
    warn: '#f59e0b', danger: '#ef4444', blue: '#3b82f6',
    purple: '#8b5cf6', indigo: '#6366f1',
  };

  // ── Tab engine ────────────────────────────────────────────────────────────
  const _initialized = {};

  function setupTabs(tabs, onSwitch) {
    tabs.forEach(({ btn, panel, name }) => {
      btn.addEventListener('click', () => activateTab(name, tabs, onSwitch));
    });
    // Activate initial tab from STATE
    const initial = STATE.getTab();
    const target = tabs.find(t => t.name === initial) || tabs[0];
    activateTab(target.name, tabs, onSwitch);
  }

  function activateTab(name, tabs, onSwitch) {
    STATE.setTab(name);
    tabs.forEach(({ btn, panel, name: n }) => {
      const active = n === name;
      btn.classList.toggle('bg-slate-700', active);
      btn.classList.toggle('text-white', active);
      btn.classList.toggle('text-slate-400', !active);
      panel.classList.toggle('hidden', !active);
    });
    if (!_initialized[name]) {
      _initialized[name] = true;
      if (onSwitch) onSwitch(name);
    }
  }

  // Force re-render a tab (e.g., after ticker change)
  function resetTab(name) { _initialized[name] = false; }
  function resetAllTabs() { Object.keys(_initialized).forEach(k => delete _initialized[k]); }

  // ── Loading overlay ───────────────────────────────────────────────────────
  function loading(containerId, message = '데이터 로딩 중...') {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `
      <div class="flex flex-col items-center justify-center py-16 text-slate-400">
        <svg class="animate-spin h-8 w-8 mb-4 text-emerald-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
        <span class="text-sm">${message}</span>
      </div>`;
  }

  function error(containerId, message = '데이터를 불러오지 못했습니다') {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `
      <div class="flex flex-col items-center justify-center py-12 text-red-400">
        <svg class="h-8 w-8 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
        </svg>
        <span class="text-sm text-center px-4">${message}</span>
        <button onclick="location.reload()" class="mt-3 text-xs text-slate-500 underline">새로고침</button>
      </div>`;
  }

  function noKey(containerId, apiName, configKey) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `
      <div class="flex flex-col items-center justify-center py-12 text-amber-400 gap-3">
        <svg class="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/>
        </svg>
        <span class="text-sm font-medium">${apiName} API 키 필요</span>
        <span class="text-xs text-slate-400 text-center px-6">우측 상단 <strong class="text-slate-300">⚙️ API 설정</strong> 버튼을 눌러 키를 입력하세요</span>
        <button onclick="_openSettings()"
          class="mt-1 bg-amber-600/80 hover:bg-amber-500 text-white text-xs px-4 py-2 rounded-lg transition-colors">
          ⚙️ API 키 설정하기
        </button>
      </div>`;
  }

  // ── KPI card renderer ─────────────────────────────────────────────────────
  // value: formatted string, sub: secondary label, color: 'green'|'red'|'yellow'|'blue'|null
  function kpi(label, value, { sub = '', color = null, icon = '', cacheAge = null } = {}) {
    const colorMap = { green: 'text-emerald-400', red: 'text-red-400', yellow: 'text-amber-400', blue: 'text-blue-400' };
    const valueClass = colorMap[color] || 'text-white';
    const ageHtml = cacheAge
      ? `<span class="absolute top-1 right-2 text-[10px] text-slate-600">${cacheAge}</span>`
      : '';
    return `
      <div class="relative bg-slate-800 rounded-xl p-4 border border-slate-700 flex flex-col gap-1 min-w-0">
        ${ageHtml}
        <span class="text-xs text-slate-400 truncate">${icon ? icon + ' ' : ''}${label}</span>
        <span class="text-lg font-bold ${valueClass} truncate">${value ?? '—'}</span>
        ${sub ? `<span class="text-xs text-slate-500 truncate">${sub}</span>` : ''}
      </div>`;
  }

  // Render a row of KPI cards into a container
  function renderKPIs(containerId, items) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = items.map(item => kpi(item.label, item.value, item)).join('');
  }

  // ── Section headers ───────────────────────────────────────────────────────
  function sectionHeader(title, sub = '') {
    return `
      <div class="flex items-baseline gap-3 mb-3">
        <h3 class="text-base font-semibold text-slate-200">${title}</h3>
        ${sub ? `<span class="text-xs text-slate-500">${sub}</span>` : ''}
      </div>`;
  }

  // ── Ticker badge / "My Position" badge ───────────────────────────────────
  function renderTickerHeader(ticker, profileData, pos) {
    const el = document.getElementById('ticker-header');
    if (!el) return;
    if (!ticker) {
      el.innerHTML = '<span class="text-slate-500 text-sm">종목을 검색하세요</span>';
      return;
    }
    const changePct = profileData?.changePct;
    const changeColor = changePct > 0 ? 'text-emerald-400' : changePct < 0 ? 'text-red-400' : 'text-slate-400';
    const posHtml = pos ? `
      <span class="shrink-0 bg-emerald-900/50 text-emerald-400 border border-emerald-700 text-xs px-2 py-0.5 rounded-full whitespace-nowrap">
        보유 ${fmt.shares(pos.shares)}주
      </span>` : '';
    el.innerHTML = `
      <div class="flex items-center justify-between gap-2 min-w-0">
        <div class="flex items-center gap-2 min-w-0 overflow-hidden">
          ${profileData?.logo ? `<img src="${profileData.logo}" class="h-6 w-6 rounded object-contain bg-white p-0.5 shrink-0" onerror="this.remove()">` : ''}
          <span class="text-base font-bold text-white shrink-0">${ticker}</span>
          ${profileData?.name ? `<span class="text-slate-400 text-xs truncate hidden sm:block">${profileData.name}</span>` : ''}
          ${profileData?.sector ? `<span class="text-xs text-slate-500 bg-slate-700 px-2 py-0.5 rounded-full shrink-0 hidden sm:block">${profileData.sector}</span>` : ''}
        </div>
        ${posHtml}
      </div>`;
  }

  // ── Toast notification ────────────────────────────────────────────────────
  function toast(message, type = 'info', duration = 4000) {
    const colorMap = {
      info:    'bg-slate-700 border-slate-600 text-slate-200',
      success: 'bg-emerald-900/80 border-emerald-700 text-emerald-200',
      warn:    'bg-amber-900/80 border-amber-700 text-amber-200',
      error:   'bg-red-900/80 border-red-700 text-red-200',
    };
    const container = document.getElementById('toast-container') || _createToastContainer();
    const el = document.createElement('div');
    el.className = `px-4 py-3 rounded-lg border text-sm shadow-lg transition-all ${colorMap[type] || colorMap.info}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, duration);
  }

  function _createToastContainer() {
    const el = document.createElement('div');
    el.id = 'toast-container';
    el.className = 'fixed bottom-4 right-4 flex flex-col gap-2 z-50 max-w-xs';
    document.body.appendChild(el);
    return el;
  }

  // ── Signal badge: green/yellow/red with label ─────────────────────────────
  function signalBadge(label, type = 'neutral') {
    const map = {
      bullish: 'bg-emerald-900/60 text-emerald-400 border-emerald-700',
      bearish: 'bg-red-900/60 text-red-400 border-red-700',
      neutral: 'bg-slate-700 text-slate-400 border-slate-600',
      warn:    'bg-amber-900/60 text-amber-400 border-amber-700',
    };
    return `<span class="border text-xs px-2 py-0.5 rounded-full ${map[type] || map.neutral}">${label}</span>`;
  }

  // ── Score meter (Piotroski, Altman) ──────────────────────────────────────
  function scoreMeter(score, max, label, thresholds = { low: max * 0.33, high: max * 0.67 }) {
    const pct = max > 0 ? (score / max) * 100 : 0;
    const color = score >= thresholds.high ? '#10b981' : score >= thresholds.low ? '#f59e0b' : '#ef4444';
    return `
      <div class="flex flex-col gap-1">
        <div class="flex justify-between text-xs text-slate-400 mb-0.5">
          <span>${label}</span>
          <span class="font-semibold" style="color:${color}">${score ?? '—'} / ${max}</span>
        </div>
        <div class="w-full bg-slate-700 rounded-full h-2">
          <div class="h-2 rounded-full transition-all" style="width:${pct}%;background:${color}"></div>
        </div>
      </div>`;
  }

  // ── Mini sparkline (inline SVG) ───────────────────────────────────────────
  function sparkline(values, width = 80, height = 24, color = '#10b981') {
    if (!values || values.length < 2) return '';
    const clean = values.filter(v => v != null && !isNaN(v));
    if (clean.length < 2) return '';
    const min = Math.min(...clean);
    const max = Math.max(...clean);
    const range = max - min || 1;
    const step = width / (clean.length - 1);
    const pts = clean.map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="inline-block">
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/>
    </svg>`;
  }

  // ── Table builder ─────────────────────────────────────────────────────────
  function table(headers, rows, { id = '', compact = false } = {}) {
    const thCls = `px-3 ${compact ? 'py-1.5' : 'py-2'} text-left text-xs font-medium text-slate-400 uppercase tracking-wide`;
    const tdCls = `px-3 ${compact ? 'py-1.5' : 'py-2'} text-sm`;
    return `
      <div class="overflow-x-auto rounded-lg border border-slate-700">
        <table class="w-full" ${id ? `id="${id}"` : ''}>
          <thead class="bg-slate-800/80">
            <tr>${headers.map(h => `<th class="${thCls}">${h}</th>`).join('')}</tr>
          </thead>
          <tbody class="divide-y divide-slate-700/50">
            ${rows.map(row => `<tr class="hover:bg-slate-700/30 transition-colors">${row.map(cell => `<td class="${tdCls}">${cell}</td>`).join('')}</tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // ── Formatting helpers ────────────────────────────────────────────────────
  const fmt = {
    dollar: (v, digits = 0) => v == null ? '—'
      : '$' + (Math.abs(v) >= 1e9 ? (v/1e9).toFixed(1) + 'B'
              : Math.abs(v) >= 1e6 ? (v/1e6).toFixed(1) + 'M'
              : Math.abs(v) >= 1e3 ? (v/1e3).toFixed(1) + 'K'
              : v.toFixed(digits)),

    pct: (v, digits = 1) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(digits) + '%',

    pctRaw: (v, digits = 1) => v == null ? '—' : v.toFixed(digits) + '%',

    num: (v, digits = 2) => v == null ? '—' : v.toFixed(digits),

    multiple: (v, digits = 1) => v == null ? '—' : v.toFixed(digits) + 'x',

    shares: (v) => v == null ? '—' : v.toLocaleString('en-US', { maximumFractionDigits: 2 }),

    date: (ts) => {
      if (!ts) return '—';
      const d = typeof ts === 'string' ? new Date(ts) : new Date(ts * 1000);
      return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'short', day: 'numeric' });
    },

    pctColor: (v) => v == null ? '' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400',

    changeArrow: (v) => v == null ? '' : v > 0 ? '▲' : v < 0 ? '▼' : '—',
  };

  return {
    C, setupTabs, activateTab, resetTab, resetAllTabs,
    loading, error, noKey, kpi, renderKPIs, sectionHeader,
    renderTickerHeader, toast, signalBadge, scoreMeter, sparkline,
    table, fmt,
  };
})();
