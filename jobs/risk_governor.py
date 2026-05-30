"""
jobs/risk_governor.py — the single risk gate every recommendation passes (Plan §7).

15 deterministic gates consolidate risk logic that was scattered across the
codebase. The SAME module serves two contexts:

  * **Cloud snapshot** (no portfolio) — runs the market/data gates only
    (stale data, DQ, liquidity, downtrend, extreme valuation, macro, IPO).
  * **Local Mode** (portfolio passed in) — additionally runs the
    portfolio-relative gates (concentration, leverage, correlation, drawdown).

Portfolio gates simply no-op when `portfolio` is None, so personal data never
needs to reach the cloud for the market gates to work.

Output (mirrors the JS governor for parity):
    {
      risk_status: APPROVED | SIZE_REDUCED | BLOCKED | REVIEW_REQUIRED,
      risk_flags: [...],
      max_allowed_weight: float,
      suggested_size_before_risk: float,
      suggested_size_after_risk: float,
      size_multiplier: float,
      reason: str,
      manual_checks: [...],
    }

Severity order: BLOCKED > REVIEW_REQUIRED > SIZE_REDUCED > APPROVED.
"""

from __future__ import annotations

from typing import Optional

# ── thresholds (Plan §7) ────────────────────────────────────────────────────
DQ_CRITICAL = 50
DQ_LOW = 70
DQ_LOW_MULT = 0.4
ADV_MIN_NEW_ENTRY = 2_000_000        # $2M average dollar volume
EV_SALES_EXTREME = 20.0
HY_OAS_SEVERE_PCT = 5.0              # 500bp (macro_daily stores percent)
IPO_MIN_DAYS = 90
HIGH_BETA_BASKET_CAP = 0.20
SECTOR_CONC_CAP = 0.35
THEME_CONC_CAP = 0.30
SINGLE_POSITION_CAP = 0.08
LEVERAGE_CAP = 1.3
PORTFOLIO_DD_FLOOR = -0.15
CORRELATION_CAP = 0.85

_SEVERITY = {"APPROVED": 0, "SIZE_REDUCED": 1, "REVIEW_REQUIRED": 2, "BLOCKED": 3}


class _Result:
    def __init__(self):
        self.status = "APPROVED"
        self.flags: list[str] = []
        self.reasons: list[str] = []
        self.manual_checks: list[str] = []
        self.size_multiplier = 1.0
        self.max_weight = 1.0

    def gate(self, status: str, flag: str, reason: str, *,
             mult: Optional[float] = None, cap: Optional[float] = None,
             manual: Optional[str] = None):
        if _SEVERITY[status] > _SEVERITY[self.status]:
            self.status = status
        self.flags.append(flag)
        self.reasons.append(reason)
        if mult is not None:
            self.size_multiplier *= mult
        if cap is not None:
            self.max_weight = min(self.max_weight, cap)
        if manual:
            self.manual_checks.append(manual)


