#!/usr/bin/env bash
# scripts/github-setup.sh — bootstrap the GitHub deployment in one command.
#
# Creates the repo, pushes, and enables Pages via the GitHub CLI. Secrets still
# need YOUR key values (set in the UI or with `gh secret set`) — this script
# never handles or prints a key value.
#
#   bash scripts/github-setup.sh USER/REPO
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
REPO="${1:-}"

if ! command -v gh >/dev/null 2>&1; then
  cat <<'MSG'
GitHub CLI (gh) is not installed. Do these manually (one-time):

  1) Create a PUBLIC repo on github.com
  2) Push a sanitized, history-free public export:
       bash scripts/push-public-export.sh USER/REPO
  3) Settings → Secrets and variables → Actions → New repository secret:
       ALPACA_KEY, ALPACA_SECRET     (recommended — real price history)
       TIINGO_KEY, FINNHUB_KEY, FRED_KEY   (optional)
       (optional Variable) SEC_USER_AGENT = "Your Name you@email"
  4) Settings → Pages → Source = "GitHub Actions"
  5) Actions tab → "Daily Snapshot" → Run workflow   (activates real prices)

Install gh: https://cli.github.com  then re-run:  bash scripts/github-setup.sh USER/REPO
MSG
  exit 0
fi

if [ -z "$REPO" ]; then
  echo "usage: bash scripts/github-setup.sh USER/REPO"; exit 1
fi

echo "→ pre-flight guards…"
bash scripts/secret-scan.sh
bash scripts/ci-guard-personal-data.sh

echo "→ creating $REPO if needed …"
gh repo create "$REPO" --public >/dev/null 2>&1 || true

echo "→ pushing sanitized public export (no local git history / no private dashboard)…"
bash scripts/push-public-export.sh "$REPO"

echo "→ enabling GitHub Pages (Actions source) …"
gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || echo "  (if this failed, enable manually: Settings → Pages → Source = GitHub Actions)"

cat <<MSG

✅ Repo pushed + Pages workflow ready.

Final step — add YOUR keys as secrets (values never committed):
  gh secret set ALPACA_KEY     --repo $REPO
  gh secret set ALPACA_SECRET  --repo $REPO
  # optional: gh secret set TIINGO_KEY / FINNHUB_KEY / FRED_KEY  --repo $REPO

Then activate real prices in CI:
  gh workflow run "Daily Snapshot" --repo $REPO
  gh run watch --repo $REPO

Verify after it finishes (data_realism should flip):
  git pull && python3 -c "import json;d=json.load(open('data/views/latest_snapshot_health.json'))['data_realism'];print('all_synthetic',d['all_prices_synthetic'],'| real',d['price_quality']['real'],'| valuation',d['valuation_populated_count'])"
MSG
