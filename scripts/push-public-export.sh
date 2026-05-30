#!/usr/bin/env bash
# Push a sanitized, history-free public snapshot to GitHub.
#
# Use this for a PUBLIC repo. It does not push this local repo's git history,
# and it excludes the private Google Sheets dashboard shell.
#
#   bash scripts/push-public-export.sh aidenlee0810/wealth-dashboard
set -euo pipefail

cd "$(dirname "$0")/.." || exit 2

REPO="${1:-}"
BRANCH="${2:-main}"

if [ -z "$REPO" ]; then
  echo "usage: bash scripts/push-public-export.sh USER/REPO [branch]" >&2
  exit 1
fi

if [[ "$REPO" == http* || "$REPO" == git@* || "$REPO" == /* || "$REPO" == ./* ]]; then
  REMOTE_URL="$REPO"
else
  REMOTE_URL="https://github.com/$REPO.git"
fi

echo "→ pre-flight guards on local workspace…"
bash scripts/secret-scan.sh
bash scripts/ci-guard-personal-data.sh

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "→ assembling sanitized public export…"
rsync -a --delete ./ "$TMP"/ \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='_site' \
  --exclude='logs' \
  --exclude='server.log' \
  --exclude='*.pid' \
  --exclude='config.js' \
  --exclude='config.local.js' \
  --exclude='config.local.*' \
  --exclude='index.html' \
  --exclude='js' \
  --exclude='data/db/personal.sqlite*' \
  --exclude='data/personal' \
  --exclude='data/snapshots/_scratch'

cat > "$TMP/config.js" <<'JS'
// Public config. Private account settings are intentionally omitted.
// The public site serves static research views; server-side provider keys live in
// GitHub Actions Secrets and are never shipped to browsers.
const CONFIG = {
  PUBLIC_SITE: true,
  PORTFOLIO_TARGETS: {},
  ACCOUNT_RULES: [],
  REBALANCE_THRESHOLD: 5,
};

const RESEARCH_CONFIG = {
  FINNHUB_KEY: '',
  FMP_KEY: '',
  FRED_KEY: '',
  GURUS: {
    '워렌 버핏 (Berkshire Hathaway)': '0001067983',
    '빌 액만 (Pershing Square)': '0001336528',
    '마이클 버리 (Scion Asset Mgmt)': '0001649339',
    '데이비드 아인혼 (Greenlight)': '0001079114',
    '데이비드 테퍼 (Appaloosa)': '0001003237',
    '스탠 드러켄밀러 (Duquesne)': '0001536411',
    '세스 클라만 (Baupost Group)': '0001061219',
    '모니쉬 파브라이 (Pabrai Funds)': '0001173334',
    '하워드 마크스 (Oaktree Capital)': '0000949509',
  },
};
JS

rm -f "$TMP/research/portfolio.js" "$TMP/research/optimizer.js" "$TMP/research/validation.js"
python3 - <<PY
from pathlib import Path
p = Path("$TMP/research.html")
s = p.read_text()
import re
# Drop the private-overrides tag (config.local.js is never published).
s = re.sub(r'\\n\\s*<script src="config\\.local\\.js.*?</script>', '', s)
for tab in ('portfolio', 'optimizer', 'validation'):
    s = re.sub(r'\\n\\s*<button id="btn-' + tab + r'".*?</button>', '', s, flags=re.S)
    s = re.sub(r'\\n\\s*<!-- [^<]* -->\\n\\s*<div id="tab-' + tab + r'" class="hidden"></div>', '', s)
for module in ('optimizer', 'portfolio', 'validation'):
    s = re.sub(r'\\n\\s*<script src="research/' + module + r'\\.js(?:\\?v=[^"]*)?"></script>', '', s)
p.write_text(s)
PY

if grep -R "Google Sheets\\|Tiller\\|gapi\\|google.accounts" "$TMP"/research.html "$TMP"/config.js >/dev/null; then
  echo "blocked: public export still contains personal dashboard references" >&2
  exit 1
fi

echo "→ creating history-free commit…"
git -C "$TMP" init -q
git -C "$TMP" checkout -q -b "$BRANCH"
git -C "$TMP" add .
git -C "$TMP" commit -q -m "initial public research release"
git -C "$TMP" remote add origin "$REMOTE_URL"
git -C "$TMP" fetch origin "$BRANCH" >/dev/null 2>&1 || true

echo "→ pushing sanitized export to $REMOTE_URL ($BRANCH)…"
git -C "$TMP" push --force-with-lease -u origin "$BRANCH"

cat <<MSG

✅ Public export pushed.

Next:
  1) GitHub repo → Settings → Secrets and variables → Actions
  2) Add ALPACA_KEY and ALPACA_SECRET
  3) Settings → Pages → Source = GitHub Actions
  4) Actions → Daily Snapshot → Run workflow

MSG
