// research/demo.js — Demo mode: realistic mock data for all 6 tabs
// Overrides API methods with mock versions so the platform works without API keys.
// Ticker: AAPL (Apple Inc.) as the demo subject.

const DEMO_RESEARCH = (() => {
  let _active = false;

  // ── Price & candle generation ─────────────────────────────────────────────
  function _genCandles(days = 365, startPrice = 175, volatility = 0.015, trend = 0.0003) {
    const now = Math.floor(Date.now() / 1000);
    const t = [], o = [], h = [], l = [], c = [], v = [];
    let price = startPrice;
    for (let i = days; i >= 0; i--) {
      const ts = now - i * 86400;
      const dayOfWeek = new Date(ts * 1000).getDay();
      if (dayOfWeek === 0 || dayOfWeek === 6) continue; // skip weekends
      const change = (Math.random() - 0.48) * volatility + trend;
      const open  = price;
      const close = price * (1 + change);
      const high  = Math.max(open, close) * (1 + Math.random() * 0.008);
      const low   = Math.min(open, close) * (1 - Math.random() * 0.008);
      const vol   = Math.floor(60e6 + Math.random() * 40e6);
      t.push(ts); o.push(+open.toFixed(2)); h.push(+high.toFixed(2));
      l.push(+low.toFixed(2)); c.push(+close.toFixed(2)); v.push(vol);
      price = close;
    }
    return { t, o, h, l, c, v };
  }

  const _aaplCandles = _genCandles(500, 170, 0.014, 0.00035);
  const _spyCandles  = _genCandles(500, 480, 0.010, 0.00025);
  const _qqq         = _genCandles(500, 400, 0.013, 0.00030);
  const _bnd         = _genCandles(500,  76, 0.004, 0.00005);
  const _gld         = _genCandles(500, 185, 0.008, 0.00010);

  const CANDLE_MAP = {
    AAPL: _aaplCandles, SPY: _spyCandles, QQQ: _qqq, BND: _bnd, GLD: _gld,
    // Sector ETFs
    XLK:  _genCandles(500, 190, 0.016, 0.00040),
    XLV:  _genCandles(500, 140, 0.010, 0.00015),
    XLF:  _genCandles(500,  42, 0.013, 0.00020),
    XLE:  _genCandles(500,  88, 0.018, 0.00010),
    XLI:  _genCandles(500, 115, 0.011, 0.00018),
    XLY:  _genCandles(500, 175, 0.017, 0.00025),
    XLB:  _genCandles(500,  92, 0.013, 0.00012),
    XLC:  _genCandles(500,  82, 0.015, 0.00030),
    XLRE: _genCandles(500,  44, 0.011, 0.00008),
    XLU:  _genCandles(500,  68, 0.009, 0.00005),
    XLP:  _genCandles(500,  78, 0.007, 0.00010),
    // Portfolio tickers (used by timing + optimizer tabs)
    TECL: _genCandles(500,  58, 0.038, 0.00080), // 3× XLK — high vol, strong uptrend
    LLY:  _genCandles(500, 710, 0.016, 0.00055), // Eli Lilly — strong uptrend
    QQQM: _genCandles(500, 185, 0.013, 0.00030), // ≈ QQQ
    META: _genCandles(500, 480, 0.018, 0.00060), // Meta — strong uptrend
    GOOGL:_genCandles(500, 155, 0.015, 0.00035), // Alphabet
    CRCL: _genCandles(500,  22, 0.045, 0.00070), // High-beta
    AMZN: _genCandles(500, 185, 0.015, 0.00040), // Amazon
    ISRG: _genCandles(500, 380, 0.013, 0.00030), // Intuitive Surgical
    HOOD: _genCandles(500,  24, 0.042, 0.00065), // Robinhood — high-beta
    RKLB: _genCandles(500,  14, 0.050, 0.00060), // Rocket Lab — high-beta
    NFLX: _genCandles(500, 610, 0.018, 0.00045), // Netflix
  };

  const _lastPrice = (sym) => {
    const c = CANDLE_MAP[sym];
    return c ? c.c[c.c.length - 1] : null;
  };

  // ── Mock API overrides ────────────────────────────────────────────────────
  const _mockAPI = {
    quote(ticker) {
      const c = CANDLE_MAP[ticker] || _aaplCandles;
      const last = c.c[c.c.length - 1];
      const prev = c.c[c.c.length - 2];
      return Promise.resolve({
        price: last, change: last - prev, changePercent: (last - prev) / prev * 100,
        high: Math.max(...c.h.slice(-5)), low: Math.min(...c.l.slice(-5)),
        open: c.o[c.o.length - 1], prevClose: prev,
        volume: c.v[c.v.length - 1],
        marketCap: last * 15.5e9, // ~15.5B shares
        pe: 28.4, pb: 46.1, ps: 7.4, eps: last / 28.4,
        beta: 1.21, high52w: Math.max(...c.h), low52w: Math.min(...c.l),
        dividendYield: 0.52, roeTTM: 1.471, roaTTM: 0.284,
        revGrowth1y: 0.024, netMargin: 0.263, grossMargin: 0.460,
        _raw: {},
      });
    },

    profile(ticker) {
      return Promise.resolve({
        name: ticker === 'AAPL' ? 'Apple Inc.' : ticker,
        exchange: 'NASDAQ', industry: 'Technology', sector: 'Technology',
        logo: 'https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/AAPL.png',
        weburl: 'https://www.apple.com', country: 'US', currency: 'USD',
        shareOutstanding: 15543.12, marketCap: _lastPrice(ticker) * 15.5e9,
        ipo: '1980-12-12',
      });
    },

    candles(ticker, from, to, resolution = 'D') {
      const data = CANDLE_MAP[ticker] || _genCandles(500, 100, 0.012, 0.0002);
      const fromTs = from || 0;
      const toTs = to || Infinity;
      // Filter to requested range
      const indices = data.t.map((ts, i) => ts >= fromTs && ts <= toTs ? i : -1).filter(i => i >= 0);
      if (!indices.length) return Promise.resolve(null);
      return Promise.resolve({
        t: indices.map(i => data.t[i]),
        o: indices.map(i => data.o[i]),
        h: indices.map(i => data.h[i]),
        l: indices.map(i => data.l[i]),
        c: indices.map(i => data.c[i]),
        v: indices.map(i => data.v[i]),
      });
    },

    keyMetrics(ticker) {
      const price = _lastPrice(ticker) || 190;
      return Promise.resolve({
        pe: 28.4, forwardPe: 25.8, pb: 46.1, ps: 7.4, evEbitda: 21.3, evFcf: 28.1,
        priceToFcf: 27.5, peg: 2.8, dcfValue: price * 0.88,
        roic: 0.562, roe: 1.471, roa: 0.284, grossMargin: 0.460,
        ebitdaMargin: 0.334, netMargin: 0.263,
        grossMarginHistory: [
          { year: '2020', grossMargin: 0.382 }, { year: '2021', grossMargin: 0.418 },
          { year: '2022', grossMargin: 0.433 }, { year: '2023', grossMargin: 0.444 },
          { year: '2024', grossMargin: 0.460 },
        ],
        revGrowth: 0.024, revCagr3y: 0.061, epsGrowth: 0.081,
        debtToEquity: -4.3, currentRatio: 1.04, interestCoverage: 28.1,
        netDebt: -53.44e9, cashAndShortTermInvestments: 65.17e9,
        altmanZ: 5.84, piotroski: 7,
        ownersEarnings: 95.2e9, fcf: 108.8e9, capex: -9.4e9,
        dso: 26.4,
        revenueHistory: [
          { year: '2020', revenue: 274.5e9 }, { year: '2021', revenue: 365.8e9 },
          { year: '2022', revenue: 394.3e9 }, { year: '2023', revenue: 383.3e9 },
          { year: '2024', revenue: 391.0e9 },
        ],
        epsHistory: [
          { year: '2020', eps: 3.28 }, { year: '2021', eps: 5.61 },
          { year: '2022', eps: 6.11 }, { year: '2023', eps: 6.13 },
          { year: '2024', eps: 6.43 },
        ],
        marketCap: price * 15.5e9, enterpriseValue: price * 15.5e9 - 53.44e9,
        revenue: 391.0e9, ebitda: 130.5e9, netIncome: 103.0e9,
        shareOutstanding: 15543.12,
      });
    },

    earnings(ticker) {
      return Promise.resolve({
        history: [
          { period: 'Q1 FY24', actual: 2.18, estimate: 2.10, surprise: 0.08, surprisePct: 3.8 },
          { period: 'Q2 FY24', actual: 1.53, estimate: 1.50, surprise: 0.03, surprisePct: 2.0 },
          { period: 'Q3 FY24', actual: 1.40, estimate: 1.35, surprise: 0.05, surprisePct: 3.7 },
          { period: 'Q4 FY24', actual: 1.64, estimate: 1.60, surprise: 0.04, surprisePct: 2.5 },
          { period: 'Q1 FY25', actual: 2.40, estimate: 2.35, surprise: 0.05, surprisePct: 2.1 },
          { period: 'Q2 FY25', actual: 1.65, estimate: 1.62, surprise: 0.03, surprisePct: 1.9 },
          { period: 'Q3 FY25', actual: 1.51, estimate: 1.48, surprise: 0.03, surprisePct: 2.0 },
          { period: 'Q4 FY25', actual: null, estimate: 1.72, surprise: null, surprisePct: null },
        ],
        nextDate: '2025-10-30',
      });
    },

    insiderTransactions(ticker) {
      return Promise.resolve([
        { name: 'Timothy D. Cook', change: 50000, transactionCode: 'P', transactionPrice: 188.20, transactionDate: '2025-03-15', filingDate: '2025-03-17' },
        { name: 'Luca Maestri', change: -25000, transactionCode: 'S', transactionPrice: 192.40, transactionDate: '2025-02-20', filingDate: '2025-02-22' },
        { name: 'Katherine Adams', change: 15000, transactionCode: 'P', transactionPrice: 185.60, transactionDate: '2025-01-10', filingDate: '2025-01-12' },
        { name: 'Jeff Williams', change: -10000, transactionCode: 'S', transactionPrice: 194.80, transactionDate: '2024-12-05', filingDate: '2024-12-07' },
        { name: 'Deirdre O\'Brien', change: 8000, transactionCode: 'P', transactionPrice: 180.25, transactionDate: '2024-11-22', filingDate: '2024-11-24' },
      ]);
    },

    companyNews(ticker) {
      return Promise.resolve([
        { headline: 'Apple Intelligence features rolling out globally in latest iOS update', source: 'Reuters', datetime: Math.floor(Date.now()/1000) - 3600, url: '#', summary: 'Apple continues expanding its AI features.' },
        { headline: 'AAPL beats Q4 earnings estimates, iPhone sales rebound in China', source: 'Bloomberg', datetime: Math.floor(Date.now()/1000) - 86400, url: '#', summary: 'Strong earnings beat across all segments.' },
        { headline: 'Apple unveils next-generation M5 chip with 40% performance improvement', source: 'The Verge', datetime: Math.floor(Date.now()/1000) - 172800, url: '#', summary: 'New chip generation brings significant gains.' },
        { headline: 'Warren Buffett increases Berkshire\'s Apple stake to 43% of portfolio', source: 'WSJ', datetime: Math.floor(Date.now()/1000) - 259200, url: '#', summary: 'Berkshire continues to hold Apple as top position.' },
        { headline: 'Apple Services revenue hits record $26B, up 14% YoY', source: 'CNBC', datetime: Math.floor(Date.now()/1000) - 345600, url: '#', summary: 'Services segment drives margin expansion.' },
      ]);
    },

    marketNews() {
      return Promise.resolve([
        { headline: 'Fed signals rate cuts on hold amid sticky inflation data', source: 'Reuters', datetime: Math.floor(Date.now()/1000) - 1800, url: '#' },
        { headline: 'S&P 500 hits new record as AI earnings beat expectations', source: 'Bloomberg', datetime: Math.floor(Date.now()/1000) - 7200, url: '#' },
        { headline: '10Y Treasury yield rises to 4.52% after strong jobs report', source: 'WSJ', datetime: Math.floor(Date.now()/1000) - 14400, url: '#' },
        { headline: 'VIX falls to 13.4 as market volatility subsides', source: 'MarketWatch', datetime: Math.floor(Date.now()/1000) - 21600, url: '#' },
        { headline: 'China stimulus package lifts Asian markets, copper prices surge', source: 'FT', datetime: Math.floor(Date.now()/1000) - 28800, url: '#' },
        { headline: 'Investment-grade credit spreads near 5-year lows, signaling risk-on', source: 'Bloomberg', datetime: Math.floor(Date.now()/1000) - 36000, url: '#' },
        { headline: 'Hedge funds increase tech sector exposure to multi-year highs', source: 'Goldman Sachs Research', datetime: Math.floor(Date.now()/1000) - 43200, url: '#' },
        { headline: 'PCE inflation eases to 2.3%, boosting soft-landing narrative', source: 'CNBC', datetime: Math.floor(Date.now()/1000) - 50400, url: '#' },
        { headline: 'Dollar weakens as Fed dot plot shows two cuts expected in 2026', source: 'Reuters', datetime: Math.floor(Date.now()/1000) - 57600, url: '#' },
        { headline: 'NVIDIA data center revenue beats $30B quarterly estimate', source: 'Bloomberg', datetime: Math.floor(Date.now()/1000) - 64800, url: '#' },
      ]);
    },

    fred(seriesId) {
      return Promise.resolve(_fredMockData(seriesId));
    },

    fredLatest(seriesId) {
      const data = _fredMockData(seriesId);
      return Promise.resolve(data[data.length - 1] || null);
    },

    fredBatch(seriesIds) {
      const out = {};
      seriesIds.forEach(id => { out[id] = _fredMockData(id); });
      return Promise.resolve(out);
    },

    sec13F(cik) {
      // Berkshire Hathaway mock 13F
      return Promise.resolve({
        filedDate: '2025-02-14',
        cik: '0001067983',
        totalValue: 320_000_000_000,
        holdings: [
          { ticker: 'AAPL',  name: 'Apple Inc.',             value: 137_600_000_000, shares: 730_000_000 },
          { ticker: 'BAC',   name: 'Bank of America Corp',   value:  32_400_000_000, shares: 1_020_000_000 },
          { ticker: 'AXP',   name: 'American Express Co',    value:  23_800_000_000, shares: 151_610_000 },
          { ticker: 'KO',    name: 'Coca-Cola Co',           value:  22_500_000_000, shares: 400_000_000 },
          { ticker: 'CVX',   name: 'Chevron Corp',           value:  17_400_000_000, shares: 118_610_000 },
          { ticker: 'OXY',   name: 'Occidental Petroleum',   value:  13_100_000_000, shares: 248_000_000 },
          { ticker: 'MCO',   name: 'Moody\'s Corp',          value:   9_800_000_000, shares:  21_240_000 },
          { ticker: 'VRSN',  name: 'VeriSign Inc',           value:   2_900_000_000, shares:  13_280_000 },
          { ticker: 'ATVI',  name: 'Activision Blizzard',    value:   1_400_000_000, shares:  14_660_000 },
          { ticker: 'HPQ',   name: 'HP Inc.',                value:   3_200_000_000, shares:  87_000_000 },
          { ticker: 'ALLY',  name: 'Ally Financial',         value:   1_100_000_000, shares:  29_000_000 },
          { ticker: 'ULTA',  name: 'Ulta Beauty',            value:     280_000_000, shares:     740_000 },
        ],
      });
    },

    sectorPerformance() {
      const now = Math.floor(Date.now() / 1000);
      const y1ago = now - 365 * 86400;
      const out = {};
      const allSyms = ['SPY', ...Object.keys(API.SECTOR_NAMES || {
        XLK:'', XLV:'', XLF:'', XLE:'', XLI:'', XLY:'', XLB:'', XLC:'', XLRE:'', XLU:'', XLP:''
      })];
      allSyms.forEach(sym => {
        const c = CANDLE_MAP[sym];
        if (!c) return;
        const last = c.c[c.c.length - 1];
        const m1 = c.c[Math.max(0, c.c.length - 22)];
        const m3 = c.c[Math.max(0, c.c.length - 65)];
        const m6 = c.c[Math.max(0, c.c.length - 130)];
        const y1 = c.c[0];
        const ytdBase = (() => {
          const yr = new Date().getFullYear();
          const idx = c.t.findIndex(ts => new Date(ts * 1000).getFullYear() === yr);
          return idx >= 0 ? c.c[idx] : c.c[0];
        })();
        out[sym] = {
          name: API.SECTOR_NAMES?.[sym] || sym,
          latest: last,
          ret1m:  m1  > 0 ? (last / m1  - 1) * 100 : null,
          ret3m:  m3  > 0 ? (last / m3  - 1) * 100 : null,
          ret6m:  m6  > 0 ? (last / m6  - 1) * 100 : null,
          retYtd: ytdBase > 0 ? (last / ytdBase - 1) * 100 : null,
          ret1y:  y1  > 0 ? (last / y1  - 1) * 100 : null,
          closes: c.c, dates: c.t,
        };
      });
      return Promise.resolve(out);
    },
  };

  // ── FRED mock data generator ──────────────────────────────────────────────
  function _fredMockData(seriesId) {
    const N = 260;
    const today = new Date();
    const makeTs = (daysAgo) => {
      const d = new Date(today);
      d.setDate(d.getDate() - daysAgo);
      return d.toISOString().slice(0, 10);
    };

    const configs = {
      FEDFUNDS:  { base: 5.33, noise: 0.02, trend: 0 },
      DGS2:      { base: 4.52, noise: 0.15, trend: -0.001 },
      DGS5:      { base: 4.38, noise: 0.12, trend: -0.001 },
      DGS10:     { base: 4.45, noise: 0.10, trend: -0.0008 },
      DGS30:     { base: 4.62, noise: 0.08, trend: -0.0006 },
      DFII10:    { base: 2.12, noise: 0.08, trend: -0.0005 },
      T10Y2Y:    { base: -0.08, noise: 0.12, trend: 0.0005 },
      CPIAUCSL:  { base: 314.5, noise: 0.5, trend: 0.02, monthly: true },
      CPILFESL:  { base: 322.1, noise: 0.4, trend: 0.018, monthly: true },
      PCEPI:     { base: 123.4, noise: 0.2, trend: 0.012, monthly: true },
      UNRATE:    { base: 4.2, noise: 0.1, trend: 0, monthly: true },
      BAMLC0A0CM: { base: 92, noise: 8, trend: 0 },
      BAMLH0A0HYM2: { base: 305, noise: 20, trend: 0 },
      PCOPPUSDM: { base: 4.20, noise: 0.15, trend: 0.001, monthly: true },
      GOLDAMGBD228NLBM: { base: 2380, noise: 40, trend: 0.5 },
      UMCSENT:   { base: 69.1, noise: 3, trend: 0.02, monthly: true },
      GDPC1:     { base: 21900, noise: 100, trend: 5, quarterly: true },
      DGS3MO:    { base: 5.28, noise: 0.05, trend: 0 },
      DTWEXBGS:  { base: 126.4, noise: 1.5, trend: -0.01 },
    };

    const cfg = configs[seriesId] || { base: 100, noise: 2, trend: 0 };
    const step = cfg.monthly ? 30 : cfg.quarterly ? 90 : 1;
    const count = cfg.monthly ? 36 : cfg.quarterly ? 20 : N;
    const result = [];
    let val = cfg.base;
    for (let i = count; i >= 0; i--) {
      val += cfg.trend + (Math.random() - 0.5) * cfg.noise;
      result.push({ date: makeTs(i * step), value: +val.toFixed(4) });
    }
    return result;
  }

  // ── Activate / deactivate demo mode ──────────────────────────────────────
  function activate() {
    if (_active) return;
    _active = true;

    // Override API methods
    Object.keys(_mockAPI).forEach(method => {
      if (typeof API[method] === 'function') {
        API['_real_' + method] = API[method];
        API[method] = _mockAPI[method];
      }
    });

    // Clear all caches so fresh demo data loads
    CACHE.clearAll();

    // Show demo banner
    _showBanner();

    // Inject mock portfolio holdings so optimizer + timing show position data
    // Uses {ts, holdings:[...]} format matching PORTFOLIO_ENGINE.saveHoldingsCache()
    try {
      const _p = (sym) => { const c = CANDLE_MAP[sym]; return c ? c.c[c.c.length - 1] : 100; };
      const _raw = [
        { ticker: 'TECL', shares: 150, avgCost:  65.20, account: 'IRA',               accountType: 'IRA',       taxType: 'traditional_ira', purchaseDate: '2022-03-15' },
        { ticker: 'LLY',  shares: 10,  avgCost: 680.50, account: 'IRA',               accountType: 'IRA',       taxType: 'traditional_ira', purchaseDate: '2021-11-08' },
        { ticker: 'QQQM', shares: 40,  avgCost: 165.30, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2023-01-20' },
        { ticker: 'NFLX', shares: 5,   avgCost: 580.00, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2023-06-12' },
        { ticker: 'ISRG', shares: 8,   avgCost: 355.40, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2022-08-30' },
        { ticker: 'META', shares: 12,  avgCost: 420.80, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2023-02-14' },
        { ticker: 'GOOGL',shares: 25,  avgCost: 142.60, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2022-12-01' },
        { ticker: 'AMZN', shares: 18,  avgCost: 170.30, account: 'Fidelity Brokerage',accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2023-04-05' },
        { ticker: 'CRCL', shares: 200, avgCost:  18.50, account: 'Robinhood',          accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2024-08-20' },
        { ticker: 'HOOD', shares: 150, avgCost:  20.10, account: 'Robinhood',          accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2024-10-03' },
        { ticker: 'RKLB', shares: 300, avgCost:  11.80, account: 'Robinhood',          accountType: 'Brokerage', taxType: 'taxable',          purchaseDate: '2024-07-15' },
      ];
      const now = Date.now();
      const totalMV = _raw.reduce((s, h) => s + h.shares * _p(h.ticker), 0) || 1;
      const holdings = _raw.map(h => {
        const price = _p(h.ticker);
        const mktVal = h.shares * price;
        const cost = h.shares * h.avgCost;
        const gain = mktVal - cost;
        const pdMs = new Date(h.purchaseDate).getTime();
        const holdDays = Math.floor((now - pdMs) / 86400000);
        return {
          ...h,
          costBasis:        cost,
          currentPrice:     price,
          currentValue:     mktVal,
          marketVal:        mktVal,
          holdingPeriodDays: holdDays,
          isLongTerm:       holdDays >= 365,
          gain,
          gainPct:          cost > 0 ? (gain / cost) * 100 : 0,
          targetWeight:     (typeof CONFIG !== 'undefined' && CONFIG.PORTFOLIO_TARGETS || {})[h.ticker] || 0,
          currentWeight:    (mktVal / totalMV) * 100,
          sector:           '',
          isCash:           false,
          dataSource:       'demo',
          lastUpdated:      now,
        };
      });
      localStorage.setItem('wd_holdings_cache', JSON.stringify({ ts: now, holdings }));
    } catch(_) {}

    // Set demo ticker
    STATE.setTicker('AAPL');
    const searchInput = document.getElementById('ticker-search');
    if (searchInput) searchInput.value = 'AAPL';

    UI.toast('데모 모드 활성화 — 샘플 데이터(AAPL)로 모든 탭 체험 가능', 'success', 6000);
  }

  function deactivate() {
    if (!_active) return;
    _active = false;
    // Restore real API methods
    Object.keys(_mockAPI).forEach(method => {
      const real = API['_real_' + method];
      if (real) {
        API[method] = real;
        delete API['_real_' + method];
      }
    });
    CACHE.clearAll();
    try { localStorage.removeItem('wd_holdings_cache'); } catch(_) {}
    _hideBanner();
    UI.toast('데모 모드 해제 — 실제 API 키 사용', 'info');
  }

  function isActive() { return _active; }

  function _showBanner() {
    let el = document.getElementById('demo-banner');
    if (!el) {
      el = document.createElement('div');
      el.id = 'demo-banner';
      el.className = 'bg-amber-950/80 border-b border-amber-700/60 px-4 py-2 text-center text-amber-300 text-sm flex items-center justify-center gap-3';
      el.innerHTML = `
        <span>📊 데모 모드 — 샘플 데이터입니다 (AAPL 기준 가상 데이터)</span>
        <button onclick="DEMO_RESEARCH.deactivate(); location.reload();"
          class="text-amber-400 hover:text-white text-xs underline">데모 해제</button>`;
      document.body.insertBefore(el, document.body.firstChild);
    }
    el.classList.remove('hidden');
  }

  function _hideBanner() {
    const el = document.getElementById('demo-banner');
    if (el) el.classList.add('hidden');
  }

  return { activate, deactivate, isActive };
})();
