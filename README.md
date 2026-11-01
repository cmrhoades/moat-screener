# Moat Screener

Upload a spreadsheet of tickers → get one table with, for each stock:

| Column | Rule |
|---|---|
| ROIC test | Average ROIC over the last 7 fiscal years ≥ 10% |
| Erosion test | Avg ROIC of the most recent 5 yrs ≥ 0.80 × avg of the prior 5 yrs |
| Valuation | Price vs. bear/base/bull DCF (8% discount; 3%/7%/9.5% growth; 2%/2.5%/3% terminal) |
| Market-implied 10-yr FCF CAGR | Growth today's price requires (8% discount, 2.5% terminal) |
| Actual 5-yr FCF CAGR | Free cash flow growth over the last 5 fiscal years |

Data comes from the official roic.ai API.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Paste your roic.ai API key in the sidebar, upload a `.csv` or `.xlsx` with a `Ticker` column (see `sample_tickers.csv`), click **Run analysis**, and download the results as Excel.

To avoid pasting the key each time, set it once:
- **Locally:** `export ROICAI_API_KEY=your_key` before running, or
- **Streamlit Cloud:** add `ROICAI_API_KEY = "your_key"` under App → Settings → Secrets.

## roic.ai plan

The erosion test needs 10 years of ROIC history, so you need a plan with at least ~11 years of data (Premium, $49/mo personal). On the free plan (2 years) and the 5-year plans, the ROIC tests show **Insufficient data**; valuation and implied CAGR still work. Each stock uses ~7 API calls.

## Notes on the numbers

- **FCF used for valuation:** trailing-12-months if your plan has it, otherwise the latest fiscal year (shown in "FCF basis").
- **Cash** = cash + short-term investments; **debt** = short + long-term borrowings, from the latest balance sheet. Debt is subtracted inside intrinsic value.
- **Blank ROIC years** are skipped in averages and flagged in Notes — usually negative equity from buybacks (review manually, don't auto-reject).
- Negative FCF → valuation shows N/A. Negative FCF at either end of the 5-yr window → actual CAGR shows N/A.
- Banks/insurers: the FCF/ROIC model doesn't fit them; ignore their output.

## Change the rules

All thresholds live at the top of `engine/analysis.py`.

## Tests

`python -m pytest tests -q` — offline; checks the math against hand-calculated results (e.g. VEEV IVs $194.27 / $263.13 / $330.55).
