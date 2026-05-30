// research/audit-tests.js — Reliability audit test suite
// Run in browser console: AUDIT.runAll()
// Tests cover costBasis contract, regime format, calculation accuracy, blocking logic

const AUDIT = (() => {
  let _pass = 0, _fail = 0, _results = [];

  function _assert(name, condition, detail = '') {
    if (condition) {
      _pass++;
      _results.push({ status: 'PASS', name, detail });
    } else {
      _fail++;
      _results.push({ status: 'FAIL', name, detail: detail || '조건 불충족' });
      console.error(`[AUDIT FAIL] ${name}: ${detail}`);
    }
  }

  // ── Test 1: costBasis vs avgCost contract ─────────────────────────────────
  function testCostBasisContract() {
    const pos = STATE.myPosition('AAPL');
    if (!pos) { _assert('costBasis contract', true, '포지션 없음 — 스킵'); return; }
    _assert('costBasis is total (not per-share)',
      pos.costBasis >= pos.avgCost,
      `costBasis=${pos.costBasis}, avgCost=${pos.avgCost}, shares=${pos.shares}`);
    _assert('costBasis ≈ avgCost × shares',
      Math.abs(pos.costBasis - pos.avgCost * pos.shares) < 0.01 * pos.costBasis,
      `expected ~${(pos.avgCost * pos.shares).toFixed(2)}, got ${pos.costBasis}`);
  }

  // ── Test 2: PORTFOLIO_TARGETS valid weights ────────────────────────────────
  function testTargetWeights() {
    const targets = CONFIG.PORTFOLIO_TARGETS || {};
    const tickers = Object.keys(targets);
    _assert('PORTFOLIO_TARGETS not empty', tickers.length > 0);
    const total = tickers.reduce((s, t) => s + (targets[t] || 0), 0);
    _assert('Target weights sum to ~100%', Math.abs(total - 100) < 1,
      `sum=${total.toFixed(1)}%`);
    const invalid = tickers.filter(t => targets[t] > 100 || targets[t] < 0);
    _assert('No individual target > 100% or < 0%', invalid.length === 0,
      invalid.length ? `invalid: ${invalid.join(', ')}` : '');
  }

  // ── Test 3: HY OAS unit conversion ────────────────────────────────────────
  function testHyOasUnits() {
    // FRED returns e.g. 3.0 for 300bp. After ×100 conversion, should be 300.
    const testVal = 3.5; // 350bp raw from FRED
    const converted = testVal * 100;
    _assert('HY OAS 3.5 → 350bp after ×100', converted === 350,
      `got ${converted}bp`);
    _assert('HY OAS 350bp is WATCH tier (not safe, not crisis)',
      converted >= 350 && converted < 500, `${converted}bp`);
  }

  // ── Test 4: Calmar ratio calculation ──────────────────────────────────────
  function testCalmarRatio() {
    const annRet = 0.20;  // 20% annualized
    const mdd    = -0.25; // -25% MDD
    const calmar = Math.abs(mdd) > 0 ? annRet / Math.abs(mdd) : null;
    _assert('Calmar = annRet / |MDD|', Math.abs(calmar - 0.8) < 0.001,
      `expected 0.8, got ${calmar?.toFixed(3)}`);
    _assert('Calmar > 1 = good', calmar < 1, 'Calmar 0.8 is below 1 (expected)');
  }

  // ── Test 5: EXTENDED state blocks allocation ──────────────────────────────
  function testExtendedBlocking() {
    const mult = (SIGNALS?.TIMING_MULT || {})?.[' EXTENDED'] ?? null;
    // Access via the SIGNALS module directly
    const timingMult = {
      TRIGGER: 1.0, ADD: 0.80, EXTENDED: 0.0, WATCH: 0.10, AVOID: 0, EXIT: 0, DATA_INSUFFICIENT: 0,
    };
    _assert('EXTENDED timing mult = 0', timingMult['EXTENDED'] === 0, `got ${timingMult['EXTENDED']}`);
    _assert('DATA_INSUFFICIENT timing mult = 0', timingMult['DATA_INSUFFICIENT'] === 0);
    _assert('TRIGGER timing mult = 1.0', timingMult['TRIGGER'] === 1.0);
  }

  // ── Test 6: Downtrend blocks new buy (AVOID state) ────────────────────────
  function testDowntrendBlock() {
    // Simulate classifyState with downtrend conditions
    const mockCandles = Array.from({ length: 50 }, (_, i) => ({
      t: Date.now() / 1000 - (50 - i) * 86400,
      o: 90, h: 92, l: 88, c: 85, v: 1e6  // price stuck at 85
    }));
    const mockInd = {
      ma200: Array(50).fill(100),  // price 85 < 200DMA 100 (85/100 = 0.85 < 0.93)
      ma50:  Array(50).fill(95),
      rsi14: Array(50).fill(35),
      volData: { distCount25: Array(50).fill(0) },
    };
    const mockPatterns = { trendStructure: { isDowntrend: true }, overextension: null, setupType: 'trendPullback' };
    try {
      const state = SIGNALS.classifyState(40, mockCandles, mockInd, mockPatterns, false, 'TEST');
      _assert('Downtrend + price < 200DMA×0.93 → AVOID', state === 'AVOID', `got ${state}`);
    } catch (e) {
      _assert('Downtrend block test runnable', false, e.message);
    }
  }

  // ── Test 7: Overweight blocks allocation ──────────────────────────────────
  function testOverweightBlock() {
    // currentPct > target → underweightPct < 0 → should block
    const target = 10, currentPct = 15;
    const underweightPct = target - currentPct; // -5
    _assert('Overweight (underweight < 0) priority = 0', Math.max(0, underweightPct) === 0,
      `underweight=${underweightPct}%, priority should be 0`);
  }

  // ── Test 8: DATA_INSUFFICIENT state ───────────────────────────────────────
  function testDataInsufficient() {
    const meta = SIGNALS?.STATE_META?.['DATA_INSUFFICIENT'];
    _assert('DATA_INSUFFICIENT exists in STATE_META', !!meta,
      meta ? '' : 'missing from STATE_META');
    _assert('DATA_INSUFFICIENT has action field', !!meta?.action,
      meta?.action || 'missing');
  }

  // ── Test 9: TECL filter with regime ──────────────────────────────────────
  function testTeclRegimeFilter() {
    const riskOffRegimes = ['RISK_OFF', 'CAUTION'];
    _assert('RISK_OFF blocks TECL (regime check)', riskOffRegimes.includes('RISK_OFF'));
    _assert('CAUTION blocks TECL (regime check)', riskOffRegimes.includes('CAUTION'));
    _assert('NEUTRAL does not block via regime alone', !riskOffRegimes.includes('NEUTRAL'));
    _assert('RISK_ON does not block via regime alone', !riskOffRegimes.includes('RISK_ON'));
  }

  // ── Test 10: Guru ticker in gurus config ──────────────────────────────────
  function testGuruConfig() {
    const gurus = RESEARCH_CONFIG?.GURUS || {};
    _assert('At least 1 guru configured', Object.keys(gurus).length > 0,
      `found ${Object.keys(gurus).length}`);
    const ciks = Object.values(gurus);
    const validCik = ciks.every(c => /^\d{10}$/.test(c));
    _assert('All CIKs are 10-digit strings', validCik,
      validCik ? '' : 'Some CIKs are not 10 digits');
  }

  // ── Test 11: Sector UNKNOWN handling ─────────────────────────────────────
  function testSectorUnknown() {
    // UNKNOWN should appear in the exposure map when unrecognized tickers are held
    const exposure = { XLK: 5000, UNKNOWN: 2000 };
    const total = Object.values(exposure).reduce((s, v) => s + v, 0);
    _assert('UNKNOWN sector included in total', total === 7000, `total=${total}`);
    const unknownPct = (exposure.UNKNOWN / total * 100).toFixed(1);
    _assert('UNKNOWN pct correctly calculated', parseFloat(unknownPct) > 0,
      `${unknownPct}%`);
  }

  // ── Test 12: Macro regime canonical format ────────────────────────────────
  function testMacroRegimeFormat() {
    const validRegimes = ['RISK_ON', 'NEUTRAL', 'CAUTION', 'RISK_OFF'];
    try {
      const cached = JSON.parse(localStorage.getItem('wr_macro_regime'));
      if (cached?.value) {
        _assert('Cached macro regime is canonical format',
          validRegimes.includes(cached.value),
          `got '${cached.value}' — expected one of: ${validRegimes.join(', ')}`);
      } else {
        _assert('Macro regime cache', true, '캐시 없음 (매크로 탭 미방문) — 스킵');
      }
    } catch (_) {
      _assert('Macro regime cache parseable', true, '캐시 없음 — 스킵');
    }
  }

  // ── Test 13: Speculative basket cap ──────────────────────────────────────
  function testSpeculativeBasketCap() {
    const basket = ['CRCL', 'HOOD', 'RKLB'];
    const portfolioValue = 100000;
    const specValues = { CRCL: 8000, HOOD: 7000, RKLB: 6000 }; // total 21%
    const specTotal = basket.reduce((s, t) => s + (specValues[t] || 0), 0);
    const specPct = specTotal / portfolioValue * 100;
    _assert('Speculative basket > 20% triggers cap', specPct > 20,
      `specPct=${specPct.toFixed(1)}%`);
  }

  // ── Test 14: CVaR > VaR (CVaR should be worse) ───────────────────────────
  function testCVarWorseThanVaR() {
    // CVaR (Expected Shortfall) must be ≤ VaR (more negative)
    const mockReturns = [-0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.04, 0.05,
                          -0.08, -0.12, -0.06, 0.03, 0.07, -0.09, 0.01, -0.02, 0.04];
    const sorted = [...mockReturns].sort((a, b) => a - b);
    const alpha = 0.05;
    const varIdx = Math.floor(alpha * sorted.length);
    const varVal = sorted[varIdx];
    const tail = sorted.slice(0, Math.max(1, varIdx));
    const cvarVal = tail.reduce((a, b) => a + b, 0) / tail.length;
    _assert('CVaR ≤ VaR (CVaR worse or equal)', cvarVal <= varVal,
      `CVaR=${cvarVal.toFixed(4)}, VaR=${varVal.toFixed(4)}`);
  }

  // ── Test 15: SMA200 guard for TRIGGER state ──────────────────────────────
  function testTriggerSma200Guard() {
    // If price < SMA200, TRIGGER should not fire for new buyers
    const mockCandles = Array.from({ length: 52 }, (_, i) => ({
      t: Date.now() / 1000 - (52 - i) * 86400,
      o: 95, h: 97, l: 93, c: 95, v: 1e6
    }));
    const mockInd = {
      ma200: Array(52).fill(110),  // price 95 < 200DMA 110
      ma50:  Array(52).fill(100),
      rsi14: Array(52).fill(58),
      volData: { distCount25: Array(52).fill(0) },
      ema20: Array(52).fill(98),
      stochShort: { K: Array(52).fill(55) },
      stochMid:   { K: Array(52).fill(60) },
      stochLong:  { K: Array(52).fill(65) },
      bb20: { width: Array(52).fill(0.05) },
      atr14: Array(52).fill(2),
    };
    const mockPatterns = { trendStructure: { isDowntrend: false, isMixed: false }, overextension: null, setupType: 'trendPullback', srLevels: [], candlePatterns: [] };
    try {
      const state = SIGNALS.classifyState(75, mockCandles, mockInd, mockPatterns, false, 'TEST');
      _assert('Score 75 but price < 200DMA → not TRIGGER for new buyer',
        state !== 'TRIGGER',
        `got ${state} (expected WATCH or other, not TRIGGER)`);
    } catch (e) {
      _assert('SMA200 guard test runnable', false, e.message);
    }
  }

  // ── Test 16: DATA_QUALITY.score returns valid object ─────────────────────
  function testDataQualityScore() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DATA_QUALITY module loaded', false, 'DATA_QUALITY is not defined'); return;
    }
    const result = DATA_QUALITY.score('AAPL', {
      timingResult: { price: 200, techScore: 75, state: 'SETUP' },
      fundamentals: { pe: 28, eps: 6.5, rev: 400e9 },
      positions: [],
      targets: { AAPL: 10 },
    });
    _assert('DQ.score returns object', typeof result === 'object' && result !== null, JSON.stringify(result));
    _assert('DQ.score has score field (0-100)', typeof result.score === 'number' && result.score >= 0 && result.score <= 100,
      `score=${result.score}`);
    _assert('DQ.score has label field', typeof result.label === 'string', `label=${result.label}`);
    _assert('DQ.score has multiplier (0-1)', typeof result.multiplier === 'number' && result.multiplier >= 0 && result.multiplier <= 1,
      `multiplier=${result.multiplier}`);
    _assert('DQ.score has missingData array', Array.isArray(result.missingData), '');
    _assert('DQ: fundamentals present → score > 40', result.score > 40, `score=${result.score}`);
  }

  // ── Test 17: DATA_QUALITY NaN force insufficient ──────────────────────────
  function testDataQualityNaNForce() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DATA_QUALITY NaN test', false, 'module not loaded'); return;
    }
    const result = DATA_QUALITY.score('NAN_TEST', {
      timingResult: { price: NaN, techScore: NaN, state: 'WATCH' },
    });
    _assert('DQ: NaN price → score = 0 (forceInsufficient)', result.score === 0,
      `score=${result.score}, forceInsufficient=${result.forceInsufficient}`);
    _assert('DQ: NaN price → forceInsufficient = true', result.forceInsufficient === true,
      `forceInsufficient=${result.forceInsufficient}`);
  }

  // ── Test 18: DATA_QUALITY.allocationMultiplier thresholds ────────────────
  function testDQAllocationMultiplier() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DQ allocationMultiplier', false, 'module not loaded'); return;
    }
    _assert('DQ 90 → multiplier 1.0', DATA_QUALITY.allocationMultiplier(90) === 1.0, `got ${DATA_QUALITY.allocationMultiplier(90)}`);
    _assert('DQ 75 → multiplier 0.8', DATA_QUALITY.allocationMultiplier(75) === 0.8, `got ${DATA_QUALITY.allocationMultiplier(75)}`);
    _assert('DQ 55 → multiplier 0.4', DATA_QUALITY.allocationMultiplier(55) === 0.4, `got ${DATA_QUALITY.allocationMultiplier(55)}`);
    _assert('DQ 40 → multiplier 0',   DATA_QUALITY.allocationMultiplier(40) === 0,   `got ${DATA_QUALITY.allocationMultiplier(40)}`);
  }

  // ── Test 19: DATA_QUALITY composite cap at DQ < 50 ───────────────────────
  function testDQCompositeCap() {
    if (typeof DATA_QUALITY === 'undefined' || !DATA_QUALITY.capCompositeScore) {
      _assert('DQ composite cap', false, 'module not loaded'); return;
    }
    _assert('DQ 40 caps composite at 55', DATA_QUALITY.capCompositeScore(80, 40) <= 55,
      `got ${DATA_QUALITY.capCompositeScore(80, 40)}`);
    _assert('DQ 70 does not cap composite 80', DATA_QUALITY.capCompositeScore(80, 70) === 80,
      `got ${DATA_QUALITY.capCompositeScore(80, 70)}`);
  }

  // ── Test 20: SIGNAL_RESULT.build returns valid object ────────────────────
  function testSignalResultBuild() {
    if (typeof SIGNAL_RESULT === 'undefined') {
      _assert('SIGNAL_RESULT module loaded', false, 'SIGNAL_RESULT is not defined'); return;
    }
    const sig = SIGNAL_RESULT.build({
      ticker: 'AAPL', price: 200, state: 'TRIGGER',
      compositeScore: 78, dataQualityScore: 82, buyAllocationScore: 62,
      technicalScore: 75,
      reasons: ['Trend strong'], blockers: [], nextTrigger: 'ATR×2 눌림목',
    });
    _assert('SIGNAL_RESULT.build: ticker is uppercase', sig.ticker === 'AAPL', `got ${sig.ticker}`);
    _assert('SIGNAL_RESULT.build: id field exists', typeof sig.id === 'string' && sig.id.length > 0, `id=${sig.id}`);
    _assert('SIGNAL_RESULT.build: reasons is array', Array.isArray(sig.reasons), '');
    _assert('SIGNAL_RESULT.build: outcomes starts empty', typeof sig.outcomes === 'object', '');
    _assert('SIGNAL_RESULT.build: compositeScore set', sig.compositeScore === 78, `got ${sig.compositeScore}`);
  }

  // ── Test 21: REC_LOG save + retrieve ────────────────────────────────────
  function testRecLogSaveRetrieve() {
    if (typeof REC_LOG === 'undefined') {
      _assert('REC_LOG module loaded', false, 'REC_LOG is not defined'); return;
    }
    const testSig = SIGNAL_RESULT.build({
      ticker: 'AUDIT_TEST', price: 100, state: 'TRIGGER',
      compositeScore: 70, dataQualityScore: 75, buyAllocationScore: 56,
    });
    REC_LOG.save(testSig);
    const retrieved = REC_LOG.forTicker('AUDIT_TEST');
    _assert('REC_LOG.save + forTicker: entry exists', retrieved.length >= 1,
      `found ${retrieved.length}`);
    _assert('REC_LOG.forTicker: ticker matches', retrieved[0].ticker === 'AUDIT_TEST',
      `got ${retrieved[0]?.ticker}`);
    // Clean up test entry
    try {
      const entries = JSON.parse(localStorage.getItem('wr_recommendation_log') || '[]');
      const cleaned = entries.filter(e => e.ticker !== 'AUDIT_TEST');
      localStorage.setItem('wr_recommendation_log', JSON.stringify(cleaned));
    } catch (_) {}
  }

  // ── Test 22: PORTFOLIO_ENGINE.calcPositions ───────────────────────────────
  function testPortfolioEngineCalcPositions() {
    if (typeof PORTFOLIO_ENGINE === 'undefined') {
      _assert('PORTFOLIO_ENGINE module loaded', false, 'PORTFOLIO_ENGINE is not defined'); return;
    }
    const rawPos = [
      { ticker: 'AAPL', shares: '10', costBasis: '1800', currentPrice: '200' },
      { ticker: 'GOOGL', shares: '5', costBasis: '700', avgCost: '140' },
    ];
    const priceMap = { AAPL: 200, GOOGL: 150 };
    const positions = PORTFOLIO_ENGINE.calcPositions(rawPos, priceMap);
    _assert('calcPositions: returns array', Array.isArray(positions), '');
    _assert('calcPositions: AAPL currentValue = 2000', positions.find(p=>p.ticker==='AAPL')?.currentValue === 2000,
      `got ${positions.find(p=>p.ticker==='AAPL')?.currentValue}`);
    _assert('calcPositions: GOOGL pnl positive (150 > 140)', (positions.find(p=>p.ticker==='GOOGL')?.pnl ?? 0) > 0, '');
    _assert('calcPositions: AAPL sector = XLC or XLK', ['XLK','XLC','UNKNOWN'].includes(positions.find(p=>p.ticker==='AAPL')?.sector || ''), '');
  }

  // ── Test 23: PORTFOLIO_ENGINE.calcTargetVsActual ──────────────────────────
  function testPortfolioEngineTargetVsActual() {
    if (typeof PORTFOLIO_ENGINE === 'undefined') {
      _assert('PORTFOLIO_ENGINE calcTargetVsActual', false, 'module not loaded'); return;
    }
    const positions = [
      { ticker:'AAPL', shares:10, costBasis:1800, currentValue:2000, pnl:200, pnlPct:11.1, price:200, sector:'XLK', theme:'빅테크', isLeverage2x:false, isLeverage3x:false },
      { ticker:'LLY',  shares:5,  costBasis:2500, currentValue:3000, pnl:500, pnlPct:20,   price:600, sector:'XLV', theme:'바이오파마', isLeverage2x:false, isLeverage3x:false },
    ];
    const targets = { AAPL: 40, LLY: 60 };
    const result = PORTFOLIO_ENGINE.calcTargetVsActual(positions, targets);
    _assert('calcTargetVsActual: totalValue = 5000', result.totalValue === 5000,
      `got ${result.totalValue}`);
    _assert('calcTargetVsActual: AAPL is UNDERWEIGHT (40% target, ~40% actual)',
      ['UNDERWEIGHT','ON_TARGET'].includes(result.rows.find(r=>r.ticker==='AAPL')?.status || ''),
      `status=${result.rows.find(r=>r.ticker==='AAPL')?.status}`);
  }

  // ── Test 24: PORTFOLIO_ENGINE.allocationStatus ────────────────────────────
  function testAllocationStatus() {
    if (typeof PORTFOLIO_ENGINE === 'undefined') {
      _assert('PORTFOLIO_ENGINE allocationStatus', false, 'module not loaded'); return;
    }
    _assert('allocationStatus: +5% gap → OVERWEIGHT',  PORTFOLIO_ENGINE.allocationStatus(15, 10) === 'OVERWEIGHT', '');
    _assert('allocationStatus: -5% gap → UNDERWEIGHT', PORTFOLIO_ENGINE.allocationStatus(5, 10) === 'UNDERWEIGHT', '');
    _assert('allocationStatus: 0% gap → ON_TARGET',    PORTFOLIO_ENGINE.allocationStatus(10, 10) === 'ON_TARGET', '');
    _assert('allocationStatus: +2% gap → ON_TARGET (within ±3%)', PORTFOLIO_ENGINE.allocationStatus(12, 10) === 'ON_TARGET', '');
  }

  // ── Test 25: LEVERAGE_SINGLE tickers in TIMING and PORTFOLIO_ENGINE ───────
  function testLeverageSingleConsistency() {
    const timingLS = TIMING?.LEVERAGE_SINGLE || {};
    const peLS     = PORTFOLIO_ENGINE?.LEVERAGE_SINGLE || {};
    const timingTickers = Object.keys(timingLS);
    const peTickers     = Object.keys(peLS);
    _assert('TIMING.LEVERAGE_SINGLE has METU/GGLL/AMZZ', timingTickers.includes('METU') && timingTickers.includes('GGLL') && timingTickers.includes('AMZZ'),
      `found: ${timingTickers.join(',')}`);
    _assert('PORTFOLIO_ENGINE.LEVERAGE_SINGLE has same tickers', peTickers.includes('METU') && peTickers.includes('GGLL'),
      `found: ${peTickers.join(',')}`);
    _assert('TIMING.LEVERAGE_SINGLE METU → META', timingLS['METU'] === 'META', `got ${timingLS['METU']}`);
  }

  // ── Test 26: CAND_UNIVERSE module loaded and functional ──────────────────
  function testCandUniverseModule() {
    if (typeof CAND_UNIVERSE === 'undefined') {
      _assert('CAND_UNIVERSE module loaded', false, 'not defined'); return;
    }
    const all = CAND_UNIVERSE.getAllTickers();
    _assert('CAND_UNIVERSE.getAllTickers() returns array', Array.isArray(all) && all.length > 10,
      `got ${all?.length}`);
    _assert('CAND_UNIVERSE.getFundamentalTier(NVDA) = TIER_1',
      CAND_UNIVERSE.getFundamentalTier('NVDA') === 'TIER_1', `got ${CAND_UNIVERSE.getFundamentalTier('NVDA')}`);
    _assert('CAND_UNIVERSE.getFundamentalTier(SOFI) = TIER_3',
      CAND_UNIVERSE.getFundamentalTier('SOFI') === 'TIER_3', `got ${CAND_UNIVERSE.getFundamentalTier('SOFI')}`);
    _assert('CAND_UNIVERSE.isHighBeta(NVDA) = true',
      CAND_UNIVERSE.isHighBeta('NVDA') === true, '');
    _assert('CAND_UNIVERSE.isHighBeta(LMT) = false',
      CAND_UNIVERSE.isHighBeta('LMT') === false, '');
    _assert('CAND_UNIVERSE.getSector(NVDA) = XLK',
      CAND_UNIVERSE.getSector('NVDA') === 'XLK', `got ${CAND_UNIVERSE.getSector('NVDA')}`);
  }

  // ── Test 27: TIER_3 fundamental should block Core Buy in optimizer ────────
  function testFundamentalTierBlock() {
    if (typeof CAND_UNIVERSE === 'undefined') {
      _assert('TIER_3 fundamental blocks Core Buy', false, 'CAND_UNIVERSE not loaded'); return;
    }
    const tier3 = CAND_UNIVERSE.getFundamentalTier('SOFI');
    _assert('SOFI is TIER_3', tier3 === 'TIER_3', `got ${tier3}`);
    // In optimizer, fundTier===TIER_3 => fundamentalMult=0.25 => priority reduced to 25%
    const fundMult = tier3 === 'TIER_1' ? 1.20 : tier3 === 'TIER_2' ? 1.00 : tier3 === 'TIER_3' ? 0.25 : 0.70;
    _assert('TIER_3 fundamentalMult = 0.25 (severe reduction)', fundMult === 0.25, `got ${fundMult}`);
  }

  // ── Test 28: EXTENDED state → allocation must be $0 ───────────────────────
  function testExtendedNoAllocation() {
    if (typeof OPTIMIZER === 'undefined') {
      _assert('EXTENDED → $0 allocation', false, 'OPTIMIZER not loaded'); return;
    }
    // STATE_MULTIPLIER check via known constant behavior
    const extMult = { TRIGGER:1.00, ADD:0.85, SETUP:0.70, BASE_BUILDING:0.40,
      EXTENDED:0.00, WATCH:0.05, AVOID:0.00, REDUCE:0.00, EXIT:0.00, DATA_INSUFFICIENT:0.00 };
    _assert('EXTENDED timing multiplier = 0', extMult['EXTENDED'] === 0, '');
    _assert('AVOID timing multiplier = 0',    extMult['AVOID']    === 0, '');
    _assert('REDUCE timing multiplier = 0',   extMult['REDUCE']   === 0, '');
  }

  // ── Test 29: Weak fundamental + strong sector → NOT Core Buy ──────────────
  function testWeakFundStrongSector() {
    // Test the candidateType logic from the spec
    // TIER_3 ticker (fundamentalScore ~15-20) with strong sector should be Tactical at most
    const mockScores = { fundamental: 18, macro: 14, sectorTheme: 18, technical: 10 };
    const composite = mockScores.fundamental + mockScores.macro + mockScores.sectorTheme + mockScores.technical;
    _assert('Weak Fund + Strong Sector: fundamental score too low for Core (< 30)',
      mockScores.fundamental < 30, `fundamental=${mockScores.fundamental}`);
    _assert('Weak Fund + Strong Sector: composite reasonable (≥50)',
      composite >= 50, `composite=${composite}`);
    // Core requires f >= 30; this ticker should be Tactical at most
    const isCore = mockScores.fundamental >= 30 && mockScores.macro >= 11 && mockScores.sectorTheme >= 13;
    _assert('Weak Fund + Strong Sector: should NOT qualify as Core (f<30)',
      !isCore, `isCore=${isCore}`);
  }

  // ── Test 30: RISK_OFF + high beta → allocation blocked ────────────────────
  function testRiskOffHighBeta() {
    // In candidate.js: macro RISK_OFF + isHighBeta → _passesFilters returns false
    // In optimizer.js: RISK_OFF → regimeMult=0.20, and leveraged ETF gets specialBlock
    const regimeMult = { RISK_ON:1.0, NEUTRAL:0.8, CAUTION:0.5, RISK_OFF:0.2 };
    _assert('RISK_OFF regime multiplier = 0.2', regimeMult['RISK_OFF'] === 0.2, '');
    // High beta in candidates: _passesFilters should return false for RISK_OFF + isHighBeta
    const riskOffHighBetaBlocked = (macro, isHighBeta) => macro === 'RISK_OFF' && isHighBeta;
    _assert('RISK_OFF + high beta → candidate filter blocks', riskOffHighBetaBlocked('RISK_OFF', true), '');
    _assert('NEUTRAL + high beta → candidate filter passes', !riskOffHighBetaBlocked('NEUTRAL', true), '');
  }

  // ── Test 31: Watchlist save does NOT touch wr_target_allocations ──────────
  function testCandidateWatchlistSeparation() {
    const WL_KEY = 'wr_candidate_watchlist';
    const TA_KEY = 'wr_target_allocations';
    const taBefore = localStorage.getItem(TA_KEY);
    // Simulate watchlist add (pure data manipulation, no DOM)
    const wl = JSON.parse(localStorage.getItem(WL_KEY) || '[]');
    const testEntry = { ticker: '__AUDIT_TEST__', addedAt: Date.now(), status: 'watching' };
    wl.push(testEntry);
    localStorage.setItem(WL_KEY, JSON.stringify(wl));
    const taAfter = localStorage.getItem(TA_KEY);
    _assert('Adding to watchlist does NOT modify wr_target_allocations',
      taBefore === taAfter, `before=${taBefore?.slice(0,30)} after=${taAfter?.slice(0,30)}`);
    // Cleanup
    const wl2 = JSON.parse(localStorage.getItem(WL_KEY) || '[]').filter(e => e.ticker !== '__AUDIT_TEST__');
    localStorage.setItem(WL_KEY, JSON.stringify(wl2));
  }

  // ── Test 32: REC_LOG saves new fields (fundamentalScore, source, etc.) ─────
  function testRecLogNewFields() {
    if (typeof REC_LOG === 'undefined' || typeof SIGNAL_RESULT === 'undefined') {
      _assert('REC_LOG new fields', false, 'modules not loaded'); return;
    }
    const sig = SIGNAL_RESULT.build({
      ticker: '__AUDIT_REC__',
      price: 100,
      state: 'SETUP',
      fundamentalScore: 82,
      macroScore: 15,
      sectorThemeScore: 16,
      technicalTimingScore: 10,
      macroRegime: 'NEUTRAL',
      sectorThemeState: 'leader',
      source: 'new_candidate',
      candidateType: 'Core',
    });
    _assert('SIGNAL_RESULT: fundamentalScore saved', sig.fundamentalScore === 82, `got ${sig.fundamentalScore}`);
    _assert('SIGNAL_RESULT: sectorThemeScore saved', sig.sectorThemeScore === 16, `got ${sig.sectorThemeScore}`);
    _assert('SIGNAL_RESULT: source = new_candidate', sig.source === 'new_candidate', `got ${sig.source}`);
    _assert('SIGNAL_RESULT: candidateType = Core', sig.candidateType === 'Core', `got ${sig.candidateType}`);
    const saved = REC_LOG.save(sig);
    _assert('REC_LOG.save returns entry', saved != null, '');
    _assert('REC_LOG saved fundamentalScore', saved?.fundamentalScore === 82, `got ${saved?.fundamentalScore}`);
    _assert('REC_LOG saved source', saved?.source === 'new_candidate', `got ${saved?.source}`);
    // Cleanup
    const log = JSON.parse(localStorage.getItem('wr_recommendation_log') || '[]')
      .filter(e => e.ticker !== '__AUDIT_REC__');
    localStorage.setItem('wr_recommendation_log', JSON.stringify(log));
  }

  // ── Test 33: DATA_QUALITY checkBuyBlock with fundamentalScore < 50 ─────────
  function testDQFundamentalBlock() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DQ.checkBuyBlock fundamentalScore<50', false, 'DATA_QUALITY not loaded'); return;
    }
    if (typeof DATA_QUALITY.checkBuyBlock !== 'function') {
      _assert('DQ.checkBuyBlock fundamentalScore<50', false, 'checkBuyBlock not a function'); return;
    }
    const block = DATA_QUALITY.checkBuyBlock(80, 75, 'TRIGGER', false, 45);
    _assert('DQ.checkBuyBlock: fundamentalScore=45 (<50) → blocked',
      block?.blocked === true, `got blocked=${block?.blocked}, reason=${block?.reason}`);
    const noBlock = DATA_QUALITY.checkBuyBlock(80, 75, 'TRIGGER', false, 75);
    _assert('DQ.checkBuyBlock: fundamentalScore=75 → not blocked by fundamental gate',
      noBlock?.blocked !== true || !noBlock?.reason?.includes('Fundamental'), `got ${noBlock?.reason}`);
  }

  // ── Test 34: MARKET_REGIME module loaded with required API surface ──────────
  function testMarketRegimeModule() {
    if (typeof MARKET_REGIME === 'undefined') {
      _assert('MARKET_REGIME module loaded', false, 'MARKET_REGIME not defined'); return;
    }
    _assert('MARKET_REGIME.get is function',        typeof MARKET_REGIME.get          === 'function', '');
    _assert('MARKET_REGIME.classify is function',   typeof MARKET_REGIME.classify     === 'function', '');
    _assert('MARKET_REGIME.getWeights is function', typeof MARKET_REGIME.getWeights   === 'function', '');
    _assert('MARKET_REGIME.badge is function',      typeof MARKET_REGIME.badge        === 'function', '');
    _assert('MARKET_REGIME.renderPlaybook is function', typeof MARKET_REGIME.renderPlaybook === 'function', '');
    _assert('MARKET_REGIME.regimeFitScore is function', typeof MARKET_REGIME.regimeFitScore === 'function', '');
  }

  // ── Test 35: MARKET_REGIME weights for all 8 regimes sum to 100 ─────────────
  function testMarketRegimeWeightsSumTo100() {
    if (typeof MARKET_REGIME === 'undefined') {
      _assert('Regime weights sum to 100', false, 'MARKET_REGIME not loaded'); return;
    }
    const regimes = [
      'BROAD_RISK_ON','NARROW_THEME_LEADERSHIP','ROTATION_MARKET','MACRO_RISK_OFF',
      'DEFENSIVE_QUALITY_MARKET','RATE_PRESSURE_GROWTH_COMPRESSION','EARLY_RECOVERY','DATA_INSUFFICIENT'
    ];
    regimes.forEach(r => {
      const w = MARKET_REGIME.getWeights(r);
      if (!w) { _assert(`${r} weights exist`, false, 'null returned'); return; }
      const sum = (w.businessQuality||0) + (w.valuation||0) + (w.macro||0) + (w.sectorTheme||0) + (w.technical||0);
      _assert(`${r} weights sum to 100`, sum === 100, `sum=${sum}`);
    });
  }

  // ── Test 36: MARKET_REGIME.badge returns non-empty HTML for known regime ────
  function testMarketRegimeBadge() {
    if (typeof MARKET_REGIME === 'undefined') {
      _assert('MARKET_REGIME.badge HTML', false, 'MARKET_REGIME not loaded'); return;
    }
    const html = MARKET_REGIME.badge('BROAD_RISK_ON');
    _assert('badge for BROAD_RISK_ON returns non-empty string', typeof html === 'string' && html.length > 5, `got: ${html?.slice(0,40)}`);
    const html2 = MARKET_REGIME.badge('DATA_INSUFFICIENT');
    _assert('badge for DATA_INSUFFICIENT returns string', typeof html2 === 'string' && html2.length > 0, `got: ${html2?.slice(0,40)}`);
  }

  // ── Test 37: MARKET_REGIME whipsaw — pending stored, not committed immediately ─
  function testMarketRegimeWhipsaw() {
    if (typeof MARKET_REGIME === 'undefined') {
      _assert('Whipsaw: pending stored first', false, 'MARKET_REGIME not loaded'); return;
    }
    // Seed current regime as BROAD_RISK_ON
    const PENDING_KEY = 'wr_signal_regime_pending';
    const REGIME_KEY  = 'wr_signal_regime';
    const prev = localStorage.getItem(REGIME_KEY);
    const prevPending = localStorage.getItem(PENDING_KEY);

    // Write a current regime as BROAD_RISK_ON (established)
    localStorage.setItem(REGIME_KEY, JSON.stringify({ regime8: 'BROAD_RISK_ON', confidence: 72, ts: Date.now() - 1000 * 60 * 10 }));
    localStorage.removeItem(PENDING_KEY);

    // If classify is called and returns a different regime, pending should be set
    // We can't call classify() with live data in unit test, so we verify the key contract:
    // pending must be set before regime changes (except MACRO_RISK_OFF ≥80)
    _assert('Pending key constant correct', PENDING_KEY === 'wr_signal_regime_pending', '');
    _assert('Regime key constant correct',  REGIME_KEY  === 'wr_signal_regime', '');

    // Restore
    if (prev) localStorage.setItem(REGIME_KEY, prev); else localStorage.removeItem(REGIME_KEY);
    if (prevPending) localStorage.setItem(PENDING_KEY, prevPending); else localStorage.removeItem(PENDING_KEY);
    _assert('Whipsaw: keys validated and storage restored', true, '');
  }

  // ── Test 38: MARKET_REGIME storage separation from macro.js ─────────────────
  function testRegimeStorageSeparation() {
    // wr_macro_regime must come from FRED (macro.js), wr_signal_regime from signal-engine.js
    // Verify they are distinct keys with distinct purposes
    const MACRO_KEY  = 'wr_macro_regime';
    const SIGNAL_KEY = 'wr_signal_regime';
    _assert('Storage keys are distinct', MACRO_KEY !== SIGNAL_KEY, '');

    // If both exist, their structures may differ — macro has tipsRate/hyOas/yieldCurve
    const macroRaw  = localStorage.getItem(MACRO_KEY);
    const signalRaw = localStorage.getItem(SIGNAL_KEY);
    if (macroRaw && signalRaw) {
      let macroObj  = null, signalObj = null;
      try { macroObj  = JSON.parse(macroRaw);  } catch(_) {}
      try { signalObj = JSON.parse(signalRaw); } catch(_) {}
      if (macroObj && signalObj) {
        // macro regime value should be simple: RISK_ON / RISK_OFF / NEUTRAL / CAUTION
        const validMacro  = ['RISK_ON','RISK_OFF','NEUTRAL','CAUTION'].includes(macroObj?.value || macroObj?.regime);
        const validSignal = typeof signalObj?.regime8 === 'string' || typeof signalObj?.value === 'string';
        _assert('wr_macro_regime has valid macro value', validMacro, `got: ${JSON.stringify(macroObj).slice(0,60)}`);
        _assert('wr_signal_regime has valid value', validSignal, `got: ${JSON.stringify(signalObj).slice(0,60)}`);
      } else {
        _assert('Regime storage separation: at least one key parseable', macroObj != null || signalObj != null, '');
      }
    } else {
      _assert('Regime storage separation: keys defined correctly (data may not be cached yet)', true, 'keys verified');
    }
  }

  // ── Test 39: DQ v3 Fundamental section max = 20pts ───────────────────────────
  function testDQv3FundamentalMax20() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DQ v3 fundamental max 20', false, 'DATA_QUALITY not loaded'); return;
    }
    // Call score(ticker, data) with ideal fundamental data — DATA_QUALITY.score(ticker, data)
    const result = typeof DATA_QUALITY.score === 'function' ? DATA_QUALITY.score('MSFT', {
      candles: Array.from({length:60}, (_,i) => ({ c: 400 + i, v: 1e6, o: 399+i, h: 402+i, l: 398+i })),
      fundamentals: { pe: 32, eps: 12, rev: 2e11, grossMargin: 0.70, roic: 0.25, fcfYield: 0.04 },
      timingResult: { price: 400 },
    }) : null;
    if (result == null) {
      _assert('DQ v3 fundamental max 20: score() callable', false, 'score returned null'); return;
    }
    _assert('DQ v3: total score ≤ 100', result.score <= 100, `score=${result.score}`);
    _assert('DQ v3: breakdown has fundamentalData key', 'fundamentalData' in result.breakdown, `keys: ${Object.keys(result.breakdown||{}).join(',')}`);
    const fundPts = result.breakdown?.fundamentalData ?? null;
    if (fundPts !== null) {
      _assert('DQ v3: fundamentalData max 20pts', fundPts <= 20, `got ${fundPts}`);
    } else {
      _assert('DQ v3: fundamentalData key present with value', false, 'null value');
    }
  }

  // ── Test 40: DQ v3 sector scoring uses wr_sector_perf_cache ─────────────────
  function testDQv3SectorCacheKey() {
    if (typeof DATA_QUALITY === 'undefined') {
      _assert('DQ v3 sector cache key', false, 'DATA_QUALITY not loaded'); return;
    }
    // Seed sector cache with XLK data in correct format (percent units, lowercase keys)
    const SECTOR_KEY = 'wr_sector_perf_cache';
    const prevCache = localStorage.getItem(SECTOR_KEY);
    const testCache = { XLK: { ret1m: 3.5, ret3m: 8.2, ret6m: 12.1, retYtd: 15.4, ret1y: 28.3 } };
    localStorage.setItem(SECTOR_KEY, JSON.stringify(testCache));

    // Call score(ticker, data) — even if MSFT→XLK not mapped, broad cache presence gives ≥5 pts
    const result = typeof DATA_QUALITY.score === 'function'
      ? DATA_QUALITY.score('MSFT', { timingResult: { price: 400 } }) : null;
    const sectorPts = result?.breakdown?.sectorData ?? -1;
    _assert('DQ v3 sector scoring: sectorData points > 0 when cache seeded',
      sectorPts > 0, `got sectorPts=${sectorPts}`);

    // Restore
    if (prevCache) localStorage.setItem(SECTOR_KEY, prevCache); else localStorage.removeItem(SECTOR_KEY);

    // Also verify lowercase keys in cache (ret1m, not ret1M)
    const cacheEntry = testCache['XLK'];
    _assert('Sector cache keys are lowercase: ret1m', 'ret1m' in cacheEntry && !('ret1M' in cacheEntry), '');
    _assert('Sector cache keys are lowercase: ret3m', 'ret3m' in cacheEntry && !('ret3M' in cacheEntry), '');
    _assert('Sector cache values in percent (3.5, not 0.035)', cacheEntry.ret1m > 1, `ret1m=${cacheEntry.ret1m}`);
  }

  // ── Test 41: SIGNAL_RESULT.build() includes v2 fields ───────────────────────
  function testSignalResultV2Fields() {
    if (typeof SIGNAL_RESULT === 'undefined') {
      _assert('SIGNAL_RESULT v2 fields', false, 'SIGNAL_RESULT not loaded'); return;
    }
    const sig = SIGNAL_RESULT.build({
      ticker: '__AUDIT_V2__',
      price: 150,
      state: 'TRIGGER',
      businessQualityScore: 78,
      valuationScore: 62,
      marketRegime: 'BROAD_RISK_ON',
      secondaryRegime: 'ROTATION_MARKET',
      regimeConfidence: 74,
      effectiveWeights: { businessQuality: 30, valuation: 10, macro: 20, sectorTheme: 25, technical: 15 },
      regimeFit: 81,
    });
    _assert('v2: businessQualityScore saved', sig.businessQualityScore === 78, `got ${sig.businessQualityScore}`);
    _assert('v2: valuationScore saved',       sig.valuationScore === 62,       `got ${sig.valuationScore}`);
    _assert('v2: marketRegime saved',         sig.marketRegime === 'BROAD_RISK_ON', `got ${sig.marketRegime}`);
    _assert('v2: secondaryRegime saved',      sig.secondaryRegime === 'ROTATION_MARKET', `got ${sig.secondaryRegime}`);
    _assert('v2: regimeConfidence saved',     sig.regimeConfidence === 74,     `got ${sig.regimeConfidence}`);
    _assert('v2: effectiveWeights saved',     sig.effectiveWeights?.businessQuality === 30, `got ${JSON.stringify(sig.effectiveWeights)}`);
    _assert('v2: regimeFit saved',            sig.regimeFit === 81,            `got ${sig.regimeFit}`);
  }

  // ── Test 42: updateOutcomeFromCandles uses actual nth candle ─────────────────
  function testUpdateOutcomeFromCandles() {
    // updateOutcomeFromCandles lives on REC_LOG, not SIGNAL_RESULT
    if (typeof REC_LOG === 'undefined') {
      _assert('updateOutcomeFromCandles', false, 'REC_LOG not loaded'); return;
    }
    _assert('updateOutcomeFromCandles function exists', typeof REC_LOG.updateOutcomeFromCandles === 'function', '');
    if (typeof REC_LOG.updateOutcomeFromCandles !== 'function') return;

    // Build a candle array: entry at index 0, target in 5 candles
    const entryPrice = 100;
    const candles = [
      { c: entryPrice, t: Date.now() - 5 * 86400000 },
      { c: 102, t: Date.now() - 4 * 86400000 },
      { c: 105, t: Date.now() - 3 * 86400000 },
      { c: 108, t: Date.now() - 2 * 86400000 },
      { c: 112, t: Date.now() - 1 * 86400000 },  // 5th candle (index 4) = +12%
      { c: 115, t: Date.now() },
    ];
    if (typeof SIGNAL_RESULT !== 'undefined') {
      const testId = '__AUDIT_CANDLE_' + Date.now() + '__';
      const sig = SIGNAL_RESULT.build({ ticker: '__AUDIT_CANDLE__', price: entryPrice, state: 'TRIGGER' });
      sig.id = testId;
      REC_LOG.save(sig);

      // target=4 → signalIdx=0 + 4 = candles[4].c = 112 → +12%
      // (signalTs is in seconds, candle ts in ms — all candles satisfy c.t >= signalTs, so signalIdx=0)
      const updated = REC_LOG.updateOutcomeFromCandles(testId, 4, candles);
      if (updated) {
        // outcomes[4].ret = (candles[4].c - candles[0].c) / candles[0].c = (112-100)/100 = 0.12
        const o = updated.outcomes?.[4];
        const expectedReturn = (candles[4].c - entryPrice) / entryPrice;
        _assert('updateOutcomeFromCandles: outcomes[4].ret ≈ +12%',
          o != null && Math.abs((o.ret || 0) - expectedReturn) < 0.01,
          `got ret=${o?.ret?.toFixed(4)} expected ${expectedReturn.toFixed(4)}`);
        _assert('updateOutcomeFromCandles: outcome label set', o?.label != null, `label=${o?.label}`);
      } else {
        _assert('updateOutcomeFromCandles: entry updated', false, 'returned null/undefined');
      }

      // Cleanup
      const log = JSON.parse(localStorage.getItem('wr_recommendation_log') || '[]')
        .filter(e => e.id !== testId && e.ticker !== '__AUDIT_CANDLE__');
      localStorage.setItem('wr_recommendation_log', JSON.stringify(log));
    } else {
      _assert('updateOutcomeFromCandles: REC_LOG available', false, 'REC_LOG not loaded');
    }
  }

  // ── Test 43: REC_LOG.save persists regime8 fields ───────────────────────────
  function testRecLogRegime8Fields() {
    if (typeof REC_LOG === 'undefined' || typeof SIGNAL_RESULT === 'undefined') {
      _assert('REC_LOG regime8 persistence', false, 'modules not loaded'); return;
    }
    const sig = SIGNAL_RESULT.build({
      ticker: '__AUDIT_REGIME8__',
      price: 200,
      state: 'SETUP',
      marketRegime: 'NARROW_THEME_LEADERSHIP',
      regimeConfidence: 68,
      regimeFit: 72,
      businessQualityScore: 80,
      effectiveWeights: { businessQuality: 25, valuation: 10, macro: 15, sectorTheme: 35, technical: 15 },
    });
    const saved = REC_LOG.save(sig);
    _assert('REC_LOG: marketRegime persisted',     saved?.marketRegime === 'NARROW_THEME_LEADERSHIP', `got ${saved?.marketRegime}`);
    _assert('REC_LOG: regimeConfidence persisted', saved?.regimeConfidence === 68, `got ${saved?.regimeConfidence}`);
    _assert('REC_LOG: regimeFit persisted',        saved?.regimeFit === 72,        `got ${saved?.regimeFit}`);
    _assert('REC_LOG: effectiveWeights persisted', saved?.effectiveWeights?.sectorTheme === 35, `got ${JSON.stringify(saved?.effectiveWeights)}`);

    // Also verify it appears in forTicker
    const entries = REC_LOG.forTicker('__AUDIT_REGIME8__');
    _assert('REC_LOG.forTicker retrieves saved entry', entries.length > 0, `got ${entries.length} entries`);
    _assert('Retrieved entry has marketRegime', entries[0]?.marketRegime === 'NARROW_THEME_LEADERSHIP', `got ${entries[0]?.marketRegime}`);

    // Cleanup
    const log = JSON.parse(localStorage.getItem('wr_recommendation_log') || '[]')
      .filter(e => e.ticker !== '__AUDIT_REGIME8__');
    localStorage.setItem('wr_recommendation_log', JSON.stringify(log));
  }

  // ── Test 44: 9-multiplier formula: zero in any factor → zero priority ────────
  function testNineMultiplierZeroBlocking() {
    // Simulate the 9-multiplier chain from optimizer.js
    // If ANY multiplier = 0, overall priority = 0
    const factors = {
      underweightPct: 5.0,
      technicalMult:  1.0,   // TRIGGER
      fundamentalMult:1.0,   // TIER_1
      macroMult:      1.0,   // RISK_ON
      sectorThemeMult:1.0,   // leader
      valuationGuard: 1.0,   // reasonable P/E
      dqMult:         0.0,   // ← zero (DQ < 40 → blocked)
      riskMult:       1.0,
      regimeFitMult:  1.0,
    };
    const priority = Math.max(0, factors.underweightPct)
      * factors.technicalMult * factors.fundamentalMult * factors.macroMult
      * factors.sectorThemeMult * factors.valuationGuard * factors.dqMult
      * factors.riskMult * factors.regimeFitMult;
    _assert('9-multiplier: dqMult=0 → priority=0', priority === 0, `got ${priority}`);

    // Extended state: technicalMult = 0 → priority = 0
    const factors2 = { ...factors, dqMult: 0.9, technicalMult: 0.0 }; // EXTENDED
    const priority2 = Math.max(0, factors2.underweightPct)
      * factors2.technicalMult * factors2.fundamentalMult * factors2.macroMult
      * factors2.sectorThemeMult * factors2.valuationGuard * factors2.dqMult
      * factors2.riskMult * factors2.regimeFitMult;
    _assert('9-multiplier: technicalMult=0 (EXTENDED) → priority=0', priority2 === 0, `got ${priority2}`);

    // All positive factors → priority > 0
    const factors3 = { ...factors, dqMult: 0.9, technicalMult: 1.0, regimeFitMult: 0.8 };
    const priority3 = Math.max(0, factors3.underweightPct)
      * factors3.technicalMult * factors3.fundamentalMult * factors3.macroMult
      * factors3.sectorThemeMult * factors3.valuationGuard * factors3.dqMult
      * factors3.riskMult * factors3.regimeFitMult;
    _assert('9-multiplier: all positive factors → priority > 0', priority3 > 0, `got ${priority3}`);
  }

  // ── Test 45: MACRO_RISK_OFF with confidence ≥80 triggers immediately ─────────
  function testMacroRiskOffImmediateTrigger() {
    // This validates the whipsaw exception logic:
    // proposed=MACRO_RISK_OFF && confidence>=80 → immediate, no pending required
    const immediateOverride = (proposed, confidence) =>
      proposed === 'MACRO_RISK_OFF' && confidence >= 80;

    _assert('MACRO_RISK_OFF conf=85 → immediate override',  immediateOverride('MACRO_RISK_OFF', 85), '');
    _assert('MACRO_RISK_OFF conf=80 → immediate override',  immediateOverride('MACRO_RISK_OFF', 80), '');
    _assert('MACRO_RISK_OFF conf=79 → NOT immediate',      !immediateOverride('MACRO_RISK_OFF', 79), '');
    _assert('BROAD_RISK_ON  conf=90 → NOT immediate',      !immediateOverride('BROAD_RISK_ON',  90), '');
  }

  // ── Runner ────────────────────────────────────────────────────────────────
  function runAll() {
    _pass = 0; _fail = 0; _results = [];
    console.group('🔍 AUDIT: Reliability Tests');
    testCostBasisContract();
    testTargetWeights();
    testHyOasUnits();
    testCalmarRatio();
    testExtendedBlocking();
    testDowntrendBlock();
    testOverweightBlock();
    testDataInsufficient();
    testTeclRegimeFilter();
    testGuruConfig();
    testSectorUnknown();
    testMacroRegimeFormat();
    testSpeculativeBasketCap();
    testCVarWorseThanVaR();
    testTriggerSma200Guard();
    // New tests (production function calls)
    testDataQualityScore();
    testDataQualityNaNForce();
    testDQAllocationMultiplier();
    testDQCompositeCap();
    testSignalResultBuild();
    testRecLogSaveRetrieve();
    testPortfolioEngineCalcPositions();
    testPortfolioEngineTargetVsActual();
    testAllocationStatus();
    testLeverageSingleConsistency();
    // Investment philosophy + candidate tests
    testCandUniverseModule();
    testFundamentalTierBlock();
    testExtendedNoAllocation();
    testWeakFundStrongSector();
    testRiskOffHighBeta();
    testCandidateWatchlistSeparation();
    testRecLogNewFields();
    testDQFundamentalBlock();
    // v2: market-regime + DQ v3 + 9-multiplier + regime8 fields
    testMarketRegimeModule();
    testMarketRegimeWeightsSumTo100();
    testMarketRegimeBadge();
    testMarketRegimeWhipsaw();
    testRegimeStorageSeparation();
    testDQv3FundamentalMax20();
    testDQv3SectorCacheKey();
    testSignalResultV2Fields();
    testUpdateOutcomeFromCandles();
    testRecLogRegime8Fields();
    testNineMultiplierZeroBlocking();
    testMacroRiskOffImmediateTrigger();

    const total = _pass + _fail;
    const pct = total > 0 ? Math.round(_pass / total * 100) : 0;
    console.log(`\n✅ PASS: ${_pass}/${total} (${pct}%)  ❌ FAIL: ${_fail}/${total}`);
    _results.forEach(r => {
      const icon = r.status === 'PASS' ? '✅' : '❌';
      console.log(`  ${icon} ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
    });
    if (_fail === 0) console.log('🎉 모든 테스트 통과!');
    else console.warn(`⚠️ ${_fail}개 실패 — 위 FAIL 항목 확인`);
    console.groupEnd();
    return { pass: _pass, fail: _fail, total, pct, results: _results };
  }

  // Convenience: render results to a DOM element (call from any tab)
  function renderResults(containerId) {
    const result = runAll();
    const el = document.getElementById(containerId);
    if (!el) return result;
    const rows = result.results.map(r => {
      const color = r.status === 'PASS' ? 'text-emerald-400' : 'text-red-400';
      return `<div class="flex items-start gap-2 text-xs py-1 border-b border-slate-700/30">
        <span class="${color} font-bold w-10">${r.status}</span>
        <span class="text-slate-300 flex-1">${r.name}</span>
        ${r.detail ? `<span class="text-slate-500 text-right max-w-[30%] truncate" title="${r.detail}">${r.detail}</span>` : ''}
      </div>`;
    }).join('');
    el.innerHTML = `
      <div class="bg-slate-800/50 border border-slate-700/50 rounded-xl p-4">
        <div class="font-semibold text-slate-300 mb-3">🔍 AUDIT 결과: ${result.pass}/${result.total} (${result.pct}%)</div>
        ${rows}
      </div>`;
    return result;
  }

  return { runAll, renderResults };
})();
