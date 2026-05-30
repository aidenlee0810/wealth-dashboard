// research/api.js — Unified API layer with caching and rate limiting
// APIs: Finnhub (60 req/min free), FMP ($14/mo optional), FRED (unlimited free)
// SEC EDGAR (unlimited free), BLS (unlimited free)

const API = (() => {
  // ── Rate limiter for Finnhub (60 req/min) ────────────────────────────────
  const _queue = [];
  let _tokens = 60;
  let _lastRefill = Date.now();

  function _getToken() {
    return new Promise(resolve => {
      const tryGet = () => {
        const now = Date.now();
        const elapsed = now - _lastRefill;
        if (elapsed >= 60000) {
          _tokens = 60;
          _lastRefill = now;
        }
        if (_tokens > 0) {
          _tokens--;
          resolve();
        } else {
          setTimeout(tryGet, 1000);
        }
      };
      tryGet();
    });
  }

  // ── Core fetch helpers ────────────────────────────────────────────────────
  async function _finnhub(path, params = {}) {
    const key = RESEARCH_CONFIG.FINNHUB_KEY;
    await _getToken();
    const url = key
      ? new URL('https://finnhub.io/api/v1' + path)
      : new URL('/api/finnhub', window.location.origin);
    if (key) url.searchParams.set('token', key);
    else url.searchParams.set('path', path);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    const res = await fetch(url.toString());
    if (!res.ok) throw new Error('Finnhub ' + res.status + ': ' + path);
    return res.json();
  }

  async function _fmp(path, params = {}) {
    const key = RESEARCH_CONFIG.FMP_KEY;
    if (!key) return null; // graceful skip if not configured
    const url = new URL('https://financialmodelingprep.com/api/v3' + path);
    url.searchParams.set('apikey', key);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    const res = await fetch(url.toString());
    if (!res.ok) return null;
    return res.json();
  }

  async function _fred(seriesId) {
    const key = RESEARCH_CONFIG.FRED_KEY;
    // Route through local server.py proxy (/api/fred) to avoid CORS block.
    // FRED does not send Access-Control-Allow-Origin headers, so direct browser
    // fetch fails with net::ERR_FAILED. The proxy at /api/fred passes the request
    // server-side and adds CORS headers.
    const params = new URLSearchParams({
      series_id:  seriesId,
      limit:      '260',
      sort_order: 'desc',
    });
    if (key) params.set('api_key', key);
    const res = await fetch('/api/fred?' + params.toString());
    if (!res.ok) throw new Error('FRED ' + res.status + ': ' + seriesId);
    const json = await res.json();
    return (json.observations || [])
      .filter(o => o.value !== '.')
      .map(o => ({ date: o.date, value: parseFloat(o.value) }))
      .reverse();
  }

  async function _edgar(path) {
    // SEC EDGAR data.sec.gov supports CORS directly
    const url = 'https://data.sec.gov' + path;
    const res = await fetch(url, { headers: { 'User-Agent': 'WealthDashboard research@example.com' } });
    if (!res.ok) throw new Error('EDGAR ' + res.status + ': ' + path);
    return res.json();
  }

  // ── Cached wrapper ────────────────────────────────────────────────────────
  async function _cached(cacheType, cacheId, fetcher) {
    const hit = CACHE.get(cacheType, cacheId);
    if (hit !== null) return hit;
    const data = await fetcher();
    if (data !== null && data !== undefined) {
      CACHE.set(cacheType, cacheId, data);
    }
    return data;
  }

  // ── Public API methods ────────────────────────────────────────────────────

  // Real-time quote: { price, change, changePercent, high, low, open, prevClose, volume, marketCap, pe }
  async function quote(ticker) {
    return _cached('quote', ticker, async () => {
      const [q, m] = await Promise.all([
        _finnhub('/quote', { symbol: ticker }),
        _finnhub('/stock/metric', { symbol: ticker, metric: 'all' }).catch(() => null),
      ]);
      return {
        price:         q.c,
        change:        q.d,
        changePercent: q.dp,
        high:          q.h,
        low:           q.l,
        open:          q.o,
        prevClose:     q.pc,
        volume:        q.v,
        marketCap:     m?.metric?.marketCapitalization ? m.metric.marketCapitalization * 1e6 : null,
        pe:            m?.metric?.peBasicExclExtraTTM || null,
        pb:            m?.metric?.pbAnnual || null,
        ps:            m?.metric?.psTTM || null,
        eps:           m?.metric?.epsBasicExclExtraAnnual || null,
        beta:          m?.metric?.beta || null,
        high52w:       m?.metric?.['52WeekHigh'] || null,
        low52w:        m?.metric?.['52WeekLow'] || null,
        dividendYield: m?.metric?.dividendYieldIndicatedAnnual || null,
        roeTTM:        m?.metric?.roeTTM || null,
        roaTTM:        m?.metric?.roaTTM || null,
        revGrowth1y:   m?.metric?.revenueGrowthTTMYoy || null,
        netMargin:     m?.metric?.netProfitMarginTTM || null,
        grossMargin:   m?.metric?.grossMarginTTM || null,
        _raw: m?.metric || {},
      };
    });
  }

  // Company profile: { name, exchange, industry, sector, logo, weburl, description }
  async function profile(ticker) {
    return _cached('fundamentals', ticker + '_profile', async () => {
      const d = await _finnhub('/stock/profile2', { symbol: ticker });
      return {
        name:        d.name,
        exchange:    d.exchange,
        industry:    d.finnhubIndustry,
        sector:      _mapSector(d.finnhubIndustry),
        logo:        d.logo,
        weburl:      d.weburl,
        description: d.description || null,
        country:     d.country,
        currency:    d.currency,
        shareOutstanding: d.shareOutstanding,
        marketCap:   d.marketCapitalization ? d.marketCapitalization * 1e6 : null,
        ipo:         d.ipo,
      };
    });
  }

  // OHLCV candles: { t[], o[], h[], l[], c[], v[] }
  // resolution: 'D' (daily), 'W' (weekly), 'M' (monthly)
  async function candles(ticker, fromTs, toTs, resolution = 'D') {
    const cacheId = ticker + '_' + resolution + '_' + Math.floor(fromTs / 86400) + '_' + Math.floor(toTs / 86400);
    return _cached('candles', cacheId, async () => {
      // Try Finnhub first
      try {
        const d = await _finnhub('/stock/candle', {
          symbol: ticker,
          resolution,
          from: Math.floor(fromTs),
          to: Math.floor(toTs),
        });
        if (d.s === 'ok') return { t: d.t, o: d.o, h: d.h, l: d.l, c: d.c, v: d.v };
      } catch (e) {
        // Fall through to Yahoo Finance proxy on 403 or any error
        if (!e.message.includes('403') && !e.message.includes('401') && !e.message.includes('no_data')) {
          console.warn('Finnhub candle error, falling back to Yahoo Finance:', e.message);
        }
      }
      // Yahoo Finance fallback via local proxy
      const rangeMap = {
        D: { period: '1y',  interval: '1d' },
        W: { period: '5y',  interval: '1wk' },
        M: { period: '10y', interval: '1mo' },
      };
      const { period, interval } = rangeMap[resolution] || rangeMap.D;
      const yhUrl = new URL('/api/yhfin', window.location.origin);
      yhUrl.searchParams.set('symbol', ticker);
      yhUrl.searchParams.set('period', period);
      yhUrl.searchParams.set('interval', interval);
      const res = await fetch(yhUrl.toString());
      if (!res.ok) throw new Error('Yahoo Finance ' + res.status + ': ' + ticker);
      const yh = await res.json();
      if (yh.s !== 'ok' || !yh.t?.length) return null;
      // Filter to the originally requested time range (Yahoo returns a fixed period)
      const filtered = { t: [], o: [], h: [], l: [], c: [], v: [] };
      for (let i = 0; i < yh.t.length; i++) {
        if (yh.t[i] >= Math.floor(fromTs) && yh.t[i] <= Math.floor(toTs)) {
          filtered.t.push(yh.t[i]);
          filtered.o.push(yh.o[i]);
          filtered.h.push(yh.h[i]);
          filtered.l.push(yh.l[i]);
          filtered.c.push(yh.c[i]);
          filtered.v.push(yh.v[i]);
        }
      }
      return filtered.t.length ? filtered : null;
    });
  }

  // FMP key metrics (ROIC, EV/EBITDA, FCF, etc.) — requires FMP key
  async function keyMetrics(ticker) {
    return _cached('fundamentals', ticker + '_metrics', async () => {
      const [metrics, ratios, income, balanceSheet, cashflow] = await Promise.all([
        _fmp('/key-metrics/' + ticker, { limit: 5 }),
        _fmp('/ratios/' + ticker, { limit: 5 }),
        _fmp('/income-statement/' + ticker, { limit: 5 }),
        _fmp('/balance-sheet-statement/' + ticker, { limit: 5 }),
        _fmp('/cash-flow-statement/' + ticker, { limit: 5 }),
      ]);
      if (!metrics || !metrics.length) return null;
      const m = metrics[0]; // most recent annual
      const r = ratios ? ratios[0] : {};
      const i = income ? income[0] : {};
      const b = balanceSheet ? balanceSheet[0] : {};
      const cf = cashflow ? cashflow[0] : {};

      // Altman Z-Score (manufacturing formula)
      const workingCapital = (b.totalCurrentAssets || 0) - (b.totalCurrentLiabilities || 0);
      const totalAssets = b.totalAssets || 1;
      const retainedEarnings = b.retainedEarnings || 0;
      const ebit = i.operatingIncome || 0;
      const totalLiabilities = b.totalLiabilities || 0;
      const marketCap = m.marketCap || 0;
      const revenue = i.revenue || 0;
      const z = totalAssets > 0 ? (
        1.2 * (workingCapital / totalAssets) +
        1.4 * (retainedEarnings / totalAssets) +
        3.3 * (ebit / totalAssets) +
        0.6 * (marketCap / Math.max(totalLiabilities, 1)) +
        1.0 * (revenue / totalAssets)
      ) : null;

      // Piotroski F-Score (0-9)
      const piotroski = _calcPiotroski(income, balanceSheet, cashflow);

      // Owner's Earnings (Buffett: Net Income + D&A - CapEx)
      const ownersEarnings = (i.netIncome || 0) + (cf.depreciationAndAmortization || 0) - Math.abs(cf.capitalExpenditure || 0);

      // Revenue CAGR 3Y
      const rev3yago = income && income[3] ? income[3].revenue : null;
      const revCagr3y = rev3yago && rev3yago > 0 && revenue > 0
        ? Math.pow(revenue / rev3yago, 1/3) - 1 : null;

      // Gross margin trend (array)
      const grossMarginHistory = (income || []).map(yr => ({
        year: yr.calendarYear,
        grossMargin: yr.revenue > 0 ? yr.grossProfit / yr.revenue : null,
      })).reverse();

      return {
        // Valuation
        pe:           r.priceEarningsRatio,
        forwardPe:    m.peRatio,
        pb:           r.priceToBookRatio,
        ps:           r.priceToSalesRatio,
        evEbitda:     m.enterpriseValueOverEBITDA,
        evFcf:        m.evToFreeCashFlow,
        priceToFcf:   m.priceToFreeCashFlowsRatio,
        peg:          m.pegRatio,
        dcfValue:     m.dcf,
        // Quality
        roic:         m.roic,
        roe:          m.roe,
        roa:          m.returnOnTangibleAssets,
        grossMargin:  r.grossProfitMargin,
        ebitdaMargin: r.ebitdaMargin,
        netMargin:    r.netProfitMargin,
        grossMarginHistory,
        // Growth
        revGrowth:    r.revenueGrowth,
        revCagr3y,
        epsGrowth:    r.epsgrowth,
        fcfGrowth:    null, // computed below if available
        // Balance sheet
        debtToEquity: r.debtEquityRatio,
        currentRatio: r.currentRatio,
        interestCoverage: r.interestCoverage,
        netDebt:      b.netDebt,
        cashAndShortTermInvestments: b.cashAndShortTermInvestments,
        // Special metrics
        altmanZ:      z,
        piotroski,
        ownersEarnings,
        fcf:          cf.freeCashFlow,
        capex:        cf.capitalExpenditure,
        dso:          revenue > 0 ? (b.netReceivables || 0) / (revenue / 365) : null,
        revenueHistory: (income || []).map(yr => ({ year: yr.calendarYear, revenue: yr.revenue })).reverse(),
        epsHistory:     (income || []).map(yr => ({ year: yr.calendarYear, eps: yr.eps })).reverse(),
        // Raw
        marketCap:    m.marketCap,
        enterpriseValue: m.enterpriseValue,
        revenue:      revenue,
        ebitda:       i.ebitda,
        netIncome:    i.netIncome,
      };
    });
  }

  // Piotroski F-Score (0-9)
  function _calcPiotroski(income, balance, cashflow) {
    if (!income || income.length < 2) return null;
    const i0 = income[0], i1 = income[1];
    const b0 = balance ? balance[0] : {};
    const b1 = balance && balance[1] ? balance[1] : {};
    const cf0 = cashflow ? cashflow[0] : {};
    let score = 0;
    // Profitability (4 points)
    if ((i0.netIncome || 0) > 0) score++;                          // F1: ROA > 0
    if ((cf0.operatingCashFlow || 0) > 0) score++;                 // F2: CFO > 0
    const roa0 = b0.totalAssets > 0 ? i0.netIncome / b0.totalAssets : 0;
    const roa1 = b1.totalAssets > 0 ? i1.netIncome / b1.totalAssets : 0;
    if (roa0 > roa1) score++;                                       // F3: Δ ROA > 0
    if (b0.totalAssets > 0 && (cf0.operatingCashFlow || 0) / b0.totalAssets > roa0) score++; // F4: Accruals
    // Leverage (3 points)
    const lev0 = b0.totalAssets > 0 ? b0.longTermDebt / b0.totalAssets : 0;
    const lev1 = b1.totalAssets > 0 ? (b1.longTermDebt || 0) / b1.totalAssets : 0;
    if (lev0 < lev1) score++;                                       // F5: Δ Leverage < 0
    const curr0 = b0.totalCurrentLiabilities > 0 ? b0.totalCurrentAssets / b0.totalCurrentLiabilities : 0;
    const curr1 = b1.totalCurrentLiabilities > 0 ? (b1.totalCurrentAssets || 0) / b1.totalCurrentLiabilities : 0;
    if (curr0 > curr1) score++;                                     // F6: Δ Current Ratio > 0
    if ((b0.commonStock || 0) <= (b1.commonStock || 0)) score++;   // F7: No new shares issued
    // Operating efficiency (2 points)
    const gm0 = i0.revenue > 0 ? i0.grossProfit / i0.revenue : 0;
    const gm1 = i1.revenue > 0 ? i1.grossProfit / i1.revenue : 0;
    if (gm0 > gm1) score++;                                         // F8: Δ Gross Margin > 0
    const at0 = b0.totalAssets > 0 ? i0.revenue / b0.totalAssets : 0;
    const at1 = b1.totalAssets > 0 ? i1.revenue / b1.totalAssets : 0;
    if (at0 > at1) score++;                                         // F9: Δ Asset Turnover > 0
    return score;
  }

  // Earnings calendar + history (8 quarters)
  async function earnings(ticker) {
    return _cached('fundamentals', ticker + '_earnings', async () => {
      const [hist, cal] = await Promise.all([
        _finnhub('/stock/earnings', { symbol: ticker, limit: 8 }),
        _finnhub('/calendar/earnings', {
          from: new Date().toISOString().slice(0, 10),
          to: new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10),
          symbol: ticker,
        }).catch(() => null),
      ]);
      const nextDate = cal?.earningsCalendar?.[0]?.date || null;
      return {
        history: (hist || []).map(e => ({
          period:   e.period,
          actual:   e.actual,
          estimate: e.estimate,
          surprise: e.surprise,
          surprisePct: e.surprisePercent,
        })),
        nextDate,
      };
    });
  }

  // Insider transactions from Finnhub (Form 4)
  async function insiderTransactions(ticker) {
    return _cached('insider', ticker, async () => {
      const d = await _finnhub('/stock/insider-transactions', { symbol: ticker });
      return (d.data || []).slice(0, 50).map(t => ({
        name:         t.name,
        share:        t.share,
        change:       t.change,
        transactionDate: t.transactionDate,
        transactionCode: t.transactionCode, // P = open-market purchase, S = sale
        transactionPrice: t.transactionPrice,
        filingDate:   t.filingDate,
      }));
    });
  }

  // Company news (last N days)
  // Paywalled / low-value sources to exclude from Finnhub company news
  const _BLOCKED_NEWS_SOURCES = new Set([
    'Benzinga', 'Seeking Alpha', 'Motley Fool', 'The Motley Fool',
  ]);

  async function companyNews(ticker, days = 7) {
    const from = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
    const to = new Date().toISOString().slice(0, 10);
    return _cached('news', ticker, async () => {
      const d = await _finnhub('/company-news', { symbol: ticker, from, to });
      return (d || [])
        .filter(n => !_BLOCKED_NEWS_SOURCES.has(n.source))
        .slice(0, 20)
        .map(n => ({
          headline:  n.headline,
          source:    n.source,
          datetime:  n.datetime,
          url:       n.url,
          summary:   n.summary,
          sentiment: n.sentiment || null,
        }));
    });
  }

  // FRED macroeconomic series — returns [{ date, value }] ascending
  async function fred(seriesId) {
    return _cached('macro', seriesId, () => _fred(seriesId));
  }

  // Latest FRED value
  async function fredLatest(seriesId) {
    const data = await fred(seriesId);
    return data && data.length ? data[data.length - 1] : null;
  }

  // Multiple FRED series in parallel
  async function fredBatch(seriesIds) {
    const results = await Promise.allSettled(seriesIds.map(id => fred(id)));
    const out = {};
    seriesIds.forEach((id, i) => {
      out[id] = results[i].status === 'fulfilled' ? results[i].value : [];
    });
    return out;
  }

  // Market news — RSS aggregator (CNBC, Reuters, AP, MarketWatch, Yahoo Finance, Fed Reserve)
  // Falls back to Finnhub (minus blocked sources) if proxy unavailable
  async function marketNews(category = 'general') {
    return _cached('news', 'market_rss_v3', async () => {
      try {
        const res = await fetch('/api/news?mode=market');
        if (res.ok) {
          const d = await res.json();
          if (Array.isArray(d) && d.length > 0) return d;
        }
      } catch (_) { /* fall through */ }
      // Fallback: Finnhub filtered
      const d = await _finnhub('/news', { category });
      return (d || [])
        .filter(n => !_BLOCKED_NEWS_SOURCES.has(n.source))
        .slice(0, 20)
        .map(n => ({ headline: n.headline, source: n.source, datetime: n.datetime, url: n.url, summary: n.summary }));
    });
  }

  // SEC EDGAR — get latest 13F filing for a CIK
  async function sec13F(cik) {
    return _cached('sec13f', cik, async () => {
      const padded = cik.replace(/^0+/, '').padStart(10, '0');
      const submissions = await _edgar('/submissions/CIK' + padded + '.json');
      // Find latest 13F-HR filing
      const filings = submissions.filings?.recent;
      if (!filings) return null;
      const idx = filings.form.findIndex(f => f === '13F-HR');
      if (idx === -1) return null;
      const accNum = filings.accessionNumber[idx].replace(/-/g, '');
      const filedDate = filings.filingDate[idx];
      // Fetch the filing index via proxy
      const indexEdgarPath = '/Archives/edgar/data/' + submissions.cik + '/' + accNum + '/';
      const indexRes = await fetch(new URL('/api/edgar' + indexEdgarPath, window.location.origin).toString());
      const indexText = await indexRes.text();
      // Parse infotable XML link from HTML index
      const xmlMatch = indexText.match(/href="([^"]*infotable[^"]*\.xml)"/i)
                    || indexText.match(/href="([^"]*\.xml)"/i);
      if (!xmlMatch) return { filedDate, holdings: [], totalValue: 0 };
      // Route XML fetch through proxy too
      let xmlEdgarPath = xmlMatch[1];
      if (xmlEdgarPath.includes('data.sec.gov')) {
        xmlEdgarPath = xmlEdgarPath.replace('https://data.sec.gov', '');
      } else if (xmlEdgarPath.includes('www.sec.gov')) {
        xmlEdgarPath = xmlEdgarPath.replace('https://www.sec.gov', '');
      } else if (!xmlEdgarPath.startsWith('/')) {
        xmlEdgarPath = indexEdgarPath + xmlEdgarPath;
      }
      const [xmlRes, tickerMap] = await Promise.all([
        fetch(new URL('/api/edgar' + xmlEdgarPath, window.location.origin).toString()),
        _loadTickerMap(),
      ]);
      const xmlText = await xmlRes.text();
      const holdings = _parse13FXML(xmlText, tickerMap);
      const totalValue = holdings.reduce((s, h) => s + (h.value || 0), 0);
      return { filedDate, holdings, totalValue, cik: submissions.cik };
    });
  }

  // ── SEC company_tickers.json lookup (name → ticker) ─────────────────────
  // Fetched once and cached 7 days. Used to resolve 13F nameOfIssuer → ticker.
  let _tickerMap = null; // normalized_name → ticker

  // Common name aliases where 13F filings use non-standard names
  const _NAME_ALIASES = {
    'BANK AMERICA': 'BAC', 'BANK OF AMERICA': 'BAC',
    'AMERICAN EXPRESS': 'AXP',
    'COCA COLA': 'KO', 'COCACOLA': 'KO',
    'OCCIDENTAL PETE': 'OXY', 'OCCIDENTAL PETROLEUM': 'OXY',
    'CHEVRON': 'CVX',
    'APPLE': 'AAPL',
    'ALPHABET': 'GOOGL',
    'CHUBB': 'CB',
    'MOODYS': 'MCO', "MOODY'S": 'MCO',
    'KRAFT HEINZ': 'KHC',
    'AMAZON': 'AMZN',
    'MICROSOFT': 'MSFT',
    'JPMORGAN CHASE': 'JPM', 'JP MORGAN CHASE': 'JPM',
    'BERKSHIRE HATHAWAY': 'BRK.B',
    'WELLS FARGO': 'WFC',
    'CITIGROUP': 'C',
    'GOLDMAN SACHS': 'GS',
    'MORGAN STANLEY': 'MS',
    'META PLATFORMS': 'META',
    'NVIDIA': 'NVDA',
    'TESLA': 'TSLA',
    'VISA': 'V', 'MASTERCARD': 'MA',
    'UNITEDHEALTH': 'UNH', 'UNITED HEALTH': 'UNH',
    'JOHNSON JOHNSON': 'JNJ',
    'EXXON MOBIL': 'XOM', 'EXXONMOBIL': 'XOM',
    'PROCTER GAMBLE': 'PG',
    'HOME DEPOT': 'HD',
    'ABBVIE': 'ABBV',
    'MERCK': 'MRK',
    'PFIZER': 'PFE',
    'BRISTOL MYERS SQUIBB': 'BMY',
    'ELI LILLY': 'LLY',
    'LOCKHEED MARTIN': 'LMT',
    'GENERAL DYNAMICS': 'GD',
    'NORTHROP GRUMMAN': 'NOC',
    'RAYTHEON': 'RTX',
    'LIBERTY MEDIA': 'LSXMA',
    'VERISIGN': 'VRSN',
    'DAVITA': 'DVA',
    'FLOOR DECOR': 'FND',
    'CHARTER COMMUNICATIONS': 'CHTR',
    'SIRIUS XM': 'SIRI',
    'NU HOLDINGS': 'NU',
    'PILOT': 'PBF',
    'DIAGEO': 'DEO',
    'AON': 'AON',
    'MARSH MCLENNAN': 'MMC',
  };

  function _normName(s) {
    return (s || '').toUpperCase()
      .replace(/\b(INCORPORATED|CORPORATION|COMPANY|LIMITED|INTERNATIONAL|TECHNOLOGIES|TECHNOLOGY|BANCSHARES|BANCORP|FINANCIAL|HOLDINGS|PETROLEUM|RESOURCES|INDUSTRIES|SOLUTIONS|SYSTEMS|SERVICES|GROUP|TRUST|FUND|INC|CORP|CO|LTD|LLC|PLC|LP|THE|OF|AND)\b\.?/g, '')
      .replace(/[^A-Z0-9 ]/g, '')
      .replace(/\s+/g, ' ').trim();
  }

  async function _loadTickerMap() {
    if (_tickerMap) return _tickerMap;
    const cacheKey = 'wr_sec_ticker_map';
    try {
      const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
      // Require at least 5000 entries — an empty {} means a prior failed fetch
      if (cached && Object.keys(cached.data || {}).length > 5000 && (Date.now() - cached.ts) < 7 * 24 * 3600 * 1000) {
        _tickerMap = cached.data;
        return _tickerMap;
      }
    } catch(_) {}
    try {
      const res = await fetch('/api/edgar/files/company_tickers.json');
      if (!res.ok) throw new Error('status ' + res.status);
      const raw = await res.json();
      const map = {};
      Object.values(raw).forEach(({ ticker, title }) => {
        if (!ticker || !title) return;
        // Store both aggressive-normalized and full-normalized keys
        const norm = _normName(title);
        if (norm) map[norm] = ticker.toUpperCase();
        const full = title.toUpperCase().replace(/[^A-Z0-9 ]/g, '').replace(/\s+/g, ' ').trim();
        if (full && !map[full]) map[full] = ticker.toUpperCase();
      });
      _tickerMap = map;
      localStorage.setItem(cacheKey, JSON.stringify({ ts: Date.now(), data: map }));
    } catch(e) {
      console.warn('SEC ticker map load failed:', e.message);
      _tickerMap = {};
    }
    return _tickerMap;
  }

  function _resolveTicker(name, map) {
    if (!name) return '';
    // 1) Alias table (handles 13F abbreviations like "OCCIDENTAL PETE")
    const aliasKey = _normName(name);
    if (_NAME_ALIASES[aliasKey]) return _NAME_ALIASES[aliasKey];
    // Also try first word(s) for short names
    const words = aliasKey.split(' ');
    if (words.length >= 2 && _NAME_ALIASES[words.slice(0, 2).join(' ')]) return _NAME_ALIASES[words.slice(0, 2).join(' ')];
    if (words.length >= 1 && _NAME_ALIASES[words[0]]) return _NAME_ALIASES[words[0]];
    if (!map || !Object.keys(map).length) return '';
    // 2) SEC company_tickers.json lookup — aggressive normalization
    return map[aliasKey] || map[name.toUpperCase().replace(/[^A-Z0-9 ]/g, '').replace(/\s+/g, ' ').trim()] || '';
  }

  function _parse13FXML(xml, tickerMap) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(xml, 'text/xml');
    // SEC XML uses a default namespace so querySelector('infoTable') fails;
    // use getElementsByTagNameNS with wildcard namespace instead.
    const entries = doc.getElementsByTagNameNS('*', 'infoTable');
    const holdings = [];
    Array.from(entries).forEach(entry => {
      const get = tag => entry.getElementsByTagNameNS('*', tag)[0]?.textContent?.trim() || '';
      const name = get('nameOfIssuer');
      const xmlTicker = get('tickerSymbol');
      const resolved = xmlTicker || _resolveTicker(name, tickerMap);
      holdings.push({
        ticker:  resolved,
        name,
        value:   parseInt(get('value'), 10),  // already in dollars (filers ignore the "thousands" spec)
        shares:  parseInt(get('sshPrnamt'), 10),
        putCall: get('putCall') || null,
        type:    get('sshPrnamtType'),
      });
    });

    // Deduplicate: same issuer + same putCall = different voting authority rows → merge
    const merged = new Map();
    holdings.forEach(h => {
      const key = (h.name || '') + '|' + (h.putCall || '');
      if (merged.has(key)) {
        const existing = merged.get(key);
        existing.value  += h.value  || 0;
        existing.shares += h.shares || 0;
      } else {
        merged.set(key, { ...h });
      }
    });

    return Array.from(merged.values()).sort((a, b) => b.value - a.value);
  }

  // Sector performance via SPDR ETFs (Finnhub candles)
  const SPDR_ETFS = ['XLK','XLV','XLF','XLE','XLI','XLY','XLB','XLC','XLRE','XLU','XLP'];
  const SECTOR_NAMES = {
    XLK: '기술 (Technology)', XLV: '헬스케어 (Healthcare)',
    XLF: '금융 (Financials)', XLE: '에너지 (Energy)',
    XLI: '산업재 (Industrials)', XLY: '임의소비재 (Cons. Discretionary)',
    XLB: '소재 (Materials)', XLC: '커뮤니케이션 (Communication)',
    XLRE: '부동산 (Real Estate)', XLU: '유틸리티 (Utilities)',
    XLP: '필수소비재 (Cons. Staples)',
  };

  // Thematic sub-sector ETFs for granular analysis
  const SUB_SECTOR_ETFS = [
    'SOXX','CIBR','SKYY','BOTZ',           // 기술 세부
    'IBB','IHI','PJP',                     // 헬스케어 세부
    'KBE','FINX','BKCH',                   // 금융 세부
    'ITA','UFO','ROBO',                    // 산업재·우주·방산
    'ICLN','FCG',                          // 에너지 세부
    'XRT',                                 // 소비재 세부
  ];
  const SUB_SECTOR_NAMES = {
    SOXX:'반도체 (Semiconductors)',   CIBR:'사이버보안 (Cybersecurity)',
    SKYY:'클라우드 (Cloud Computing)', BOTZ:'AI·로봇자동화 (AI & Robotics)',
    IBB:'바이오테크 (Biotech)',        IHI:'의료기기 (Med Devices)',
    PJP:'빅파마 (Big Pharma)',
    KBE:'은행 (Banks)',               FINX:'핀테크 (Fintech)',
    BKCH:'암호화폐·블록체인 (Crypto)',
    ITA:'항공우주·방산 (Aerospace & Defense)', UFO:'우주 (Space)',
    ROBO:'로봇·자동화 (Robotics)',
    ICLN:'클린에너지 (Clean Energy)', FCG:'천연가스 (Natural Gas)',
    XRT:'리테일 (Retail)',
  };

  async function sectorPerformance() {
    return _cached('sectors', 'spdr_all', async () => {
      const now = Math.floor(Date.now() / 1000);
      const y1ago = now - 365 * 86400;
      const etfs = ['SPY', ...SPDR_ETFS];
      const results = await Promise.allSettled(
        etfs.map(sym => candles(sym, y1ago, now, 'W'))
      );
      const out = {};
      etfs.forEach((sym, i) => {
        if (results[i].status !== 'fulfilled' || !results[i].value) return;
        const c = results[i].value.c;
        if (!c || c.length < 2) return;
        const latest = c[c.length - 1];
        const m1ago  = c[Math.max(0, c.length - 5)];
        const m3ago  = c[Math.max(0, c.length - 13)];
        const m6ago  = c[Math.max(0, c.length - 26)];
        const ytdIdx = _ytdStartIdx(results[i].value.t);
        const ytdBase = c[ytdIdx];
        const y1base  = c[0];
        out[sym] = {
          name:   SECTOR_NAMES[sym] || sym,
          latest,
          ret1m:  m1ago  > 0 ? (latest / m1ago  - 1) * 100 : null,
          ret3m:  m3ago  > 0 ? (latest / m3ago  - 1) * 100 : null,
          ret6m:  m6ago  > 0 ? (latest / m6ago  - 1) * 100 : null,
          retYtd: ytdBase > 0 ? (latest / ytdBase - 1) * 100 : null,
          ret1y:  y1base > 0  ? (latest / y1base  - 1) * 100 : null,
          closes: c,
          dates:  results[i].value.t,
        };
      });
      return out;
    });
  }

  async function subSectorPerformance() {
    return _cached('sectors', 'sub_all', async () => {
      const now = Math.floor(Date.now() / 1000);
      const y1ago = now - 365 * 86400;
      const results = await Promise.allSettled(
        SUB_SECTOR_ETFS.map(sym => candles(sym, y1ago, now, 'W'))
      );
      const out = {};
      SUB_SECTOR_ETFS.forEach((sym, i) => {
        if (results[i].status !== 'fulfilled' || !results[i].value) return;
        const c = results[i].value.c;
        if (!c || c.length < 2) return;
        const latest  = c[c.length - 1];
        const m1ago   = c[Math.max(0, c.length - 5)];
        const m3ago   = c[Math.max(0, c.length - 13)];
        const m6ago   = c[Math.max(0, c.length - 26)];
        const ytdIdx  = _ytdStartIdx(results[i].value.t);
        const ytdBase = c[ytdIdx];
        const y1base  = c[0];
        out[sym] = {
          name:   SUB_SECTOR_NAMES[sym] || sym,
          latest,
          ret1m:  m1ago  > 0 ? (latest / m1ago  - 1) * 100 : null,
          ret3m:  m3ago  > 0 ? (latest / m3ago  - 1) * 100 : null,
          ret6m:  m6ago  > 0 ? (latest / m6ago  - 1) * 100 : null,
          retYtd: ytdBase > 0 ? (latest / ytdBase - 1) * 100 : null,
          ret1y:  y1base > 0  ? (latest / y1base  - 1) * 100 : null,
        };
      });
      return out;
    });
  }

  function _ytdStartIdx(timestamps) {
    const yearStart = new Date(new Date().getFullYear(), 0, 1).getTime() / 1000;
    let idx = 0;
    for (let i = 0; i < timestamps.length; i++) {
      if (timestamps[i] >= yearStart) { idx = i; break; }
    }
    return idx;
  }

  // Map Finnhub industry → broad sector
  function _mapSector(industry) {
    if (!industry) return 'Other';
    const i = industry.toLowerCase();
    if (i.includes('tech') || i.includes('software') || i.includes('semiconductor')) return 'Technology';
    if (i.includes('health') || i.includes('biotech') || i.includes('pharma') || i.includes('medical')) return 'Healthcare';
    if (i.includes('bank') || i.includes('financ') || i.includes('insurance') || i.includes('invest')) return 'Financials';
    if (i.includes('energy') || i.includes('oil') || i.includes('gas') || i.includes('utility')) return 'Energy';
    if (i.includes('consumer') && i.includes('discret')) return 'Consumer Discretionary';
    if (i.includes('consumer') || i.includes('retail') || i.includes('food')) return 'Consumer Staples';
    if (i.includes('industrial') || i.includes('aerospace') || i.includes('defense')) return 'Industrials';
    if (i.includes('material') || i.includes('chemical') || i.includes('mining')) return 'Materials';
    if (i.includes('real estate') || i.includes('reit')) return 'Real Estate';
    if (i.includes('communication') || i.includes('media') || i.includes('telecom')) return 'Communication';
    return industry;
  }

  // VIX (CBOE Volatility Index) — via Yahoo Finance proxy (Finnhub free tier blocks ^VIX)
  async function vix() {
    return _cached('quote', '__VIX__', async () => {
      try {
        const params = new URLSearchParams({ symbol: '^VIX', period: '5d', interval: '1d' });
        const res = await fetch('/api/yhfin?' + params.toString());
        if (!res.ok) throw new Error('yhfin ' + res.status);
        const d = await res.json();
        if (d.s !== 'ok' || !d.c?.length) return null;
        const last  = d.c[d.c.length - 1];
        const prev  = d.c.length >= 2 ? d.c[d.c.length - 2] : last;
        const high  = d.h?.[d.h.length - 1] ?? null;
        const low   = d.l?.[d.l.length - 1] ?? null;
        const chg   = last - prev;
        const chgPct = prev > 0 ? (chg / prev) * 100 : 0;
        return { price: +last.toFixed(2), change: +chg.toFixed(2), changePct: +chgPct.toFixed(1), high, low };
      } catch (_) { return null; }
    });
  }

  // Fear & Greed Index — CNN (via local proxy) primary, alternative.me (crypto) fallback
  // CNN source: production.dataviz.cnn.io — stock market sentiment (authoritative)
  async function fearAndGreed() {
    return _cached('macro', '__fng_cnn_v2__', async () => {
      // ── Primary: CNN Fear & Greed (stock market) ──
      try {
        const res = await fetch('/api/feargreed');
        if (res.ok) {
          const d = await res.json();
          if (d.score != null) {
            const ratingMap = {
              'extreme fear': 'Extreme Fear', 'fear': 'Fear',
              'neutral': 'Neutral', 'greed': 'Greed', 'extreme greed': 'Extreme Greed',
            };
            const rating = ratingMap[(d.rating || '').toLowerCase()] || d.rating || 'N/A';
            return {
              score:     Math.round(d.score),
              rating,
              prevClose: d.previous_close != null ? Math.round(d.previous_close) : null,
              prevWeek:  d.previous_1_week != null ? Math.round(d.previous_1_week) : null,
              prevMonth: d.previous_1_month != null ? Math.round(d.previous_1_month) : null,
              prevYear:  d.previous_1_year != null ? Math.round(d.previous_1_year) : null,
              source:    'CNN',
            };
          }
        }
      } catch (_) { /* fall through */ }

      // ── Fallback: alternative.me (crypto sentiment proxy) ──
      try {
        const res = await fetch('https://api.alternative.me/fng/?limit=30&format=json');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const d = await res.json();
        const entries = d.data;
        if (!entries?.length) return null;
        return {
          score:     parseInt(entries[0].value),
          rating:    entries[0].value_classification,
          prevClose: entries[1] ? parseInt(entries[1].value) : null,
          prevWeek:  entries[6] ? parseInt(entries[6].value) : null,
          prevMonth: entries[29] ? parseInt(entries[29].value) : null,
          prevYear:  null,
          source:    'alternative.me (crypto)',
        };
      } catch (_) { return null; }
    });
  }

  // Yahoo Finance monthly candles via local proxy — returns [{t, o, h, l, c, v}]
  async function yhfinMonthly(symbol, period = '5y') {
    return _cached('macro', 'yhfin_' + symbol + '_' + period, async () => {
      const params = new URLSearchParams({ symbol, period, interval: '1mo' });
      const res = await fetch('/api/yhfin?' + params.toString());
      if (!res.ok) throw new Error('yhfin ' + res.status);
      const d = await res.json();
      if (d.s !== 'ok' || !d.t?.length) return [];
      return d.t.map((t, i) => ({ t, o: d.o[i], h: d.h[i], l: d.l[i], c: d.c[i], v: d.v[i] }));
    });
  }

  return {
    quote, profile, candles, keyMetrics, earnings, insiderTransactions,
    companyNews, marketNews, fred, fredLatest, fredBatch,
    yhfinMonthly, sec13F, sectorPerformance, subSectorPerformance,
    vix, fearAndGreed,
    SPDR_ETFS, SECTOR_NAMES, SUB_SECTOR_ETFS, SUB_SECTOR_NAMES,
  };
})();
