// research/candidate.js — 🔭 신규편입 후보 탭
// 펀더멘털·매크로·섹터/테마·차트 조건을 통과한 신규편입 후보 스캔 및 관리
// Container: tab-candidates
// Cache key: wr_candidate_scan_cache
// Watchlist key: wr_candidate_watchlist

const CANDIDATES = (() => {
  const CONTAINER = 'tab-candidates';
  const CACHE_KEY = 'wr_candidate_scan_cache';
  const WATCHLIST_KEY = 'wr_candidate_watchlist';
  const SCAN_DEPTH_KEY = 'wr_candidate_scan_depth';

  // Browser-side deep scans run through free API limits. Standard covers enough
  // breadth for useful discovery without making every scan an all-afternoon job.
  const SCAN_DEPTHS = {
    quick:    { label: '빠른',   limit: 120, note: '핵심 후보 위주' },
    standard: { label: '표준',   limit: 220, note: '일일 추천용' },
    broad:    { label: '광범위', limit: 350, note: '주간 후보 발굴' },
    full:     { label: '전체',   limit: Infinity, note: '전체 유니버스' },
  };

  // ── Escape helper ──────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Toast notification ─────────────────────────────────────────────────────
  function _toast(msg, color = 'emerald') {
    const t = document.createElement('div');
    t.className = `fixed bottom-6 right-6 z-50 bg-${color}-600 text-white text-sm px-4 py-2.5 rounded-xl shadow-lg transition-opacity`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 2200);
  }

  // ── Scoring Functions ──────────────────────────────────────────────────────
  let _fundamentalsByTicker = {};
  let _marketLens = null;

  async function _loadStaticFundamentals() {
    if (typeof DB === 'undefined' || typeof DB.view !== 'function') return {};
    try {
      const view = await DB.view('latest_fundamentals');
      return view?.fundamentals || {};
    } catch (_) {
      return {};
    }
  }

  async function _loadMarketLens() {
    if (typeof DB === 'undefined' || typeof DB.view !== 'function') return null;
    try {
      const [reg, st] = await Promise.all([
        DB.view('latest_market_regime').catch(() => null),
        DB.view('latest_sector_theme').catch(() => null),
      ]);
      return {
        regime: reg?.regime || 'UNKNOWN',
        confidence: reg?.confidence ?? null,
        favoredThemes: reg?.favored_themes || [],
        emergingThemes: reg?.emerging_themes || [],
        favoredSectors: reg?.favored_sectors || [],
        topThemes: (st?.themes || []).slice(0, 6),
        topSectors: (st?.sectors || []).slice(0, 6),
      };
    } catch (_) {
      return null;
    }
  }

  function _fundamentalFromSnapshot(ticker) {
    return _fundamentalsByTicker?.[(ticker || '').toUpperCase()] || null;
  }

  // Fundamental Score 0–45
  function _scoreFundamental(ticker, quoteData) {
    const f = _fundamentalFromSnapshot(ticker);
    if (f) {
      let score = 14;
      const roic = f.roic;
      const fcfMargin = f.fcf_margin;
      const growth = f.revenue_growth_yoy;
      const peerPe = f.peer_valuation?.sector?.pe || f.peer_valuation?.themes?.[0] || null;
      const peg = f.peg_ttm;
      const coverage = f.coverage_ratio ?? 0;

      if (roic != null) {
        if (roic >= 0.25) score += 10;
        else if (roic >= 0.15) score += 8;
        else if (roic >= 0.08) score += 5;
        else if (roic > 0) score += 2;
      }
      if (fcfMargin != null) {
        if (fcfMargin >= 0.25) score += 8;
        else if (fcfMargin >= 0.15) score += 6;
        else if (fcfMargin >= 0.05) score += 3;
        else if (fcfMargin < 0) score -= 3;
      }
      if (growth != null) {
        if (growth >= 0.25) score += 7;
        else if (growth >= 0.12) score += 5;
        else if (growth >= 0.03) score += 2;
        else if (growth < 0) score -= 3;
      }
      if (peg != null) {
        if (peg > 0 && peg < 1.2) score += 4;
        else if (peg > 2.5) score -= 2;
      }
      if (peerPe?.interpretation === 'cheaper_than_peers') score += 3;
      if (peerPe?.interpretation === 'expensive_vs_peers') score -= 2;
      if (coverage >= 80) score += 2;
      else if (coverage && coverage < 50) score -= 2;
      return Math.max(8, Math.min(45, Math.round(score)));
    }

    const tier = CAND_UNIVERSE.getFundamentalTier(ticker);
    let score = tier === 'TIER_1' ? 35 : tier === 'TIER_2' ? 25 : 15; // TIER_3 + UNKNOWN = 15

    // Tier 1/2 already implies large-cap / institutional-quality coverage in
    // the current curated universe, so keep this bonus even during quote-lite
    // broad scans.
    if (tier === 'TIER_1' || tier === 'TIER_2') score += 2;

    if (quoteData) {
      // 52-week high proximity < 30% below ATH → momentum quality
      const high52w = quoteData.high52w ?? quoteData.h;
      const price = quoteData.price ?? quoteData.c;
      if (high52w && price && high52w > 0) {
        const pctFromHigh = (high52w - price) / high52w;
        if (pctFromHigh < 0.30) score += 2;
      }

      // P/E check: use quote 'pe' if present (Finnhub basic financials not available in quote)
      // quoteData from API.quote returns { c, h, l, o, pc, t } — no PE; skip PE adjustment
    }

    return Math.min(45, score);
  }

  // Macro Score 0–20 — reads wr_macro_regime (FRED) + wr_signal_regime (8-regime)
  function _scoreMacro() {
    try {
      // Base score from FRED macro regime
      const macroCache = JSON.parse(localStorage.getItem('wr_macro_regime') || 'null');
      const macroVal = macroCache?.value || 'NEUTRAL';
      let score = { RISK_ON: 16, NEUTRAL: 11, CAUTION: 6, RISK_OFF: 2 }[macroVal] ?? 10;

      // Adjust for 8-regime signal
      const sigCache = JSON.parse(localStorage.getItem('wr_signal_regime') || 'null');
      const regime8 = sigCache?.regime8 || '';
      if (regime8 === 'BROAD_RISK_ON')                    score = Math.min(20, score + 2);
      if (regime8 === 'MACRO_RISK_OFF')                   score = Math.max(1,  score - 3);
      if (regime8 === 'EARLY_RECOVERY')                   score = Math.min(18, score + 1);
      if (regime8 === 'DEFENSIVE_QUALITY_MARKET')         score = Math.max(5,  score - 1);
      if (regime8 === 'RATE_PRESSURE_GROWTH_COMPRESSION') score = Math.max(4,  score - 2);

      return Math.max(1, Math.min(20, score));
    } catch (_) { return 10; }
  }

  // Macro regime string
  function _getMacroRegime() {
    try {
      const cached = JSON.parse(localStorage.getItem('wr_macro_regime') || 'null');
      return cached?.value || 'NEUTRAL';
    } catch (_) { return 'NEUTRAL'; }
  }

  // 8-regime string
  function _getRegime8() {
    try {
      const cached = JSON.parse(localStorage.getItem('wr_signal_regime') || 'null');
      return cached?.regime8 || '';
    } catch (_) { return ''; }
  }

  // Sector/Theme Score 0–20 — reads wr_sector_perf_cache
  // Keys: lowercase (ret1m/ret3m), values: percent (3.2 = +3.2%)
  function _scoreSectorTheme(ticker) {
    let score = 0;
    const sector = CAND_UNIVERSE.getSector(ticker);
    const theme = CAND_UNIVERSE.getPrimaryTheme(ticker);

    try {
      const sectorPerf = JSON.parse(localStorage.getItem('wr_sector_perf_cache') || 'null');
      if (sectorPerf && sector && sectorPerf[sector]) {
        const perf = sectorPerf[sector];
        // 1M return (percent: 3.2 = +3.2%)
        const ret1m = perf.ret1m ?? 0;
        if      (ret1m > 5)  score += 8;   // strong leader
        else if (ret1m > 2)  score += 6;   // improving
        else if (ret1m < -5) score += 2;   // breakdown
        else if (ret1m < -2) score += 3;   // lagging
        else                 score += 4;   // flat

        // 3M return (percent)
        const ret3m = perf.ret3m ?? 0;
        if      (ret3m > 10) score += 7;   // strong multi-month leader
        else if (ret3m > 4)  score += 5;   // solid
        else if (ret3m < -8) score += 1;   // sustained breakdown
        else if (ret3m < -3) score += 2;   // lagging
        else                 score += 3;   // flat
      } else {
        // No sector perf data — give neutral 8
        score += 8;
      }
    } catch (_) {
      score += 8;
    }

    // Strong theme bonus (regime-context: NARROW_THEME_LEADERSHIP boosts theme matching)
    const regime8 = (() => {
      try { return JSON.parse(localStorage.getItem('wr_signal_regime') || 'null')?.regime8 || ''; } catch (_) { return ''; }
    })();
    const strongThemes = ['AI/반도체', '사이버보안', '바이오/의약', 'AI/클라우드'];
    const isStrongTheme = strongThemes.some(t => theme.includes(t.replace('/의약', '')) || t.includes(theme));
    if (isStrongTheme) score += regime8 === 'NARROW_THEME_LEADERSHIP' ? 5 : 3;

    return Math.min(20, score);
  }

  // Technical Score 0–15 — from TIMING.getStateForTicker() result
  function _scoreTechnical(timingResult) {
    if (!timingResult) return 2;
    const state = timingResult.state || 'WATCH';
    const techScore = timingResult.techScore ?? timingResult.total ?? 0;

    const stateMap = {
      TRIGGER: 14,
      ADD: 8,        // new buyer perspective
      SETUP: 8,
      BASE_BUILDING: 5,
      WATCH: 3,
      EXTENDED: 2,
      AVOID: 0,
      REDUCE: 0,
      EXIT: 0,
      DATA_INSUFFICIENT: 2,
    };
    let score = stateMap[state] ?? 3;

    // Bonus from techScore: up to 3 extra pts
    const bonus = Math.round((techScore / 100) * 3);
    score += bonus;

    return Math.min(15, score);
  }

  function _compositeScore(f, m, s, t) {
    return f + m + s + t;
  }

  // Candidate type classification — regime-aware
  function _candidateType(f, m, s, t, dq, techState) {
    const composite = _compositeScore(f, m, s, t);
    const badStates = ['AVOID', 'EXTENDED', 'EXIT', 'REDUCE'];
    const regime8 = _getRegime8();

    // Skip conditions first
    if (f < 15) return 'Skip';
    if (dq < 50) return 'Skip';
    if (techState === 'AVOID') return 'Skip';
    // In MACRO_RISK_OFF: only allow Core if fundamentals are very strong
    if (regime8 === 'MACRO_RISK_OFF' && f < 30) return 'Skip';
    if (composite < 25) return 'Skip';

    // Core: all conditions met (regime-aware thresholds)
    const coreThreshold = regime8 === 'MACRO_RISK_OFF' ? { f: 35, m: 3, s: 10, t: 5 }
      : regime8 === 'DEFENSIVE_QUALITY_MARKET' ? { f: 33, m: 8, s: 10, t: 5 }
      : { f: 30, m: 11, s: 13, t: 5 };
    if (f >= coreThreshold.f && m >= coreThreshold.m && s >= coreThreshold.s
        && t >= coreThreshold.t && dq >= 70 && !badStates.includes(techState)) {
      return 'Core';
    }

    // Watchlist pathways
    const watchlistStates = ['SETUP', 'BASE_BUILDING', 'WATCH'];
    if (f >= 25 && m >= 8 && watchlistStates.includes(techState)) return 'Watchlist';
    if (f >= 30 && (dq < 70 || m < 11)) return 'Watchlist';
    if (f >= 25 && s >= 15 && techState === 'EXTENDED') return 'Watchlist';

    // Tactical: strong sector/theme + chart but weak fundamentals
    if (s >= 15 && t >= 8 && f >= 15 && f < 30) return 'Tactical';

    return 'Skip';
  }

  // ── Filter Function ────────────────────────────────────────────────────────
  function _passesFilters(ticker, scores, timingResult, heldTickers, macroRegime) {
    if (heldTickers.includes(ticker)) return false;
    if (scores.dataQuality < 50) return false;
    if (scores.fundamental < 15) return false;
    const techState = timingResult?.state || 'WATCH';
    if (techState === 'AVOID') return false;
    if (macroRegime === 'RISK_OFF' && CAND_UNIVERSE.isHighBeta(ticker)) return false;

    // Price below SMA200 and SMA200 falling
    if (timingResult?.sma200 > 0 && timingResult?.price > 0) {
      if (timingResult.price < timingResult.sma200 * 0.95) return false;
    }
    return true;
  }

  // ── Reason / Blocker Generation ────────────────────────────────────────────
  function _buildReasons(ticker, scores, timingResult, macroRegime) {
    const reasons = [];
    const blockers = [];

    if (scores.fundamental >= 30) reasons.push('펀더멘털 우량 — Tier 1/2 기업');
    else if (scores.fundamental >= 20) reasons.push('펀더멘털 양호 — Tier 2 수준');

    if (scores.macro >= 15) reasons.push('매크로 환경 우호적 — RISK ON');
    else if (scores.macro >= 10) reasons.push('매크로 중립 — NEUTRAL');
    else blockers.push('매크로 비우호적 — CAUTION/RISK OFF 환경');

    if (scores.sectorTheme >= 15) reasons.push(`섹터/테마 강세 — ${_esc(CAND_UNIVERSE.getPrimaryTheme(ticker))}`);
    else if (scores.sectorTheme >= 10) reasons.push(`섹터 양호 — ${_esc(CAND_UNIVERSE.getPrimaryTheme(ticker))}`);
    else blockers.push('섹터 모멘텀 약세');

    const techState = timingResult?.state || 'WATCH';
    if (['TRIGGER', 'SETUP'].includes(techState)) reasons.push(`차트 셋업 완성 — ${techState}`);
    else if (techState === 'BASE_BUILDING') reasons.push('베이스 형성 중 — 진입 준비 단계');
    else if (techState === 'EXTENDED') blockers.push('과열 구간 — 신규 매수 시점 아님');
    else if (['REDUCE', 'EXIT'].includes(techState)) blockers.push(`차트 약세 신호 — ${techState}`);

    if (CAND_UNIVERSE.isHighBeta(ticker)) {
      if (macroRegime === 'CAUTION' || macroRegime === 'RISK_OFF') {
        blockers.push('고베타 종목 + 주의 매크로 환경');
      }
    }

    return { reasons, blockers };
  }

  // ── Next Trigger Text ──────────────────────────────────────────────────────
  function _nextTrigger(techState) {
    const map = {
      TRIGGER: '현재 매수 트리거 발동 중 — 즉시 분할 진입 가능',
      SETUP: '돌파 트리거: 20일 고점 + 거래량 평균 150% 초과',
      BASE_BUILDING: '베이스 이탈 상향 돌파 + 거래량 폭발 시 진입',
      ADD: '눌림목 SMA50 지지 확인 후 추가',
      WATCH: 'RSI 45+ + SMA50 위 안착 후 재확인',
      EXTENDED: 'SMA50까지 눌린 후 반등 확인',
      BASE_BUILDING: '박스권 상향 돌파 + 거래량 증가',
    };
    return map[techState] || 'TRIGGER 신호 형성 후 진입 검토';
  }

  // ── Data Quality Score (simple estimate for candidates) ────────────────────
  function _estimateDQ(timingResult, candlesLen) {
    if (!timingResult || !candlesLen) return 40;
    let dq = 50;
    if (candlesLen >= 200) dq += 20;
    else if (candlesLen >= 60) dq += 10;
    if (timingResult.price > 0) dq += 10;
    if (timingResult.sma200 > 0) dq += 10;
    if (timingResult.state && timingResult.state !== 'DATA_INSUFFICIENT') dq += 10;
    return Math.min(100, dq);
  }

  // ── Main Init ──────────────────────────────────────────────────────────────
  // Dynamic universe summary (built once per init)
  let _universeSummary = null;

  async function init() {
    const panel = document.getElementById(CONTAINER);
    if (!panel) return;

    const heldTickers = STATE.getMyPositions().map(p => (p.ticker || '').toUpperCase()).filter(Boolean);

    // Build the dynamic universe (6-layer merge). Non-fatal if it fails.
    try {
      if (typeof UNIVERSE !== 'undefined') _universeSummary = await UNIVERSE.build();
    } catch (e) { console.warn('[CANDIDATES] universe build failed', e); _universeSummary = null; }
    [_fundamentalsByTicker, _marketLens] = await Promise.all([
      _loadStaticFundamentals(),
      _loadMarketLens(),
    ]);

    // Load cached scan
    let cachedScan = null;
    try { cachedScan = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); } catch (_) {}

    // Render the full page shell
    panel.innerHTML = `
      <div class="space-y-6">
        ${_renderHeader(cachedScan?.scanInfo || null)}
        ${_renderUniverseSummary(_universeSummary)}
        <div id="candidates-scan-status"></div>
        ${_renderFilters()}
        <div id="candidates-cards-area">
          ${cachedScan?.candidates?.length
            ? _renderCards(cachedScan.candidates, heldTickers)
            : _renderEmptyState()}
        </div>
        ${_renderWatchlistSection()}
      </div>`;

    // Wire scan controls
    document.getElementById('candidates-scan-btn')?.addEventListener('click', () => _scanNow());
    document.getElementById('candidates-scan-depth')?.addEventListener('change', (e) => {
      try { localStorage.setItem(SCAN_DEPTH_KEY, e.target.value); } catch (_) {}
    });

    // Wire filter changes
    ['filter-type-core', 'filter-type-watchlist', 'filter-type-tactical',
     'filter-min-fund', 'filter-hide-beta', 'filter-sort'].forEach(id => {
      document.getElementById(id)?.addEventListener('change', () => {
        const cached = _loadCache();
        if (cached?.candidates?.length) {
          document.getElementById('candidates-cards-area').innerHTML =
            _renderCards(cached.candidates, heldTickers);
        }
      });
    });
  }

  // ── Scan Now ───────────────────────────────────────────────────────────────
  async function _scanNow() {
    _fundamentalsByTicker = await _loadStaticFundamentals();
    _marketLens = await _loadMarketLens();

    const heldTickers = STATE.getMyPositions().map(p => (p.ticker || '').toUpperCase()).filter(Boolean);

    const scanPlan = _buildScanPlan(heldTickers);
    const { scanTickers, depthKey, universeSize } = scanPlan;
    const total = scanTickers.length;
    if (!total) {
      _toast('스캔할 신규 종목이 없습니다', 'amber');
      return;
    }

    const statusEl = document.getElementById('candidates-scan-status');
    const scanBtn  = document.getElementById('candidates-scan-btn');
    if (scanBtn) { scanBtn.disabled = true; scanBtn.textContent = '스캔 중...'; }

    const macroRegime = _getMacroRegime();
    const candidates = [];
    let scanned = 0;
    let excluded = 0;

    // SPY quote for REC_LOG benchmark (fetch once)
    let benchSpy = null;
    try { benchSpy = await API.quote('SPY'); } catch (_) {}

    // Batch processing. Each ticker now uses the canonical TIMING pipeline only;
    // this avoids the old duplicate quote + candle calls and lets us scan 200+
    // names at a tolerable pace under Finnhub's free rate limit.
    const BATCH = 3;
    for (let i = 0; i < scanTickers.length; i += BATCH) {
      const batch = scanTickers.slice(i, i + BATCH);

      await Promise.all(batch.map(async (ticker) => {
        scanned++;
        if (statusEl) {
          const meta = SCAN_DEPTHS[depthKey] || SCAN_DEPTHS.standard;
          statusEl.innerHTML = `<div class="text-slate-400 text-sm text-center py-2">
            ${_esc(meta.label)} 스캔 중... ${scanned}/${total} — ${_esc(ticker)}
            <span class="text-slate-600 text-xs ml-2">전체 ${universeSize}개 중 선별</span>
          </div>`;
        }

        try {
          // Get timing state
          const timingResult = await TIMING.getStateForTicker(ticker).catch(() => null);
          const candlesLen = timingResult?.candlesLen || 0;

          // Compute scores
          const fScore = _scoreFundamental(ticker, null);
          const mScore = _scoreMacro();
          const sScore = _scoreSectorTheme(ticker);
          const tScore = _scoreTechnical(timingResult);
          const dqScore = _estimateDQ(timingResult, candlesLen);

          const scores = {
            fundamental: fScore,
            macro: mScore,
            sectorTheme: sScore,
            technical: tScore,
            dataQuality: dqScore,
          };

          const techState = timingResult?.state || 'WATCH';
          const composite = _compositeScore(fScore, mScore, sScore, tScore);
          const cType = _candidateType(fScore, mScore, sScore, tScore, dqScore, techState);

          // Apply filters
          if (!_passesFilters(ticker, scores, timingResult, heldTickers, macroRegime)) {
            excluded++;
            return;
          }
          if (cType === 'Skip') {
            excluded++;
            return;
          }

          const { reasons, blockers } = _buildReasons(ticker, scores, timingResult, macroRegime);

          // Dynamic universe metadata (source buckets, theme/sector fallback)
          const uEntry = (typeof UNIVERSE !== 'undefined') ? UNIVERSE.get(ticker) : null;
          const themeLabel = CAND_UNIVERSE.getPrimaryTheme(ticker)
            || uEntry?.themeKo || (uEntry?.themes?.[0]) || '기타';
          const sectorLabel = CAND_UNIVERSE.getSector(ticker) || uEntry?.sectorEtf || '—';

          const candidate = {
            ticker,
            candidateType: cType,
            compositeScore: composite,
            scores,
            techState,
            price: timingResult?.price || 0,
            theme: themeLabel,
            sector: sectorLabel,
            sourceBuckets: uEntry?.sourceBuckets || [],
            canonicalSource: uEntry?.canonicalSource || 'new_candidate',
            discoveryScore: uEntry?.discoveryScore ?? null,
            reasons,
            blockers,
            nextTrigger: _nextTrigger(techState),
            scannedAt: Date.now(),
          };

          candidates.push(candidate);

          // Log to REC_LOG for Core and Watchlist
          if (['Core', 'Watchlist'].includes(cType) && typeof REC_LOG !== 'undefined') {
            try {
              REC_LOG.save(SIGNAL_RESULT.build({
                ticker,
                price: timingResult?.price || 0,
                state: techState,
                compositeScore: composite,
                fundamentalScore: fScore,
                macroScore: mScore,
                sectorThemeScore: sScore,
                technicalTimingScore: tScore,
                dataQualityScore: dqScore,
                source: candidate.canonicalSource || 'new_candidate',
                candidateType: cType,
                benchmarkAtSignal: { spy: benchSpy?.price || benchSpy?.c || 0 },
                reasons,
                blockers,
                action: cType === 'Core' ? '신규편입 Core 후보' : cType === 'Watchlist' ? 'Watchlist 후보' : '전술적 관찰',
              }));
            } catch (_) {}
          }

        } catch (_) {
          excluded++;
        }
      }));

      // Small delay between batches to respect rate limit
      if (i + BATCH < scanTickers.length) {
        await new Promise(r => setTimeout(r, 350));
      }
    }

    // Overlay Alpaca live price + today's change onto the cards (one batch call
    // via the server proxy; keys stay server-side). No-op if unavailable.
    if (typeof LIVE_PRICE !== 'undefined' && candidates.length) {
      try {
        const live = await LIVE_PRICE.snapshot(candidates.map(c => c.ticker));
        candidates.forEach(c => {
          const q = live[c.ticker];
          if (q && q.price > 0) { c.price = q.price; c.liveChangePct = q.changePct; }
        });
      } catch (_) {}
    }

    await _hydrateCandidateFundamentals(candidates);
    const finalCandidates = _diversifyCandidateResults(candidates);

    // Save to cache
    const scanInfo = {
      scannedAt: Date.now(),
      totalScanned: scanned,
      universeSize,
      depthKey,
      depthLabel: SCAN_DEPTHS[depthKey]?.label || depthKey,
      passed: finalCandidates.length,
      excluded,
    };
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ candidates: finalCandidates, scanInfo }));
    } catch (_) {}

    if (scanBtn) { scanBtn.disabled = false; scanBtn.textContent = '🔍 신규 후보 스캔'; }
    if (statusEl) statusEl.innerHTML = '';

    // Re-render header + cards
    const headerEl = document.querySelector(`#${CONTAINER} .candidates-header-area`);
    if (headerEl) headerEl.outerHTML = _renderHeader(scanInfo);
    document.getElementById('candidates-scan-btn')?.addEventListener('click', () => _scanNow());
    document.getElementById('candidates-scan-depth')?.addEventListener('change', (e) => {
      try { localStorage.setItem(SCAN_DEPTH_KEY, e.target.value); } catch (_) {}
    });

    const cardsArea = document.getElementById('candidates-cards-area');
    if (cardsArea) {
      cardsArea.innerHTML = finalCandidates.length
        ? _renderCards(finalCandidates, heldTickers)
        : _renderEmptyState();
    }

    // Re-render watchlist section
    const wlArea = document.getElementById('candidates-watchlist-area');
    if (wlArea) wlArea.outerHTML = _renderWatchlistSection();

    _toast(`스캔 완료 — ${scanned}/${universeSize}개 검사, ${finalCandidates.length}개 후보 발견`);
  }

  async function _hydrateCandidateFundamentals(candidates) {
    const missing = candidates
      .filter(c => !_fundamentalFromSnapshot(c.ticker))
      .sort((a, b) => b.compositeScore - a.compositeScore)
      .slice(0, 24);
    for (const c of missing) {
      try {
        const qs = c.price > 0 ? '&price=' + encodeURIComponent(c.price) : '';
        const res = await fetch('/api/fundamentals?ticker=' + encodeURIComponent(c.ticker) + qs,
                                { headers: { Accept: 'application/json' } });
        if (!res.ok) continue;
        const f = await res.json();
        if (!f?.available) continue;
        _fundamentalsByTicker[c.ticker] = f;
        const old = c.scores.fundamental;
        const next = _scoreFundamental(c.ticker, null);
        c.scores.fundamental = next;
        c.fundamentalSource = f.source || f.ratio_source || 'on-demand';
        c.fundamentalSnapshot = {
          roic: f.roic, fcfMargin: f.fcf_margin, revGrowth: f.revenue_growth_yoy,
          pe: f.pe_ttm || f.per_ttm, pfcf: f.pfcf_ttm, peg: f.peg_ttm,
          dividend_yield: f.dividend_yield,
          peer: f.peer_valuation?.sector?.pe || null,
        };
        c.compositeScore = _compositeScore(
          c.scores.fundamental, c.scores.macro, c.scores.sectorTheme, c.scores.technical);
        c.candidateType = _candidateType(
          c.scores.fundamental, c.scores.macro, c.scores.sectorTheme,
          c.scores.technical, c.scores.dataQuality, c.techState);
        if (next !== old) {
          c.reasons = _buildReasons(c.ticker, c.scores, { state: c.techState }, _getMacroRegime()).reasons;
        }
      } catch (_) {}
    }
  }

  function _diversifyCandidateResults(candidates) {
    const sorted = candidates.filter(c => c.candidateType !== 'Skip').sort((a, b) =>
      b.compositeScore - a.compositeScore || a.ticker.localeCompare(b.ticker));
    const selected = [];
    const sectorCount = {};
    const maxPerSector = 8;
    const add = c => {
      if (!c || selected.some(x => x.ticker === c.ticker)) return;
      const key = c.sector || 'UNKNOWN';
      if ((sectorCount[key] || 0) >= maxPerSector) return;
      selected.push(c);
      sectorCount[key] = (sectorCount[key] || 0) + 1;
    };
    sorted.forEach(add);
    sorted.forEach(c => {
      if (!selected.some(x => x.ticker === c.ticker)) selected.push(c);
    });
    return selected;
  }

  function _scanDepthKey() {
    try {
      const saved = localStorage.getItem(SCAN_DEPTH_KEY);
      if (saved && SCAN_DEPTHS[saved]) return saved;
    } catch (_) {}
    return 'standard';
  }

  function _scanLimit(depthKey, total) {
    const limit = SCAN_DEPTHS[depthKey]?.limit ?? SCAN_DEPTHS.standard.limit;
    return limit === Infinity ? total : Math.min(limit, total);
  }

  function _scanPriority(e) {
    const sources = new Set(e.sources || []);
    let score = e.discoveryScore || 0;
    if (sources.has('core_watchlist')) score += 16;
    if (sources.has('theme_basket')) score += 8;
    if (sources.has('sector_holdings')) score += 6;
    if (e.themeLeader) score += 8;
    if (e.isNasdaq100) score += 5;
    if ((e.daysActive || 0) >= 5) score += 6;
    if (e.modelType === 'etf') score -= 4;

    // ── Dynamic, market-driven terms — these ROTATE the scan list daily so we
    //    don't keep analyzing the same names regardless of the tape (§4-5 E/F).
    if (e.dynScore != null) score += e.dynScore * 0.6;  // daily discovery (momentum + quality)
    if (e.emergingTheme) score += 18;                   // catch new leadership early
    if (e.themeFavored)  score += 14;                   // regime-favored theme
    if (e.sectorFavored) score += 8;                    // regime-favored sector
    if (e.themeAvoided || e.fadingTheme) score -= 16;   // out-of-favor / fading theme
    if (e.sectorAvoided) score -= 10;
    return score;
  }

  function _sectorKey(e) {
    return e.sectorEtf || e.gicsSector || 'UNKNOWN';
  }

  function _diversifiedTopN(entries, cap) {
    const ranked = entries
      .map(e => ({ ...e, scanPriority: _scanPriority(e) }))
      .sort((a, b) => b.scanPriority - a.scanPriority || a.ticker.localeCompare(b.ticker));

    const selected = [];
    const seen = new Set();
    const add = (e) => {
      if (!e || seen.has(e.ticker) || selected.length >= cap) return;
      selected.push(e);
      seen.add(e.ticker);
    };

    // Keep the highest-conviction names first, then diversify the rest by sector.
    const seedTarget = Math.min(cap, Math.ceil(cap * 0.55));
    ranked.slice(0, seedTarget).forEach(add);

    const buckets = new Map();
    ranked.forEach(e => {
      if (seen.has(e.ticker)) return;
      const key = _sectorKey(e);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(e);
    });
    const sectorOrder = Array.from(buckets.keys())
      .sort((a, b) => (buckets.get(b)?.[0]?.scanPriority || 0) - (buckets.get(a)?.[0]?.scanPriority || 0));

    let progressed = true;
    while (selected.length < cap && progressed) {
      progressed = false;
      for (const key of sectorOrder) {
        const bucket = buckets.get(key);
        if (!bucket?.length) continue;
        add(bucket.shift());
        progressed = true;
        if (selected.length >= cap) break;
      }
    }

    ranked.forEach(add);
    return selected.slice(0, cap);
  }

  function _buildScanPlan(heldTickers) {
    const held = new Set(heldTickers);
    const depthKey = _scanDepthKey();

    if (typeof UNIVERSE !== 'undefined' && UNIVERSE.isBuilt) {
      const entries = UNIVERSE.getAll().filter(e => !held.has(e.ticker));
      const cap = _scanLimit(depthKey, entries.length);
      const selected = _diversifiedTopN(entries, cap);
      return {
        depthKey,
        universeSize: entries.length,
        scanTickers: selected.map(e => e.ticker),
      };
    }

    const fallback = CAND_UNIVERSE.getAllTickers().filter(t => !held.has(t));
    const cap = _scanLimit(depthKey, fallback.length);
    return {
      depthKey,
      universeSize: fallback.length,
      scanTickers: fallback.slice(0, cap),
    };
  }

  // ── Cache Load Helper ──────────────────────────────────────────────────────
  function _loadCache() {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); } catch (_) { return null; }
  }

  // ── UI: Header ─────────────────────────────────────────────────────────────
  function _renderHeader(scanInfo) {
    const depthKey = scanInfo?.depthKey || _scanDepthKey();
    const depthOptions = Object.entries(SCAN_DEPTHS).map(([key, meta]) => {
      const text = meta.limit === Infinity ? '전체 유니버스' : `${meta.label} · ${meta.limit}개`;
      return `<option value="${key}" ${key === depthKey ? 'selected' : ''}>${text}</option>`;
    }).join('');

    const infoHtml = scanInfo
      ? (() => {
          const mins = Math.round((Date.now() - scanInfo.scannedAt) / 60000);
          const timeLabel = mins < 60 ? `${mins}분 전` : `${Math.round(mins / 60)}시간 전`;
          const depth = scanInfo.depthLabel ? ` · ${_esc(scanInfo.depthLabel)} 스캔` : '';
          const universe = scanInfo.universeSize ? ` · 유니버스 ${scanInfo.universeSize}개 중` : '';
          return `<span class="text-xs text-slate-500">마지막 스캔: ${_esc(timeLabel)}${depth}${universe} ${scanInfo.totalScanned}개 검사 · 통과 ${scanInfo.passed}개 · 제외 ${scanInfo.excluded}개</span>`;
        })()
      : '<span class="text-xs text-slate-500">스캔 기록 없음</span>';

    // Regime playbook compact banner
    const regimePlaybookHtml = (typeof MARKET_REGIME !== 'undefined')
      ? MARKET_REGIME.renderPlaybook(null, true)
      : '';

    // Today's market context driving the dynamic ordering — makes it visible
    // that the candidate list rotates with regime/theme/trend, not a fixed list.
    const rc = (typeof UNIVERSE !== 'undefined' && UNIVERSE.getRegimeContext)
      ? UNIVERSE.getRegimeContext() : null;
    const _chips = (arr, cls) => (arr || []).slice(0, 4)
      .map(t => `<span class="px-1.5 py-0.5 rounded ${cls}">${_esc(t)}</span>`).join(' ');
    const regimeLine = rc ? `
      <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
        <span>오늘의 국면:</span>
        <span class="text-emerald-300 font-semibold">${_esc(rc.regime || '—')}</span>
        ${rc.favoredThemes?.length ? `<span class="text-slate-500">· 우선 테마</span> ${_chips(rc.favoredThemes, 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/40')}` : ''}
        ${rc.emergingThemes?.length ? `<span class="text-slate-500">· 🌱 부상</span> ${_chips(rc.emergingThemes, 'bg-amber-900/40 text-amber-300 border border-amber-700/40')}` : ''}
      </div>` : '';
    const lens = _marketLens;
    const lensLine = lens ? `
      <div class="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
        <div class="rounded-lg bg-slate-900/35 border border-slate-700/50 px-3 py-2">
          <span class="text-slate-500">시장 국면</span>
          <span class="ml-2 text-emerald-300 font-semibold">${_esc(lens.regime || '—')}</span>
        </div>
        <div class="rounded-lg bg-slate-900/35 border border-slate-700/50 px-3 py-2">
          <span class="text-slate-500">강한 테마</span>
          <span class="ml-2 text-amber-300">${_esc((lens.topThemes || []).slice(0, 2).map(x => x.name).join(' · ') || '—')}</span>
        </div>
        <div class="rounded-lg bg-slate-900/35 border border-slate-700/50 px-3 py-2">
          <span class="text-slate-500">강한 섹터</span>
          <span class="ml-2 text-sky-300">${_esc((lens.topSectors || []).slice(0, 3).map(x => x.name).join(' · ') || '—')}</span>
        </div>
      </div>` : '';

    return `
      <div class="candidates-header-area bg-slate-800/40 border border-slate-700/50 rounded-2xl p-6">
        <div class="flex flex-wrap items-start justify-between gap-4 mb-4">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">🔭 신규편입 후보</h2>
            <p class="text-sm text-slate-400 max-w-2xl">현재 보유하지 않은 종목 중, 펀더멘털·매크로·섹터/테마·차트 조건을 통과한 편입 검토 후보입니다.</p>
            ${regimeLine}
            ${lensLine}
            <div class="mt-2">${infoHtml}</div>
          </div>
          <div class="shrink-0 flex flex-wrap items-center gap-2 justify-end">
            <select id="candidates-scan-depth"
              class="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-xs text-slate-300 focus:outline-none"
              title="스캔 범위">
              ${depthOptions}
            </select>
            <button id="candidates-scan-btn"
              class="bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white font-semibold px-5 py-2.5 rounded-xl text-sm transition-colors">
              🔍 신규 후보 스캔
            </button>
            <div class="basis-full text-[11px] text-slate-500 text-right">
              표준 220개 권장 · 전체 스캔은 시간이 오래 걸릴 수 있음
            </div>
          </div>
        </div>
        ${regimePlaybookHtml}
      </div>`;
  }

  // ── UI: Dynamic Universe Summary (source breakdown) ─────────────────────────
  const SOURCE_LABELS = {
    core_watchlist: { ko: 'Core 워치리스트', color: 'emerald' },
    sp500:          { ko: 'S&P 500',        color: 'sky' },
    nasdaq100:      { ko: '나스닥 100',      color: 'indigo' },
    sector_holdings:{ ko: '섹터 상위보유',   color: 'violet' },
    theme_basket:   { ko: '테마 바스켓',     color: 'amber' },
    momentum_discovery: { ko: '모멘텀 발굴', color: 'rose' },
    event_news:     { ko: '이벤트/뉴스',     color: 'pink' },
  };

  function _renderUniverseSummary(summary) {
    if (!summary || !summary.count) {
      return `
        <div class="bg-slate-800/30 border border-slate-700/40 rounded-xl px-4 py-3 text-xs text-slate-500">
          Dynamic Universe 로드 안 됨 — data/*.json 확인 필요 (Static/Local 모드 모두 동작해야 함)
        </div>`;
    }
    const bs = summary.bySource || {};
    const order = ['core_watchlist', 'sp500', 'nasdaq100', 'sector_holdings', 'theme_basket', 'momentum_discovery', 'event_news'];
    const chips = order.filter(s => bs[s]).map(s => {
      const meta = SOURCE_LABELS[s] || { ko: s, color: 'slate' };
      return `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-${meta.color}-900/30 border border-${meta.color}-700/40 text-${meta.color}-300 text-xs">
                ${_esc(meta.ko)} <span class="font-mono font-bold">${bs[s]}</span>
              </span>`;
    }).join('');

    // Core-eligible count (days_active >= 5 in Local, else core membership)
    let coreEligible = 0;
    try { coreEligible = (typeof UNIVERSE !== 'undefined') ? UNIVERSE.getCoreEligible().length : 0; } catch (_) {}

    const modeBadge = (typeof DB !== 'undefined' && DB.modeBadge) ? DB.modeBadge() : '';

    return `
      <div class="bg-slate-800/40 border border-slate-700/50 rounded-2xl p-5">
        <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-bold text-white">🌐 Dynamic Universe</h3>
            <span class="text-2xl font-black text-emerald-400">${summary.count}</span>
            <span class="text-xs text-slate-500">종목 (6-layer 머지)</span>
          </div>
          <div class="flex items-center gap-2">
            ${modeBadge}
            <span class="text-xs text-slate-500">Core 적격 <span class="text-emerald-400 font-semibold">${coreEligible}</span></span>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">${chips}</div>
        <div class="mt-2 text-[11px] text-slate-600">
          source 중복 포함 합계. 한 종목이 여러 레이어(예: S&P500 + 테마 + Core)에 동시 소속될 수 있음.
        </div>
      </div>`;
  }

  // ── UI: Filters ────────────────────────────────────────────────────────────
  function _renderFilters() {
    return `
      <div class="bg-slate-800/30 border border-slate-700/40 rounded-xl px-4 py-3 flex flex-wrap items-center gap-4">
        <div class="flex items-center gap-3 text-sm text-slate-300">
          <span class="text-xs text-slate-500 font-semibold">유형</span>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" id="filter-type-core" checked class="accent-emerald-500">
            <span class="text-emerald-400 text-xs font-semibold">Core</span>
          </label>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" id="filter-type-watchlist" checked class="accent-blue-500">
            <span class="text-blue-400 text-xs font-semibold">Watchlist</span>
          </label>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" id="filter-type-tactical" checked class="accent-amber-500">
            <span class="text-amber-400 text-xs font-semibold">Tactical</span>
          </label>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs text-slate-500 font-semibold">펀더멘털 최소</span>
          <select id="filter-min-fund"
            class="bg-slate-700 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none">
            <option value="0">0+</option>
            <option value="25">25+</option>
            <option value="30" selected>30+</option>
            <option value="35">35+</option>
          </select>
        </div>
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" id="filter-hide-beta" class="accent-red-500">
          <span class="text-xs text-slate-400">고베타 숨기기</span>
        </label>
        <div class="flex items-center gap-2 ml-auto">
          <span class="text-xs text-slate-500 font-semibold">정렬</span>
          <select id="filter-sort"
            class="bg-slate-700 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none">
            <option value="composite">Composite Score ↓</option>
            <option value="fundamental">Fundamental ↓</option>
            <option value="sectorTheme">Sector/Theme ↓</option>
            <option value="technical">Technical ↓</option>
          </select>
        </div>
      </div>`;
  }

  // ── UI: Cards Section ──────────────────────────────────────────────────────
  function _renderCards(candidates, heldTickers) {
    // Read filters
    const showCore      = document.getElementById('filter-type-core')?.checked !== false;
    const showWatchlist = document.getElementById('filter-type-watchlist')?.checked !== false;
    const showTactical  = document.getElementById('filter-type-tactical')?.checked !== false;
    const minFund       = parseInt(document.getElementById('filter-min-fund')?.value || '0', 10);
    const hideBeta      = document.getElementById('filter-hide-beta')?.checked === true;
    const sortKey       = document.getElementById('filter-sort')?.value || 'composite';

    let filtered = candidates.filter(c => {
      if (c.candidateType === 'Core'      && !showCore)      return false;
      if (c.candidateType === 'Watchlist' && !showWatchlist) return false;
      if (c.candidateType === 'Tactical'  && !showTactical)  return false;
      if (c.scores.fundamental < minFund) return false;
      if (hideBeta && CAND_UNIVERSE.isHighBeta(c.ticker)) return false;
      return true;
    });

    // Sort
    const sortMap = {
      composite: c => -c.compositeScore,
      fundamental: c => -c.scores.fundamental,
      sectorTheme: c => -c.scores.sectorTheme,
      technical: c => -c.scores.technical,
    };
    filtered.sort((a, b) => (sortMap[sortKey]?.(a) ?? 0) - (sortMap[sortKey]?.(b) ?? 0));

    if (!filtered.length) return _renderEmptyState();

    const groups = [
      { type: 'Core',      items: filtered.filter(c => c.candidateType === 'Core'),      label: '⭐ Core 후보', sub: '펀더멘털·매크로·섹터·차트 모두 통과' },
      { type: 'Watchlist', items: filtered.filter(c => c.candidateType === 'Watchlist'), label: '📋 Watchlist 후보', sub: '조건 일부 미달 — 셋업 형성 대기' },
      { type: 'Tactical',  items: filtered.filter(c => c.candidateType === 'Tactical'),  label: '⚡ Tactical 후보', sub: '강한 섹터/차트 + 약한 펀더멘털' },
    ];

    return groups
      .filter(g => g.items.length > 0)
      .map(g => `
        <div class="mb-6">
          ${UI.sectionHeader(g.label, g.sub)}
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            ${g.items.map(c => _renderCard(c)).join('')}
          </div>
        </div>`)
      .join('');
  }

  // ── UI: Single Candidate Card ──────────────────────────────────────────────
  // Render compact source-bucket chips (sp500 / nasdaq100 / theme:* / sector:* / core)
  function _renderSourceChips(buckets) {
    if (!buckets || !buckets.length) return '';
    const labelFor = (b) => {
      if (b === 'core_watchlist') return { t: 'Core', c: 'emerald' };
      if (b === 'core_watchlist:user') return { t: 'Core(내)', c: 'emerald' };
      if (b === 'sp500') return { t: 'S&P500', c: 'sky' };
      if (b === 'nasdaq100') return { t: 'NDX100', c: 'indigo' };
      if (b.startsWith('sector:')) return { t: b.slice(7), c: 'violet' };
      if (b.startsWith('theme:')) return { t: b.slice(6).replace(/_/g, ' '), c: 'amber' };
      return { t: b, c: 'slate' };
    };
    const chips = buckets.slice(0, 6).map(b => {
      const { t, c } = labelFor(b);
      return `<span class="px-1.5 py-0.5 rounded bg-${c}-900/30 border border-${c}-700/40 text-${c}-300 text-[10px] whitespace-nowrap">${_esc(t)}</span>`;
    }).join('');
    const more = buckets.length > 6 ? `<span class="text-[10px] text-slate-500">+${buckets.length - 6}</span>` : '';
    return `<div class="flex flex-wrap gap-1 items-center">${chips}${more}</div>`;
  }

  function _renderCard(c) {
    const { ticker, candidateType, compositeScore, scores, techState, theme, sector, reasons, blockers, nextTrigger, price, sourceBuckets, discoveryScore } = c;
    const watchlist = _getWatchlist();
    const inWatchlist = watchlist.some(w => w.ticker === ticker);

    // Score color
    const scoreColor = compositeScore >= 70 ? 'text-emerald-400' : compositeScore >= 50 ? 'text-amber-400' : 'text-red-400';

    // Badge color
    const badgeClass = candidateType === 'Core'
      ? 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50'
      : candidateType === 'Watchlist'
      ? 'bg-blue-900/50 text-blue-300 border-blue-700/50'
      : 'bg-amber-900/50 text-amber-300 border-amber-700/50';

    // Score bars
    const _bar = (val, max, color) => {
      const pct = Math.min(100, (val / max) * 100).toFixed(0);
      return `<div class="flex-1 bg-slate-700/50 rounded-full h-1.5"><div class="h-1.5 rounded-full" style="width:${pct}%;background:${color}"></div></div>`;
    };

    // Suggested target and monthly buy
    const suggestedTarget = candidateType === 'Core' ? '3~5%' : candidateType === 'Watchlist' ? '1~3%' : '1~2%';
    const suggestedBuy = candidateType === 'Core'
      ? (['TRIGGER', 'SETUP'].includes(techState) ? '$300' : '$200')
      : candidateType === 'Watchlist' ? '$100' : '$50';

    const reasonsHtml = reasons.length
      ? reasons.map(r => `<div class="flex gap-1.5 text-xs"><span class="text-emerald-400 shrink-0">✓</span><span class="text-slate-300">${_esc(r)}</span></div>`).join('')
      : '<div class="text-xs text-slate-500">—</div>';

    const blockersHtml = blockers.length
      ? `<div class="mt-2">
           <div class="text-xs text-slate-500 font-semibold mb-1">차단 요인</div>
           ${blockers.map(b => `<div class="flex gap-1.5 text-xs"><span class="text-red-400 shrink-0">✗</span><span class="text-slate-300">${_esc(b)}</span></div>`).join('')}
         </div>`
      : '';

    const watchlistBtnClass = inWatchlist
      ? 'bg-amber-900/40 border border-amber-700/50 text-amber-300 hover:bg-amber-900/60'
      : 'bg-blue-900/40 border border-blue-700/50 text-blue-300 hover:bg-blue-900/60';
    const watchlistBtnText = inWatchlist ? '✅ Watchlist 제거' : '+ Watchlist 추가';

    const priceDisplay = price > 0 ? `$${price.toFixed(2)}` : '—';
    const changeDisplay = (c.liveChangePct != null)
      ? `<div class="text-[11px] font-mono ${c.liveChangePct >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${c.liveChangePct >= 0 ? '+' : ''}${c.liveChangePct.toFixed(2)}%</div>`
      : '';
    const fs = c.fundamentalSnapshot || _fundamentalFromSnapshot(ticker) || null;
    const fmtPct = v => v == null || !Number.isFinite(v) ? '—' : (v * 100).toFixed(1) + '%';
    const fmtX = v => v == null || !Number.isFinite(v) ? '—' : Number(v).toFixed(v > 10 ? 1 : 2) + 'x';
    const divYield = fs?.dividend_yield ?? fs?.dividendYield ?? null;
    const afterTaxDiv = (typeof TAX_EST !== 'undefined') ? TAX_EST.afterTaxDividendYield(divYield) : null;
    const peer = fs?.peer || fs?.peer_valuation?.sector?.pe || fs?.peer_valuation?.themes?.[0] || null;
    const peerTxt = peer
      ? `${peer.interpretation === 'cheaper_than_peers' ? '동종 대비 저렴' : peer.interpretation === 'expensive_vs_peers' ? '동종 대비 비쌈' : '동종 중앙값 근처'}`
      : '';

    return `
      <div class="bg-slate-800/50 border border-slate-700/50 rounded-xl p-4 flex flex-col gap-3">
        <!-- Header row -->
        <div class="flex items-start justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="font-mono font-bold text-white text-lg">${_esc(ticker)}</span>
            <span class="text-xs px-1.5 py-0.5 rounded border ${badgeClass} font-semibold">${_esc(candidateType)}</span>
          </div>
          <div class="text-right">
            <div class="text-xs text-slate-500">현재가</div>
            <div class="text-sm font-mono text-slate-200">${priceDisplay}</div>
            ${changeDisplay}
          </div>
        </div>

        <!-- Composite score -->
        <div class="flex items-center gap-3">
          <div class="text-center">
            <div class="text-xs text-slate-500">Composite</div>
            <div class="text-2xl font-black ${scoreColor}">${compositeScore}</div>
            <div class="text-xs text-slate-600">/100</div>
          </div>
          <div class="text-center">
            <div class="text-xs text-slate-500">DQ</div>
            <div class="text-lg font-bold ${scores.dataQuality >= 70 ? 'text-emerald-400' : scores.dataQuality >= 50 ? 'text-amber-400' : 'text-red-400'}">${scores.dataQuality}</div>
          </div>
          <!-- Score bars -->
          <div class="flex-1 space-y-1.5 text-xs">
            <div class="flex items-center gap-2">
              <span class="text-slate-500 w-20 shrink-0">펀더멘털</span>
              ${_bar(scores.fundamental, 45, '#10b981')}
              <span class="text-slate-400 w-10 text-right">${scores.fundamental}/45</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-slate-500 w-20 shrink-0">매크로</span>
              ${_bar(scores.macro, 20, '#3b82f6')}
              <span class="text-slate-400 w-10 text-right">${scores.macro}/20</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-slate-500 w-20 shrink-0">섹터/테마</span>
              ${_bar(scores.sectorTheme, 20, '#8b5cf6')}
              <span class="text-slate-400 w-10 text-right">${scores.sectorTheme}/20</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-slate-500 w-20 shrink-0">기술적</span>
              ${_bar(scores.technical, 15, '#f59e0b')}
              <span class="text-slate-400 w-10 text-right">${scores.technical}/15</span>
            </div>
          </div>
        </div>

        <!-- Source buckets (which universe layers this ticker came from) -->
        ${sourceBuckets && sourceBuckets.length ? `
        <div class="flex items-center gap-2">
          <span class="text-[10px] text-slate-500 shrink-0">출처</span>
          ${_renderSourceChips(sourceBuckets)}
          ${discoveryScore != null ? `<span class="text-[10px] text-slate-600 ml-auto">발굴점수 <span class="font-mono text-slate-400">${discoveryScore}</span></span>` : ''}
        </div>` : ''}

        <!-- Meta info -->
        <div class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <div><span class="text-slate-500">테마:</span> <span class="text-slate-300">${_esc(theme)}</span></div>
          <div><span class="text-slate-500">섹터:</span> <span class="text-slate-300">${_esc(sector)}</span></div>
          <div><span class="text-slate-500">기술 상태:</span> <span class="text-slate-300">${_esc(techState)}</span></div>
          <div><span class="text-slate-500">초기 목표:</span> <span class="text-emerald-400 font-semibold">${suggestedTarget}</span></div>
          <div><span class="text-slate-500">월 적립안:</span> <span class="text-blue-400 font-semibold">${suggestedBuy}</span></div>
        </div>

        ${fs ? `
        <div class="grid grid-cols-5 gap-2 rounded-lg bg-slate-900/35 border border-slate-700/50 px-3 py-2 text-[11px]">
          <div><span class="text-slate-500 block">ROIC</span><span class="text-slate-200 font-semibold">${fmtPct(fs.roic)}</span></div>
          <div><span class="text-slate-500 block">FCF마진</span><span class="text-slate-200 font-semibold">${fmtPct(fs.fcfMargin ?? fs.fcf_margin)}</span></div>
          <div><span class="text-slate-500 block">PER</span><span class="text-slate-200 font-semibold">${fmtX(fs.pe ?? fs.pe_ttm ?? fs.per_ttm)}</span></div>
          <div><span class="text-slate-500 block">PEG</span><span class="text-slate-200 font-semibold">${fmtX(fs.peg ?? fs.peg_ttm)}</span></div>
          <div><span class="text-slate-500 block">세후배당</span><span class="text-slate-200 font-semibold">${fmtPct(afterTaxDiv)}</span></div>
          ${peerTxt ? `<div class="col-span-5 text-slate-500">Peer: <span class="text-amber-300">${_esc(peerTxt)}</span></div>` : ''}
        </div>` : ''}

        <!-- Reasons / Blockers -->
        <div>
          <div class="text-xs text-slate-500 font-semibold mb-1">긍정 요인</div>
          ${reasonsHtml}
          ${blockersHtml}
        </div>

        <!-- Next trigger -->
        <div class="bg-slate-700/30 rounded-lg px-3 py-2 text-xs">
          <span class="text-slate-500 font-semibold">다음 트리거: </span>
          <span class="text-slate-300">${_esc(nextTrigger)}</span>
        </div>

        <!-- Action buttons -->
        <div class="flex flex-wrap gap-2 pt-1">
          <button
            onclick="CANDIDATES._watchlistToggle('${_esc(ticker)}')"
            class="text-xs px-3 py-1.5 rounded-lg transition-colors ${watchlistBtnClass}">
            ${watchlistBtnText}
          </button>
          <button
            onclick="CANDIDATES._saveToTarget('${_esc(ticker)}')"
            class="text-xs px-3 py-1.5 rounded-lg bg-slate-700/60 border border-slate-600/50 text-slate-300 hover:bg-slate-700 transition-colors">
            📌 목표비중 저장
          </button>
        </div>
      </div>`;
  }

  // ── UI: Empty State ────────────────────────────────────────────────────────
  function _renderEmptyState() {
    return `
      <div class="text-center py-16 bg-slate-800/30 border border-slate-700/40 rounded-xl">
        <div class="text-4xl mb-3">🔭</div>
        <div class="text-slate-300 font-semibold mb-2">현재 조건을 만족하는 신규편입 후보 없음</div>
        <div class="text-slate-500 text-sm space-y-1 max-w-sm mx-auto">
          <div>• 위 "신규 후보 스캔" 버튼을 눌러 최신 데이터로 스캔하세요</div>
          <div>• 필터 조건을 완화하면 더 많은 후보가 표시됩니다</div>
          <div>• 매크로 환경이 RISK OFF일 경우 고베타 종목이 제외됩니다</div>
        </div>
      </div>`;
  }

  // ── UI: Watchlist Section ──────────────────────────────────────────────────
  function _renderWatchlistSection() {
    const wl = _getWatchlist();
    const rows = wl.map(w => {
      const addedDate = new Date(w.addedAt).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' });
      const statusLabel = w.status === 'added_to_targets' ? '목표 저장됨' : '관찰 중';
      const statusClass = w.status === 'added_to_targets' ? 'text-emerald-400' : 'text-blue-400';
      const typeBadge = w.candidateType === 'Core'
        ? 'text-emerald-300' : w.candidateType === 'Watchlist' ? 'text-blue-300' : 'text-amber-300';
      return `
        <tr class="border-b border-slate-700/30 hover:bg-slate-700/10">
          <td class="py-2 pl-4 font-mono text-sm text-white">${_esc(w.ticker)}</td>
          <td class="py-2 text-xs ${typeBadge}">${_esc(w.candidateType)}</td>
          <td class="py-2 text-sm text-slate-300">${w.candidateScore ?? '—'}</td>
          <td class="py-2 text-xs text-slate-500">${addedDate}</td>
          <td class="py-2 text-xs ${statusClass}">${statusLabel}</td>
          <td class="py-2 text-xs text-slate-500 max-w-[100px] truncate">${_esc(w.notes || '')}</td>
          <td class="py-2 pr-4 text-right">
            <button onclick="CANDIDATES._watchlistRemove('${_esc(w.ticker)}')"
              class="text-xs text-slate-500 hover:text-red-400 transition-colors">제거</button>
          </td>
        </tr>`;
    }).join('');

    return `
      <div id="candidates-watchlist-area">
        ${UI.sectionHeader('📋 나의 Watchlist', '관찰 중인 신규편입 후보 목록')}
        <div class="bg-slate-800/50 border border-slate-700/50 rounded-xl overflow-hidden">
          ${wl.length ? `
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="border-b border-slate-700 bg-slate-800/60 text-xs text-slate-400">
                    <th class="text-left py-2.5 pl-4">종목</th>
                    <th class="text-left py-2.5">유형</th>
                    <th class="text-left py-2.5">점수</th>
                    <th class="text-left py-2.5">추가일</th>
                    <th class="text-left py-2.5">상태</th>
                    <th class="text-left py-2.5">메모</th>
                    <th class="py-2.5 pr-4"></th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>` :
            '<div class="text-center py-8 text-slate-500 text-sm">Watchlist가 비어 있습니다. 후보 카드에서 추가하세요.</div>'
          }
        </div>
      </div>`;
  }

  // ── Watchlist Actions ──────────────────────────────────────────────────────
  function _getWatchlist() {
    try { return JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]'); } catch (_) { return []; }
  }

  function _saveWatchlist(wl) {
    try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(wl)); } catch (_) {}
  }

  function _watchlistToggle(ticker) {
    const wl = _getWatchlist();
    const idx = wl.findIndex(w => w.ticker === ticker);
    if (idx >= 0) {
      _watchlistRemove(ticker);
    } else {
      // Find candidate data from cache
      const cached = _loadCache();
      const cand = cached?.candidates?.find(c => c.ticker === ticker);
      if (cand) {
        _watchlistAdd(ticker, cand);
      } else {
        _watchlistAdd(ticker, { ticker, candidateType: 'Watchlist', compositeScore: null, scores: {} });
      }
    }
  }

  function _watchlistAdd(ticker, candidateData) {
    const wl = _getWatchlist();
    if (wl.some(w => w.ticker === ticker)) {
      _toast(`${ticker}은 이미 Watchlist에 있습니다`, 'amber');
      return;
    }

    const entry = {
      ticker,
      name: '',
      addedAt: Date.now(),
      candidateType: candidateData.candidateType || 'Watchlist',
      fundamentalScore: candidateData.scores?.fundamental ?? null,
      macroScore: candidateData.scores?.macro ?? null,
      sectorThemeScore: candidateData.scores?.sectorTheme ?? null,
      technicalScore: candidateData.scores?.technical ?? null,
      dataQualityScore: candidateData.scores?.dataQuality ?? null,
      candidateScore: candidateData.compositeScore ?? null,
      suggestedInitialTarget: candidateData.candidateType === 'Core' ? '3~5%' : candidateData.candidateType === 'Watchlist' ? '1~3%' : '1~2%',
      reason: candidateData.reasons || [],
      blockers: candidateData.blockers || [],
      status: 'watching',
      notes: '',
    };

    wl.push(entry);
    _saveWatchlist(wl);
    _toast(`${ticker} Watchlist에 추가됨`);

    // Re-render watchlist section
    const wlArea = document.getElementById('candidates-watchlist-area');
    if (wlArea) wlArea.outerHTML = _renderWatchlistSection();

    // Update button in card
    _refreshCardButton(ticker, true);
  }

  function _watchlistRemove(ticker) {
    const wl = _getWatchlist().filter(w => w.ticker !== ticker);
    _saveWatchlist(wl);
    _toast(`${ticker} Watchlist에서 제거됨`, 'amber');

    const wlArea = document.getElementById('candidates-watchlist-area');
    if (wlArea) wlArea.outerHTML = _renderWatchlistSection();

    _refreshCardButton(ticker, false);
  }

  function _refreshCardButton(ticker, inWatchlist) {
    // Find and update the watchlist toggle button in the card
    document.querySelectorAll(`button[onclick*="_watchlistToggle('${ticker}')"]`).forEach(btn => {
      if (inWatchlist) {
        btn.textContent = '✅ Watchlist 제거';
        btn.className = btn.className
          .replace(/bg-blue-900\/40|border-blue-700\/50|text-blue-300|hover:bg-blue-900\/60/g, '')
          .trim() + ' bg-amber-900/40 border border-amber-700/50 text-amber-300 hover:bg-amber-900/60';
      } else {
        btn.textContent = '+ Watchlist 추가';
        btn.className = btn.className
          .replace(/bg-amber-900\/40|border-amber-700\/50|text-amber-300|hover:bg-amber-900\/60/g, '')
          .trim() + ' bg-blue-900/40 border border-blue-700/50 text-blue-300 hover:bg-blue-900/60';
      }
    });
  }

  // ── Save to Target Allocation ──────────────────────────────────────────────
  function _saveToTarget(ticker) {
    // Render inline prompt below the card's save button
    const btnEl = [...document.querySelectorAll(`button[onclick*="_saveToTarget('${ticker}')"]`)][0];
    if (!btnEl) return;

    // Remove existing prompt if open
    const existing = document.getElementById(`target-prompt-${ticker}`);
    if (existing) { existing.remove(); return; }

    const prompt = document.createElement('div');
    prompt.id = `target-prompt-${ticker}`;
    prompt.className = 'mt-2 flex items-center gap-2 bg-slate-700/60 border border-slate-600/60 rounded-lg px-3 py-2';
    prompt.innerHTML = `
      <span class="text-xs text-slate-400">${_esc(ticker)} 목표 비중:</span>
      <input type="number" id="target-pct-${_esc(ticker)}" value="3" min="0.5" max="20" step="0.5"
        class="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-0.5 text-sm text-white focus:outline-none focus:border-emerald-500">
      <span class="text-xs text-slate-500">%</span>
      <button onclick="CANDIDATES._commitTarget('${_esc(ticker)}')"
        class="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-2.5 py-1 rounded transition-colors">확인</button>
      <button onclick="document.getElementById('target-prompt-${_esc(ticker)}')?.remove()"
        class="text-xs text-slate-500 hover:text-white">✕</button>`;

    btnEl.parentElement.appendChild(prompt);
    document.getElementById(`target-pct-${ticker}`)?.focus();
  }

  function _commitTarget(ticker) {
    const val = parseFloat(document.getElementById(`target-pct-${ticker}`)?.value);
    if (isNaN(val) || val <= 0) {
      _toast('유효한 비중을 입력하세요', 'red');
      return;
    }

    // Read and update wr_target_allocations
    let targets = {};
    try { targets = JSON.parse(localStorage.getItem('wr_target_allocations') || '{}'); } catch (_) {}
    targets[ticker] = val;

    const total = Object.values(targets).reduce((s, v) => s + (Number(v) || 0), 0);
    try { localStorage.setItem('wr_target_allocations', JSON.stringify(targets)); } catch (_) {}

    if (total > 100) {
      _toast(`목표비중 저장됨. 합계: ${total.toFixed(1)}% — 100% 초과 주의!`, 'amber');
    } else {
      _toast(`목표비중 저장됨. 합계: ${total.toFixed(1)}%`);
    }

    // Update watchlist entry status
    const wl = _getWatchlist();
    const idx = wl.findIndex(w => w.ticker === ticker);
    if (idx >= 0) {
      wl[idx].status = 'added_to_targets';
      _saveWatchlist(wl);
    }

    document.getElementById(`target-prompt-${ticker}`)?.remove();

    // Refresh watchlist section
    const wlArea = document.getElementById('candidates-watchlist-area');
    if (wlArea) wlArea.outerHTML = _renderWatchlistSection();
  }

  // ── Expose public API ──────────────────────────────────────────────────────
  window.CANDIDATES = {
    init,
    _watchlistAdd,
    _watchlistRemove,
    _watchlistToggle,
    _saveToTarget,
    _commitTarget,
    _scanNow,
  };

  return { init, _watchlistAdd, _watchlistRemove, _watchlistToggle, _saveToTarget, _commitTarget, _scanNow };
})();
