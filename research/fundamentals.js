// research/fundamentals.js — Fundamentals analysis tab
// P/E, ROIC, DCF, Piotroski, Altman Z, Earnings History, Insider Transactions

const FUNDAMENTALS = (() => {
  const CONTAINER = 'tab-fundamentals';
  let _ticker = null;

  async function init(ticker) {
    _ticker = ticker;
    if (!ticker) {
      _renderNoTicker();
      return;
    }
    UI.loading(CONTAINER, ticker + ' 펀더멘털 분석 중...');
    try {
      await _render(ticker);
    } catch (e) {
      console.error('Fundamentals error:', e);
      UI.error(CONTAINER, '데이터 로딩 실패: ' + e.message);
    }
  }

  function _renderNoTicker() {
    document.getElementById(CONTAINER).innerHTML = `
      <div class="flex flex-col items-center justify-center py-24 gap-4 text-slate-500">
        <svg class="h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
        </svg>
        <p class="text-base">위에서 종목을 검색하세요</p>
        <p class="text-sm text-slate-600">예: AAPL, MSFT, NVDA, TECL</p>
      </div>`;
  }

  async function _render(ticker) {
    // Fetch in parallel
    const [q, prof, metrics, staticFund, earn, insider] = await Promise.allSettled([
      API.quote(ticker),
      API.profile(ticker),
      API.keyMetrics(ticker),
      _staticFundamentals(ticker),
      API.earnings(ticker),
      API.insiderTransactions(ticker),
    ]);

    const quote   = q.status === 'fulfilled'       ? q.value   : null;
    const profile = prof.status === 'fulfilled'    ? prof.value : null;
    const m       = _mergeMetrics(
      metrics.status === 'fulfilled' ? metrics.value : null,
      staticFund.status === 'fulfilled' ? staticFund.value : null,
      quote
    );
    const earns   = earn.status === 'fulfilled'    ? earn.value : null;
    const insiders = insider.status === 'fulfilled' ? insider.value : [];

    // Update ticker header
    UI.renderTickerHeader(ticker, {
      logo:      profile?.logo,
      name:      profile?.name,
      sector:    profile?.sector,
      changePct: quote?.changePercent,
    }, STATE.myPosition(ticker));

    const pos = STATE.myPosition(ticker);
    const price = quote?.price;
    const dcf = m?.dcfValue;
    const upside = dcf && price ? ((dcf - price) / price) * 100 : null;

    // Build HTML
    const el = document.getElementById(CONTAINER);
    el.innerHTML = `
      ${_heroSection(quote, m, profile, pos, upside, dcf)}
      ${_sourceBanner(m)}
      ${_valuationSection(m, quote)}
      ${_afterTaxSection(m, quote)}
      ${_qualitySection(m)}
      ${_financialHealthSection(m)}
      ${_growthSection(m)}
      ${_earningsSection(earns)}
      ${_dcfSection(price, m)}
      ${_insiderSection(insiders)}
      ${_newsSection(ticker)}`;

    // Render charts after DOM update
    _renderGrossMarginChart(m);
    _renderRevenueChart(m);
    _renderEarningsChart(earns);
    _renderInsiderChart(insiders);
    await _renderNewsAsync(ticker);
  }

  function _sourceBanner(m) {
    if (!m?.staticFundamentals && !m?.sourceWarning) return '';
    const stat = m.staticFundamentals || {};
    const src = [m.fundamentalSource, stat.fiscal_period, stat.usable_at ? 'usable ' + stat.usable_at : null]
      .filter(Boolean).join(' · ');
    const warning = m.sourceWarning
      ? `<div class="text-amber-300 mt-1">${m.sourceWarning}</div>`
      : '';
    return `
      <div class="mb-4 rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-2 text-xs text-slate-400">
        <span class="text-slate-300 font-semibold">Fundamental source:</span> ${src || 'static snapshot'}
        ${warning}
      </div>`;
  }

  async function _staticFundamentals(ticker) {
    const tk = ticker?.toUpperCase?.();
    if (!tk) return null;
    // 1) static snapshot view — fast, covers the daily deep-scan set (~50 names)
    if (typeof DB !== 'undefined' && typeof DB.view === 'function') {
      try {
        const view = await DB.view('latest_fundamentals');
        const f = view?.fundamentals?.[tk];
        if (f) return f;
      } catch (_) { /* fall through to on-demand */ }
    }
    // 2) on-demand SEC fundamentals for ANY other ticker (Local Mode, no API key).
    //    SEC EDGAR companyfacts is free → ROIC/FCF/margins/P-multiples computed live.
    try {
      const res = await fetch('/api/fundamentals?ticker=' + encodeURIComponent(tk),
                              { headers: { Accept: 'application/json' } });
      if (res.ok) {
        const item = await res.json();
        if (item && item.available) return item;
      }
    } catch (_) { /* no server (Static site) → Finnhub-only, current behavior */ }
    return null;
  }

  function _mergeMetrics(live, stat, quote) {
    const out = { ...(live || {}) };
    if (!stat) return live || null;

    const hasSyntheticSnapshotPrice = !!stat.valuation_warning;
    const price = quote?.price || (hasSyntheticSnapshotPrice ? null : stat.market_price);
    const shares = stat.shares_out;
    const marketCap = price && shares ? price * shares : null;
    const peFromSec = marketCap && stat.net_income > 0 ? marketCap / stat.net_income : null;
    const psFromSec = marketCap && stat.revenue > 0 ? marketCap / stat.revenue : null;
    const pfcfFromSec = marketCap && stat.fcf > 0 ? marketCap / stat.fcf : null;
    const pbFromSec = marketCap && stat.total_equity > 0 ? marketCap / stat.total_equity : null;

    const fill = (key, value) => {
      if (out[key] == null && value != null && Number.isFinite(value)) out[key] = value;
    };
    fill('pe', stat.pe_ttm ?? peFromSec);
    fill('ps', stat.ps_ttm ?? psFromSec);
    fill('priceToFcf', stat.pfcf_ttm ?? pfcfFromSec);
    fill('pb', stat.pb_ttm ?? pbFromSec);
    fill('peg', stat.peg_ttm);
    fill('roic', stat.roic);
    fill('roe', stat.roe);
    fill('grossMargin', stat.gross_margin);
    fill('operatingMargin', stat.operating_margin);
    fill('fcfMargin', stat.fcf_margin);
    fill('netMargin', stat.net_margin);
    fill('revGrowth', stat.revenue_growth_yoy);
    fill('revenue', stat.revenue);
    fill('netIncome', stat.net_income);
    fill('fcf', stat.fcf);
    if (out.marketCap == null && marketCap != null) out.marketCap = marketCap;
    out.staticFundamentals = stat;
    out.fundamentalSource = stat.source || stat.ratio_source || 'static snapshot';
    out.fundamentalUsableAt = stat.usable_at;
    out.fundamentalCoverage = stat.coverage_ratio;
    // Dividend: per-share TTM (price-independent) + snapshot yield fallback (§33)
    if (out.ttmDividend == null && stat.ttm_dividend != null) out.ttmDividend = stat.ttm_dividend;
    if (out.dividendYield == null && stat.dividend_yield != null) out.dividendYield = stat.dividend_yield;
    out.peerValuation = stat.peer_valuation || null;
    out.themes = stat.themes || [];
    out.peSource = out.pe != null
      ? (live?.pe != null ? 'Finnhub metric' : 'SEC TTM + live price')
      : '';
    out.pegSource = stat.peg_source || (out.peg != null ? 'PEG 대체값' : '');
    out.roicSource = out.roic != null
      ? `${stat.source || 'provider'} · ${stat.fiscal_period || ''}`.trim()
      : '';
    out.sourceWarning = stat.valuation_warning
      ? (quote?.price
          ? '스냅샷 가격 이력은 synthetic이지만, 표시된 P/E·P/S·P/FCF는 실시간 quote 가격과 SEC TTM 재무값으로 재계산했습니다.'
          : '스냅샷 가격 이력이 synthetic이라 P/E·P/S·P/FCF는 실시간 quote 가격이 있을 때만 표시합니다.')
      : null;
    // SEC-based composite health (Task 3): scalar for the existing meter +
    // the full object so the UI can show honest partial/N-A availability.
    if (out.piotroski == null && stat.piotroski) out.piotroski = stat.piotroski.f_score;
    out.piotroskiDetail = stat.piotroski || null;
    if (out.altmanZ == null && stat.altman_z) out.altmanZ = stat.altman_z.z_score;  // null when N/A
    out.altmanZDetail = stat.altman_z || null;
    out.financialTrends = stat.trends || null;
    return out;
  }

  // ── Hero Section ──────────────────────────────────────────────────────────
  function _heroSection(quote, m, profile, pos, upside, dcf) {
    const { fmt } = UI;
    const changeColor = (quote?.changePercent || 0) > 0 ? 'text-emerald-400' : 'text-red-400';
    const uptxt = upside != null ? `${upside > 0 ? '▲' : '▼'} ${Math.abs(upside).toFixed(1)}% ${upside > 0 ? '저평가' : '고평가'}` : '—';
    const uptColor = upside > 0 ? 'text-emerald-400' : upside < 0 ? 'text-red-400' : 'text-slate-400';

    let posHtml = '';
    if (pos) {
      const avgCost   = pos.avgCost;      // per-share average cost
      const costBasis = pos.costBasis;    // total cost basis (avgCost × shares)
      const currentVal = (quote?.price || 0) * pos.shares;
      const gain    = costBasis > 0 && quote?.price != null ? currentVal - costBasis : null;
      const gainPct = costBasis > 0 && gain != null ? (gain / costBasis) * 100 : null;
      const gainColor = gain > 0 ? 'text-emerald-400' : gain < 0 ? 'text-red-400' : 'text-slate-400';
      posHtml = `
        <div class="bg-emerald-950/30 border border-emerald-800/50 rounded-xl p-4 mb-4">
          <div class="text-xs text-emerald-400 font-medium mb-2">📦 내 포지션</div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div><span class="text-slate-400 text-xs">보유 주수</span><br><span class="text-white font-semibold">${fmt.shares(pos.shares)}주</span></div>
            <div><span class="text-slate-400 text-xs">평균 단가</span><br><span class="text-white font-semibold">${fmt.dollar(avgCost, 2)}</span></div>
            <div><span class="text-slate-400 text-xs">평가금액</span><br><span class="text-white font-semibold">${fmt.dollar(currentVal)}</span></div>
            <div><span class="text-slate-400 text-xs">평가손익</span><br><span class="${gainColor} font-semibold">${fmt.dollar(gain, 0)} (${fmt.pct(gainPct)})</span></div>
          </div>
        </div>`;
    }

    const ticker = STATE.getTicker();
    const logoHtml = profile?.logo
      ? `<img src="${profile.logo}" alt="${ticker}" class="w-10 h-10 rounded-xl object-contain bg-slate-700/50 p-0.5 shrink-0" onerror="this.style.display='none'">`
      : `<div class="w-10 h-10 rounded-xl bg-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shrink-0">${ticker.slice(0,2)}</div>`;

    return `
      <div class="mb-6">
        <div class="flex items-center gap-5 mb-4 flex-wrap">
          <div class="flex items-end gap-3 flex-wrap">
            <span class="text-4xl font-bold text-white">${quote?.price ? '$' + quote.price.toFixed(2) : '—'}</span>
            <span class="${changeColor} text-lg font-semibold">
              ${quote?.change >= 0 ? '+' : ''}${quote?.change?.toFixed(2) || '—'}
              (${fmt.pct(quote?.changePercent, 2)})
            </span>
          </div>
          <div class="flex items-center gap-2.5 ml-2">
            ${logoHtml}
            <div>
              <div class="text-slate-200 text-sm font-semibold leading-tight">${profile?.name || ticker}</div>
              <div class="text-slate-500 text-xs">${ticker}${profile?.exchange ? ' · ' + profile.exchange : ''}</div>
            </div>
          </div>
        </div>
        ${posHtml}
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          ${UI.kpi('시가총액', fmt.dollar(quote?.marketCap || m?.marketCap))}
          ${UI.kpi('기업가치(EV)', fmt.dollar(m?.enterpriseValue))}
          ${UI.kpi('DCF 내재가치', dcf ? '$' + dcf.toFixed(2) : '—', { color: upside > 0 ? 'green' : 'red' })}
          ${UI.kpi('업사이드/다운사이드', uptxt, { color: upside > 0 ? 'green' : 'red' })}
          ${UI.kpi('52주 고가', quote?.high52w ? '$' + quote.high52w.toFixed(2) : '—')}
          ${UI.kpi('52주 저가', quote?.low52w ? '$' + quote.low52w.toFixed(2) : '—')}
        </div>
      </div>`;
  }

  // ── Valuation Section ─────────────────────────────────────────────────────
  function _valuationSection(m, quote) {
    if (!m && !quote) return '';
    const { fmt } = UI;
    const pe = m?.pe || quote?.pe;
    const pb = m?.pb || quote?.pb;
    const ps = m?.ps || quote?.ps;
    // Graham Number: √(22.5 × EPS × BVPS)
    const eps = quote?.eps;
    const bvps = pe && pe > 0 && quote?.price ? (quote.price / (pb || 1)) : null;
    const grahamNum = eps && eps > 0 && bvps && bvps > 0
      ? Math.sqrt(22.5 * eps * bvps) : null;

    const peColor = pe ? (pe < 15 ? 'green' : pe < 25 ? null : 'red') : null;
    const pegColor = m?.peg ? (m.peg < 1 ? 'green' : m.peg < 2 ? null : 'red') : null;
    // Helper: format a multiple — show '—' (not '—x') when value is null/undefined
    const fmtX = (v, dec = 1) => v != null ? fmt.num(v, dec) + 'x' : '—';
    // EV/FCF is meaningless for negative FCF — suppress
    const evFcfVal = (m?.evFcf != null && m?.fcf != null && m.fcf > 0) ? m.evFcf : null;

    // Dividend yield (TTM cash dividend / price). Prefer a live-price recompute;
    // fall back to the snapshot's static yield. NRA holders are withheld at the
    // Korea–US treaty rate (CONFIG.NRA_DIVIDEND_RATE, 15%), so show after-tax too.
    const _divPS = m?.ttmDividend;
    const _divPrice = quote?.price || m?.staticFundamentals?.market_price || null;
    const divYield = (_divPS && _divPrice) ? (_divPS / _divPrice) : (m?.dividendYield ?? null);
    const _nraRate = (typeof CONFIG !== 'undefined' && CONFIG?.NRA_DIVIDEND_RATE != null)
      ? CONFIG.NRA_DIVIDEND_RATE : 0.15;
    const divYieldStr = divYield != null ? (divYield * 100).toFixed(2) + '%' : '—';
    const divColor = divYield ? (divYield > 0.08 ? 'red' : 'green') : null;  // >8% = yield-trap flag
    const divSub = divYield != null
      ? `세후(조약 ${(_nraRate * 100).toFixed(0)}%) ${(divYield * (1 - _nraRate) * 100).toFixed(2)}%`
      : '무배당/데이터 없음';
    const sectorPe = m?.peerValuation?.sector?.pe;
    const themePe = m?.peerValuation?.themes?.[0];
    const peerFmt = p => p
      ? `${fmtX(p.median)} · ${p.discount_to_median < 0 ? '' : '+'}${(p.discount_to_median * 100).toFixed(0)}%`
      : '—';
    const peerSub = p => p
      ? `${p.group_name} · ${p.peer_count}개 · ${Math.round((p.percentile ?? 0) * 100)} percentile`
      : '무료 peer 데이터 부족';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('밸류에이션 (Valuation)', '업종 평균 대비 색상 표시')}
        <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
          ${UI.kpi('PER (TTM)', fmtX(pe), { color: peColor, sub: m?.peSource || '' })}
          ${UI.kpi('Forward P/E', fmtX(m?.forwardPe))}
          ${UI.kpi('PBR', fmtX(pb))}
          ${UI.kpi('P/S (TTM)', fmtX(ps))}
          ${UI.kpi('EV/EBITDA', fmtX(m?.evEbitda), { color: m?.evEbitda != null ? (m.evEbitda < 12 ? 'green' : m.evEbitda > 25 ? 'red' : null) : null })}
          ${UI.kpi('P/FCF', fmtX(m?.priceToFcf))}
          ${UI.kpi('EV/FCF', fmtX(evFcfVal), { sub: m?.fcf != null && m.fcf < 0 ? 'FCF 음수 — 표시 불가' : '' })}
          ${UI.kpi('PEG', m?.peg != null ? fmt.num(m.peg, 2) : '—', { color: pegColor, sub: m?.pegSource || 'Forward EPS 성장률 데이터 부족 시 미표시' })}
          ${UI.kpi('Graham Number', fmt.dollar(grahamNum, 0), { sub: quote?.price && grahamNum ? (quote.price < grahamNum ? '현재가 ≤ Graham(저평가)' : '현재가 > Graham(고평가)') : '' })}
          ${UI.kpi('배당수익률', divYieldStr, { color: divColor, sub: divSub })}
          ${UI.kpi('섹터 P/E 중앙값', peerFmt(sectorPe), { sub: peerSub(sectorPe) })}
          ${UI.kpi('테마 P/E 중앙값', peerFmt(themePe), { sub: peerSub(themePe) })}
        </div>
      </div>`;
  }

  function _afterTaxSection(m, quote) {
    if (typeof TAX_EST === 'undefined') return '';
    const { fmt } = UI;
    const _divPS = m?.ttmDividend;
    const _divPrice = quote?.price || m?.staticFundamentals?.market_price || null;
    const divYield = (_divPS && _divPrice) ? (_divPS / _divPrice) : (m?.dividendYield ?? null);
    const tax = TAX_EST.summary({ dividendYield: divYield, capitalReturn: 0.10 });
    const pct = v => v == null || !Number.isFinite(v) ? '—' : (v * 100).toFixed(2) + '%';
    return `
      <div class="mb-6">
        ${UI.sectionHeader('세후 수익 관점', '배당·자본이득을 세후로 볼 때 남는 수익률 추정')}
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          ${UI.kpi('현재 세무상태', tax.status || '—', { sub: 'CONFIG.RA_DATE 기준' })}
          ${UI.kpi('세후 배당수익률', pct(tax.afterTaxDividendYield), { sub: `배당세율 ${(tax.dividendTaxRate * 100).toFixed(0)}% 가정` })}
          ${UI.kpi('10% 상승 시 세후(LT)', pct(tax.afterTaxLongTermReturn), { sub: `장기 세율 ${(tax.longTermCapGainTaxRate * 100).toFixed(0)}% 추정` })}
          ${UI.kpi('10% 상승 시 세후(ST)', pct(tax.afterTaxShortTermReturn), { sub: `단기 세율 ${(tax.shortTermCapGainTaxRate * 100).toFixed(0)}% 추정` })}
        </div>
        <div class="mt-2 text-[11px] text-slate-500">
          실제 세금은 보유 lot, 매도일, 체류일수, 주/연방 소득구간에 따라 달라집니다. 내 포트폴리오 탭은 실제 lot 기반 세후 수익률을 별도로 계산합니다.
        </div>
      </div>`;
  }

  // ── Quality Section ───────────────────────────────────────────────────────
  function _qualitySection(m) {
    if (!m) return '';
    const { fmt } = UI;
    const fmtPct = (v, d = 1) => v == null || !Number.isFinite(v) ? '—' : fmt.pctRaw(v * 100, d);
    const roicColor = m?.roic ? (m.roic > 0.15 ? 'green' : m.roic > 0.08 ? null : 'red') : null;
    const grossColor = m?.grossMargin ? (m.grossMargin > 0.4 ? 'green' : m.grossMargin > 0.2 ? null : 'red') : null;

    return `
      <div class="mb-6">
        ${UI.sectionHeader('수익성 & 퀄리티 (Quality)')}
        <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 mb-4">
          ${UI.kpi('ROIC', fmtPct(m?.roic), { color: roicColor, sub: m?.roicSource || 'Return on Invested Capital' })}
          ${UI.kpi('ROE', fmtPct(m?.roe), { sub: 'Return on Equity' })}
          ${UI.kpi('ROA', fmtPct(m?.roa))}
          ${UI.kpi('매출총이익률', fmtPct(m?.grossMargin), { color: grossColor })}
          ${UI.kpi('영업이익률', fmtPct(m?.operatingMargin))}
          ${UI.kpi('순이익률', fmtPct(m?.netMargin))}
        </div>
        ${m?.grossMarginHistory?.length > 1 ? `
        <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div class="text-xs text-slate-400 mb-2">매출총이익률 추세 (5개년)</div>
          <div style="height:100px"><canvas id="chart-gross-margin"></canvas></div>
        </div>` : ''}
      </div>`;
  }

  // ── Financial Health Section ──────────────────────────────────────────────
  function _financialHealthSection(m) {
    if (!m) return '';
    const { fmt, scoreMeter, signalBadge } = UI;
    // Altman Z interpretation
    const z = m.altmanZ;
    const zColor = z == null ? null : z > 2.99 ? 'green' : z > 1.81 ? null : 'red';
    const zLabel = z == null ? '—' : z > 2.99 ? '안전 구간' : z > 1.81 ? '회색 구간' : '위험 구간';

    // Piotroski interpretation
    const f = m.piotroski;
    const fColor = f == null ? null : f >= 7 ? 'green' : f >= 5 ? null : 'red';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('재무 건전성')}
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700 flex flex-col gap-3">
            <div class="text-xs text-slate-400 font-medium">스코어 지표</div>
            ${f != null ? scoreMeter(f, 9, 'Piotroski F-Score', { low: 4, high: 7 })
                        : '<div class="text-xs text-slate-500">Piotroski F-Score · 데이터 없음</div>'}
            ${(() => {
              const pd = m.piotroskiDetail;
              if (pd && pd.available_signals != null && pd.available_signals < 9) {
                return `<div class="text-[10px] text-amber-400/90 -mt-1">${pd.available_signals}/9 신호만 계산 가능 (SEC generic facts — 잔액표 항목 부족)</div>`;
              }
              return '';
            })()}
            ${z != null ? `
            <div class="flex flex-col gap-1">
              <div class="flex justify-between text-xs text-slate-400 mb-0.5">
                <span>Altman Z-Score</span>
                <span class="${zColor === 'green' ? 'text-emerald-400' : zColor === 'red' ? 'text-red-400' : 'text-amber-400'} font-semibold">
                  ${z.toFixed(2)} · ${zLabel}
                </span>
              </div>
              <div class="w-full bg-slate-700 rounded-full h-2">
                <div class="h-2 rounded-full" style="width:${Math.min(z/5*100, 100)}%;background:${zColor === 'green' ? '#10b981' : zColor === 'red' ? '#ef4444' : '#f59e0b'}"></div>
              </div>
            </div>`
            : `<div class="text-[10px] text-slate-500">Altman Z-Score · 데이터 없음 (총자산·총부채·운전자본·이익잉여금 미수집)</div>`}
          </div>
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 font-medium mb-3">레버리지</div>
            <div class="space-y-2 text-sm">
              <div class="flex justify-between"><span class="text-slate-400">부채/자기자본</span><span class="text-white">${fmt.num(m.debtToEquity, 2)}</span></div>
              <div class="flex justify-between"><span class="text-slate-400">이자보상배율</span>
                <span class="${m.interestCoverage > 5 ? 'text-emerald-400' : m.interestCoverage > 3 ? 'text-amber-400' : 'text-red-400'}">
                  ${fmt.num(m.interestCoverage, 1)}x
                </span>
              </div>
              <div class="flex justify-between"><span class="text-slate-400">유동비율</span><span class="text-white">${fmt.num(m.currentRatio, 2)}</span></div>
              <div class="flex justify-between"><span class="text-slate-400">순부채</span><span class="text-white">${fmt.dollar(m.netDebt)}</span></div>
            </div>
          </div>
          <div class="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div class="text-xs text-slate-400 font-medium mb-3">현금 흐름</div>
            <div class="space-y-2 text-sm">
              <div class="flex justify-between"><span class="text-slate-400">FCF</span><span class="${m.fcf > 0 ? 'text-emerald-400' : 'text-red-400'}">${fmt.dollar(m.fcf)}</span></div>
              <div class="flex justify-between"><span class="text-slate-400">Owner's Earnings</span><span class="${m.ownersEarnings > 0 ? 'text-emerald-400' : 'text-red-400'}">${fmt.dollar(m.ownersEarnings)}</span></div>
              <div class="flex justify-between"><span class="text-slate-400">CapEx</span><span class="text-white">${fmt.dollar(m.capex)}</span></div>
              <div class="flex justify-between"><span class="text-slate-400">DSO (매출채권회전일)</span><span class="${m.dso > 60 ? 'text-amber-400' : 'text-white'}">${m.dso ? m.dso.toFixed(0) + '일' : '—'}</span></div>
            </div>
          </div>
        </div>
      </div>`;
  }

  // ── Growth Section ────────────────────────────────────────────────────────
  function _growthSection(m) {
    if (!m) return '';
    const { fmt } = UI;
    const revColor = (v) => v == null ? null : v > 0.1 ? 'green' : v > 0 ? null : 'red';
    // Show '—' for null/undefined growth values instead of '0.0%' (which looks misleading)
    const pOrDash = (v) => v != null ? fmt.pct(v * 100) : '—';

    return `
      <div class="mb-6">
        ${UI.sectionHeader('성장성 (Growth)')}
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          ${UI.kpi('매출 성장 (YoY)', pOrDash(m.revGrowth),  { color: revColor(m.revGrowth) })}
          ${UI.kpi('매출 CAGR 3Y',   pOrDash(m.revCagr3y), { color: revColor(m.revCagr3y) })}
          ${UI.kpi('EPS 성장 (YoY)', pOrDash(m.epsGrowth),  { color: revColor(m.epsGrowth) })}
          ${UI.kpi('FCF 성장',        pOrDash(m.fcfGrowth))}
        </div>
        ${m.revenueHistory?.length > 1 ? `
        <div class="mt-4 bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div class="text-xs text-slate-400 mb-2">연간 매출 추세</div>
          <div style="height:120px"><canvas id="chart-revenue"></canvas></div>
        </div>` : ''}
      </div>`;
  }

  // ── Earnings Section ──────────────────────────────────────────────────────
  function _earningsSection(earns) {
    if (!earns) return '';
    const { fmt } = UI;
    return `
      <div class="mb-6">
        ${UI.sectionHeader('실적 히스토리 (Earnings)', earns.nextDate ? '다음 발표: ' + earns.nextDate : '')}
        <div style="height:160px" class="mb-4"><canvas id="chart-earnings"></canvas></div>
        ${earns.nextDate ? `
        <div class="bg-blue-950/30 border border-blue-800/50 rounded-lg px-4 py-2 text-sm text-blue-300 inline-flex items-center gap-2">
          📅 다음 실적 발표: <strong>${earns.nextDate}</strong>
        </div>` : ''}
      </div>`;
  }

  // ── DCF Calculator Section ────────────────────────────────────────────────
  function _dcfSection(price, m) {
    if (!price) return '';
    const eps = m?.ownersEarnings && m?.shareOutstanding
      ? m.ownersEarnings / (m.shareOutstanding * 1e6)
      : null;

    // When eps is unavailable, show a disabled placeholder — no point rendering interactive sliders
    if (!eps || eps <= 0) {
      return `
        <div class="mb-6">
          ${UI.sectionHeader('DCF 계산기 (간이)', 'Owner\'s Earnings 기반 내재가치 추정')}
          <div class="bg-slate-800/50 rounded-xl p-5 border border-slate-700 opacity-60">
            <div class="text-sm text-slate-500 flex items-center gap-2">
              <span>⚠️</span>
              <span>Owner's Earnings 데이터 없음 — FMP API 키가 있어야 DCF 계산이 가능합니다.</span>
            </div>
          </div>
        </div>`;
    }

    const defaultGrowth = 10;
    const defaultDiscount = 10;
    const defaultTerminal = 15;
    const defaultYears = 10;
    return `
      <div class="mb-6">
        ${UI.sectionHeader('DCF 계산기 (간이)', 'Owner\'s Earnings 기반 내재가치 추정')}
        <div class="bg-slate-800/50 rounded-xl p-5 border border-slate-700">
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
            <label class="flex flex-col gap-1">
              <span class="text-xs text-slate-400">성장률 (1-10년)</span>
              <input id="dcf-growth" type="number" value="${defaultGrowth}" min="0" max="50" step="1"
                class="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-sm w-full" onchange="FUNDAMENTALS.calcDCF()">
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-slate-400">할인율 (WACC%)</span>
              <input id="dcf-discount" type="number" value="${defaultDiscount}" min="5" max="25" step="0.5"
                class="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-sm w-full" onchange="FUNDAMENTALS.calcDCF()">
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-slate-400">Terminal P/E 배수</span>
              <input id="dcf-terminal" type="number" value="${defaultTerminal}" min="8" max="40" step="1"
                class="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-sm w-full" onchange="FUNDAMENTALS.calcDCF()">
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-slate-400">예측 기간 (년)</span>
              <input id="dcf-years" type="number" value="${defaultYears}" min="5" max="20" step="1"
                class="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-sm w-full" onchange="FUNDAMENTALS.calcDCF()">
            </label>
          </div>
          <div id="dcf-result" class="text-sm text-slate-400">
            기준 EPS(Owner's): $${eps.toFixed(2)} | 현재가: $${price.toFixed(2)}
          </div>
        </div>
      </div>`;
  }

  function calcDCF() {
    if (!_ticker) return;
    const price = parseFloat(document.getElementById('ticker-header')?.dataset.price || 0);
    // Re-calculate from cached data
    const metrics = CACHE.get('fundamentals', _ticker + '_metrics');
    if (!metrics) return;
    const q = CACHE.get('quote', _ticker);
    const currentPrice = q?.price;
    if (!currentPrice) return;
    const eps = metrics.ownersEarnings && metrics.shareOutstanding
      ? metrics.ownersEarnings / (metrics.shareOutstanding * 1e6) : null;
    if (!eps || eps <= 0) return;

    const g = parseFloat(document.getElementById('dcf-growth').value) / 100;
    const r = parseFloat(document.getElementById('dcf-discount').value) / 100;
    const termMult = parseFloat(document.getElementById('dcf-terminal').value);
    const years = parseInt(document.getElementById('dcf-years').value, 10);

    let pv = 0;
    let e = eps;
    for (let i = 1; i <= years; i++) {
      e *= (1 + g);
      pv += e / Math.pow(1 + r, i);
    }
    const terminalValue = e * termMult / Math.pow(1 + r, years);
    const intrinsic = pv + terminalValue;
    const upside = ((intrinsic - currentPrice) / currentPrice) * 100;
    const color = upside > 0 ? 'text-emerald-400' : 'text-red-400';

    document.getElementById('dcf-result').innerHTML = `
      <div class="flex flex-wrap gap-6 items-center">
        <div>
          <span class="text-slate-400">내재가치:</span>
          <span class="${color} font-bold text-lg ml-2">$${intrinsic.toFixed(2)}</span>
        </div>
        <div>
          <span class="text-slate-400">현재가:</span>
          <span class="text-white ml-2">$${currentPrice.toFixed(2)}</span>
        </div>
        <div>
          <span class="text-slate-400">업사이드:</span>
          <span class="${color} font-bold ml-2">${upside > 0 ? '+' : ''}${upside.toFixed(1)}%</span>
          <span class="text-slate-500 ml-1">(${upside > 0 ? '저평가' : '고평가'})</span>
        </div>
        <div class="text-slate-500 text-xs">성장률 ${(g*100).toFixed(0)}% · 할인율 ${(r*100).toFixed(0)}% · Terminal P/E ${termMult}x · ${years}년</div>
      </div>`;
  }

  // ── Insider Transactions Section ──────────────────────────────────────────
  function _insiderSection(insiders) {
    if (!insiders || !insiders.length) return '';
    const { fmt } = UI;
    const buys  = insiders.filter(t => t.transactionCode === 'P');  // open-market purchase = meaningful
    const sells = insiders.filter(t => t.transactionCode === 'S');  // open-market sale (can include tax withholding)
    const rows = insiders.slice(0, 12).map(t => [
      t.name || '—',
      t.transactionCode === 'P'
        ? '<span class="text-emerald-400 font-medium">매수 (P)</span>'
        : '<span class="text-red-400">매도 (S)</span>',
      fmt.shares(Math.abs(t.change || 0)) + '주',
      t.transactionPrice ? '$' + t.transactionPrice.toFixed(2) : '—',
      fmt.date(t.transactionDate),
    ]);
    // Signal: only show bullish on cash buys; bearish only if sells are lopsided (≥3:1 vs buys)
    const signalHtml = buys.length > 0 && buys.length >= sells.length
      ? UI.signalBadge('내부자 순매수 — 강한 정렬 신호', 'bullish') + ' '
      : (sells.length >= 3 && sells.length > buys.length * 2)
      ? UI.signalBadge('내부자 대량 매도 — 주의', 'bearish') + ' '
      : UI.signalBadge('매도 다수 — 세금/스탁옵션 행사 포함 가능', 'neutral');
    return `
      <div class="mb-6">
        ${UI.sectionHeader('내부자 거래 (Form 4)', `매수 ${buys.length}건 / 매도 ${sells.length}건 (최근 6개월)`)}
        ${signalHtml}
        <div class="mt-3">
          ${UI.table(['이름', '유형', '수량', '단가', '거래일'], rows)}
        </div>
        <p class="text-xs text-slate-600 mt-2">* S코드 매도에는 세금 원천징수·옵션 행사 후 즉시 매도가 포함될 수 있습니다. P코드 현금 매수가 가장 신뢰도 높은 정렬 신호입니다.</p>
      </div>`;
  }

  // ── News placeholder (loaded async) ─────────────────────────────────────
  function _newsSection(ticker) {
    return `
      <div class="mb-6">
        ${UI.sectionHeader('최신 뉴스')}
        <div id="fundamentals-news">
          <div class="text-slate-500 text-sm">뉴스 로딩 중...</div>
        </div>
      </div>`;
  }

  async function _renderNewsAsync(ticker) {
    try {
      const news = await API.companyNews(ticker, 7);
      const el = document.getElementById('fundamentals-news');
      if (!el) return;
      if (!news || !news.length) {
        el.innerHTML = '<p class="text-slate-500 text-sm">최근 뉴스 없음</p>';
        return;
      }
      el.innerHTML = news.slice(0, 8).map(n => `
        <a href="${n.url}" target="_blank" rel="noopener"
          class="block bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 rounded-lg p-3 mb-2 transition-colors">
          <div class="text-sm text-slate-200 font-medium leading-snug">${n.headline}</div>
          <div class="text-xs text-slate-500 mt-1">${n.source} · ${UI.fmt.date(n.datetime)}</div>
        </a>`).join('');
    } catch (_) {}
  }

  // ── Chart rendering ───────────────────────────────────────────────────────
  function _renderGrossMarginChart(m) {
    if (!m?.grossMarginHistory?.length) return;
    const history = m.grossMarginHistory.filter(d => d.grossMargin != null);
    if (history.length < 2) return;
    CHARTS.line('chart-gross-margin',
      history.map(d => d.year),
      [{ label: '매출총이익률', data: history.map(d => +(d.grossMargin * 100).toFixed(1)), color: '#10b981', fill: true }],
      { scales: { y: { ticks: { callback: v => v + '%' } } } }
    );
  }

  function _renderEarningsChart(earns) {
    if (!earns?.history?.length) return;
    const h = [...earns.history].reverse().slice(-8);
    const labels = h.map(e => e.period);
    const actual = h.map(e => e.actual);
    const estimate = h.map(e => e.estimate);
    const colors = h.map(e => e.actual >= e.estimate ? '#10b981aa' : '#ef4444aa');
    CHARTS.bar('chart-earnings', labels, [
      { label: '실제 EPS', data: actual, colors },
      { label: '추정 EPS', data: estimate, color: '#3b82f680', borderRadius: 2 },
    ], { plugins: { legend: { display: true } } });
  }

  function _renderInsiderChart(insiders) {
    // Show buy/sell count bar
  }

  function _renderRevenueChart(m) {
    if (!m?.revenueHistory?.length) return;
    const history = m.revenueHistory.filter(d => d.revenue != null);
    CHARTS.bar('chart-revenue', history.map(d => d.year),
      [{ label: '매출', data: history.map(d => d.revenue), color: '#3b82f6' }],
      { scales: { y: { ticks: { callback: v => '$' + (v/1e9).toFixed(1) + 'B' } } } }
    );
  }

  return { init, calcDCF };
})();
