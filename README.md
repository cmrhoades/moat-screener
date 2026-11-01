# Moat Screener

Upload a spreadsheet of tickers → get one table with, for each stock:

| Column | Rule |
|---|---|
| ROIC test | Average ROIC over the last 7 fiscal years ≥ 10% |
| Erosion test | Avg ROIC of the most recent 5 yrs ≥ 0.80 × avg of the prior 5 yrs |
| Valuation | Price vs. bear/base/bull DCF (8% discount; 3%/7%/9.5% growth; 2%/2.5%/3% terminal) |
| Market-implied 10-yr FCF CAGR | Growth today's price requires (8% discount, 2.5% terminal) |
| Actual 5-yr FCF CAGR | Free cash flow growth over the last 5 fiscal years |

**Free data, no account:** financials from SEC EDGAR (companies' 10-K filings), prices from Yahoo Finance (Stooq as backup). The only thing you enter is your email — the SEC asks every user of its data to identify themselves.

## Use it

- **Streamlit Cloud:** deploy from your GitHub repo with main file `app.py`. Optional: add `SEC_CONTACT = "you@email.com"` under Settings → Secrets so you don't retype it.
- **On your computer:** `pip install -r requirements.txt` then `streamlit run app.py`.

Upload a `.csv` or `.xlsx` with a `Ticker` column (see `sample_tickers.csv`). Optional `Price` column overrides fetched prices.

## How the numbers are built

- **ROIC** = operating income × (1 − tax rate) ÷ (total debt + shareholders' equity), each fiscal year. Cash isn't subtracted, so cash-rich companies aren't flattered. Tax rate is clamped 0–50% to neutralize one-offs (e.g. 2017 Tax Act).
- **Blank ROIC years** = equity so negative from buybacks that invested capital ≤ 0. Skipped in averages and flagged in Notes — review manually, don't auto-reject.
- **FCF** = operating cash flow − capital expenditures, latest fiscal year.
- **Cash** = cash + short-term investments; **debt** = short + long-term debt (excl. operating leases). Debt is subtracted inside intrinsic value.
- Negative FCF → valuation N/A. Negative FCF at either end of the 5-yr window → actual CAGR N/A.
- Numbers can differ from roic.ai, Morningstar etc. because each defines ROIC slightly differently.

## Limits

- US SEC filers only (S&P 500 / Nasdaq-100 are fine). Foreign companies filing 20-F won't work.
- Banks/insurers: the ROIC/FCF model doesn't fit them; ignore their output.
- Some companies tag their filings unusually — gaps show as "Insufficient data" or a Note.
- ~1 second per stock; 500 stocks ≈ 10 minutes.

## Change the rules

All thresholds live at the top of `engine/analysis.py`.

## Tests

`python -m pytest tests -q` — offline; checks the math against hand-calculated results (e.g. VEEV IVs $194.27 / $263.13 / $330.55).
