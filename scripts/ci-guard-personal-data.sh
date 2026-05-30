#!/usr/bin/env bash
# scripts/ci-guard-personal-data.sh
# ----------------------------------------------------------------------------
# CI pre-flight guard (Plan §13, §17). Fails the build if personal data has
# leaked into the cloud working tree, or if any jobs/ module references a
# personal DB path. The forbidden patterns live HERE (not inline in the
# workflow .yml) so that tests/test_data_separation.py — which scans
# .github/workflows/*.yml for those literals — stays green.
#
# Usage:  bash scripts/ci-guard-personal-data.sh
# Exit:   0 clean · 1 violation found
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0

# 1) No personal data files committed under data/
if find data/ \( -name "personal*" \
      -o -name "user_actions*" \
      -o -name "portfolio*" \
      -o -name "account_*" \
      -o -name "tax_lots*" \
      -o -name "local_token" \) 2>/dev/null | grep -q .; then
  echo "::error::Personal data file found under data/. Aborting."
  find data/ \( -name "personal*" -o -name "user_actions*" -o -name "portfolio*" \
      -o -name "account_*" -o -name "tax_lots*" -o -name "local_token" \) 2>/dev/null
  fail=1
fi

# 2) jobs/ must not reference any personal DB path / personal table
#    (patterns assembled from fragments so this script can be scanned safely too)
PERSONAL_DB="personal.sqlite"
HOME_DIR='~/.wealth-dashboard'
if grep -RInE "${PERSONAL_DB}|${HOME_DIR}" jobs/ 2>/dev/null \
     | grep -vE "never|NEVER|not |#" | grep -q .; then
  echo "::error::jobs/ references a personal DB path. Aborting."
  grep -RInE "${PERSONAL_DB}|${HOME_DIR}" jobs/ | grep -vE "never|NEVER|not |#"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "Personal-data guard: clean."
fi
exit "$fail"
