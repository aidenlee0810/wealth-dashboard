# Runbook — Wealth Dashboard Research Platform

This runbook covers operational failure scenarios. Each entry follows:
**Symptom → Diagnosis → Fix → Prevent**.

---

## 1. Daily Snapshot Failed (GitHub Actions)

### Symptom
- GHA workflow `Daily Snapshot` shows red ❌
- Email alert from GitHub Actions
- UI shows "Snapshot stale" or `latest_alerts.json` has `critical` entry

### Diagnosis
1. Open GHA logs: `https://github.com/USER/REPO/actions`
2. Identify failure stage from log output:
   - `stage1_discovery` — Yahoo/Finnhub API issue
   - `stage2_deep_*` — Fundamentals/Risk Governor
   - `build_views` — view JSON generation
   - `commit_push` — git conflict
   - `pre_commit_check` — personal data leak detected (rare, would be a code bug)

### Fix
- **API issue (rate limit, 5xx):**
  - Check provider status: status.finnhub.io, status.federalreserve.gov, etc.
  - Wait 1 hour, then trigger `workflow_dispatch` manually.
  - If consistent 4xx, an API key may have rotated — update GitHub Secret.
- **Git conflict:**
  - Manual rebase: `git pull --rebase origin main && git push`
  - Workflow has `concurrency: daily-snapshot` set, so this is rare.
- **Data corruption (DB unreadable):**
  - Restore from yesterday: `git checkout HEAD~1 data/db/research_market.sqlite && git commit -m "restore previous DB"`
  - Re-run snapshot.
- **Pre-commit check failure:**
  - **DO NOT bypass.** This indicates personal data leaked into a commit.
  - Inspect what was staged: `git diff --cached`
  - Remove personal files from staging, fix code that wrote them, retry.

### Prevent
- Provider circuit breaker active (plan §11)
- `concurrency` group in workflow prevents overlapping runs
- Pre-commit hook is bulletproof — never edit `.git/hooks/pre-commit` to weaken checks

---

## 2. Reconciliation Failure Spike

### Symptom
- `latest_alerts.json` shows: `reconciliation.failure_rate > 5%`
- Multiple `reconciliation_log` rows in last 24h
- UI macro tab shows ⚠️ warning

### Diagnosis
1. Open `data/views/latest_alerts.json`
2. Query: `python -c "import db; print(db.validate_and_query('reconciliation_log', limit=20))"`
3. Identify pattern:
   - **Single ticker repeated** → ticker-specific (split, halt, delisting candidate)
   - **Multiple tickers, same source** → source-wide issue (Finnhub or Yahoo down)
   - **All sources** → upstream data feed issue (unlikely)

### Fix
- **Single ticker:**
  - Check `corporate_actions` — recent split? If yes, adjust pipeline handled it but bound check too tight.
  - If delisting, set `ticker_master.active = 0` manually
- **Source-wide:**
  - Temporarily increase reconciliation diff tolerance for the affected source
  - File issue with provider if persistent
- **Upstream feed:**
  - Switch primary source (Finnhub → Yahoo or vice versa)
  - Document in this runbook

### Prevent
- Cross-source reconciliation always runs (§25)
- Multiple fallback providers configured

---

## 3. Repo Size Exceeded 500MB

### Symptom
- GHA workflow warning: `Repo size >500MB`
- `git clone` becomes slow

### Diagnosis
1. Check `latest_snapshot_health.json` for `db_size_kb` and `repo_size_kb`
2. Identify biggest contributors: `du -sh data/* | sort -h`

### Fix
- **Immediate:** Run `python jobs/monthly_archive.py` (if exists)
  - Moves 60-day-old snapshots to `data/archive/YYYY-MM/snapshot.sqlite.gz`
- **Phase 3 transition:** switch to one of:
  - Option A: SQLite via GHA artifact only (no commit)
  - Option B: monthly compressed release assets
  - Option C: data/archive/YYYY-MM/ rotation (recommended)

### Prevent
- Monthly `VACUUM` job
- Repo size monitor in every snapshot
- Archive policy documented in ADR

---

## 4. DQ Distribution Degraded

### Symptom
- `latest_alerts.json`: `dq.below_50_count > 30%`
- Many candidates blocked

