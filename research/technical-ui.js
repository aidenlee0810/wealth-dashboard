// research/technical-ui.js — MA presets, signal cards, controls

const TECH_UI = (() => {

  // ── Presets ────────────────────────────────────────────────────────────────
  // U = US Growth (default)
  // F = Full Analysis

  const PRESETS = {
    U: {
      label: 'US Growth',
      mas: ['ema10', 'ema20', 'ma50', 'ma200'],
      ichimoku: true,
      ichimokuCloudOnly: true,   // cloud fill only — no Tenkan/Kijun/Chikou/boundary
      sr: false,
      fib: false,
      markers: false,
    },
    F: {
      label: 'Full',
      mas: ['ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240'],
      ichimoku: true,
      ichimokuCloudOnly: false,  // show all lines
      sr: true,
      fib: true,
      markers: true,
    },
  };

  // MA color scheme — Korean HTS style on light background
  const MA_COLORS = {
    ma5:   '#9b59b6', ma10:  '#8e44ad', ma20:  '#e6b800',
    ma50:  '#27ae60', ma60:  '#27ae60', ma100: '#e67e22',
    ma120: '#c0392b', ma150: '#e74c3c', ma200: '#c0392b',
    ma240: '#7f3b00', ema8:  '#9b59b6', ema10: '#8e44ad',
    ema20: '#e6b800', ema50: '#27ae60',
  };

  // ── Controls renderer ─────────────────────────────────────────────────────

  function renderControls(containerId, { preset = 'K', range = '6M', resolution = 'D', onPreset, onRange, onResolution } = {}) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const ranges = ['1M', '3M', '6M', '1Y', '2Y', '5Y'];
    const resolutions = [
      { key: 'D', label: '일봉' },
      { key: 'W', label: '주봉' },
      { key: 'M', label: '월봉' },
    ];

    el.innerHTML = `
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex gap-1" id="range-btns">
          ${ranges.map(r => `
            <button data-range="${r}"
              class="px-2.5 py-1 text-xs rounded font-mono transition-colors border
                ${r === range
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white'}">
              ${r}
            </button>`).join('')}
        </div>
        <div class="w-px h-4 bg-slate-600 mx-1"></div>
        <div class="flex gap-1" id="res-btns">
          ${resolutions.map(r => `
            <button data-res="${r.key}"
              class="px-2.5 py-1 text-xs rounded font-mono transition-colors border
                ${r.key === resolution
                  ? 'bg-violet-600 text-white border-violet-600'
                  : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white'}">
              ${r.label}
            </button>`).join('')}
        </div>
        <div class="w-px h-4 bg-slate-600 mx-1"></div>
        <div class="flex gap-1" id="preset-btns">
          ${Object.entries(PRESETS).map(([key, p]) => `
            <button data-preset="${key}"
              class="px-2.5 py-1 text-xs rounded font-mono transition-colors border
                ${key === preset
                  ? 'bg-slate-500 text-white border-slate-400'
                  : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white'}">
              ${p.label}
            </button>`).join('')}
        </div>
      </div>`;

    el.querySelectorAll('#range-btns button').forEach(btn => {
      btn.addEventListener('click', () => {
        el.querySelectorAll('#range-btns button').forEach(b => {
          b.className = b.className.replace('bg-blue-600 text-white border-blue-600', 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white');
        });
        btn.className = btn.className.replace('bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white', 'bg-blue-600 text-white border-blue-600');
        onRange && onRange(btn.dataset.range);
      });
    });

    el.querySelectorAll('#res-btns button').forEach(btn => {
      btn.addEventListener('click', () => {
        el.querySelectorAll('#res-btns button').forEach(b => {
          b.className = b.className.replace('bg-violet-600 text-white border-violet-600', 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white');
        });
        btn.className = btn.className.replace('bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white', 'bg-violet-600 text-white border-violet-600');
        onResolution && onResolution(btn.dataset.res);
      });
    });

    el.querySelectorAll('#preset-btns button').forEach(btn => {
      btn.addEventListener('click', () => {
        el.querySelectorAll('#preset-btns button').forEach(b => {
          b.className = b.className.replace('bg-slate-500 text-white border-slate-400', 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white');
        });
        btn.className = btn.className.replace('bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500 hover:text-white', 'bg-slate-500 text-white border-slate-400');
        onPreset && onPreset(btn.dataset.preset);
      });
    });
  }

  // ── MA Legend (compact one-line) ──────────────────────────────────────────

  function renderMALegend(containerId, preset = 'K') {
    const el = document.getElementById(containerId);
    if (!el) return;
    const mas = PRESETS[preset]?.mas || PRESETS.K.mas;
    const items = mas.map(key => `
      <span class="flex items-center gap-1">
        <span class="inline-block w-5 h-0.5 rounded" style="background:${MA_COLORS[key]}"></span>
        <span class="text-slate-500">${key.toUpperCase()}</span>
      </span>`).join('');

    const hasIchi = PRESETS[preset]?.ichimoku;
    const ichiItem = hasIchi
      ? `<span class="flex items-center gap-1">
           <span class="inline-block w-5 h-2 rounded opacity-50" style="background:rgba(56,189,248,0.5)"></span>
           <span class="text-slate-400">구름</span>
         </span>`
      : '';

    el.innerHTML = `<div class="flex flex-wrap items-center gap-3 text-xs font-mono">${items}${ichiItem}</div>`;
  }

  // ── Signal Card (compact, below chart) ───────────────────────────────────

  function renderSignalCard(containerId, { state, stateMeta, setup, score, explanation, ticker }) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const meta = stateMeta || { label: state, color: '#94a3b8', bg: 'bg-slate-100', action: '' };

    const scoreColor = score >= 70 ? '#16a34a' : score >= 40 ? '#d97706' : '#dc2626';
    const pct = Math.round(score);

    const levelRow = (label, price, color) => price != null ? `
      <div class="flex justify-between py-0.5">
        <span class="text-slate-400">${label}</span>
        <span class="font-mono font-semibold text-xs" style="color:${color}">$${Number(price).toFixed(2)}</span>
      </div>` : '';

    const rrVal = setup?.rr != null && isFinite(setup.rr) ? Number(setup.rr).toFixed(1) : null;

    el.innerHTML = `
      <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3">
        <div class="flex items-center justify-between gap-2 mb-2">
          <span class="px-2.5 py-1 rounded-lg text-sm font-bold ${meta.bg || 'bg-slate-700'}" style="color:${meta.color}">${meta.label}</span>
          <div class="flex items-center gap-2 flex-1">
            <div class="relative h-1.5 flex-1 bg-slate-700 rounded-full overflow-hidden">
              <div class="absolute inset-y-0 left-0 rounded-full" style="width:${pct}%;background:${scoreColor}"></div>
            </div>
            <span class="text-xs font-mono font-bold" style="color:${scoreColor}">${score}</span>
            ${rrVal ? `<span class="text-xs text-slate-500 font-mono">R:R ${rrVal}</span>` : ''}
          </div>
        </div>
        ${meta.action ? `<p class="text-xs text-slate-400 mb-2">${meta.action}</p>` : ''}
        ${setup ? `
        <div class="text-xs space-y-0.5 border-t border-slate-700/50 pt-2">
          ${levelRow('매수존', setup.buyLow, '#4ade80')}
          ${levelRow('트리거', setup.trigger, '#60a5fa')}
          ${levelRow('스톱', setup.stop, '#f87171')}
          ${levelRow('목표', setup.target1, '#fbbf24')}
        </div>` : ''}
        ${explanation ? `<p class="text-xs text-slate-500 mt-2 leading-relaxed border-t border-slate-700/50 pt-2">${explanation.split('\n')[0]}</p>` : ''}
      </div>`;
  }

  // ── SR Card (minimal) ─────────────────────────────────────────────────────

  function renderSRCard(containerId, { srLevels = [], fibData = null, currentPrice }) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const priceDist = (price) => {
      if (!currentPrice) return '';
      const pct = ((price - currentPrice) / currentPrice * 100);
      const col = pct >= 0 ? 'text-red-500' : 'text-green-600';
      return `<span class="${col} text-xs font-mono">${pct > 0 ? '+' : ''}${pct.toFixed(1)}%</span>`;
    };

    // Only show top 3 levels
    const srRows = srLevels.slice(0, 3).map(lv => {
      const isS = lv.type === 'support';
      return `
        <div class="flex items-center justify-between py-1 border-b border-slate-700/40 last:border-0 text-xs">
          <span class="${isS ? 'text-green-600' : 'text-red-500'}">${isS ? '지지' : '저항'}</span>
          ${priceDist(lv.price)}
          <span class="font-mono text-slate-200">$${Number(lv.price).toFixed(2)}</span>
        </div>`;
    }).join('');

    el.innerHTML = srRows ? `
      <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3">
        <p class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">지지 · 저항</p>
        ${srRows}
      </div>` : '';
  }

  // ── Volume Card (minimal) ─────────────────────────────────────────────────

  function renderVolumeCard(containerId, { volSignals, volData, candles }) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const n = candles?.length || 0;
    // volData uses rvol array (per-candle), volSignals uses scalar rvol + boolean flags
    const rvol = volSignals?.rvol ?? volData?.rvol?.[n - 1] ?? 1;
    const distDays = volSignals?.distCount ?? volData?.distCount25?.[n - 1] ?? 0;
    const signals = volSignals || {};

    const badge = signals.breakoutVol ? ['돌파 볼륨', '#16a34a']
      : signals.climaxVol            ? ['클라이맥스', '#d97706']
      : signals.dryUp                ? ['볼륨 소멸', '#2563eb']
      : signals.distribution         ? ['분배 신호', '#dc2626']
      : null;

    el.innerHTML = `
      <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3">
        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-400">RVOL</span>
          <span class="font-mono font-bold ${rvol >= 1.5 ? 'text-emerald-400' : rvol < 0.7 ? 'text-slate-500' : 'text-slate-300'}">${rvol.toFixed(1)}x</span>
          <span class="text-slate-500">매도일</span>
          <span class="font-mono font-bold ${distDays >= 4 ? 'text-red-400' : 'text-slate-300'}">${distDays}일</span>
          ${badge ? `<span class="px-2 py-0.5 rounded text-white text-xs font-semibold" style="background:${badge[1]}">${badge[0]}</span>` : ''}
        </div>
      </div>`;
  }

  // ── Indicator Summary Panel ───────────────────────────────────────────────

  function renderIndicatorPanel(containerId, { ind, candles, scoreComponents }) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const n = candles?.length;
    if (!n || !ind) { el.innerHTML = ''; return; }

    const last = i => Array.isArray(i) ? i[n - 1] : null;
    const rsi = last(ind.rsi14);
    const macd = ind.macdData ? {
      line: last(ind.macdData.macd),
      signal: last(ind.macdData.signal),
      hist: last(ind.macdData.histogram),
    } : null;
    const stochS = ind.stochShort ? { k: last(ind.stochShort.K), d: last(ind.stochShort.D) } : null;
    const stochM = ind.stochMid   ? { k: last(ind.stochMid.K),   d: last(ind.stochMid.D)   } : null;
    const stochL = ind.stochLong  ? { k: last(ind.stochLong.K),  d: last(ind.stochLong.D)  } : null;
    const adx = ind.adxData ? last(ind.adxData.adx) : null;

    const rsiColor = rsi == null ? 'text-slate-500' : rsi >= 70 ? 'text-red-400' : rsi <= 30 ? 'text-emerald-400' : 'text-slate-200';
    const adxLabel = adx == null ? '—' : adx >= 25 ? (adx >= 40 ? '강한추세' : '추세') : '횡보';

    const stochCell = (s, label) => s ? `
      <div class="text-center">
        <div class="text-xs text-slate-500 mb-0.5">${label}</div>
        <div class="text-sm font-mono font-bold ${s.k >= 80 ? 'text-red-400' : s.k <= 20 ? 'text-emerald-400' : 'text-slate-200'}">
          ${s.k?.toFixed(0) ?? '—'}<span class="text-slate-500 text-xs">/${s.d?.toFixed(0) ?? '—'}</span>
        </div>
      </div>` : '';

    el.innerHTML = `
      <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-3 mt-3">
        <div class="flex flex-wrap gap-4 items-center">
          <div>
            <div class="text-xs text-slate-400 mb-0.5">RSI(14)</div>
            <div class="text-sm font-mono font-bold ${rsiColor}">${rsi?.toFixed(1) ?? '—'}</div>
          </div>
          <div>
            <div class="text-xs text-slate-500 mb-0.5">ADX</div>
            <div class="text-sm font-mono font-bold ${adx >= 25 ? 'text-emerald-400' : 'text-slate-500'}">${adx?.toFixed(0) ?? '—'} <span class="text-xs font-normal text-slate-500">${adxLabel}</span></div>
          </div>
          ${macd ? `
          <div>
            <div class="text-xs text-slate-500 mb-0.5">MACD</div>
            <div class="text-sm font-mono font-bold ${macd.hist > 0 ? 'text-emerald-400' : 'text-red-400'}">${macd.hist?.toFixed(2) ?? '—'}</div>
          </div>` : ''}
          <div class="w-px h-8 bg-slate-700/50"></div>
          ${stochCell(stochS, '단기')}
          ${stochCell(stochM, '중기')}
          ${stochCell(stochL, '장기')}
        </div>
      </div>`;
  }

  return {
    PRESETS, MA_COLORS,
    renderControls, renderSignalCard, renderSRCard,
    renderVolumeCard, renderIndicatorPanel, renderMALegend,
  };
})();
