// research/data-quality.js v3 — Data Quality Score (0–100)
// Evaluates the reliability of a signal before acting on it.
// Low DQ → cap composite score, block BUY/ADD allocation.
//
// Score breakdown (100 pts total):
//   Market Data       20  — current price valid + recent
//   Candle history    15  — ≥200 bars for SMA200 calculation
//   Fundamentals      20  — P/E(5), EPS(5), revenue(5), quality signal(5)
//   Macro context     10  — wr_macro_regime present & fresh (<6h)
//   Sector data       10  — wr_sector_perf_cache has this ticker's sector
//   Portfolio context 10  — holdings data from wd_holdings_cache
//   Target allocation  5  — CONFIG.PORTFOLIO_TARGETS entry exists
//   Signal history     5  — ≥1 prior signal for this ticker
//   System integrity   5  — no NaN in critical fields; tech score valid
//   Penalties:        up to −50
//     NaN in critical field: −30 (forced DATA_INSUFFICIENT if triggered)
//     Stale price (>36h):    −15
//     No candles at all:     −10
//     Macro data >12h old:   −5

const DATA_QUALITY = (() => {

  // ── DQ label thresholds ───────────────────────────────────────────────────
  const THRESHOLDS = [
    { min: 85, label: 'HIGH',     color: '#22c55e' },
    { min: 70, label: 'MEDIUM',   color: '#84cc16' },
    { min: 50, label: 'LOW',      color: '#eab308' },
    { min:  0, label: 'VERY_LOW', color: '#ef4444' },
  ];

  function _label(score) {
    return THRESHOLDS.find(t => score >= t.min) || THRESHOLDS[THRESHOLDS.length - 1];
  }

  // ── Allocation multiplier based on DQ ─────────────────────────────────────
  function allocationMultiplier(dqScore) {
    if (dqScore >= 85) return 1.0;
    if (dqScore >= 70) return 0.8;
    if (dqScore >= 50) return 0.4;
    return 0;  // DQ < 50 → no allocation
  }

  // ── Main scorer ────────────────────────────────────────────────────────────
  // @param ticker   string
  // @param data     { candles, fundamentals, macro, positions, targets, timingResult }
  // @returns        { score, label, color, multiplier, breakdown, missingData, forceInsufficient }
  function score(ticker, data = {}) {
    const { candles, fundamentals, macro, positions, targets, timingResult } = data;
    let pts = 0;
    const missing = [];
    const breakdown = {};
    let forceInsufficient = false;

    // If caller explicitly signals DATA_INSUFFICIENT state, force it
    if (data.state === 'DATA_INSUFFICIENT') forceInsufficient = true;

    // ── 1. Market Data — price validity (20 pts) ───────────────────────────
    const price = timingResult?.price ?? candles?.[candles?.length - 1]?.c ?? null;
    let marketDataPts = 0;
    if (price != null && isFinite(price) && price > 0) {
      marketDataPts = 20;
      pts += 20;
    } else {
      missing.push('현재가');
      if (price !== null && !isFinite(price)) {
        forceInsufficient = true;
        missing.push('Critical: 현재가 데이터 손상 (NaN)');
      }
    }

    // ── 2. Candle history (15 pts) ─────────────────────────────────────────
    const candleLen = Array.isArray(candles) ? candles.length : 0;
    let candlePts = 0;
    if (candleLen >= 200) {
      candlePts = 15;
    } else if (candleLen >= 60) {
      candlePts = 8;  missing.push(`캔들 ${candleLen}개 (<200)`);
    } else if (candleLen >= 20) {
      candlePts = 3;  missing.push(`캔들 ${candleLen}개 (<60)`);
    } else {
      candlePts = 0;
      if (!candleLen) missing.push('캔들 데이터 없음');
      else missing.push(`캔들 ${candleLen}개 (부족)`);
    }
    pts += candlePts;
    // marketData breakdown = price(20) + candles(15) combined for display
    breakdown.marketData = marketDataPts + candlePts;

    // ── 3. Fundamental data (20 pts) ──────────────────────────────────────
    // Base: P/E(5) + EPS(5) + Revenue(5) = 15pts
    // Quality signal: gross margin or ROIC present = +5pts
    const fund = fundamentals || {};
    let fundPts = 0;
    if (fund.pe   != null && isFinite(fund.pe))   fundPts += 5;
    else missing.push('P/E');
    if (fund.eps  != null && isFinite(fund.eps))  fundPts += 5;
    else missing.push('EPS');
    if (fund.rev  != null && isFinite(fund.rev))  fundPts += 5;
    else missing.push('매출');
    // Quality signal bonus: grossMargin or roic or fcfYield
    const hasQualitySignal = (
      (fund.grossMargin != null && isFinite(fund.grossMargin)) ||
      (fund.roic        != null && isFinite(fund.roic))        ||
      (fund.fcfYield    != null && isFinite(fund.fcfYield))
    );
    if (hasQualitySignal) fundPts += 5;
    else missing.push('품질 지표 (grossMargin/ROIC/FCF 수익률)');
    pts += fundPts;
    breakdown.fundamentalData = fundPts;
    if (fundPts === 0) missing.push('Critical: 펀더멘털 데이터 없음 (P/E, EPS, 매출)');

    // ── 4. Macro context (10 pts) ─────────────────────────────────────────
    let macroPts = 0;
    try {
      const macroCache = macro || JSON.parse(localStorage.getItem('wr_macro_regime') || 'null');
      const macroAge = macroCache?.ts ? (Date.now() - macroCache.ts) / 3600000 : 999;
      if (macroCache?.value && macroAge < 6) {
        macroPts = 10;
      } else if (macroCache?.value && macroAge < 12) {
        macroPts = 6; missing.push('매크로 데이터 오래됨 (6-12h)');
      } else if (macroCache?.value) {
        macroPts = 2; missing.push('매크로 데이터 오래됨 (>12h)');
      } else {
        missing.push('매크로 국면 없음');
      }
    } catch (_) { missing.push('매크로 캐시 오류'); }
    pts += macroPts;
    breakdown.macroData = macroPts;

    // ── 5. Sector data (10 pts) ────────────────────────────────────────────
    // Reads wr_sector_perf_cache — saved by sectors.js after loading
    // Maps ticker to sector ETF via TICKER_SECTOR (sectors.js) or CONFIG
    let sectorPts = 0;
    try {
      const sectorCache = JSON.parse(localStorage.getItem('wr_sector_perf_cache') || 'null');
      if (sectorCache && Object.keys(sectorCache).length) {
        // Check if we have sector data for the ticker's sector
        const t = ticker?.toUpperCase();
        // Try to find sector for this ticker
        const sectorMap = (typeof SECTORS !== 'undefined' && SECTORS.TICKER_SECTOR)
          ? SECTORS.TICKER_SECTOR : {};
        const candUniv = (typeof CAND_UNIVERSE !== 'undefined')
          ? CAND_UNIVERSE : null;
        const sectorETF = sectorMap[t]
          || (candUniv ? candUniv.getSector?.(t) : null);

        if (sectorETF && sectorCache[sectorETF]) {
          const sd = sectorCache[sectorETF];
          if (sd.ret1m != null && sd.ret3m != null) sectorPts = 10;
          else sectorPts = 5;
        } else if (Object.keys(sectorCache).length >= 8) {
          // Broad sector data present even if ticker mapping unknown
          sectorPts = 5;
          if (!sectorETF) missing.push(`섹터 미매핑 (${t})`);
        } else {
          missing.push('섹터 성과 데이터 부족');
        }
      } else {
        missing.push('섹터 성과 캐시 없음 (섹터 탭 방문 필요)');
      }
    } catch (_) { missing.push('섹터 캐시 오류'); }
    pts += sectorPts;
    breakdown.sectorData = sectorPts;

    // ── 6. Portfolio context (10 pts) ─────────────────────────────────────
    const myPositions = Array.isArray(positions)
      ? positions
      : (() => { try { return STATE?.getMyPositions?.() || []; } catch (_) { return []; } })();
    let portfolioPts = 0;
    if (myPositions.length > 0) {
      portfolioPts = 10;
      pts += 10;
    } else {
      portfolioPts = 3;
      pts += 3; missing.push('보유 포지션 없음');
    }
    breakdown.portfolioData = portfolioPts;

    // ── 7. Target allocation (5 pts) ──────────────────────────────────────
    const allTargets = targets || (() => {
      try { return CONFIG?.PORTFOLIO_TARGETS || {}; } catch (_) { return {}; }
    })();
    let targetPts = 0;
    if (allTargets[ticker] != null && allTargets[ticker] > 0) {
      targetPts = 5;
      pts += 5;
    } else {
      missing.push('목표 비중 미설정');
    }
    breakdown.targetData = targetPts;

    // ── 8. Signal history (5 pts) ─────────────────────────────────────────
    let sigHistPts = 0;
    try {
      const log = JSON.parse(localStorage.getItem('wr_recommendation_log') || '[]');
      const prior = log.filter(e => e.ticker === ticker).length;
      if (prior >= 3) {
        sigHistPts = 5;
      } else if (prior >= 1) {
        sigHistPts = 2;
      } else {
        missing.push('과거 신호 없음');
      }
    } catch (_) { /* sigHistPts stays 0 */ }
    pts += sigHistPts;
    breakdown.signalHistory = sigHistPts;

    // ── 9. System integrity (5 pts) ───────────────────────────────────────
    const techScore = timingResult?.techScore;
    const state     = timingResult?.state;
    let sysOk = (techScore != null && isFinite(techScore)) && (state != null);
    let calcPts = 0;
    if (sysOk) { calcPts = 5; pts += 5; }
    else { missing.push('Tech Score / State 미계산'); }
    breakdown.calcIntegrity = calcPts;
    // NaN guard on techScore
    if (techScore != null && !isFinite(techScore)) forceInsufficient = true;

    // ── Penalties ─────────────────────────────────────────────────────────
    let penalty = 0;

    // Stale price check (candle last bar age)
    if (candleLen > 0 && candles[candleLen - 1]?.t) {
      const ageHours = (Date.now() / 1000 - candles[candleLen - 1].t) / 3600;
      if (ageHours > 36) { penalty += 15; missing.push('가격 데이터 오래됨 (>36h)'); }
    }
    if (!candleLen) penalty += 10;
    // NaN force: hard penalty
    if (forceInsufficient) penalty += 30;

    // ── Final score ───────────────────────────────────────────────────────
    const raw = Math.max(0, Math.min(100, pts - penalty));
    const dqScore = forceInsufficient ? 0 : raw;

    const { label, color } = _label(dqScore);

    return {
      score: dqScore,
      label,
      color,
      multiplier: allocationMultiplier(dqScore),
      breakdown: {
        marketData:      breakdown.marketData      ?? 0,
        fundamentalData: breakdown.fundamentalData ?? 0,
        macroData:       breakdown.macroData       ?? 0,
        sectorData:      breakdown.sectorData      ?? 0,
        portfolioData:   breakdown.portfolioData   ?? 0,
        targetData:      breakdown.targetData      ?? 0,
        signalHistory:   breakdown.signalHistory   ?? 0,
        calcIntegrity:   breakdown.calcIntegrity   ?? 0,
      },
      missingData: missing,
      forceInsufficient,
    };
  }

  // ── Composite Score hard-cap based on DQ ─────────────────────────────────
  // Called by optimizer + timing recommendation cards
  function capCompositeScore(compositeScore, dqScore) {
    if (dqScore < 50) return Math.min(compositeScore, 55);
    return compositeScore;
  }

  // ── Buy Allocation block check ────────────────────────────────────────────
  // Returns { blocked, reason } — if blocked, allocation must be 0
  // fundamentalScore optional: if provided and < 50, blocks Core Buy
  function checkBuyBlock(dqScore, compositeScore, state, priceBelowSma200, fundamentalScore) {
    if (dqScore < 50) return { blocked: true, reason: `DQ ${dqScore} < 50 — 데이터 불충분` };
    if (state === 'EXTENDED') return { blocked: true, reason: 'EXTENDED — 과열 구간 신규 매수 금지' };
    if (priceBelowSma200) return { blocked: true, reason: '가격 < SMA200 하락 중 — 매수 블록' };
    if (fundamentalScore != null && fundamentalScore < 50) return { blocked: true, reason: 'Fundamental Score < 50 — Core Buy 금지' };
    return { blocked: false, reason: '' };
  }

  // ── Render a compact DQ badge (HTML string) ───────────────────────────────
  function badge(dqScore, dqLabel, showDetails = false) {
    const { color } = _label(dqScore);
    const tip = showDetails ? ` title="데이터 품질 점수: 신호의 신뢰도를 나타냅니다"` : '';
    return `<span class="inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded"
                  style="color:${color};border:1px solid ${color}40;background:${color}15"${tip}>
              DQ ${dqScore}
            </span>`;
  }

  return { score, allocationMultiplier, capCompositeScore, checkBuyBlock, badge };
})();
