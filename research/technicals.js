// research/technicals.js — Korean HTS technical analysis orchestrator
// Depends: indicators.js, chart-engine.js, pattern-engine.js, signal-engine.js, technical-ui.js

const TECHNICALS = (() => {
  const CONTAINER = 'tab-technicals';

  let _ticker    = null;
  let _range     = '6M';
  let _preset    = 'U';   // US Growth default
  let _resOverride = null;  // null = auto from RANGE_RES, 'D'/'W'/'M' = user override
  let _loading   = false;
  let _candles   = null;   // [{t,o,h,l,c,v}]
  let _ind       = null;   // INDICATORS.computeAll result
  let _patterns  = null;   // PATTERNS.computeAll result
  let _state     = null;   // SIGNALS.classifyState result
  let _score     = 0;
  let _scoreComp = null;
  let _setup     = null;
  let _explanation = '';
  let _regime    = 'neutral';

  const RANGE_DAYS = {
    '1M':30, '3M':90, '6M':180, '1Y':365, '2Y':730, '5Y':1825,
  };
  const RANGE_RES = {
    '1M':'D', '3M':'D', '6M':'D', '1Y':'D', '2Y':'W', '5Y':'M',
  };

  // ── Public entry point ────────────────────────────────────────────────────

  async function init(ticker) {
    _ticker      = ticker;
    _loading     = false;   // reset on new ticker
    _resOverride = null;    // reset resolution override on new ticker
    const el = document.getElementById(CONTAINER);

    if (!ticker) {
      el.innerHTML = `<div class="flex items-center justify-center py-24 text-slate-500 text-sm">종목을 먼저 검색하세요</div>`;
      return;
    }

    // Read stored API keys from localStorage
    const saved = JSON.parse(localStorage.getItem('wr_api_keys') || '{}');
    if (saved.finnhub) {
      try { RESEARCH_CONFIG.FINNHUB_KEY = saved.finnhub; } catch(_) {}
    }

    if (!RESEARCH_CONFIG.FINNHUB_KEY) {
      UI.noKey(CONTAINER, 'Finnhub', 'FINNHUB_KEY');
      return;
    }

    await _loadAndRender();
  }

  // ── Load + full pipeline ──────────────────────────────────────────────────

  async function _loadAndRender() {
    if (_loading) return;
    _loading = true;
    UI.loading(CONTAINER, _ticker + ' 기술분석 데이터 로딩 중...');
    try {
      const days = RANGE_DAYS[_range] || 180;
      const resolution = _resOverride || RANGE_RES[_range] || 'D';
      const now  = Math.floor(Date.now() / 1000);
      // Fetch extra 220 bars for indicator warmup (SMA200 needs 200 bars)
      const bufferDays = resolution === 'D' ? 220 : resolution === 'W' ? 220 * 7 : 220 * 30;
      const from = now - (days + bufferDays) * 86400;

      const raw = await API.candles(_ticker, from, now, resolution);
      if (!raw || !raw.c?.length || raw.c.length < 5) {
        UI.error(CONTAINER, '차트 데이터가 부족합니다. Finnhub 무료 플랜 제한일 수 있습니다.');
        return;
      }

      // Build full OHLCV array (includes warmup buffer)
      const allCandles = raw.t.map((t, i) => ({
        t, o: raw.o[i], h: raw.h[i], l: raw.l[i], c: raw.c[i], v: raw.v[i],
      }));

      // Compute indicators on full dataset (so SMA200 is valid from first display candle)
      const allInd = INDICATORS.computeAll(allCandles);

      // Trim to display range: keep only last `days` calendar days
      const displayFrom = now - days * 86400;
      const trimIdx = allCandles.findIndex(c => c.t >= displayFrom);
      _candles = trimIdx > 0 ? allCandles.slice(trimIdx) : allCandles;

      // Trim indicator arrays to match display candles
      // Handles both flat arrays AND nested objects ({K,D}, {adx,plusDI,...}, etc.)
      const offset = allCandles.length - _candles.length;
      _ind = {};
      for (const key of Object.keys(allInd)) {
        const val = allInd[key];
        if (Array.isArray(val)) {
          _ind[key] = val.slice(offset);
        } else if (val !== null && typeof val === 'object') {
          const sliced = {};
          for (const subkey of Object.keys(val)) {
            const sv = val[subkey];
            sliced[subkey] = Array.isArray(sv) ? sv.slice(offset) : sv;
          }
          _ind[key] = sliced;
        } else {
          _ind[key] = val;
        }
      }

      // Detect macro regime from localStorage / global state
      _regime = _getRegime();

      // Detect patterns
      _patterns = PATTERNS.computeAll(_candles, _ind, _regime);

      // Compute score and classify state
      const hasPosition = _checkHolding(_ticker);
      _score     = SIGNALS.computeScore(_candles, _ind, _patterns, _regime);
      _scoreComp = null;  // component breakdown not yet exposed by signal-engine
      _state     = SIGNALS.classifyState(_score, _candles, _ind, _patterns, hasPosition, _ticker);
      _setup     = SIGNALS.computeTradeSetup(_state, _candles, _ind, _patterns);
      _explanation = SIGNALS.generateExplanation(_state, _score, _candles, _ind, _patterns, _regime);

      _renderAll();
    } catch (e) {
      console.error('Technicals error:', e);
      UI.error(CONTAINER, '기술분석 실패: ' + e.message);
    } finally {
      _loading = false;
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  function _renderAll() {
    const el = document.getElementById(CONTAINER);
    const stateMeta = SIGNALS.STATE_META[_state] || {};

    el.innerHTML = `
      <!-- Controls row -->
      <div id="tech-controls" class="mb-3"></div>

      <!-- MA legend -->
      <div id="ma-legend" class="mb-2 px-1"></div>

      <!-- Chart — full width -->
      <div id="lwc-chart" class="mb-3" style="min-height:1053px;border-radius:0.75rem;overflow:hidden;border:1px solid #1e293b;"></div>

      <!-- Signal cards row below chart -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
        <div id="tech-signal-card"></div>
        <div id="tech-sr-card"></div>
        <div id="tech-volume-card"></div>
      </div>

      <!-- Indicator summary -->
      <div id="tech-indicator-panel"></div>`;

    // Render controls
    TECH_UI.renderControls('tech-controls', {
      preset: _preset,
      range: _range,
      resolution: _resOverride || RANGE_RES[_range] || 'D',
      onPreset:      async (p) => { _preset = p; _applyPreset(); },
      onRange:       async (r) => { _range  = r; await _loadAndRender(); },
      onResolution:  async (r) => { _resOverride = r; await _loadAndRender(); },
    });

    // MA legend
    TECH_UI.renderMALegend('ma-legend', _preset);

    // Init TradingView chart
    _renderChart();

    // Signal card
    TECH_UI.renderSignalCard('tech-signal-card', {
      state:     _state,
      stateMeta,
      setup:     _setup,
      score:     _score,
      explanation: _explanation,
      ticker:    _ticker,
    });

    // SR card
    const lastPrice = _candles[_candles.length - 1].c;
    TECH_UI.renderSRCard('tech-sr-card', {
      srLevels:    _patterns?.srLevels || [],
      fibData:     _patterns?.fibData || null,
      currentPrice: lastPrice,
    });

    // Volume card
    TECH_UI.renderVolumeCard('tech-volume-card', {
      volSignals: _patterns?.volumeSignals || {},
      volData:    _ind?.volData || null,
      candles:    _candles,
    });

    // Indicator panel
    TECH_UI.renderIndicatorPanel('tech-indicator-panel', {
      ind:            _ind,
      candles:        _candles,
      scoreComponents: _scoreComp,
    });
  }

  function _renderChart() {
    CHART_ENGINE.init('lwc-chart').then(() => {
      const cfg = TECH_UI.PRESETS[_preset] || TECH_UI.PRESETS.U;

      // Ichimoku cloud fills FIRST — so candles/MAs render on top (not hidden)
      if (_ind && cfg.ichimoku && _ind.ichiData) {
        CHART_ENGINE.setIchimoku(_candles, _ind.ichiData, { cloudOnly: !!cfg.ichimokuCloudOnly });
      }

      // Candles (always) — on top of cloud
      CHART_ENGINE.setCandles(_candles);

      // Volume (always)
      CHART_ENGINE.setVolume(_candles);

      // MA lines — preset-defined only (guard: _ind may be null on very short data)
      if (_ind) {
        cfg.mas.forEach(key => {
          const values = _ind[key];
          if (values) CHART_ENGINE.setMA(key, _candles, values);
        });
      }

      // S/R lines — max 2 for K/U, all for F
      if (cfg.sr && _patterns?.srLevels?.length) {
        const maxLevels = _preset === 'F' ? 8 : 2;
        CHART_ENGINE.setSR(_patterns.srLevels, maxLevels);
      }

      // Fibonacci — Full only
      if (cfg.fib && _patterns?.fibData) {
        CHART_ENGINE.setFib(_patterns.fibData, _candles);
      }

      // Stochastic panes (only when _ind is available)
      if (_ind) {
        if (_ind.stochShort) CHART_ENGINE.setStochastic(0, _candles, _ind.stochShort);
        if (_ind.stochMid)   CHART_ENGINE.setStochastic(1, _candles, _ind.stochMid);
        if (_ind.stochLong)  CHART_ENGINE.setStochastic(2, _candles, _ind.stochLong);
      }

      // Signal markers — Full only
      if (cfg.markers) {
        const markers = _buildMarkers();
        if (markers.length) CHART_ENGINE.setSignalMarkers(markers);
      }

      CHART_ENGINE.fitContent();
    }).catch(e => {
      console.error('Chart engine init failed:', e);
      document.getElementById('lwc-chart').innerHTML =
        `<div class="flex items-center justify-center h-64 text-slate-500 text-sm">차트 로딩 실패: ${e.message}</div>`;
    });
  }

  function _applyPreset() {
    // Re-apply MA lines without reloading candles
    TECH_UI.renderMALegend('ma-legend', _preset);
    // Full re-render since CHART_ENGINE doesn't support partial update easily
    _renderChart();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _buildMarkers() {
    if (!_candles?.length) return [];
    const markers = [];

    // Golden / Dead cross markers (scan entire visible range)
    if (_ind?.ma50 && _ind?.ma200) {
      const n = _candles.length;
      for (let i = 1; i < n; i++) {
        const cur50 = _ind.ma50[i], cur200 = _ind.ma200[i];
        const prv50 = _ind.ma50[i-1], prv200 = _ind.ma200[i-1];
        if (cur50 == null || cur200 == null || prv50 == null || prv200 == null) continue;
        if (prv50 <= prv200 && cur50 > cur200) {
          markers.push({ time: _candles[i].t, dir: 'buy', label: '골든크로스' });
        } else if (prv50 >= prv200 && cur50 < cur200) {
          markers.push({ time: _candles[i].t, dir: 'sell', label: '데드크로스' });
        }
      }
    }

    // Candle pattern markers (last 10)
    if (_patterns?.candlePatterns?.length) {
      const recent = _patterns.candlePatterns.slice(-10);
      recent.forEach(p => {
        if (!p || !p.index) return;
        const c = _candles[p.index];
        if (!c) return;
        markers.push({ time: c.t, dir: p.bullish ? 'buy' : 'sell', label: p.label || p.pattern });
      });
    }

    // State-based marker on last candle
    const last = _candles[_candles.length - 1];
    const meta = SIGNALS.STATE_META[_state];
    if (meta && last && ['TRIGGER', 'SETUP', 'ADD'].includes(_state)) {
      markers.push({ time: last.t, dir: 'buy', label: meta.label });
    } else if (meta && last && ['REDUCE', 'EXIT', 'AVOID'].includes(_state)) {
      markers.push({ time: last.t, dir: 'sell', label: meta.label });
    }
    return markers;
  }

  function _getRegime() {
    // Try to read macro regime from global state or localStorage
    try {
      const cached = localStorage.getItem('wr_macro_regime');
      if (cached) {
        const { value, ts } = JSON.parse(cached);
        if (Date.now() - ts < 6 * 3600 * 1000) return value;
      }
    } catch(_) {}
    return 'neutral';
  }

  function _checkHolding(ticker) {
    try {
      const raw = JSON.parse(localStorage.getItem('wd_holdings_cache') || 'null');
      const arr = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.holdings) ? raw.holdings : []);
      return arr.some(h => (h.ticker || '').toUpperCase() === (ticker || '').toUpperCase());
    } catch(_) { return false; }
  }

  return { init };
})();
