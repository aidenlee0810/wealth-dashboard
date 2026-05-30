// research/market-regime.js — Market Regime Classifier v2
//
// 8 regimes:
//   BROAD_RISK_ON                   — broad market uptrend, credit healthy, all sectors participate
//   NARROW_THEME_LEADERSHIP         — select themes/sectors lead, broad market mixed
//   ROTATION_MARKET                 — frequent sector rotation, no clear leader
//   MACRO_RISK_OFF                  — FRED signals deteriorating, HY OAS elevated, yield inversion
//   DEFENSIVE_QUALITY_MARKET        — defensives outperform, quality premium active
//   RATE_PRESSURE_GROWTH_COMPRESSION — TIPS real rate > 2.5%, growth multiple compression
//   EARLY_RECOVERY                  — market turning up from recent low, breadth improving
//   DATA_INSUFFICIENT               — not enough data to classify
//
// Storage:
//   wr_signal_regime   — { value, regime8, confidence, secondaryRegime, weights, playbook, ts }
//   wr_market_regime_log — [..., { regime8, confidence, ts }]  (rolling 90-entry log)
//
// Whipsaw prevention:
//   - New regime must be confirmed in 2 consecutive classify() calls to take effect
//   - Exception: MACRO_RISK_OFF with confidence ≥ 80 triggers immediately
//   - Pending candidate stored in wr_signal_regime_pending

