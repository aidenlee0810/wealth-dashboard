// research/charts.js — Chart.js factory wrappers
// Matches the wealth dashboard's dark theme palette and Chart.js 4.4.0 usage

const CHARTS = (() => {
  const _instances = {}; // track all chart instances by id

  // ── Shared theme ──────────────────────────────────────────────────────────
  const PALETTE = ['#10b981','#3b82f6','#f59e0b','#8b5cf6','#ef4444',
                   '#06b6d4','#ec4899','#84cc16','#f97316','#a78bfa'];

  const GRID_COLOR  = 'rgba(51,65,85,0.6)';
  const TEXT_COLOR  = '#94a3b8';
  const FONT_FAMILY = "'Inter','system-ui',sans-serif";

  const BASE_OPTS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        borderWidth: 1,
        titleColor: '#e2e8f0',
        bodyColor: '#94a3b8',
        padding: 10,
      },
    },
    scales: {
      x: {
        grid: { color: GRID_COLOR },
        ticks: { color: TEXT_COLOR, font: { family: FONT_FAMILY, size: 11 }, maxTicksLimit: 8 },
      },
      y: {
        grid: { color: GRID_COLOR },
        ticks: { color: TEXT_COLOR, font: { family: FONT_FAMILY, size: 11 } },
      },
    },
  };

  // Destroy chart if it already exists (prevents canvas reuse error)
  function _destroy(id) {
    if (_instances[id]) {
      _instances[id].destroy();
      delete _instances[id];
    }
  }

  function _getCanvas(id) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    _destroy(id);
    return canvas;
  }

  // ── Line chart ────────────────────────────────────────────────────────────
  // datasets: [{ label, data: number[], color }]
  function line(id, labels, datasets, opts = {}) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: datasets.map((ds, i) => ({
          label:           ds.label || '',
          data:            ds.data,
          borderColor:     ds.color || PALETTE[i % PALETTE.length],
          backgroundColor: ds.fill ? (ds.color || PALETTE[i % PALETTE.length]).replace(')', ',0.1)').replace('rgb', 'rgba') : 'transparent',
          borderWidth:     ds.borderWidth || 2,
          pointRadius:     ds.pointRadius ?? (ds.data.length > 60 ? 0 : 3),
          pointHoverRadius: 5,
          tension:         0.3,
          fill:            ds.fill || false,
          yAxisID:         ds.yAxis || 'y',
        })),
      },
      options: {
        ...BASE_OPTS,
        plugins: {
          ...BASE_OPTS.plugins,
          legend: { display: datasets.length > 1, labels: { color: TEXT_COLOR, boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            ...BASE_OPTS.plugins.tooltip,
            callbacks: opts.tooltipCallbacks || {},
          },
        },
        scales: {
          ...BASE_OPTS.scales,
          ...(opts.scales || {}),
        },
        ...opts,
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Bar chart ─────────────────────────────────────────────────────────────
  function bar(id, labels, datasets, opts = {}) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: datasets.map((ds, i) => ({
          label:           ds.label || '',
          data:            ds.data,
          backgroundColor: ds.colors || ds.color || PALETTE[i % PALETTE.length],
          borderColor:     'transparent',
          borderRadius:    ds.borderRadius ?? 4,
          borderSkipped:   false,
        })),
      },
      options: {
        ...BASE_OPTS,
        plugins: {
          ...BASE_OPTS.plugins,
          legend: { display: datasets.length > 1, labels: { color: TEXT_COLOR, boxWidth: 12, font: { size: 11 } } },
        },
        scales: {
          ...BASE_OPTS.scales,
          ...(opts.scales || {}),
        },
        ...opts,
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Doughnut / Pie chart ──────────────────────────────────────────────────
  function doughnut(id, labels, values, opts = {}) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const chart = new Chart(canvas, {
      type: opts.pie ? 'pie' : 'doughnut',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: opts.colors || PALETTE,
          borderColor: '#0f172a',
          borderWidth: 2,
          hoverBorderWidth: 3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        cutout: opts.pie ? 0 : '65%',
        plugins: {
          legend: {
            display: true,
            position: opts.legendPosition || 'right',
            labels: { color: TEXT_COLOR, boxWidth: 12, font: { size: 11 }, padding: 10 },
          },
          tooltip: {
            ...BASE_OPTS.plugins.tooltip,
            callbacks: {
              label: ctx => ` ${ctx.label}: ${ctx.formattedValue}%`,
              ...opts.tooltipCallbacks,
            },
          },
        },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Candlestick chart (manual — no external plugin needed) ────────────────
  // Draws using a custom Chart.js bar dataset (high-low as bar, open-close as thin bar)
  function candlestick(id, candles, opts = {}) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const { t, o, h, l, c, v } = candles;
    const labels = t.map(ts => {
      const d = new Date(ts * 1000);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });
    // Color bars by up/down
    const colors = c.map((close, i) => close >= (o[i] || close) ? '#10b981' : '#ef4444');

    // High-Low range bars (thin, grey)
    const hlData = h.map((high, i) => ({ x: i, min: l[i], max: high }));

    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            // Open-Close candle body
            label: '가격',
            data: c.map((close, i) => ({
              x: labels[i],
              y: [Math.min(o[i], close), Math.max(o[i], close)],
            })),
            backgroundColor: colors,
            borderColor: colors,
            borderWidth: 1,
            borderRadius: 1,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 200 },
        parsing: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...BASE_OPTS.plugins.tooltip,
            callbacks: {
              label: ctx => {
                const i = ctx.dataIndex;
                return [`시가: $${o[i]?.toFixed(2)}`, `고가: $${h[i]?.toFixed(2)}`,
                        `저가: $${l[i]?.toFixed(2)}`, `종가: $${c[i]?.toFixed(2)}`];
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: GRID_COLOR },
            ticks: { color: TEXT_COLOR, font: { size: 10 }, maxTicksLimit: 8 },
          },
          y: {
            grid: { color: GRID_COLOR },
            ticks: { color: TEXT_COLOR, font: { size: 10 }, callback: v => '$' + v.toFixed(0) },
          },
        },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── RSI chart ─────────────────────────────────────────────────────────────
  function rsiChart(id, labels, rsiValues) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const colors = rsiValues.map(v => v > 70 ? '#ef4444' : v < 30 ? '#10b981' : '#94a3b8');
    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'RSI(14)',
          data: rsiValues,
          borderColor: '#8b5cf6',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 200 },
        plugins: {
          legend: { display: false },
          tooltip: { ...BASE_OPTS.plugins.tooltip },
          annotation: {
            annotations: {
              ob: { type: 'line', yMin: 70, yMax: 70, borderColor: '#ef444466', borderWidth: 1, borderDash: [4,4] },
              os: { type: 'line', yMin: 30, yMax: 30, borderColor: '#10b98166', borderWidth: 1, borderDash: [4,4] },
            },
          },
        },
        scales: {
          x: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 }, maxTicksLimit: 6 } },
          y: { min: 0, max: 100, grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 } } },
        },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── MACD chart ────────────────────────────────────────────────────────────
  function macdChart(id, labels, macdLine, signalLine, histogram) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const histColors = histogram.map(v => v >= 0 ? '#10b98180' : '#ef444480');
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { type: 'bar',  label: 'Histogram', data: histogram, backgroundColor: histColors, borderRadius: 2, borderSkipped: false },
          { type: 'line', label: 'MACD',      data: macdLine,  borderColor: '#3b82f6', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
          { type: 'line', label: 'Signal',    data: signalLine,borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
        ],
      },
      options: {
        ...BASE_OPTS,
        plugins: {
          ...BASE_OPTS.plugins,
          legend: { display: true, labels: { color: TEXT_COLOR, boxWidth: 10, font: { size: 10 } } },
        },
        scales: {
          x: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 }, maxTicksLimit: 6 } },
          y: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 } } },
        },
        animation: { duration: 200 },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Histogram (return distribution) ─────────────────────────────────────
  function histogram(id, bucketLabels, counts, meanLabel) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: bucketLabels,
        datasets: [{
          label: '빈도',
          data: counts,
          backgroundColor: PALETTE[1] + '99',
          borderColor: PALETTE[1],
          borderWidth: 1,
          borderRadius: 2,
          borderSkipped: false,
        }],
      },
      options: {
        ...BASE_OPTS,
        plugins: { ...BASE_OPTS.plugins },
        scales: {
          x: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 } } },
          y: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 10 } } },
        },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Yield curve bar ───────────────────────────────────────────────────────
  function yieldCurve(id, maturities, yields) {
    const canvas = _getCanvas(id);
    if (!canvas) return null;
    const colors = yields.map((v, i) => i === 0 ? PALETTE[2] : v < yields[0] ? '#ef4444' : PALETTE[0]);
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: maturities,
        datasets: [{
          label: '수익률(%)',
          data: yields,
          backgroundColor: colors,
          borderRadius: 4,
          borderSkipped: false,
        }],
      },
      options: {
        ...BASE_OPTS,
        plugins: {
          ...BASE_OPTS.plugins,
          tooltip: {
            ...BASE_OPTS.plugins.tooltip,
            callbacks: { label: ctx => ` ${ctx.parsed.y?.toFixed(2)}%` },
          },
        },
        scales: {
          x: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 11 } } },
          y: { grid: { color: GRID_COLOR }, ticks: { color: TEXT_COLOR, font: { size: 11 }, callback: v => v + '%' } },
        },
      },
    });
    _instances[id] = chart;
    return chart;
  }

  // ── Heatmap (sector rotation) — pure CSS, no Chart.js ────────────────────
  function sectorHeatmap(containerId, rows) {
    // rows: [{ label, values: { '1M': x, '3M': x, ... } }]
    const el = document.getElementById(containerId);
    if (!el) return;
    const periods = ['1M', '3M', '6M', 'YTD', '1Y'];
    const allVals = rows.flatMap(r => periods.map(p => r.values[p])).filter(v => v != null);
    const max = Math.max(...allVals.map(Math.abs)) || 1;

    const colorCell = (v) => {
      if (v == null) return 'bg-slate-700 text-slate-500';
      const intensity = Math.min(Math.abs(v) / max, 1);
      if (v > 0) {
        const g = Math.floor(50 + intensity * 150);
        return `background:rgba(16,${g + 55},${g},0.5);color:#${g > 150 ? '10b981' : 'a7f3d0'}`;
      } else {
        const r = Math.floor(100 + intensity * 130);
        return `background:rgba(${r + 30},30,30,0.5);color:#${r > 180 ? 'ef4444' : 'fca5a5'}`;
      }
    };

    el.innerHTML = `
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th class="text-left px-3 py-2 text-xs text-slate-400">섹터</th>
              ${periods.map(p => `<th class="px-3 py-2 text-xs text-slate-400 text-right">${p}</th>`).join('')}
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            ${rows.map(r => `
              <tr>
                <td class="px-3 py-2 text-slate-300 font-medium whitespace-nowrap">${r.label}</td>
                ${periods.map(p => {
                  const v = r.values[p];
                  const style = colorCell(v);
                  return `<td class="px-3 py-2 text-right rounded font-mono text-xs" style="${style}">
                    ${v != null ? (v > 0 ? '+' : '') + v.toFixed(1) + '%' : '—'}
                  </td>`;
                }).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function destroyAll() {
    Object.values(_instances).forEach(c => { try { c.destroy(); } catch(_) {} });
    Object.keys(_instances).forEach(k => delete _instances[k]);
  }

  function destroy(id) { _destroy(id); }

  return {
    line, bar, doughnut, candlestick, rsiChart, macdChart, histogram,
    yieldCurve, sectorHeatmap, destroy, destroyAll, PALETTE,
  };
})();
