// research/gurus.js — Guru portfolio tracker via SEC EDGAR 13F filings
// Tracks famous investors' quarterly 13F-HR filings (Q/Q changes)

const GURUS = (() => {
  const CONTAINER = 'tab-gurus';
  let _selectedGuru = null;
  let _prevHoldings = null; // prior quarter for diff

  async function init() {
    const el = document.getElementById(CONTAINER);
    el.innerHTML = _buildShell();
    _selectedGuru = Object.keys(RESEARCH_CONFIG.GURUS || {})[0] || null;
    if (!_selectedGuru) {
      el.innerHTML = '<div class="py-12 text-center text-slate-500">config.js에 RESEARCH_CONFIG.GURUS를 설정하세요</div>';
      return;
    }
    _wireGuruSelector();
    await _loadGuru(_selectedGuru);
  }

  function _buildShell() {
    const gurus = RESEARCH_CONFIG.GURUS || {};
    const options = Object.keys(gurus).map(name =>
      `<option value="${name}">${name}</option>`
    ).join('');
    return `
      <div class="bg-amber-950/20 border border-amber-800/40 rounded-xl p-3 mb-4 text-xs text-amber-300">
        ⚠️ <strong>13F 공시 주의사항:</strong> 13F는 분기 종료 후 최대 45일 이내 공시됩니다. 가장 최근 데이터도 실제 포지션 기준일로부터 최소 45~135일 이전을 반영합니다. 공시 이후 포지션은 이미 변경되었을 수 있으며, 분기 내 단기 매매는 전혀 반영되지 않습니다. 참고용으로만 활용하고 맹목적으로 따라 하지 마세요.
      </div>
      <div class="flex flex-col sm:flex-row gap-4 items-start sm:items-center mb-6">
        <label class="text-sm text-slate-400">거장 선택:</label>
        <select id="guru-selector"
          class="bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm min-w-48">
          ${options}
        </select>
        <span id="guru-filing-date" class="text-xs text-slate-500"></span>
      </div>
      <div id="guru-content"><div class="text-slate-500 text-sm text-center py-12">거장를 선택하세요</div></div>`;
  }

  function _wireGuruSelector() {
    const sel = document.getElementById('guru-selector');
    if (!sel) return;
    sel.addEventListener('change', async () => {
      _selectedGuru = sel.value;
      await _loadGuru(_selectedGuru);
    });
  }

  async function _loadGuru(guruName) {
    const cik = RESEARCH_CONFIG.GURUS[guruName];
    if (!cik) return;
    const el = document.getElementById('guru-content');
    el.innerHTML = `<div class="flex items-center justify-center py-12 text-slate-400">
      <svg class="animate-spin h-6 w-6 mr-3 text-emerald-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
      </svg>
      SEC EDGAR에서 13F 파싱 중...</div>`;
    try {
      const filing = await API.sec13F(cik);
      if (!filing || !filing.holdings?.length) {
        el.innerHTML = '<div class="py-12 text-center text-slate-500">13F 데이터 없음 — SEC EDGAR에서 파일을 찾을 수 없습니다</div>';
        return;
      }
      document.getElementById('guru-filing-date').textContent = '최신 13F: ' + filing.filedDate;
      _renderGuru(filing, guruName);
    } catch (e) {
      el.innerHTML = `<div class="py-12 text-center text-red-400 text-sm">
        13F 로딩 실패: ${e.message}<br>
        <span class="text-slate-500 text-xs">SEC EDGAR CORS 정책으로 일부 환경에서 제한될 수 있습니다</span>
      </div>`;
    }
  }

  function _renderGuru(filing, guruName) {
    const { fmt } = UI;
    const myPositions = new Set(STATE.getMyPositions().map(p => (p.ticker || '').toUpperCase()));
    const totalValue  = filing.totalValue;
    const holdings    = filing.holdings.slice(0, 50); // top 50

    const el = document.getElementById('guru-content');
    el.innerHTML = `
      ${_summarySection(filing, guruName, totalValue)}
      ${_overlapSection(holdings, myPositions)}
      ${_holdingsTable(holdings, totalValue, myPositions)}`;
  }

  function _summarySection(filing, guruName, totalValue) {
    const { fmt } = UI;
    const h = filing.holdings;
    const top5Pct = h.slice(0, 5).reduce((s, x) => s + x.value, 0) / totalValue * 100;
    return `
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        ${UI.kpi('포트폴리오 총액', fmt.dollar(totalValue), { icon: '💼' })}
        ${UI.kpi('보유 종목 수', h.length + '개')}
        ${UI.kpi('상위 5개 집중도', top5Pct.toFixed(1) + '%', {
          color: top5Pct > 60 ? 'yellow' : null,
          sub: top5Pct > 60 ? '집중형 포트폴리오' : '분산형 포트폴리오',
        })}
        ${UI.kpi('최대 보유 종목', h[0]?.ticker || '—', { sub: h[0] ? (h[0].value / totalValue * 100).toFixed(1) + '%' : '' })}
      </div>`;
  }

  function _overlapSection(holdings, myPositions) {
    if (!myPositions.size) return '';
    const overlap = holdings.filter(h => myPositions.has(h.ticker?.toUpperCase()));
    if (!overlap.length) return `
      <div class="mb-6 bg-slate-800/50 border border-slate-700 rounded-xl p-4 text-sm text-slate-400">
        📊 내 포트폴리오와 겹치는 종목 없음 — 이 거장와 다른 투자 관점
      </div>`;

    return `
      <div class="mb-6 bg-emerald-950/20 border border-emerald-800/40 rounded-xl p-4">
        <div class="text-sm font-medium text-emerald-400 mb-3">
          ✅ 내 포트폴리오 오버랩 (${overlap.length}개 종목)
        </div>
        <div class="flex flex-wrap gap-2">
          ${overlap.map(h => `
            <span class="bg-emerald-900/50 border border-emerald-700 text-emerald-300 text-xs px-3 py-1 rounded-full">
              ${h.ticker} · ${(h.value / (holdings.reduce((s,x)=>s+x.value,0)) * 100).toFixed(1)}%
            </span>`).join('')}
        </div>
        <p class="text-xs text-slate-500 mt-2">이 거장도 보유 중인 종목 — 투자 논리 검증에 활용하세요</p>
      </div>`;
  }

  function _holdingsTable(holdings, totalValue, myPositions) {
    const { fmt } = UI;
    const rows = holdings.map((h, i) => {
      const pct = totalValue > 0 ? (h.value / totalValue * 100).toFixed(2) : '—';
      const isMine = myPositions.has(h.ticker?.toUpperCase());
      const ticker = isMine
        ? `<span class="font-mono text-emerald-300">${h.ticker} ⭐</span>`
        : `<span class="font-mono">${h.ticker || '<span class="text-slate-500 text-xs">미확인</span>'}</span>`;
      const pctBar = `
        <div class="flex items-center gap-2">
          <div class="w-20 bg-slate-700 rounded h-1.5">
            <div class="h-1.5 rounded bg-emerald-500/70" style="width:${Math.min(parseFloat(pct)*3, 100)}%"></div>
          </div>
          <span class="text-slate-300 font-mono text-xs">${pct}%</span>
        </div>`;
      return [
        `<span class="text-slate-400 text-xs">#${i+1}</span>`,
        ticker,
        h.name?.length > 25 ? h.name.slice(0, 25) + '…' : (h.name || '—'),
        fmt.dollar(h.value),
        fmt.shares(h.shares),
        pctBar,
        h.putCall ? `<span class="text-amber-400 text-xs">${h.putCall}</span>` : '',
      ];
    });

    return `
      <div class="mb-6">
        ${UI.sectionHeader('보유 종목 상세', '⭐ = 내 포트폴리오와 겹침')}
        ${UI.table(['순위', '티커', '회사명', '보유금액', '주수', '비중', '유형'], rows)}
      </div>`;
  }

  return { init };
})();
