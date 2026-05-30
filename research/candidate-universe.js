// ─────────────────────────────────────────────
//  CANDIDATE UNIVERSE — 신규편입 후보 종목 정의
// ─────────────────────────────────────────────

const CAND_UNIVERSE = (() => {

  // ── 1. Candidate Universe by Theme ──────────
  const CANDIDATE_UNIVERSE = {
    'AI / Cloud':                 ['MSFT', 'NVDA', 'AVGO', 'ORCL', 'NOW', 'SNOW', 'DDOG', 'NET'],
    'Semiconductor':              ['NVDA', 'AVGO', 'AMD', 'TSM', 'ASML', 'AMAT', 'LRCX', 'MU'],
    'Cybersecurity':              ['CRWD', 'PANW', 'ZS', 'NET', 'FTNT'],
    'Biopharma':                  ['NVO', 'REGN', 'VRTX', 'AMGN', 'ABBV'],
    'Medical Devices':            ['SYK', 'MDT', 'BSX', 'EW', 'DXCM'],
    'Digital Finance':            ['SOFI', 'SQ', 'PYPL', 'NU'],
    'Crypto Infrastructure':      ['COIN', 'MSTR'],
    'Space & Aerospace':          ['LMT', 'NOC', 'RTX'],
    'Power / Nuclear Infrastructure': ['CEG', 'VST', 'ETN', 'GEV', 'NEE'],
    'Consumer Internet / Media':  ['SPOT', 'UBER', 'ABNB'],
    'E-commerce / Cloud':         ['SHOP', 'MELI'],
    'Broad Growth ETF':           ['VUG', 'SCHG'],
  };

  // ── 2. Sector Map (GICS Sector ETF) ─────────
  const SECTOR_MAP = {
    // Technology
    MSFT: 'XLK', NVDA: 'XLK', AVGO: 'XLK', ORCL: 'XLK', NOW: 'XLK',
    SNOW: 'XLK', DDOG: 'XLK', NET: 'XLK', AMD: 'XLK', TSM: 'XLK',
    ASML: 'XLK', AMAT: 'XLK', LRCX: 'XLK', MU: 'XLK',
    CRWD: 'XLK', PANW: 'XLK', ZS: 'XLK', FTNT: 'XLK',
    // Healthcare
    NVO: 'XLV', REGN: 'XLV', VRTX: 'XLV', AMGN: 'XLV', ABBV: 'XLV',
    SYK: 'XLV', MDT: 'XLV', BSX: 'XLV', EW: 'XLV', DXCM: 'XLV',
    // Financials
    SOFI: 'XLF', SQ: 'XLF', PYPL: 'XLF', NU: 'XLF', COIN: 'XLF',
    // Industrials / Defense
    LMT: 'XLI', NOC: 'XLI', RTX: 'XLI', ETN: 'XLI', GEV: 'XLI',
    // Utilities
    CEG: 'XLU', VST: 'XLU', NEE: 'XLU',
    // Communication Services
    SPOT: 'XLC', UBER: 'XLC', ABNB: 'XLC',
    // Consumer Discretionary
    SHOP: 'XLY', MELI: 'XLY',
    // Speculative / mixed
    MSTR: 'XLK',
    // ETFs (self-referential — use broad market)
    VUG: 'VUG', SCHG: 'SCHG',
  };

  // ── 3. Theme Map (Korean primary theme) ─────
  const THEME_MAP = {
    MSFT: 'AI/클라우드', NVDA: 'AI/반도체', AVGO: 'AI/반도체', ORCL: 'AI/클라우드',
    NOW: 'AI/클라우드', SNOW: 'AI/클라우드', DDOG: 'AI/클라우드', NET: '사이버보안',
    AMD: '반도체', TSM: '반도체', ASML: '반도체', AMAT: '반도체', LRCX: '반도체', MU: '반도체',
    CRWD: '사이버보안', PANW: '사이버보안', ZS: '사이버보안', FTNT: '사이버보안',
    NVO: '바이오파마', REGN: '바이오파마', VRTX: '바이오파마', AMGN: '바이오파마', ABBV: '바이오파마',
    SYK: '의료기기', MDT: '의료기기', BSX: '의료기기', EW: '의료기기', DXCM: '의료기기',
    SOFI: '디지털금융', SQ: '디지털금융', PYPL: '디지털금융', NU: '디지털금융',
    COIN: '크립토인프라', MSTR: '크립토인프라',
    LMT: '우주/방산', NOC: '우주/방산', RTX: '우주/방산',
    CEG: '전력/원전인프라', VST: '전력/원전인프라', ETN: '전력/원전인프라',
    GEV: '전력/원전인프라', NEE: '전력/원전인프라',
    SPOT: '소비자인터넷/미디어', UBER: '소비자인터넷/미디어', ABNB: '소비자인터넷/미디어',
    SHOP: '이커머스/클라우드', MELI: '이커머스/클라우드',
    VUG: '성장ETF', SCHG: '성장ETF',
  };

  // ── 4. Fundamental Tier ──────────────────────
  const FUNDAMENTAL_TIER = {
    // TIER_1: Fundamental Score likely 75+
    MSFT: 'TIER_1', NVDA: 'TIER_1', AVGO: 'TIER_1', ORCL: 'TIER_1',
    ASML: 'TIER_1', NVO: 'TIER_1', REGN: 'TIER_1', VRTX: 'TIER_1',
    CRWD: 'TIER_1', PANW: 'TIER_1', NOW: 'TIER_1', SHOP: 'TIER_1',
    MELI: 'TIER_1', LMT: 'TIER_1',
    // TIER_2: Solid, Fundamental Score likely 60-74
    AMD: 'TIER_2', TSM: 'TIER_2', AMAT: 'TIER_2', LRCX: 'TIER_2', MU: 'TIER_2',
    ZS: 'TIER_2', NET: 'TIER_2', DDOG: 'TIER_2', SNOW: 'TIER_2',
    SYK: 'TIER_2', BSX: 'TIER_2', EW: 'TIER_2', DXCM: 'TIER_2',
    AMGN: 'TIER_2', ABBV: 'TIER_2',
    COIN: 'TIER_2', ETN: 'TIER_2', CEG: 'TIER_2', VST: 'TIER_2', GEV: 'TIER_2',
    UBER: 'TIER_2', ABNB: 'TIER_2', SQ: 'TIER_2',
    // TIER_3: Uncertain / speculative, Fundamental Score likely 40-59
    FTNT: 'TIER_3', SOFI: 'TIER_3', PYPL: 'TIER_3', NU: 'TIER_3',
    MSTR: 'TIER_3', MDT: 'TIER_3', NOC: 'TIER_3', RTX: 'TIER_3',
    NEE: 'TIER_3', SPOT: 'TIER_3', SCHG: 'TIER_3', VUG: 'TIER_3',
  };

  // ── 5. High-Beta Tickers (beta > 1.5) ───────
  const HIGH_BETA_TICKERS = ['NVDA', 'AMD', 'SNOW', 'DDOG', 'COIN', 'MSTR', 'SOFI', 'NU', 'CRWD', 'NET', 'ZS'];

  // ── 6. Public API ────────────────────────────

  function getAllTickers() {
    const all = Object.values(CANDIDATE_UNIVERSE).flat();
    return [...new Set(all)];
  }

  function getByTheme(theme) {
    return CANDIDATE_UNIVERSE[theme] || [];
  }

  function getFundamentalTier(ticker) {
    return FUNDAMENTAL_TIER[ticker] || 'UNKNOWN';
  }

  function getPrimaryTheme(ticker) {
    return THEME_MAP[ticker] || '기타';
  }

  function getSector(ticker) {
    return SECTOR_MAP[ticker] || null;
  }

  function isHighBeta(ticker) {
    return HIGH_BETA_TICKERS.includes(ticker);
  }

  function getUserUniverse() {
    try {
      const raw = localStorage.getItem('wr_candidate_universe');
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn('[CAND_UNIVERSE] Failed to parse wr_candidate_universe from localStorage', e);
    }
    return CANDIDATE_UNIVERSE;
  }

  function saveUserUniverse(universeObj) {
    try {
      localStorage.setItem('wr_candidate_universe', JSON.stringify(universeObj));
    } catch (e) {
      console.error('[CAND_UNIVERSE] Failed to save user universe to localStorage', e);
    }
  }

  function resetToDefault() {
    localStorage.removeItem('wr_candidate_universe');
  }

  return {
    CANDIDATE_UNIVERSE,
    SECTOR_MAP,
    THEME_MAP,
    FUNDAMENTAL_TIER,
    HIGH_BETA_TICKERS,
    getAllTickers,
    getByTheme,
    getFundamentalTier,
    getPrimaryTheme,
    getSector,
    isHighBeta,
    getUserUniverse,
    saveUserUniverse,
    resetToDefault,
  };

})();
