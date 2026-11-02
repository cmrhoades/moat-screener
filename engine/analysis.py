"""Pure analysis math. No network calls here — everything is testable offline.

Criteria (Nov 2026 version):
  ROIC test     : average ROIC over the last 7 fiscal years >= 10%
  Erosion test  : avg ROIC (most recent 5 yrs) >= 0.80 x avg ROIC (prior 5 yrs)
  Valuation     : 3-scenario DCF (bear/base/bull), price vs intrinsic value
  Implied CAGR  : FCF growth the current price bakes in (8% discount, 2.5% terminal)
  Actual CAGR   : FCF growth over the last 5 fiscal years
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- thresholds (edit here to change the rules) ---------------------------
ROIC_AVG_YEARS = 7
ROIC_AVG_MIN = 10.0          # percent
ROIC_MIN_POPULATED = 5       # need at least this many of the 7 years reported
EROSION_RATIO_MIN = 0.80
EROSION_MIN_PER_HALF = 4     # need at least 4 reported years in each 5-yr half

DISCOUNT = 0.08
SCENARIOS = {                # name: (10-yr FCF growth, terminal growth)
    "bear": (0.03, 0.02),
    "base": (0.07, 0.025),
    "bull": (0.095, 0.03),
}
IMPLIED_TERMINAL = 0.025
FAIR_BAND = 0.10             # within +/-10% of base IV = "Fairly Valued"
ACTUAL_CAGR_YEARS = 5


# ---- ROIC tests ------------------------------------------------------------
def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def roic_test(roic_by_year: dict[int, float | None]) -> dict:
    years = sorted(roic_by_year)[-ROIC_AVG_YEARS:]
    vals = [roic_by_year[y] for y in years]
    populated = [v for v in vals if v is not None]
    out = {"roic_years": years, "roic_7yr_avg": _avg(vals), "roic_blank_years":
           [y for y in years if roic_by_year[y] is None]}
    if len(populated) < ROIC_MIN_POPULATED:
        out["roic_test"] = "Insufficient data"
    else:
        out["roic_test"] = "Pass" if out["roic_7yr_avg"] >= ROIC_AVG_MIN else "Fail"
    return out


def erosion_test(roic_by_year: dict[int, float | None]) -> dict:
    years = sorted(roic_by_year)[-10:]
    older, recent = years[:5], years[5:]
    o = [roic_by_year[y] for y in older if roic_by_year[y] is not None]
    r = [roic_by_year[y] for y in recent if roic_by_year[y] is not None]
    out = {"erosion_recent_avg": _avg(r), "erosion_older_avg": _avg(o), "erosion_ratio": None}
    if len(years) < 10 or len(o) < EROSION_MIN_PER_HALF or len(r) < EROSION_MIN_PER_HALF:
        out["erosion_test"] = "Insufficient data"
        return out
    ro, rr = out["erosion_older_avg"], out["erosion_recent_avg"]
    if ro > 0:
        out["erosion_ratio"] = rr / ro
        out["erosion_test"] = "Pass" if rr >= EROSION_RATIO_MIN * ro else "Fail"
    else:  # older average was negative: just require improvement
        out["erosion_test"] = "Pass" if rr >= ro else "Fail"
    return out


# ---- DCF -------------------------------------------------------------------
def intrinsic_value_per_share(fcf, cash, debt, shares, growth, terminal, discount=DISCOUNT):
    pv = sum(fcf * (1 + growth) ** n / (1 + discount) ** n for n in range(1, 11))
    fcf10 = fcf * (1 + growth) ** 10
    pv += fcf10 * (1 + terminal) / (discount - terminal) / (1 + discount) ** 10
    return (pv + cash - debt) / shares


def bucket(price, bear, base, bull):
    if price > bull:
        return "Strongly Overvalued"
    if price < bear:
        return "Strongly Undervalued"
    if abs(price - base) <= FAIR_BAND * base:
        return "Fairly Valued"
    return "Probably Overvalued" if price > base else "Probably Undervalued"


def implied_cagr(fcf, cash, debt, shares, price):
    """Solve for the 10-yr FCF growth that makes IV == price (bisection)."""
    f = lambda g: intrinsic_value_per_share(fcf, cash, debt, shares, g, IMPLIED_TERMINAL) - price
    lo, hi = -0.50, 1.00
    if f(lo) > 0:
        return lo        # price is below even -50%/yr: market expects collapse
    if f(hi) < 0:
        return None      # would need >100%/yr: report as off the charts
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def actual_cagr(fcf_by_year: dict[int, float | None], years=ACTUAL_CAGR_YEARS):
    ys = sorted(y for y, v in fcf_by_year.items() if v is not None)
    if not ys:
        return None, "No FCF history"
    end_y = ys[-1]
    start_y = end_y - years
    start, end = fcf_by_year.get(start_y), fcf_by_year[end_y]
    if start is None:
        return None, f"No FCF for {start_y}"
    if start <= 0 or end <= 0:
        return None, f"FCF negative in {start_y} or {end_y}"
    return (end / start) ** (1 / years) - 1, None


@dataclass
class Inputs:
    ticker: str
    name: str = ""
    price: float | None = None
    fcf: float | None = None          # latest FCF (TTM if available)
    fcf_source: str = ""
    cash: float = 0.0
    debt: float = 0.0
    shares: float | None = None
    roic_by_year: dict = field(default_factory=dict)
    fcf_by_year: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def analyze(x: Inputs) -> dict:
    row = {"ticker": x.ticker, "name": x.name, "price": x.price, "notes": list(x.notes),
           "roic_history": dict(sorted(x.roic_by_year.items())),
           "fcf_history": dict(sorted(x.fcf_by_year.items())),
           "cash": x.cash, "debt": x.debt, "shares": x.shares, "by_year": x.detail}
    row.update(roic_test(x.roic_by_year) if x.roic_by_year else
               {"roic_test": "Insufficient data", "roic_7yr_avg": None})
    row.update(erosion_test(x.roic_by_year) if x.roic_by_year else
               {"erosion_test": "Insufficient data", "erosion_ratio": None})
    if row.get("roic_blank_years"):
        row["notes"].append("Blank ROIC yrs " + ",".join(map(str, row["roic_blank_years"]))
                            + " (often negative equity from buybacks — review)")

    cagr, why = actual_cagr(x.fcf_by_year)
    row["actual_fcf_cagr_5y"] = cagr
    if why:
        row["notes"].append("5-yr CAGR: " + why)

    row["valuation"], row["margin_of_safety"], row["implied_fcf_cagr_10y"] = "N/A", None, None
    for s in SCENARIOS:
        row[f"iv_{s}"] = None
    if x.price is None or x.shares in (None, 0) or x.fcf is None:
        row["notes"].append("Missing price, shares or FCF — valuation skipped")
        return row
    if x.fcf <= 0:
        row["valuation"] = "N/A (negative FCF)"
        return row

    ivs = {s: intrinsic_value_per_share(x.fcf, x.cash, x.debt, x.shares, g, t)
           for s, (g, t) in SCENARIOS.items()}
    for s, v in ivs.items():
        row[f"iv_{s}"] = v
    row["valuation"] = bucket(x.price, ivs["bear"], ivs["base"], ivs["bull"])
    row["margin_of_safety"] = (ivs["base"] - x.price) / ivs["base"] if ivs["base"] > 0 else None
    ic = implied_cagr(x.fcf, x.cash, x.debt, x.shares, x.price)
    row["implied_fcf_cagr_10y"] = ic
    if ic is None:
        row["notes"].append("Implied growth >100%/yr")
    row["fcf_used"] = x.fcf
    row["fcf_source"] = x.fcf_source
    return row
