"""
jobs/backtest/bias.py — backtest integrity guards (Plan §10).

Two failure modes silently inflate every backtest; this module makes both
visible and refuses to hide them:

  1. Lookahead — using a feature on a decision date before it was knowable.
     `assert_no_lookahead` enforces usable_at <= decision_date, structurally.

  2. Survivorship / no point-in-time data — backtesting today's index members
     over the past (dead companies missing) or scoring history with features
     that didn't exist then. `assess_bias` inspects what history the DB
     actually has and downgrades `results_status` accordingly, so a pretty
     CAGR can never masquerade as reliable when the data can't support it.
"""

from __future__ import annotations

from typing import Optional, Sequence


class LookaheadError(AssertionError):
    """Raised when a feature would be used before its usable_at timestamp."""


def assert_no_lookahead(feature_rows: Sequence[dict], decision_date: str,
                        *, usable_key: str = "usable_at") -> int:
    """Assert every feature row was knowable on decision_date.

    feature_rows : iterable of dicts each carrying a `usable_at` (ISO string).
    Rows with usable_at is None are treated as ALWAYS-knowable (e.g. prices,
    which are point-in-time by construction) and skipped. Returns the number
    of rows checked. Raises LookaheadError on the first violation set.

    Comparison is **date-granular**: a feature stamped usable_at='D 20:00Z'
    (computed after the close on day D) is usable for a decision dated D. Only a
    usable_at whose calendar date is strictly after decision_date is a lookahead
    — comparing the raw timestamp string would falsely flag the same-day time
    suffix."""
    dd = str(decision_date)[:10]
    illegal = [r for r in feature_rows
               if r.get(usable_key) is not None and str(r[usable_key])[:10] > dd]
    if illegal:
        sample = illegal[0]
        raise LookaheadError(
            f"Lookahead: {len(illegal)} feature row(s) have {usable_key} > "
            f"decision_date {decision_date} (e.g. {sample.get('ticker', '?')} "
            f"{usable_key}={sample.get(usable_key)})")
    return len(feature_rows)


def assert_no_lookahead_db(conn, decision_date: str, *,
                           table: str = "features_daily") -> int:
    """DB variant: no row dated on/before decision_date may have a usable_at
    whose CALENDAR DATE is after decision_date. Returns rows checked; raises on
    violation. Compares substr(usable_at,1,10) so a same-day post-close
    timestamp ('D 20:00Z') is not falsely flagged."""
    bad = conn.execute(
        f"SELECT COUNT(*) n FROM {table} "
        f"WHERE date <= ? AND usable_at IS NOT NULL AND substr(usable_at,1,10) > ?",
        (decision_date, decision_date)).fetchone()["n"]
    if bad:
        raise LookaheadError(
            f"Lookahead: {bad} {table} rows dated <= {decision_date} have "
            f"DATE(usable_at) > {decision_date}")
    total = conn.execute(
        f"SELECT COUNT(*) n FROM {table} WHERE date <= ?",
        (decision_date,)).fetchone()["n"]
    return total


def assess_bias(conn, start_date: str, end_date: str) -> dict:
    """Judge how trustworthy a backtest over [start_date, end_date] can be,
    given the history the DB actually holds (Plan §10 assess_bias).

    Returns the fields stored on backtest_runs:
        survivorship_bias_risk ∈ none|low|medium|high|unknown
        pit_features_available : bool
        results_status         ∈ reliable|reference_only|unavailable
        bias_warning_message   : str | None
    """
    # 1. Do we have universe membership recorded BEFORE the backtest start?
    #    If not, we'd be testing today's constituents over the past → survivorship.
    hist_membership = conn.execute(
        "SELECT COUNT(*) n FROM universe_membership WHERE date < ?",
        (start_date,)).fetchone()["n"]

    if hist_membership == 0:
        return {
            "survivorship_bias_risk": "high",
            "pit_features_available": False,
            "results_status": "reference_only",
            "bias_warning_message": (
                "⛔ 과거 universe membership 없음 — 현재 구성종목으로 과거를 재구성하면 "
                "상장폐지·피인수 종목이 누락되어 survivorship bias가 큼. 결과는 참고용."),
        }

    # 2. Do we have point-in-time features before the start? A feature is PIT
    #    if its usable_at DATE is on/before its own feature date (not future-
    #    stamped). Compare date portions so a same-day post-close timestamp
    #    ('D 20:00Z') counts as PIT.
    pit_features = conn.execute(
        "SELECT COUNT(*) n FROM features_daily "
        "WHERE date < ? AND usable_at IS NOT NULL AND substr(usable_at,1,10) <= date",
        (start_date,)).fetchone()["n"]

    if pit_features == 0:
        return {
            "survivorship_bias_risk": "medium",
            "pit_features_available": False,
            "results_status": "unavailable",
            "bias_warning_message": (
                "❌ Point-in-time 펀더멘털 없음 — fundamental 백테스트 불가. "
                "technical-only 백테스트만 신뢰 가능."),
        }

    return {
        "survivorship_bias_risk": "low",
        "pit_features_available": True,
        "results_status": "reliable",
        "bias_warning_message": None,
    }


def history_span(conn, ticker: str = "SPY") -> dict:
    """How much price history exists for a benchmark — used to decide whether a
    baseline backtest is even runnable. Returns {n, first, last}."""
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(date) first, MAX(date) last "
        "FROM prices_daily WHERE ticker=?", (ticker,)).fetchone()
    return {"n": row["n"], "first": row["first"], "last": row["last"]}
