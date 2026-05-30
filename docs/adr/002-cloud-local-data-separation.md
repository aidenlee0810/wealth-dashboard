# ADR 002: Cloud vs Local Data Separation

**Status:** Accepted  
**Date:** 2026-05-28  
**Phase:** 1

---

## Context

The platform aggregates market research (universal across users) and personal portfolio data (user-specific, sensitive). Storing both in the same database has serious implications:

- **Public repo + cloud DB:** if portfolio data leaks, the user's holdings, trades, and account info become visible to anyone with the repo URL.
- **CI/CD:** GitHub Actions runs in cloud; any DB it touches could be inspected by GitHub support, logged, or leaked.
- **Snapshot reproducibility:** market data should be the same for all users; personal data should not be.

## Decision

**Strict separation: two SQLite files in two locations.**

| DB | Path | Committed to git? | Visible to GHA? | Visible via /api/db/query? |
|----|------|-------------------|-----------------|----------------------------|
| `research_market.sqlite` | `data/db/` | ✅ Yes | ✅ Yes | ✅ Yes (read-only, whitelisted) |
| `personal.sqlite` | `~/.wealth-dashboard/` | ❌ NEVER | ❌ NEVER | ❌ NEVER (via /api/local/* only) |

## Enforcement Mechanisms

1. **`.gitignore` patterns** block personal files
2. **`.git/hooks/pre-commit`** blocks personal patterns + API key literals
3. **GHA workflow first step** scans for personal files, fails build if found
4. **`db.py:PERSONAL_TABLES` constant** is enforced by `validate_and_query`
5. **`tests/test_data_separation.py`** runs 7 checks in CI
6. **`/api/local/*` endpoints** check `client_address == localhost`

## Rationale

- **Defense in depth:** 6 independent checks (any one of which catches the leak)
- **User can run app entirely offline** (personal DB never touches network)
- **Public repo is safe** — market data is not sensitive
- **Replay/backtest still works** — uses cloud DB only

## Consequences

**Pros:**
- Personal portfolio safe even if repo is public
- GHA can run unattended — no risk of leaking
- Backtest results portable (same cloud DB everywhere)

**Cons:**
- Matching cloud signal to personal action requires `signal_id` cross-reference
- Two DBs to keep in sync (different migration files)
- "Magic" — new contributors must learn the separation rule

## Migration Path

- If user moves to multi-device, sync `personal.sqlite` via Dropbox/iCloud (still .gitignored, never via repo)
- No cross-device cloud sync of personal data is provided by this platform

## Reviewed

- Permanent decision; rescind only if architecture changes fundamentally
