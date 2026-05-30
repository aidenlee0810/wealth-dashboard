"""
jobs/backtest/ — the Python backtest engine (Plan §10, §28).

Pure-function, stdlib-only building blocks the runner composes:

  metrics   return-series performance metrics (CAGR, Sharpe, Sortino, ...)
  stats     statistical rigor (bootstrap CI, deflated Sharpe, PBO/CSCV,
            time-series CV, White's reality check) — §28
  bias      no-lookahead enforcement + survivorship/PIT bias assessment +
            transaction-cost model

Design philosophy mirrors the rest of jobs/: **no numpy/scipy**. The §28
statistics are implemented in stdlib using `statistics.NormalDist` for the
normal CDF / inverse-CDF, manual skew/kurtosis, and seeded `random` for the
bootstrap so every result is deterministic and reproducible in CI.
"""
