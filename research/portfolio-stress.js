// research/portfolio-stress.js — Portfolio Stress Test (Phase 9 §9/§30)
//
// Loads latest_stress_test.json (factor exposures + scenario returns) and
// portfolio weights from the local DB (Local Mode) or defaults to equal-
// weight Core candidates (Static Mode).
//
// API:
//   PORTFOLIO_STRESS.renderWidget(containerId)   — standalone stress section
//   PORTFOLIO_STRESS.init(containerId, weights)  — init with explicit weights
//   PORTFOLIO_STRESS.getSummary(scenario)         — {portfolio_return, contributors}

const PORTFOLIO_STRESS = (() => {
  // ── Constants ────────────────────────────────────────────────────────────

  const SCENARIO_ORDER = [
    'hypo_spy_m10',
    'hypo_spy_m20',
    'covid_crash_2020',
    'rate_shock_2022',
    'tech_bubble_2000',
    'gfc_2008',
    'volpocalypse_2018',
    'aug_2024_carry_unwind',
  ];

  const SCENARIO_ICONS = {
    'hypo_spy_m10':           '📉',
    'hypo_spy_m20':           '📉',
    'covid_crash_2020':       '🦠',
    'rate_shock_2022':        '📈',   // rates rising
    'tech_bubble_2000':       '💥',
    'gfc_2008':               '🏦',
    'volpocalypse_2018':      '⚡',
    'aug_2024_carry_unwind':  '🇯🇵',
  };

  // R² threshold below which we show "low model quality" warning
  const LOW_R2_THRESHOLD = 0.30;
  const HIGH_BETA_THRESHOLD = 1.50;

  // ── Module state ─────────────────────────────────────────────────────────
  let _stressData   = null;   // latest_stress_test.json
  let _weights      = null;   // { ticker: weight_fraction }
  let _activeScenario = 'hypo_spy_m10';
  let _container    = null;

  // ── Data loading ──────────────────────────────────────────────────────────

  async function _loadStressData() {
    if (_stressData) return _stressData;
    try {
      const resp = await fetch('data/views/latest_stress_test.json');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      _stressData = await resp.json();
      return _stressData;
    } catch (e) {
      console.warn('[portfolio-stress] Could not load stress data:', e.message);
      return null;
    }
  }

  async function _loadWeightsFromDB() {
    // Try local portfolio API first (Local Mode)
    try {
      const resp = await fetch('/api/local/portfolio', {
        headers: { 'X-Local-Token': window.LOCAL_TOKEN || '' },
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.holdings && data.holdings.length > 0) {
          const totalValue = data.holdings.reduce((s, h) => s + (h.current_value || 0), 0);
          if (totalValue > 0) {
            const weights = {};
            data.holdings.forEach(h => {
              if (h.ticker && h.current_value > 0) {
                weights[h.ticker] = h.current_value / totalValue;
              }
            });
            return { weights, source: 'local_portfolio' };
          }
        }
      }
    } catch (_) { /* not in Local Mode */ }

    // Fallback: equal-weight Core candidates from stress data
    if (_stressData && _stressData.ticker_stress) {
      const tickers = Object.keys(_stressData.ticker_stress);
      if (tickers.length > 0) {
        const w = 1.0 / tickers.length;
        const weights = {};
        tickers.forEach(t => (weights[t] = w));
        return { weights, source: 'equal_weight_candidates' };
      }
    }
    return { weights: {}, source: 'none' };
  }

  // ── Stress computation ─────────────────────────────────────────────────

  function computePortfolioStress(scenario, stressData, weights) {
    if (!stressData || !stressData.ticker_stress) return null;
    const totalWeight = Object.values(weights).reduce((s, w) => s + w, 0);
    if (totalWeight <= 0) return null;

    const contribs = [];
    let portRet = 0;
    let highBetaCount = 0;
    let lowR2Count = 0;

    for (const [ticker, w] of Object.entries(weights)) {
      const wNorm = w / totalWeight;
      const td = stressData.ticker_stress[ticker];
      if (!td) continue;

      const sc = td.scenarios && td.scenarios[scenario];
      if (!sc) continue;

      const er = sc.estimated_return || 0;
      const contribution = wNorm * er;
      portRet += contribution;

      if (td.beta !== null && td.beta !== undefined && td.beta > HIGH_BETA_THRESHOLD) {
        highBetaCount++;
      }
      if (td.r_squared !== null && td.r_squared !== undefined &&
          td.r_squared < LOW_R2_THRESHOLD) {
        lowR2Count++;
      }

      contribs.push({
        ticker,
        weight: Math.round(wNorm * 10000) / 100,   // as %
        estimated_return: er,
        contribution,
        beta: td.beta,
        r_squared: td.r_squared,
        method: sc.method,
      });
    }

    contribs.sort((a, b) => a.contribution - b.contribution);  // worst first

    return {
      portfolio_return: portRet,
      contributors: contribs,
      top5: contribs.slice(0, 5),
      n_positions: contribs.length,
      high_beta_count: highBetaCount,
      low_r2_count: lowR2Count,
    };
  }

  // ── Rendering helpers ──────────────────────────────────────────────────

  function _pct(v, digits = 1) {
    if (v == null) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${(v * 100).toFixed(digits)}%`;
  }

  function _pctColor(v) {
    if (v == null) return 'text-slate-400';
    return v >= 0 ? 'text-emerald-400' : 'text-rose-400';
  }

  function _methodBadge(method) {
    if (!method) return '';
    const map = {
      actual:        { label: '실측', cls: 'bg-emerald-900/50 text-emerald-400' },
      beta:          { label: 'β추정', cls: 'bg-blue-900/50 text-blue-400' },
      low_r2:        { label: '저R² β추정', cls: 'bg-amber-900/50 text-amber-400' },
      spy_proxy:     { label: 'SPY대리', cls: 'bg-slate-700 text-slate-300' },
      no_beta:       { label: '베타없음', cls: 'bg-rose-900/50 text-rose-400' },
    };
    const m = map[method] || { label: method, cls: 'bg-slate-700 text-slate-400' };
    return `<span class="text-xs px-1.5 py-0.5 rounded ${m.cls}">${m.label}</span>`;
  }

  function _renderScenarioTabs(scenarios) {
    return SCENARIO_ORDER
      .filter(k => scenarios[k])
      .map(k => {
        const sc = scenarios[k];
        const icon = SCENARIO_ICONS[k] || '📊';
        const active = k === _activeScenario;
        return `
          <button
            data-scenario="${k}"
            class="stress-tab px-3 py-2 rounded-lg text-sm font-medium transition-colors
                   ${active
                     ? 'bg-violet-600 text-white'
                     : 'bg-slate-800/60 text-slate-400 hover:bg-slate-700 hover:text-slate-200'}"
          >
            ${icon} ${sc.label}
          </button>`;
      })
      .join('');
  }

  function _renderScenarioMeta(scenario, stressData) {
    const sc = stressData.scenarios[scenario];
    if (!sc) return '';
    const spyPct = _pct(sc.spy_return);
    const spyColor = _pctColor(sc.spy_return);
    const sourceTag = sc.spy_source === 'fallback'
      ? '<span class="text-xs text-amber-400">(추정치)</span>'
      : '';
    return `
      <div class="flex flex-wrap gap-4 mt-3 p-3 bg-slate-800/40 rounded-lg">
        <div class="text-sm">
          <span class="text-slate-500">SPY:</span>
          <span class="${spyColor} font-semibold ml-1">${spyPct}</span>
          ${sourceTag}
        </div>
        ${sc.type === 'historical' ? `
          <div class="text-sm text-slate-400">
            ${sc.period_start} ~ ${sc.period_end}
          </div>` : ''}
        <div class="text-sm text-slate-400">${sc.description}</div>
      </div>`;
  }

  function _renderSummaryKPIs(result) {
    const portPct = _pct(result.portfolio_return);
    const portColor = _pctColor(result.portfolio_return);

    const alerts = [];
    if (result.high_beta_count > 0) {
      alerts.push(`<span class="text-amber-400">⚠ 고베타(>${HIGH_BETA_THRESHOLD}) 종목 ${result.high_beta_count}개</span>`);
    }
    if (result.low_r2_count > 0) {
      alerts.push(`<span class="text-slate-400 text-xs">R²<${LOW_R2_THRESHOLD} 종목 ${result.low_r2_count}개 (추정 신뢰도 낮음)</span>`);
    }

    return `
      <div class="flex flex-wrap gap-6 mt-4 mb-2">
        <div>
          <div class="text-xs text-slate-500 mb-0.5">포트폴리오 예상 손실</div>
          <div class="text-3xl font-bold ${portColor}">${portPct}</div>
          <div class="text-xs text-slate-500 mt-0.5">${result.n_positions}개 종목</div>
        </div>
        ${result.portfolio_return < -0.10 ? `
          <div class="self-center p-3 bg-rose-900/30 rounded-lg border border-rose-700/40 text-sm text-rose-300 max-w-xs">
            ⛔ 포트폴리오 손실 ${_pct(result.portfolio_return)} 예상 — 리스크 농도 점검 필요
          </div>` : ''}
        ${alerts.length ? `
          <div class="self-center flex flex-col gap-1">${alerts.join('')}</div>` : ''}
      </div>`;
  }

  function _renderContributorTable(result) {
    if (!result.contributors.length) {
      return '<div class="text-sm text-slate-500 py-4">데이터 없음</div>';
    }
    const rows = result.contributors.slice(0, 10).map(c => {
      const retColor = _pctColor(c.estimated_return);
      const contribColor = _pctColor(c.contribution);
      const betaTag = (c.beta !== null && c.beta !== undefined && c.beta > HIGH_BETA_THRESHOLD)
        ? `<span class="text-xs text-amber-400 ml-1">β${c.beta.toFixed(2)}</span>`
        : (c.beta !== null && c.beta !== undefined)
          ? `<span class="text-xs text-slate-500 ml-1">β${c.beta.toFixed(2)}</span>`
          : '';
      return `
        <tr class="border-b border-slate-700/50 hover:bg-slate-800/30 transition-colors">
          <td class="py-2 pr-3 font-semibold text-slate-200">${c.ticker}${betaTag}</td>
          <td class="py-2 pr-3 text-right text-slate-300">${c.weight.toFixed(1)}%</td>
          <td class="py-2 pr-3 text-right ${retColor}">${_pct(c.estimated_return)}</td>
          <td class="py-2 text-right ${contribColor} font-medium">${_pct(c.contribution)}</td>
          <td class="py-2 pl-2 text-right">${_methodBadge(c.method)}</td>
        </tr>`;
    }).join('');

    return `
      <table class="w-full text-sm mt-3">
        <thead>
          <tr class="text-xs text-slate-500 border-b border-slate-700">
            <th class="pb-1.5 text-left">종목</th>
            <th class="pb-1.5 text-right">비중</th>
            <th class="pb-1.5 text-right">개별 수익률</th>
            <th class="pb-1.5 text-right">기여도</th>
            <th class="pb-1.5 text-right">방법</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function _renderWeightSource(source) {
    const labels = {
      local_portfolio:       '🟢 내 포트폴리오 (로컬 DB)',
      equal_weight_candidates: '⚪ 동일비중 (후보 종목)',
      none:                  '🔴 포트폴리오 데이터 없음',
    };
    return `<span class="text-xs text-slate-500">${labels[source] || source}</span>`;
  }

  function _fullHTML(stressData, result, weightSource) {
    const snapshotAge = stressData.snapshot_date
      ? `<span class="text-xs text-slate-500 ml-2">스냅샷: ${stressData.snapshot_date}</span>`
      : '';

    return `
      <div class="space-y-4" id="stress-root">

        <!-- Header row -->
        <div class="flex items-center justify-between flex-wrap gap-2">
          <h3 class="text-sm font-semibold text-slate-300">
            📊 포트폴리오 스트레스 테스트
            ${snapshotAge}
          </h3>
          ${_renderWeightSource(weightSource)}
        </div>

        <!-- Scenario tabs -->
        <div class="flex flex-wrap gap-2" id="stress-tabs">
          ${_renderScenarioTabs(stressData.scenarios)}
        </div>

        <!-- Scenario metadata bar -->
        <div id="stress-scenario-meta">
          ${_renderScenarioMeta(_activeScenario, stressData)}
        </div>

        <!-- KPI row -->
        <div id="stress-kpi">
          ${result ? _renderSummaryKPIs(result) : '<div class="text-slate-500 text-sm">데이터 로딩 중...</div>'}
        </div>

        <!-- Contributor table -->
        <div id="stress-table">
          ${result
            ? `<div class="text-xs text-slate-500 mb-1">손익 기여도 (worst first)</div>
               ${_renderContributorTable(result)}`
            : ''}
        </div>

        <!-- Methodology note -->
        <div class="text-xs text-slate-600 mt-2 leading-relaxed">
          방법론: <strong class="text-slate-500">실측</strong> = 해당 기간 실제 가격 사용.
          <strong class="text-slate-500">β추정</strong> = 시장베타 × 시나리오 충격 (OLS, 2년 일별 수익률).
          R² &lt; ${LOW_R2_THRESHOLD} 이면 추정 신뢰도 낮음.
        </div>
      </div>`;
  }

  // ── Rendering entry point ─────────────────────────────────────────────

  async function _rerender() {
    if (!_container) return;
    const el = document.getElementById(_container);
    if (!el) return;

    if (!_stressData || !_weights) {
      el.innerHTML = '<div class="text-slate-500 text-sm py-4">스트레스 테스트 데이터 로딩 중...</div>';
      return;
    }

    const result = computePortfolioStress(_activeScenario, _stressData, _weights);
    el.innerHTML = _fullHTML(_stressData, result, _weightSource || 'none');

    // Attach tab click handlers
    el.querySelectorAll('.stress-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        _activeScenario = btn.dataset.scenario;
        _rerender();
      });
    });
  }

  let _weightSource = 'none';

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Render the full stress test widget inside `containerId`.
   * Handles data loading + portfolio weight detection automatically.
   */
  async function renderWidget(containerId) {
    _container = containerId;
    const el = document.getElementById(containerId);
    if (!el) return;

    el.innerHTML = '<div class="text-slate-500 text-sm py-4 animate-pulse">스트레스 테스트 로딩 중...</div>';

    const [sd, { weights, source }] = await Promise.all([
      _loadStressData(),
      _loadWeightsFromDB(),
    ]);

    _stressData = sd;
    _weights = weights;
    _weightSource = source;

    if (!_stressData) {
      el.innerHTML = `
        <div class="text-slate-500 text-sm py-4 text-center">
          스트레스 테스트 데이터 없음
          <div class="text-xs mt-1">daily snapshot 실행 후 latest_stress_test.json이 생성됩니다</div>
        </div>`;
      return;
    }

    await _rerender();
  }

  /**
   * Init with explicit weights dict (for embedding in other contexts).
   * weights: { TICKER: fraction, ... }
   */
  async function init(containerId, weights) {
    _container = containerId;
    _weights = weights;
    _weightSource = 'explicit';
    _stressData = await _loadStressData();
    await _rerender();
  }

  /**
   * Return stress summary for a single scenario (without rendering).
   */
  function getSummary(scenario) {
    if (!_stressData || !_weights) return null;
    return computePortfolioStress(scenario, _stressData, _weights);
  }

  /**
   * Return all scenarios with portfolio returns (for optimizer integration).
   */
  function getAllSummaries() {
    if (!_stressData || !_weights) return {};
    const out = {};
    SCENARIO_ORDER.forEach(key => {
      if (_stressData.scenarios[key]) {
        out[key] = computePortfolioStress(key, _stressData, _weights);
      }
    });
    return out;
  }

  return { renderWidget, init, getSummary, getAllSummaries };
})();
