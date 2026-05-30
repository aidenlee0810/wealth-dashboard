"""
ledger_engine.py — the 3-ledger comparison (Plan §8, Phase 8).

The one place that reads BOTH the cloud DB and the local personal DB to answer:
"system recommendation vs what the Risk Governor allowed vs what you actually
did — and what each cost you."

    A. System Pure   — generic_signals.target_weight_generic (composite-only,
                       pre-risk). "If you'd bought exactly as the signal said."
    B. Risk Policy   — generic_signals.risk_adjusted_weight (post-governor).
                       "System recommendation + Risk Governor + caps."
    C. User Actual   — local user_actions (BUY/PARTIAL deploy capital; IGNORE/
                       REJECT/WATCHLIST_ONLY do not). "What you really did."

All three are compared as **return on deployed capital** at a chosen horizon, so
the different capital amounts normalize away and the deltas are meaningful:

    governor_cost  = pure_return  − policy_return   (price of the governor)
    behavior_cost  = policy_return − actual_return  (price of your behavior)

CRITICAL — data separation: this module lives at the repo ROOT, never under
jobs/. jobs/*.py are forbidden from touching the personal DB (tests/
test_data_separation.py Test 4); only the Local-Mode server may compose
cloud + local, which is exactly what this engine does. It never writes personal
data to the cloud.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

DEPLOY_ACTIONS = frozenset({"BUY", "PARTIAL"})        # deployed capital
NON_DEPLOY_ACTIONS = frozenset({"IGNORE", "REJECT", "WATCHLIST_ONLY", "SELL"})


def _ledger_stats(positions: list[tuple]) -> dict:
    """Return-on-deployed-capital for a ledger.

    positions: list of (weight, ret, spy_ret). weight is the fraction of capital
    deployed; ret is the realized ticker return; spy_ret the benchmark over the
    same window (may be None). All returns are fractions (0.05 = +5%)."""
    deployed = sum(w for w, _, _ in positions if w and w > 0)
    if deployed <= 0:
        return {"n_positions": len(positions), "capital_deployed": 0.0,
                "portfolio_return": None, "benchmark_spy_return": None,
                "excess_vs_spy": None}
    port = sum(w * r for w, r, _ in positions if w and w > 0) / deployed
    # benchmark over the positions that have a SPY return
    bench_w = sum(w for w, _, s in positions if w and w > 0 and s is not None)
    bench = (sum(w * s for w, _, s in positions if w and w > 0 and s is not None) / bench_w
             if bench_w > 0 else None)
    return {
        "n_positions": sum(1 for w, _, _ in positions if w and w > 0),
        "capital_deployed": round(deployed, 6),
        "portfolio_return": round(port, 6),
        "benchmark_spy_return": round(bench, 6) if bench is not None else None,
        "excess_vs_spy": round(port - bench, 6) if bench is not None else None,
    }


def compute_three_ledger(horizon: int = 60, account: Optional[str] = None) -> dict:
    """Compose the System Pure / Risk Policy / User Actual ledgers at `horizon`.

    Reads cloud (signals + outcomes) and local (user_actions). Returns a dict
    safe to surface in Local Mode only — it contains personal-action aggregates
    and must never be written to a cloud view."""
    # 1. cloud: every signal that has a graded outcome at this horizon
    with db.cloud(readonly=True) as c:
        rows = c.execute(
            "SELECT g.signal_id, g.date, g.ticker, "
            "       g.target_weight_generic AS wp, g.risk_adjusted_weight AS wq, "
            "       o.abs_ret, o.spy_ret "
            "FROM generic_signals g "
            "JOIN signal_outcomes o ON o.signal_id = g.signal_id "
            "WHERE o.horizon = ? AND o.abs_ret IS NOT NULL",
            (horizon,)).fetchall()
    sig = {r["signal_id"]: dict(r) for r in rows}

    # 2. local: the user's actual actions (personal DB — Local Mode only)
    with db.local(readonly=True) as loc:
        if account:
            actions = [dict(r) for r in loc.execute(
                "SELECT signal_id, ticker, action_type, actual_amount, account "
                "FROM user_actions WHERE account = ?", (account,)).fetchall()]
        else:
            actions = [dict(r) for r in loc.execute(
                "SELECT signal_id, ticker, action_type, actual_amount, account "
                "FROM user_actions").fetchall()]

    # 3. System Pure + Risk Policy ledgers (from cloud weights)
    pure_pos = [(s["wp"], s["abs_ret"], s["spy_ret"])
                for s in sig.values() if s["wp"]]
    policy_pos = [(s["wq"], s["abs_ret"], s["spy_ret"])
                  for s in sig.values() if s["wq"] and s["wq"] > 0]

    # 4. User Actual ledger (from local actions joined to cloud outcomes)
    actual_pos = []
    breakdown: dict[str, list] = {}
    matched = 0
    for a in actions:
        sid = a.get("signal_id")
        s = sig.get(sid) if sid else None
        if s is None:                                # action's signal not graded yet
            continue
        matched += 1
        breakdown.setdefault(a["action_type"], []).append(s["abs_ret"])
        if a["action_type"] in DEPLOY_ACTIONS:
            # weight by the dollar amount the user actually deployed; if absent,
            # fall back to the system's pure weight so it's still comparable
            w = a.get("actual_amount")
            if not w or w <= 0:
                w = s["wp"] or 0.0
            actual_pos.append((w, s["abs_ret"], s["spy_ret"]))

    pure = _ledger_stats(pure_pos)
    policy = _ledger_stats(policy_pos)
    actual = _ledger_stats(actual_pos)

    # 5. cost deltas (only where both sides have a return)
    def _delta(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 6)

    governor_cost = _delta(pure["portfolio_return"], policy["portfolio_return"])
    behavior_cost = _delta(policy["portfolio_return"], actual["portfolio_return"])
    total_user_gap = _delta(pure["portfolio_return"], actual["portfolio_return"])

    # 6. action-type breakdown (missed upside / partial drag)
    action_breakdown = {
        atype: {
            "n": len(rets),
            "avg_return": round(sum(rets) / len(rets), 6) if rets else None,
        } for atype, rets in breakdown.items()
    }

    return {
        "horizon": horizon,
        "account": account,
        "n_signals_graded": len(sig),
        "n_actions_matched": matched,
        "ledgers": {
            "system_pure": pure,
            "system_risk_policy": policy,
            "user_actual": actual,
        },
        "deltas": {
            "governor_cost": governor_cost,        # >0 ⇒ governor gave up return
            "behavior_cost": behavior_cost,        # >0 ⇒ your behavior lost return
            "total_user_gap": total_user_gap,      # pure − actual
        },
        "action_breakdown": action_breakdown,
        "_note": ("User Actual is empty until you record user_actions in Local Mode. "
                  "Returns are on deployed capital so the three ledgers are comparable."),
    }


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="3-ledger comparison (Local Mode)")
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--account", default=None)
    args = ap.parse_args()
    print(json.dumps(compute_three_ledger(args.horizon, args.account),
                     indent=2, ensure_ascii=False))
