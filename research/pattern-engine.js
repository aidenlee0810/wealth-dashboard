// research/pattern-engine.js — S/R detection, Fibonacci, candle patterns, setup detection

const PATTERNS = (() => {

  // ── Pivot High/Low ────────────────────────────────────────────────────────

  function detectPivots(candles, n = 5) {
    const highs = [], lows = [];
    for (let i = n; i < candles.length - n; i++) {
      const c = candles[i];
      let ph = true, pl = true;
      for (let j = i - n; j <= i + n; j++) {
        if (j === i) continue;
        if (candles[j].h >= c.h) ph = false;
        if (candles[j].l <= c.l) pl = false;
      }
      if (ph) highs.push({ idx: i, price: c.h, time: c.t });
      if (pl) lows.push({ idx: i, price: c.l, time: c.t });
    }
    return { highs, lows };
  }

  // ── Support/Resistance zones ──────────────────────────────────────────────

  function detectSR(candles, pivots, atrValues) {
    const price  = candles[candles.length - 1].c;
    const atr    = atrValues[atrValues.length - 1] || price * 0.02;
    const zones  = [];
    const cluster = atr * 1.5; // cluster radius

    const merge = (pts, type) => {
      pts.forEach(pt => {
        const existing = zones.find(z => z.type === type && Math.abs(z.price - pt.price) < cluster);
        if (existing) {
          existing.price = (existing.price * existing.count + pt.price) / (existing.count + 1);
          existing.count++;
          existing.lastTime = Math.max(existing.lastTime, pt.time);
          existing.score += 10;
        } else {
          zones.push({
            price: pt.price,
            type,
            count: 1,
            firstTime: pt.time,
            lastTime:  pt.time,
            score:     20,
          });
        }
      });
    };

    merge(pivots.highs, 'resistance');
    merge(pivots.lows,  'support');

    // Score: penalize distant levels, bonus for recent
    const now = candles[candles.length - 1].t;
    zones.forEach(z => {
      // Recency bonus
      const ageDays = (now - z.lastTime) / 86400;
      z.score += Math.max(0, 30 - ageDays * 0.5);
      // Touch count bonus
      z.score += z.count * 10;
      // Distance from current price
      const distPct = Math.abs(z.price - price) / price * 100;
      z.score -= distPct * 2;
      z.distPct = distPct;
    });

    // Sort by proximity
    return zones
      .filter(z => z.distPct < 25 && z.score > 10)
      .sort((a, b) => a.distPct - b.distPct);
  }

  // ── Fibonacci Retracements ────────────────────────────────────────────────

  function detectFib(candles, pivots) {
    if (!pivots.highs.length || !pivots.lows.length) return null;
    // Find most significant recent swing
    const last = candles.length - 1;
    const recentHighs = pivots.highs.filter(h => h.idx > last - 120);
    const recentLows  = pivots.lows.filter(l => l.idx > last - 120);
    if (!recentHighs.length || !recentLows.length) return null;

    const swingHigh = recentHighs.reduce((m, h) => h.price > m.price ? h : m);
    const swingLow  = recentLows.reduce((m, l) => l.price < m.price ? l : m);
    const isUptrend = swingLow.idx < swingHigh.idx;
    const high = swingHigh.price, low = swingLow.price;
    const range = high - low;
    if (range <= 0) return null;

    const fib = (r) => isUptrend ? high - range * r : low + range * r;
    return {
      swingHigh, swingLow, isUptrend, range,
      fib236: fib(0.236), fib382: fib(0.382),
      fib500: fib(0.500), fib618: fib(0.618), fib786: fib(0.786),
      ext1272: isUptrend ? high + range * 0.272 : low - range * 0.272,
      ext1618: isUptrend ? high + range * 0.618 : low - range * 0.618,
    };
  }

  // Nearest fib level to current price
  function nearestFib(fibData, price) {
    if (!fibData) return null;
    const levels = [fibData.fib236, fibData.fib382, fibData.fib500, fibData.fib618, fibData.fib786];
    const names  = ['23.6%', '38.2%', '50%', '61.8%', '78.6%'];
    let best = null, bestDist = Infinity;
    levels.forEach((lv, i) => {
      const d = Math.abs(lv - price) / price;
      if (d < bestDist) { bestDist = d; best = { price: lv, label: names[i], distPct: d * 100 }; }
    });
    return best;
  }

  // ── Candle Pattern Recognition ────────────────────────────────────────────

  function detectCandlePatterns(candles, atrValues) {
    const n = candles.length;
    const results = [];
    const last = n - 1;
    if (last < 2) return results;

    const c0 = candles[last], c1 = candles[last-1], c2 = last >= 2 ? candles[last-2] : null;
    const atr = atrValues[last] || (c0.h - c0.l);
    const body = Math.abs(c0.c - c0.o);
    const range = c0.h - c0.l;
    const clv = range > 0 ? (c0.c - c0.l) / range : 0.5;
    const isGreen = c0.c > c0.o;
    // Compute 20-day average volume directly from candles (not from atrValues which is a price array)
    const vol20Slice = candles.slice(Math.max(0, last - 20), last);
    const vol20 = vol20Slice.length > 0 ? vol20Slice.reduce((s, c) => s + c.v, 0) / vol20Slice.length : c0.v;

    // Long green candle: strong range, big body, high close, above-avg volume
    if (isGreen && range > 1.5 * atr && body > 0.6 * range && clv > 0.75 && c0.v > 1.5 * vol20) {
      results.push({ pattern: 'longGreen', label: '장대양봉', bullish: true, strength: 'strong' });
    }
    // Long red candle: strong range, big body, low close, above-avg volume
    if (!isGreen && range > 1.5 * atr && body > 0.6 * range && clv < 0.25 && c0.v > 1.5 * vol20) {
      results.push({ pattern: 'longRed', label: '장대음봉', bullish: false, strength: 'strong' });
    }
    // Doji
    if (body < 0.1 * range && range > 0.5 * atr) {
      results.push({ pattern: 'doji', label: '도지', bullish: null, strength: 'neutral' });
    }
    // Hammer (long lower wick, small body near top)
    const lowerWick = Math.min(c0.c, c0.o) - c0.l;
    const upperWick = c0.h - Math.max(c0.c, c0.o);
    if (lowerWick > 2 * body && upperWick < 0.5 * body && body > 0) {
      results.push({ pattern: 'hammer', label: '망치형', bullish: true, strength: 'moderate' });
    }
    // Shooting star
    if (upperWick > 2 * body && lowerWick < 0.5 * body && body > 0) {
      results.push({ pattern: 'shootingStar', label: '슈팅스타', bullish: false, strength: 'moderate' });
    }
    // Bullish engulfing
    if (isGreen && c1.c < c1.o && c0.o < c1.c && c0.c > c1.o && body > Math.abs(c1.c - c1.o)) {
      results.push({ pattern: 'bullEngulf', label: '상승 엔걸핑', bullish: true, strength: 'strong' });
    }
    // Bearish engulfing
    if (!isGreen && c1.c > c1.o && c0.o > c1.c && c0.c < c1.o && body > Math.abs(c1.c - c1.o)) {
      results.push({ pattern: 'bearEngulf', label: '하락 엔걸핑', bullish: false, strength: 'strong' });
    }
    // Morning star
    if (c2 && c2.c < c2.o && Math.abs(c1.c - c1.o) < 0.3 * atr && isGreen && c0.c > (c2.o + c2.c) / 2) {
      results.push({ pattern: 'morningStar', label: '모닝스타', bullish: true, strength: 'strong' });
    }
    // Inside bar
    if (c0.h < c1.h && c0.l > c1.l) {
      results.push({ pattern: 'insideBar', label: '인사이드바', bullish: null, strength: 'neutral' });
    }
    return results;
  }

  // ── Volume Signal Analysis ────────────────────────────────────────────────

  function detectVolumeSignals(candles, volData) {
    const n = candles.length;
    const last = n - 1;
    const c = candles[last];
    // Support both old (acc/dist) and new (isAccDay/isDistDay) field names
    const { v20, rvol, isAccDay, isDistDay, distCount25 } = volData;
    const avgVol = v20[last] || 1;
    const signals = [];
    const rv = rvol[last] || 1;

    const isUp = c.c > candles[last-1]?.c;
    const range = c.h - c.l;
    const clv   = range > 0 ? (c.c - c.l) / range : 0.5;

    // Compute recent average range for dry-up detection (not volume-based)
    const avgRange = candles.slice(Math.max(0, last - 20), last)
      .reduce((s, cv) => s + cv.h - cv.l, 0) / Math.min(20, last) || range;
    const rangeContracted = range < avgRange * 0.5;

    // Breakout volume
    const isBreakoutVol = rv >= 1.5 && isUp && clv > 0.6;
    if (isBreakoutVol) {
      signals.push({ type: 'breakoutVol', label: '돌파 거래량', color: 'green', msg: '강한 매수 거래량 — 기관 참여 가능성' });
    }
    // Climax volume
    const isClimaxVol = rv >= 3.0;
    if (isClimaxVol) {
      signals.push({ type: 'climaxVol', label: '클라이맥스 거래량', color: 'yellow', msg: isUp ? '급등 말기 가능성 — 신규매수 주의' : '투매 완료 가능성 — 반등 후 확인 필요' });
    }
    // Volume dry-up: rvol < 0.5 (or volume < 50% of avg) AND price range contracted
    const isDryUp = (rv < 0.5 || c.v < avgVol * 0.5) && rangeContracted;
    if (isDryUp) {
      signals.push({ type: 'dryUp', label: '거래량 소멸', color: 'blue', msg: '변동성 압축 — 방향성 이탈 대기' });
    }
    // Pullback on low volume (healthy)
    const isHealthyPullback = !isUp && rv < 0.8 && clv > 0.4;
    if (isHealthyPullback) {
      signals.push({ type: 'healthyPullback', label: '건강한 눌림목', color: 'green', msg: '낮은 거래량 눌림 — 매수 후보 구간' });
    }
    // Distribution warning
    const dc = distCount25[last] || 0;
    const isDistribution = dc >= 4;
    if (isDistribution) {
      signals.push({ type: 'distribution', label: `매도일 경고 (${dc}일)`, color: 'red', msg: `최근 25일 매도일 ${dc}개 — 매도 압력 증가` });
    }
    // Up move + low volume = weak
    const isWeakRally = isUp && rv < 0.7;
    if (isWeakRally) {
      signals.push({ type: 'weakRally', label: '약한 반등', color: 'yellow', msg: '거래량 없는 상승 — 추격매수 주의' });
    }

    return {
      signals,
      breakoutVol:    isBreakoutVol,
      climaxVol:      isClimaxVol,
      dryUp:          isDryUp,
      healthyPullback:isHealthyPullback,
      distribution:   isDistribution,
      weakRally:      isWeakRally,
      rvol:           rv,
      distCount:      dc,
    };
  }

  // ── Setup Detection ───────────────────────────────────────────────────────

  function detectSetupType(candles, ind, regime) {
    const n = candles.length;
    const last = n - 1;
    const price = candles[last].c;
    const { ma20, ma50, ma200, ema20, atr14, rsi14, bb20, ichiData } = ind;
    if (!ma50 || !ma200) return 'unknown';

    const m200 = ma200[last], m50 = ma50[last], m20 = ma20[last];
    const ema20v = ema20[last];
    const rsi = rsi14[last];
    const atr = atr14[last] || price * 0.02;
    const bbW = bb20?.width?.[last] || 0;

    // Trend Pullback Buy
    if (m200 && price > m200 && m50 > m200 * 0.99 &&
        price < candles.slice(-20).reduce((m,c)=>Math.max(m,c.h),0) * 0.93 &&
        rsi >= 35 && rsi <= 60 && ema20v && price > ema20v * 0.97) {
      return 'trendPullback';
    }
    // Base Breakout — price near multi-week high on low volatility
    if (m50 && price > m50 && bbW < atr * 4) {
      const recent20High = Math.max(...candles.slice(-20).map(c=>c.h));
      if (price > recent20High * 0.98) return 'baseBreakout';
    }
    // Cloud breakout
    if (ichiData) {
      const ichiSig = INDICATORS.ichimokuSignals(candles, ichiData);
      const prev = ichiSig.cloudBottom;
      if (ichiSig.priceAbove && prev && candles[last-3]?.c < prev) return 'cloudBreakout';
    }
    // Moving average reclaim
    if (m50 && price > m50 && candles[last-3]?.c < m50) return 'maReclaim';
    // Downtrend reversal
    if (m200 && price < m200 && m50 && price > m50 &&
        candles.slice(-5).every(c => c.c > c.o)) return 'downtrendReversal';

    return 'none';
  }

  // ── Overextension ─────────────────────────────────────────────────────────

  function detectOverextension(candles, ind) {
    const last = candles.length - 1;
    const price = candles[last].c;
    const { ma50, ma200, ema20, atr14, rsi14, bb20 } = ind;

    const m50  = ma50?.[last],  m200 = ma200?.[last], e20 = ema20?.[last];
    const atr  = atr14?.[last] || price * 0.02;
    const rsi  = rsi14?.[last] || 50;
    const pctB = bb20?.pctB?.[last] || 0.5;

    const ext50  = m50  ? (price / m50  - 1) * 100 : 0;
    const ext200 = m200 ? (price / m200 - 1) * 100 : 0;
    const extEma = e20  ? (price - e20) / atr : 0;

    let score = 0;
    if (rsi > 70) score += 20;
    if (rsi > 80) score += 20;
    if (ext50 > 15) score += 15;
    if (ext50 > 25) score += 15;
    if (ext200 > 30) score += 10;
    if (extEma > 2.5) score += 15;
    if (pctB > 0.95) score += 15;

    return {
      score,
      isExtended: score >= 40,
      isExtremelyExtended: score >= 65,
      rsi, ext50, ext200, extEma, pctB,
      reason: _buildExtReason(rsi, ext50, ext200, extEma),
    };
  }

  function _buildExtReason(rsi, ext50, ext200, extEma) {
    const parts = [];
    if (rsi > 78) parts.push(`RSI ${rsi.toFixed(0)} 과열`);
    if (ext50 > 15) parts.push(`50DMA 대비 +${ext50.toFixed(1)}%`);
    if (ext200 > 30) parts.push(`200DMA 대비 +${ext200.toFixed(1)}%`);
    if (extEma > 2.5) parts.push(`20EMA 위 ${extEma.toFixed(1)} ATR`);
    return parts.join(' · ') || '정상 범위';
  }

  // ── Higher High/Lower Low trend structure ─────────────────────────────────

  function detectTrendStructure(candles, pivots) {
    const highs = pivots.highs.slice(-4);
    const lows  = pivots.lows.slice(-4);
    let hhCount = 0, hlCount = 0, lhCount = 0, llCount = 0;
    for (let i = 1; i < highs.length; i++) {
      if (highs[i].price > highs[i-1].price) hhCount++; else lhCount++;
    }
    for (let i = 1; i < lows.length; i++) {
      if (lows[i].price > lows[i-1].price) hlCount++; else llCount++;
    }
    const isUptrend   = hhCount >= lhCount && hlCount >= llCount;
    const isDowntrend = lhCount >= hhCount && llCount >= hlCount;
    return {
      isUptrend, isDowntrend, isMixed: !isUptrend && !isDowntrend,
      hhCount, hlCount, lhCount, llCount,
      label: isUptrend ? '상승 추세 (HH·HL)' : isDowntrend ? '하락 추세 (LH·LL)' : '횡보/전환 구간',
    };
  }

  // ── Compute all patterns ──────────────────────────────────────────────────

  function computeAll(candles, ind, regime) {
    if (!candles || candles.length < 10 || !ind) return null;
    const pivots   = detectPivots(candles, 5);
    const srLevels = detectSR(candles, pivots, ind.atr14);
    const fibData  = detectFib(candles, pivots);
    const candleP  = detectCandlePatterns(candles, ind.atr14);
    const volSig   = detectVolumeSignals(candles, ind.volData);
    const setupType = detectSetupType(candles, ind, regime);
    const extData  = detectOverextension(candles, ind);
    const trendStr = detectTrendStructure(candles, pivots);

    return { pivots, srLevels, fibData, candlePatterns: candleP, volumeSignals: volSig, setupType, overextension: extData, trendStructure: trendStr };
  }

  return {
    detectPivots, detectSR, detectFib, nearestFib,
    detectCandlePatterns, detectVolumeSignals,
    detectSetupType, detectOverextension, detectTrendStructure,
    computeAll,
  };
})();
