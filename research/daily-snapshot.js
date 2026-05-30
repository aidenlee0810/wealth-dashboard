// research/daily-snapshot.js — Snapshot status card (Plan §11 UI 상태 카드)
// ============================================================================
// Renders the daily-snapshot health card at the top of the 매크로 tab:
//   - mode badge (🟢 DB live / 🟡 static / 🟠 stale / 🔴 fallback)
//   - snapshot date + age, result_status
//   - universe / candidate counts, API stats, DQ, active alerts
//   - Local Mode: "지금 스냅샷 실행" button → POST /api/snapshot
//
// Data source: DB.view('latest_snapshot_health') + DB.view('latest_alerts')
// (these resolve to data/views/*.json in every mode). Degrades gracefully to a
// "no snapshot yet" card when the views are still skeletons.
//
// Public API:
//   SNAPSHOT.render(containerId)  — (re)mount the card; safe to call repeatedly
//   SNAPSHOT.refresh()            — re-fetch views and re-render in place
// ============================================================================

const SNAPSHOT = (() => {
  const CARD_ID = 'snapshot-status-card';
  let _lastContainer = null;
  let _running = false;

  function _ageBucket(hours) {
    if (hours == null) return { cls: 'text-slate-400', label: '—' };
    if (hours <= 24) return { cls: 'text-emerald-400', label: `${hours}h ago` };
    if (hours <= 48) return { cls: 'text-amber-400', label: `${hours}h ago` };
    return { cls: 'text-orange-400', label: `${hours}h ago (stale)` };
  }

  const STATUS_PILL = {
    success:       ['정상', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'],
    degraded:      ['일부 실패', 'bg-amber-500/15 text-amber-300 border-amber-500/30'],
    partial:       ['부분 실패', 'bg-orange-500/15 text-orange-300 border-orange-500/30'],
    failed:        ['실패', 'bg-red-500/15 text-red-300 border-red-500/30'],
    market_closed: ['휴장', 'bg-slate-500/15 text-slate-300 border-slate-500/30'],
    no_snapshot_yet: ['스냅샷 없음', 'bg-slate-500/15 text-slate-300 border-slate-500/30'],
    unknown:       ['—', 'bg-slate-500/15 text-slate-300 border-slate-500/30'],
  };

  function _pill(status) {
    const [label, cls] = STATUS_PILL[status] || STATUS_PILL.unknown;
    return `<span class="px-2 py-0.5 rounded-full text-xs font-medium border ${cls}">${label}</span>`;
  }

  function _stat(label, value, accent = 'text-slate-100') {
    return `<div class="flex flex-col">
      <span class="text-[11px] uppercase tracking-wide text-slate-500">${label}</span>
      <span class="text-sm font-semibold ${accent}">${value}</span></div>`;
  }

  function _alertsBlock(alerts) {
    if (!alerts || !alerts.alerts || alerts.alerts.length === 0) {
      return `<div class="text-xs text-emerald-400/80 mt-2">✓ 활성 경고 없음</div>`;
    }
    const rows = alerts.alerts.slice(0, 5).map(a => {
      const icon = a.severity === 'critical' ? '🚨' : '⚠️';
      const color = a.severity === 'critical' ? 'text-red-300' : 'text-amber-300';
      return `<li class="${color}">${icon} ${a.message}</li>`;
    }).join('');
    return `<ul class="text-xs mt-2 space-y-0.5 list-none">${rows}</ul>`;
  }

  function _cardHTML(health, alerts) {
    const modeBadge = (typeof DB !== 'undefined' && DB.modeBadge) ? DB.modeBadge() : '';
    const status = health?.result_status || 'no_snapshot_yet';
    const age = _ageBucket(health?.snapshot_age_hours);
    const u = health?.universe || {};
    const c = health?.candidates || {};
    const api = health?.api || {};
    const dq = health?.dq || {};
    const sys = health?.system || {};
    const totalCalls = Object.values(api.calls_by_provider || {}).reduce((a, b) => a + b, 0);

    const isLocal = (typeof DB !== 'undefined' && DB.isLocal);
    const runBtn = isLocal
      ? `<button id="snapshot-run-btn" class="px-3 py-1.5 rounded-md text-xs font-medium
           bg-sky-600 hover:bg-sky-500 text-white transition disabled:opacity-50"
           ${_running ? 'disabled' : ''}>${_running ? '실행 중…' : '지금 스냅샷 실행'}</button>`
      : `<span class="text-[11px] text-slate-500">수동 실행은 Local Mode 필요</span>`;

    return `
    <div id="${CARD_ID}" class="rounded-xl border border-slate-700/60 bg-slate-800/40 p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-slate-200">📊 데이터 스냅샷</span>
          ${modeBadge}
          ${_pill(status)}
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs ${age.cls}">${health?.market_date || '—'} · ${age.label}</span>
          ${runBtn}
        </div>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        ${_stat('유니버스', `${u.total_active ?? 0}`, 'text-sky-300')}
        ${_stat('신규', `+${u.new_today ?? 0}`)}
        ${_stat('Core / Watch / Tact',
          `${c.core ?? 0} / ${c.watchlist ?? 0} / ${c.tactical ?? 0}`, 'text-emerald-300')}
        ${_stat('API 호출', `${totalCalls}`, totalCalls ? 'text-slate-100' : 'text-slate-500')}
        ${_stat('실패', `${api.failed_count ?? 0}`,
          (api.failed_count ?? 0) > 0 ? 'text-amber-300' : 'text-slate-100')}
        ${_stat('DQ<70', `${dq.below_70_count ?? 0}`,
          (dq.below_70_count ?? 0) > 0 ? 'text-amber-300' : 'text-slate-100')}
      </div>
      ${(sys.db_size_kb || sys.elapsed_seconds) ? `
      <div class="text-[11px] text-slate-500 mt-2">
        ${sys.elapsed_seconds ? `소요 ${sys.elapsed_seconds}s · ` : ''}
        ${sys.db_size_kb ? `DB ${(sys.db_size_kb / 1024).toFixed(1)}MB · ` : ''}
        ${sys.consecutive_success_count != null ? `연속 성공 ${sys.consecutive_success_count}일 · ` : ''}
        ${sys.archive_policy || ''}
      </div>` : ''}
      ${_alertsBlock(alerts)}
    </div>`;
  }

  function _attachRunHandler() {
    const btn = document.getElementById('snapshot-run-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (_running) return;
      _running = true;
      btn.disabled = true;
      btn.textContent = '실행 중…';
      try {
        await DB.snapshot();
        await refresh();
      } catch (e) {
        btn.textContent = '실패: ' + (e.message || 'error');
        setTimeout(() => { _running = false; refresh(); }, 4000);
        return;
      }
      _running = false;
    });
  }

  async function render(containerId) {
    if (containerId) _lastContainer = containerId;
    const container = document.getElementById(_lastContainer || 'tab-macro');
    if (!container) return;

    let health = null, alerts = null;
    try {
      if (typeof DB !== 'undefined' && DB.view) {
        [health, alerts] = await Promise.all([
          DB.view('latest_snapshot_health'),
          DB.view('latest_alerts'),
        ]);
      }
    } catch (e) { /* degrade to skeleton */ }

    const html = _cardHTML(health, alerts);
    const existing = document.getElementById(CARD_ID);
    if (existing) {
      existing.outerHTML = html;
    } else {
      container.insertAdjacentHTML('afterbegin', html);
    }
    _attachRunHandler();
  }

  async function refresh() { return render(); }

  return { render, refresh };
})();

if (typeof window !== 'undefined') window.SNAPSHOT = SNAPSHOT;
