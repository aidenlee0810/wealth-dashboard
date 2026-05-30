// research/chart-engine.js — TradingView Lightweight Charts v4 multi-pane wrapper
// Panes: [0] price+MA+cloud  [1] volume  [2..4] short/mid/long stochastic

const CHART_ENGINE = (() => {
  const CDN = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';

  let _charts = [], _candleSeries = null, _container = null, _candles = [];
  let _tooltip = null;

  // Dark HTS theme (Korean-style dark)
  const THEME = {
    layout: { background: { color: '#0f172a' }, textColor: '#94a3b8' },
    grid:   { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
    crosshair: { mode: 1, vertLine: { labelBackgroundColor: '#475569' }, horzLine: { labelBackgroundColor: '#475569' } },
    rightPriceScale: { borderColor: '#1e293b' },
    timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
    handleScroll: true, handleScale: true,
  };

  // Stochastic style — HTS pink K / indigo D (consistent across all panes)
  const STOCH_K_COLOR   = '#f472b6';   // pink-400
  const STOCH_D_COLOR   = '#818cf8';   // indigo-400
  const STOCH_UP_FILL   = 'rgba(249,115,22,0.42)';  // orange fill: K above D
  const STOCH_DN_FILL   = 'rgba(96,165,250,0.42)';  // blue fill: K below D
  const CHART_BG        = '#0f172a';   // must match THEME background

  const MA_STYLE = {
    // Korean HTS Clean palette
    ma5:  ['#9b59b6', 1],   ma10: ['#8e44ad', 1],   ma20: ['#e6b800', 1.5],
    ma60: ['#27ae60', 1.5], ma120:['#c0392b', 2],    ma240:['#7f3b00', 1.5],
    // US Growth palette
    ma50: ['#27ae60', 1.5], ma100:['#e67e22', 1],    ma150:['#e74c3c', 1.5],
    ma200:['#c0392b', 2],
    ema8: ['#9b59b6', 1],   ema10:['#8e44ad', 1.5],  ema20:['#e6b800', 1.5],
    ema50:['#27ae60', 1.5],
  };

  // ── SDK loader ────────────────────────────────────────────────────────────

  async function _load() {
    if (window.LightweightCharts) return;
    return new Promise((res, rej) => {
      const s = Object.assign(document.createElement('script'), { src: CDN });
      s.onload = res; s.onerror = () => rej(new Error('LWC CDN failed'));
      document.head.appendChild(s);
    });
  }

  // ── Init multi-pane ───────────────────────────────────────────────────────

  async function init(containerId) {
    await _load();
    destroy();
    _container = document.getElementById(containerId);
    if (!_container) throw new Error('Container not found: ' + containerId);
    _container.innerHTML = '';
    _container.style.cssText = 'display:flex;flex-direction:column;background:#0f172a;border-radius:0.75rem;overflow:hidden;';

    const PANES = [
      { pct: '48%', min: '513px', time: false, label: '' },
      { pct: '8%',  min: '81px',  time: false, label: '' },
      { pct: '14%', min: '149px', time: false, label: '단기 Stoch 5,3,3' },
      { pct: '15%', min: '155px', time: false, label: '중기 Stoch 14,3,3' },
      { pct: '15%', min: '155px', time: true,  label: '장기 Stoch 50,10,10' },
    ];

    _charts = PANES.map(({ pct, min, time, label }) => {
      const el = document.createElement('div');
      el.style.cssText = `width:100%;height:${pct};min-height:${min};position:relative;flex-shrink:0;`;
      if (label) {
        const lbl = Object.assign(document.createElement('div'), { textContent: label });
        lbl.style.cssText = 'position:absolute;top:4px;left:8px;font-size:9px;color:#64748b;z-index:2;pointer-events:none;font-family:monospace;letter-spacing:0.02em;';
        el.appendChild(lbl);
      }
      _container.appendChild(el);
      return LightweightCharts.createChart(el, {
        ...THEME,
        width: _container.clientWidth || 800,
        height: parseInt(min),
        timeScale: { ...THEME.timeScale, visible: time },
      });
    });

    _syncPanes();
    window.addEventListener('resize', _resize);
    return _charts;
  }

  // ── Series setters ────────────────────────────────────────────────────────

  function setCandles(candles) {
    if (!_charts[0]) return null;
    _candles = candles;
    _candleSeries = _charts[0].addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    _candleSeries.setData(candles.map(c => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })));
    _charts.forEach(ch => ch.timeScale().fitContent());
    _initTooltip(candles);
    return _candleSeries;
  }

  function setVolume(candles) {
    if (!_charts[1]) return null;
    const s = _charts[1].addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' });
    _charts[1].priceScale('').applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });
    s.setData(candles.map(c => ({
      time: c.t, value: c.v,
      color: c.c >= c.o ? 'rgba(34,197,94,0.5)' : 'rgba(239,68,68,0.5)',
    })));
    return s;
  }

  function setMA(key, candles, values) {
    if (!_charts[0]) return null;
    const [color, width] = MA_STYLE[key] || ['#94a3b8', 1];
    const s = _charts[0].addLineSeries({
      color, lineWidth: width,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(_line(candles, values));
    return s;
  }

  function setIchimoku(candles, ichi, { cloudOnly = false } = {}) {
    if (!_charts[0] || !ichi) return;
    const { tenkan, kijun, senkouA, senkouB, chikou } = ichi;
    const n = candles.length;
    const allTimes = _futureTimes(candles, 26);

    // Tenkan / Kijun / Chikou — hidden in cloudOnly mode
    if (!cloudOnly) {
      _addLine(_charts[0], _line(candles, tenkan.slice(0,n)), '#d946ef', 1);
      _addLine(_charts[0], _line(candles, kijun.slice(0,n)),  '#3b82f6', 1.5);
      _addLine(_charts[0], _line(candles, chikou.slice(0,n)), '#6b7280', 1);
    }

    // Senkou A & B (projected forward)
    const spA = [], spB = [];
    for (let i = 0; i < Math.min(senkouA.length, allTimes.length); i++) {
      if (senkouA[i] != null) spA.push({ time: allTimes[i], value: senkouA[i] });
      if (senkouB[i] != null) spB.push({ time: allTimes[i], value: senkouB[i] });
    }

    // Cloud fill — only between Senkou A and Senkou B (masking technique)
    // Layer order: green fill from A downward → mask below B with background color → net result: only A~B band filled
    const BG = '#0f172a';  // chart background (opaque mask)
    const bullA = [], bullB = [], bearA = [], bearB = [];
    const bMap = new Map(spB.map(d => [d.time, d.value]));
    spA.forEach(a => {
      const bVal = bMap.get(a.time);
      if (bVal == null) return;
      if (a.value >= bVal) { bullA.push({ time: a.time, value: a.value }); bullB.push({ time: a.time, value: bVal }); }
      else                 { bearA.push({ time: a.time, value: a.value }); bearB.push({ time: a.time, value: bVal }); }
    });

    const _area = (color, lineColor, alpha1, alpha2) => ({
      topColor: `rgba(${color},${alpha1})`, bottomColor: `rgba(${color},${alpha2})`,
      lineColor: lineColor, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });

    if (bullA.length) {
      // 1. Bullish green: fill from Senkou A downward
      _charts[0].addAreaSeries(_area('56,189,248', 'rgba(56,189,248,0.5)', 0.22, 0.06)).setData(bullA);
      // 2. Mask below Senkou B: overwrite with background → only A~B band remains
      _charts[0].addAreaSeries({ topColor: BG, bottomColor: BG, lineColor: 'transparent', priceLineVisible: false, lastValueVisible: false }).setData(bullB);
    }
    if (bearB.length) {
      // 1. Bearish red: fill from Senkou B (upper) downward
      _charts[0].addAreaSeries(_area('251,113,133', 'rgba(251,113,133,0.5)', 0.22, 0.06)).setData(bearB);
      // 2. Mask below Senkou A: overwrite with background → only A~B band remains
      _charts[0].addAreaSeries({ topColor: BG, bottomColor: BG, lineColor: 'transparent', priceLineVisible: false, lastValueVisible: false }).setData(bearA);
    }
    // Senkou B boundary line — hidden in cloudOnly mode
    if (!cloudOnly) {
      _addLine(_charts[0], spB, 'rgba(148,163,184,0.45)', 0.8);
    }
  }

  function setSR(levels, maxLevels = 2) {
    if (!_charts[0] || !levels?.length || !_candles.length) return;
    const t0 = _candles[0].t, t1 = _candles[_candles.length-1].t;
    // Show only top maxLevels by score (1 support + 1 resistance)
    const supports = levels.filter(l => l.type === 'support').slice(0, Math.ceil(maxLevels / 2));
    const resists  = levels.filter(l => l.type === 'resistance').slice(0, Math.floor(maxLevels / 2));
    [...supports, ...resists].forEach(lv => {
      const col = lv.type === 'support' ? '#16a34a' : '#dc2626';
      const s = _charts[0].addLineSeries({
        color: col + '60', lineWidth: 1, lineStyle: 2,
        priceLineVisible: false, lastValueVisible: false,
      });
      s.setData([{ time: t0, value: lv.price }, { time: t1, value: lv.price }]);
    });
  }

  function setFib(fibData, candles) {
    if (!_charts[0] || !fibData) return;
    const t0 = candles[0].t, t1 = candles[candles.length-1].t;
    const FIB_DEF = [
      ['fib236','#a78bfa','23.6%'], ['fib382','#818cf8','38.2%'],
      ['fib500','#60a5fa','50%'],   ['fib618','#34d399','61.8%'],
      ['fib786','#fbbf24','78.6%'],
    ];
    FIB_DEF.forEach(([key, color, label]) => {
      const price = fibData[key];
      if (!price) return;
      _charts[0].addLineSeries({ color: color+'88', lineWidth:1, lineStyle:3, priceLineVisible:false, lastValueVisible:true, title: label })
        .setData([{ time: t0, value: price }, { time: t1, value: price }]);
    });
  }

  function setStochastic(panelIdx, candles, stochData) {
    const chart = _charts[2 + panelIdx];
    if (!chart || !stochData) return;
    const { K, D } = stochData;
    const t0 = candles[0].t, t1 = candles[candles.length - 1].t;

    // ── Reference lines 80 / 20 ──────────────────────────────────────────────
    chart.addLineSeries({ color: 'rgba(239,68,68,0.50)', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, title: '과매수' })
      .setData([{ time: t0, value: 80 }, { time: t1, value: 80 }]);
    chart.addLineSeries({ color: 'rgba(56,189,248,0.50)', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, title: '과매도' })
      .setData([{ time: t0, value: 20 }, { time: t1, value: 20 }]);

    const kData = candles.map((c, i) => K[i] != null ? { time: c.t, value: K[i] } : null).filter(Boolean);
    const dData = candles.map((c, i) => D[i] != null ? { time: c.t, value: D[i] } : null).filter(Boolean);

    // ── Zone area fills — smooth fill above 80 (overbought) / below 20 (oversold) ──
    const _noLine = { topLineColor: 'rgba(0,0,0,0)', bottomLineColor: 'rgba(0,0,0,0)',
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    try {
      // Overbought: fill from K down to 80 baseline (orange)
      chart.addBaselineSeries({
        ..._noLine,
        baseValue: { type: 'price', price: 80 },
        topFillColor1: STOCH_UP_FILL, topFillColor2: STOCH_UP_FILL,
        bottomFillColor1: 'transparent', bottomFillColor2: 'transparent',
      }).setData(kData);
      // Oversold: fill from 20 baseline down to K (blue)
      chart.addBaselineSeries({
        ..._noLine,
        baseValue: { type: 'price', price: 20 },
        topFillColor1: 'transparent', topFillColor2: 'transparent',
        bottomFillColor1: STOCH_DN_FILL, bottomFillColor2: STOCH_DN_FILL,
      }).setData(kData);
    } catch (_) {}
    const kS = chart.addLineSeries({ color: STOCH_K_COLOR, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: true });
    const dS = chart.addLineSeries({ color: STOCH_D_COLOR, lineWidth: 1.5,
      priceLineVisible: false, lastValueVisible: false });
    kS.setData(kData);
    dS.setData(dData);
    chart.priceScale('right').applyOptions({
      autoScale: false, minValue: 0, maxValue: 100,
      scaleMargins: { top: 0.08, bottom: 0.08 },
    });

    // ── State overlay (K / D / zone label) ──────────────────────────────────
    const paneEl = _container?.children[2 + panelIdx];
    if (paneEl && kData.length) {
      const lastK = kData[kData.length - 1].value;
      const lastD = dData.length ? dData[dData.length - 1].value : null;
      const zone = lastK >= 80 ? { label: '과매수', color: '#ef4444' }
                 : lastK <= 20 ? { label: '과매도', color: '#38bdf8' }
                 : { label: '중립', color: '#64748b' };
      const el = document.createElement('div');
      el.style.cssText = 'position:absolute;top:4px;right:60px;font-size:9px;font-family:monospace;z-index:3;pointer-events:none;display:flex;gap:6px;align-items:center;';
      el.innerHTML = `
        <span style="color:${STOCH_K_COLOR}">K ${lastK.toFixed(0)}</span>
        ${lastD != null ? `<span style="color:${STOCH_D_COLOR}">D ${lastD.toFixed(0)}</span>` : ''}
        <span style="color:${zone.color};font-weight:bold;">${zone.label}</span>`;
      paneEl.appendChild(el);
    }

    return { kS, dS };
  }

  function setSignalMarkers(markers) {
    if (!_candleSeries || !markers?.length) return;
    _candleSeries.setMarkers(markers.map(m => ({
      time:     m.time,
      position: m.dir === 'buy' ? 'belowBar' : 'aboveBar',
      color:    m.dir === 'buy' ? '#22c55e' : m.dir === 'sell' ? '#ef4444' : '#f59e0b',
      shape:    m.dir === 'buy' ? 'arrowUp' : m.dir === 'sell' ? 'arrowDown' : 'circle',
      text:     m.label || '',
      size:     1,
    })));
  }

  function addPriceMarker(price, label, color) {
    if (!_charts[0]) return;
    const s = _charts[0].addLineSeries({ color, lineWidth:1.5, lineStyle:0, priceLineVisible:false, lastValueVisible:true, title: label });
    const t0 = _candles[0]?.t || 0, t1 = _candles[_candles.length-1]?.t || 0;
    s.setData([{ time: t0, value: price }, { time: t1, value: price }]);
  }

  function fitContent() {
    _charts.forEach(c => c.timeScale().fitContent());
    // After fitContent, clamp the right edge to last candle + 5 bars padding
    // (Ichimoku adds 26 future bars that would otherwise push data to the left)
    if (_candles.length > 1 && _charts[0]) {
      const iv = _candles[_candles.length-1].t - _candles[_candles.length-2].t;
      const rightT = _candles[_candles.length-1].t + 5 * iv;
      const firstT  = _candles[0].t;
      try {
        _charts.forEach(c => c.timeScale().setVisibleRange({ from: firstT, to: rightT }));
      } catch(_) {}
    }
  }

  function destroy() {
    _charts.forEach(c => { try { c.remove(); } catch(_){} });
    _charts = []; _candleSeries = null; _candles = [];
    _tooltip = null;
    window.removeEventListener('resize', _resize);
    if (_container) _container.innerHTML = '';
  }

  // ── OHLCV Tooltip ─────────────────────────────────────────────────────────

  function _initTooltip(candles) {
    if (!_charts[0] || !_container) return;

    // Remove existing tooltip if any
    if (_tooltip) { try { _tooltip.remove(); } catch(_){} _tooltip = null; }

    // Build tooltip element (floats inside first pane)
    const paneEl = _container.children[0];
    if (!paneEl) return;
    paneEl.style.position = 'relative';

    _tooltip = document.createElement('div');
    _tooltip.style.cssText = [
      'position:absolute', 'z-index:10', 'pointer-events:none',
      'background:rgba(15,23,42,0.92)', 'border:1px solid #334155',
      'border-radius:6px', 'padding:7px 10px', 'font-family:monospace',
      'font-size:11px', 'line-height:1.7', 'white-space:nowrap',
      'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'display:none',
      'min-width:160px',
    ].join(';');
    paneEl.appendChild(_tooltip);

    // Build a time→candle lookup for fast access
    const candleMap = new Map(candles.map((c, i) => [c.t, { ...c, _i: i }]));

    _charts[0].subscribeCrosshairMove(param => {
      if (!param || !param.time || !param.point) {
        _tooltip.style.display = 'none';
        return;
      }

      const c = candleMap.get(param.time);
      if (!c) { _tooltip.style.display = 'none'; return; }

      const prev = c._i > 0 ? candles[c._i - 1] : null;
      const pctChg = prev && prev.c > 0 ? ((c.c - prev.c) / prev.c) * 100 : null;
      const isUp = c.c >= c.o;
      const chgColor = pctChg === null ? '#94a3b8' : pctChg >= 0 ? '#22c55e' : '#ef4444';
      const chgStr = pctChg === null ? '' : (pctChg >= 0 ? '+' : '') + pctChg.toFixed(2) + '%';
      const chgAbs = pctChg !== null && prev ? (c.c - prev.c) : null;
      const chgAbsStr = chgAbs !== null
        ? (chgAbs >= 0 ? '+' : '') + chgAbs.toFixed(2)
        : '';

      // Format date
      const d = new Date(c.t * 1000);
      const dateStr = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;

      // Format volume
      const vol = c.v >= 1e9 ? (c.v/1e9).toFixed(2)+'B'
                : c.v >= 1e6 ? (c.v/1e6).toFixed(2)+'M'
                : c.v >= 1e3 ? (c.v/1e3).toFixed(1)+'K'
                : c.v?.toFixed(0) || '—';

      _tooltip.innerHTML = `
        <div style="color:#94a3b8;margin-bottom:2px;font-size:10px;">${dateStr}</div>
        <div style="display:grid;grid-template-columns:auto auto;gap:0 12px;">
          <span style="color:#64748b;">시가</span><span style="color:#e2e8f0;">${c.o.toFixed(2)}</span>
          <span style="color:#64748b;">고가</span><span style="color:#22c55e;font-weight:bold;">${c.h.toFixed(2)}</span>
          <span style="color:#64748b;">저가</span><span style="color:#ef4444;font-weight:bold;">${c.l.toFixed(2)}</span>
          <span style="color:#64748b;">종가</span><span style="color:${isUp ? '#22c55e' : '#ef4444'};font-weight:bold;">${c.c.toFixed(2)}</span>
          <span style="color:#64748b;">거래량</span><span style="color:#94a3b8;">${vol}</span>
          ${chgStr ? `<span style="color:#64748b;">등락</span><span style="color:${chgColor};font-weight:bold;">${chgAbsStr} (${chgStr})</span>` : ''}
        </div>`;

      // Position tooltip: follow cursor, stay inside pane
      const paneRect = paneEl.getBoundingClientRect();
      const x = param.point.x;
      const y = param.point.y;
      const ttW = 190, ttH = 140;
      const left = x + ttW + 16 > paneRect.width ? Math.max(0, x - ttW - 10) : x + 16;
      const top  = Math.max(4, Math.min(y - 20, paneRect.height - ttH - 4));

      _tooltip.style.left = left + 'px';
      _tooltip.style.top  = top  + 'px';
      _tooltip.style.display = 'block';
    });
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  function _line(candles, values) {
    return candles.map((c, i) => values[i] != null ? { time: c.t, value: values[i] } : null).filter(Boolean);
  }
  function _addLine(chart, data, color, width) {
    const s = chart.addLineSeries({ color, lineWidth: width, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false });
    s.setData(data); return s;
  }
  function _futureTimes(candles, n) {
    const times = candles.map(c => c.t);
    const iv = candles.length > 1 ? candles[candles.length-1].t - candles[candles.length-2].t : 86400;
    for (let i = 1; i <= n; i++) times.push(candles[candles.length-1].t + i * iv);
    return times;
  }
  function _syncPanes() {
    _charts.forEach((chart, ci) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (!range) return;
        _charts.forEach((other, oi) => {
          if (oi !== ci) try { other.timeScale().setVisibleLogicalRange(range); } catch(_){}
        });
      });
    });
  }
  function _resize() {
    if (!_container) return;
    _charts.forEach(ch => { try { ch.applyOptions({ width: _container.clientWidth }); } catch(_){} });
  }

  return {
    init, setCandles, setVolume, setMA, setIchimoku,
    setSR, setFib, setStochastic, setSignalMarkers,
    addPriceMarker, fitContent, destroy,
  };
})();
