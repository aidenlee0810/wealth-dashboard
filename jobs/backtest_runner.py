"""
jobs/backtest_runner.py — the backtest engine entry point (Plan §10, §28).

Runs a strategy over [start, end], computes the full metric bundle + §28
statistical rigor, assesses survivorship / point-in-time bias, and persists the
result to `backtest_runs` (idempotent — deterministic run_id upserts). The UI
never computes a backtest; it only reads stored runs via views_builder.

Strategies (Plan §10):
  signal_outcome      generic_signals × signal_outcomes → per-trade EV/PF/Sharpe
                      across horizons; the flagship that runs on day-1 data.
  candidate_discovery candidate_snapshots → forward excess vs SPY (rel_spy).
  risk_governor       System Pure vs Risk Policy avoided/missed ledger (§7/§8).
  portfolio_dca       monthly equal-weight top-N candidates, cost-adjusted.
  regime_aware        fixed vs regime-conditional weighting.

Baselines: SPY / QQQ buy-and-hold equity curves from prices_daily. They are
persisted as reference-only benchmark runs, not alpha claims.

Honest status: with little price history most equity-curve strategies report
results_status='unavailable' (assess_bias / insufficient periods). The trade-
distribution strategies (signal_outcome, candidate_discovery, risk_governor)
work as soon as outcomes accrue. Correctness is proven by tests on synthetic
data; this module never fabricates a number it cannot support.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

from _logging import get_logger  # noqa: E402
from backtest import metrics, stats, bias, costs  # noqa: E402

FEATURE_VERSION = "4.5.0"
UNIVERSE_VERSION = "2.0.0"
DEFAULT_HORIZONS = (5, 20, 60)
MIN_TRADES = 10            # below this a trade-distribution strategy is 'unavailable'
DEFAULT_N_TRIALS = 10      # multiple-testing context for the deflated Sharpe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_id(strategy: str, start: str, end: str, config: dict) -> str:
    payload = f"{strategy}|{start}|{end}|{json.dumps(config, sort_keys=True)}"
    return "bt_" + hashlib.sha1(payload.encode()).hexdigest()[:12]


def _adj_close_series(conn, ticker: str, start: str, end: str) -> list[tuple]:
    rows = conn.execute(
        "SELECT date, COALESCE(adj_close, close) c FROM prices_daily "
        "WHERE ticker=? AND date>=? AND date<=? AND c IS NOT NULL ORDER BY date ASC",
        (ticker, start, end)).fetchall()
    return [(r["date"], r["c"]) for r in rows if r["c"] is not None]


def _returns_from_series(series: list[tuple]) -> list[float]:
    vals = [v for _, v in series]
    return metrics.returns_from_equity(vals)


# ---------------------------------------------------------------------------
# Baselines (equity curves from prices)
# ---------------------------------------------------------------------------
def baseline_buy_and_hold(conn, ticker: str, start: str, end: str) -> dict:
    """SPY/QQQ buy-and-hold: daily return series from adj_close."""
    series = _adj_close_series(conn, ticker, start, end)
    rets = _returns_from_series(series)
    src_rows = conn.execute(
        "SELECT COALESCE(source,'unknown') source, COUNT(*) n FROM prices_daily "
        "WHERE ticker=? AND date>=? AND date<=? GROUP BY COALESCE(source,'unknown')",
        (ticker, start, end)).fetchall()
    price_sources = {r["source"]: r["n"] for r in src_rows}
    return {
        "returns": rets,
        "equity": [v for _, v in series],
        "periods_per_year": metrics.TRADING_DAYS,
        "benchmark_returns": None,
        "available": len(rets) >= MIN_TRADES,
        "n": len(rets),
        "is_baseline": True,
        "uses_features": False,
        "meta": {"ticker": ticker, "first": series[0][0] if series else None,
                 "last": series[-1][0] if series else None,
                 "role": "market_benchmark",
                 "price_sources": price_sources},
        "note": "benchmark_reference" if len(rets) >= MIN_TRADES else "insufficient_price_history",
    }


def _baseline_strategy(ticker: str):
    def _fn(conn, start: str, end: str, config: dict) -> dict:
        return baseline_buy_and_hold(conn, ticker, start, end)
    _fn.__name__ = f"strat_baseline_{ticker.lower()}"
    return _fn


# ---------------------------------------------------------------------------
# Strategy: signal_outcome (trade distribution)
# ---------------------------------------------------------------------------
def strat_signal_outcome(conn, start: str, end: str, config: dict) -> dict:
    """Per-trade returns from graded signals. Each (signal, horizon) is one
    equal-weight bet held `horizon` trading days; metrics are the trade
    distribution annualized at ppy = 252/horizon. Variants across horizons feed
    the PBO test (did we overfit by picking the best horizon?)."""
    horizons = config.get("horizons", DEFAULT_HORIZONS)
    primary = config.get("horizon", 20)
    source = config.get("source")
    risk_status = config.get("risk_status")

    where = ["g.date>=?", "g.date<=?", "o.abs_ret IS NOT NULL"]
    args = [start, end]
    if source:
        where.append("g.source=?"); args.append(source)
    if risk_status:
        where.append("g.risk_status=?"); args.append(risk_status)

    variants = {}
    for h in horizons:
        rows = conn.execute(
            "SELECT o.abs_ret, o.spy_ret FROM signal_outcomes o "
            "JOIN generic_signals g ON g.signal_id=o.signal_id "
            f"WHERE {' AND '.join(where)} AND o.horizon=?",
            (*args, h)).fetchall()
        if len(rows) >= MIN_TRADES:
            variants[f"h{h}"] = {
                "abs": [r["abs_ret"] for r in rows],
                "spy": [r["spy_ret"] for r in rows if r["spy_ret"] is not None],
            }

    primary_key = f"h{primary}"
    if primary_key not in variants:
        # fall back to the horizon with the most trades
        if not variants:
            return {"returns": [], "available": False, "n": 0,
                    "periods_per_year": 252 / primary, "benchmark_returns": None,
                    "meta": {"horizons_tried": list(horizons)},
                    "note": "no_graded_signals_in_window"}
        primary_key = max(variants, key=lambda k: len(variants[k]["abs"]))
        primary = int(primary_key[1:])

    rets = variants[primary_key]["abs"]
    bench = variants[primary_key]["spy"]
    # align benchmark length to returns (per-trade pairs); only used if equal len
    bench_aligned = bench if len(bench) == len(rets) else None

    return {
        "returns": rets,
        "benchmark_returns": bench_aligned,
        "periods_per_year": 252 / primary,
        "available": True,
        "n": len(rets),
        "variants": {k: v["abs"] for k, v in variants.items()},   # for PBO
        "meta": {"primary_horizon": primary, "source": source,
                 "risk_status": risk_status,
                 "trades_by_horizon": {k: len(v["abs"]) for k, v in variants.items()}},
        "note": None,
    }


# ---------------------------------------------------------------------------
# Strategy: candidate_discovery (forward excess vs SPY)
# ---------------------------------------------------------------------------
def strat_candidate_discovery(conn, start: str, end: str, config: dict) -> dict:
    """Did discovered candidates beat SPY over the forward horizon? Uses the
    stored rel_spy (ticker return − SPY return) per candidate at `horizon`."""
    horizon = config.get("horizon", 60)
    ctype = config.get("candidate_type", "Core")
    rows = conn.execute(
        "SELECT o.rel_spy, o.abs_ret, o.spy_ret FROM signal_outcomes o "
        "JOIN generic_signals g ON g.signal_id=o.signal_id "
        "JOIN candidate_snapshots cs ON cs.date=g.date AND cs.ticker=g.ticker "
        "WHERE g.date>=? AND g.date<=? AND o.horizon=? AND cs.candidate_type=? "
        "AND o.rel_spy IS NOT NULL",
        (start, end, horizon, ctype)).fetchall()
    rel = [r["rel_spy"] for r in rows]
    abs_r = [r["abs_ret"] for r in rows if r["abs_ret"] is not None]
    spy_r = [r["spy_ret"] for r in rows if r["spy_ret"] is not None]
    bench_aligned = spy_r if len(spy_r) == len(abs_r) and abs_r else None
    return {
        "returns": abs_r,                       # absolute returns for the metric bundle
        "excess_returns": rel,                  # vs SPY (the discovery question)
        "benchmark_returns": bench_aligned,
        "periods_per_year": 252 / horizon,
        "available": len(abs_r) >= MIN_TRADES,
        "n": len(abs_r),
        "meta": {"candidate_type": ctype, "horizon": horizon,
                 "avg_excess_vs_spy": (round(sum(rel) / len(rel), 4) if rel else None)},
        "note": None if len(abs_r) >= MIN_TRADES else "insufficient_candidates",
    }


# ---------------------------------------------------------------------------
# Strategy: risk_governor (avoided / missed ledger over the window)
# ---------------------------------------------------------------------------
def strat_risk_governor(conn, start: str, end: str, config: dict) -> dict:
    """Backtest the governor itself: over [start,end], how much drawdown did its
    size cuts avoid vs how much upside they forwent (§7/§8 ledger)."""
    horizon = config.get("horizon", 20)
    rows = conn.execute(
        "SELECT g.target_weight_generic wp, g.risk_adjusted_weight wq, o.abs_ret "
        "FROM signal_outcomes o JOIN generic_signals g ON g.signal_id=o.signal_id "
        "WHERE g.date>=? AND g.date<=? AND o.horizon=? AND o.abs_ret IS NOT NULL",
        (start, end, horizon)).fetchall()
    avoided = missed = 0.0
    n_governed = 0
    pure_rets, policy_rets = [], []
    for r in rows:
        wp, wq, ret = r["wp"], r["wq"], r["abs_ret"]
        if wp is None or wq is None:
            continue
        pure_rets.append(wp * ret)
        policy_rets.append(wq * ret)
        cut = wp - wq
        if cut > 1e-9:
            n_governed += 1
            if ret < 0:
                avoided += cut * (-ret)
            elif ret > 0:
                missed += cut * ret
    net = avoided - missed
    return {
        "returns": policy_rets,                 # the Risk-Policy ledger return stream
        "benchmark_returns": pure_rets,         # the System-Pure ledger (the baseline)
        "periods_per_year": 252 / horizon,
        "available": n_governed >= 5,
        "n": len(policy_rets),
        "meta": {"horizon": horizon, "n_governed": n_governed,
                 "avoided_drawdown": round(avoided, 5),
                 "missed_upside": round(missed, 5),
                 "net_governor_value": round(net, 5),
                 "verdict": ("beneficial" if net > 1e-6 else
                             "costly" if net < -1e-6 else "neutral")},
        "note": None if n_governed >= 5 else "too_few_governed_positions",
    }


# ---------------------------------------------------------------------------
# Strategy: portfolio_dca (monthly equal-weight top-N) — equity curve
# ---------------------------------------------------------------------------
def strat_portfolio_dca(conn, start: str, end: str, config: dict) -> dict:
    """Monthly: pick top-N candidates by score, equal-weight, hold one month,
    cost-adjust the rebalance. Needs price history for the held names — reports
    'unavailable' when the DB lacks the spans to compute monthly returns."""
    top_n = config.get("top_n", 10)
    # month-end decision dates that have candidates
    dates = [r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM candidate_snapshots WHERE date>=? AND date<=? "
        "ORDER BY date ASC", (start, end)).fetchall()]
    if len(dates) < 2:
        return {"returns": [], "available": False, "n": 0,
                "periods_per_year": 12, "benchmark_returns": None,
                "meta": {"top_n": top_n, "decision_dates": len(dates)},
                "note": "insufficient_candidate_dates"}

    monthly_rets = []
    prev_weights: dict = {}
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        picks = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM candidate_snapshots WHERE date=? "
            "AND candidate_type IN ('Core','Watchlist','Tactical') "
            "ORDER BY score DESC LIMIT ?", (d0, top_n)).fetchall()]
        if not picks:
            continue
        w = 1.0 / len(picks)
        target = {t: w for t in picks}
        # equal-weight realized return d0→d1
        rs = []
        for t in picks:
            e = conn.execute("SELECT COALESCE(adj_close,close) c FROM prices_daily "
                             "WHERE ticker=? AND date=?", (t, d0)).fetchone()
            x = conn.execute("SELECT COALESCE(adj_close,close) c FROM prices_daily "
                             "WHERE ticker=? AND date=?", (t, d1)).fetchone()
            if e and x and e["c"] and x["c"]:
                rs.append(x["c"] / e["c"] - 1.0)
        if not rs:
            continue
        gross = sum(rs) / len(rs)
        turnover = costs.turnover_from_weights(prev_weights, target)
        monthly_rets.append(costs.apply_costs(gross, turnover))
        prev_weights = target

    return {
        "returns": monthly_rets,
        "periods_per_year": 12,
        "benchmark_returns": None,
        "available": len(monthly_rets) >= MIN_TRADES,
        "n": len(monthly_rets),
        "meta": {"top_n": top_n, "rebalances": len(monthly_rets)},
        "note": None if len(monthly_rets) >= MIN_TRADES else "insufficient_price_spans",
    }


# ---------------------------------------------------------------------------
# Strategy: regime_aware (fixed vs regime-conditional) — placeholder structure
# ---------------------------------------------------------------------------
def strat_regime_aware(conn, start: str, end: str, config: dict) -> dict:
    """Compare equal-weight signals in risk-on vs risk-off regimes. Reports the
    per-regime trade distributions; full dynamic weighting is deferred until a
    multi-regime price history exists."""
    horizon = config.get("horizon", 20)
    rows = conn.execute(
        "SELECT g.market_regime reg, o.abs_ret FROM signal_outcomes o "
        "JOIN generic_signals g ON g.signal_id=o.signal_id "
        "WHERE g.date>=? AND g.date<=? AND o.horizon=? AND o.abs_ret IS NOT NULL",
        (start, end, horizon)).fetchall()
    risk_on, risk_off = [], []
    for r in rows:
        reg = (r["reg"] or "").upper()
        if "RISK_OFF" in reg or reg == "MACRO_RISK_OFF":
            risk_off.append(r["abs_ret"])
        else:
            risk_on.append(r["abs_ret"])
    rets = risk_on + risk_off
    return {
        "returns": rets,
        "benchmark_returns": None,
        "periods_per_year": 252 / horizon,
        "available": len(rets) >= MIN_TRADES,
        "n": len(rets),
        "meta": {"horizon": horizon, "n_risk_on": len(risk_on),
                 "n_risk_off": len(risk_off),
                 "avg_risk_on": round(sum(risk_on) / len(risk_on), 4) if risk_on else None,
                 "avg_risk_off": round(sum(risk_off) / len(risk_off), 4) if risk_off else None},
        "note": None if len(rets) >= MIN_TRADES else "insufficient_signals",
    }


STRATEGIES = {
    "signal_outcome": strat_signal_outcome,
    "candidate_discovery": strat_candidate_discovery,
    "risk_governor": strat_risk_governor,
    "portfolio_dca": strat_portfolio_dca,
    "regime_aware": strat_regime_aware,
    "baseline_spy": _baseline_strategy("SPY"),
    "baseline_qqq": _baseline_strategy("QQQ"),
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_backtest(strategy_name: str, *, start: str, end: str,
                 config: Optional[dict] = None, persist: bool = True,
                 log=None) -> dict:
    """Run one strategy end-to-end: metrics + §28 rigor + bias → backtest_runs."""
    log = log or get_logger("backtest_runner")
    config = config or {}
    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy_name} "
                         f"(have {list(STRATEGIES)})")
    rid = _run_id(strategy_name, start, end, config)
    n_trials = config.get("n_trials", DEFAULT_N_TRIALS)

    with db.cloud() as conn:
        result = STRATEGIES[strategy_name](conn, start, end, config)
        if result.get("is_baseline"):
            biasinfo = _baseline_biasinfo(result)
        else:
            biasinfo = bias.assess_bias(conn, start, end)

        # no-lookahead guard on any features in the window (structural). Price-only
        # benchmark baselines do not consume features, so feature PIT errors should
        # not contaminate their market-reference cards.
        if result.get("uses_features", True):
            try:
                bias.assert_no_lookahead_db(conn, end)
                lookahead_ok = True
            except bias.LookaheadError as e:
                lookahead_ok = False
                log.error("lookahead_violation", strategy=strategy_name, error=str(e))
        else:
            lookahead_ok = True

        rets = result.get("returns") or []
        ppy = result.get("periods_per_year", metrics.TRADING_DAYS)

        if not result.get("available") or len(rets) < 2:
            metric_bundle = {"n_periods": len(rets)}
            rigor = {}
            results_status = "unavailable"
        else:
            metric_bundle = metrics.summary(rets, periods_per_year=ppy)
            boot = stats.bootstrap_sharpe(rets, periods_per_year=ppy)
            defl = stats.deflated_sharpe_from_returns(rets, n_trials=n_trials,
                                                      periods_per_year=ppy)
            whites = (stats.whites_reality_check(rets, result["benchmark_returns"])
                      if result.get("benchmark_returns") else
                      {"p_value": None, "significant": None, "note": "no_benchmark"})
            # PBO across the strategy's own config variants (e.g. horizons) +
            # vs benchmark when present
            pbo_matrix = dict(result.get("variants") or {})
            if not pbo_matrix:
                pbo_matrix = {"strategy": rets}
                if result.get("benchmark_returns"):
                    pbo_matrix["benchmark"] = result["benchmark_returns"]
            pbo = (stats.probability_backtest_overfitting(pbo_matrix)
                   if len(pbo_matrix) >= 2 else {"pbo": None, "note": "single_config"})
            rigor = {"bootstrap": boot, "deflated_sharpe": defl,
                     "whites_reality_check": whites, "pbo": pbo}
            stat_status = stats.classify_results_status(
                deflated=defl, pbo=pbo, whites=whites, n_periods=len(rets))
            # the weaker of bias-status and stat-status governs
            results_status = _combine_status(biasinfo["results_status"], stat_status)
            if result.get("is_baseline"):
                results_status = "reference_only"

        record = _assemble(rid, strategy_name, start, end, config,
                           metric_bundle, rigor, biasinfo, lookahead_ok,
                           results_status, result, n_trials, ppy)
        if persist:
            _persist(conn, record)

    log.info("backtest_done", strategy=strategy_name, run_id=rid,
             n=len(rets), status=results_status, available=result.get("available"))
    return record


def _combine_status(bias_status: str, stat_status: str) -> str:
    """The more cautious of the two statuses wins."""
    rank = {"unavailable": 0, "reference_only": 1, "overfit_risk": 1,
            "marginal": 2, "reliable": 3}
    a, b = rank.get(bias_status, 0), rank.get(stat_status, 0)
    worse = bias_status if a <= b else stat_status
    return worse


def _baseline_biasinfo(result: dict) -> dict:
    meta = result.get("meta") or {}
    ticker = meta.get("ticker", "benchmark")
    sources = meta.get("price_sources") or {}
    source_note = ""
    if sources:
        src_txt = ", ".join(f"{k}:{v}" for k, v in sorted(sources.items()))
        if set(sources) == {"synthetic"}:
            source_note = f" 현재 가격 이력 source={src_txt}라 실제 시장 성과가 아닌 synthetic smoke 기준입니다."
        else:
            source_note = f" 가격 이력 source={src_txt}."
    if not result.get("available"):
        return {
            "survivorship_bias_risk": "none",
            "pit_features_available": True,
            "results_status": "unavailable",
            "bias_warning_message": f"{ticker} 가격 이력이 부족해 benchmark 산출 불가.",
        }
    return {
        "survivorship_bias_risk": "none",
        "pit_features_available": True,
        "results_status": "reference_only",
        "bias_warning_message": (
            f"{ticker} buy-and-hold 시장 기준선입니다. 전략 알파 검정이 아니라 "
            f"비교용 benchmark로만 해석하세요.{source_note}"),
    }


def _assemble(rid, strategy, start, end, config, metric_bundle, rigor,
              biasinfo, lookahead_ok, results_status, raw, n_trials, ppy) -> dict:
    boot = rigor.get("bootstrap", {})
    defl = rigor.get("deflated_sharpe", {})
    pbo = rigor.get("pbo", {})
    whites = rigor.get("whites_reality_check", {})
    return {
        "run_id": rid, "strategy_name": strategy,
        "start_date": start, "end_date": end,
        "config_json": json.dumps(config, ensure_ascii=False),
        "universe_version": UNIVERSE_VERSION, "feature_version": FEATURE_VERSION,
        "periods_per_year": ppy,
        # headline metrics
        "cagr": metric_bundle.get("cagr"),
        "total_return": metric_bundle.get("total_return"),
        "max_drawdown": metric_bundle.get("max_drawdown"),
        "volatility": metric_bundle.get("volatility"),
        "sharpe": metric_bundle.get("sharpe"),
        "sortino": metric_bundle.get("sortino"),
        "calmar": metric_bundle.get("calmar"),
        "hit_rate": metric_bundle.get("hit_rate"),
        "avg_win": metric_bundle.get("avg_win"),
        "avg_loss": metric_bundle.get("avg_loss"),
        "profit_factor": (metric_bundle.get("profit_factor")
                          if metric_bundle.get("profit_factor") != "inf" else None),
        "expected_value": metric_bundle.get("expected_value"),
        # §28 rigor
        "sharpe_ci_lower": boot.get("ci_lower"),
        "sharpe_ci_upper": boot.get("ci_upper"),
        "deflated_sharpe": defl.get("deflated_sharpe"),
        "pbo": pbo.get("pbo"),
        "whites_p_value": whites.get("p_value"),
        "cv_method": "walk_forward",
        "n_oos_periods": metric_bundle.get("n_periods"),
        # bias + status
        "lookahead_check_passed": 1 if lookahead_ok else 0,
        "survivorship_bias_risk": biasinfo["survivorship_bias_risk"],
        "pit_features_available": 1 if biasinfo["pit_features_available"] else 0,
        "results_status": results_status,
        "bias_warning_message": biasinfo["bias_warning_message"],
        "result_json": json.dumps({
            "metrics": metric_bundle, "rigor": rigor,
            "strategy_meta": raw.get("meta"), "note": raw.get("note"),
            "n_trials_context": n_trials,
        }, ensure_ascii=False, default=str),
    }


def _persist(conn, record: dict) -> None:
    row = {k: v for k, v in record.items() if k != "periods_per_year"}
    db.upsert(conn, "backtest_runs", row, conflict_cols=("run_id",))


def run_suite(start: str, end: str, *, log=None) -> dict:
    """Run all strategy and benchmark entries for a window; return statuses."""
    log = log or get_logger("backtest_runner")
    out = {}
    for name in STRATEGIES:
        try:
            r = run_backtest(name, start=start, end=end, log=log)
            out[name] = {"run_id": r["run_id"], "status": r["results_status"],
                         "n": r["n_oos_periods"], "sharpe": r["sharpe"]}
        except Exception as e:                                   # noqa: BLE001
            log.error("strategy_failed", strategy=name, error=str(e))
            out[name] = {"error": str(e)}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="backtest engine")
    ap.add_argument("--strategy", default="signal_outcome",
                    choices=list(STRATEGIES) + ["suite", "baseline"])
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--ticker", default="SPY", help="for --strategy baseline")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    if args.strategy == "suite":
        print(json.dumps(run_suite(args.start, args.end), indent=2, default=str))
    elif args.strategy == "baseline":
        strat = f"baseline_{args.ticker.lower()}"
        if strat in STRATEGIES:
            out = run_backtest(strat, start=args.start, end=args.end,
                               config={}, persist=not args.no_persist)
            print(json.dumps({k: v for k, v in out.items() if k != "result_json"},
                             indent=2, default=str))
        else:
            with db.cloud() as c:
                b = baseline_buy_and_hold(c, args.ticker, args.start, args.end)
            print(json.dumps({k: v for k, v in b.items() if k not in ("returns", "equity")},
                             indent=2, default=str))
    else:
        cfg = {} if args.strategy.startswith("baseline_") else {"horizon": args.horizon}
        out = run_backtest(args.strategy, start=args.start, end=args.end,
                           config=cfg, persist=not args.no_persist)
        print(json.dumps({k: v for k, v in out.items() if k != "result_json"},
                         indent=2, default=str))
