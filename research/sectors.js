// research/sectors.js — Sector rotation analysis using SPDR ETFs
// 11 sector ETFs vs SPY, heatmap, relative performance, leaders/laggards

const SECTORS = (() => {
  const CONTAINER = 'tab-sectors';

  // Sector ETF → Korean label + color
  const SECTOR_META = {
    XLK:  { name: '기술 (Technology)',              color: '#3b82f6' },
    XLV:  { name: '헬스케어 (Healthcare)',           color: '#10b981' },
    XLF:  { name: '금융 (Financials)',               color: '#f59e0b' },
    XLE:  { name: '에너지 (Energy)',                  color: '#ef4444' },
    XLI:  { name: '산업재 (Industrials)',             color: '#8b5cf6' },
    XLY:  { name: '임의소비재 (Cons. Discretionary)', color: '#ec4899' },
    XLB:  { name: '소재 (Materials)',                 color: '#84cc16' },
    XLC:  { name: '커뮤니케이션 (Communication)',     color: '#06b6d4' },
    XLRE: { name: '부동산 (Real Estate)',             color: '#f97316' },
    XLU:  { name: '유틸리티 (Utilities)',             color: '#a78bfa' },
    XLP:  { name: '필수소비재 (Cons. Staples)',       color: '#34d399' },
  };

  // Economic cycle sector rotation model
  const CYCLE_GUIDE = [
    { phase: '초기 확장 (Early Expansion)', leaders: ['XLY', 'XLF', 'XLI'],  laggards: ['XLU', 'XLP'] },
    { phase: '중기 확장 (Mid Expansion)',   leaders: ['XLK', 'XLC', 'XLI'],   laggards: ['XLU', 'XLRE'] },
    { phase: '후기 확장 (Late Expansion)',  leaders: ['XLE', 'XLB', 'XLF'],   laggards: ['XLY', 'XLK'] },
    { phase: '경기 수축 (Contraction)',     leaders: ['XLP', 'XLU', 'XLV'],   laggards: ['XLY', 'XLF', 'XLE'] },
  ];

  // Ticker → sector mapping
  const TICKER_SECTOR = {
    AAPL:'XLK', MSFT:'XLK', NVDA:'XLK', GOOGL:'XLC', META:'XLC', AMZN:'XLY',
    TSLA:'XLY', NFLX:'XLC', TECL:'XLK', QQQ:'XLK', SPY:null,
    LLY:'XLV', JNJ:'XLV', UNH:'XLV', ISRG:'XLV', ABBV:'XLV',
    JPM:'XLF', BAC:'XLF', GS:'XLF', BRK:'XLF',
    XOM:'XLE', CVX:'XLE',
    CAT:'XLI', DE:'XLI',
    HOOD:'XLF', RKLB:'XLI', CRCL:'XLF', QQQM:null,
    // Single-stock 2× leveraged ETFs — same sector as underlying
    METU:'XLC', GGLL:'XLC', AMZZ:'XLY',
  };

  // Short Korean name for each broad sector ETF (used in cycle guide badges)
  const SECTOR_SHORT = {
    XLK:'기술', XLV:'헬스케어', XLF:'금융', XLE:'에너지', XLI:'산업재',
    XLY:'임의소비재', XLB:'소재', XLC:'커뮤니케이션', XLRE:'부동산', XLU:'유틸리티', XLP:'필수소비재',
  };

  // Sub-sector groups: parent broad sector + thematic ETFs
  const SUB_SECTOR_GROUPS = [
    { parent:'XLK', label:'기술 세부',         color:'#3b82f6',
      etfs:[{ sym:'SOXX',name:'반도체' },{ sym:'CIBR',name:'사이버보안' },{ sym:'SKYY',name:'클라우드' },{ sym:'BOTZ',name:'AI·로봇자동화' }] },
    { parent:'XLV', label:'헬스케어 세부',     color:'#10b981',
      etfs:[{ sym:'IBB',name:'바이오테크' },{ sym:'IHI',name:'의료기기' },{ sym:'PJP',name:'빅파마' }] },
    { parent:'XLF', label:'금융 세부',         color:'#f59e0b',
      etfs:[{ sym:'KBE',name:'은행' },{ sym:'FINX',name:'핀테크' },{ sym:'BKCH',name:'암호화폐·블록체인' }] },
    { parent:'XLI', label:'산업재·우주·방산', color:'#8b5cf6',
      etfs:[{ sym:'ITA',name:'항공우주·방산' },{ sym:'UFO',name:'우주' },{ sym:'ROBO',name:'로봇·자동화' }] },
    { parent:'XLE', label:'에너지 세부',       color:'#ef4444',
      etfs:[{ sym:'ICLN',name:'클린에너지' },{ sym:'FCG',name:'천연가스' }] },
    { parent:'XLY', label:'소비재 세부',       color:'#ec4899',
      etfs:[{ sym:'XRT',name:'리테일' }] },
  ];

  // Single-stock leveraged ETFs: ticker → { underlying, mult }
  const LEVERAGE_SINGLE = {
    METU: { underlying: 'META',  mult: 2 },
    GGLL: { underlying: 'GOOGL', mult: 2 },
    AMZZ: { underlying: 'AMZN',  mult: 2 },
  };

  async function init() {
    if (!RESEARCH_CONFIG.FINNHUB_KEY) {
      UI.noKey(CONTAINER, 'Finnhub', 'FINNHUB_KEY');
      return;
    }
    UI.loading(CONTAINER, '섹터 성과 데이터 로딩 중...');
    try {
      await _render();
    } catch (e) {
      UI.error(CONTAINER, '섹터 데이터 로딩 실패: ' + e.message);
    }
  }

  async function _render() {
    const [sectorData, subData] = await Promise.all([
      API.sectorPerformance(),
      API.subSectorPerformance().catch(() => ({})),
    ]);
    if (!sectorData || !Object.keys(sectorData).length) {
      UI.error(CONTAINER, '섹터 ETF 데이터 없음');
      return;
    }

    // ── Save standardized sector perf cache for optimizer / candidate / market-regime ──
    // Keys: lowercase (ret1m/ret3m/ret6m/retYtd/ret1y), values: percent (3.2 = +3.2%)
    try {
      const cacheEntry = {};
      Object.entries(sectorData).forEach(([sym, d]) => {
        if (!d) return;
        cacheEntry[sym] = {
          ret1m:  d.ret1m  ?? null,
          ret3m:  d.ret3m  ?? null,
          ret6m:  d.ret6m  ?? null,
          retYtd: d.retYtd ?? null,
          ret1y:  d.ret1y  ?? null,
        };
      });
      localStorage.setItem('wr_sector_perf_cache', JSON.stringify(cacheEntry));
    } catch (_) {}

    const spy   = sectorData['SPY'];
    const etfs  = Object.keys(SECTOR_META);
    const rows  = etfs.map(sym => {
      const d = sectorData[sym];
      if (!d) return null;
      return {
        sym,
        name: SECTOR_META[sym].name,
        color: SECTOR_META[sym].color,
        values: {
          '1M':  d.ret1m,
          '3M':  d.ret3m,
          '6M':  d.ret6m,
          'YTD': d.retYtd,
          '1Y':  d.ret1y,
        },
        // Relative to SPY
        relValues: {
          '1M':  d.ret1m  != null && spy?.ret1m  != null ? d.ret1m  - spy.ret1m  : null,
          '3M':  d.ret3m  != null && spy?.ret3m  != null ? d.ret3m  - spy.ret3m  : null,
          '6M':  d.ret6m  != null && spy?.ret6m  != null ? d.ret6m  - spy.ret6m  : null,
          'YTD': d.retYtd != null && spy?.retYtd != null ? d.retYtd - spy.retYtd : null,
          '1Y':  d.ret1y  != null && spy?.ret1y  != null ? d.ret1y  - spy.ret1y  : null,
        },
        closes: d.closes,
        dates:  d.dates,
      };
    }).filter(Boolean);

    // Find leaders/laggards (by 3M or YTD)
    const sorted = [...rows].filter(r => r.values['3M'] != null).sort((a, b) => b.values['3M'] - a.values['3M']);
    const leaders  = sorted.slice(0, 3);
    const laggards = sorted.slice(-3).reverse();

    const el = document.getElementById(CONTAINER);
    el.innerHTML = `
      ${_summaryRow(spy, rows)}
      ${_heatmapSection(rows)}
      ${_leadersLaggards(leaders, laggards)}
      ${_relativePerformanceSection()}
      ${_subSectorSection(subData, spy)}
      ${_cycleGuideSection()}
      <div id="theme-discovery-root"></div>`;

    // Render charts
    CHARTS.sectorHeatmap('sector-heatmap', rows.map(r => ({ label: `${r.name.split(' ')[0]} (${r.sym})`, values: r.relValues })));
    _renderRelativeBar(rows, spy);
    _renderSubSectorHeatmap(subData);

    // Theme lifecycle board (Phase 10)
    if (typeof THEME_DISCOVERY !== 'undefined') {
      THEME_DISCOVERY.init('theme-discovery-root');
    }
  }

  // ── Summary row ───────────────────────────────────────────────────────────
  function _summaryRow(spy, rows) {
    const { fmt } = UI;
    const ytds = rows.map(r => r.values['YTD']).filter(v => v != null);
    const best  = Math.max(...ytds);
    const worst = Math.min(...ytds);
    return `
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        ${UI.kpi('S&P 500 (YTD)', spy?.retYtd != null ? fmt.pct(spy.retYtd) : '—', { color: spy?.retYtd > 0 ? 'green' : 'red' })}
        ${UI.kpi('최고 섹터 (YTD)', rows.find(r => r.values['YTD'] === best)?.name.split(' ')[0] || '—', { color: 'green', sub: (rows.find(r => r.values['YTD'] === best)?.sym || '') + ' · ' + fmt.pct(best) })}
        ${UI.kpi('최저 섹터 (YTD)', rows.find(r => r.values['YTD'] === worst)?.name.split(' ')[0] || '—', { color: 'red', sub: (rows.find(r => r.values['YTD'] === worst)?.sym || '') + ' · ' + fmt.pct(worst) })}
        ${UI.kpi('분석 섹터 수', rows.length + '개')}
      </div>`;
  }

  // ── Heatmap ───────────────────────────────────────────────────────────────
  function _heatmapSection(rows) {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('섹터 로테이션 히트맵', 'SPY 대비 상대 성과 · 파란=시장 상회, 빨간=시장 하회')}
        <div class="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
          <div id="sector-heatmap"></div>
        </div>
      </div>`;
  }

  // ── Leaders / Laggards ────────────────────────────────────────────────────
  function _leadersLaggards(leaders, laggards) {
    const { fmt } = UI;
    return `
      <div class="mb-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div class="bg-emerald-950/20 border border-emerald-800/40 rounded-xl p-4">
          ${UI.sectionHeader('🏆 강세 섹터 (3개월 기준)')}
          ${leaders.map((r, i) => `
            <div class="flex justify-between items-center py-2 ${i < leaders.length-1 ? 'border-b border-slate-700/50' : ''}">
              <div>
                <span class="text-white font-semibold text-sm">${r.name.split(' ')[0]}</span>
                <span class="text-slate-500 text-xs ml-2 font-mono">${r.sym}</span>
              </div>
              <span class="text-emerald-400 font-semibold">${fmt.pct(r.values['3M'])}</span>
            </div>`).join('')}
        </div>
        <div class="bg-red-950/20 border border-red-800/40 rounded-xl p-4">
          ${UI.sectionHeader('⚠️ 약세 섹터 (3개월 기준)')}
          ${laggards.map((r, i) => `
            <div class="flex justify-between items-center py-2 ${i < laggards.length-1 ? 'border-b border-slate-700/50' : ''}">
              <div>
                <span class="text-white font-semibold text-sm">${r.name.split(' ')[0]}</span>
                <span class="text-slate-500 text-xs ml-2 font-mono">${r.sym}</span>
              </div>
              <span class="text-red-400 font-semibold">${fmt.pct(r.values['3M'])}</span>
            </div>`).join('')}
        </div>
      </div>`;
  }

  // ── Relative Performance Bar Chart ────────────────────────────────────────
  function _relativePerformanceSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('SPY 대비 상대 성과 (YTD)', '양수 = 시장 초과, 음수 = 시장 미달')}
        <div class="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
          <div style="height:220px"><canvas id="chart-sector-rel"></canvas></div>
        </div>
      </div>`;
  }

  function _renderRelativeBar(rows, spy) {
    const relYtd = rows.map(r => r.values['YTD'] != null && spy?.retYtd != null
      ? r.values['YTD'] - spy.retYtd : null);
    const labels = rows.map(r => r.name.split(' ')[0]);
    const colors = relYtd.map(v => v == null ? '#64748b' : v >= 0 ? '#10b98199' : '#ef444499');
    CHARTS.bar('chart-sector-rel', labels, [
      { label: 'YTD 상대 성과 (%)', data: relYtd, colors },
    ], {
      scales: {
        y: { ticks: { callback: v => v?.toFixed(1) + '%' } },
      },
    });
  }

  // Broad-index 3× leveraged ETFs
  const LEVERAGE_3X = ['TECL', 'TQQQ', 'SOXL', 'SPXL', 'UPRO'];

  // ── My Portfolio Exposure ─────────────────────────────────────────────────
  function _calcMyExposure(positions) {
    const exposure = {};
    const leveragedFound = [];  // { ticker, mult }
    // pairs: underlying → { nomValue, leveraged: [{ticker, nomValue, effValue, mult}] }
    const pairs = {};
    // Pre-populate pairs for any underlying that has a LEVERAGE_SINGLE entry
    Object.values(LEVERAGE_SINGLE).forEach(({ underlying }) => {
      pairs[underlying] = pairs[underlying] || { nomValue: 0, leveraged: [] };
    });
    let totalValue = 0;

    positions.forEach(pos => {
      const ticker = (pos.ticker || '').toUpperCase();
      const nomValue = (parseFloat(pos.shares) || 0) * (parseFloat(pos.currentPrice) || 0);
      if (!nomValue) return;

      // Single-stock 2× leveraged (METU, GGLL, AMZZ)
      if (LEVERAGE_SINGLE[ticker]) {
        const { underlying, mult } = LEVERAGE_SINGLE[ticker];
        const effValue = nomValue * mult;
        leveragedFound.push({ ticker, mult });
        const sector = TICKER_SECTOR[ticker];
        if (sector) { exposure[sector] = (exposure[sector] || 0) + effValue; totalValue += effValue; }
        if (!pairs[underlying]) pairs[underlying] = { nomValue: 0, leveraged: [] };
        pairs[underlying].leveraged.push({ ticker, nomValue, effValue, mult });
        return;
      }

      if (LEVERAGE_3X.includes(ticker)) leveragedFound.push({ ticker, mult: 3 });

      const knownSector = Object.prototype.hasOwnProperty.call(TICKER_SECTOR, ticker)
        ? TICKER_SECTOR[ticker] : 'UNKNOWN';
      // null = multi-sector ETF (e.g. QQQM) — skip single-sector attribution
      if (knownSector === null) { totalValue += nomValue; return; }
      exposure[knownSector] = (exposure[knownSector] || 0) + nomValue;
      totalValue += nomValue;

      // Track underlying value for pairing display
      if (pairs[ticker] !== undefined) pairs[ticker].nomValue += nomValue;
    });

    // Remove empty pairs (neither held)
    Object.keys(pairs).forEach(k => {
      if (!pairs[k].nomValue && !pairs[k].leveraged.length) delete pairs[k];
    });

    return { exposure, totalValue, leveragedFound, pairs };
  }

  function _myExposureSection({ exposure, totalValue, leveragedFound, pairs }) {
    if (!totalValue || !Object.keys(exposure).length) return '';
    const { fmt } = UI;
    const entries = Object.entries(exposure).sort((a, b) => b[1] - a[1]);

    const lev3x = leveragedFound.filter(l => l.mult === 3).map(l => l.ticker);
    const lev2x = leveragedFound.filter(l => l.mult === 2).map(l => l.ticker);
    const levWarnParts = [];
    if (lev3x.length) levWarnParts.push(`<strong>${lev3x.join(', ')}</strong>는 3× 레버리지 ETF — 명목 금액 대비 실제 노출도 3배`);
    if (lev2x.length) levWarnParts.push(`<strong>${lev2x.join(', ')}</strong>는 2× 단일종목 레버리지 ETF — 섹터 노출도 계산 시 유효 노출도(2×) 적용됨`);
    const levWarn = levWarnParts.length
      ? `<div class="bg-amber-950/20 border border-amber-700/40 rounded-lg px-3 py-2 text-xs text-amber-300 mb-3">
           ⚠️ ${levWarnParts.join('<br>')}
         </div>` : '';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('내 포트폴리오 섹터 노출도', '보유 종목 기반 추정 · 2× ETF는 유효 노출도 기준')}
        ${levWarn}
        <div class="space-y-2">
          ${entries.map(([sym, val]) => {
            const pct = (val / totalValue * 100);
            const meta = SECTOR_META[sym] || { name: sym === 'UNKNOWN' ? '미분류 (Unknown)' : sym, color: '#64748b' };
            return `
              <div class="flex items-center gap-3">
                <div class="w-24 flex-shrink-0">
                  <span class="text-xs text-slate-200">${meta.name.split('(')[0].trim()}</span>
                  <span class="text-[10px] font-mono text-slate-500 ml-1">${sym}</span>
                </div>
                <div class="flex-1 bg-slate-700 rounded-full h-4 relative overflow-hidden">
                  <div class="h-4 rounded-full transition-all"
                    style="width:${pct.toFixed(0)}%;background:${meta.color}80"></div>
                </div>
                <span class="text-sm text-slate-300 w-12 text-right">${pct.toFixed(1)}%</span>
              </div>`;
          }).join('')}
        </div>
        <p class="text-xs text-slate-600 mt-2">* 종목-섹터 매핑은 추정값입니다. QQQM/QQQ 같은 멀티섹터 ETF는 제외됩니다. 미분류 종목은 Unknown으로 표시됩니다.</p>
      </div>
      ${_leveragePairsSection(pairs, totalValue)}`;
  }

  // ── Leverage Pairing Section ──────────────────────────────────────────────
  function _leveragePairsSection(pairs, totalValue) {
    if (!pairs || !totalValue) return '';
    const { fmt } = UI;
    const activePairs = Object.entries(pairs).filter(([, v]) => v.leveraged.length > 0);
    if (!activePairs.length) return '';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('레버리지 페어링', '기초 종목 + 레버리지 ETF 유효 노출도 합산')}
        <div class="space-y-3">
          ${activePairs.map(([underlying, { nomValue, leveraged }]) => {
            const levEffTotal = leveraged.reduce((s, l) => s + l.effValue, 0);
            const combinedEff = nomValue + levEffTotal;
            const combinedPct = totalValue > 0 ? combinedEff / totalValue * 100 : 0;
            const underlyingSector = TICKER_SECTOR[underlying];
            const sectorColor = underlyingSector ? (SECTOR_META[underlyingSector]?.color || '#64748b') : '#64748b';

            return `
              <div class="bg-slate-800/60 border border-slate-700/60 rounded-xl p-4">
                <div class="flex justify-between items-center mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-bold text-white">${underlying}</span>
                    <span class="text-xs text-slate-500">그룹</span>
                    ${leveraged.map(l => `<span class="bg-amber-900/50 border border-amber-700/50 text-amber-300 text-xs px-2 py-0.5 rounded font-mono">${l.ticker} ${l.mult}×</span>`).join('')}
                  </div>
                  <div class="text-right">
                    <span class="text-xs text-amber-300 font-semibold">${combinedPct.toFixed(1)}%</span>
                    <span class="text-xs text-slate-500 ml-1">유효 노출도</span>
                  </div>
                </div>

                ${nomValue > 0 ? `
                <div class="flex items-center gap-3 mb-2">
                  <span class="text-xs font-mono text-slate-300 w-16">${underlying}</span>
                  <div class="flex-1 bg-slate-700 rounded h-2.5">
                    <div class="h-2.5 rounded" style="width:${Math.min(nomValue / combinedEff * 100, 100).toFixed(0)}%;background:${sectorColor}99"></div>
                  </div>
                  <span class="text-xs text-slate-300 w-20 text-right">${fmt.dollar(nomValue)}</span>
                  <span class="text-xs text-slate-500 w-14 text-right">1× 직접</span>
                </div>` : ''}

                ${leveraged.map(({ ticker, nomValue: nv, effValue, mult }) => `
                <div class="flex items-center gap-3 mb-2">
                  <span class="text-xs font-mono text-amber-300 w-16">${ticker}</span>
                  <div class="flex-1 bg-slate-700 rounded h-2.5">
                    <div class="h-2.5 rounded bg-amber-500/70" style="width:${Math.min(effValue / combinedEff * 100, 100).toFixed(0)}%"></div>
                  </div>
                  <span class="text-xs text-slate-400 w-20 text-right">${fmt.dollar(nv)}</span>
                  <span class="text-xs text-amber-400 w-14 text-right">${mult}× → ${fmt.dollar(effValue)}</span>
                </div>`).join('')}

                <div class="mt-2 pt-2 border-t border-slate-700/50 flex justify-between text-xs">
                  <span class="text-slate-500">합산 ${underlying} 유효 시장 노출도</span>
                  <span class="text-amber-200 font-semibold">${fmt.dollar(combinedEff)}</span>
                </div>
              </div>`;
          }).join('')}
        </div>
        <p class="text-xs text-slate-600 mt-2">* 유효 노출도 = 명목 금액 × 레버리지 배수. 섹터 노출도 차트에도 유효 노출도 기준으로 반영됩니다.</p>
      </div>`;
  }

  // ── Thematic Sub-sector Section ──────────────────────────────────────────
  function _subSectorSection(subData, spy) {
    if (!subData || !Object.keys(subData).length) return '';
    const { fmt } = UI;

    // Collect all sub-sectors with data
    const all = [];
    SUB_SECTOR_GROUPS.forEach(grp => {
      grp.etfs.forEach(e => {
        const d = subData[e.sym];
        if (!d) return;
        all.push({ sym: e.sym, name: e.name, parent: grp.parent, color: grp.color,
          ret1m: d.ret1m, ret3m: d.ret3m, ret6m: d.ret6m, retYtd: d.retYtd, ret1y: d.ret1y });
      });
    });
    if (!all.length) return '';

    const withYtd = all.filter(r => r.retYtd != null).sort((a, b) => b.retYtd - a.retYtd);
    const subLeaders  = withYtd.slice(0, 5);
    const subLaggards = withYtd.slice(-5).reverse();

    return `
      <div class="mb-6">
        ${UI.sectionHeader('테마 세부 섹터', '반도체·사이버보안·바이오·핀테크·우주 등 기간별 수익률')}

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <div class="bg-emerald-950/20 border border-emerald-800/40 rounded-xl p-4">
            ${UI.sectionHeader('🏆 강세 테마 (YTD)', '')}
            ${subLeaders.map((r, i) => `
              <div class="flex justify-between items-center py-1.5 ${i < subLeaders.length - 1 ? 'border-b border-slate-700/50' : ''}">
                <div class="flex items-center gap-2">
                  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:${r.color}"></span>
                  <span class="text-xs font-semibold text-white">${r.name}</span>
                  <span class="text-[10px] font-mono text-slate-500">${r.sym}</span>
                </div>
                <div class="text-right">
                  <span class="text-emerald-400 font-semibold text-sm">${r.retYtd >= 0 ? '+' : ''}${r.retYtd.toFixed(1)}%</span>
                  ${r.ret3m != null ? `<span class="text-xs text-slate-500 ml-2">3M ${r.ret3m >= 0 ? '+' : ''}${r.ret3m.toFixed(1)}%</span>` : ''}
                </div>
              </div>`).join('')}
          </div>
          <div class="bg-red-950/20 border border-red-800/40 rounded-xl p-4">
            ${UI.sectionHeader('⚠️ 약세 테마 (YTD)', '')}
            ${subLaggards.map((r, i) => `
              <div class="flex justify-between items-center py-1.5 ${i < subLaggards.length - 1 ? 'border-b border-slate-700/50' : ''}">
                <div class="flex items-center gap-2">
                  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:${r.color}"></span>
                  <span class="text-xs font-semibold text-white">${r.name}</span>
                  <span class="text-[10px] font-mono text-slate-500">${r.sym}</span>
                </div>
                <div class="text-right">
                  <span class="text-red-400 font-semibold text-sm">${r.retYtd >= 0 ? '+' : ''}${r.retYtd.toFixed(1)}%</span>
                  ${r.ret3m != null ? `<span class="text-xs text-slate-500 ml-2">3M ${r.ret3m >= 0 ? '+' : ''}${r.ret3m.toFixed(1)}%</span>` : ''}
                </div>
              </div>`).join('')}
          </div>
        </div>

        <div class="space-y-3">
          ${SUB_SECTOR_GROUPS.map(grp => {
            const grpEtfs = grp.etfs.filter(e => subData[e.sym]);
            if (!grpEtfs.length) return '';
            return `
              <div class="bg-slate-800/50 border border-slate-700 rounded-xl overflow-hidden">
                <div class="flex items-center gap-2 px-4 py-2.5 border-b border-slate-700/60"
                  style="background:${grp.color}14">
                  <span class="w-2 h-2 rounded-full flex-shrink-0" style="background:${grp.color}"></span>
                  <span class="text-sm font-semibold text-slate-200">${grp.label}</span>
                  <span class="text-xs font-mono text-slate-600">${grp.parent}</span>
                </div>
                <div id="sub-heatmap-${grp.parent}"></div>
              </div>`;
          }).join('')}
        </div>
        <p class="text-xs text-slate-600 mt-2">* 절대 수익률 기준. 초록=상승, 빨강=하락. 동일 기간 SPY 대비 상대 강도는 위 메인 히트맵 참고.</p>
      </div>`;
  }

  function _renderSubSectorHeatmap(subData) {
    if (!subData) return;
    SUB_SECTOR_GROUPS.forEach(grp => {
      const rows = grp.etfs
        .map(e => {
          const d = subData[e.sym];
          if (!d) return null;
          return {
            label: `${e.name} (${e.sym})`,
            values: { '1M': d.ret1m, '3M': d.ret3m, '6M': d.ret6m, 'YTD': d.retYtd, '1Y': d.ret1y },
          };
        })
        .filter(Boolean);
      if (rows.length) CHARTS.sectorHeatmap(`sub-heatmap-${grp.parent}`, rows);
    });
  }

  // ── Economic Cycle Guide ──────────────────────────────────────────────────
  function _cycleGuideSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('경기 사이클 섹터 로테이션 가이드')}
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          ${CYCLE_GUIDE.map(phase => `
            <div class="bg-slate-800/50 border border-slate-700 rounded-xl p-4">
              <div class="text-xs font-semibold text-slate-300 mb-3">${phase.phase}</div>
              <div class="mb-2">
                <div class="text-xs text-emerald-400 mb-1">▲ 강세</div>
                <div class="flex flex-wrap gap-1">
                  ${phase.leaders.map(s => `
                    <span class="bg-emerald-900/50 text-emerald-300 text-xs px-2 py-0.5 rounded">
                      <span>${SECTOR_SHORT[s] || s}</span>
                      <span class="opacity-50 ml-1 font-mono text-[10px]">${s}</span>
                    </span>`).join('')}
                </div>
              </div>
              <div>
                <div class="text-xs text-red-400 mb-1">▼ 약세</div>
                <div class="flex flex-wrap gap-1">
                  ${phase.laggards.map(s => `
                    <span class="bg-red-900/50 text-red-300 text-xs px-2 py-0.5 rounded">
                      <span>${SECTOR_SHORT[s] || s}</span>
                      <span class="opacity-50 ml-1 font-mono text-[10px]">${s}</span>
                    </span>`).join('')}
                </div>
              </div>
            </div>`).join('')}
        </div>
      </div>`;
  }

  return { init };
})();