def evaluate(
    *,
    ticker: str,
    model_type: str = "operating_company",
    composite: Optional[float] = None,
    dq_score: Optional[float] = None,
    state: Optional[str] = None,
    price_vs_sma200: Optional[float] = None,
    sma200_slope: Optional[float] = None,
    price: Optional[float] = None,
    adv_dollar: Optional[float] = None,
    has_prices: bool = True,
    ev_sales: Optional[float] = None,
    fcf_margin: Optional[float] = None,
    market_regime: Optional[str] = None,
    hy_oas: Optional[float] = None,
    days_since_ipo: Optional[int] = None,
    earnings_in_days: Optional[int] = None,
    base_weight: float = 0.0,
    is_new_entry: bool = True,
    portfolio: Optional[dict] = None,
) -> dict:
    """Run all applicable gates. Gates whose inputs are unavailable are skipped
    (not fired) so we never block on missing data — except the explicit
    missing-essentials gate."""
    r = _Result()

    # ── 1. missing essentials ───────────────────────────────────────────────
    if not has_prices or price is None:
        r.gate("BLOCKED", "MISSING_ESSENTIALS", "가격/캔들 데이터 없음")
        return _finalize(r, base_weight)

    # ── 2/3. data quality ───────────────────────────────────────────────────
    if dq_score is not None:
        if dq_score < DQ_CRITICAL:
            r.gate("BLOCKED", "DQ_CRITICAL", f"데이터품질 {dq_score:.0f} (<50)")
        elif dq_score < DQ_LOW:
            r.gate("SIZE_REDUCED", "DQ_LOW", f"데이터품질 {dq_score:.0f} (50-69)", mult=DQ_LOW_MULT)

    # ── 4. IPO seasoning (§33) ──────────────────────────────────────────────
    if days_since_ipo is not None and days_since_ipo < IPO_MIN_DAYS:
        r.gate("REVIEW_REQUIRED", "IPO_UNSEASONED",
               f"IPO {days_since_ipo}일 경과 (<90) — 변동성·데이터 부족",
               manual="IPO 종목 — 신중한 사이징")

    # ── 5. liquidity (new entry only) ───────────────────────────────────────
    if is_new_entry and adv_dollar is not None and adv_dollar < ADV_MIN_NEW_ENTRY:
        r.gate("BLOCKED", "LIQUIDITY_LOW",
               f"ADV ${adv_dollar/1e6:.1f}M (<$2M) — 신규진입 부적합")

    # ── 6. downtrend (new entry only) ───────────────────────────────────────
    below_200 = (price_vs_sma200 is not None and price_vs_sma200 < 0)
    falling_200 = (sma200_slope is not None and sma200_slope < 0)
    if is_new_entry and below_200 and falling_200:
        r.gate("BLOCKED", "DOWNTREND",
               "200일선 아래 + 하락기울기 — 신규진입 차단")
    elif is_new_entry and state == "DOWNTREND":
        r.gate("SIZE_REDUCED", "DOWNTREND_SOFT", "추세 약세 — 사이즈 축소", mult=0.5)

    # ── 7. extreme valuation w/o FCF ────────────────────────────────────────
    if ev_sales is not None and ev_sales > EV_SALES_EXTREME and \
            fcf_margin is not None and fcf_margin < 0:
        r.gate("BLOCKED", "EXTREME_VALUATION_NO_FCF",
               f"EV/Sales {ev_sales:.0f}x + FCF 적자 — 밸류 위험")

    # ── 8. earnings blackout ────────────────────────────────────────────────
    if earnings_in_days is not None:
        if abs(earnings_in_days) <= 3:
            r.gate("REVIEW_REQUIRED", "EARNINGS_BLACKOUT",
                   f"실적발표 ±{abs(earnings_in_days)}거래일 이내",
                   manual="실적발표 임박 — 발표 후 진입 검토")
    else:
        r.manual_checks.append("실적발표 일정 확인 불가(데이터 없음)")

    # ── 10b. leveraged ETF decay (no portfolio needed) ──────────────────────
    if model_type == "leveraged_etf":
        r.gate("SIZE_REDUCED", "LEVERAGE_DECAY",
               "레버리지 ETF — 변동성 손실(decay) 위험", mult=0.5,
               manual="레버리지 ETF — 장기보유 부적합")

    # ── 14. macro severe ────────────────────────────────────────────────────
    if market_regime in ("MACRO_RISK_OFF", "BROAD_RISK_OFF") and \
            hy_oas is not None and hy_oas > HY_OAS_SEVERE_PCT:
        r.gate("BLOCKED", "MACRO_SEVERE",
               f"매크로 위험회피 + HY OAS {hy_oas*100:.0f}bp (>500bp)")

    # ── Portfolio-relative gates (Local Mode only) ──────────────────────────
    if portfolio:
        _portfolio_gates(r, ticker, model_type, portfolio)

    return _finalize(r, base_weight)