### Diagnosis
1. Query: `python -c "import db; print(db.validate_and_query('features_daily', cols=['ticker','dq_score'], where={'dq_score__lt':50}, limit=30))"`
2. Common causes:
   - Stale fundamentals (no recent earnings)
   - Macro data delayed (FRED publishing delay)
   - Sector ETF data missing

### Fix
- **Stale fundamentals:** Wait until next quarterly report; for now, those tickers are correctly de-prioritized
- **Macro delay:** FRED usually catches up within 24h; check `macro_daily.usable_at` for latest
- **Code bug:** if DQ formula recently changed, run regression test (`tests/test_golden.py`)

### Prevent
- DQ formula version tracked in `feature_version`
- Golden set regression tests in CI

---

## 5. Contract Violation Detected

### Symptom
- `latest_alerts.json`: `contract_violation.count_24h > 5`
- `contract_violations` table has new rows

### Diagnosis
1. Query latest violations:
   ```sql
   SELECT * FROM contract_violations ORDER BY detected_at DESC LIMIT 10;
   ```
2. Identify source + endpoint affected
3. Inspect `actual_payload_hash` — compare against expected schema

### Fix
- **Provider changed API:** Update schema in `jobs/contracts/<provider>.py`
  - Bump `SCHEMA_VERSION` constant
  - Add new fields as Optional
  - Run unit tests
- **Single bad record:** Add to ignore list, file ticket with provider

### Prevent
- All API responses validated via pydantic models (§23)
- Schema version comparison runs on every fetch

---

## 6. Lookahead Bug in Backtest

### Symptom
- `backtest_runs.lookahead_check_passed = 0`
- CI test failure in `tests/test_backtest_no_lookahead.py`

### Diagnosis
- Open the failing backtest's `result_json`
- Identify which feature has `usable_at > decision_date`

### Fix
- **DO NOT ship the backtest result.** Mark `results_status='unavailable'`
- Trace the feature back through pipeline:
  - Raw → Layer 1: timestamp lost?
  - Layer 1 → Layer 2: normalization used future data?
  - Layer 2 → Layer 3: feature engineering used current values?
- Fix the violating step + add unit test

### Prevent
- `assert_no_lookahead()` called from backtest_runner.py before each run
- Time-series CV (never random split)
- Code reviews flag any `df.shift(-N)` patterns

---

## 7. Signal Lineage Replay Fails

### Symptom
- `replay_signal(signal_id)` returns `identical: false`
- Discrepancy between original and replayed signal

### Diagnosis
- Check what changed:
  - `code_commit_sha` — was code reverted?
  - `feature_version` — did formula change?
  - Underlying data — was a price reconciled differently?

### Fix
- **Code drift:** Use `with_current_code=True` to confirm new behavior intentional
- **Data drift:** Investigate — should never happen for archived `prices_daily` rows

### Prevent
- All signals carry full `lineage_json`
- Regression tests run replay on 10 signals every CI build

---

## 8. Personal Data Detected in Commit

### Symptom
- Pre-commit hook blocks: `❌ COMMIT BLOCKED: Personal data files detected`
- OR: CI test `test_2_git_status_no_personal_files` fails

### Diagnosis
- `git status` to see offending files
- These should NEVER be in the repo:
  - `personal.sqlite`
  - `user_actions*`
  - `portfolio_snapshots*` (CSV or JSON)
  - `~/.wealth-dashboard/` content
  - `data/personal/`

### Fix
1. **DO NOT bypass pre-commit hook** (no `--no-verify`)
2. Unstage: `git restore --staged <file>`
3. Move file to local-only location: `~/.wealth-dashboard/` or `data/db/personal.sqlite`
4. Verify `.gitignore` covers the pattern
5. Retry commit

### Prevent
- Pre-commit hook installed (`.git/hooks/pre-commit`)
- `.gitignore` aggressive personal patterns
- CI test runs on every PR

---

## Useful Commands

```bash
# DB health check
python db.py health

# Verify no personal data leaks
python db.py verify

# Run all separation tests
python -m unittest tests.test_data_separation

# Phase 3 synthetic smoke test without mutating committed DB/views
python jobs/daily_snapshot.py --synthetic --temp-output --limit 40

# Backfill REC_LOG from browser export
python jobs/migrate_rec_log.py rec_log_export.json

# Tail today's snapshot logs (human-readable)
tail -f logs/snapshot_$(date +%Y-%m-%d).jsonl | jq -r '. | "[\(.level)] \(.job): \(.msg)"'

# Repo size check
du -sh data/db/research_market.sqlite
du -sh data/

# Server with verbose output
python server.py 2>&1 | tee logs/server.log
```

