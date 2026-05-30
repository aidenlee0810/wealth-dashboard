// research/indicators.js — Pure technical indicator calculations
// All functions: (candles[]|closes[], params) → result[]
// candle format: { t (unix sec), o, h, l, c, v }

const INDICATORS = (() => {
  const _c = candles => candles.map(x => x.c);

  // ── Smoothing helper ──────────────────────────────────────────────────────
  function _slidingSMA(arr, period) {
    const out = new Array(arr.length).fill(null);
    for (let i = period - 1; i < arr.length; i++) {
      let s = 0, n = 0;
      for (let j = i; j >= 0 && n < period; j--) {
        if (arr[j] == null) break;
        s += arr[j]; n++;
      }
      if (n === period) out[i] = s / period;
    }
    return out;
  }

  // ── Moving Averages ───────────────────────────────────────────────────────

  function sma(closes, period) {
    const out = new Array(closes.length).fill(null);
    if (closes.length < period) return out;
    let sum = 0;
    for (let i = 0; i < period; i++) sum += (closes[i] ?? 0);
    out[period - 1] = sum / period;
    for (let i = period; i < closes.length; i++) {
      sum += (closes[i] ?? 0) - (closes[i - period] ?? 0);
      out[i] = sum / period;
    }
    return out;
  }

  function ema(closes, period) {
    const out = new Array(closes.length).fill(null);
    const k = 2 / (period + 1);
    let seed = 0, count = 0;
    for (let i = 0; i < closes.length; i++) {
      if (closes[i] == null) continue;
      seed += closes[i]; count++;
      if (count === period) { out[i] = seed / period; }
      else if (count > period) { out[i] = closes[i] * k + out[i - 1] * (1 - k); }
    }
    return out;
  }

  function wma(closes, period) {
    const denom = period * (period + 1) / 2;
    return closes.map((_, i) => {
      if (i < period - 1) return null;
      let s = 0;
      for (let j = 0; j < period; j++) s += (closes[i - j] ?? 0) * (period - j);
      return s / denom;
    });
  }

  // Moving average slope (% change over n bars)
  function maSlope(maValues, bars = 5) {
    return maValues.map((v, i) => {
      if (v == null || i < bars) return null;
      const prev = maValues[i - bars];
      return prev != null && prev > 0 ? (v - prev) / prev * 100 : null;
    });
  }

  // ── RSI ───────────────────────────────────────────────────────────────────

  function rsi(closes, period = 14) {
    const out = new Array(closes.length).fill(null);
    if (closes.length < period + 1) return out;
    let ag = 0, al = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d > 0) ag += d; else al -= d;
    }
    ag /= period; al /= period;
    out[period] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      ag = (ag * (period - 1) + Math.max(d, 0)) / period;
      al = (al * (period - 1) + Math.max(-d, 0)) / period;
      out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    }
    return out;
  }

  // ── MACD ──────────────────────────────────────────────────────────────────

  function macd(closes, fast = 12, slow = 26, sig = 9) {
    const ef = ema(closes, fast), es = ema(closes, slow);
    const line = closes.map((_, i) => ef[i] != null && es[i] != null ? ef[i] - es[i] : null);
    const sigLine = ema(line.map(v => v ?? 0), sig).map((v, i) => line[i] != null ? v : null);
    const hist = line.map((v, i) => v != null && sigLine[i] != null ? v - sigLine[i] : null);
    return { macd: line, signal: sigLine, histogram: hist };
  }

  // ── ATR ───────────────────────────────────────────────────────────────────

  function atr(candles, period = 14) {
    const out = new Array(candles.length).fill(null);
    const tr = candles.map((c, i) => {
      if (i === 0) return c.h - c.l;
      const pc = candles[i - 1].c;
      return Math.max(c.h - c.l, Math.abs(c.h - pc), Math.abs(c.l - pc));
    });
    let sum = tr.slice(0, period).reduce((a, b) => a + b, 0);
    out[period - 1] = sum / period;
    for (let i = period; i < candles.length; i++) {
      out[i] = (out[i - 1] * (period - 1) + tr[i]) / period;
    }
    return out;
  }

  // ── ADX / DI ─────────────────────────────────────────────────────────────

  function adx(candles, period = 14) {
    const n = candles.length;
    const adxArr = new Array(n).fill(null);
    const pDI = new Array(n).fill(null);
    const mDI = new Array(n).fill(null);
    if (n < period * 2) return { adx: adxArr, plusDI: pDI, minusDI: mDI };

    const tr = [], pdm = [], mdm = [];
    for (let i = 0; i < n; i++) {
      if (i === 0) { tr.push(candles[i].h - candles[i].l); pdm.push(0); mdm.push(0); continue; }
      const up = candles[i].h - candles[i-1].h, dn = candles[i-1].l - candles[i].l;
      pdm.push(up > dn && up > 0 ? up : 0);
      mdm.push(dn > up && dn > 0 ? dn : 0);
      const pc = candles[i-1].c;
      tr.push(Math.max(candles[i].h - candles[i].l, Math.abs(candles[i].h - pc), Math.abs(candles[i].l - pc)));
    }
    const ws = arr => {
      const r = new Array(n).fill(null);
      r[period-1] = arr.slice(0, period).reduce((a,b)=>a+b, 0);
      for (let i = period; i < n; i++) r[i] = r[i-1] - r[i-1]/period + arr[i];
      return r;
    };
    const sTR = ws(tr), sPDM = ws(pdm), sMDM = ws(mdm);
    const dx = new Array(n).fill(null);
    for (let i = period-1; i < n; i++) {
      if (!sTR[i]) continue;
      pDI[i]  = 100 * sPDM[i] / sTR[i];
      mDI[i] = 100 * sMDM[i] / sTR[i];
      const s = pDI[i] + mDI[i];
      dx[i] = s > 0 ? 100 * Math.abs(pDI[i] - mDI[i]) / s : 0;
    }
    let adxS = 0, cnt = 0;
    for (let i = period-1; i < n; i++) {
      if (dx[i] == null) continue;
      adxS += dx[i]; cnt++;
      if (cnt === period) adxArr[i] = adxS / period;
      else if (cnt > period) adxArr[i] = (adxArr[i-1] * (period-1) + dx[i]) / period;
    }
    return { adx: adxArr, plusDI: pDI, minusDI: mDI };
  }

  // ── Stochastic ────────────────────────────────────────────────────────────

  function stochastic(candles, kPeriod = 14, dSmooth = 3, kSmooth = 1) {
    const n = candles.length;
    const rawK = new Array(n).fill(null);
    for (let i = kPeriod - 1; i < n; i++) {
      const s = candles.slice(i - kPeriod + 1, i + 1);
      const hi = Math.max(...s.map(c => c.h));
      const lo = Math.min(...s.map(c => c.l));
      rawK[i] = hi === lo ? 50 : (candles[i].c - lo) / (hi - lo) * 100;
    }
    const K = kSmooth > 1 ? _slidingSMA(rawK, kSmooth) : rawK;
    const D = _slidingSMA(K, dSmooth);
    return { K, D };
  }

  // ── Ichimoku ──────────────────────────────────────────────────────────────

  function ichimoku(candles, tenkan = 9, kijun = 26, senkouB = 52, disp = 26) {
    const n = candles.length;
    const mid = (s, e) => {
      const sl = candles.slice(s, e);
      return (Math.max(...sl.map(c => c.h)) + Math.min(...sl.map(c => c.l))) / 2;
    };
    const tenkanArr  = new Array(n).fill(null);
    const kijunArr   = new Array(n).fill(null);
    const senkouAArr = new Array(n + disp).fill(null);
    const senkouBArr = new Array(n + disp).fill(null);
    const chikouArr  = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      if (i >= tenkan - 1)  tenkanArr[i] = mid(i - tenkan + 1, i + 1);
      if (i >= kijun - 1)   kijunArr[i]  = mid(i - kijun + 1, i + 1);
      if (i >= senkouB - 1) {
        const sa = tenkanArr[i] != null && kijunArr[i] != null
          ? (tenkanArr[i] + kijunArr[i]) / 2 : null;
        senkouAArr[i + disp] = sa;
        senkouBArr[i + disp] = mid(i - senkouB + 1, i + 1);
      }
      if (i >= disp) chikouArr[i - disp] = candles[i].c;
    }
    return { tenkan: tenkanArr, kijun: kijunArr, senkouA: senkouAArr, senkouB: senkouBArr, chikou: chikouArr };
  }

  // Classify price relative to cloud
  function ichimokuSignals(candles, ichi) {
    const n = candles.length;
    const last = n - 1;
    const price = candles[last].c;
    const { tenkan, kijun, senkouA, senkouB, chikou } = ichi;
    const cloudTop    = Math.max(senkouA[last] ?? 0, senkouB[last] ?? 0);
    const cloudBottom = Math.min(senkouA[last] ?? 0, senkouB[last] ?? 0);
    const priceAbove  = price > cloudTop;
    const priceBelow  = price < cloudBottom;
    const priceInside = !priceAbove && !priceBelow;
    const cloudBull   = (senkouA[last] ?? 0) >= (senkouB[last] ?? 0);
    const thickness   = cloudTop > 0 ? (cloudTop - cloudBottom) / price * 100 : 0;

    // Tenkan/Kijun cross
    const prevT = tenkan[last - 1], prevK = kijun[last - 1];
    const curT  = tenkan[last],     curK  = kijun[last];
    let tkCross = 'none';
    if (prevT != null && prevK != null && curT != null && curK != null) {
      if (prevT < prevK && curT >= curK) tkCross = 'bullish';
      if (prevT > prevK && curT <= curK) tkCross = 'bearish';
    }
    // Chikou above/below price 26 bars ago
    const chikouPrice = candles[last - 26]?.c ?? null;
    const chikouSignal = chikou[last] != null && chikouPrice != null
      ? (chikou[last] > chikouPrice ? 'bullish' : 'bearish') : 'neutral';

    return { priceAbove, priceBelow, priceInside, cloudBull, thickness, tkCross, chikouSignal, cloudTop, cloudBottom };
  }

  // ── Bollinger Bands ───────────────────────────────────────────────────────

  function bollingerBands(closes, period = 20, mult = 2) {
    const mid = sma(closes, period);
    const upper = [], lower = [], width = [], pctB = [];
    for (let i = 0; i < closes.length; i++) {
      if (mid[i] == null) { upper.push(null); lower.push(null); width.push(null); pctB.push(null); continue; }
      const sl = closes.slice(i - period + 1, i + 1);
      const sd = Math.sqrt(sl.reduce((s, v) => s + (v - mid[i]) ** 2, 0) / period);
      upper.push(mid[i] + mult * sd);
      lower.push(mid[i] - mult * sd);
      width.push(mult * 2 * sd);
      pctB.push(sd > 0 ? (closes[i] - (mid[i] - mult * sd)) / (mult * 2 * sd) : 0.5);
    }
    return { upper, middle: mid, lower, width, pctB };
  }

  // ── Volume indicators ─────────────────────────────────────────────────────

  function obv(candles) {
    let val = 0;
    return candles.map((c, i) => {
      if (i > 0) val += c.c > candles[i-1].c ? c.v : c.c < candles[i-1].c ? -c.v : 0;
      return val;
    });
  }

  function cmf(candles, period = 20) {
    const mfv = candles.map(c => {
      const r = c.h - c.l;
      return r === 0 ? 0 : ((c.c - c.l) - (c.h - c.c)) / r * c.v;
    });
    return candles.map((_, i) => {
      if (i < period - 1) return null;
      const vs = candles.slice(i - period + 1, i + 1).reduce((s, c) => s + c.v, 0);
      return vs > 0 ? mfv.slice(i - period + 1, i + 1).reduce((s, v) => s + v, 0) / vs : 0;
    });
  }

  function mfi(candles, period = 14) {
    const tp = candles.map(c => (c.h + c.l + c.c) / 3);
    return candles.map((_, i) => {
      if (i < period) return null;
      let pos = 0, neg = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const f = tp[j] * candles[j].v;
        if (j > 0 && tp[j] > tp[j-1]) pos += f; else neg += f;
      }
      return neg === 0 ? 100 : 100 - 100 / (1 + pos / neg);
    });
  }

  function vwap(candles) {
    let cv = 0, ct = 0;
    return candles.map(c => { const tp = (c.h + c.l + c.c) / 3; ct += tp * c.v; cv += c.v; return cv > 0 ? ct / cv : tp; });
  }

  function volumeAnalysis(candles, period = 20) {
    const vols = candles.map(c => c.v);
    const v20  = sma(vols, period), v50 = sma(vols, 50);
    const rvol = candles.map((c, i) => v20[i] > 0 ? c.v / v20[i] : null);
    const acc  = new Array(candles.length).fill(false);
    const dist = new Array(candles.length).fill(false);
    for (let i = 1; i < candles.length; i++) {
      const c = candles[i], rng = c.h - c.l;
      const clv = rng > 0 ? (c.c - c.l) / rng : 0.5;
      const above = v20[i] && c.v > v20[i];
      if (c.c > candles[i-1].c && above && clv > 0.7) acc[i] = true;
      if (c.c < candles[i-1].c && above && clv < 0.3) dist[i] = true;
    }
    const distCount25 = candles.map((_, i) => i < 25 ? 0 : dist.slice(i - 25, i).filter(Boolean).length);
    const accCount25  = candles.map((_, i) => i < 25 ? 0 : acc.slice(i - 25, i).filter(Boolean).length);
    // isAccDay / isDistDay are arrays of booleans per candle (canonical names)
    return { v20, v50, rvol, isAccDay: acc, isDistDay: dist, distCount25, accCount25 };
  }

  // ── Misc oscillators ─────────────────────────────────────────────────────

  function williamsR(candles, period = 14) {
    return candles.map((c, i) => {
      if (i < period - 1) return null;
      const s = candles.slice(i - period + 1, i + 1);
      const hi = Math.max(...s.map(x => x.h)), lo = Math.min(...s.map(x => x.l));
      return hi === lo ? -50 : (hi - c.c) / (hi - lo) * -100;
    });
  }

  function cci(candles, period = 20) {
    return candles.map((c, i) => {
      if (i < period - 1) return null;
      const sl = candles.slice(i - period + 1, i + 1);
      const tp = (c.h + c.l + c.c) / 3;
      const tps = sl.map(x => (x.h + x.l + x.c) / 3);
      const mean = tps.reduce((a,b)=>a+b,0) / period;
      const md   = tps.reduce((a,b)=>a+Math.abs(b-mean),0) / period;
      return md === 0 ? 0 : (tp - mean) / (0.015 * md);
    });
  }

  function supertrend(candles, period = 10, mult = 3) {
    const atrV = atr(candles, period);
    const st = new Array(candles.length).fill(null);
    const dir = new Array(candles.length).fill(1);
    let up = 0, dn = 0;
    for (let i = period; i < candles.length; i++) {
      if (!atrV[i]) continue;
      const hl2 = (candles[i].h + candles[i].l) / 2;
      const bUp = hl2 + mult * atrV[i], bDn = hl2 - mult * atrV[i];
      up = bUp < up || candles[i-1].c > up ? bUp : up;
      dn = bDn > dn || candles[i-1].c < dn ? bDn : dn;
      dir[i] = dir[i-1] === 1 ? (candles[i].c < dn ? -1 : 1) : (candles[i].c > up ? 1 : -1);
      st[i] = dir[i] > 0 ? dn : up;
    }
    return { line: st, direction: dir };
  }

  function relativeStrength(stockCloses, benchCloses) {
    const n = Math.min(stockCloses.length, benchCloses.length);
    return Array.from({ length: n }, (_, i) => benchCloses[i] > 0 ? stockCloses[i] / benchCloses[i] : null);
  }

  // ── Compute all at once ───────────────────────────────────────────────────

  function computeAll(candles) {
    if (!candles || candles.length < 10) return null;
    const closes = _c(candles);
    return {
      // MAs
      ma5:  sma(closes,5),  ma10: sma(closes,10),  ma20: sma(closes,20),
      ma50: sma(closes,50), ma60: sma(closes,60),   ma100:sma(closes,100),
      ma120:sma(closes,120),ma150:sma(closes,150),  ma200:sma(closes,200),
      ma240:sma(closes,240),
      ema8: ema(closes,8),  ema10:ema(closes,10),   ema20:ema(closes,20),
      // Oscillators
      rsi14:    rsi(closes, 14),
      macdData: macd(closes),
      // Volatility
      atr14:    atr(candles, 14),
      bb20:     bollingerBands(closes),
      // Trend strength
      adxData:  adx(candles, 14),
      // Triple stochastic
      stochShort: stochastic(candles, 5,  3, 3),
      stochMid:   stochastic(candles, 14, 3, 3),
      stochLong:  stochastic(candles, 50, 10, 10),
      // Ichimoku
      ichiData:   ichimoku(candles),
      // Volume
      obvData:    obv(candles),
      cmf14:      cmf(candles, 14),
      mfi14:      mfi(candles, 14),
      vwapData:   vwap(candles),
      volData:    volumeAnalysis(candles),
      // Misc
      willR14:    williamsR(candles, 14),
      cci20:      cci(candles, 20),
      stData:     supertrend(candles),
    };
  }

  return {
    sma, ema, wma, maSlope, rsi, macd, atr, adx, stochastic,
    ichimoku, ichimokuSignals, bollingerBands,
    obv, cmf, mfi, vwap, volumeAnalysis,
    williamsR, cci, supertrend, relativeStrength,
    computeAll,
  };
})();
