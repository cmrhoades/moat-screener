"""Free data: SEC EDGAR (financials) + Yahoo Finance / Stooq (price). No account or key.

SEC asks every visitor to identify themselves with a contact email in the
request header — that's the only thing you need to provide.

ROIC is computed from the raw 10-K filings:
    NOPAT = operating income x (1 - income tax / pretax income)
    Invested capital = total debt + shareholders' equity     (year-end)
    ROIC = NOPAT / invested capital
Cash is NOT subtracted from invested capital, so cash-rich companies aren't
flattered (e.g. Veeva comes out ~10%, close to roic.ai's own figure).
When equity is so negative that invested capital <= 0 (heavy buybacks),
the year is left blank and flagged for manual review.
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
from datetime import date

from .analysis import Inputs

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# First concept found wins. Order = preference.
CONCEPTS = {
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"],
    "ebit": ["OperatingIncomeLoss"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "st_inv": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
               "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "OtherShortTermInvestments"],
    "debt_total": ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
                        "LongTermDebtAndFinanceLeaseLiabilitiesNoncurrent", "LongTermNotesPayable",
                        "SeniorLongTermNotes", "ConvertibleNotesPayableNoncurrent"],
    "debt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                     "ConvertibleNotesPayableCurrent"],
    "debt_current_all": ["DebtCurrent"],
    "st_borrow": ["ShortTermBorrowings", "CommercialPaper"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
DURATION = {"ocf", "capex", "ebit", "pretax", "tax", "net_income", "interest", "shares_diluted"}


# ---- HTTP ------------------------------------------------------------------
def _get(url: str, contact: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"MoatScreener/2.0 {contact}", "Accept-Encoding": "identity"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


class SecClient:
    """Thin SEC client. Respects SEC's 10 requests/second limit."""
    def __init__(self, contact: str, getter=_get):
        if "@" not in (contact or ""):
            raise ValueError("SEC requires a contact email")
        self.contact, self._get, self._last = contact, getter, 0.0
        self._tickers = None

    def _fetch_json(self, url):
        wait = 0.12 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        return json.loads(self._get(url, self.contact))

    def lookup(self, ticker: str):
        if self._tickers is None:
            raw = self._fetch_json(SEC_TICKERS)
            self._tickers = {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in raw.values()}
        t = ticker.upper().split(":")[-1].replace(".", "-")
        if t not in self._tickers:
            raise LookupError(f"'{ticker}' isn't an SEC-registered US filer")
        return self._tickers[t]

    def facts(self, cik: int):
        return self._fetch_json(SEC_FACTS.format(cik=cik))


# ---- parsing SEC XBRL facts --------------------------------------------------
def _d(s):
    return date.fromisoformat(s)


