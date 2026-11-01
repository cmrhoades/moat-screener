"""Pulls what the analysis needs from the official roic.ai API (pip: roicai).

~7 API calls per stock. Needs a roic.ai plan with >= 11 years of history
for the erosion test (Premium/Professional). On the free plan you'll get
'Insufficient data' for the ROIC tests because history is capped at 2 years.
"""
from __future__ import annotations

from roicai import ApiError, Roicai

from .analysis import Inputs

US_EXCHANGES = ["NASDAQ", "NYSE", "NYSE ARCA", "NYSEARCA", "AMEX", "NYSE AMERICAN", "BATS", "CBOE"]


def _items(pager):
    return list(getattr(pager, "items", None) or [])


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def resolve(client: Roicai, ticker: str) -> tuple[str, str]:
    """Bare 'AAPL' -> ('NASDAQ:AAPL', 'Apple Inc.'). 'NYSE:LOW' passes through."""
    t = ticker.strip().upper()
    if ":" in t:
        try:
            info = client.tickers.retrieve(t)
            return t, getattr(info, "name", "") or ""
        except ApiError:
            return t, ""
    res = _items(client.tickers.search(query=t, search_by="symbol", limit=10))
    exact = [r for r in res if (r.symbol or "").upper() == t]
    if not exact:
        raise LookupError(f"roic.ai has no ticker '{t}'")
    def rank(r):
        ex = (r.exchange or "").upper()
        return (0 if ex in US_EXCHANGES else 1, 0 if r.is_primary else 1)
    best = sorted(exact, key=rank)[0]
    return f"{best.exchange}:{best.symbol}", best.name or ""


def fetch(client: Roicai, ticker: str) -> Inputs:
    ident, name = resolve(client, ticker)
    x = Inputs(ticker=ticker.strip().upper(), name=name)

    # ROIC history (percent values, e.g. 46.9)
    prof = _items(client.fundamentals.ratios.profitability.list(
        ident, period_type="annual", order="desc", limit=12))
    x.roic_by_year = {p.fiscal_year: _num(p.return_on_inv_capital) for p in prof if p.fiscal_year}

    # FCF history (annual) + latest FCF (TTM preferred)
    cfa = _items(client.fundamentals.cash_flow_statements.list(
        ident, period_type="annual", order="desc", limit=12))
    x.fcf_by_year = {c.fiscal_year: _num(c.cf_free_cash_flow) for c in cfa if c.fiscal_year}
    latest_cf = cfa[0] if cfa else None
    try:
        ttm = _items(client.fundamentals.cash_flow_statements.list(
            ident, period_type="ttm", order="desc", limit=1))
        if ttm and _num(ttm[0].cf_free_cash_flow) is not None:
            latest_cf, x.fcf_source = ttm[0], "TTM"
    except ApiError:
        pass
    if latest_cf is not None:
        x.fcf = _num(latest_cf.cf_free_cash_flow)
        x.fcf_source = x.fcf_source or f"FY{latest_cf.fiscal_year}"

    # Cash & debt: latest quarter if the plan allows, else latest fiscal year
    bs = None
    for ptype in ("quarterly", "annual"):
        try:
            got = _items(client.fundamentals.balance_sheets.list(
                ident, period_type=ptype, order="desc", limit=1))
            if got:
                bs = got[0]
                break
        except ApiError:
            continue
    if bs is not None:
        x.cash = (_num(bs.bs_cash_near_cash_item) or 0) + (_num(bs.bs_mkt_sec_other_st_invest) or 0)
        x.debt = (_num(bs.bs_st_borrow) or 0) + (_num(bs.bs_lt_borrow) or 0)

    # Diluted shares (latest fiscal year)
    inc = _items(client.fundamentals.income_statements.list(
        ident, period_type="annual", order="desc", limit=1))
    if inc:
        x.shares = _num(inc[0].is_sh_for_diluted_eps) or _num(inc[0].is_avg_num_sh_for_eps)

    # Unit safety net: shares and dollar amounts must be in the same scale.
    # Cross-check against roic.ai's own FCF-per-share; rescale by 1000s if off.
    if x.shares and latest_cf is not None:
        fps = _num(getattr(latest_cf, "free_cash_flow_per_sh", None))
        f = _num(latest_cf.cf_free_cash_flow)
        if fps and f and fps * f > 0:
            ratio = (f / x.shares) / fps
            for k in (1e-9, 1e-6, 1e-3, 1e3, 1e6, 1e9):
                if 0.5 * k < ratio < 2 * k:
                    x.shares *= k
                    x.notes.append("Share units auto-rescaled")
                    break

    x.price = _num(client.stock_prices.latest.retrieve(ident).close)
    return x


def make_client(api_key: str) -> Roicai:
    return Roicai(api_key=api_key, max_retries=4, timeout=60,
                  app_info={"name": "moat-screener", "version": "2.0"})


def friendly_error(e: Exception) -> str:
    code = getattr(e, "status_code", None)
    return {
        401: "API key invalid",
        402: "Your roic.ai plan doesn't include this data",
        403: "Your roic.ai plan's history limit blocks this request",
        404: "Ticker not found on roic.ai",
        429: "Rate limit hit — try again shortly",
    }.get(code, str(e)[:120])
