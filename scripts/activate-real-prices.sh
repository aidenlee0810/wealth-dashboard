#!/usr/bin/env bash
# scripts/activate-real-prices.sh — flip synthetic → real prices in one command.
#
# The ONLY thing you provide is a real-price key. Everything else (refresh,
# re-score, regenerate signals, rebuild views, verify) is automated.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

if [ -z "${ALPACA_KEY:-}" ] && [ -z "${TIINGO_KEY:-}" ]; then
  cat <<'MSG'
No real-price key found in the environment. Add ONE of:

  export ALPACA_KEY=...  ALPACA_SECRET=...     # recommended — free at alpaca.markets
  export TIINGO_KEY=...                         # fallback   — free at tiingo.com

Then re-run:   bash scripts/activate-real-prices.sh

(Keys are read from the environment only — never written to config.js or committed.)
MSG
  exit 1
fi

echo "Activating real prices… (provider order tries Alpaca → Tiingo → Yahoo)"
python jobs/activate_real_prices.py "$@"