def _annual_series(facts: dict, concept: str, duration: bool) -> dict[date, float]:
    """{period end date: value} from 10-K filings; latest filing wins for restatements."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return {}
    unit = "shares" if concept.startswith("WeightedAverage") else "USD"
    out, filed = {}, {}
    for f in node.get("units", {}).get(unit, []):
        if not str(f.get("form", "")).startswith("10-K"):
            continue
        if duration:
            if "start" not in f:
                continue
            days = (_d(f["end"]) - _d(f["start"])).days
            if not 350 <= days <= 380:
                continue
        end = _d(f["end"])
        if end not in filed or f.get("filed", "") >= filed[end]:
            out[end], filed[end] = float(f["val"]), f.get("filed", "")
    return out


def _pick(facts, key):
    for c in CONCEPTS[key]:
        s = _annual_series(facts, c, key in DURATION)
        if s:
            return s
    return {}


def _near(series: dict[date, float], target: date, tol=10):
    best = None
    for d, v in series.items():
        if abs((d - target).days) <= tol and (best is None or abs((d - target).days) < best[0]):
            best = (abs((d - target).days), v)
    return None if best is None else best[1]


def parse_facts(facts: dict) -> dict:
    """Return per-fiscal-year metrics keyed by fiscal year (year of the FY end date)."""
    s = {k: _pick(facts, k) for k in CONCEPTS}
    fy_ends = sorted(set(s["ocf"]) | set(s["ebit"]))
    years = {}
    for end in fy_ends:
        g = lambda k: _near(s[k], end)
        ocf, capex = g("ocf"), g("capex")
        fcf = None if ocf is None else ocf - abs(capex or 0)

        # debt: prefer components, fall back to totals
        noncur, cur, cur_all, stb, tot = (g("debt_noncurrent"), g("debt_current"),
                                         g("debt_current_all"), g("st_borrow"), g("debt_total"))
        if cur_all is not None:
            current = cur_all                      # DebtCurrent already includes ST borrowings
        else:
            current = (cur or 0) + (stb or 0)
        if noncur is not None:
            debt = noncur + current
        elif tot is not None:                      # LongTermDebt includes its current portion
            debt = tot + (stb or 0)
        else:
            debt = current

        ebit, pretax, tax, eq = g("ebit"), g("pretax"), g("tax"), g("equity")
        if ebit is None and pretax is not None:    # some filers don't tag operating income
            ebit = pretax + (g("interest") or 0)
        roic = None
        if ebit is not None and eq is not None:
            rate = (tax / pretax) if (tax is not None and pretax and pretax > 0) else 0.21
            rate = min(max(rate, 0.0), 0.5)        # clamp one-offs (e.g. 2017 Tax Act)
            ic = debt + eq
            if ic > 0:
                roic = ebit * (1 - rate) / ic * 100
        years[end.year] = {
            "end": end, "fcf": fcf, "capex_missing": capex is None, "roic": roic,
            "cash": (g("cash") or 0) + (g("st_inv") or 0), "debt": debt,
            "shares": g("shares_diluted"), "equity": eq,
        }
    return years


# ---- prices ------------------------------------------------------------------
def price_yahoo(ticker: str):
    import yfinance as yf
    p = yf.Ticker(ticker.replace(".", "-")).fast_info.get("last_price")
    return float(p) if p else None


def price_stooq(ticker: str, contact: str = ""):
    raw = _get(f"https://stooq.com/q/l/?s={ticker.lower().replace('.', '-')}.us&f=sd2t2ohlcv&h&e=csv",
               contact).decode()
    rows = list(__import__("csv").DictReader(io.StringIO(raw)))
    c = rows[0].get("Close") if rows else None
    return float(c) if c not in (None, "", "N/D") else None


def get_price(ticker: str, contact: str):
    for fn in (lambda: price_yahoo(ticker), lambda: price_stooq(ticker, contact)):
        try:
            p = fn()
            if p:
                return p
        except Exception:
            continue
    return None


# ---- main entry --------------------------------------------------------------
def fetch(sec: SecClient, ticker: str, price_override: float | None = None, price_fn=None) -> Inputs:
    t = ticker.strip().upper().split(":")[-1]
    cik, name = sec.lookup(t)
    facts = sec.facts(cik)
    yrs = parse_facts(facts)
    x = Inputs(ticker=t, name=name)
    if not yrs:
        x.notes.append("No US-GAAP annual data in SEC filings (foreign or non-standard filer)")
        return x
    recent = sorted(yrs)[-11:]
    x.roic_by_year = {y: yrs[y]["roic"] for y in recent}
    x.fcf_by_year = {y: yrs[y]["fcf"] for y in recent}
    last = yrs[recent[-1]]
    x.fcf, x.fcf_source = last["fcf"], f"FY{recent[-1]}"
    x.cash, x.debt, x.shares = last["cash"], last["debt"], last["shares"]
    if last["capex_missing"]:
        x.notes.append("Capex not found in filings — FCF may be overstated")
    if last["shares"] is None:
        x.notes.append("Diluted share count not found")
    if (date.today() - last["end"]).days > 550:
        x.notes.append(f"Latest 10-K is old (FY ending {last['end']})")
    x.price = price_override if price_override else (price_fn or get_price)(t, sec.contact)
    if price_override:
        x.notes.append("Price from your spreadsheet")
    elif x.price is None:
        x.notes.append("Couldn't fetch price — add a Price column to your spreadsheet")
    return x


def friendly_error(e: Exception) -> str:
    code = getattr(e, "code", None)
    if code == 403:
        return "SEC blocked the request — check the contact email in the sidebar"
    if code == 404:
        return "No SEC financial data for this company"
    return str(e)[:150]
