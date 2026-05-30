// research/macro.js — Macro environment tab
// FRED: Fed rate, Treasury yields, TIPS, CPI, credit spreads, Copper/Gold ratio
// Finnhub: market news

const MACRO = (() => {
  const CONTAINER = 'tab-macro';

  // FRED Series IDs
  const SERIES = {
    FED_FUNDS:  'FEDFUNDS',       // Federal funds effective rate
    T2Y:        'DGS2',           // 2-Year Treasury
    T5Y:        'DGS5',           // 5-Year Treasury
    T10Y:       'DGS10',          // 10-Year Treasury
    T30Y:       'DGS30',          // 30-Year Treasury
    TIPS10Y:    'DFII10',         // 10-Year TIPS real yield
    SPREAD:     'T10Y2Y',         // 10Y-2Y spread (inversion indicator)
    T10Y3M:     'T10Y3M',         // 10Y-3M spread (recession probability)
    CPI:        'CPIAUCSL',       // CPI All Urban
    CORE_CPI:   'CPILFESL',       // Core CPI (ex food & energy)
    PCE:        'PCEPI',          // PCE inflation
    UNEMP:      'UNRATE',         // Unemployment rate
    GDP:        'GDPC1',          // Real GDP
    IG_OAS:     'BAMLC0A0CM',     // Investment Grade credit spread (OAS)
    HY_OAS:     'BAMLH0A0HYM2',  // High Yield credit spread (OAS)
    COPPER:     'PCOPPUSDM',         // Copper price (monthly, USD/metric ton)
    PMI_MFG:    'ISM_MAN_PMI',    // ISM Manufacturing PMI
    CONF_BD:    'UMCSENT',        // University of Michigan Consumer Sentiment
    DXY_PROXY:  'DTWEXBGS',       // Broad USD index
  };

  async function init() {
    if (!RESEARCH_CONFIG.FRED_KEY) {
      UI.noKey(CONTAINER, 'FRED (Federal Reserve)', 'FRED_KEY');
      if (window.SNAPSHOT) SNAPSHOT.render(CONTAINER);  // snapshot card works without FRED key
      return;
    }
    UI.loading(CONTAINER, '매크로 데이터 로딩 중...');
    try {
      await _render();
    } catch (e) {
      UI.error(CONTAINER, '매크로 데이터 로딩 실패: ' + e.message);
    }
    // Prepend the daily-snapshot status card (Plan §11) at the top of the tab.
    if (window.SNAPSHOT) SNAPSHOT.render(CONTAINER);
  }

  async function _render() {
    // Fetch FRED + VIX + Fear&Greed in parallel
    const [rates, spreads, inflation, growth, credit, commodities, vixData, fgData] = await Promise.all([
      API.fredBatch([SERIES.FED_FUNDS, SERIES.T2Y, SERIES.T5Y, SERIES.T10Y, SERIES.T30Y, SERIES.TIPS10Y, SERIES.SPREAD, SERIES.T10Y3M]),
      API.fredBatch([SERIES.SPREAD, SERIES.T10Y3M]),
      API.fredBatch([SERIES.CPI, SERIES.CORE_CPI, SERIES.PCE]),
      API.fredBatch([SERIES.UNEMP, SERIES.GDP, SERIES.CONF_BD]),
      API.fredBatch([SERIES.IG_OAS, SERIES.HY_OAS]),
      API.fredBatch([SERIES.COPPER]),
      API.vix().catch(() => null),
      API.fearAndGreed().catch(() => null),
    ]);

    // Fetch GLD (Gold ETF) monthly via Yahoo Finance proxy — FRED gold series not available on free tier
    let goldMonthly = [];
    try {
      const gldRaw = await API.yhfinMonthly('GLD', '5y');
      goldMonthly = gldRaw;
    } catch (_) {}

    // Extract latest values
    const lat = (series) => {
      const data = rates[series] || spreads[series] || inflation[series] || growth[series] || credit[series] || commodities[series];
      if (!data || !data.length) return null;
      return data[data.length - 1];
    };

    const fedFunds = lat(SERIES.FED_FUNDS);
    const t2y  = lat(SERIES.T2Y);
    const t5y  = lat(SERIES.T5Y);
    const t10y = lat(SERIES.T10Y);
    const t30y = lat(SERIES.T30Y);
    const tips = lat(SERIES.TIPS10Y);
    const spread = lat(SERIES.SPREAD);
    const t10y3m = lat(SERIES.T10Y3M);
    const igOas = lat(SERIES.IG_OAS);
    const hyOas = lat(SERIES.HY_OAS);

    // 10Y-2Y change momentum (21 ≈ 1M trading days, 63 ≈ 3M)
    const spreadHistory = rates[SERIES.SPREAD] || spreads[SERIES.SPREAD] || [];
    const spreadVal = spread?.value;
    const _pastSpread = (n) => spreadHistory.length > n ? spreadHistory[spreadHistory.length - 1 - n]?.value : null;
    const change1m = spreadVal != null && _pastSpread(21) != null ? spreadVal - _pastSpread(21) : null;
    const change3m = spreadVal != null && _pastSpread(63) != null ? spreadVal - _pastSpread(63) : null;

    // Classify 10Y-2Y and 10Y-3M
    const spreadClass  = _classifySpread(spreadVal, change1m);
    const t10y3mClass  = _classifyT10Y3M(t10y3m?.value);

    // Credit spread regime — FRED OAS values are in % (e.g. 3.0 = 300bp)
    const igVal = igOas?.value != null ? igOas.value * 100 : null;  // convert to bp
    const hyVal = hyOas?.value != null ? hyOas.value * 100 : null;  // convert to bp
    const creditSignal = hyVal > 500 ? 'bearish' : hyVal > 350 ? 'warn' : 'bullish';

    // Composite regime: 10Y-2Y + HY OAS + real yield + copper/gold
    const hyOasScore  = hyVal == null ? 0 : hyVal < 350 ? 1 : hyVal < 500 ? 0 : hyVal < 700 ? -1 : -2;
    const tipsScore   = tips?.value == null ? 0 : tips.value > 2.5 ? -1 : tips.value > 1.5 ? 0 : 1;

    // Compute YoY CPI
    const cpiData = inflation[SERIES.CORE_CPI] || [];
    const cpiYoY  = _yoyChange(cpiData);

    // Compute Copper/Gold ratio — copper in USD/metric ton, GLD in $/share (proxy for gold)
    const copperData = commodities[SERIES.COPPER] || [];
    const cgRatio    = _copperGoldRatio(copperData, goldMonthly);

    // Regime composite score
    const regime = _calcRegime(spreadClass.score, hyOasScore, tipsScore, cgRatio?.slice(-1)[0]?.ratio);

    // Persist canonical regime for optimizer and signal-engine to read (6h TTL)
    // Also store TIPS/HY OAS raw values for market-regime.js feature extraction
    try {
      localStorage.setItem('wr_macro_regime', JSON.stringify({
        value: regime,
        ts: Date.now(),
        tipsRate:   tips?.value   ?? null,
        hyOas:      hyVal         ?? null,   // basis points
        yieldCurve: spreadVal     ?? null,
      }));
    } catch (_) {}

    // Run 8-regime classifier now that macro data is fresh
    const regimeData8 = (typeof MARKET_REGIME !== 'undefined') ? MARKET_REGIME.classify() : null;

    const el = document.getElementById(CONTAINER);
    el.innerHTML = `
      ${_regimeSection(regime)}
      ${regimeData8 ? _playbookSection(regimeData8) : ''}
      ${_sentimentSection(vixData, fgData)}
      ${_ratesSection(fedFunds, t2y, t5y, t10y, t30y, tips)}
      ${_yieldCurveSection(t2y, t5y, t10y, t30y, spreadVal, change1m, change3m, spreadClass, t10y3m?.value, t10y3mClass)}
      ${_creditSection(igVal, hyVal, creditSignal)}
      ${_inflationSection(cpiYoY, _yoyChange(inflation[SERIES.PCE] || []), tips)}
      ${_economicSection(lat(SERIES.UNEMP), lat(SERIES.CONF_BD))}
      ${_copperGoldSection()}
      ${_newsSection()}`;

    // Render charts
    _renderYieldCurve(t2y?.value, t5y?.value, t10y?.value, t30y?.value);
    _renderSpreadHistory(rates[SERIES.SPREAD] || []);
    _renderCreditSpreads(credit[SERIES.IG_OAS] || [], credit[SERIES.HY_OAS] || []);
    _renderCopperGold(cgRatio);
    await _renderNewsAsync();
  }

  // ── Market Playbook Section (8-regime) ───────────────────────────────────
  function _playbookSection(regimeData8) {
    if (!regimeData8 || typeof MARKET_REGIME === 'undefined') return '';
    return `
      <div class="mb-6">
        ${UI.sectionHeader('시장 국면 플레이북', '8-Regime Classifier · Market-Regime.js')}
        ${MARKET_REGIME.renderPlaybook(regimeData8, false)}
      </div>`;
  }

  // ── Sentiment Section: VIX + Fear & Greed ────────────────────────────────
  function _sentimentSection(vix, fg) {
    // VIX formatting & color
    const vixVal  = vix?.price;
    const vixChg  = vix?.changePct;
    const vixColor = vixVal == null ? 'text-slate-400'
                   : vixVal < 15   ? 'text-emerald-400'
                   : vixVal < 20   ? 'text-amber-400'
                   : vixVal < 30   ? 'text-orange-400'
                   : 'text-red-400';
    const vixLabel = vixVal == null ? '—'
                   : vixVal < 15 ? '저변동 (안정)' : vixVal < 20 ? '보통' : vixVal < 30 ? '주의' : '공포 구간';
    const vixStr = vixVal != null ? vixVal.toFixed(2) : '—';
    const vixChgStr = vixChg != null ? (vixChg >= 0 ? '+' : '') + vixChg.toFixed(1) + '%' : '';

    // Fear & Greed formatting
    const fgScore  = fg?.score;
    const fgRating = fg?.rating || '';
    const fgColor  = fgScore == null ? 'text-slate-400'
                   : fgScore >= 75 ? 'text-emerald-400'
                   : fgScore >= 55 ? 'text-green-400'
                   : fgScore >= 45 ? 'text-slate-300'
                   : fgScore >= 25 ? 'text-orange-400'
                   : 'text-red-400';
    const fgRatingKo = {
      'Extreme Greed': '극단적 탐욕',
      'Greed':         '탐욕',
      'Neutral':       '중립',
      'Fear':          '공포',
      'Extreme Fear':  '극단적 공포',
    }[fgRating] || fgRating;
    const fgStr = fgScore != null ? fgScore.toString() : '—';

    // VIX gauge bar (0-50 scale, capped)
    const vixPct = vixVal != null ? Math.min(100, vixVal / 50 * 100) : 0;
    const vixBarColor = vixVal < 15 ? 'bg-emerald-500' : vixVal < 20 ? 'bg-amber-500' : vixVal < 30 ? 'bg-orange-500' : 'bg-red-500';

    // F&G gauge bar (0-100)
    const fgPct = fgScore != null ? fgScore : 0;
    const fgBarColor = fgScore >= 75 ? 'bg-emerald-500' : fgScore >= 55 ? 'bg-green-500'
                     : fgScore >= 45 ? 'bg-slate-400'   : fgScore >= 25 ? 'bg-orange-500' : 'bg-red-500';

    // Historical F&G
    const fgHist = (label, val) => val != null
      ? `<div class="text-center"><div class="text-xs text-slate-500">${label}</div><div class="text-xs font-semibold text-slate-300">${Math.round(val)}</div></div>`
      : '';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('시장 심리 지표', `VIX · Fear & Greed Index (${fg?.source || 'CNN'})`)}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">

          <!-- VIX Card -->
          <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs text-slate-400 font-medium">VIX (변동성 지수)</span>
              ${vixChgStr ? `<span class="text-xs ${vixChg >= 0 ? 'text-red-400' : 'text-emerald-400'}">${vixChgStr}</span>` : ''}
            </div>
            <div class="flex items-baseline gap-2 mb-2">
              <span class="text-3xl font-bold font-mono ${vixColor}">${vixStr}</span>
              <span class="text-xs ${vixColor}">${vixLabel}</span>
            </div>
            <div class="relative h-2 bg-slate-700 rounded-full overflow-hidden mb-2">
              <div class="absolute left-0 top-0 h-full rounded-full transition-all ${vixBarColor}" style="width:${vixPct}%"></div>
            </div>
            <div class="flex justify-between text-xs text-slate-600">
              <span>0 (평온)</span><span>20 (주의)</span><span>30+ (공포)</span>
            </div>
            ${vix?.high != null ? `<div class="text-xs text-slate-600 mt-2">오늘 범위: ${vix.low?.toFixed(1)} – ${vix.high?.toFixed(1)}</div>` : ''}
          </div>

          <!-- Fear & Greed Card -->
          <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs text-slate-400 font-medium">공포·탐욕 지수 (CNN F&G)</span>
              ${fgRatingKo ? `<span class="text-xs ${fgColor} font-semibold">${fgRatingKo}</span>` : ''}
            </div>
            <div class="flex items-baseline gap-2 mb-2">
              <span class="text-3xl font-bold font-mono ${fgColor}">${fgStr}</span>
              <span class="text-xs text-slate-500">/ 100</span>
            </div>
            <div class="relative h-2 bg-slate-700 rounded-full overflow-hidden mb-2">
              <div class="absolute left-0 top-0 h-full rounded-full transition-all ${fgBarColor}" style="width:${fgPct}%"></div>
            </div>
            <div class="flex justify-between text-xs text-slate-600">
              <span>0 극단공포</span><span>50 중립</span><span>100 극단탐욕</span>
            </div>
            ${fg ? `<div class="flex gap-4 mt-2">
              ${fgHist('전일', fg.prevClose)}${fgHist('1주전', fg.prevWeek)}${fgHist('1달전', fg.prevMonth)}${fgHist('1년전', fg.prevYear)}
            </div>` : ''}
            ${!fg ? `<div class="text-xs text-slate-600 mt-2">로딩 실패</div>` : ''}
          </div>

        </div>
      </div>`;
  }

  // ── Regime Indicator ──────────────────────────────────────────────────────
  function _calcRegime(spreadScore, hyOasScore, tipsScore, cgRatio) {
    let score = (spreadScore || 0) + (hyOasScore || 0) + (tipsScore || 0);
    if (cgRatio != null) score += cgRatio > 0.2 ? 1 : cgRatio > 0.15 ? 0 : -1;
    // Max possible ~4, min ~-6
    // RISK_ON ≥2, NEUTRAL ≥0, CAUTION ≥-2, RISK_OFF <-2
    if (score >= 2)  return 'RISK_ON';
    if (score >= 0)  return 'NEUTRAL';
    if (score >= -2) return 'CAUTION';
    return 'RISK_OFF';
  }

  function _regimeSection(regime) {
    const map = {
      'RISK_ON':  { label: '리스크 온 (Risk-On)',   color: 'text-emerald-400', bg: 'bg-emerald-950/30 border-emerald-800/50', dot: 'bg-emerald-400', desc: '금리·신용·구리/금 모두 안정 → 성장주·위험자산 우호적 환경' },
      'NEUTRAL':  { label: '중립 (Neutral)',         color: 'text-amber-400',   bg: 'bg-amber-950/20 border-amber-800/40',     dot: 'bg-amber-400',   desc: '혼재 신호 — 섹터별 선택적 접근 필요' },
      'CAUTION':  { label: '주의 (Caution)',         color: 'text-orange-400',  bg: 'bg-orange-950/20 border-orange-800/40',   dot: 'bg-orange-400',  desc: '부정적 신호 증가 — 고위험 포지션 사이즈 축소 검토' },
      'RISK_OFF': { label: '리스크 오프 (Risk-Off)', color: 'text-red-400',     bg: 'bg-red-950/30 border-red-800/50',         dot: 'bg-red-400',     desc: '수익률 역전·신용 스프레드 확대 → 방어주·채권·금 우호적 환경' },
    };
    const r = map[regime] || map['NEUTRAL'];
    return `
      <div class="${r.bg} border rounded-xl p-4 mb-6">
        <div class="flex items-center gap-3">
          <div class="h-3 w-3 rounded-full ${r.dot} animate-pulse"></div>
          <span class="font-bold text-base ${r.color}">${r.label}</span>
          <span class="text-xs text-slate-400 ml-2">(종합 매크로 시그널)</span>
        </div>
        <p class="text-sm text-slate-400 mt-2">${r.desc}</p>
      </div>`;
  }

  // ── Rates Section ─────────────────────────────────────────────────────────
  function _ratesSection(fed, t2y, t5y, t10y, t30y, tips) {
    const { fmt } = UI;
    const v = d => d?.value != null ? d.value.toFixed(2) + '%' : '—';
    const tipsColor = tips?.value > 2.5 ? 'red' : tips?.value > 1.5 ? null : 'green';
    return `
      <div class="mb-6">
        ${UI.sectionHeader('금리 현황', '현재 수준')}
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          ${UI.kpi('Fed 기준금리', v(fed), { icon: '🏦' })}
          ${UI.kpi('2Y 국채', v(t2y))}
          ${UI.kpi('5Y 국채', v(t5y))}
          ${UI.kpi('10Y 국채', v(t10y))}
          ${UI.kpi('30Y 국채', v(t30y))}
          ${UI.kpi('TIPS 실질금리 (10Y)', v(tips), { color: tipsColor,
            sub: tips?.value > 2.5 ? '성장주 밸류에이션 압박' : '성장주 우호적' })}
        </div>
      </div>`;
  }

  // ── Yield Curve Section ───────────────────────────────────────────────────
  function _yieldCurveSection(t2y, t5y, t10y, t30y, spreadVal, change1m, change3m, spreadClass, t10y3mVal, t10y3mClass) {
    const fmtSpread = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%p';
    const fmtChg    = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%p';
    const chgColor  = v => v == null ? 'text-slate-500' : v > 0.02 ? 'text-emerald-400' : v < -0.02 ? 'text-red-400' : 'text-slate-300';
    const direction = change1m == null ? '' : change1m > 0.05 ? ' ↑ 스티프닝' : change1m < -0.05 ? ' ↓ 플래트닝' : '';
    const signalColors = { bearish: 'text-red-400', warn: 'text-amber-400', bullish: 'text-emerald-400' };

    const mixedSignal = spreadVal != null && t10y3mVal != null && ((spreadVal < 0) !== (t10y3mVal < 0));
    const bothInverted = spreadVal != null && t10y3mVal != null && spreadVal < 0 && t10y3mVal < 0;

    return `
      <div class="mb-6">
        ${UI.sectionHeader('수익률 곡선 (Yield Curve)')}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 mb-3">현재 수익률 곡선</div>
            <div style="height:160px"><canvas id="chart-yield-curve"></canvas></div>
          </div>
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 mb-2">10Y - 2Y 스프레드 추세</div>
            <div style="height:100px"><canvas id="chart-spread-history"></canvas></div>
            <div class="mt-2 grid grid-cols-3 gap-2 text-xs text-center">
              <div>
                <div class="text-slate-500 mb-0.5">현재</div>
                <div class="font-mono font-bold text-slate-200">${fmtSpread(spreadVal)}</div>
              </div>
              <div>
                <div class="text-slate-500 mb-0.5">1개월 변화</div>
                <div class="font-mono font-bold ${chgColor(change1m)}">${fmtChg(change1m)}${direction}</div>
              </div>
              <div>
                <div class="text-slate-500 mb-0.5">3개월 변화</div>
                <div class="font-mono font-bold ${chgColor(change3m)}">${fmtChg(change3m)}</div>
              </div>
            </div>
            <div class="mt-2">
              ${UI.signalBadge(spreadClass.label, spreadClass.signal)}
            </div>
            <p class="text-xs ${signalColors[spreadClass.signal] || 'text-slate-400'} mt-1 leading-relaxed">${spreadClass.msg}</p>
          </div>
        </div>
        <div class="mt-3 bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
            <span class="text-xs text-slate-400">10Y - 3M 스프레드 (경기침체 선행지표)</span>
            ${bothInverted  ? '<span class="text-xs text-red-400 font-semibold">⚠ 10Y-2Y · 10Y-3M 동시 역전 — 경기침체 선행 신호 강화</span>' : ''}
            ${mixedSignal   ? '<span class="text-xs text-amber-400 font-semibold">⚠ Mixed Signal: 10Y-2Y와 10Y-3M 방향 상이</span>' : ''}
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <span class="font-mono font-bold text-slate-200 text-sm">${fmtSpread(t10y3mVal)}</span>
            ${UI.signalBadge(t10y3mClass.label, t10y3mClass.signal)}
            <span class="text-xs text-slate-500">Recession probability 관점에서 10Y-2Y보다 중요한 보조 지표</span>
          </div>
          <p class="text-xs text-slate-500 mt-2">💡 단독 매도 신호는 아니며 HY OAS, 실질금리, 구리/금과 함께 종합 판단하세요.</p>
        </div>
      </div>`;
  }

  // ── Credit Spreads Section ────────────────────────────────────────────────
  function _creditSection(igOas, hyOas, signal) {
    const igColor = igOas > 150 ? 'red' : igOas > 100 ? null : 'green';
    const hyColor = hyOas > 700 ? 'red' : hyOas > 500 ? null : 'green';
    const hyStress = hyOas == null
      ? { label: '—', color: 'text-slate-500' }
      : hyOas < 350 ? { label: 'Credit Stress Low',    color: 'text-emerald-400' }
      : hyOas < 500 ? { label: 'Watch',                color: 'text-amber-400'   }
      : hyOas < 700 ? { label: 'Risk-Off Warning',     color: 'text-orange-400'  }
      :               { label: 'Severe Credit Stress', color: 'text-red-500'     };
    return `
      <div class="mb-6">
        ${UI.sectionHeader('신용 스프레드 (Credit Spreads)', 'OAS — 넓어질수록 위험 신호')}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <div class="grid grid-cols-2 gap-3 mb-4">
              ${UI.kpi('IG 스프레드 (OAS)', igOas != null ? igOas.toFixed(0) + 'bp' : '—', { color: igColor, sub: '> 150bp = 경보' })}
              ${UI.kpi('HY 스프레드 (OAS)', hyOas != null ? hyOas.toFixed(0) + 'bp' : '—', { color: hyColor, sub: hyStress.label })}
            </div>
            <div class="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-sm">
              <span class="${hyStress.color} font-semibold">${hyStress.label}</span>
              <span class="text-slate-400 ml-2">— HY OAS 구간: &lt;350bp 안정 · 350-500 주의 · 500-700 경보 · &gt;700 위기</span>
            </div>
          </div>
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 mb-2">신용 스프레드 추세 (1년)</div>
            <div style="height:140px"><canvas id="chart-credit-spreads"></canvas></div>
          </div>
        </div>
      </div>`;
  }

  // ── Inflation Section ─────────────────────────────────────────────────────
  function _inflationSection(cpiYoY, pceYoY, tips) {
    const cpiColor = cpiYoY > 4 ? 'red' : cpiYoY > 2.5 ? null : 'green';
    const tipsVal = tips?.value;
    const realRateColor = tipsVal > 2.5 ? 'red' : tipsVal > 1.0 ? null : 'green';
    const realRateSub = tipsVal > 2.5 ? '성장주 밸류에이션 압박' : tipsVal > 1.0 ? '중립' : '성장주 우호적';
    return `
      <div class="mb-6">
        ${UI.sectionHeader('인플레이션')}
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
          ${UI.kpi('Core CPI (YoY)', cpiYoY != null ? cpiYoY.toFixed(1) + '%' : '—',
            { color: cpiColor, sub: 'Fed 목표: 2%' })}
          ${UI.kpi('PCE YoY', pceYoY != null ? pceYoY.toFixed(1) + '%' : '—',
            { sub: 'Fed 선호 인플레이션 지표' })}
          ${UI.kpi('10Y 실질금리 (TIPS)', tipsVal != null ? tipsVal.toFixed(2) + '%' : '—',
            { color: realRateColor, sub: realRateSub })}
        </div>
      </div>`;
  }

  // ── Economic Indicators Section ───────────────────────────────────────────
  function _economicSection(unemp, consConf) {
    const unempColor = unemp?.value < 4 ? 'green' : unemp?.value < 5 ? null : 'red';
    return `
      <div class="mb-6">
        ${UI.sectionHeader('경제 지표')}
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
          ${UI.kpi('실업률', unemp?.value != null ? unemp.value.toFixed(1) + '%' : '—',
            { color: unempColor })}
          ${UI.kpi('소비자 신뢰지수 (UMich)', consConf?.value != null ? consConf.value.toFixed(1) : '—')}
          ${UI.kpi('다음 FOMC', _nextFOMC(), { icon: '📅', sub: '예정일 (추정)' })}
        </div>
      </div>`;
  }

  // ── Copper/Gold Ratio Section ─────────────────────────────────────────────
  function _copperGoldSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('구리/금 비율 (Copper/Gold Ratio)', 'PMI보다 빠른 경기 선행지표')}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 mb-2">구리/금 비율 추세 (5년)</div>
            <div style="height:160px"><canvas id="chart-copper-gold"></canvas></div>
          </div>
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-sm text-slate-400 flex flex-col justify-center gap-3">
            <div><strong class="text-emerald-400">비율 상승</strong> → 구리 강세 = 경기 확장, 위험자산 선호</div>
            <div><strong class="text-red-400">비율 하락</strong> → 금 강세 = 경기 수축, 안전자산 도피</div>
            <div class="text-xs text-slate-500">구리: 산업 수요 proxy / 금: 안전자산 / 비율 = 경기 온도계</div>
          </div>
        </div>
      </div>`;
  }

  // ── News Section ──────────────────────────────────────────────────────────
  function _newsSection() {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('시장 뉴스 (24시간)')}
        <div id="macro-news"><div class="text-slate-500 text-sm">뉴스 로딩 중...</div></div>
      </div>`;
  }

  // ── Chart rendering ───────────────────────────────────────────────────────
  function _renderYieldCurve(t2y, t5y, t10y, t30y) {
    const maturities = ['2Y', '5Y', '10Y', '30Y'];
    const yields = [t2y, t5y, t10y, t30y];
    if (yields.some(v => v == null)) return;
    CHARTS.yieldCurve('chart-yield-curve', maturities, yields);
  }

  function _renderSpreadHistory(spreadData) {
    if (!spreadData.length) return;
    const recent = spreadData.slice(-260); // ~1 year daily
    const labels = recent.map(d => d.date.slice(5)); // MM-DD
    const values = recent.map(d => d.value);
    const colors = values.map(v => v < 0 ? '#ef4444aa' : '#10b98199');
    CHARTS.bar('chart-spread-history', labels, [
      { label: '10Y-2Y', data: values, colors },
    ], {
      scales: {
        x: { ticks: { maxTicksLimit: 6 } },
        y: { ticks: { callback: v => v.toFixed(1) + '%' } },
      },
    });
  }

  function _renderCreditSpreads(igData, hyData) {
    if (!igData.length && !hyData.length) return;
    const recent = Math.min(igData.length, hyData.length, 260);
    const ig = igData.slice(-recent);
    const hy = hyData.slice(-recent);
    const labels = ig.map(d => d.date.slice(5));
    // FRED OAS in % → multiply ×100 to display as bp
    CHARTS.line('chart-credit-spreads', labels, [
      { label: 'IG OAS (bp)', data: ig.map(d => Math.round(d.value * 100)), color: '#3b82f6', pointRadius: 0, yAxis: 'y' },
      { label: 'HY OAS (bp)', data: hy.map(d => Math.round(d.value * 100)), color: '#ef4444', pointRadius: 0, yAxis: 'y1' },
    ], {
      plugins: { legend: { display: true } },
      scales: {
        x: { ticks: { maxTicksLimit: 6 } },
        y:  { ticks: { callback: v => v + 'bp' }, position: 'left' },
        y1: { ticks: { callback: v => v + 'bp' }, position: 'right', grid: { drawOnChartArea: false } },
      },
    });
  }

  function _renderCopperGold(cgData) {
    if (!cgData || !cgData.length) return;
    const recent = cgData.slice(-60); // 5 years monthly
    const labels = recent.map(d => d.date.slice(0, 7));
    const values = recent.map(d => d.ratio);
    const colors = values.map((v, i) => i === 0 ? '#94a3b8' : v > values[i-1] ? '#10b98199' : '#ef444499');
    CHARTS.bar('chart-copper-gold', labels, [
      { label: '구리/금 비율', data: values, colors },
    ], {
      scales: {
        x: { ticks: { maxTicksLimit: 8 } },
        y: { ticks: { callback: v => v.toFixed(3) } },
      },
    });
  }

  async function _renderNewsAsync() {
    const el = document.getElementById('macro-news');
    if (!el) return;
    try {
      const news = await API.marketNews('general');
      if (!news || !news.length) {
        el.innerHTML = '<p class="text-slate-500 text-sm">뉴스 없음</p>';
        return;
      }
      el.innerHTML = `
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          ${news.slice(0, 10).map(n => `
            <a href="${n.url}" target="_blank" rel="noopener"
              class="block bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 rounded-lg p-3 transition-colors">
              <div class="text-sm text-slate-200 font-medium leading-snug line-clamp-2">${n.headline}</div>
              <div class="text-xs text-slate-500 mt-1">${n.source} · ${UI.fmt.date(n.datetime)}</div>
            </a>`).join('')}
        </div>`;
    } catch (_) {}
  }

  // ── Spread classifiers ────────────────────────────────────────────────────
  function _classifySpread(spreadVal, change1m) {
    if (spreadVal == null) return { label: '—', msg: '', score: 0, signal: 'warn' };
    if (spreadVal < -0.25) return {
      label: '위험: 역전', signal: 'bearish', score: -2,
      msg: '10Y-2Y가 뚜렷하게 역전되어 경기침체/정책완화 기대가 강합니다.',
    };
    if (spreadVal < 0) return {
      label: '주의: 약한 역전', signal: 'bearish', score: -1.5,
      msg: '장단기 금리차가 음수입니다. 위험자산 비중 확대는 신중해야 합니다.',
    };
    if (spreadVal < 0.50) {
      const flattening = change1m != null && change1m < 0;
      return {
        label: flattening ? '주의: 정상이나 평탄화 중' : '중립: 낮은 양의 기울기',
        signal: 'warn', score: -0.5,
        msg: flattening
          ? '역전은 아니지만 금리차가 낮고 최근 축소(플래트닝) 중입니다.'
          : '역전은 아니지만 장단기 금리차가 낮은 상태입니다.',
      };
    }
    if (spreadVal < 1.50) return {
      label: '정상', signal: 'bullish', score: 0.5,
      msg: '장단기 금리차가 정상적인 양의 기울기입니다.',
    };
    if (spreadVal < 2.50) return {
      label: '가파름', signal: 'bullish', score: 0.3,
      msg: '수익률곡선이 가파릅니다. 경기 회복 기대 또는 장기금리 상승 요인을 함께 확인하세요.',
    };
    return {
      label: '주의: 과도한 가팔라짐', signal: 'warn', score: -0.3,
      msg: '장기금리가 과도하게 높아졌을 수 있어 성장주 밸류에이션 압박을 확인하세요.',
    };
  }

  function _classifyT10Y3M(val) {
    if (val == null) return { label: '—', signal: 'warn', score: 0 };
    if (val < -0.25) return { label: '역전 (경기침체 위험)', signal: 'bearish', score: -2 };
    if (val < 0)     return { label: '약한 역전',           signal: 'bearish', score: -1 };
    if (val < 0.50)  return { label: '평탄',                signal: 'warn',    score: -0.5 };
    return               { label: '정상',                signal: 'bullish', score: 0.5 };
  }

  // ── Helper functions ──────────────────────────────────────────────────────
  function _yoyChange(data) {
    if (!data || data.length < 13) return null;
    const latest = data[data.length - 1].value;
    const yearAgo = data[data.length - 13]?.value;
    return yearAgo ? ((latest - yearAgo) / yearAgo) * 100 : null;
  }

  function _copperGoldRatio(copperData, goldMonthly) {
    // copperData: [{date:'YYYY-MM-DD', value: USD/metric_ton}]  — FRED monthly
    // goldMonthly: [{t: unix, c: price}]                        — Yahoo Finance GLD monthly
    if (!copperData.length || !goldMonthly.length) return [];
    // Build gold month→price map from Yahoo data
    const goldMap = new Map();
    goldMonthly.forEach(g => {
      const d = new Date(g.t * 1000);
      const key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
      goldMap.set(key, g.c);
    });
    return copperData.slice(-60).map(copper => {
      const monthKey = copper.date.slice(0, 7);
      const gldPrice = goldMap.get(monthKey);
      // Copper USD/ton ÷ (GLD $/share × 10 ≈ gold per troy oz proxy)
      // Ratio is scaled so typical range is 0.1–0.5 for readability
      return {
        date: copper.date,
        ratio: gldPrice && gldPrice > 0 ? +(copper.value / gldPrice / 10).toFixed(4) : null,
      };
    }).filter(d => d.ratio != null);
  }

  function _nextFOMC() {
    // Approximate 2025-2026 FOMC dates (8 per year)
    const dates = [
      '2025-06-17', '2025-07-29', '2025-09-16', '2025-10-28',
      '2025-12-09', '2026-01-27', '2026-03-17', '2026-04-28',
      '2026-06-16', '2026-07-28', '2026-09-15',
    ];
    const today = new Date().toISOString().slice(0, 10);
    const next = dates.find(d => d > today);
    return next ? next : '—';
  }

  return { init };
})();
