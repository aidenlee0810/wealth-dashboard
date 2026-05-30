// ─────────────────────────────────────────────────────────────────────────────
//  TIMING.js — Tactical Timing Engine
//  9 Stock States · Technical Score 0–100 · Market Regime · Setup Detection
//  Fibonacci · Support/Resistance · Position Sizing · Special Rules
// ─────────────────────────────────────────────────────────────────────────────
const TIMING = (() => {

  // ── Constants ───────────────────────────────────────────────────────────────
  const STATES = {
    AVOID:         { label: 'AVOID',         color: '#ef4444', bg: 'bg-red-900/30',    border: 'border-red-700',    desc: '매수 금지 — 트렌드 붕괴 또는 리스크오프 환경' },
    WATCH:         { label: 'WATCH',         color: '#f97316', bg: 'bg-orange-900/30', border: 'border-orange-700', desc: '관망 — 셋업 형성 중, 아직 매수 타이밍 아님' },
    BASE_BUILDING: { label: 'BASE BUILDING', color: '#eab308', bg: 'bg-yellow-900/30', border: 'border-yellow-700', desc: '베이스 형성 중 — 소량 분할 매수 가능' },
    SETUP:         { label: 'SETUP',         color: '#84cc16', bg: 'bg-lime-900/30',   border: 'border-lime-700',   desc: '셋업 완성 — 트리거 진입 대기' },
    TRIGGER:       { label: 'TRIGGER',       color: '#22c55e', bg: 'bg-green-900/30',  border: 'border-green-700',  desc: '매수 트리거 — 즉시 진입 신호' },
    EXTENDED:      { label: 'EXTENDED 과열',  color: '#06b6d4', bg: 'bg-cyan-900/30',   border: 'border-cyan-700',   desc: '피벗 대비 너무 많이 올라 신규 매수 시 손실 위험 높음 — 눌림목(SMA50) 복귀 대기' },
    ADD:           { label: 'ADD',           color: '#3b82f6', bg: 'bg-blue-900/30',   border: 'border-blue-700',   desc: '추가 매수 — 기존 포지션 증량 적절' },
    REDUCE:        { label: 'REDUCE',        color: '#a855f7', bg: 'bg-purple-900/30', border: 'border-purple-700', desc: '비중 축소 — 일부 익절 또는 트레일링 스탑 적용' },
    EXIT:          { label: 'EXIT',          color: '#ec4899', bg: 'bg-pink-900/30',   border: 'border-pink-700',   desc: '청산 신호 — 포지션 전량 또는 일부 정리' },
  };

  const REGIMES = {
    RISK_ON:  { label: 'RISK ON',  color: '#22c55e', multiplier: 1.0, desc: '전시장 강세 — 풀 포지션 가동' },
    NEUTRAL:  { label: 'NEUTRAL',  color: '#eab308', multiplier: 0.8, desc: '혼조세 — 선별적 매수' },
    CAUTION:  { label: 'CAUTION',  color: '#f97316', multiplier: 0.5, desc: '약세 조짐 — 포지션 축소 권고' },
    RISK_OFF: { label: 'RISK OFF', color: '#ef4444', multiplier: 0.2, desc: '리스크오프 — 현금 비중 확대' },
  };

  // Tickers that need special handling
  const HIGH_BETA = ['CRCL', 'HOOD', 'RKLB'];
  const LEVERAGE_ETF = ['TECL', 'TQQQ', 'SOXL', 'UPRO', 'SPXL', 'METU', 'GGLL', 'AMZZ'];
  // Single-stock 2× leveraged ETF → underlying ticker
  const LEVERAGE_SINGLE = { METU: 'META', GGLL: 'GOOGL', AMZZ: 'AMZN' };

  // ── Helpers (reused from technicals module pattern) ─────────────────────────
  function _sma(arr, n) {
    return arr.map((_, i) => i < n - 1 ? null : arr.slice(i - n + 1, i + 1).reduce((s, v) => s + v, 0) / n);
  }
  function _ema(arr, n) {
    const k = 2 / (n + 1); const out = [];
    arr.forEach((v, i) => out.push(i === 0 ? v : v * k + out[i - 1] * (1 - k)));
    return out;
  }
  function _rsi(closes, n = 14) {
    const out = new Array(closes.length).fill(null);
    for (let i = n; i < closes.length; i++) {
      let gain = 0, loss = 0;
      for (let j = i - n + 1; j <= i; j++) {
        const d = closes[j] - closes[j - 1];
        if (d > 0) gain += d; else loss -= d;
      }
      const rs = gain / (loss || 1e-9);
      out[i] = 100 - 100 / (1 + rs);
    }
    return out;
  }
  function _atr(candles, n = 14) {
    const tr = candles.map((c, i) => {
      if (i === 0) return c.h - c.l;
      const prev = candles[i - 1].c;
      return Math.max(c.h - c.l, Math.abs(c.h - prev), Math.abs(c.l - prev));
    });
    return _sma(tr, n);
  }
  function _obv(candles) {
    let o = 0;
    return candles.map((c, i) => {
      if (i === 0) return 0;
      o += c.v * (c.c > candles[i - 1].c ? 1 : c.c < candles[i - 1].c ? -1 : 0);
      return o;
    });
  }

  // ── Support / Resistance Detection ──────────────────────────────────────────
  function _detectSR(candles, tolerance = 0.015) {
    const pivots = [];
    for (let i = 2; i < candles.length - 2; i++) {
      const isHigh = candles[i].h >= candles[i-1].h && candles[i].h >= candles[i-2].h &&
                     candles[i].h >= candles[i+1].h && candles[i].h >= candles[i+2].h;
      const isLow  = candles[i].l <= candles[i-1].l && candles[i].l <= candles[i-2].l &&
                     candles[i].l <= candles[i+1].l && candles[i].l <= candles[i+2].l;
      if (isHigh) pivots.push({ price: candles[i].h, type: 'R' });
      if (isLow)  pivots.push({ price: candles[i].l, type: 'S' });
    }
    // Cluster pivots within tolerance
    const levels = [];
    pivots.forEach(p => {
      const existing = levels.find(l => Math.abs(l.price - p.price) / p.price < tolerance);
      if (existing) { existing.count++; existing.price = (existing.price + p.price) / 2; }
      else levels.push({ price: p.price, type: p.type, count: 1 });
    });
    return levels.filter(l => l.count >= 2).sort((a, b) => b.count - a.count).slice(0, 8);
  }

  // ── Fibonacci Levels ────────────────────────────────────────────────────────
  function _fibonacci(candles, lookback = 60) {
    const slice = candles.slice(-lookback);
    const high = Math.max(...slice.map(c => c.h));
    const low  = Math.min(...slice.map(c => c.l));
    const range = high - low;
    return [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map(r => ({
      ratio: r,
      price: high - range * r,
      label: `${(r * 100).toFixed(1)}%`,
    }));
  }

  // ── Accumulation / Distribution Day Counter ─────────────────────────────────
  // Counts over last 25 sessions. Acc day = up + above-avg volume. Dist day = down + above-avg vol.
  function _countAccDist(candles, avgVol) {
    const lookback = Math.min(25, candles.length - 1);
    let acc = 0, dist = 0;
    for (let i = candles.length - lookback; i < candles.length; i++) {
      const up  = candles[i].c > candles[i - 1].c;
      const vol = candles[i].v > avgVol * 1.1; // 10% above avg
      if (up && vol) acc++;
      else if (!up && vol) dist++;
    }
    return { acc, dist, lookback,
      signal: dist >= 5 ? 'bearish' : dist >= 3 && acc <= 2 ? 'caution' : acc >= 4 ? 'bullish' : 'neutral' };
  }

  // ── Candle Pattern Recognition ───────────────────────────────────────────────
  function _detectCandlePatterns(candles) {
    const patterns = [];
    const n = candles.length - 1;
    if (n < 2) return patterns;
    const c0 = candles[n], c1 = candles[n - 1], c2 = candles[n - 2];
    const body0 = Math.abs(c0.c - c0.o);
    const range0 = c0.h - c0.l;

    // 장대양봉 — large bullish candle (body > 60% of recent avg range)
    const avgRange = candles.slice(-10).reduce((s, c) => s + c.h - c.l, 0) / 10;
    if (c0.c > c0.o && body0 > avgRange * 0.6) {
      patterns.push({ name: '장대양봉', signal: 'bullish', desc: '강한 상승 캔들 — 매수세 우위' });
    }
    // 장대음봉 — large bearish candle
    if (c0.o > c0.c && body0 > avgRange * 0.6) {
      patterns.push({ name: '장대음봉', signal: 'bearish', desc: '강한 하락 캔들 — 매도세 우위' });
    }
    // 도지 — indecision
    if (body0 < range0 * 0.1 && range0 > avgRange * 0.5) {
      patterns.push({ name: '도지', signal: 'neutral', desc: '매수/매도 균형 — 방향 전환 가능' });
    }
    // 망치형 (Hammer) — bullish reversal at low
    const lowerShadow = Math.min(c0.c, c0.o) - c0.l;
    const upperShadow = c0.h - Math.max(c0.c, c0.o);
    if (lowerShadow > body0 * 2 && upperShadow < body0 * 0.5 && c0.c > c0.o) {
      patterns.push({ name: '망치형', signal: 'bullish', desc: '하락 추세 후 반전 신호' });
    }
    // 역망치형 (Shooting Star) — bearish reversal at high
    if (upperShadow > body0 * 2 && lowerShadow < body0 * 0.5 && c0.o > c0.c) {
      patterns.push({ name: '슈팅스타', signal: 'bearish', desc: '상승 추세 후 반전 경고' });
    }
    // 상승 엔걸핑 (Bullish Engulfing)
    if (c1.o > c1.c && c0.c > c0.o && c0.o <= c1.c && c0.c >= c1.o) {
      patterns.push({ name: '상승 엔걸핑', signal: 'bullish', desc: '전일 하락봉을 완전히 감싼 강한 반전 신호' });
    }
    // 하락 엔걸핑 (Bearish Engulfing)
    if (c1.c > c1.o && c0.o > c0.c && c0.o >= c1.c && c0.c <= c1.o) {
      patterns.push({ name: '하락 엔걸핑', signal: 'bearish', desc: '전일 상승봉을 감싼 하락 반전 신호' });
    }
    return patterns;
  }

  // ── Setup Detection ─────────────────────────────────────────────────────────
  function _detectSetups(candles, sma50, sma200, rsiArr, avgVol) {
    const n = candles.length - 1;
    const price = candles[n].c;
    const s50   = sma50[n];
    const s200  = sma200[n];
    const rsi   = rsiArr[n];
    const vol   = candles[n].v;
    const setups = [];

    if (!s50 || !s200) return setups;

    // 1. Trend Pullback: above 200DMA, pulled back near 50DMA, RSI 40-60
    if (price > s200 && Math.abs(price - s50) / s50 < 0.03 && rsi >= 38 && rsi <= 62) {
      setups.push({ type: 'Trend Pullback', score: 85,
        desc: '200DMA 위 + 50DMA 지지 테스트 + RSI 중립권',
        action: '50DMA 지지 확인 후 진입. 스탑: 50DMA -3%' });
    }
    // 2. Base Breakout: consolidation + volume expansion
    const recentHigh = Math.max(...candles.slice(-20).map(c => c.h));
    const priorHigh  = Math.max(...candles.slice(-60, -20).map(c => c.h));
    if (price >= recentHigh * 0.99 && vol > avgVol * 1.5 && recentHigh > priorHigh * 0.97) {
      setups.push({ type: 'Base Breakout', score: 90,
        desc: '20일 고점 돌파 + 거래량 급증 (평균 150%+)',
        action: '돌파 시 즉시 진입. 스탑: 베이스 저점 -2%' });
    }
    // 3. Downtrend Reversal: was below 200DMA, recently crossed above
    const crossIdx = sma200.slice(-30).findIndex((v, i, a) =>
      v && i > 0 && candles[n - 29 + i].c > v && candles[n - 30 + i].c <= a[i - 1]);
    if (crossIdx >= 0 && price > s200) {
      setups.push({ type: 'Downtrend Reversal', score: 70,
        desc: '200DMA 상향 돌파 (최근 30일)', action: '재테스트 반등 시 진입 (소량 분할매수)' });
    }
    // 4. Earnings Gap: price gapped up significantly
    for (let i = 1; i <= 5 && i < candles.length; i++) {
      const gap = (candles[n - i + 1].o - candles[n - i].c) / candles[n - i].c;
      if (gap > 0.04 && price > candles[n - i].c) {
        setups.push({ type: 'Earnings Gap', score: 75,
          desc: `갭업 +${(gap * 100).toFixed(1)}% 갭 유지 중`,
          action: '갭 하단 리테스트 시 진입 또는 첫 눌림 매수' });
        break;
      }
    }
    // 5. RS Leader: outperforming trend
    const perfLen = Math.min(63, candles.length - 1);
    const ret3M = (price / candles[n - perfLen].c) - 1;
    if (ret3M > 0.15 && price > s200 && price > s50) {
      setups.push({ type: 'RS Leader', score: 80,
        desc: `3개월 수익률 +${(ret3M * 100).toFixed(1)}% — 시장 아웃퍼폼`,
        action: '조정 시 매수. 강도 유지 확인 필수' });
    }

    return setups;
  }

  // ── Market Regime ───────────────────────────────────────────────────────────
  function _computeRegime(spyCandles, qqqCandles, hyOAS, tipsYield) {
    let score = 0; // 0-100

    if (spyCandles?.length > 200) {
      const spy = spyCandles.map(c => c.c);
      const sma200 = _sma(spy, 200);
      const n = spy.length - 1;
      if (sma200[n] && spy[n] > sma200[n]) score += 25;
    }
    if (qqqCandles?.length > 200) {
      const qqq = qqqCandles.map(c => c.c);
      const sma200 = _sma(qqq, 200);
      const n = qqq.length - 1;
      if (sma200[n] && qqq[n] > sma200[n]) score += 25;
    }
    // Credit spreads: HY OAS < 300 = risk-on, > 600 = risk-off
    if (hyOAS != null) {
      if (hyOAS < 300) score += 25;
      else if (hyOAS < 450) score += 15;
      else if (hyOAS < 600) score += 5;
    } else {
      score += 12; // neutral assumption when unavailable
    }
    // TIPS real yield: < 1% = accommodative, > 2.5% = restrictive
    if (tipsYield != null) {
      if (tipsYield < 1.0) score += 25;
      else if (tipsYield < 2.0) score += 15;
      else if (tipsYield < 2.5) score += 8;
    } else {
      score += 12; // neutral assumption
    }

    let regime;
    if (score >= 75) regime = 'RISK_ON';
    else if (score >= 55) regime = 'NEUTRAL';
    else if (score >= 35) regime = 'CAUTION';
    else regime = 'RISK_OFF';

    return { regime, score, hyOAS, tipsYield };
  }

  // ── Technical Score (0–100) ─────────────────────────────────────────────────
  function _computeTechScore(candles, regimeScore, setupCount) {
    const closes = candles.map(c => c.c);
    const n = candles.length - 1;
    const price = closes[n];

    const sma50  = _sma(closes, 50);
    const sma200 = _sma(closes, 200);
    const rsiArr = _rsi(closes);
    const atrArr = _atr(candles);
    const obvArr = _obv(candles);

    let components = {
      trend: 0, momentum: 0, volume: 0, sr: 0, volatility: 0, regime: 0, setup: 0,
    };

    // Trend (25pts)
    const s50  = sma50[n]  || price;
    const s200 = sma200[n] || price;
    if (price > s200) components.trend += 10;
    if (price > s50)  components.trend += 8;
    if (s50 > s200)   components.trend += 7;

    // Momentum / RS (20pts)
    const rsi = rsiArr[n] || 50;
    if (rsi > 50) components.momentum += 8;
    if (rsi > 60) components.momentum += 4;
    if (rsi < 70) components.momentum += 4; // not overbought
    const ret1M = n >= 21 ? (price / closes[n - 21]) - 1 : 0;
    if (ret1M > 0)   components.momentum += 4;

    // Volume (15pts)
    const avgVol20 = candles.slice(-21, -1).reduce((s, c) => s + c.v, 0) / 20;
    const obvTrend = obvArr[n] > (obvArr[n - 20] || 0);
    if (obvTrend) components.volume += 8;
    if (candles[n].v > avgVol20 && closes[n] > closes[n - 1]) components.volume += 7;

    // Support/Resistance proximity (15pts)
    const srLevels = _detectSR(candles);
    const nearSupport = srLevels.some(l =>
      l.type === 'S' && Math.abs(price - l.price) / price < 0.03 && price >= l.price);
    const belowResist = srLevels.some(l =>
      l.type === 'R' && price < l.price && (l.price - price) / price < 0.05);
    if (nearSupport) components.sr += 10;
    if (!belowResist) components.sr += 5;

    // Volatility (10pts): moderate ATR is healthy
    const atr = atrArr[n] || 0;
    const atrPct = (price > 0 && isFinite(atr)) ? atr / price : 0.03;
    if (atrPct < 0.04) components.volatility += 10; // low-moderate vol
    else if (atrPct < 0.07) components.volatility += 6;
    else components.volatility += 2;

    // Market Regime (10pts)
    components.regime = Math.round(regimeScore / 10);

    // Setup Quality (5pts)
    if (setupCount > 0) components.setup = Math.min(5, setupCount * 2 + 1);

    const total = Object.values(components).reduce((s, v) => s + v, 0);
    return {
      total: Math.min(100, total),
      components,
      sma50: s50, sma200: s200,
      rsi, atrPct,
      avgVol20,
    };
  }

  // ── State Classification ────────────────────────────────────────────────────
  function _classifyState(score, techData, regimeKey, hasPosition, ticker) {
    const regime = REGIMES[regimeKey];
    const effectiveScore = score * regime.multiplier;
    const { rsi, sma50, sma200, atrPct } = techData;
    const price = techData.price;

    // Special rules for leveraged ETFs
    if (LEVERAGE_ETF.includes(ticker)) {
      // TECL: only trade when QQQ/XLK above 200DMA (checked via regime + trend component)
      if (regimeKey === 'RISK_OFF' || techData.components.trend < 15) return 'AVOID';
    }

    // High-beta: cap position size when below 200DMA (handled in optimizer)
    // But state still reflects the signal

    if (hasPosition) {
      // Hard exit signals
      if (effectiveScore < 20 || (sma200 && price < sma200 * 0.93)) return 'EXIT';
      // Reduce: structural breakdown
      if (effectiveScore < 32 || (sma200 && price < sma200)) return 'REDUCE';
      // Overbought / overextended — hold, don't chase
      if (rsi > 78 || (score > 80 && atrPct > 0.06)) return 'EXTENDED';
      // Strong signal → add to winner
      if (effectiveScore >= 58 && score >= 65) return 'ADD';
      // Mediocre but OK — hold and watch
      return 'WATCH';
    } else {
      if (regimeKey === 'RISK_OFF' || effectiveScore < 20) return 'AVOID';
      if (effectiveScore < 35) return 'WATCH';
      if (effectiveScore < 50) return 'BASE_BUILDING';
      if (effectiveScore < 65) return 'SETUP';
      if (rsi > 75) return 'EXTENDED';
      return 'TRIGGER';
    }
  }

  // ── Position Sizing ─────────────────────────────────────────────────────────
  function _positionSize(price, atrMultiple, portfolioValue, riskPct = 0.5) {
    const safeAtr = (atrMultiple > 0 && isFinite(atrMultiple)) ? atrMultiple : 0.04;
    const stopDistance = safeAtr * price;
    const riskAmount = portfolioValue * (riskPct / 100);
    const shares = stopDistance > 0 ? Math.floor(riskAmount / stopDistance) : 0;
    const dollars = shares * price;
    const stopPrice = price - stopDistance;
    return { shares, dollars, stopPrice: isFinite(stopPrice) ? stopPrice : price * 0.96, riskAmount, riskPct };
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  async function init(ticker) {
    const panel = document.getElementById('tab-timing');
    if (!panel) return;
    UI.loading('tab-timing', '타이밍 분석 로딩 중...');

    if (!ticker) {
      panel.innerHTML = `<div class="flex flex-col items-center justify-center py-20 text-slate-500">
        <div class="text-5xl mb-4">🎯</div>
        <div class="text-lg font-medium mb-1">종목을 검색하세요</div>
        <div class="text-sm">위 검색창에서 분석할 종목을 입력하세요 (예: AAPL, TECL, GOOGL)</div>
      </div>`; return;
    }

    try {
      const now = Math.floor(Date.now() / 1000);
      const from1Y = now - 365 * 86400;
      // Fetch data in parallel
      const [candlesRaw, spyRaw, qqqRaw, fredData] = await Promise.all([
        API.candles(ticker, from1Y, now, 'D'),
        API.candles('SPY',  from1Y, now, 'D'),
        API.candles('QQQ',  from1Y, now, 'D'),
        RESEARCH_CONFIG.FRED_KEY
          ? API.fredBatch(['BAMLH0A0HYM2', 'DFII10'])
          : Promise.resolve({}),
      ]);

      const _toObj = (raw) => {
        if (!raw?.t?.length) return [];
        return raw.t.map((ts, i) => ({ t: ts, o: raw.o[i], h: raw.h[i], l: raw.l[i], c: raw.c[i], v: raw.v[i] }));
      };
      const candles = _toObj(candlesRaw);
      if (candles.length < 60) {
        UI.error('tab-timing', '캔들 데이터 부족 (60일 이상 필요)'); return;
      }

      const spyCandles = _toObj(spyRaw);
      const qqqCandles = _toObj(qqqRaw);
      // FRED BAMLH0A0HYM2 is in percent (e.g. 3.0 = 300bp). Convert to bp for comparisons and display.
      const hyOASRaw  = fredData?.['BAMLH0A0HYM2']?.slice(-1)[0]?.value ?? null;
      const hyOAS     = hyOASRaw != null ? hyOASRaw * 100 : null; // → basis points
      const tipsYield = fredData?.['DFII10']?.slice(-1)[0]?.value ?? null;

      // Regime (FRED-enhanced when key available)
      const regimeData = _computeRegime(spyCandles, qqqCandles, hyOAS, tipsYield);

      // ── Canonical state via shared SIGNALS engine ──────────────────────────
      // Uses INDICATORS + PATTERNS + SIGNALS so timing tab = technicals tab state
      const closes   = candles.map(c => c.c);
      const sma50arr = _sma(closes, 50);
      const sma200arr = _sma(closes, 200);
      const n = candles.length - 1;

      const price = candles[n].c || 0;  // declare first — used throughout this scope

      const ind      = INDICATORS.computeAll(candles);
      const pats     = ind ? PATTERNS.computeAll(candles, ind, regimeData.regime) : null;
      const techScore = SIGNALS.computeScore(candles, ind, pats, regimeData.regime);

      const _rawPos = STATE.myPosition(ticker);
      const pos = _rawPos ? {
        ..._rawPos,
        unrealizedPct: (_rawPos.costBasis > 0 && price > 0)
          ? ((_rawPos.shares * price - _rawPos.costBasis) / _rawPos.costBasis) * 100
          : (_rawPos.gainPct || 0),
      } : null;
      const stateKey = SIGNALS.classifyState(techScore, candles, ind, pats, !!pos, ticker);
      const state = STATES[stateKey] || STATES.WATCH;
      const regime = REGIMES[regimeData.regime];

      const atrArr = _atr(candles);
      const rsiArr = _rsi(closes);
      const avgVol = candles.slice(-21, -1).reduce((s, c) => s + c.v, 0) / 20;
      const setups  = _detectSetups(candles, sma50arr, sma200arr, rsiArr, avgVol);
      const accDist = _countAccDist(candles, avgVol);
      const candlePatterns = _detectCandlePatterns(candles);

      const techData = _computeTechScore(candles, regimeData.score, setups.length);
      techData.price = price;
      techData.total = techScore;

      const srLevels = _detectSR(candles);
      const fibLevels = _fibonacci(candles);
      const atr = atrArr[n] || 0;
      const atrPct = (price > 0 && isFinite(atr)) ? atr / price : 0.03;

      // Estimate portfolio value from positions
      const myPos = STATE.getMyPositions();
      const portfolioValue = myPos.reduce((s, p) => s + (p.shares * (p.price || price)), 0) || 100000;

      // ── Compute DQ + Composite + Buy Allocation scores ─────────────────────
      const dqResult = (typeof DATA_QUALITY !== 'undefined')
        ? DATA_QUALITY.score(ticker, {
            candles,
            timingResult: { price, techScore, state: stateKey, sma200: techData.sma200 },
            positions: STATE.getMyPositions(),
            targets: CONFIG?.PORTFOLIO_TARGETS,
          })
        : { score: 60, label: 'LOW', color: '#eab308', multiplier: 0.4, missingData: ['DQ module not loaded'], forceInsufficient: false };

      // ── Composite Score — Investment Philosophy Weights ──────────────────────
      // BQ 35% + Valuation 10% + Macro 20% + Sector/Theme 20% + Technical 15%
      // (regime-specific weights from market-regime.js when available)

      // Sub-scores for composite
      const fundTier = (typeof CAND_UNIVERSE !== 'undefined')
        ? CAND_UNIVERSE.getFundamentalTier(ticker) : 'UNKNOWN';
      const businessQualityScore = fundTier === 'TIER_1' ? 85 : fundTier === 'TIER_2' ? 68 :
                                   fundTier === 'TIER_3' ? 45 : 60;

      const macroVal = (() => {
        try {
          const mc = JSON.parse(localStorage.getItem('wr_macro_regime') || 'null');
          return mc?.value || 'NEUTRAL';
        } catch (_) { return 'NEUTRAL'; }
      })();
      const macroSubScore = { RISK_ON: 80, NEUTRAL: 60, CAUTION: 40, RISK_OFF: 20 }[macroVal] ?? 60;

      const sectorSubScore = (() => {
        try {
          const sp = JSON.parse(localStorage.getItem('wr_sector_perf_cache') || 'null');
          if (!sp) return 50;
          const sec = (typeof CAND_UNIVERSE !== 'undefined') ? CAND_UNIVERSE.getSector(ticker) : null;
          if (sec && sp[sec]) {
            const r3m = sp[sec].ret3m ?? 0;
            if (r3m > 10) return 80; if (r3m > 5) return 65; if (r3m < -10) return 20; if (r3m < -5) return 35;
          }
        } catch (_) {}
        return 50;
      })();

      // Get regime-specific weights
      const regimeData8 = (typeof MARKET_REGIME !== 'undefined') ? MARKET_REGIME.get() : null;
      const wts = regimeData8
        ? MARKET_REGIME.getWeights(regimeData8.regime8)
        : { businessQuality: 35, valuation: 10, macro: 20, sectorTheme: 20, technical: 15 };

      const rawComposite = Math.round(
        businessQualityScore * (wts.businessQuality / 100) +
        50 * (wts.valuation / 100) +            // valuation proxy = 50 (neutral if no real data)
        macroSubScore * (wts.macro / 100) +
        sectorSubScore * (wts.sectorTheme / 100) +
        techScore * (wts.technical / 100)
      );
      const compositeScore = (typeof DATA_QUALITY !== 'undefined' && DATA_QUALITY.capCompositeScore)
        ? DATA_QUALITY.capCompositeScore(rawComposite, dqResult.score)
        : rawComposite;

      // Regime fit score
      const regimeFit = (typeof MARKET_REGIME !== 'undefined' && regimeData8)
        ? MARKET_REGIME.regimeFitScore(
            ticker, fundTier,
            (typeof CAND_UNIVERSE !== 'undefined') ? CAND_UNIVERSE.getSector(ticker) : null,
            LEVERAGE_ETF.includes(ticker) || HIGH_BETA.includes(ticker)
          )
        : null;

      // Buy Allocation Score: compositeScore × DQ multiplier, blocked by DQ / EXTENDED / below SMA200
      const sma200 = techData.sma200 || null;
      const priceBelowSma200 = sma200 && price < sma200 * 0.99;
      const buyBlock = (typeof DATA_QUALITY !== 'undefined' && DATA_QUALITY.checkBuyBlock)
        ? DATA_QUALITY.checkBuyBlock(dqResult.score, compositeScore, stateKey, priceBelowSma200)
        : { blocked: false, reason: '' };
      const buyAllocationScore = buyBlock.blocked ? 0 : Math.round(compositeScore * dqResult.multiplier);

      // Save signal to REC_LOG (deduped by day) — includes all v2 fields
      if (typeof REC_LOG !== 'undefined' && price > 0 && stateKey !== 'DATA_INSUFFICIENT') {
        try {
          REC_LOG.save(SIGNAL_RESULT.build({
            ticker, price, state: stateKey,
            compositeScore, dataQualityScore: dqResult.score, buyAllocationScore,
            technicalScore: techScore,
            fundamentalScore: businessQualityScore,
            macroScore: macroSubScore,
            sectorThemeScore: sectorSubScore,
            businessQualityScore,
            valuationScore: 50,  // placeholder until real valuation module
            macroRegime: macroVal,
            marketRegime: regimeData8?.regime8 || '',
            secondaryRegime: regimeData8?.secondaryRegime || '',
            regimeConfidence: regimeData8?.confidence ?? null,
            effectiveWeights: wts,
            regimeFit,
            source: 'timing',
            missingData: dqResult.missingData,
            technicalSnapshot: { sma50: techData.sma50, sma200: techData.sma200, rsi: techData.rsi, ema20: techData.ema20 },
          }));
        } catch (_) {}
      }

      // Render
      panel.innerHTML = `
        <div class="space-y-6">
          ${_renderHero(ticker, price, stateKey, state, regime, techData.total, regimeData, pos)}
          ${_renderRecommendationCard(stateKey, pos, price, compositeScore, dqResult, buyAllocationScore, buyBlock, candles, techData)}
          ${_renderScoreBreakdown(techData)}
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            ${_renderAccDist(accDist, candlePatterns)}
            ${_renderSetups(setups, stateKey)}
          </div>
          ${_renderSpecialRules(ticker, stateKey, techData, regimeData)}
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            ${_renderSRFib(srLevels, fibLevels, price)}
            ${_renderPositionSizer(price, atrPct, portfolioValue, stateKey)}
          </div>
        </div>
      `;

    } catch (e) {
      console.error('TIMING.init error:', e);
      UI.error('tab-timing', '타이밍 분석 실패: ' + e.message);
    }
  }

  // ── Hero ────────────────────────────────────────────────────────────────────
  function _renderHero(ticker, price, stateKey, state, regime, techScore, regimeData, pos) {
    const _fmt2 = v => (v != null && isFinite(v)) ? (+v).toFixed(2) : '—';
    const _fmt1 = v => (v != null && isFinite(v)) ? (+v).toFixed(1) : '—';
    const posHtml = pos ? `
      <div class="bg-blue-900/20 border border-blue-700/50 rounded-xl px-4 py-3 text-sm">
        <div class="text-blue-400 font-semibold mb-1">내 포지션</div>
        <div class="flex gap-6 text-blue-300">
          <span>${_fmt2(pos.shares)}주</span>
          <span>평단 $${_fmt2(pos.avgCost)}</span>
          <span class="${(pos.unrealizedPct || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}">
            ${(pos.unrealizedPct || 0) >= 0 ? '+' : ''}${_fmt1(pos.unrealizedPct)}%</span>
        </div>
      </div>` : '';

    return `
      <div class="${state.bg} border ${state.border} rounded-2xl p-6">
        <div class="flex flex-wrap items-start gap-6">
          <div>
            <div class="text-slate-400 text-sm mb-1">${ticker} 타이밍 상태</div>
            <div class="text-4xl font-black" style="color:${state.color}">${state.label}</div>
            <div class="text-slate-300 text-sm mt-2 max-w-sm">${state.desc}</div>
          </div>
          <div class="flex flex-wrap gap-4 flex-1">
            <div class="bg-slate-800/60 rounded-xl px-4 py-3 text-center min-w-[100px]">
              <div class="text-slate-400 text-xs mb-1">Technical Score</div>
              <div class="text-2xl font-bold ${techScore >= 70 ? 'text-emerald-400' : techScore >= 45 ? 'text-yellow-400' : 'text-red-400'}">${techScore}</div>
              <div class="text-slate-500 text-xs">/ 100</div>
            </div>
            <div class="bg-slate-800/60 rounded-xl px-4 py-3 text-center min-w-[120px]">
              <div class="text-slate-400 text-xs mb-1">시장 국면</div>
              <div class="text-lg font-bold" style="color:${regime.color}">${regime.label}</div>
              <div class="text-slate-500 text-xs">×${regime.multiplier} 배율</div>
            </div>
            <div class="bg-slate-800/60 rounded-xl px-4 py-3 text-center min-w-[100px]">
              <div class="text-slate-400 text-xs mb-1">현재가</div>
              <div class="text-xl font-bold text-white">$${_fmt2(price)}</div>
              <div class="text-slate-500 text-xs">최근 종가</div>
            </div>
            ${regimeData.hyOAS != null ? `
            <div class="bg-slate-800/60 rounded-xl px-4 py-3 text-center min-w-[100px]">
              <div class="text-slate-400 text-xs mb-1">HY OAS</div>
              <div class="text-xl font-bold ${regimeData.hyOAS < 350 ? 'text-emerald-400' : regimeData.hyOAS < 500 ? 'text-yellow-400' : 'text-red-400'}">${regimeData.hyOAS.toFixed(0)}bp</div>
              <div class="text-slate-500 text-xs">하이일드 스프레드</div>
            </div>` : ''}
            ${regimeData.tipsYield != null ? `
            <div class="bg-slate-800/60 rounded-xl px-4 py-3 text-center min-w-[100px]">
              <div class="text-slate-400 text-xs mb-1">TIPS 실질금리</div>
              <div class="text-xl font-bold ${regimeData.tipsYield < 1.5 ? 'text-emerald-400' : regimeData.tipsYield < 2.5 ? 'text-yellow-400' : 'text-red-400'}">${regimeData.tipsYield.toFixed(2)}%</div>
              <div class="text-slate-500 text-xs">10Y 실질수익률</div>
            </div>` : ''}
          </div>
        </div>
        ${pos ? '<div class="mt-4">' + posHtml + '</div>' : ''}
      </div>`;
  }

  // ── Recommendation Card (13-field standard format) ─────────────────────────
  function _renderRecommendationCard(stateKey, pos, price, compositeScore, dqResult, buyAllocationScore, buyBlock, candles, techData) {
    const hasPos = !!pos;

    // Action headline
    const actionMap = {
      TRIGGER:           { action: '매수 트리거 발동', icon: '⚡', newBuyer: '지금 진입 — 분할매수 1차 (목표 비중의 50%). 손절: ATR×2 이하',           existingHolder: '트리거 구간 — 추가매수 가능. 평균단가 낮추기 최적 타이밍' },
      ADD:               { action: '추가 매수 구간',   icon: '➕', newBuyer: '기다리세요 — ADD는 기존 보유자 신호. 신규는 다음 눌림목 대기',              existingHolder: '추가매수 가능 — 눌림목 지지 확인 후 분할 진입' },
      SETUP:             { action: '셋업 완성·대기',   icon: '🎯', newBuyer: '소량 선진입 가능 — 트리거 돌파+거래량 확인 후 본격 진입',                  existingHolder: '셋업 완성 — 트리거 신호 기다리며 홀드' },
      BASE_BUILDING:     { action: '베이스 형성 중',   icon: '🧱', newBuyer: '베이스 형성 중 — 진입 이름. 관찰만 (소량 분할매수 가능)',                 existingHolder: '홀드 — 박스권 이탈(상향) 신호 대기' },
      EXTENDED:          { action: '과열 — 신규 매수 금지', icon: '🔥', newBuyer: '신규 매수 금지. 다음 눌림목까지 대기',                              existingHolder: '과열 구간 — 추가 매수 중단. 기존 포지션 홀드·일부 익절 검토' },
      WATCH:             { action: '관망',             icon: '👁', newBuyer: '아직 매수 신호 없음 — 관망',                                               existingHolder: '추가매수 보류. 지지 여부 확인 중' },
      REDUCE:            { action: '비중 축소',        icon: '📉', newBuyer: '매수 금지 — 하락 압력',                                                    existingHolder: '비중 축소 검토 — 일부 익절 또는 트레일링 스탑' },
      EXIT:              { action: '청산 신호',        icon: '🚨', newBuyer: '매수 절대 금지',                                                            existingHolder: '청산 신호 — 손절·전량 청산 강력 검토' },
      AVOID:             { action: '진입 금지',        icon: '❌', newBuyer: '진입 금지 — 하락 추세 유지 중',                                            existingHolder: '포지션 정리 고려 — 추세 붕괴' },
      DATA_INSUFFICIENT: { action: '데이터 부족',      icon: '⚠️', newBuyer: '데이터 부족 — 분석 보류',                                                 existingHolder: '데이터 부족 — 분석 보류' },
    };
    const g = actionMap[stateKey] || actionMap['WATCH'];
    const isBuy = ['TRIGGER', 'ADD', 'SETUP', 'BASE_BUILDING'].includes(stateKey);
    const isSell = ['REDUCE', 'EXIT', 'AVOID'].includes(stateKey);

    // Reasons & Blockers (derived from techData)
    const reasons = [];
    const blockers = [];
    const warnings = [];
    if (techData?.components) {
      const c = techData.components;
      if (c.trend >= 18)     reasons.push('추세 강세 — 가격 > SMA50/200');
      if (c.momentum >= 15)  reasons.push('모멘텀 강함 — RSI·MACD 상승 중');
      if (c.volume >= 10)    reasons.push('거래량 확인 — 기관 매집 신호');
      if (c.sr >= 10)        reasons.push('지지/저항 지지선 위 — 기술적 안정');
      if (c.trend < 10)      blockers.push('추세 약세 — 가격 SMA 아래');
      if (c.momentum < 8)    blockers.push('모멘텀 부재 — RSI 중립 이하');
      if (c.regime < 5)      warnings.push('시장 국면 약세 — 리스크오프 환경');
    }
    if (dqResult.missingData?.length) {
      dqResult.missingData.slice(0, 3).forEach(m => warnings.push(`데이터 누락: ${m}`));
    }
    if (buyBlock.blocked && buyBlock.reason) blockers.push(buyBlock.reason);

    // Next trigger
    const nextTriggerMap = {
      TRIGGER: '추가 분할매수: ATR×2 이내 눌림목 지지 확인 후',
      ADD: '다음 눌림목: SMA50 리테스트 후 반등 확인',
      SETUP: '돌파 트리거: 20일 고점 + 거래량 평균 150% 초과',
      BASE_BUILDING: '베이스 이탈: 상향 돌파 + 거래량 폭발',
      EXTENDED: '다음 매수 기회: SMA50까지 눌린 후 반등',
      WATCH: '셋업 형성: RSI 45 이상 + SMA50 위 안착',
      REDUCE: '비중 축소 완료 후 재평가',
      EXIT: '포지션 청산 후 재진입 신호 탐색',
      AVOID: '200DMA 회복 + RSI 50 이상 시 재검토',
      DATA_INSUFFICIENT: '데이터 확보 후 재분석',
    };
    const invalidationMap = {
      TRIGGER: 'ATR×2 이하 하락 + 거래량 급증 → 스탑 실행',
      ADD: 'SMA200 붕괴 + RSI 40 이하 → 포지션 재검토',
      SETUP: '베이스 저점 하향 돌파 → 셋업 무효화',
      BASE_BUILDING: '전 저점 하향 + 거래량 급증 → 손절',
      EXTENDED: 'SMA50 붕괴 → 추세 전환 가능성',
      WATCH: '전 저점 하향 돌파 → AVOID로 전환',
      REDUCE: '추가 하락 가속 → EXIT로 격상',
      EXIT: '반등 후 SMA200 회복 시 재평가',
      AVOID: 'SMA200 상향 돌파 + 거래량 증가 시 재검토',
      DATA_INSUFFICIENT: '없음 (데이터 부족)',
    };

    // Confidence
    const confidence = compositeScore >= 75 ? 'HIGH' : compositeScore >= 55 ? 'MEDIUM' : 'LOW';
    const confColor   = confidence === 'HIGH' ? '#22c55e' : confidence === 'MEDIUM' ? '#eab308' : '#ef4444';

    // Score colors
    const _sc = v => v >= 70 ? '#22c55e' : v >= 50 ? '#eab308' : '#ef4444';

    // Scores row
    const scoresHtml = `
      <div class="flex flex-wrap gap-3 mb-4">
        <div class="text-center">
          <div class="text-xs text-slate-500 mb-0.5">종합 점수</div>
          <div class="text-2xl font-black" style="color:${_sc(compositeScore)}">${compositeScore}</div>
          <div class="text-xs text-slate-600">/100</div>
        </div>
        <div class="text-center">
          <div class="text-xs text-slate-500 mb-0.5">매수 배분</div>
          <div class="text-2xl font-black" style="color:${_sc(buyAllocationScore)}">${buyAllocationScore}</div>
          <div class="text-xs text-slate-600">/100</div>
        </div>
        <div class="text-center">
          <div class="text-xs text-slate-500 mb-0.5">데이터 품질</div>
          <div class="text-2xl font-black" style="color:${dqResult.color}">${dqResult.score}</div>
          <div class="text-xs text-slate-600">${dqResult.label}</div>
        </div>
        <div class="text-center">
          <div class="text-xs text-slate-500 mb-0.5">신뢰도</div>
          <div class="text-lg font-bold" style="color:${confColor}">${confidence}</div>
          <div class="text-xs text-slate-600">Confidence</div>
        </div>
      </div>`;

    // Reasons / Blockers / Warnings HTML
    const reasonsHtml = reasons.length
      ? reasons.map(r => `<div class="flex gap-2 text-xs"><span class="text-emerald-400 shrink-0">✓</span><span class="text-slate-300">${r}</span></div>`).join('')
      : '<div class="text-xs text-slate-500">긍정 요인 없음</div>';

    const blockersHtml = blockers.length
      ? blockers.map(b => `<div class="flex gap-2 text-xs"><span class="text-red-400 shrink-0">✗</span><span class="text-slate-300">${b}</span></div>`).join('')
      : '';

    const warningsHtml = warnings.length
      ? warnings.map(w => `<div class="flex gap-2 text-xs"><span class="text-amber-400 shrink-0">⚠</span><span class="text-slate-400">${w}</span></div>`).join('')
      : '';

    // Historical signal stats for this ticker
    let signalStatsHtml = '';
    try {
      if (typeof REC_LOG !== 'undefined') {
        const prior = REC_LOG.forTicker(typeof ticker !== 'undefined' ? ticker : '');
        if (prior.length >= 2) {
          const hits = prior.filter(e => e.outcomes?.[20]?.label === 'HIT').length;
          const resolved = prior.filter(e => e.outcomes?.[20]).length;
          if (resolved >= 1) {
            const hr = (hits / resolved * 100).toFixed(0);
            signalStatsHtml = `<div class="text-xs text-slate-500 mt-1">이 종목 과거 신호 ${resolved}건 중 ${hr}% 적중 (20D)</div>`;
          }
        }
      }
    } catch (_) {}

    const cardBorder = isBuy ? 'border-emerald-800/40' : isSell ? 'border-red-800/40' : 'border-slate-700/50';
    const cardBg     = isBuy ? 'bg-emerald-950/10' : isSell ? 'bg-red-950/10' : 'bg-slate-800/30';

    return `
      <div class="${cardBg} border ${cardBorder} rounded-2xl p-5">
        <div class="flex items-center gap-3 mb-4">
          <span class="text-2xl">${g.icon}</span>
          <div>
            <div class="text-lg font-black text-white">${g.action}</div>
            <div class="text-xs text-slate-500">신호 저장됨 · 💼 내 포트폴리오 탭에서 추적</div>
          </div>
        </div>

        ${scoresHtml}

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <div>
            <div class="text-xs text-slate-400 font-semibold mb-1.5">긍정 요인</div>
            <div class="space-y-1">${reasonsHtml}</div>
            ${signalStatsHtml}
          </div>
          <div>
            ${blockers.length ? `<div class="text-xs text-slate-400 font-semibold mb-1.5">차단 요인</div>
            <div class="space-y-1 mb-2">${blockersHtml}</div>` : ''}
            ${warnings.length ? `<div class="text-xs text-slate-400 font-semibold mb-1.5">경고</div>
            <div class="space-y-1">${warningsHtml}</div>` : ''}
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-xs">
          <div class="bg-slate-800/50 rounded-lg px-3 py-2">
            <div class="text-slate-500 font-semibold mb-1">🎯 다음 매수 트리거</div>
            <div class="text-slate-300">${nextTriggerMap[stateKey] || '—'}</div>
          </div>
          <div class="bg-slate-800/50 rounded-lg px-3 py-2">
            <div class="text-slate-500 font-semibold mb-1">🚫 무효화 조건</div>
            <div class="text-slate-300">${invalidationMap[stateKey] || '—'}</div>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
          <div class="bg-slate-800/50 rounded-lg px-3 py-2">
            <div class="text-slate-500 font-semibold mb-1">👥 기존 보유자</div>
            <div class="text-slate-300">${g.existingHolder}</div>
          </div>
          <div class="bg-slate-800/50 rounded-lg px-3 py-2">
            <div class="text-slate-500 font-semibold mb-1">🆕 신규 매수자</div>
            <div class="text-slate-300">${g.newBuyer}</div>
          </div>
        </div>

        <div class="mt-3 pt-3 border-t border-slate-700/40 text-xs text-slate-600">
          ⚠️ 본 분석은 정보 제공 목적이며 투자 조언이 아닙니다. 모든 매매는 증권사 앱을 통해 직접 실행하세요.
        </div>
      </div>`;
  }

  // ── Legacy alias (unused but kept for compatibility) ────────────────────────
  function _renderActionCard(stateKey, pos, price) {
    return _renderRecommendationCard(stateKey, pos, price, 50, { score:50, label:'LOW', color:'#eab308', multiplier:0.4, missingData:[] }, 20, { blocked:false, reason:'' }, null, {});
  }

  // ── Score Breakdown ─────────────────────────────────────────────────────────
  function _renderScoreBreakdown(techData) {
    const defs = [
      { key: 'trend',     label: '추세',         max: 25 },
      { key: 'momentum',  label: '모멘텀/상대강도', max: 20 },
      { key: 'volume',    label: '거래량',         max: 15 },
      { key: 'sr',        label: '지지/저항',      max: 15 },
      { key: 'volatility',label: '변동성',         max: 10 },
      { key: 'regime',    label: '시장 국면',      max: 10 },
      { key: 'setup',     label: '셋업 품질',      max: 5  },
    ];
    const bars = defs.map(d => {
      const v = techData.components[d.key] || 0;
      const pct = (v / d.max * 100).toFixed(0);
      const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#eab308' : '#ef4444';
      return `
        <div class="flex items-center gap-3">
          <div class="text-slate-400 text-xs w-24 shrink-0">${d.label}</div>
          <div class="flex-1 bg-slate-700/50 rounded-full h-2">
            <div class="h-2 rounded-full transition-all" style="width:${pct}%;background:${color}"></div>
          </div>
          <div class="text-xs font-mono text-slate-300 w-10 text-right">${v}/${d.max}</div>
        </div>`;
    }).join('');

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-4">📊 Technical Score 세부 항목</div>
        <div class="space-y-2.5">${bars}</div>
        <div class="mt-4 text-xs text-slate-500">
          RSI ${techData.rsi?.toFixed(1) ?? '—'} &nbsp;|&nbsp;
          SMA50 $${techData.sma50?.toFixed(2) ?? '—'} &nbsp;|&nbsp;
          SMA200 $${techData.sma200?.toFixed(2) ?? '—'} &nbsp;|&nbsp;
          ATR% ${(techData.atrPct != null && isFinite(techData.atrPct)) ? (techData.atrPct * 100).toFixed(1) : '—'}%
        </div>
        <div class="mt-2 text-xs text-slate-600 italic">
          * 세부 항목 점수는 진단 참고용입니다. 실제 투자 상태(State)는 SIGNALS 엔진의 종합 판단을 따릅니다. 세부 항목 합계와 최종 Tech Score가 다를 수 있습니다.
        </div>
      </div>`;
  }

  // ── Accumulation / Distribution + Candle Patterns ───────────────────────────
  function _renderAccDist(accDist, patterns) {
    const sigColors = { bullish: 'text-emerald-400', bearish: 'text-red-400', caution: 'text-amber-400', neutral: 'text-slate-400' };
    const sigLabels = { bullish: '매집 우위 🟢', bearish: '매도 우위 🔴', caution: '주의 ⚠️', neutral: '중립' };
    const patColors = { bullish: '#10b981', bearish: '#ef4444', neutral: '#94a3b8' };

    const patHtml = patterns.length
      ? patterns.map(p => `
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold" style="color:${patColors[p.signal]}">${p.name}</span>
            <span class="text-slate-400 text-xs">${p.desc}</span>
          </div>`).join('')
      : '<div class="text-slate-500 text-xs">감지된 패턴 없음</div>';

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-3">📅 수급 분석 (최근 ${accDist.lookback}일)</div>
        <div class="flex gap-6 mb-4">
          <div class="text-center">
            <div class="text-2xl font-black text-emerald-400">${accDist.acc}</div>
            <div class="text-xs text-slate-400">매집일</div>
            <div class="text-xs text-slate-500">(상승+거래량↑)</div>
          </div>
          <div class="text-center">
            <div class="text-2xl font-black text-red-400">${accDist.dist}</div>
            <div class="text-xs text-slate-400">매도일</div>
            <div class="text-xs text-slate-500">(하락+거래량↑)</div>
          </div>
          <div class="text-center flex-1">
            <div class="text-sm font-bold ${sigColors[accDist.signal]}">${sigLabels[accDist.signal]}</div>
            <div class="text-xs text-slate-500 mt-1">
              ${accDist.dist >= 5 ? '매도일 5개 이상 — 기관 매도 경고' :
                accDist.acc >= 4 ? '매집일 다수 — 기관 매수 신호' :
                '수급 중립 상태'}
            </div>
          </div>
        </div>
        <div class="border-t border-slate-700/50 pt-3">
          <div class="text-slate-400 text-xs font-medium mb-2">캔들 패턴</div>
          <div class="space-y-1.5">${patHtml}</div>
        </div>
      </div>`;
  }

  // ── Setups ──────────────────────────────────────────────────────────────────
  function _renderSetups(setups, stateKey) {
    // EXTENDED warning banner — shown even when setups exist
    const extWarn = stateKey === 'EXTENDED' ? `
      <div class="bg-amber-950/30 border border-amber-700/50 rounded-xl p-3 mb-3 text-sm text-amber-300">
        🔥 현재 상태 <strong>EXTENDED (과열)</strong> — 셋업이 감지되더라도 신규 매수는 자제하세요. 기존 포지션은 홀드, 다음 눌림목을 기다리세요.
      </div>` : '';

    if (!setups.length) return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-2">🎯 매수 셋업 감지</div>
        ${extWarn}
        <div class="text-slate-500 text-sm">현재 감지된 셋업 없음 — 관망 권고</div>
      </div>`;

    const cards = setups.map(s => `
      <div class="bg-slate-700/40 rounded-xl p-4 border border-slate-600/30">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-emerald-400 font-bold text-sm">${s.type}</span>
          <span class="bg-emerald-900/40 text-emerald-300 text-xs px-2 py-0.5 rounded-full">강도 ${s.score}</span>
        </div>
        <div class="text-slate-300 text-xs mb-1">${s.desc}</div>
        <div class="text-amber-400/80 text-xs">→ ${s.action}</div>
      </div>`).join('');

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-3">🎯 매수 셋업 감지 (${setups.length}개)</div>
        ${extWarn}
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">${cards}</div>
      </div>`;
  }

  // ── Special Rules ───────────────────────────────────────────────────────────
  function _renderSpecialRules(ticker, stateKey, techData, regimeData) {
    const rules = [];

    if (LEVERAGE_ETF.includes(ticker)) {
      const pass = techData.components.trend >= 20;
      rules.push({
        icon: '⚡', label: `${ticker} 3× 레버리지 ETF 규칙`,
        status: pass ? 'pass' : 'fail',
        text: pass
          ? 'QQQ/XLK 200DMA 위 — 레버리지 ETF 매수 조건 충족'
          : '200DMA 아래 또는 약세 트렌드 — 레버리지 ETF 매수 금지 (AVOID 강제)',
      });
      rules.push({
        icon: '📏', label: '레버리지 포지션 사이징',
        status: 'info',
        text: '최대 포트폴리오 대비 20% (목표 비중 상한). 시장 국면 CAUTION 이상 시 50%로 축소.',
      });
    }

    if (HIGH_BETA.includes(ticker)) {
      const aboveSma200 = techData.sma200 && techData.price > techData.sma200;
      rules.push({
        icon: '🔥', label: `${ticker} 고베타 종목 규칙`,
        status: aboveSma200 ? 'pass' : 'warn',
        text: aboveSma200
          ? '200DMA 위 — 정상 포지션 사이즈 적용'
          : '200DMA 아래 — 최대 포지션 사이즈 50% 캡. 손절폭 확대 (ATR ×3 적용)',
      });
    }

    if (!rules.length) return '';

    const items = rules.map(r => {
      const colors = { pass: 'text-emerald-400 bg-emerald-900/20 border-emerald-700/40',
                       fail: 'text-red-400 bg-red-900/20 border-red-700/40',
                       warn: 'text-amber-400 bg-amber-900/20 border-amber-700/40',
                       info: 'text-blue-400 bg-blue-900/20 border-blue-700/40' };
      return `
        <div class="flex gap-3 items-start p-3 rounded-lg border ${colors[r.status]}">
          <span class="text-lg">${r.icon}</span>
          <div>
            <div class="font-semibold text-sm">${r.label}</div>
            <div class="text-xs mt-0.5 opacity-80">${r.text}</div>
          </div>
        </div>`;
    }).join('');

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-3">⚙️ 종목별 특수 규칙</div>
        <div class="space-y-2">${items}</div>
      </div>`;
  }

  // ── S/R + Fibonacci ─────────────────────────────────────────────────────────
  function _renderSRFib(srLevels, fibLevels, price) {
    const srRows = srLevels.slice(0, 6).map(l => {
      const dist = ((l.price - price) / price * 100).toFixed(1);
      const sign = l.price > price ? '+' : '';
      return `
        <tr class="border-t border-slate-700/30">
          <td class="py-1.5 px-2">
            <span class="${l.type === 'S' ? 'text-emerald-400' : 'text-red-400'} font-bold text-xs">${l.type}</span>
          </td>
          <td class="py-1.5 px-2 text-white font-mono text-sm">$${l.price.toFixed(2)}</td>
          <td class="py-1.5 px-2 text-slate-400 text-xs">${sign}${dist}%</td>
          <td class="py-1.5 px-2 text-slate-500 text-xs">터치 ${l.count}회</td>
        </tr>`;
    }).join('');

    const fibRows = fibLevels.map(f => {
      const dist = ((f.price - price) / price * 100).toFixed(1);
      const sign = f.price > price ? '+' : '';
      const near = Math.abs(f.price - price) / price < 0.02;
      return `
        <tr class="border-t border-slate-700/30 ${near ? 'bg-amber-900/10' : ''}">
          <td class="py-1.5 px-2 text-amber-400 text-xs font-mono">${f.label}</td>
          <td class="py-1.5 px-2 text-white font-mono text-sm">$${f.price.toFixed(2)}</td>
          <td class="py-1.5 px-2 text-slate-400 text-xs">${sign}${dist}%</td>
        </tr>`;
    }).join('');

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
        <div class="text-slate-300 font-semibold mb-3">📍 지지/저항 레벨</div>
        <table class="w-full text-sm mb-5"><tbody>${srRows}</tbody></table>
        <div class="text-slate-300 font-semibold mb-3">🌀 피보나치 되돌림</div>
        <table class="w-full text-sm"><tbody>${fibRows}</tbody></table>
      </div>`;
  }

  // ── Position Sizer ──────────────────────────────────────────────────────────
  function _renderPositionSizer(price, atrPct, portfolioValue, stateKey) {
    const riskLevels = [
      { label: '보수적 (0.25%)', riskPct: 0.25 },
      { label: '기본 (0.5%)',    riskPct: 0.5  },
      { label: '공격적 (0.75%)', riskPct: 0.75 },
    ];
    const rows = riskLevels.map(r => {
      const sz = _positionSize(price, atrPct * 2, portfolioValue, r.riskPct);
      return `
        <tr class="border-t border-slate-700/30">
          <td class="py-2 px-2 text-slate-300 text-xs">${r.label}</td>
          <td class="py-2 px-2 text-white font-mono">${sz.shares}주</td>
          <td class="py-2 px-2 text-white font-mono">$${sz.dollars.toLocaleString()}</td>
          <td class="py-2 px-2 text-red-400 font-mono text-xs">$${sz.stopPrice.toFixed(2)}</td>
        </tr>`;
    }).join('');

    const actionable = ['TRIGGER', 'SETUP', 'ADD', 'BASE_BUILDING'].includes(stateKey);
    const dimClass = actionable ? '' : 'opacity-40';

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5 ${dimClass}">
        <div class="text-slate-300 font-semibold mb-1">💼 포지션 사이징 (리스크 기반)</div>
        <div class="text-slate-500 text-xs mb-3">
          스탑 = ATR×2 (${(atrPct > 0 && isFinite(atrPct)) ? (atrPct * 2 * 100).toFixed(1) : '~4.0'}%) &nbsp;|&nbsp; 포트폴리오 $${portfolioValue.toLocaleString()}
          ${!actionable ? ' &nbsp;|&nbsp; <span class="text-red-400">현재 상태에서 매수 권장 안 함</span>' : ''}
        </div>
        <table class="w-full text-sm">
          <thead><tr class="text-slate-500 text-xs">
            <th class="pb-1 px-2 text-left">리스크</th>
            <th class="pb-1 px-2 text-left">주수</th>
            <th class="pb-1 px-2 text-left">투자금</th>
            <th class="pb-1 px-2 text-left">스탑가</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }


  // ── Public API ──────────────────────────────────────────────────────────────
  // Delegates to SIGNALS.getStateForTicker so timing tab, technicals tab, and
  // optimizer all share the same canonical state engine.
  async function getStateForTicker(ticker) {
    return SIGNALS.getStateForTicker(ticker);
  }

  return { init, getStateForTicker, STATES, REGIMES, HIGH_BETA, LEVERAGE_ETF, LEVERAGE_SINGLE };
})();
