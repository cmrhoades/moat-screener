"""Run: python -m pytest tests -q   (offline — uses a fake roic.ai client)."""
from types import SimpleNamespace as NS

from engine import analysis as A
from engine.data import fetch


def test_veev_matches_earlier_manual_run():
    # Same inputs we used by hand in Aug 2026: bear 194.27 / base 263.13 / bull 330.55
    x = A.Inputs("VEEV", price=186.24, fcf=1415, cash=6561, debt=96, shares=167)
    r = A.analyze(x)
    assert round(r["iv_bear"], 2) == 194.27
    assert round(r["iv_base"], 2) == 263.13
    assert round(r["iv_bull"], 2) == 330.55
    assert r["valuation"] == "Strongly Undervalued"


def test_implied_cagr_matches_earlier():
    # FIX at $1,733.60 implied 17.2%; VEEV at $234.69 implied 5.3%
    assert round(A.implied_cagr(1030, 982, 483, 35, 1733.60) * 100, 1) == 17.2
    assert round(A.implied_cagr(1415, 6561, 96, 167, 234.69) * 100, 1) == 5.3


def test_roic_and_erosion():
    good = {y: 20.0 for y in range(2016, 2026)}
    assert A.roic_test(good)["roic_test"] == "Pass"
    assert A.erosion_test(good)["erosion_test"] == "Pass"
    eroding = {**{y: 30.0 for y in range(2016, 2021)}, **{y: 15.0 for y in range(2021, 2026)}}
    assert A.roic_test(eroding)["roic_test"] == "Pass"          # 7-yr avg ~19%
    assert A.erosion_test(eroding)["erosion_test"] == "Fail"    # 15 < 0.8*30
    weak = {y: 8.0 for y in range(2016, 2026)}
    assert A.roic_test(weak)["roic_test"] == "Fail"
    short = {2024: 20.0, 2025: 22.0}                            # free-plan history
    assert A.roic_test(short)["roic_test"] == "Insufficient data"
    assert A.erosion_test(short)["erosion_test"] == "Insufficient data"


def test_actual_cagr():
    fix = {2020: 262, 2021: 158, 2022: 253, 2023: 545, 2024: 738, 2025: 1031}
    assert round(A.actual_cagr(fix)[0] * 100, 1) == 31.5      # matches earlier FIX calc
    assert A.actual_cagr({2020: -5, 2025: 100})[0] is None


def test_negative_fcf_skips_valuation():
    r = A.analyze(A.Inputs("X", price=10, fcf=-5, shares=1))
    assert r["valuation"] == "N/A (negative FCF)"


class FakeClient:
    """Mimics the roicai client shape with canned data."""
    def __init__(self, share_scale=1.0):
        page = lambda items: NS(items=items)
        yrs = range(2015, 2026)
        self.tickers = NS(
            search=lambda **k: page([NS(symbol="TST", exchange="NASDAQ", is_primary=True, name="Test Co")]),
            retrieve=lambda i: NS(name="Test Co"))
        prof = [NS(fiscal_year=y, return_on_inv_capital=25.0) for y in reversed(yrs)]
        cfa = [NS(fiscal_year=y, cf_free_cash_flow=100.0 * 1.1 ** (y - 2015),
                  free_cash_flow_per_sh=100.0 * 1.1 ** (y - 2015) / 10) for y in reversed(yrs)]
        def cf_list(ident, period_type, **k):
            if period_type == "ttm":
                return page([NS(fiscal_year=2026, cf_free_cash_flow=270.0, free_cash_flow_per_sh=27.0)])
            return page(cfa)
        bs = NS(bs_cash_near_cash_item=50.0, bs_mkt_sec_other_st_invest=10.0, bs_st_borrow=20.0, bs_lt_borrow=80.0)
        self.fundamentals = NS(
            ratios=NS(profitability=NS(list=lambda *a, **k: page(prof))),
            cash_flow_statements=NS(list=cf_list),
            balance_sheets=NS(list=lambda *a, **k: page([bs])),
            income_statements=NS(list=lambda *a, **k: page([NS(is_sh_for_diluted_eps=10.0 * share_scale,
                                                               is_avg_num_sh_for_eps=10.0)])))
        self.stock_prices = NS(latest=NS(retrieve=lambda i: NS(close=400.0)))


def test_fetch_and_analyze_end_to_end():
    x = fetch(FakeClient(), "tst")
    assert x.fcf == 270.0 and x.fcf_source == "TTM"
    assert x.cash == 60.0 and x.debt == 100.0 and x.shares == 10.0 and x.price == 400.0
    r = A.analyze(x)
    assert r["roic_test"] == "Pass" and r["erosion_test"] == "Pass"
    assert round(r["actual_fcf_cagr_5y"] * 100, 1) == 10.0
    assert r["valuation"] in {"Strongly Undervalued", "Probably Undervalued", "Fairly Valued",
                              "Probably Overvalued", "Strongly Overvalued"}


def test_share_unit_mismatch_is_rescaled():
    x = fetch(FakeClient(share_scale=1e6), "tst")   # shares reported in raw units
    assert abs(x.shares - 10.0) < 1e-9
    assert "Share units auto-rescaled" in x.notes
