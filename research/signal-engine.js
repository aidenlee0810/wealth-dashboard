// research/signal-engine.js — Technical state machine, scoring, explanations, buy plan
// States: AVOID · WATCH · BASE_BUILDING · SETUP · TRIGGER · ADD · EXTENDED · REDUCE · EXIT · DATA_INSUFFICIENT

const SIGNALS = (() => {

  const STATES = ['AVOID','WATCH','BASE_BUILDING','SETUP','TRIGGER','ADD','EXTENDED','REDUCE','EXIT','DATA_INSUFFICIENT'];

  const STATE_META = {
    AVOID:             { label:'❌ 피함',       color:'text-slate-500',  bg:'bg-slate-800/60',  action:'신규매수 금지' },
    WATCH:             { label:'👁 관찰',       color:'text-slate-300',  bg:'bg-slate-700/60',  action:'신규매수 보류' },
    BASE_BUILDING:     { label:'🧱 베이스',     color:'text-blue-400',   bg:'bg-blue-900/30',   action:'트리거 대기' },
    SETUP:             { label:'🎯 셋업',       color:'text-indigo-400', bg:'bg-indigo-900/30', action:'분할매수 준비' },
    TRIGGER:           { label:'⚡ 트리거',     color:'text-emerald-400',bg:'bg-emerald-900/30',action:'매수 진입 신호' },
    ADD:               { label:'➕ 추가',       color:'text-teal-400',   bg:'bg-teal-900/30',   action:'추가매수 가능' },
    EXTENDED:          { label:'🔥 과열',       color:'text-amber-400',  bg:'bg-amber-900/30',  action:'신규매수 금지' },
    REDUCE:            { label:'📉 축소',       color:'text-orange-400', bg:'bg-orange-900/30', action:'일부 익절 검토' },
    EXIT:              { label:'🚨 청산',       color:'text-red-400',    bg:'bg-red-900/30',    action:'손절·청산 신호' },
    DATA_INSUFFICIENT: { label:'⚠️ 데이터 부족', color:'text-slate-400',  bg:'bg-slate-800/60',  action:'분석 불가 — 데이터 수집 중' },
  };

  // ── Technical Score (0–100) ───────────────────────────────────────────────

  function computeScore(candles, ind, patterns, regime) {
    if (!candles || !ind || candles.length < 52) return 50;
    const n = candles.length, last = n - 1;
    const price = candles[last].c;

    // ── 1. Trend (25pts) ──
    let trend = 0;
    const { ma50, ma200, ema20, ma20 } = ind;
    const m50  = ma50?.[last]  || 0;
    const m200 = ma200?.[last] || 0;
    const e20  = ema20?.[last] || 0;
    if (m200 && price > m200)              trend += 10;
    if (m50  && price > m50)               trend +=  8;
    if (e20  && price > e20)               trend +=  4;
    if (m50  && m50  > m200)               trend +=  3;
    // Golden / Dead cross bonus (within last 30 bars = fresh signal)
    const cross = _detectMACross(ma50, ma200, last, 30);
    if (cross.goldCross)  trend = Math.min(25, trend + 5);  // fresh golden cross = strong bull
    if (cross.deadCross)  trend = Math.max(0,  trend - 5);  // fresh dead cross   = strong bear
    // MA slope (+5 cap — prevents a slight upward tick from inflating trend to max)
    const ma50prev = ma50?.[last - 10] || 0;
    if (m50 > ma50prev)                    trend = Math.min(25, trend + 5);

    // ── 2. Momentum/RS (20pts) ──
    let momentum = 0;
    const rsi = ind.rsi14?.[last] || 50;
    const { K: sK } = ind.stochShort || {}, { K: mK } = ind.stochMid || {}, { K: lK } = ind.stochLong || {};
    if (rsi > 50 && rsi < 70) momentum += 8;
    if (rsi > 40 && rsi < 80) momentum += 4;
    if (sK?.[last] > 50) momentum += 2;
    if (mK?.[last] > 50) momentum += 4;
    if (lK?.[last] > 50) momentum += 6; // long stoch most important

    // ── 3. Volume (15pts) ──
    let volume = 0;
    const { rvol, isAccDay, distCount25 } = ind.volData || {};
    const rv = rvol?.[last] || 1;
    const dc = distCount25?.[last] || 0;
    if (rv > 1.3 && isAccDay?.[last]) volume += 10;
    else if (isAccDay?.[last])        volume +=  6;
    else if (rv < 0.7 && candles[last].c > candles[last-1].c) volume += 4; // dry-up pullback good
    if (dc < 2) volume += 5; else if (dc < 4) volume += 2;

    // ── 4. S/R Confluence (15pts) ──
    let srScore = 0;
    const srLevels  = patterns?.srLevels || [];
    const fibData   = patterns?.fibData;
    const nearSupport = srLevels.find(z => z.type === 'support' && z.distPct < 5);
    if (nearSupport) srScore += Math.min(15, 5 + nearSupport.score * 0.1);
    if (fibData) {
      const nfib = PATTERNS.nearestFib(fibData, price);
      if (nfib && nfib.distPct < 2) srScore = Math.min(15, srScore + 5);
    }
    // Ichimoku support
    if (ind.ichiData) {
      const iSig = INDICATORS.ichimokuSignals(candles, ind.ichiData);
      if (iSig.priceAbove) srScore = Math.min(15, srScore + 4);
    }

    // ── 5. Volatility/Extension (10pts, higher = penalize) ──
    let volExt = 10;
    const ext = patterns?.overextension;
    if (ext) {
      if (ext.isExtremelyExtended) volExt = 0;
      else if (ext.isExtended)     volExt = 3;
      else if (ext.score < 20)     volExt = 10;
      else volExt = 7;
    }
    // BB contraction = bonus
    const bbW = ind.bb20?.width?.[last] || 0;
    const bbWprev = ind.bb20?.width?.[last - 20] || bbW;
    if (bbW < bbWprev * 0.7) volExt = Math.min(10, volExt + 2); // contraction

    // ── 6. Market Regime (10pts) ──
    let regimeScore = 5; // neutral default
    if (regime === 'RISK_ON')  regimeScore = 10;
    if (regime === 'CAUTION')  regimeScore = 3;
    if (regime === 'RISK_OFF') regimeScore = 0;

    // ── 7. Setup Quality (5pts) ──
    let setupScore = 0;
    const st = patterns?.setupType;
    if (st === 'trendPullback')    setupScore = 5;
    else if (st === 'baseBreakout')setupScore = 5;
    else if (st === 'cloudBreakout')setupScore = 4;
    else if (st === 'maReclaim')   setupScore = 3;
    const candleP = patterns?.candlePatterns || [];
    if (candleP.some(p => p.bullish && p.strength === 'strong')) setupScore = Math.min(5, setupScore + 2);

    // Each component already scaled to its max weight; sum = 0–100
    const raw = Math.round(trend + momentum + volume + srScore + volExt + regimeScore + setupScore);
    return Math.max(0, Math.min(100, raw));
  }

  // ── State Classification ──────────────────────────────────────────────────

  function classifyState(score, candles, ind, patterns, hasPosition, ticker) {
    const last = candles.length - 1;
    const price = candles[last].c;
    const m200 = ind.ma200?.[last], m50 = ind.ma50?.[last];
    const rsi  = ind.rsi14?.[last] || 50;
    const ext  = patterns?.overextension;
    const ts   = patterns?.trendStructure;
    const dc   = ind.volData?.distCount25?.[last] || 0;

    // Hard AVOID: broken 200DMA + lower lows
    if (m200 && price < m200 * 0.93 && ts?.isDowntrend) return 'AVOID';
    // EXTENDED: overheated — RSI > 75 OR price > SMA50 × 1.20 OR overextension flag
    const isOverextended = ext?.isExtended || rsi > 75 || (m50 && price > m50 * 1.20);
    if (isOverextended && score >= 60) return 'EXTENDED';
    // EXIT: major support breakdown
    if (hasPosition && m200 && price < m200 * 0.97 && ts?.isDowntrend) return 'EXIT';
    if (hasPosition && m50 && price < m50 * 0.93 && dc >= 4) return 'EXIT';
    // REDUCE
    if (hasPosition && (ext?.isExtended || dc >= 4 || (m50 && price < m50 * 0.97))) return 'REDUCE';
    // TRIGGER: fresh buy signal — no new buys when price is below 200DMA
    if (!hasPosition && score >= 72 && patterns?.setupType !== 'none') {
      if (m200 && price < m200) return 'WATCH'; // 200DMA guard: no new entries below 200DMA
      return 'TRIGGER';
    }
    // ADD: existing position healthy pullback — block if deeply below SMA50 (>5% under)
    if (hasPosition && score >= 60 && !isOverextended) {
      if (m50 && price < m50 * 0.95) return 'WATCH'; // SMA50 guard: don't add into a deep dip
      return 'ADD';
    }
    // SETUP: good candidate, waiting for trigger
    if (score >= 55 && m50 && price > m50 * 0.97) return 'SETUP';
    // BASE_BUILDING
    if (score >= 40 && m200 && price > m200 && ts?.isMixed) return 'BASE_BUILDING';
    // WATCH: chart weak but not in downtrend
    if (score >= 30) return 'WATCH';
    // AVOID
    return 'AVOID';
  }

  // ── Trade Setup Computation ───────────────────────────────────────────────

  function computeTradeSetup(state, candles, ind, patterns) {
    const last = candles.length - 1;
    const price = candles[last].c;
    const atr   = ind.atr14?.[last] || price * 0.02;
    const m50   = ind.ma50?.[last];
    const m200  = ind.ma200?.[last];
    const ema20v = ind.ema20?.[last];

    // Buy zone: nearest support or MA
    const nearSupport = patterns?.srLevels?.find(z => z.type === 'support' && z.distPct < 8);
    const buyLow  = nearSupport?.price || (ema20v ? Math.min(ema20v, m50 || ema20v) : price * 0.97);
    const buyHigh = buyLow * 1.03;
    // Trigger: breakout of recent swing high
    const recentHigh = Math.max(...candles.slice(-10).map(c => c.h));
    const trigger = recentHigh * 1.001;
    // Stop: below swing low or 2 ATR
    const swingLow = patterns?.fibData?.swingLow?.price || (price - 2.5 * atr);
    const stop = Math.min(swingLow * 0.995, price - 2.5 * atr);
    // Targets
    const risk = price - stop;
    const target1 = price + risk * 1.5;
    const target2 = price + risk * 2.5;
    const rr = risk > 0 ? (target1 - price) / risk : null;

    // Position size suggestion (% of intended allocation)
    const sizePct = { TRIGGER: 50, ADD: 25, SETUP: 0, BASE_BUILDING: 0, EXTENDED: 0, REDUCE: -25, EXIT: -50 }[state] ?? 0;

    return { buyLow, buyHigh, trigger, stop, invalidation: stop, target1, target2, rr, sizePct, atr };
  }

  // ── Korean Explanation Generator ──────────────────────────────────────────

  function generateExplanation(state, score, candles, ind, patterns, regime) {
    const last  = candles.length - 1;
    const price = candles[last].c;
    const m200  = ind.ma200?.[last];
    const m50   = ind.ma50?.[last];
    const rsi   = ind.rsi14?.[last] || 50;
    const ext   = patterns?.overextension;
    const ts    = patterns?.trendStructure;
    const setup = patterns?.setupType;
    const sr    = patterns?.srLevels || [];
    const canP  = patterns?.candlePatterns || [];
    const volSig = patterns?.volumeSignals;

    const parts = [];

    // Golden / Dead cross — prepend prominent signal if recent (within 30 bars)
    const crossSig = _detectMACross(ind.ma50, ind.ma200, last, 30);
    if (crossSig.goldCross) {
      const ago = crossSig.goldCrossAgo;
      parts.push(`🟡 골든크로스 발생${ago === 0 ? ' (당일)' : ` (${ago}일 전)`} — MA50이 MA200을 상향 돌파. 중장기 추세 전환 신호.`);
    } else if (crossSig.deadCross) {
      const ago = crossSig.deadCrossAgo;
      parts.push(`⚫ 데드크로스 발생${ago === 0 ? ' (당일)' : ` (${ago}일 전)`} — MA50이 MA200을 하향 돌파. 중장기 약세 신호.`);
    }

    switch (state) {
      case 'TRIGGER': {
        if (setup === 'trendPullback')  parts.push('20EMA/50DMA에서 눌림목 후 반등 확인.');
        if (setup === 'baseBreakout')   parts.push('베이스 상단 저항을 거래량과 함께 돌파.');
        if (setup === 'cloudBreakout')  parts.push('구름대(Ichimoku) 상향 돌파 — 추세 전환 신호.');
        if (setup === 'maReclaim')      parts.push('50DMA 회복 + 거래량 증가 확인.');
        const bull = canP.find(p => p.bullish && p.strength === 'strong');
        if (bull) parts.push(`${bull.label} 출현.`);
        parts.push(`기술점수 ${score}점. 분할매수 진입 가능.`);
        break;
      }
      case 'ADD': {
        parts.push('보유 중인 종목이 건강한 눌림목을 형성하고 있습니다.');
        if (m50 && price > m50) parts.push('50DMA 위 지지 유지.');
        parts.push('추세가 유지되는 동안 평단가 개선(불타기) 가능합니다.');
        break;
      }
      case 'SETUP': {
        const s = sr.find(z => z.type === 'support');
        if (s) parts.push(`지지선 $${s.price.toFixed(2)} 근처에 위치.`);
        if (m50 && Math.abs(price / m50 - 1) < 0.04) parts.push('50DMA에 접근 중 — 지지 확인 대기.');
        parts.push('아직 명확한 트리거(거래량 증가+전고점 돌파)가 없습니다. 대기하세요.');
        break;
      }
      case 'BASE_BUILDING': {
        parts.push('매도세가 줄어들고 가격이 박스권을 형성 중입니다.');
        parts.push('구조적으로 개선되고 있으나, 트리거 신호가 나타날 때까지 신규매수는 보류하세요.');
        break;
      }
      case 'EXTENDED': {
        parts.push(`과열 지표: ${ext?.reason || 'RSI 과열, 이격 과다'}.`);
        parts.push('신규매수를 중단하고 다음 눌림목을 기다리세요. 기존 포지션은 부분 익절을 검토하세요.');
        break;
      }
      case 'WATCH': {
        if (m200 && price < m200) parts.push(`가격이 200DMA($${m200.toFixed(2)}) 아래에 있습니다.`);
        else if (ts?.isDowntrend) parts.push('하락 추세 구조(LH·LL)가 유지 중입니다.');
        parts.push('펀더멘털이 좋더라도 차트 신호가 없으면 신규매수를 보류하세요. 무지성 물타기는 금지입니다.');
        break;
      }
      case 'REDUCE': {
        const dc = ind.volData?.distCount25?.[last] || 0;
        if (dc >= 4) parts.push(`최근 25일 매도일 ${dc}개 — 기관 매도 압력.`);
        if (ext?.isExtended) parts.push('이격 과다 — 목표 비중 초과 시 일부 익절 고려.');
        parts.push('포지션 일부를 줄이고, 50DMA 아래 종가 마감 시 추가 축소를 검토하세요.');
        break;
      }
      case 'EXIT': {
        if (m200 && price < m200) parts.push(`200DMA($${m200.toFixed(2)}) 이탈 후 회복 실패.`);
        if (ts?.isDowntrend) parts.push('하락 추세 구조 확정 (LH·LL).');
        parts.push('핵심 지지선 붕괴 또는 thesis 훼손 신호. 손절·청산을 강하게 고려하세요.');
        break;
      }
      default: // AVOID
        parts.push('하락 추세가 지속되거나 펀더멘털이 훼손된 구간입니다.');
        parts.push('신규매수를 금지하고 관심 목록에서만 모니터링하세요.');
    }

    // Regime context
    if (regime === 'RISK_OFF') parts.push('⚠️ 시장 리스크오프 환경 — 모든 고위험 포지션 사이즈 축소 권장.');
    else if (regime === 'RISK_ON') parts.push('✅ 리스크온 환경 — 매수 신호 신뢰도 상승.');

    return parts.join(' ');
  }

  // ── Monthly Buy Plan ──────────────────────────────────────────────────────

  const TIMING_MULT = {
    TRIGGER: 1.0, ADD: 0.80, SETUP: 0.50, BASE_BUILDING: 0.25,
    EXTENDED: 0.0, WATCH: 0.10, AVOID: 0, REDUCE: 0, EXIT: 0, DATA_INSUFFICIENT: 0,
  };

  function generateBuyPlan(portfolio, targets, states, accounts, totalBudget = 2000) {
    // portfolio: [{ticker, currentValue, totalValue}]
    // targets:   {ticker: targetPct}
    // states:    {ticker: {state, score}}
    // accounts:  {ticker: accountId}

    const plan = [];
    let totalWeight = 0;

    portfolio.forEach(pos => {
      const ticker = pos.ticker;
      const target = (targets[ticker] || 0) / 100;
      const current = pos.totalValue > 0 ? pos.currentValue / pos.totalValue : 0;
      const underweight = Math.max(0, target - current);
      const stateInfo = states[ticker] || {};
      const state = stateInfo.state || 'WATCH';
      const score = stateInfo.score || 50;
      const mult  = TIMING_MULT[state] ?? 0;

      const priority = underweight * mult * (score / 100);
      totalWeight += priority;

      plan.push({
        ticker,
        account: accounts[ticker] || 'taxable',
        state, score, mult,
        targetPct: (target * 100).toFixed(0) + '%',
        currentPct: (current * 100).toFixed(1) + '%',
        underweightPct: (underweight * 100).toFixed(1) + '%',
        priority,
        rawAmount: 0, // filled below
        reason: _buyReason(state, underweight),
      });
    });

    // Distribute budget proportionally by priority
    const eligible = plan.filter(p => p.priority > 0);
    eligible.forEach(p => {
      p.rawAmount = totalWeight > 0 ? Math.round(totalBudget * p.priority / totalWeight / 10) * 10 : 0;
    });

    // Round and normalize
    let allocated = eligible.reduce((s, p) => s + p.rawAmount, 0);
    const diff = totalBudget - allocated;
    if (diff !== 0 && eligible.length > 0) {
      eligible.sort((a, b) => b.priority - a.priority)[0].rawAmount += diff;
    }

    plan.filter(p => p.priority === 0).forEach(p => p.rawAmount = 0);
    plan.sort((a, b) => b.priority - a.priority);
    return { plan, totalBudget, allocated: plan.reduce((s,p)=>s+p.rawAmount,0) };
  }

  // Detect golden cross / dead cross within last `lookback` bars
  // Returns { goldCross, deadCross, goldCrossAgo, deadCrossAgo }
  function _detectMACross(ma50, ma200, last, lookback = 30) {
    if (!ma50 || !ma200 || last < 1) return { goldCross: false, deadCross: false };
    let goldCross = false, deadCross = false, goldCrossAgo = null, deadCrossAgo = null;
    const start = Math.max(1, last - lookback);
    for (let i = start; i <= last; i++) {
      const cur50 = ma50[i], cur200 = ma200[i];
      const prv50 = ma50[i-1], prv200 = ma200[i-1];
      if (cur50 == null || cur200 == null || prv50 == null || prv200 == null) continue;
      // Golden: MA50 crossed above MA200
      if (prv50 <= prv200 && cur50 > cur200) {
        goldCross = true;
        goldCrossAgo = last - i;
      }
      // Dead: MA50 crossed below MA200
      if (prv50 >= prv200 && cur50 < cur200) {
        deadCross = true;
        deadCrossAgo = last - i;
      }
    }
    // Only report the most recent cross type
    if (goldCross && deadCross) {
      if (goldCrossAgo <= deadCrossAgo) deadCross = false;
      else goldCross = false;
    }
    return { goldCross, deadCross, goldCrossAgo: goldCrossAgo ?? 0, deadCrossAgo: deadCrossAgo ?? 0 };
  }

  function _buyReason(state, underweight) {
    if (state === 'AVOID' || state === 'EXIT')   return '신규매수 금지';
    if (state === 'REDUCE')                       return '축소 검토';
    if (state === 'EXTENDED')                     return '과열 — 이번 달 건너뜀';
    if (state === 'TRIGGER' && underweight > 0)   return '매수 트리거 + 비중 미달';
    if (state === 'ADD')                          return '추가 가능 눌림목';
    if (state === 'SETUP')                        return '셋업 구간 — 소량 준비';
    if (state === 'WATCH')                        return '관찰 중 — 최소 배분';
    if (state === 'BASE_BUILDING')                return '베이스 형성 — 절반 배분';
    return '정기 배분';
  }

  // ── Canonical getStateForTicker (used by optimizer + timing tab) ─────────────
  // Fetches candles, runs INDICATORS + PATTERNS + SIGNALS pipeline.
  // Returns { state, techScore, regime, price, sma50, sma200 }

  async function getStateForTicker(ticker) {
    try {
      const now  = Math.floor(Date.now() / 1000);
      const from = now - 365 * 86400;
      const [candlesRaw, spyRaw, qqqRaw] = await Promise.all([
        API.candles(ticker, from, now, 'D'),
        API.candles('SPY',  from, now, 'D'),
        API.candles('QQQ',  from, now, 'D'),
      ]);
      const toObj = raw => raw?.t?.length
        ? raw.t.map((ts, i) => ({ t: ts, o: raw.o[i], h: raw.h[i], l: raw.l[i], c: raw.c[i], v: raw.v[i] }))
        : [];

      const candles = toObj(candlesRaw);
      if (candles.length < 20) return { state: 'DATA_INSUFFICIENT', techScore: 0, regime: 'NEUTRAL', price: 0 };

      // Regime from SPY/QQQ (simple: above 200DMA = risk-on)
      const _regime = (() => {
        const spyC = toObj(spyRaw), qqqC = toObj(qqqRaw);
        let s = 0;
        const smaLast = (arr, n) => { const cl = arr.map(c => c.c); const sl = cl.length; if (sl < n) return null; return cl.slice(sl - n).reduce((a, b) => a + b, 0) / n; };
        if (spyC.length >= 200 && spyC[spyC.length-1].c > (smaLast(spyC, 200) || 0)) s += 40;
        if (qqqC.length >= 200 && qqqC[qqqC.length-1].c > (smaLast(qqqC, 200) || 0)) s += 40;
        s += 20; // neutral default for credit/TIPS
        return s >= 75 ? 'RISK_ON' : s >= 55 ? 'NEUTRAL' : s >= 35 ? 'CAUTION' : 'RISK_OFF';
      })();

      // Cache technical regime separately (SPY/QQQ fallback, NOT FRED-based)
      // macro.js owns wr_macro_regime; signal-engine owns wr_signal_regime
      try { localStorage.setItem('wr_signal_regime', JSON.stringify({ value: _regime, ts: Date.now() })); } catch(_) {}

      const ind      = INDICATORS.computeAll(candles);
      const patterns = ind ? PATTERNS.computeAll(candles, ind, _regime) : null;
      const score    = computeScore(candles, ind, patterns, _regime);
      const hasPos   = (() => { try { const raw = JSON.parse(localStorage.getItem('wd_holdings_cache') || 'null'); const h = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.holdings) ? raw.holdings : []); return h.some(x => (x.ticker||'').toUpperCase() === ticker.toUpperCase()); } catch(_) { return false; } })();
      const state    = classifyState(score, candles, ind, patterns, hasPos, ticker);
      const n = candles.length - 1;
      return {
        state, techScore: score, regime: _regime,
        price:  candles[n].c,
        sma50:  ind?.ma50?.[n]  || null,
        sma200: ind?.ma200?.[n] || null,
        candlesLen: candles.length,
      };
    } catch(e) {
      console.warn('SIGNALS.getStateForTicker error:', ticker, e.message);
      return { state: 'WATCH', techScore: 50, regime: 'NEUTRAL', price: 0 };
    }
  }

  return {
    STATES, STATE_META,
    computeScore, classifyState,
    computeTradeSetup, generateExplanation,
    generateBuyPlan,
    getStateForTicker,
  };
})();