---

## Escalation

For issues not covered here:
1. Check `latest_alerts.json` for active alerts
2. Tail `logs/snapshot_*.jsonl` for structured error context
3. File issue in repo with: log excerpt, reproduction steps, expected vs actual

---

## Deployment — GitHub Secrets + Actions + Pages (Real-Price Activation)

**Architecture.** API keys live in **GitHub Secrets only** (never `config.js`,
never server env that gets committed). GitHub Actions runs the daily snapshot
*server-side* with those keys, commits `data/db` + `data/views` back to `main`,
and GitHub Pages serves the **static front-end** (`data/views/*.json`) — no keys
ever ship to the browser. The public site runs in **Static Mode**.

### One-time setup (you must do these — they need your GitHub account)

```bash
# 1. Create the GitHub repo + add the remote (replace USER/REPO)
gh repo create USER/REPO --public --source=. --remote=origin   # or via the web UI

# 2. Pre-flight locally before the first push
python -m pytest -q
bash scripts/secret-scan.sh            # must say "clean"
bash scripts/ci-guard-personal-data.sh # must say "clean"

# 3. Push
git push -u origin main
```

**4. Add repo Secrets** (Settings → Secrets and variables → Actions → New secret).
Set the values in the GitHub UI — never paste a key into a file or a shell that
gets logged:

| Secret | Required? | Provider |
|--------|-----------|----------|
| `ALPACA_KEY` + `ALPACA_SECRET` | recommended | alpaca.markets (free) — real price history |
| `TIINGO_KEY` | optional fallback | tiingo.com (free) |
| `FINNHUB_KEY` | optional | finnhub.io — quotes + metric ratios |
| `FRED_KEY` | optional | fredaccount.stlouisfed.org — macro |

Optional **Variable** (not a secret): `SEC_USER_AGENT = "Your Name you@email"`
(SEC fair-access). SEC fundamentals are keyless.

**5. Enable Pages**: Settings → Pages → Build and deployment → Source =
**GitHub Actions**. The `pages.yml` workflow then publishes on every push to
`main`. The site URL appears in the workflow run summary.

### Activate real prices + verify

```bash
# Trigger the snapshot manually (fetches real prices via Alpaca/Tiingo/Yahoo)
gh workflow run "Daily Snapshot"        # or the Actions tab → Run workflow
gh run watch                            # wait for it to finish
```

After it commits the snapshot, pull and verify the realism gate flipped:

```bash
git pull
python3 - <<'PY'
import json; d = json.load(open('data/views/latest_snapshot_health.json'))['data_realism']
print('all_prices_synthetic   :', d['all_prices_synthetic'])      # want: false
print('price_quality          :', d['price_quality'])             # want: real > 0
print('valuation_populated    :', d['valuation_populated_count']) # want: > 0
print('price_provider_failures:', d['price_provider_failures'])   # why any provider failed
PY
```

`val_score` (and PER/PFCF-based valuation) populates **only** for tickers whose
*entire* price history is real. Tickers that came back partial stay `mixed` →
valuation OFF + technical DQ capped. Tickers no provider could serve stay
`synthetic`.

### Provider failure quick reference

| `price_provider_failures` reason | meaning | fix |
|----------------------------------|---------|-----|
| `alpaca: no key …` | ALPACA_KEY/SECRET not set | add the secrets |
| `yahoo: HTTP 429` | Yahoo throttled the runner IP | rely on Alpaca/Tiingo (keyed) |
| `tiingo: HTTP 404` | symbol not on Tiingo | expected for some tickers; other providers cover it |

### Security invariants (enforced in CI before any publish)
- `scripts/secret-scan.sh` — fails the build on any committed key literal.
- `scripts/ci-guard-personal-data.sh` — fails on any personal-data artifact.
- `tests/test_data_separation.py` — cloud DB has no personal tables; workflows
  reference no personal data; `/api/db/query` 403s personal tables.
- Pages publishes only `*.html`, `config.js`, `research/`, `js/`, `data/*.json`,
  `data/views/` — never `data/db/`, the backend, or any personal file.