def _portfolio_gates(r: _Result, ticker: str, model_type: str, p: dict) -> None:
    """9–13, 15, portfolio_drawdown — require personal holdings context."""
    # 9. high-beta basket concentration
    if (p.get("high_beta_basket_weight") or 0) >= HIGH_BETA_BASKET_CAP:
        r.gate("SIZE_REDUCED", "HIGH_BETA_CAP",
               f"고베타 바스켓 {p['high_beta_basket_weight']*100:.0f}% (≥20%)", mult=0.5)
    # 10. effective leverage
    if (p.get("effective_leverage") or 0) > LEVERAGE_CAP:
        r.gate("SIZE_REDUCED", "LEVERAGE_CAP",
               f"포트 레버리지 {p['effective_leverage']:.2f}x (>1.3x)", mult=0.5)
    # 11. sector concentration
    sec_w = (p.get("sector_weights") or {}).get(p.get("ticker_sector"), 0)
    if sec_w >= SECTOR_CONC_CAP:
        r.gate("SIZE_REDUCED", "SECTOR_CONCENTRATION",
               f"섹터 비중 {sec_w*100:.0f}% (≥35%)", mult=0.6)
    # 12. theme concentration
    for th, w in (p.get("theme_weights") or {}).items():
        if w >= THEME_CONC_CAP and th in (p.get("ticker_themes") or []):
            r.gate("SIZE_REDUCED", "THEME_CONCENTRATION",
                   f"테마 '{th}' {w*100:.0f}% (≥30%)", mult=0.6)
            break
    # 13. single position cap
    cur = (p.get("position_weights") or {}).get(ticker, 0)
    if cur >= SINGLE_POSITION_CAP:
        r.gate("SIZE_REDUCED", "SINGLE_POSITION_CAP",
               f"단일종목 비중 {cur*100:.0f}% (≥8%)", cap=SINGLE_POSITION_CAP)
    # 15. correlation cluster
    if (p.get("top5_avg_correlation") or 0) > CORRELATION_CAP:
        r.gate("REVIEW_REQUIRED", "CORRELATION_CLUSTER",
               f"상위5 평균상관 {p['top5_avg_correlation']:.2f} (>0.85)",
               manual="상관 높은 클러스터 — 분산 검토")
    # portfolio drawdown
    if (p.get("portfolio_1m_return") if p.get("portfolio_1m_return") is not None else 0) < PORTFOLIO_DD_FLOOR:
        r.gate("BLOCKED", "PORTFOLIO_DRAWDOWN",
               f"포트 1개월 {p['portfolio_1m_return']*100:.0f}% (<-15%) — 신규진입 중단")


def _finalize(r: _Result, base_weight: float) -> dict:
    if r.status == "BLOCKED":
        after = 0.0
    else:
        after = min(base_weight * r.size_multiplier, r.max_weight)
    if not r.reasons:
        r.reasons.append("리스크 게이트 통과")
    return {
        "risk_status": r.status,
        "risk_flags": r.flags,
        "max_allowed_weight": round(r.max_weight, 4),
        "suggested_size_before_risk": round(base_weight, 4),
        "suggested_size_after_risk": round(after, 4),
        "size_multiplier": round(r.size_multiplier, 3),
        "reason": " · ".join(r.reasons[:4]),
        "manual_checks": r.manual_checks,
    }


if __name__ == "__main__":
    import json
    # quick smoke
    print(json.dumps(evaluate(ticker="NVDA", composite=80, dq_score=95,
                              state="UPTREND", price=140, adv_dollar=5e9,
                              price_vs_sma200=0.2, sma200_slope=1.0,
                              market_regime="BROAD_RISK_ON", base_weight=0.04),
                     indent=2, ensure_ascii=False))
    print(json.dumps(evaluate(ticker="JUNK", composite=40, dq_score=42,
                              state="DOWNTREND", price=2, adv_dollar=1e6,
                              price_vs_sma200=-0.3, sma200_slope=-1.0,
                              market_regime="MACRO_RISK_OFF", hy_oas=6.0,
                              base_weight=0.04),
                     indent=2, ensure_ascii=False))
