// research/portfolio-engine.js — Portfolio-level calculation engine
// All pure functions: no DOM, no API calls, no side effects.
//
// Exports (via PORTFOLIO_ENGINE):
//   calcPositions(rawPositions, priceMap)       → enriched position array
//   calcTargetVsActual(positions, targets)      → comparison table
//   calcSectorExposure(positions)               → sector breakdown
//   calcThemeExposure(positions)                → theme breakdown
//   calcMonthlyPlan(recs, accountBuckets)       → per-account monthly plan
//   calcWhatIf(positions, changes, priceMap, targets) → simulated portfolio
//   calcBacktest(signalLog, priceHistory)       → backtest vs buy-and-hold
//   calcPortfolioComposite(tickerResults)       → portfolio-level composite score
//   allocationStatus(currentPct, targetPct)    → 'OVERWEIGHT'|'UNDERWEIGHT'|'ON_TARGET'
//   suggestedBuy(underweightPct, budget)        → suggested dollar amount

const PORTFOLIO_ENGINE = (() => {

  // Sector→ticker map (mirrors sectors.js TICKER_SECTOR)
  const TICKER_SECTOR = {
    AAPL:'XLK', MSFT:'XLK', NVDA:'XLK', GOOGL:'XLC', META:'XLC', AMZN:'XLY',
    TSLA:'XLY', NFLX:'XLC', TECL:'XLK', QQQ:'XLK', QQQM:null, SPY:null,
    LLY:'XLV', JNJ:'XLV', UNH:'XLV', ISRG:'XLV', ABBV:'XLV', MRNA:'XLV', NVO:'XLV',
    JPM:'XLF', BAC:'XLF', GS:'XLF', BRK:'XLF', V:'XLF', MA:'XLF',
    XOM:'XLE', CVX:'XLE', CEG:'XLE',
    CAT:'XLI', DE:'XLI', RKLB:'XLI',
    HOOD:'XLF', CRCL:'XLF', COIN:'XLF',
    METU:'XLC', GGLL:'XLC', AMZZ:'XLY',
    AMD:'XLK', INTC:'XLK', AVGO:'XLK', QCOM:'XLK', ARM:'XLK', SMCI:'XLK',
    ORCL:'XLK', CRM:'XLK', NOW:'XLK', SNOW:'XLK', PLTR:'XLK',
    TSLA:'XLY', RIVN:'XLY', LCID:'XLY',
    SPOT:'XLC', SNAP:'XLC', ABNB:'XLY', UBER:'XLY',
  };

  // GICS sector display names (Korean + English)
  // Keys match TICKER_SECTOR values; used for UI display only.
  const SECTOR_GICS = {
    XLK:  { ko: '기술',              en: 'Technology' },
    XLC:  { ko: '커뮤니케이션 서비스', en: 'Communication Services' },
    XLY:  { ko: '임의소비재',         en: 'Consumer Discretionary' },
    XLP:  { ko: '필수소비재',         en: 'Consumer Staples' },
    XLV:  { ko: '헬스케어',           en: 'Healthcare' },
    XLF:  { ko: '금융',              en: 'Financials' },
    XLI:  { ko: '산업재',            en: 'Industrials' },
    XLE:  { ko: '에너지',            en: 'Energy' },
    XLB:  { ko: '소재',              en: 'Materials' },
    XLU:  { ko: '유틸리티',          en: 'Utilities' },
    XLRE: { ko: '부동산',            en: 'Real Estate' },
  };

  // Rich theme map — each entry: { labelKo, detail, leverage?, underlying? }
  // Unmapped tickers fall back to '미분류'.
  const THEME_MAP = {
    // ── 현금·채권 ────────────────────────────────────────────────────────────────
    '912828ZN3': { labelKo: '현금·채권',           detail: '미국채 T-Bill' },
    '채권':      { labelKo: '현금·채권',           detail: '채권 포지션' },
    BIL:         { labelKo: '현금·채권',           detail: 'T-Bill ETF' },
    SHY:         { labelKo: '현금·채권',           detail: '단기 국채 ETF' },
    TLT:         { labelKo: '현금·채권',           detail: '장기 국채 ETF' },
    AGG:         { labelKo: '현금·채권',           detail: '채권 종합 ETF' },
    // ── 광범위 지수 ETF ─────────────────────────────────────────────────────────
    QQQM:        { labelKo: '광범위 성장 ETF',      detail: 'Nasdaq-100' },
    QQQ:         { labelKo: '광범위 성장 ETF',      detail: 'Nasdaq-100' },
    SPY:         { labelKo: '광범위 지수 ETF',      detail: 'S&P 500' },
    IVV:         { labelKo: '광범위 지수 ETF',      detail: 'S&P 500' },
    VTI:         { labelKo: '광범위 지수 ETF',      detail: '미국 전체 시장' },
    // ── 레버리지 ETF (3×) ───────────────────────────────────────────────────────
    TECL:        { labelKo: '레버리지 기술 ETF',    leverage: 3, detail: 'XLK 3×' },
    TQQQ:        { labelKo: '레버리지 성장 ETF',    leverage: 3, detail: 'QQQ 3×' },
    SOXL:        { labelKo: '레버리지 반도체 ETF',  leverage: 3, detail: '반도체지수 3×' },
    UPRO:        { labelKo: '레버리지 S&P500 ETF',  leverage: 3, detail: 'SPY 3×' },
    SPXL:        { labelKo: '레버리지 S&P500 ETF',  leverage: 3, detail: 'SPY 3×' },
    LABU:        { labelKo: '레버리지 바이오 ETF',  leverage: 3, detail: '바이오텍 3×' },
    // ── 단일종목 레버리지 (2×) ────────────────────────────────────────────────────
    METU:        { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'META',  detail: 'META 2×' },
    GGLL:        { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'GOOGL', detail: 'GOOGL 2×' },
    AMZZ:        { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'AMZN',  detail: 'AMZN 2×' },
    TSLL:        { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'TSLA',  detail: 'TSLA 2×' },
    NVDL:        { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'NVDA',  detail: 'NVDA 2×' },
    MSFT2:       { labelKo: '단일종목 레버리지',     leverage: 2, underlying: 'MSFT',  detail: 'MSFT 2×' },
    // ── 클라우드·AI ──────────────────────────────────────────────────────────────
    MSFT:        { labelKo: '클라우드·AI',          detail: 'Cloud & Enterprise AI' },
    GOOGL:       { labelKo: '클라우드·AI',          detail: 'Search & Cloud' },
    GOOG:        { labelKo: '클라우드·AI',          detail: 'Search & Cloud (Class C)' },
    AMZN:        { labelKo: '클라우드·AI',          detail: 'AWS & E-commerce' },
    ORCL:        { labelKo: '클라우드·AI',          detail: 'Cloud DB & ERP' },
    CRM:         { labelKo: '클라우드·AI',          detail: 'Enterprise CRM' },
    NOW:         { labelKo: '클라우드·AI',          detail: 'Enterprise Workflow' },
    SNOW:        { labelKo: '클라우드·AI',          detail: 'Data Cloud' },
    PLTR:        { labelKo: '클라우드·AI',          detail: 'AI Analytics' },
    // ── 소비자 인터넷 ────────────────────────────────────────────────────────────
    META:        { labelKo: '소비자 인터넷',         detail: 'Social & Digital Ads' },
    NFLX:        { labelKo: '소비자 인터넷',         detail: 'Streaming' },
    SPOT:        { labelKo: '소비자 인터넷',         detail: 'Music Streaming' },
    SNAP:        { labelKo: '소비자 인터넷',         detail: 'Social Media' },
    PINS:        { labelKo: '소비자 인터넷',         detail: 'Social Commerce' },
    UBER:        { labelKo: '소비자 인터넷',         detail: 'Ride-sharing' },
    ABNB:        { labelKo: '소비자 인터넷',         detail: 'Home Sharing' },
    // ── 반도체·AI ────────────────────────────────────────────────────────────────
    NVDA:        { labelKo: '반도체·AI',            detail: 'GPU & AI Accelerator' },
    AMD:         { labelKo: '반도체·AI',            detail: 'CPU & GPU' },
    AVGO:        { labelKo: '반도체·AI',            detail: 'Networking & Custom Silicon' },
    ARM:         { labelKo: '반도체·AI',            detail: 'CPU Architecture' },
    QCOM:        { labelKo: '반도체·AI',            detail: 'Mobile & IoT Chips' },
    INTC:        { labelKo: '반도체·AI',            detail: 'x86 Processor' },
    SMCI:        { labelKo: '반도체·AI',            detail: 'AI Server' },
    TSM:         { labelKo: '반도체·AI',            detail: 'Contract Foundry' },
    // ── 빅테크 하드웨어 ──────────────────────────────────────────────────────────
    AAPL:        { labelKo: '빅테크 하드웨어',       detail: 'iPhone & Services' },
    // ── 핀테크 ──────────────────────────────────────────────────────────────────
    HOOD:        { labelKo: '핀테크',              detail: 'Retail Brokerage' },
    V:           { labelKo: '핀테크',              detail: 'Payment Network' },
    MA:          { labelKo: '핀테크',              detail: 'Payment Network' },
    // ── 코인·크립토 인프라 ─────────────────────────────────────────────────────────
    CRCL:        { labelKo: '코인·크립토',          detail: 'Stablecoin' },
    COIN:        { labelKo: '코인·크립토',          detail: 'Crypto Exchange' },
    MSTR:        { labelKo: '코인·크립토',          detail: 'BTC Treasury' },
    BITF:        { labelKo: '코인·크립토',          detail: 'BTC Mining' },
    // ── 바이오·제약 ──────────────────────────────────────────────────────────────
    LLY:         { labelKo: '바이오·제약',          detail: 'Obesity & Diabetes' },
    NVO:         { labelKo: '바이오·제약',          detail: 'Obesity & Diabetes (DK)' },
    ABBV:        { labelKo: '바이오·제약',          detail: 'Immunology' },
    MRNA:        { labelKo: '바이오·제약',          detail: 'mRNA Vaccine' },
    JNJ:         { labelKo: '바이오·제약',          detail: 'Diversified Pharma' },
    // ── 의료기기 ────────────────────────────────────────────────────────────────
    ISRG:        { labelKo: '의료기기',             detail: 'Robotic Surgery' },
    UNH:         { labelKo: '의료기기',             detail: 'Managed Care' },
    // ── 우주·항공산업 ────────────────────────────────────────────────────────────
    RKLB:        { labelKo: '우주·항공산업',         detail: 'Launch & Space Systems' },
    SPCE:        { labelKo: '우주·항공산업',         detail: 'Space Tourism' },
    ASTR:        { labelKo: '우주·항공산업',         detail: 'Small Launch' },
    RDW:         { labelKo: '우주·항공산업',         detail: 'Space Manufacturing' },
    ASTS:        { labelKo: '우주·항공산업',         detail: 'Space-based Broadband' },
    // ── EV·모빌리티 ──────────────────────────────────────────────────────────────
    TSLA:        { labelKo: 'EV·모빌리티',          detail: 'Electric Vehicle & Energy' },
    RIVN:        { labelKo: 'EV·모빌리티',          detail: 'Electric Truck' },
    LCID:        { labelKo: 'EV·모빌리티',          detail: 'EV Luxury' },
    // ── 에너지·원자력 ────────────────────────────────────────────────────────────
    CEG:         { labelKo: '에너지·원자력',         detail: 'Nuclear & Clean Power' },
    NEE:         { labelKo: '에너지·원자력',         detail: 'Wind & Solar Utility' },
    XOM:         { labelKo: '에너지·원자력',         detail: 'Integrated Oil' },
    CVX:         { labelKo: '에너지·원자력',         detail: 'Integrated Oil' },
    // ── 전통 금융 ────────────────────────────────────────────────────────────────
    JPM:         { labelKo: '전통 금융',             detail: 'Banking' },
    BAC:         { labelKo: '전통 금융',             detail: 'Banking' },
    GS:          { labelKo: '전통 금융',             detail: 'Investment Banking' },
    BRK:         { labelKo: '전통 금융',             detail: 'Diversified Conglomerate' },
  };

  // 2× single-stock leveraged ETFs
  const LEVERAGE_SINGLE = { METU:'META', GGLL:'GOOGL', AMZZ:'AMZN' };
  // 3× broad ETFs
  const LEVERAGE_3X = ['TECL','TQQQ','SOXL','UPRO','SPXL'];

  // User-defined theme overrides (persisted in localStorage under 'wr_theme_overrides')
  let _themeOverrides = {};
  try { _themeOverrides = JSON.parse(localStorage.getItem('wr_theme_overrides') || '{}'); } catch(_) {}

  function setThemeOverride(ticker, theme) {
    const key = ticker.toUpperCase();
    if (theme) _themeOverrides[key] = theme;
    else delete _themeOverrides[key];
    try { localStorage.setItem('wr_theme_overrides', JSON.stringify(_themeOverrides)); } catch(_) {}
  }

  // ── Enrich raw positions with current value, P&L, etc. ────────────────────
  function calcPositions(rawPositions, priceMap = {}) {
    if (!Array.isArray(rawPositions)) return [];
    return rawPositions.map(p => {
      const ticker = (p.ticker || '').toUpperCase();
      const shares = parseFloat(p.shares) || 0;
      const costBasis = parseFloat(p.costBasis)
        || (parseFloat(p.avgCost) * shares)
        || 0;
      const avgCost = shares > 0 ? costBasis / shares : 0;
      const price   = priceMap[ticker] || parseFloat(p.currentPrice) || 0;
      const currentValue = shares * price;
      const pnl    = currentValue - costBasis;
      const pnlPct = costBasis > 0 ? pnl / costBasis * 100 : 0;
      const purchaseDate = p.purchaseDate ? new Date(p.purchaseDate) : null;
      const daysHeld = purchaseDate ? (Date.now() - purchaseDate.getTime()) / 86400000 : null;
      const isLongTerm = daysHeld != null && daysHeld >= 365;

      return {
        ticker, shares, costBasis, avgCost, price, currentValue,
        pnl, pnlPct,
        account: p.account || '',
        accountType: p.accountType || '',
        purchaseDate: p.purchaseDate || null,
        daysHeld, isLongTerm,
        sector: TICKER_SECTOR[ticker] || 'UNKNOWN',
        theme: _themeOverrides[ticker] || THEME_MAP[ticker]?.labelKo || '미분류',
        isLeverage2x: !!LEVERAGE_SINGLE[ticker],
        isLeverage3x: LEVERAGE_3X.includes(ticker),
        leverageUnderlying: LEVERAGE_SINGLE[ticker] || null,
      };
    }).filter(p => p.shares > 0);
  }

  // ── Target vs Actual comparison ───────────────────────────────────────────
  function calcTargetVsActual(positions, targets = {}) {
    const totalValue = positions.reduce((s, p) => s + p.currentValue, 0);

    // Group positions by ticker (multiple lots)
    const posMap = {};
    positions.forEach(p => {
      if (!posMap[p.ticker]) posMap[p.ticker] = { currentValue: 0, costBasis: 0, pnl: 0, ...p };
      else {
        posMap[p.ticker].currentValue += p.currentValue;
        posMap[p.ticker].costBasis    += p.costBasis;
        posMap[p.ticker].pnl          += p.pnl;
      }
    });

    const allTickers = new Set([...Object.keys(targets), ...Object.keys(posMap)]);
    const rows = [];
    allTickers.forEach(ticker => {
      const target     = targets[ticker] || 0;
      const currentVal = posMap[ticker]?.currentValue || 0;
      const currentPct = totalValue > 0 ? currentVal / totalValue * 100 : 0;
      const gap        = currentPct - target;
      rows.push({
        ticker,
        target,
        currentPct: +currentPct.toFixed(2),
        currentValue: currentVal,
        gap: +gap.toFixed(2),
        status: allocationStatus(currentPct, target),
        pnl: posMap[ticker]?.pnl || 0,
        pnlPct: posMap[ticker]?.costBasis > 0
          ? posMap[ticker].pnl / posMap[ticker].costBasis * 100 : 0,
        price: posMap[ticker]?.price || 0,
        shares: posMap[ticker]?.shares || 0,
      });
    });

    return {
      rows: rows.sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap)),
      totalValue,
      overweight:  rows.filter(r => r.status === 'OVERWEIGHT').map(r => r.ticker),
      underweight: rows.filter(r => r.status === 'UNDERWEIGHT').map(r => r.ticker),
    };
  }

  // ── Sector exposure (effective, accounting for leverage) ──────────────────
  function calcSectorExposure(positions) {
    const exposure = {};
    const pairs = {};
    let totalEffective = 0;

    positions.forEach(p => {
      const { ticker, currentValue, isLeverage2x, isLeverage3x, leverageUnderlying, sector } = p;
      let effValue = currentValue;
      if (isLeverage2x) effValue = currentValue * 2;
      else if (isLeverage3x) effValue = currentValue * 3;

      if (sector !== 'UNKNOWN' && sector !== null) {
        exposure[sector] = (exposure[sector] || 0) + effValue;
      }
      totalEffective += effValue;

      // Track pairs for leverage pairing display
      if (isLeverage2x && leverageUnderlying) {
        if (!pairs[leverageUnderlying]) pairs[leverageUnderlying] = { nomValue: 0, leveraged: [] };
        pairs[leverageUnderlying].leveraged.push({
          ticker, nomValue: currentValue, effValue, mult: 2,
        });
      } else if (leverageUnderlying === null && TICKER_SECTOR[ticker] !== null) {
        // Could be underlying
        if (pairs[ticker]) pairs[ticker].nomValue += currentValue;
      }
    });

    const entries = Object.entries(exposure)
      .map(([sym, val]) => ({ sym, value: val, pct: totalEffective > 0 ? val / totalEffective * 100 : 0 }))
      .sort((a, b) => b.value - a.value);

    return { entries, totalEffective, pairs };
  }

  // ── Theme exposure ────────────────────────────────────────────────────────
  function calcThemeExposure(positions) {
    const themeMap = {};
    const totalValue = positions.reduce((s, p) => s + p.currentValue, 0);
    positions.forEach(p => {
      const t = p.theme;
      if (!themeMap[t]) themeMap[t] = { value: 0, tickers: [] };
      themeMap[t].value += p.currentValue;
      if (!themeMap[t].tickers.includes(p.ticker)) themeMap[t].tickers.push(p.ticker);
    });
    return Object.entries(themeMap)
      .map(([theme, d]) => ({ theme, value: d.value, pct: totalValue > 0 ? d.value / totalValue * 100 : 0, tickers: d.tickers }))
      .sort((a, b) => b.value - a.value);
  }

  // ── Monthly contribution plan (same logic as optimizer, but read-only) ─────
  function calcMonthlyPlan(recs, accountBuckets) {
    const plan = {};
    Object.entries(accountBuckets || {}).forEach(([key, bucket]) => {
      const bucketRecs = (recs || []).filter(r => bucket.tickers.includes(r.ticker));
      plan[key] = {
        label: bucket.label,
        budget: bucket.budget,
        allocations: bucketRecs
          .filter(r => r.allocatedDollars > 0)
          .map(r => ({
            ticker: r.ticker,
            dollars: r.allocatedDollars,
            shares: r.allocatedShares,
            state: r.state,
          }))
          .sort((a, b) => b.dollars - a.dollars),
      };
    });
    return plan;
  }

  // ── What-if simulator ─────────────────────────────────────────────────────
  // changes: [{ ticker, addShares }] (can be negative to simulate sell)
  function calcWhatIf(positions, changes, priceMap, targets = {}) {
    // Clone + apply changes
    const modified = positions.map(p => ({ ...p }));
    (changes || []).forEach(ch => {
      const existing = modified.find(p => p.ticker === ch.ticker);
      const price = priceMap[ch.ticker] || 0;
      if (existing) {
        existing.shares = Math.max(0, existing.shares + ch.addShares);
        existing.currentValue = existing.shares * price;
      } else if (ch.addShares > 0) {
        modified.push({
          ticker: ch.ticker, shares: ch.addShares, currentValue: ch.addShares * price,
          costBasis: ch.addShares * price, pnl: 0, pnlPct: 0, price,
          sector: TICKER_SECTOR[ch.ticker] || 'UNKNOWN',
          theme: _themeOverrides[ch.ticker] || THEME_MAP[ch.ticker]?.labelKo || '미분류',
        });
      }
    });

    return calcTargetVsActual(modified.filter(p => p.currentValue > 0), targets);
  }

  // ── Backtest engine (no look-ahead) ───────────────────────────────────────
  // signalLog: from REC_LOG.recent() or REC_LOG.forTicker()
  // priceHistory: { TICKER: [{t, c}, ...] }  (sorted by time ascending)
  function calcBacktest(signalLog, priceHistory) {
    const results = [];
    const initialCapital = 10000;

    (signalLog || []).forEach(entry => {
      const hist = (priceHistory?.[entry.ticker] || []).sort((a, b) => a.t - b.t);
      if (hist.length < 10) return;

      // Entry: next bar after signal
      const signalTs = entry.asOf / 1000;
      const entryIdx = hist.findIndex(c => c.t > signalTs);
      if (entryIdx < 1) return;

      const entryPrice = hist[entryIdx].c;
      const shares = Math.floor(initialCapital / entryPrice);
      if (shares < 1) return;

      // Strategy: hold 20 trading days (≈ 1 month)
      const exitIdx = Math.min(entryIdx + 20, hist.length - 1);
      const exitPrice = hist[exitIdx].c;

      // Buy-and-hold comparison: hold from same entry to same exit
      const strategyRet = (exitPrice - entryPrice) / entryPrice;

      // Evaluate: was the signal directionally correct?
      const bullishStates = new Set(['TRIGGER','ADD','SETUP','BASE_BUILDING']);
      const bearishStates = new Set(['AVOID','REDUCE','EXIT']);
      let correct = null;
      if (bullishStates.has(entry.state)) correct = strategyRet > 0.01;
      if (bearishStates.has(entry.state)) correct = strategyRet < -0.01;

      results.push({
        id: entry.id,
        ticker: entry.ticker,
        state: entry.state,
        signalDate: new Date(entry.asOf).toISOString().split('T')[0],
        entryPrice,
        exitPrice,
        holdDays: exitIdx - entryIdx,
        strategyRet: +strategyRet.toFixed(4),
        correct,
        compositeScore: entry.compositeScore,
        dqScore: entry.dataQualityScore,
      });
    });

    // Aggregate stats
    const n = results.length;
    const wins = results.filter(r => r.correct === true).length;
    const losses = results.filter(r => r.correct === false).length;
    const avgRet = n ? results.reduce((s, r) => s + r.strategyRet, 0) / n : 0;
    // High DQ only
    const hiDQ = results.filter(r => (r.dqScore || 0) >= 70);
    const hiDQWins = hiDQ.filter(r => r.correct === true).length;

    return {
      trades: results,
      n,
      winRate: n ? +(wins / (wins + losses) * 100).toFixed(1) : null,
      avgReturn: +avgRet.toFixed(4),
      highDQ: {
        n: hiDQ.length,
        winRate: hiDQ.length ? +(hiDQWins / hiDQ.length * 100).toFixed(1) : null,
      },
    };
  }

  // ── Portfolio-level Composite Score ───────────────────────────────────────
  // Weighted average of per-ticker composite scores (by current value)
  function calcPortfolioComposite(tickerResults, positions) {
    if (!tickerResults || !positions?.length) return null;
    const totalVal = positions.reduce((s, p) => s + p.currentValue, 0);
    if (!totalVal) return null;
    let weightedSum = 0, totalWeight = 0;
    positions.forEach(p => {
      const r = tickerResults[p.ticker];
      if (r?.compositeScore != null) {
        const weight = p.currentValue / totalVal;
        weightedSum += r.compositeScore * weight;
        totalWeight += weight;
      }
    });
    return totalWeight > 0 ? Math.round(weightedSum / totalWeight) : null;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function allocationStatus(currentPct, targetPct) {
    const gap = currentPct - targetPct;
    if (gap > 3)  return 'OVERWEIGHT';
    if (gap < -3) return 'UNDERWEIGHT';
    return 'ON_TARGET';
  }

  function suggestedBuy(underweightPct, totalValue, budget) {
    if (underweightPct <= 0) return 0;
    const dollarGap = underweightPct / 100 * totalValue;
    return Math.min(dollarGap, budget);
  }

  // ── Leverage effective exposure summary ────────────────────────────────────
  // Returns { totalNominal, totalEffective, totalPortfolio, effectivePct, byUnderlying[] }
  // byUnderlying sorted by totalEffective desc
  function calcLeverageExposure(positions) {
    const totalPortfolio = positions.reduce((s, p) => s + p.currentValue, 0);
    const byUnderlying = {};
    let totalNominal  = 0;
    let totalEffective = 0;

    // Pass 1: collect leveraged ETF contributions
    positions.forEach(p => {
      const { ticker, currentValue, isLeverage2x, isLeverage3x, leverageUnderlying, sector } = p;
      if (!isLeverage2x && !isLeverage3x) return;
      const mult = isLeverage2x ? 2 : 3;
      const effValue = currentValue * mult;
      totalNominal   += currentValue;
      totalEffective += effValue;

      // Determine the display key for this leveraged ETF
      let underlyingKey;
      if (isLeverage2x && leverageUnderlying) {
        underlyingKey = leverageUnderlying; // e.g. 'META'
      } else {
        // 3× ETF: use sector GICS Korean name
        underlyingKey = SECTOR_GICS[sector]?.ko || sector || '기타 섹터';
      }

      if (!byUnderlying[underlyingKey]) {
        byUnderlying[underlyingKey] = { name: underlyingKey, directValue: 0, leveragedETFs: [], totalEffective: 0 };
      }
      byUnderlying[underlyingKey].leveragedETFs.push({ ticker, nomValue: currentValue, effValue, mult });
      byUnderlying[underlyingKey].totalEffective += effValue;
    });

    // Pass 2: add direct holdings of the underlying tickers
    positions.forEach(p => {
      if (p.isLeverage2x || p.isLeverage3x) return;
      if (byUnderlying[p.ticker]) {
        byUnderlying[p.ticker].directValue += p.currentValue;
        byUnderlying[p.ticker].totalEffective += p.currentValue;
      }
    });

    return {
      totalNominal,
      totalEffective,
      totalPortfolio,
      effectivePct: totalPortfolio > 0 ? totalEffective / totalPortfolio * 100 : 0,
      byUnderlying: Object.values(byUnderlying).sort((a, b) => b.totalEffective - a.totalEffective),
    };
  }

  return {
    calcPositions, calcTargetVsActual, calcSectorExposure, calcThemeExposure,
    calcLeverageExposure, calcMonthlyPlan, calcWhatIf, calcBacktest,
    calcPortfolioComposite, allocationStatus, suggestedBuy,
    setThemeOverride,
    TICKER_SECTOR, THEME_MAP, LEVERAGE_SINGLE, LEVERAGE_3X, SECTOR_GICS,
  };
})();