const MARKET_REGIME = (() => {

  // ── Regime metadata ──────────────────────────────────────────────────────────
  const REGIMES = {
    BROAD_RISK_ON: {
      label: '광범위 강세장 (Broad Risk-On)',
      color: '#22c55e',
      bg: 'bg-emerald-950/30 border-emerald-800/50',
      dot: 'bg-emerald-400',
      simpleValue: 'RISK_ON',
      desc: '전 섹터 참여 강세 — 포트폴리오 풀 포지션 가동 적기',
      icon: '🚀',
    },
    NARROW_THEME_LEADERSHIP: {
      label: '테마 주도 장세 (Narrow Theme Leadership)',
      color: '#3b82f6',
      bg: 'bg-blue-950/30 border-blue-800/50',
      dot: 'bg-blue-400',
      simpleValue: 'RISK_ON',
      desc: '특정 테마(AI·반도체 등)가 시장 주도 — 테마 집중 투자 유리',
      icon: '🎯',
    },
    ROTATION_MARKET: {
      label: '섹터 로테이션 장 (Rotation Market)',
      color: '#f59e0b',
      bg: 'bg-amber-950/20 border-amber-800/40',
      dot: 'bg-amber-400',
      simpleValue: 'NEUTRAL',
      desc: '섹터 리더십 순환 — 광범위 ETF가 개별 종목보다 유리',
      icon: '🔄',
    },
    MACRO_RISK_OFF: {
      label: '매크로 리스크오프 (Macro Risk-Off)',
      color: '#ef4444',
      bg: 'bg-red-950/30 border-red-800/50',
      dot: 'bg-red-400',
      simpleValue: 'RISK_OFF',
      desc: '신용 스프레드 확대·수익률 역전 — 현금·방어주 비중 확대',
      icon: '🚨',
    },
    DEFENSIVE_QUALITY_MARKET: {
      label: '방어·퀄리티 장세 (Defensive Quality)',
      color: '#a78bfa',
      bg: 'bg-purple-950/20 border-purple-800/40',
      dot: 'bg-purple-400',
      simpleValue: 'CAUTION',
      desc: '방어주·고퀄리티주 프리미엄 — ROIC 높은 우량주 선호',
      icon: '🛡️',
    },
    RATE_PRESSURE_GROWTH_COMPRESSION: {
      label: '금리 압박 성장 압축 (Rate Pressure)',
      color: '#f97316',
      bg: 'bg-orange-950/20 border-orange-800/40',
      dot: 'bg-orange-400',
      simpleValue: 'CAUTION',
      desc: 'TIPS 실질금리 상승 → 고PER 성장주 밸류에이션 압박',
      icon: '📈',
    },
    EARLY_RECOVERY: {
      label: '초기 회복기 (Early Recovery)',
      color: '#84cc16',
      bg: 'bg-lime-950/20 border-lime-800/40',
      dot: 'bg-lime-400',
      simpleValue: 'NEUTRAL',
      desc: '저점 통과 초기 — 사이클리컬·금융주·소재 주도',
      icon: '🌱',
    },
    DATA_INSUFFICIENT: {
      label: '데이터 부족 (Data Insufficient)',
      color: '#64748b',
      bg: 'bg-slate-800/30 border-slate-700',
      dot: 'bg-slate-400',
      simpleValue: 'NEUTRAL',
      desc: '분류 불가 — 기본 중립 가중치 적용',
      icon: '⚠️',
    },
  };

  // ── Composite weights by regime ───────────────────────────────────────────────
  // businessQuality + valuation + macro + sectorTheme + technical = 100
  const REGIME_WEIGHTS = {
    BROAD_RISK_ON:                    { businessQuality: 30, valuation: 10, macro: 20, sectorTheme: 25, technical: 15 },
    NARROW_THEME_LEADERSHIP:          { businessQuality: 25, valuation: 10, macro: 15, sectorTheme: 35, technical: 15 },
    ROTATION_MARKET:                  { businessQuality: 30, valuation: 15, macro: 15, sectorTheme: 25, technical: 15 },
    MACRO_RISK_OFF:                   { businessQuality: 40, valuation: 15, macro: 30, sectorTheme:  5, technical: 10 },
    DEFENSIVE_QUALITY_MARKET:         { businessQuality: 50, valuation: 15, macro: 20, sectorTheme:  5, technical: 10 },
    RATE_PRESSURE_GROWTH_COMPRESSION: { businessQuality: 35, valuation: 20, macro: 25, sectorTheme: 10, technical: 10 },
    EARLY_RECOVERY:                   { businessQuality: 25, valuation: 15, macro: 20, sectorTheme: 20, technical: 20 },
    DATA_INSUFFICIENT:                { businessQuality: 35, valuation: 10, macro: 20, sectorTheme: 20, technical: 15 },
  };

  // ── Market Playbook by regime ─────────────────────────────────────────────────
  const PLAYBOOK = {
    BROAD_RISK_ON: {
      strategy: '풀 포지션 가동',
      actions: [
        '전 포트폴리오 목표 비중 유지 또는 초과 허용',
        'TRIGGER/ADD 신호 → 정상 배분 집행',
        '성장주·테마주 비중 상단까지 확대 허용',
        '사이클리컬(XLF, XLI, XLY) 비중 점검',
      ],
      avoid: ['과도한 현금 보유', '방어주 과비중'],
      candidatePreference: ['TIER_1 성장주', 'AI·반도체', '금융·산업재'],
    },
    NARROW_THEME_LEADERSHIP: {
      strategy: '주도 테마 집중',
      actions: [
        '선도 섹터(AI/클라우드/반도체) 종목 비중 확대',
        '비주도 섹터 비중 중립 이하 유지',
        '테마 모멘텀 지속 여부 주 1회 점검',
        'TRIGGER 신호 = 테마 리더 종목에 집중',
      ],
      avoid: ['광범위 섹터 분산', '테마 무관 라지캡 단순 추가'],
      candidatePreference: ['AI 테마', '반도체', '클라우드 SaaS'],
    },
    ROTATION_MARKET: {
      strategy: '분산·ETF 선호',
      actions: [
        '광범위 인덱스 ETF(QQQ, SPY) 위주로 정기 적립',
        '개별 종목 신규 진입 시 소량(50% 배분) 분할 매수',
        '강세 섹터 월별 점검 후 선도 ETF에 추가',
        '현금 비중 10-15% 유지',
      ],
      avoid: ['단일 섹터 집중', '강한 모멘텀 종목에 일시 대량 진입'],
      candidatePreference: ['QQQM', 'XLK', '퀄리티 대형주'],
    },
    MACRO_RISK_OFF: {
      strategy: '방어 모드 — 현금·안전자산',
      actions: [
        '신규 매수 전면 중단 (TRIGGER 신호도 보류)',
        '손실 포지션 손절 가속화',
        '현금 비중 30-50% 목표',
        '방어주(XLP, XLU, XLV) 또는 TLT 헤지 검토',
        '다음 분기 재진입 기준 사전 설정',
      ],
      avoid: ['신규 위험자산 진입', '레버리지 ETF', '고베타 종목 추가'],
      candidatePreference: ['현금', 'XLP', 'XLU', 'XLV', 'BND'],
    },
    DEFENSIVE_QUALITY_MARKET: {
      strategy: '퀄리티 집중',
      actions: [
        'ROIC > WACC 확인된 TIER_1 우량주만 신규 매수',
        '고PER 성장주 비중 축소 검토',
        '배당·FCF 우량주 비중 확대',
        '포트폴리오 Beta 0.8 이하 목표',
      ],
      avoid: ['TIER_3 투기주', '수익 없는 성장주', '고레버리지 종목'],
      candidatePreference: ['LLY', 'ISRG', '대형 퀄리티 헬스케어', '자유현금흐름 우량주'],
    },
    RATE_PRESSURE_GROWTH_COMPRESSION: {
      strategy: '밸류에이션 방어',
      actions: [
        '고PER(>30) 성장주 비중 점진적 축소',
        '가치주·배당주·에너지 섹터 비중 확대',
        'DCF 기반 내재가치 대비 30% 이상 할인된 종목만 신규 진입',
        'TECL 등 레버리지 ETF 비중 최소화',
      ],
      avoid: ['밸류에이션 무시한 성장주 추가', '장기 국채 대량 매수'],
      candidatePreference: ['가치주', 'XLF', 'XLE', '낮은 PER 성장주'],
    },
    EARLY_RECOVERY: {
      strategy: '사이클리컬 초기 진입',
      actions: [
        '사이클리컬 섹터(XLF, XLI, XLY, XLB) 비중 점진 확대',
        'TRIGGER 신호 나온 베타 높은 종목 소량 진입 개시',
        '기존 방어주 비중 서서히 축소',
        '포트폴리오 Beta 서서히 시장 수준(1.0)으로 복귀',
      ],
      avoid: ['지나친 방어 포지션 유지', '회복기 랠리에서 완전 소외'],
      candidatePreference: ['XLF', 'XLI', 'AMZN', 'META', '사이클리컬 우량주'],
    },
    DATA_INSUFFICIENT: {
      strategy: '기본 중립',
      actions: [
        '기존 포트폴리오 유지',
        '신규 진입은 TRIGGER + 높은 DQ 점수 조합만 허용',
        '다음 매크로 탭 업데이트 후 재분류 대기',
      ],
      avoid: ['대규모 포지션 변경'],
      candidatePreference: ['TIER_1 우량주 위주'],
    },
  };

  // ── Storage keys ─────────────────────────────────────────────────────────────
  const KEY_REGIME      = 'wr_signal_regime';
  const KEY_PENDING     = 'wr_signal_regime_pending';
  const KEY_LOG         = 'wr_market_regime_log';
  const LOG_MAX         = 90;   // rolling entries
  const WHIPSAW_TTL_MS  = 12 * 3600000;  // 12h before pending expires

  // ── Read helpers ──────────────────────────────────────────────────────────────
  function _readMacroRegime() {
    try {
      const r = JSON.parse(localStorage.getItem('wr_macro_regime') || 'null');
      if (r?.value && Date.now() - (r.ts || 0) < 6 * 3600000) return r;
    } catch (_) {}
    return null;
  }

  function _readSectorPerf() {
    try {
      return JSON.parse(localStorage.getItem('wr_sector_perf_cache') || 'null');
    } catch (_) { return null; }
  }

  function _readCurrentRegime() {
    try {
      return JSON.parse(localStorage.getItem(KEY_REGIME) || 'null');
    } catch (_) { return null; }
  }

  function _readPending() {
    try {
      const p = JSON.parse(localStorage.getItem(KEY_PENDING) || 'null');
      if (!p) return null;
      // Expire pending after WHIPSAW_TTL_MS
      if (Date.now() - (p.ts || 0) > WHIPSAW_TTL_MS) return null;
      return p;
    } catch (_) { return null; }
  }

  // ── Signal feature extraction ─────────────────────────────────────────────────
  function _extractFeatures() {
    const macroCache = _readMacroRegime();
    const sectorPerf = _readSectorPerf();
    const macro      = macroCache?.value || null;        // RISK_ON|NEUTRAL|CAUTION|RISK_OFF
    const macroAge   = macroCache ? Date.now() - (macroCache.ts || 0) : Infinity;
    const hasMacro   = macroCache?.value && macroAge < 8 * 3600000;

    // Sector performance features
    let sectorDispersion = null;
    let growthLeading    = false;
    let defensiveLeading = false;
    let anySectorData    = false;

    if (sectorPerf) {
      const growthSyms    = ['XLK', 'XLC', 'XLY'];
      const defensiveSyms = ['XLP', 'XLU', 'XLV'];

      const getAvgRet = (syms, period) => {
        const vals = syms.map(s => sectorPerf[s]?.[period]).filter(v => v != null && isFinite(v));
        return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      };

      const allRets3m = Object.values(sectorPerf)
        .map(d => d?.ret3m)
        .filter(v => v != null && isFinite(v));

      if (allRets3m.length >= 4) {
        anySectorData = true;
        const mean = allRets3m.reduce((a, b) => a + b, 0) / allRets3m.length;
        const variance = allRets3m.reduce((s, v) => s + (v - mean) ** 2, 0) / allRets3m.length;
        sectorDispersion = Math.sqrt(variance);

        const growthAvg3m    = getAvgRet(growthSyms,    'ret3m');
        const defensiveAvg3m = getAvgRet(defensiveSyms, 'ret3m');
        if (growthAvg3m != null && defensiveAvg3m != null) {
          growthLeading    = growthAvg3m    > defensiveAvg3m + 3;   // growth >3pp ahead
          defensiveLeading = defensiveAvg3m > growthAvg3m    + 3;   // defensive >3pp ahead
        }
      }
    }

    // Macro-level signals from macro cache raw fields
    const tipsRate   = macroCache?.tipsRate   ?? null;  // set by macro.js when saving
    const hyOas      = macroCache?.hyOas      ?? null;  // basis points
    const yieldCurve = macroCache?.yieldCurve ?? null;  // 10Y-2Y spread

    // SPY/QQQ technical state from signal regime (set by signal-engine)
    let signalTechRegime = null;
    try {
      const sr = JSON.parse(localStorage.getItem(KEY_REGIME) || 'null');
      if (sr?.value && !sr?.regime8) signalTechRegime = sr.value; // simple 4-val from signal-engine
    } catch (_) {}

    return {
      macro,
      hasMacro,
      tipsRate,
      hyOas,
      yieldCurve,
      sectorDispersion,
      growthLeading,
      defensiveLeading,
      anySectorData,
      signalTechRegime,
    };
  }

  // ── Regime scoring ────────────────────────────────────────────────────────────
  function _scoreRegimes(f) {
    const scores = {};

    // ── BROAD_RISK_ON ────────────────────────────────────────────────────────
    let s = 0;
    if (f.macro === 'RISK_ON')         s += 35;
    if (f.signalTechRegime === 'RISK_ON') s += 25;
    if (f.growthLeading)               s += 15;
    if (f.anySectorData && f.sectorDispersion != null && f.sectorDispersion < 5) s += 15; // broad participation
    if (f.hyOas != null && f.hyOas < 350)  s += 10;
    scores.BROAD_RISK_ON = Math.min(100, s);

    // ── NARROW_THEME_LEADERSHIP ──────────────────────────────────────────────
    s = 0;
    if (f.signalTechRegime === 'RISK_ON' && f.macro !== 'RISK_ON') s += 30;
    if (f.signalTechRegime === 'RISK_ON' && f.macro === 'RISK_ON')  s += 15; // still possible
    if (f.growthLeading)                    s += 25;
    if (f.anySectorData && f.sectorDispersion != null && f.sectorDispersion > 6) s += 20; // high dispersion = narrow leadership
    if (f.macro === 'NEUTRAL')              s += 10;
    scores.NARROW_THEME_LEADERSHIP = Math.min(100, s);

    // ── ROTATION_MARKET ──────────────────────────────────────────────────────
    s = 0;
    if (f.macro === 'NEUTRAL')              s += 30;
    if (f.signalTechRegime === 'NEUTRAL')   s += 25;
    if (f.anySectorData && f.sectorDispersion != null && f.sectorDispersion > 4 && f.sectorDispersion < 9) s += 25;
    if (!f.growthLeading && !f.defensiveLeading) s += 20; // no clear sector winner
    scores.ROTATION_MARKET = Math.min(100, s);

    // ── MACRO_RISK_OFF ───────────────────────────────────────────────────────
    s = 0;
    if (f.macro === 'RISK_OFF')             s += 50;
    if (f.macro === 'CAUTION')              s += 20;
    if (f.hyOas != null && f.hyOas > 700)   s += 30;
    else if (f.hyOas != null && f.hyOas > 500) s += 20;
    if (f.yieldCurve != null && f.yieldCurve < -0.5) s += 15;
    if (f.signalTechRegime === 'RISK_OFF')  s += 20;
    scores.MACRO_RISK_OFF = Math.min(100, s);

    // ── DEFENSIVE_QUALITY_MARKET ─────────────────────────────────────────────
    s = 0;
    if (f.defensiveLeading)                 s += 35;
    if (f.macro === 'CAUTION')              s += 25;
    if (f.signalTechRegime === 'CAUTION' || f.signalTechRegime === 'NEUTRAL') s += 20;
    if (f.anySectorData && !f.growthLeading) s += 15;
    if (f.hyOas != null && f.hyOas > 350 && f.hyOas < 500) s += 15; // mild stress
    scores.DEFENSIVE_QUALITY_MARKET = Math.min(100, s);

    // ── RATE_PRESSURE_GROWTH_COMPRESSION ────────────────────────────────────
    s = 0;
    if (f.tipsRate != null && f.tipsRate > 2.5)  s += 45;
    else if (f.tipsRate != null && f.tipsRate > 1.5) s += 25;
    if (f.macro === 'CAUTION')               s += 20;
    if (!f.growthLeading && f.anySectorData) s += 20;
    if (f.yieldCurve != null && f.yieldCurve > 0.5) s += 10; // bear-steepener
    scores.RATE_PRESSURE_GROWTH_COMPRESSION = Math.min(100, s);

    // ── EARLY_RECOVERY ───────────────────────────────────────────────────────
    s = 0;
    if (f.macro === 'NEUTRAL' && f.signalTechRegime === 'NEUTRAL') s += 30;
    if (f.macro === 'NEUTRAL' && f.signalTechRegime === 'RISK_ON')  s += 40;
    if (f.macro === 'CAUTION' && f.signalTechRegime === 'NEUTRAL')  s += 25;
    if (f.anySectorData && !f.defensiveLeading && !f.growthLeading) s += 15;
    scores.EARLY_RECOVERY = Math.min(100, s);

    // ── DATA_INSUFFICIENT ────────────────────────────────────────────────────
    // High score when very little data is available
    let dataPenalty = 0;
    if (f.hasMacro)       dataPenalty += 30;
    if (f.anySectorData)  dataPenalty += 30;
    if (f.signalTechRegime) dataPenalty += 25;
    scores.DATA_INSUFFICIENT = Math.max(5, 85 - dataPenalty);

    return scores;
  }

  // ── Classify regime ──────────────────────────────────────────────────────────
  function classify() {
    const f = _extractFeatures();
    const scores = _scoreRegimes(f);

    // Sort by score descending
    const sorted = Object.entries(scores).sort((a, b) => b[1] - a[1]);
    const [topRegime, topScore] = sorted[0];
    const [secondRegime, secondScore] = sorted[1] || ['DATA_INSUFFICIENT', 0];

    // Confidence: gap between top and second (wider = more confident)
    const gap = topScore - secondScore;
    const confidence = Math.min(100, Math.round(topScore * 0.6 + gap * 2));

    const proposed = topRegime;
    const meta = REGIMES[proposed] || REGIMES.DATA_INSUFFICIENT;

    // ── Whipsaw prevention ────────────────────────────────────────────────────
    const current = _readCurrentRegime();
    const pending = _readPending();
    const currentRegime8 = current?.regime8 || 'DATA_INSUFFICIENT';

    let finalRegime = currentRegime8;
    let isNewRegime = false;

    if (proposed === currentRegime8) {
      // Already in this regime — no change needed
      finalRegime = currentRegime8;
      // Clear pending if it was pointing elsewhere
      try { localStorage.removeItem(KEY_PENDING); } catch (_) {}

    } else if (proposed === 'MACRO_RISK_OFF' && confidence >= 80) {
      // Severe risk-off: override immediately
      finalRegime = proposed;
      isNewRegime = true;
      try { localStorage.removeItem(KEY_PENDING); } catch (_) {}

    } else if (pending?.regime8 === proposed) {
      // Second consecutive vote for the same regime — confirm
      finalRegime = proposed;
      isNewRegime = true;
      try { localStorage.removeItem(KEY_PENDING); } catch (_) {}

    } else {
      // First vote for a new regime — set as pending (not committed yet)
      try {
        localStorage.setItem(KEY_PENDING, JSON.stringify({ regime8: proposed, score: topScore, ts: Date.now() }));
      } catch (_) {}
      // Keep current regime until confirmed
      finalRegime = currentRegime8 || proposed;  // if no current, use proposed immediately
      if (!currentRegime8 || currentRegime8 === 'DATA_INSUFFICIENT') {
        finalRegime = proposed;
        isNewRegime = true;
      }
    }

    const finalMeta    = REGIMES[finalRegime] || REGIMES.DATA_INSUFFICIENT;
    const finalWeights = REGIME_WEIGHTS[finalRegime] || REGIME_WEIGHTS.DATA_INSUFFICIENT;
    const finalPlaybook = PLAYBOOK[finalRegime] || PLAYBOOK.DATA_INSUFFICIENT;

    const regimeData = {
      value:            finalMeta.simpleValue,   // backward-compat 4-value
      regime8:          finalRegime,
      confidence,
      secondaryRegime:  secondRegime,
      weights:          finalWeights,
      playbook:         finalPlaybook,
      signals: {
        macroRegime:       f.macro,
        signalTechRegime:  f.signalTechRegime,
        tipsRate:          f.tipsRate,
        hyOas:             f.hyOas,
        yieldCurve:        f.yieldCurve,
        growthLeading:     f.growthLeading,
        defensiveLeading:  f.defensiveLeading,
        sectorDispersion:  f.sectorDispersion,
      },
      ts: Date.now(),
    };

    // ── Persist ───────────────────────────────────────────────────────────────
    try { localStorage.setItem(KEY_REGIME, JSON.stringify(regimeData)); } catch (_) {}

    // ── Append to regime log ──────────────────────────────────────────────────
    if (isNewRegime) {
      try {
        const log = JSON.parse(localStorage.getItem(KEY_LOG) || '[]');
        log.push({ regime8: finalRegime, confidence, ts: Date.now() });
        localStorage.setItem(KEY_LOG, JSON.stringify(log.slice(-LOG_MAX)));
      } catch (_) {}
    }

    return regimeData;
  }

  // ── Get current regime (from cache or classify if stale) ────────────────────
  // TTL: 3h before re-classifying from cached inputs
  function get(forceRefresh = false) {
    if (!forceRefresh) {
      const cached = _readCurrentRegime();
      if (cached?.regime8 && Date.now() - (cached.ts || 0) < 3 * 3600000) {
        return cached;
      }
    }
    return classify();
  }

  // ── Get just the simple 4-value regime (backward compat) ────────────────────
  function getSimpleValue() {
    return get()?.value || 'NEUTRAL';
  }

  // ── Get regime-specific composite weights ────────────────────────────────────
  function getWeights(regime8) {
    return REGIME_WEIGHTS[regime8] || REGIME_WEIGHTS.DATA_INSUFFICIENT;
  }

  // ── Get regime log ────────────────────────────────────────────────────────────
  function getLog() {
    try { return JSON.parse(localStorage.getItem(KEY_LOG) || '[]'); }
    catch (_) { return []; }
  }

  // ── Regime fit score: how well does this ticker fit the current regime ────────
  // Returns 0–100 based on ticker type vs regime preference
  function regimeFitScore(ticker, fundamentalTier, sectorETF, isHighBeta) {
    const regime = get()?.regime8 || 'DATA_INSUFFICIENT';
    const playbook = PLAYBOOK[regime] || PLAYBOOK.DATA_INSUFFICIENT;

    let score = 50; // neutral baseline

    // Prefer TIER_1 in defensive/rate pressure regimes
    if (['DEFENSIVE_QUALITY_MARKET', 'RATE_PRESSURE_GROWTH_COMPRESSION', 'MACRO_RISK_OFF'].includes(regime)) {
      if (fundamentalTier === 'TIER_1') score += 25;
      if (fundamentalTier === 'TIER_3') score -= 30;
      if (isHighBeta) score -= 20;
    }

    // Prefer growth / theme leaders in broad risk-on / narrow theme
    if (['BROAD_RISK_ON', 'NARROW_THEME_LEADERSHIP'].includes(regime)) {
      if (fundamentalTier === 'TIER_1') score += 15;
      if (fundamentalTier === 'TIER_3') score -= 10;  // still can work in risk-on
      if (sectorETF === 'XLK' || sectorETF === 'XLC') score += 15;
    }

    // Prefer early recovery picks
    if (regime === 'EARLY_RECOVERY') {
      if (['XLF', 'XLI', 'XLY', 'XLB'].includes(sectorETF)) score += 20;
      if (fundamentalTier === 'TIER_1') score += 10;
    }

    return Math.max(0, Math.min(100, score));
  }

  // ── Render a compact regime badge (HTML string) ───────────────────────────────
  function badge(regime8, showFull = false) {
    const meta = REGIMES[regime8] || REGIMES.DATA_INSUFFICIENT;
    if (!showFull) {
      return `<span class="inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded"
                    style="color:${meta.color};border:1px solid ${meta.color}40;background:${meta.color}15">
                ${meta.icon} ${regime8.replace(/_/g,' ')}
              </span>`;
    }
    return `<div class="${meta.bg} border rounded-xl px-4 py-3">
      <div class="flex items-center gap-2">
        <span class="text-lg">${meta.icon}</span>
        <span class="font-bold text-sm" style="color:${meta.color}">${meta.label}</span>
      </div>
      <p class="text-xs text-slate-400 mt-1">${meta.desc}</p>
    </div>`;
  }

  // ── Render full Market Playbook card (HTML string) ────────────────────────────
  function renderPlaybook(regimeData, compact = false) {
    if (!regimeData) regimeData = get();
    const regime8   = regimeData?.regime8 || 'DATA_INSUFFICIENT';
    const meta      = REGIMES[regime8]    || REGIMES.DATA_INSUFFICIENT;
    const playbook  = PLAYBOOK[regime8]   || PLAYBOOK.DATA_INSUFFICIENT;
    const conf      = regimeData?.confidence ?? 0;
    const secondary = regimeData?.secondaryRegime;
    const secMeta   = secondary ? REGIMES[secondary] : null;

    if (compact) {
      return `
        <div class="${meta.bg} border rounded-xl p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2">
              <span class="text-xl">${meta.icon}</span>
              <div>
                <div class="font-bold text-sm" style="color:${meta.color}">${meta.label}</div>
                <div class="text-xs text-slate-500">신뢰도 ${conf}% ${secMeta ? `· 2위: ${secMeta.icon}${secondary.replace(/_/g,' ')}` : ''}</div>
              </div>
            </div>
            <div class="text-xs font-bold px-2 py-0.5 rounded"
                 style="color:${meta.color};border:1px solid ${meta.color}40;background:${meta.color}15">
              ${playbook.strategy}
            </div>
          </div>
          <ul class="space-y-1 mt-2">
            ${playbook.actions.slice(0, 3).map(a => `<li class="text-xs text-slate-300 flex items-start gap-1.5"><span class="text-emerald-400 flex-shrink-0">▸</span>${a}</li>`).join('')}
          </ul>
        </div>`;
    }

    return `
      <div class="mb-6">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <span class="text-2xl">${meta.icon}</span>
            <div>
              <div class="font-bold text-base" style="color:${meta.color}">${meta.label}</div>
              <div class="text-xs text-slate-400">신뢰도 ${conf}%
                ${secMeta ? `<span class="text-slate-600 mx-1">·</span>2위 후보: <span style="color:${secMeta.color}">${secMeta.icon} ${secondary.replace(/_/g,' ')}</span>` : ''}
              </div>
            </div>
          </div>
          <div class="text-right hidden sm:block">
            <div class="text-xs text-slate-500 mb-0.5">레짐별 가중치</div>
            <div class="text-xs text-slate-400 font-mono">
              ${_weightsRow(REGIME_WEIGHTS[regime8] || REGIME_WEIGHTS.DATA_INSUFFICIENT)}
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="${meta.bg} border rounded-xl p-4">
            <div class="text-xs font-semibold text-slate-300 mb-2">📋 플레이북 · ${playbook.strategy}</div>
            <ul class="space-y-1.5">
              ${playbook.actions.map(a => `
                <li class="flex items-start gap-2 text-xs text-slate-300">
                  <span class="text-emerald-400 flex-shrink-0 mt-0.5">▸</span>
                  <span>${a}</span>
                </li>`).join('')}
            </ul>
          </div>
          <div class="space-y-3">
            <div class="bg-slate-800/50 border border-slate-700 rounded-xl p-3">
              <div class="text-xs font-semibold text-slate-300 mb-2">🎯 우선 후보</div>
              <div class="flex flex-wrap gap-1">
                ${playbook.candidatePreference.map(c =>
                  `<span class="bg-slate-700/80 text-slate-200 text-xs px-2 py-0.5 rounded font-mono">${c}</span>`
                ).join('')}
              </div>
            </div>
            <div class="bg-red-950/20 border border-red-800/30 rounded-xl p-3">
              <div class="text-xs font-semibold text-red-400 mb-2">⛔ 이 국면에서 피할 것</div>
              <ul class="space-y-1">
                ${playbook.avoid.map(a => `<li class="text-xs text-red-300 flex items-start gap-1.5"><span>•</span>${a}</li>`).join('')}
              </ul>
            </div>
          </div>
        </div>
      </div>`;
  }

  function _weightsRow(w) {
    return `BQ${w.businessQuality} Val${w.valuation} Mac${w.macro} Sec${w.sectorTheme} Tech${w.technical}`;
  }

  return {
    REGIMES,
    REGIME_WEIGHTS,
    PLAYBOOK,
    classify,
    get,
    getSimpleValue,
    getWeights,
    getLog,
    regimeFitScore,
    badge,
    renderPlaybook,
  };
})();
