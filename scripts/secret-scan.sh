#!/usr/bin/env bash
# scripts/secret-scan.sh — pre-deploy secret scan.
#
# Fails (exit 1) if any API-key / secret literal is committed to a tracked file.
# Run locally before pushing and in CI before publishing to GitHub Pages, so a
# key can never leak into the public repo. Keys belong in GitHub Secrets only.
#
# If this fires: ROTATE the key first, then remove it from the file (use GitHub
# Secrets / env) and scrub it from git history.
# Portable to macOS bash 3.2 (no mapfile / process-substitution required).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# Provider key/secret literals:  FINNHUB_KEY = "abc123..."  (value >= 16 chars)
P1="(FINNHUB|FRED|ALPACA|TIINGO|FMP|ANTHROPIC|ALPHA_VANTAGE|POLYGON|IEX)[A-Z_]*(KEY|SECRET)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9_-]{16,}"
# Generic high-entropy secret/token literals (value >= 32 chars).
P2="(api[_-]?key|secret|access[_-]?token|client[_-]?secret)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9]{32,}"
# Project's own non-secret tokens + obvious placeholders to ignore.
EXCL="local_token|github_token|x-local-token|csrf|placeholder|your[_-]?key|example|noreply|test|dummy|sample"

FOUND=0
OLDIFS="$IFS"; IFS='
'
for f in $(git ls-files | grep -vE '\.(sqlite|sqlite-wal|sqlite-shm|png|jpe?g|gif|ico|woff2?|pdf)$'); do
  [ -f "$f" ] || continue
  m1=$(grep -HnE "$P1" "$f" 2>/dev/null)
  if [ -n "$m1" ]; then echo "$m1"; FOUND=1; fi
  m2=$(grep -HnE "$P2" "$f" 2>/dev/null | grep -ivE "$EXCL")
  if [ -n "$m2" ]; then echo "$m2"; FOUND=1; fi
done
IFS="$OLDIFS"

if [ "$FOUND" -ne 0 ]; then
  echo "::error::secret-scan FAILED — an API key/secret literal is committed."
  echo "  → Rotate the key, remove it from the file (use GitHub Secrets / env), scrub history."
  exit 1
fi

echo "secret-scan: clean — no API key/secret literals in tracked files."
