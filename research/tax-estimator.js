// research/tax-estimator.js — lightweight after-tax lens for research tabs.
//
// This is a comparison helper, not a tax filing engine. The personal dashboard
// still uses TAX_ENGINE with actual lots. Here we estimate after-tax dividend
// yield and simple capital-gain scenarios so candidate/fundamental cards can be
// judged on "money kept", not just headline return.

const TAX_EST = (() => {
  function _num(v, fallback = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function _status() {
    try {
      if (typeof TAX_ENGINE !== 'undefined' && TAX_ENGINE.currentStatus) {
        return TAX_ENGINE.currentStatus();
      }
      if (typeof CONFIG !== 'undefined' && CONFIG.RA_DATE) {
        return new Date() < new Date(CONFIG.RA_DATE) ? 'NRA' : 'RA';
      }
    } catch (_) {}
    return 'UNKNOWN';
  }

  function dividendRate() {
    const status = _status();
    if (status === 'NRA') return _num(CONFIG?.NRA_DIVIDEND_RATE, 0.15);
    // Qualified dividends usually stack with LTCG brackets. For comparison,
    // ask TAX_ENGINE for the marginal tax on $1,000 of qualified dividends.
    try {
      if (typeof TAX_ENGINE !== 'undefined') {
        const income = _num(CONFIG?.BASE_SALARY, 68000);
        const base = TAX_ENGINE.calcRA({ grossIncome: income });
        const withDiv = TAX_ENGINE.calcRA({ grossIncome: income, qualifiedDiv: 1000 });
        return Math.max(0, Math.min(0.5, (withDiv.totalTax - base.totalTax) / 1000));
      }
    } catch (_) {}
    return 0.15;
  }

  function capitalGainRate({ longTerm = true } = {}) {
    const status = _status();
    const income = _num(CONFIG?.BASE_SALARY, 68000);
    try {
      if (typeof TAX_ENGINE !== 'undefined') {
        const days = TAX_ENGINE.estimateDaysPresentUS ? TAX_ENGINE.estimateDaysPresentUS() : null;
        const r = TAX_ENGINE.marginalCapGainTax(1000, income, longTerm, null, { daysPresentUS: days });
        return status === 'NRA' ? Math.max(0, Math.min(0.5, r.nra / 1000))
                                : Math.max(0, Math.min(0.5, r.ra / 1000));
      }
    } catch (_) {}
    if (status === 'NRA') {
      const days = _num(CONFIG?.NRA_DAYS_PRESENT_US, 0);
      return days >= 183 ? _num(CONFIG?.NRA_CAPITAL_GAINS_RATE, 0.30) : 0;
    }
    return longTerm ? 0.15 : 0.22;
  }

  function afterTaxDividendYield(grossYield) {
    if (grossYield == null || !Number.isFinite(Number(grossYield))) return null;
    return Number(grossYield) * (1 - dividendRate());
  }

  function afterTaxCapitalReturn(grossReturn, opts = {}) {
    if (grossReturn == null || !Number.isFinite(Number(grossReturn))) return null;
    const r = Number(grossReturn);
    if (r <= 0) return r; // losses are not modelled as a tax benefit here.
    return r * (1 - capitalGainRate(opts));
  }

  function summary({ dividendYield = null, capitalReturn = 0.10 } = {}) {
    const divRate = dividendRate();
    const ltRate = capitalGainRate({ longTerm: true });
    const stRate = capitalGainRate({ longTerm: false });
    return {
      status: _status(),
      dividendTaxRate: divRate,
      longTermCapGainTaxRate: ltRate,
      shortTermCapGainTaxRate: stRate,
      afterTaxDividendYield: afterTaxDividendYield(dividendYield),
      afterTaxLongTermReturn: afterTaxCapitalReturn(capitalReturn, { longTerm: true }),
      afterTaxShortTermReturn: afterTaxCapitalReturn(capitalReturn, { longTerm: false }),
    };
  }

  return {
    status: _status,
    dividendRate,
    capitalGainRate,
    afterTaxDividendYield,
    afterTaxCapitalReturn,
    summary,
  };
})();

if (typeof window !== 'undefined') window.TAX_EST = TAX_EST;
