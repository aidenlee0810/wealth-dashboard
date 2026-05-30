// research/risk.js — Risk analysis tab
// Beta, Sharpe, Sortino, Max Drawdown, VaR, Correlation matrix
// All computed client-side from price candles

const RISK = (() => {
  const CONTAINER = 'tab-risk';
  const BENCHMARK = 'SPY';
  const RISK_FREE_SERIES = 'DGS3MO'; // 3-month T-bill from FRED

  async function init(ticker) {
    if (!ticker) {
      document.getElementById(CONTAINER).innerHTML = `
        <div class="flex items-center justify-center py-24 text-slate-500 text-sm">종목을 먼저 검색하세요</div>`;
      return;
    }
    if (!RESEARCH_CONFIG.FINNHUB_KEY) {
      UI.noKey(CONTAINER, 'Finnhub', 'FINNHUB_KEY');
      return;
    }
    UI.loading(CONTAINER, '리스크 지표 계산 중...');
    try {
      await _render(ticker);
    } catch (e) {
      UI.error(CONTAINER, '리스크 분석 실패: ' + e.message);
    }
  }

  async function _render(ticker) {
    const now = Math.floor(Date.now() / 1000);
    const y2ago = now - 2 * 365 * 86400; // 2 years

    // Fetch candles for ticker + benchmark in parallel
    const benchmarkTickers = [BENCHMARK, 'QQQ', 'BND', 'GLD'];
    const allTickers = [ticker, ...benchmarkTickers];

    const [candlesResults, rfRate, vixData, fgData] = await Promise.all([
      Promise.allSettled(allTickers.map(sym => API.candles(sym, y2ago, now, 'D'))),
      _getRiskFreeRate(),
      API.vix().catch(() => null),
      API.fearAndGreed().catch(() => null),
    ]);

    const candlesMap = {};
    allTickers.forEach((sym, i) => {
      if (candlesResults[i].status === 'fulfilled') {
        candlesMap[sym] = candlesResults[i].value;
      }
    });

    const tickerCandles = candlesMap[ticker];
    const spyCandles    = candlesMap[BENCHMARK];

    if (!tickerCandles?.c?.length || !spyCandles?.c?.length) {
      UI.error(CONTAINER, '가격 데이터 부족 — 2년치 일별 데이터 필요');
      return;
    }

    // Align dates between ticker and benchmark
    const { returns: tickerRet, spyRet: benchRet, alignedDates } = _alignReturns(
      tickerCandles, spyCandles
    );

    // Core risk metrics
    const alignedCount = tickerRet.length;
    const beta         = _beta(tickerRet, benchRet);
    const annRet       = _annualReturn(tickerRet);
    const annVol       = _annualVol(tickerRet);
    const sharpe       = _sharpe(tickerRet, rfRate);
    const sortino      = _sortino(tickerRet, rfRate);
    const maxDD        = _maxDrawdown(tickerCandles.c);
    const currentDD    = _currentDrawdown(tickerCandles.c);
    const var95        = _var(tickerRet, 0.05);
    const var99        = _var(tickerRet, 0.01);
    const cvar95       = _cvar(tickerRet, 0.05);
    const cvar99       = _cvar(tickerRet, 0.01);
    const alpha        = _alpha(tickerRet, benchRet, rfRate, beta);
    const calmar       = Math.abs(maxDD) > 0 ? annRet / Math.abs(maxDD) : null;

    // Correlations with other assets
    const correlations = {};
    benchmarkTickers.forEach(sym => {
      if (candlesMap[sym]) {
        const { returns: r1, spyRet: r2 } = _alignReturns(tickerCandles, candlesMap[sym]);
        correlations[sym] = _correlation(r1, r2);
      }
    });

    // My portfolio holdings correlations (all tickers, no slice)
    const myPos = STATE.getMyPositions();
    const myTickers = [...new Set(myPos.map(p => p.ticker).filter(Boolean))];

    const el = document.getElementById(CONTAINER);
    el.innerHTML = `
      ${_marketContextSection(vixData, fgData)}
      ${_heroSection(beta, sharpe, sortino, maxDD, currentDD, annRet, annVol, rfRate, calmar, alignedCount)}
      ${_varSection(var95, var99, cvar95, cvar99, tickerCandles.c[tickerCandles.c.length - 1])}
      ${_rollingBetaSection()}
      ${_returnDistSection()}
      ${_correlationSection(correlations, ticker, myTickers)}
      <div class="mt-8 p-4 bg-slate-900/60 rounded-xl border border-slate-700/60" id="stress-widget-container"></div>`;

    // Render charts
    _renderRollingBeta(ticker, tickerCandles, spyCandles);
    _renderReturnDist(tickerRet);
    if (myTickers.length > 0) {
      _renderPortfolioCorrelation(ticker, tickerCandles, myPos, myTickers);
    }

    // Render portfolio stress test widget (Phase 9)
    if (typeof PORTFOLIO_STRESS !== 'undefined') {
      PORTFOLIO_STRESS.renderWidget('stress-widget-container');
    }
  }

  // ── Market Context: VIX + Fear & Greed ───────────────────────────────────
  function _marketContextSection(vix, fg) {
    const vixVal   = vix?.price;
    const vixColor = vixVal == null ? 'text-slate-400'
                   : vixVal < 15 ? 'text-emerald-400' : vixVal < 20 ? 'text-amber-400'
                   : vixVal < 30 ? 'text-orange-400' : 'text-red-400';
    const vixLabel = vixVal == null ? '—'
                   : vixVal < 15 ? '안정' : vixVal < 20 ? '보통' : vixVal < 30 ? '주의' : '공포';
    const vixChgStr = vix?.changePct != null
      ? (vix.changePct >= 0 ? '+' : '') + vix.changePct.toFixed(1) + '%' : '';

    const fgScore  = fg?.score;
    const fgColor  = fgScore == null ? 'text-slate-400'
                   : fgScore >= 75 ? 'text-emerald-400' : fgScore >= 55 ? 'text-green-400'
                   : fgScore >= 45 ? 'text-slate-300'   : fgScore >= 25 ? 'text-orange-400'
                   : 'text-red-400';
    const fgRatingKo = { 'Extreme Greed':'극단적 탐욕','Greed':'탐욕','Neutral':'중립',
                         'Fear':'공포','Extreme Fear':'극단적 공포' }[fg?.rating || ''] || (fg?.rating || '—');
    const fgPct    = fgScore ?? 0;
    const fgBarColor = fgScore >= 75 ? 'bg-emerald-500' : fgScore >= 55 ? 'bg-green-500'
                     : fgScore >= 45 ? 'bg-slate-400'   : fgScore >= 25 ? 'bg-orange-500' : 'bg-red-500';
    const vixPct   = vixVal != null ? Math.min(100, vixVal / 50 * 100) : 0;
    const vixBarColor = vixVal < 15 ? 'bg-emerald-500' : vixVal < 20 ? 'bg-amber-500'
                      : vixVal < 30 ? 'bg-orange-500' : 'bg-red-500';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('시장 심리 (리스크 환경)', 'VIX · Fear & Greed (alternative.me) — 종목 리스크 해석 맥락')}
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3">
            <div class="text-xs text-slate-500 mb-1">VIX (변동성지수)</div>
            <div class="text-2xl font-bold font-mono ${vixColor}">${vixVal != null ? vixVal.toFixed(1) : '—'}</div>
            <div class="text-xs ${vixColor} mt-0.5">${vixLabel}${vixChgStr ? ' · ' + vixChgStr : ''}</div>
            <div class="relative h-1.5 bg-slate-700 rounded-full overflow-hidden mt-2">
              <div class="absolute left-0 top-0 h-full ${vixBarColor} rounded-full" style="width:${vixPct}%"></div>
            </div>
          </div>
          <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3">
            <div class="text-xs text-slate-500 mb-1">공포·탐욕 (F&G)</div>
            <div class="text-2xl font-bold font-mono ${fgColor}">${fgScore != null ? fgScore : '—'}</div>
            <div class="text-xs ${fgColor} mt-0.5">${fgRatingKo}</div>
            <div class="relative h-1.5 bg-slate-700 rounded-full overflow-hidden mt-2">
              <div class="absolute left-0 top-0 h-full ${fgBarColor} rounded-full" style="width:${fgPct}%"></div>
            </div>
          </div>
          <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3 col-span-2">
            <div class="text-xs text-slate-500 mb-2">시장 심리 해석</div>
            <div class="text-xs text-slate-300 leading-relaxed">
              ${vixVal != null && fgScore != null ? _sentimentInterpret(vixVal, fgScore) : '데이터 로딩 중...'}
            </div>
          </div>
        </div>
      </div>`;
  }

  function _sentimentInterpret(vix, fg) {
    if (vix > 30 || fg < 25)
      return '⚠️ 극단적 공포 국면 — VIX 급등/F&G 최저권. 개별 종목 매수는 신중, 단 역발상 매수 기회 탐색 시점일 수 있음.';
    if (vix > 20 || fg < 40)
      return '🔸 공포 우세 — 시장 불안 고조. 포지션 사이즈 보수적으로 유지. Beta 높은 종목 리스크 특히 확대됨.';
    if (vix < 15 && fg > 70)
      return '🟡 극단적 탐욕 — VIX 저점·과도한 낙관. 신규 매수 추가 시 가격 모멘텀보다 밸류에이션 확인 필수.';
    if (vix < 18 && fg > 55)
      return '✅ 탐욕 국면 — 시장 안정적. 추세추종 전략에 우호적. 단, 변동성 급등 리스크 대비 유지.';
    return '⚖️ 중립 — 혼재 신호. 종목별 펀더멘털·기술적 분석 결과에 더 집중.';
  }

  // ── Hero Section ──────────────────────────────────────────────────────────
  // calmar = annualReturn / abs(maxDrawdown)  — higher is better
  function _heroSection(beta, sharpe, sortino, maxDD, currentDD, annRet, annVol, rfRate, calmar, alignedCount) {
    const { fmt } = UI;
    const betaColor = beta < 0.8 ? 'green' : beta > 1.5 ? 'red' : null;
    const sharpeColor = sharpe > 1 ? 'green' : sharpe > 0 ? null : 'red';
    const ddColor = maxDD > -0.2 ? 'green' : maxDD > -0.4 ? null : 'red';
    const calmarColor = calmar == null ? null : calmar > 1 ? 'green' : calmar > 0.5 ? null : 'red';
    return `
      <div class="mb-6">
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          ${UI.kpi('Beta (vs SPY)', fmt.num(beta, 2), { color: betaColor,
            sub: beta < 1 ? '시장 대비 저변동' : beta > 1 ? '시장 대비 고변동' : '시장과 동일' })}
          ${UI.kpi('Sharpe Ratio', fmt.num(sharpe, 2), { color: sharpeColor,
            sub: '연환산 · 무위험=3M T-bill' })}
          ${UI.kpi('Sortino Ratio', fmt.num(sortino, 2),
            { sub: '하방 위험 기준' })}
          ${UI.kpi('Calmar Ratio', fmt.num(calmar, 2), { color: calmarColor, sub: '연수익 / MDD' })}
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          ${UI.kpi('최대 낙폭 (MDD)', fmt.pct(maxDD * 100), { color: ddColor })}
          ${UI.kpi('현재 낙폭 (ATH 대비)', fmt.pct(currentDD * 100), { color: currentDD < -0.2 ? 'red' : null })}
          ${UI.kpi('연환산 수익률', fmt.pct(annRet * 100), { color: annRet > 0 ? 'green' : 'red' })}
          ${UI.kpi('연환산 변동성', fmt.pct(annVol * 100), { sub: '표준편차 × √252' })}
        </div>
        ${alignedCount != null ? `<div class="mt-2 text-xs text-slate-600">* ${alignedCount}거래일 SPY 공통 데이터 기준 분석 (날짜 기반 정렬)</div>` : ''}
      </div>`;
  }

  // ── VaR Section ───────────────────────────────────────────────────────────
  function _varSection(var95, var99, cvar95, cvar99, lastPrice) {
    const { fmt } = UI;
    const loss95  = lastPrice ? var95  * lastPrice : null;
    const loss99  = lastPrice ? var99  * lastPrice : null;
    const closs95 = lastPrice && cvar95 ? cvar95 * lastPrice : null;
    const closs99 = lastPrice && cvar99 ? cvar99 * lastPrice : null;
    return `
      <div class="mb-6">
        ${UI.sectionHeader('Value at Risk (VaR) & CVaR', '과거 수익률 분포 기반 · 1일 기준')}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="bg-red-950/20 border border-red-800/40 rounded-xl p-4">
            <div class="text-xs text-red-400 font-medium mb-1">VaR 95% 신뢰구간</div>
            <div class="text-2xl font-bold text-red-400">${fmt.pct(var95 * 100, 2)}</div>
            <div class="text-sm text-slate-400 mt-1">1주식당 손실: ${loss95 ? fmt.dollar(Math.abs(loss95), 2) : '—'}</div>
            <div class="text-xs text-slate-500 mt-1">100일 중 5일은 이 이상 손실 가능</div>
            ${cvar95 != null ? `
            <div class="mt-2 border-t border-red-900/50 pt-2">
              <div class="text-xs text-red-300 font-medium">CVaR (Expected Shortfall) 95%</div>
              <div class="text-lg font-bold text-red-300">${fmt.pct(cvar95 * 100, 2)}</div>
              <div class="text-xs text-slate-500">손실 발생 시 평균 손실 (VaR 초과 구간)</div>
              ${closs95 ? `<div class="text-xs text-slate-400">1주당 평균 ${fmt.dollar(Math.abs(closs95), 2)}</div>` : ''}
            </div>` : ''}
          </div>
          <div class="bg-red-950/30 border border-red-700/50 rounded-xl p-4">
            <div class="text-xs text-red-400 font-medium mb-1">VaR 99% 신뢰구간</div>
            <div class="text-2xl font-bold text-red-300">${fmt.pct(var99 * 100, 2)}</div>
            <div class="text-sm text-slate-400 mt-1">1주식당 손실: ${loss99 ? fmt.dollar(Math.abs(loss99), 2) : '—'}</div>
            <div class="text-xs text-slate-500 mt-1">100일 중 1일은 이 이상 손실 가능</div>
            ${cvar99 != null ? `
            <div class="mt-2 border-t border-red-900/50 pt-2">
              <div class="text-xs text-red-300 font-medium">CVaR (Expected Shortfall) 99%</div>
              <div class="text-lg font-bold text-red-300">${fmt.pct(cvar99 * 100, 2)}</div>
              <div class="text-xs text-slate-500">손실 발생 시 평균 손실 (VaR 초과 구간)</div>
              ${closs99 ? `<div class="text-xs text-slate-400">1주당 평균 ${fmt.dollar(Math.abs(closs99), 2)}</div>` : ''}
            </div>` : ''}
          </div>
        </div>
        <p class="text-xs text-slate-600 mt-2">* 과거 수익률 분포 기반 추정 (Historical Simulation). 미래 손실을 보장하지 않습니다. CVaR = VaR 초과 손실의 조건부 평균.</p>
      </div>`;
  }

  function _rollingBetaSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('롤링 Beta (52D)', 'vs SPY · 52거래일 ≈ 약 2.5개월')}
        <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div style="height:160px"><canvas id="chart-rolling-beta"></canvas></div>
        </div>
      </div>`;
  }

  function _returnDistSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('일별 수익률 분포')}
        <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div style="height:160px"><canvas id="chart-return-dist"></canvas></div>
        </div>
      </div>`;
  }

  function _correlationSection(correlations, ticker, myTickers) {
    const rows = Object.entries(correlations).map(([sym, corr]) => {
      const color = _corrColor(corr);
      const label = sym === 'SPY' ? 'S&P 500' : sym === 'QQQ' ? 'NASDAQ' : sym === 'BND' ? '채권 ETF' : '금 ETF';
      return [
        `<span class="font-mono">${sym}</span> <span class="text-slate-500 text-xs">(${label})</span>`,
        `<span class="${color} font-mono">${corr.toFixed(3)}</span>`,
        _corrBar(corr),
        corr >= 0.8  ? UI.signalBadge('강한 상관', 'warn')
          : corr >= 0.4  ? UI.signalBadge('중간 상관', 'neutral')
          : corr >= 0    ? UI.signalBadge('약한 상관', 'bullish')
          : UI.signalBadge('역상관 (반대 움직임)', 'bullish'),
      ];
    });
    return `
      <div class="mb-6">
        ${UI.sectionHeader('상관관계 (Correlation)', '2년 일별 수익률 기준')}
        ${UI.table(['자산', '상관계수', '', '판단'], rows)}
        ${myTickers.length > 0 ? `<div id="portfolio-corr" class="mt-4"></div>` : ''}
      </div>`;
  }

  // ── Shared helpers (used by _correlationSection and _renderPortfolioCorrelation) ──
  function _corrColor(c) {
    if (c == null) return 'text-slate-500';
    if (c < 0)    return 'text-blue-400';
    if (c < 0.4)  return 'text-emerald-400';
    if (c < 0.8)  return 'text-amber-400';
    return 'text-red-400';
  }

  function _corrLabel(c) {
    if (c == null) return '—';
    if (c < 0)    return '역상관';
    if (c < 0.4)  return '약한 상관';
    if (c < 0.8)  return '중간 상관';
    return '강한 상관';
  }

  function _corrBar(corr) {
    const pct = ((corr + 1) / 2 * 100).toFixed(0);
    const color = corr < 0 ? '#3b82f6' : corr < 0.4 ? '#10b981' : corr < 0.8 ? '#f59e0b' : '#ef4444';
    return `<div class="w-24 bg-slate-700 rounded h-1.5 relative">
      <div class="absolute left-1/2 top-0 bottom-0 w-0.5 bg-slate-500"></div>
      <div class="h-1.5 rounded absolute" style="width:${pct}%;background:${color}"></div>
    </div>`;
  }

  // ── Chart rendering ───────────────────────────────────────────────────────
  function _renderRollingBeta(ticker, tickerC, spyC) {
    const { returns: tRet, spyRet: sRet, alignedDates } = _alignReturns(tickerC, spyC);
    const windowSize = 52; // 52 trading days ≈ 1 quarter
    const rollingBeta = [];
    const labels = [];
    for (let i = windowSize; i < tRet.length; i++) {
      const window_t = tRet.slice(i - windowSize, i);
      const window_s = sRet.slice(i - windowSize, i);
      rollingBeta.push(_beta(window_t, window_s));
      labels.push(alignedDates[i] || '');
    }
    CHARTS.line('chart-rolling-beta', labels, [
      { label: 'Rolling Beta (52D)', data: rollingBeta, color: '#8b5cf6', pointRadius: 0 },
      { label: 'Beta = 1', data: new Array(rollingBeta.length).fill(1), color: '#64748b60', borderWidth: 1, pointRadius: 0 },
    ], {
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => v.toFixed(1) } } },
    });
  }

  function _renderReturnDist(returns) {
    const pct = returns.map(r => r * 100);
    const min = Math.min(...pct);
    const max = Math.max(...pct);
    const buckets = 30;
    const step = (max - min) / buckets;
    const counts = new Array(buckets).fill(0);
    const labels = [];
    pct.forEach(r => {
      const idx = Math.min(Math.floor((r - min) / step), buckets - 1);
      counts[idx]++;
    });
    for (let i = 0; i < buckets; i++) {
      labels.push((min + i * step).toFixed(1) + '%');
    }
    const colors = labels.map(l => parseFloat(l) < 0 ? '#ef444480' : '#10b98180');
    CHARTS.bar('chart-return-dist', labels, [
      { label: '빈도', data: counts, colors },
    ], { scales: { x: { ticks: { maxTicksLimit: 10 } } } });
  }

  async function _renderPortfolioCorrelation(ticker, tickerC, myPos, myTickers) {
    const el = document.getElementById('portfolio-corr');
    if (!el) return;
    const now = Math.floor(Date.now() / 1000);
    const y1ago = now - 365 * 86400;
    el.innerHTML = '<div class="text-slate-500 text-xs mt-1">내 포트폴리오 상관관계 계산 중...</div>';
    try {
      const results = await Promise.allSettled(
        myTickers.map(sym => API.candles(sym, y1ago, now, 'D'))
      );
      const cm = {};
      myTickers.forEach((sym, i) => {
        if (results[i].status === 'fulfilled' && results[i].value?.c?.length >= 20) {
          cm[sym] = results[i].value;
        }
      });

      // ── Portfolio-level synthetic returns (value-weighted) ──
      function _dk(ts) {
        const d = new Date(ts * 1000);
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
      }
      // Build weight map — use currentValue if available, else equal-weight
      const availT = myTickers.filter(t => cm[t]);
      const rawWeights = {};
      availT.forEach(t => {
        rawWeights[t] = myPos.filter(p=>p.ticker===t).reduce((s,p)=>s+(p.currentValue||0),0);
      });
      const totalValue = availT.reduce((s,t)=>s+rawWeights[t],0);
      // Fallback: equal weight if no currentValue data
      const weights = {};
      if (totalValue > 0) {
        availT.forEach(t => { weights[t] = rawWeights[t] / totalValue; });
      } else {
        availT.forEach(t => { weights[t] = 1 / availT.length; });
      }

      const retMaps = {};
      availT.forEach(t => {
        const c = cm[t];
        retMaps[t] = {};
        for (let i = 1; i < c.t.length; i++) retMaps[t][_dk(c.t[i])] = (c.c[i]-c.c[i-1])/c.c[i-1];
      });
      const dateCnt = {};
      availT.forEach(t => Object.keys(retMaps[t]).forEach(d => { dateCnt[d]=(dateCnt[d]||0)+1; }));
      const dates = Object.keys(dateCnt).filter(d => dateCnt[d] >= Math.ceil(availT.length*0.5)).sort();
      const portRetMap = {};
      dates.forEach(d => {
        let wSum=0, rSum=0;
        availT.forEach(t => {
          if (retMaps[t][d]==null) return;
          rSum += retMaps[t][d]*weights[t]; wSum+=weights[t];
        });
        if (wSum>0) portRetMap[d]=rSum/wSum;
      });

      // Align ticker returns vs portfolio synthetic returns
      function _corrVsPortfolio(tickC) {
        const r1=[],r2=[];
        for (let i=1;i<tickC.t.length;i++) {
          const d=_dk(tickC.t[i]);
          if (portRetMap[d]!=null) { r1.push((tickC.c[i]-tickC.c[i-1])/tickC.c[i-1]); r2.push(portRetMap[d]); }
        }
        return r1.length>=20 ? _correlation(r1,r2) : null;
      }

      // Individual rows
      const rows = [];
      myTickers.forEach(sym => {
        if (!cm[sym]) return;
        const { returns: r1, spyRet: r2 } = _alignReturns(tickerC, cm[sym]);
        const corrBench = _correlation(r1, r2);
        const corrPort  = _corrVsPortfolio(cm[sym]);
        const cc = (v) => _corrColor(v);
        const fmt = v => v!=null ? v.toFixed(3) : '—';
        rows.push([
          `<span class="font-mono">${sym}</span>`,
          `<span class="${cc(corrBench)} font-mono">${fmt(corrBench)}</span>`,
          `<span class="${cc(corrPort)} font-mono">${fmt(corrPort)}</span>`,
          _corrBar(corrBench),
        ]);
      });

      // Portfolio-level vs ticker
      const portDates = Object.keys(portRetMap).sort();
      const tickerRetByDate = {};
      for (let i=1;i<tickerC.t.length;i++) tickerRetByDate[_dk(tickerC.t[i])]=(tickerC.c[i]-tickerC.c[i-1])/tickerC.c[i-1];
      const pr1=[],pr2=[];
      portDates.forEach(d => { if (tickerRetByDate[d]!=null){ pr1.push(tickerRetByDate[d]); pr2.push(portRetMap[d]); } });
      const portCorr = pr1.length>=20 ? _correlation(pr1,pr2) : null;
      const pc = _corrColor(portCorr);

      el.innerHTML = `
        <div class="mt-1 mb-1 flex items-center justify-between">
          <span class="text-xs text-slate-400 font-medium">내 포트폴리오 종목과 상관관계 (1년)</span>
        </div>
        ${rows.length ? `
          <div class="bg-indigo-900/20 border border-indigo-700/40 rounded-lg px-3 py-2 mb-2 flex items-center gap-4">
            <span class="text-xs text-slate-400">내 포트폴리오 전체 vs <span class="font-mono text-white">${ticker}</span></span>
            <span class="text-base font-bold font-mono ${pc}">${portCorr!=null?portCorr.toFixed(3):'—'}</span>
            ${_corrBar(portCorr)}
            <span class="text-xs ${pc}">${_corrLabel(portCorr)}</span>
          </div>
          ${UI.table(['종목', `vs ${ticker}`, 'vs 내 포트', ''], rows, { compact: true })}
          <p class="text-xs text-slate-600 mt-1">* vs 내 포트 = 해당 종목과 포트폴리오 전체 가중 수익률의 상관계수</p>
        ` : '<div class="text-xs text-slate-600">가격 데이터 부족</div>'}`;
    } catch (e) {
      if (el) el.innerHTML = `<div class="text-xs text-slate-600">계산 실패: ${e.message}</div>`;
    }
  }

  // ── Statistical functions ─────────────────────────────────────────────────
  function _dailyReturns(closes) {
    const ret = [];
    for (let i = 1; i < closes.length; i++) {
      ret.push((closes[i] - closes[i - 1]) / closes[i - 1]);
    }
    return ret;
  }

  function _toDateKey(ts) {
    const d = new Date(ts * 1000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  }

  function _alignReturns(tickerC, benchC) {
    // Use YYYY-MM-DD string keys for alignment — avoids DST/timezone Unix timestamp mismatches
    const benchMap = {};
    benchC.t.forEach((ts, i) => { benchMap[_toDateKey(ts)] = benchC.c[i]; });
    const aligned_t = [], aligned_b = [], alignedDates = [];
    for (let i = 1; i < tickerC.t.length; i++) {
      const key     = _toDateKey(tickerC.t[i]);
      const prevKey = _toDateKey(tickerC.t[i - 1]);
      if (benchMap[key] != null && benchMap[prevKey] != null) {
        aligned_t.push((tickerC.c[i] - tickerC.c[i - 1]) / tickerC.c[i - 1]);
        aligned_b.push((benchMap[key] - benchMap[prevKey]) / benchMap[prevKey]);
        alignedDates.push(key);
      }
    }
    return { returns: aligned_t, spyRet: aligned_b, alignedDates };
  }

  function _mean(arr) {
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  }

  function _std(arr) {
    const m = _mean(arr);
    return Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / arr.length);
  }

  function _beta(tickerRet, benchRet) {
    if (!tickerRet.length || !benchRet.length) return null;
    const n = Math.min(tickerRet.length, benchRet.length);
    const t = tickerRet.slice(-n);
    const b = benchRet.slice(-n);
    const mt = _mean(t), mb = _mean(b);
    const cov = t.reduce((a, v, i) => a + (v - mt) * (b[i] - mb), 0) / n;
    const varB = b.reduce((a, v) => a + (v - mb) ** 2, 0) / n;
    return varB > 0 ? cov / varB : null;
  }

  function _correlation(r1, r2) {
    const n = Math.min(r1.length, r2.length);
    if (n < 10) return null;
    const a = r1.slice(-n), b = r2.slice(-n);
    const ma = _mean(a), mb = _mean(b);
    const cov = a.reduce((s, v, i) => s + (v - ma) * (b[i] - mb), 0) / n;
    const sa = _std(a), sb = _std(b);
    return sa > 0 && sb > 0 ? cov / (sa * sb) : null;
  }

  function _annualReturn(returns) {
    if (!returns.length) return null;
    const total = returns.reduce((a, r) => a * (1 + r), 1);
    const years = returns.length / 252;
    return Math.pow(total, 1 / years) - 1;
  }

  function _annualVol(returns) {
    return _std(returns) * Math.sqrt(252);
  }

  function _sharpe(returns, rfRate) {
    const annRet = _annualReturn(returns);
    const annVol = _annualVol(returns);
    if (annVol === 0) return null;
    return (annRet - rfRate) / annVol;
  }

  function _sortino(returns, rfRate) {
    const annRet = _annualReturn(returns);
    const downReturns = returns.filter(r => r < rfRate / 252);
    if (!downReturns.length) return null;
    const downDev = Math.sqrt(downReturns.reduce((a, r) => a + (r - rfRate/252)**2, 0) / downReturns.length) * Math.sqrt(252);
    return downDev > 0 ? (annRet - rfRate) / downDev : null;
  }

  function _alpha(tickerRet, benchRet, rfRate, beta) {
    const annTicker = _annualReturn(tickerRet);
    const annBench  = _annualReturn(benchRet);
    if (beta == null) return null;
    return annTicker - (rfRate + beta * (annBench - rfRate));
  }

  function _maxDrawdown(closes) {
    let peak = closes[0], maxDD = 0;
    for (const c of closes) {
      if (c > peak) peak = c;
      const dd = (c - peak) / peak;
      if (dd < maxDD) maxDD = dd;
    }
    return maxDD;
  }

  function _currentDrawdown(closes) {
    const peak = Math.max(...closes);
    const last = closes[closes.length - 1];
    return (last - peak) / peak;
  }

  function _var(returns, alpha) {
    const sorted = [...returns].sort((a, b) => a - b);
    const idx = Math.floor(alpha * sorted.length);
    return sorted[idx];
  }

  // CVaR (Expected Shortfall): average loss on days worse than VaR threshold
  function _cvar(returns, alpha) {
    const sorted = [...returns].sort((a, b) => a - b);
    const cutoff = Math.max(1, Math.floor(alpha * sorted.length));
    const tail = sorted.slice(0, cutoff);
    return tail.length > 0 ? tail.reduce((a, b) => a + b, 0) / tail.length : sorted[0];
  }

  async function _getRiskFreeRate() {
    try {
      const latest = await API.fredLatest(RISK_FREE_SERIES);
      return latest ? latest.value / 100 : 0.05; // fallback 5%
    } catch (_) {
      return 0.05;
    }
  }

  return { init };
})();
