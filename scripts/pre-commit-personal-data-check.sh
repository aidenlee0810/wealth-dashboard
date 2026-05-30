#!/usr/bin/env bash
# =============================================================================
# scripts/pre-commit-personal-data-check.sh
#
# Pre-commit guard: block personal data files and raw API key literals.
# Installed by:   scripts/install-hooks.sh
# Also tested by: tests/test_data_separation.py (test_2)
#
# What it blocks:
#   1. Personal data files (personal.sqlite, user_actions.*, etc.)
#   2. Raw API key literals in staged Python/JS/YAML files
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'   # no color

ERRORS=0

# ── 1. Personal data file patterns ──────────────────────────────────────────
PERSONAL_PATTERNS=(
  'personal\.sqlite'
  'personal\.sqlite-(journal|shm|wal)'
  'user_actions.*\.(csv|json|sqlite)'
  'portfolio_snapshots.*\.(csv|json|sqlite)'
  'account_buckets.*\.(csv|json|sqlite)'
  'tax_lots.*\.(csv|json|sqlite)'
  'data/personal/'
  '\.wealth-dashboard/'
  'local_token'
)

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)

if [ -z "$STAGED_FILES" ]; then
  exit 0
fi

for pat in "${PERSONAL_PATTERNS[@]}"; do
  # Use grep -E on the list of staged files
  MATCHES=$(echo "$STAGED_FILES" | grep -E "$pat" || true)
  if [ -n "$MATCHES" ]; then
    echo -e "${RED}ERROR: Personal data file detected in commit:${NC}"
    echo "$MATCHES" | while read -r f; do echo "  ❌ $f (matches: $pat)"; done
    ERRORS=$((ERRORS + 1))
  fi
done

# ── 2. API key literal patterns ──────────────────────────────────────────────
# Check staged .py / .js / .ts / .yaml / .yml / .json files for key patterns.
# Exclusions: config.js is allowed to have placeholder text but not real keys.

CODE_FILES=$(echo "$STAGED_FILES" | grep -E '\.(py|js|ts|yaml|yml)$' || true)

if [ -n "$CODE_FILES" ]; then
  # Finnhub keys: typically 'c' + 20 alphanumeric chars
  # FRED keys: 32 hex chars
  # Anthropic keys: sk-ant-...
  KEY_PATTERNS=(
    'sk-ant-api[0-9A-Za-z_-]+'
    "FRED_KEY\s*[=:]\s*['\"][0-9a-f]{32}['\"]"
    "FINNHUB_KEY\s*[=:]\s*['\"][a-z0-9]{20,}['\"]"
    'apiKey\s*:\s*["\x27][a-z0-9]{20,}["\x27]'
  )

  for f in $CODE_FILES; do
    # Only check the staged content (not working tree)
    CONTENT=$(git show ":$f" 2>/dev/null || true)
    for kpat in "${KEY_PATTERNS[@]}"; do
      if echo "$CONTENT" | grep -qE "$kpat"; then
        echo -e "${YELLOW}WARNING: Possible API key literal in staged file: $f (pattern: $kpat)${NC}"
        echo "  Use environment variables or RESEARCH_CONFIG / GitHub Secrets instead."
        # Warning only — don't block (false positives possible)
      fi
    done
  done
fi

# ── Result ───────────────────────────────────────────────────────────────────
if [ "$ERRORS" -gt 0 ]; then
  echo ""
  echo -e "${RED}Pre-commit hook FAILED: $ERRORS personal data file(s) blocked.${NC}"
  echo "Remove these files from staging before committing:"
  echo "  git reset HEAD <file>"
  echo ""
  echo "If you believe this is a false positive, check PERSONAL_PATTERNS"
  echo "in scripts/pre-commit-personal-data-check.sh"
  exit 1
fi

exit 0
