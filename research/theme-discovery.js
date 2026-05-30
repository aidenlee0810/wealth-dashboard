// research/theme-discovery.js — Theme lifecycle board (Plan §10, §30)
//
// Loads data/views/latest_sector_theme.json  → per-theme phase + scores
//        data/views/latest_market_regime.json → favored/avoided/emerging/fading lists
//
// Renders into an existing container:
//   1. Regime overlay strip  (favored ✓ / avoided ✗ / emerging ↑)
//   2. Phase lifecycle board (Emerging → Leading → Neutral → Fading → Breakdown)
//   3. Leadership score table (all themes ranked)
//
// Integration: SECTORS.init() appends the container then calls
//   THEME_DISCOVERY.init('theme-discovery-root')
//
// Static Mode: reads pre-built JSON views — no server required.

const THEME_DISCOVERY = (() => {

  // ── Phase config ────────────────────────────────────────────────────────
  const PHASES = [
    { key: 'emerging',  label: '🌱 Emerging',  desc: '새로운 트렌드 형성 중',
      bg: 'bg-cyan-900/40',    border: 'border-cyan-700/60',   badge: 'bg-cyan-800 text-cyan-200',
      dot: '#06b6d4' },
    { key: 'leading',   label: '🚀 Leading',   desc: '가장 강한 모멘텀',
      bg: 'bg-emerald-900/40', border: 'border-emerald-700/60',badge: 'bg-emerald-800 text-emerald-200',
      dot: '#10b981' },
    { key: 'extended',  label: '📈 Extended',  desc: '고점 연장 — 주의',
      bg: 'bg-yellow-900/30',  border: 'border-yellow-700/60', badge: 'bg-yellow-800 text-yellow-200',
      dot: '#eab308' },
    { key: 'neutral',   label: '➡️ Neutral',   desc: '방향성 불명확',
      bg: 'bg-slate-800/50',   border: 'border-slate-700/60',  badge: 'bg-slate-700 text-slate-300',
      dot: '#64748b' },
    { key: 'fading',    label: '🌅 Fading',    desc: '모멘텀 약화',
      bg: 'bg-amber-900/30',   border: 'border-amber-700/60',  badge: 'bg-amber-800 text-amber-200',
      dot: '#f59e0b' },
    { key: 'breakdown', label: '📉 Breakdown', desc: '추세 붕괴',
      bg: 'bg-red-900/30',     border: 'border-red-700/60',    badge: 'bg-red-900/80 text-red-300',
      dot: '#ef4444' },
  ];
  const PHASE_MAP = Object.fromEntries(PHASES.map(p => [p.key, p]));

  // ── Data loading ─────────────────────────────────────────────────────────
  let _themeData  = null;  // { snapshot_date, themes: [...] }
  let _regimeData = null;  // { regime, favored_themes, avoided_themes, ... }

  async function _loadData() {
    const base = (typeof DB_CLIENT !== 'undefined' && DB_CLIENT.baseUrl)
      ? DB_CLIENT.baseUrl : '';
    const [td, rd] = await Promise.all([
      fetch(`${base}data/views/latest_sector_theme.json`).then(r => r.ok ? r.json() : null),
      fetch(`${base}data/views/latest_market_regime.json`).then(r => r.ok ? r.json() : null),
    ]);
    _themeData  = td;
    _regimeData = rd;
  }

  // ── Helpers ──────────────────────────────────────────────────────────────
  function _pct(v) {
    if (v == null) return '—';
    const s = (v * 100).toFixed(1);
    return (v >= 0 ? '+' : '') + s + '%';
  }
  function _pctClass(v) {
    if (v == null) return 'text-slate-500';
    return v >= 0 ? 'text-emerald-400' : 'text-red-400';
  }
  function _score(v) { return v != null ? v.toFixed(0) : '—'; }
  function _set(arr) { return new Set(arr || []); }

  // ── 1. Regime overlay strip ───────────────────────────────────────────────
  function _regimeStrip(regime) {
    if (!regime) return '';
    const favored  = _set(regime.favored_themes);
    const avoided  = _set(regime.avoided_themes);
    const emerging = _set(regime.emerging_themes);
    const fading   = _set(regime.fading_themes);

    function pill(name, cls, icon) {
      return `<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${cls}">
        ${icon} ${name}
      </span>`;
    }

    const favItems  = [...favored ].map(n => pill(n, 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50', '✓'));
    const avoidItems= [...avoided ].map(n => pill(n, 'bg-red-900/50 text-red-300 border border-red-700/50', '✗'));
    const emgItems  = [...emerging].filter(n => !favored.has(n)).map(n => pill(n, 'bg-cyan-900/50 text-cyan-300 border border-cyan-700/50', '↑'));
    const fadItems  = [...fading  ].filter(n => !avoided.has(n)).map(n => pill(n, 'bg-amber-900/40 text-amber-300 border border-amber-700/40', '↓'));

    const allPills = [...favItems, ...avoidItems, ...emgItems, ...fadItems];
    if (!allPills.length) return '';

    return `
      <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4 mb-5">
        <div class="flex items-center gap-2 mb-3">
          <span class="text-sm font-semibold text-slate-200">📊 현재 레짐 테마 선호</span>
          <span class="text-xs text-slate-500 font-mono">${regime.regime || ''}</span>
        </div>
        <div class="flex flex-wrap gap-2">${allPills.join('')}</div>
        <div class="flex gap-4 mt-3 text-xs text-slate-500">
          <span><span class="text-emerald-400">✓</span> 선호</span>
          <span><span class="text-red-400">✗</span> 회피</span>
          <span><span class="text-cyan-400">↑</span> 부상중</span>
          <span><span class="text-amber-400">↓</span> 약화중</span>
        </div>
      </div>`;
  }

  // ── 2. Phase lifecycle board ──────────────────────────────────────────────
  function _phaseBoard(themes, regime) {
    const favored  = _set(regime?.favored_themes);
    const avoided  = _set(regime?.avoided_themes);
    const emerging = _set(regime?.emerging_themes);

    // Group by phase
    const byPhase = {};
    for (const t of themes) {
      const key = t.phase || 'neutral';
      byPhase[key] = byPhase[key] || [];
      byPhase[key].push(t);
    }
    // Sort each group by leadership_score desc
    for (const k of Object.keys(byPhase)) {
      byPhase[k].sort((a, b) => (b.leadership_score ?? 0) - (a.leadership_score ?? 0));
    }

    function themeCard(t) {
      const ph = PHASE_MAP[t.phase] || PHASE_MAP.neutral;
      let borderOverride = '';
      if (favored.has(t.name))  borderOverride = 'ring-1 ring-emerald-500/60';
      if (avoided.has(t.name))  borderOverride = 'ring-1 ring-red-500/60';
      if (emerging.has(t.name)) borderOverride = 'ring-1 ring-cyan-500/60';

      const regimeIcon = favored.has(t.name) ? ' <span class="text-emerald-400 text-[10px]">✓레짐</span>'
        : avoided.has(t.name) ? ' <span class="text-red-400 text-[10px]">✗레짐</span>'
        : emerging.has(t.name) ? ' <span class="text-cyan-400 text-[10px]">↑부상</span>' : '';

      return `
        <div class="bg-slate-900/60 border border-slate-700/60 ${borderOverride} rounded-lg p-3">
          <div class="flex items-start justify-between gap-1 mb-2">
            <span class="text-xs font-medium text-slate-200 leading-tight">${t.name}${regimeIcon}</span>
            <span class="text-xs font-bold tabular-nums flex-shrink-0 ${t.leadership_score >= 80 ? 'text-emerald-400' : t.leadership_score >= 50 ? 'text-slate-300' : 'text-red-400'}">
              ${_score(t.leadership_score)}
            </span>
          </div>
          <div class="flex gap-3 text-[11px] tabular-nums">
            <span class="${_pctClass(t.ret1m)}">1M ${_pct(t.ret1m)}</span>
            <span class="${_pctClass(t.ret3m)}">3M ${_pct(t.ret3m)}</span>
          </div>
          <div class="flex gap-3 text-[11px] tabular-nums mt-0.5">
            <span class="text-slate-500">Trend ${_score(t.trend_score)}</span>
            <span class="text-slate-500">RS ${t.rel_strength != null ? t.rel_strength.toFixed(2) : '—'}</span>
          </div>
        </div>`;
    }

    // Show only non-empty phases in order
    const cols = PHASES.filter(p => byPhase[p.key]?.length > 0);
    if (!cols.length) return '';

    return `
      <div class="mb-5">
        ${UI.sectionHeader('테마 라이프사이클 보드')}
        <div class="grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))">
          ${cols.map(ph => `
            <div class="${ph.bg} border ${ph.border} rounded-xl overflow-hidden">
              <div class="px-3 py-2 border-b ${ph.border} flex items-center gap-2">
                <span class="w-2 h-2 rounded-full flex-shrink-0" style="background:${ph.dot}"></span>
                <span class="text-xs font-bold text-slate-200">${ph.label}</span>
                <span class="ml-auto text-xs text-slate-500">${byPhase[ph.key].length}</span>
              </div>
              <div class="p-2 space-y-2">
                ${byPhase[ph.key].map(t => themeCard(t)).join('')}
              </div>
              <div class="px-3 pb-2 text-[10px] text-slate-600">${ph.desc}</div>
            </div>`).join('')}
        </div>
      </div>`;
  }

  // ── 3. Leadership ranking table ──────────────────────────────────────────
  function _rankingTable(themes, regime) {
    const favored = _set(regime?.favored_themes);
    const avoided = _set(regime?.avoided_themes);

    const sorted = [...themes].sort((a, b) => (b.leadership_score ?? 0) - (a.leadership_score ?? 0));

    function scoreBar(v, max = 100) {
      const pct = Math.max(0, Math.min(100, (v ?? 0) / max * 100));
      const color = pct >= 75 ? '#10b981' : pct >= 50 ? '#3b82f6' : pct >= 30 ? '#f59e0b' : '#ef4444';
      return `<div class="flex items-center gap-1.5">
        <div class="w-16 bg-slate-700 rounded-full h-1.5 flex-shrink-0">
          <div class="h-1.5 rounded-full" style="width:${pct}%;background:${color}"></div>
        </div>
        <span class="text-xs tabular-nums text-slate-400">${_score(v)}</span>
      </div>`;
    }

    return `
      <div class="mb-5">
        ${UI.sectionHeader('테마 리더십 랭킹')}
        <div class="bg-slate-800/60 border border-slate-700 rounded-xl overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="border-b border-slate-700/60 text-slate-500 text-left">
                  <th class="px-3 py-2.5 font-medium">#</th>
                  <th class="px-3 py-2.5 font-medium">테마</th>
                  <th class="px-3 py-2.5 font-medium">Phase</th>
                  <th class="px-3 py-2.5 font-medium text-right">1M</th>
                  <th class="px-3 py-2.5 font-medium text-right">3M</th>
                  <th class="px-3 py-2.5 font-medium text-right">6M</th>
                  <th class="px-3 py-2.5 font-medium">리더십</th>
                  <th class="px-3 py-2.5 font-medium">상대강도</th>
                  <th class="px-3 py-2.5 font-medium text-center">레짐</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-700/40">
                ${sorted.map((t, i) => {
                  const ph = PHASE_MAP[t.phase] || PHASE_MAP.neutral;
                  const regimeCls = favored.has(t.name) ? 'text-emerald-400' : avoided.has(t.name) ? 'text-red-400' : 'text-slate-600';
                  const regimeIcon = favored.has(t.name) ? '✓' : avoided.has(t.name) ? '✗' : '—';
                  return `
                    <tr class="hover:bg-slate-700/20 transition-colors">
                      <td class="px-3 py-2 text-slate-500 font-mono">${i + 1}</td>
                      <td class="px-3 py-2 font-medium text-slate-200">${t.name}</td>
                      <td class="px-3 py-2">
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold ${ph.badge}">${t.phase || '—'}</span>
                      </td>
                      <td class="px-3 py-2 text-right tabular-nums ${_pctClass(t.ret1m)}">${_pct(t.ret1m)}</td>
                      <td class="px-3 py-2 text-right tabular-nums ${_pctClass(t.ret3m)}">${_pct(t.ret3m)}</td>
                      <td class="px-3 py-2 text-right tabular-nums ${_pctClass(t.ret6m)}">${_pct(t.ret6m)}</td>
                      <td class="px-3 py-2">${scoreBar(t.leadership_score)}</td>
                      <td class="px-3 py-2">${scoreBar(t.rel_strength != null ? t.rel_strength * 100 : null)}</td>
                      <td class="px-3 py-2 text-center font-bold ${regimeCls}">${regimeIcon}</td>
                    </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
          <div class="px-3 py-2 border-t border-slate-700/40 text-[10px] text-slate-600">
            리더십 점수 = 추세(50%) + 브레드스(30%) + 상대강도(20%) 가중합. 레짐: ✓선호 / ✗회피
          </div>
        </div>
      </div>`;
  }

  // ── Main render ──────────────────────────────────────────────────────────
  function _render(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const themes = (_themeData?.themes || []).filter(t => t.type === 'theme');
    if (!themes.length) {
      el.innerHTML = `<div class="text-slate-500 text-sm text-center py-6">테마 데이터 없음</div>`;
      return;
    }

    const snapshotDate = _themeData?.snapshot_date || '';

    el.innerHTML = `
      <div class="mt-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base font-semibold text-slate-200">🎯 테마 디스커버리</h2>
          ${snapshotDate ? `<span class="text-xs text-slate-500 font-mono">${snapshotDate}</span>` : ''}
        </div>
        ${_regimeStrip(_regimeData)}
        ${_phaseBoard(themes, _regimeData)}
        ${_rankingTable(themes, _regimeData)}
      </div>`;
  }

  // ── Public API ────────────────────────────────────────────────────────────
  async function init(containerId = 'theme-discovery-root') {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `<div class="text-slate-500 text-sm text-center py-4 animate-pulse">테마 데이터 로딩 중…</div>`;
    try {
      await _loadData();
      _render(containerId);
    } catch (err) {
      el.innerHTML = `<div class="text-red-400 text-sm px-4 py-3 bg-red-900/20 rounded-xl">
        테마 데이터 로드 실패: ${err.message}
      </div>`;
    }
  }

  /**
   * Get a summary of themes in a given phase (for external modules).
   * @param {string} phase  — 'emerging' | 'leading' | 'fading' | 'breakdown' | 'neutral'
   * @returns {Array}
   */
  function getThemesByPhase(phase) {
    return (_themeData?.themes || [])
      .filter(t => t.type === 'theme' && t.phase === phase)
      .sort((a, b) => (b.leadership_score ?? 0) - (a.leadership_score ?? 0));
  }

  /**
   * Return { favored, avoided, emerging, fading } sets from the loaded regime.
   */
  function getRegimeThemeSets() {
    if (!_regimeData) return { favored: new Set(), avoided: new Set(), emerging: new Set(), fading: new Set() };
    return {
      favored:  _set(_regimeData.favored_themes),
      avoided:  _set(_regimeData.avoided_themes),
      emerging: _set(_regimeData.emerging_themes),
      fading:   _set(_regimeData.fading_themes),
    };
  }

  return { init, getThemesByPhase, getRegimeThemeSets };
})();
