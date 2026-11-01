"""Moat Screener — upload a spreadsheet of tickers, get the ROIC + valuation table.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

from engine import analysis as A
from engine.data import SecClient, fetch, friendly_error

st.set_page_config(page_title="Moat Screener", page_icon="📈", layout="wide")
st.title("Moat Screener")
st.caption(f"ROIC 7-yr avg ≥ {A.ROIC_AVG_MIN:.0f}% · Erosion: recent 5-yr avg ≥ "
           f"{A.EROSION_RATIO_MIN:.0%} of prior 5 · DCF at {A.DISCOUNT:.0%} · "
           "Data: SEC filings + Yahoo Finance (free)")


def _secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


with st.sidebar:
    st.header("Setup")
    contact = st.text_input("Your email", value=os.environ.get("SEC_CONTACT") or _secret("SEC_CONTACT"),
                            help="The SEC asks anyone using its free data to identify themselves "
                                 "with an email. It's only sent to sec.gov.")
    st.caption("No account or payment needed. Financials come from companies' 10-K filings "
               "on SEC EDGAR; prices from Yahoo Finance.")

# ---- upload ----------------------------------------------------------------
up = st.file_uploader("Upload a spreadsheet of tickers (.csv or .xlsx)", type=["csv", "xlsx", "xls"])
st.caption("Put tickers in a column named **Ticker** or **Symbol** (otherwise the first column is used). "
           "Optional: add a **Price** column to use your own prices instead of fetching them.")


def read_tickers(file) -> tuple[list[str], dict]:
    df = pd.read_csv(file) if file.name.lower().endswith(".csv") else pd.read_excel(file)
    cols = {str(c).lower().strip(): c for c in df.columns}
    col = cols.get("ticker") or cols.get("symbol") or df.columns[0]
    df = df.dropna(subset=[col])
    df["_t"] = df[col].astype(str).str.strip().str.upper()
    df = df[df["_t"].str.len().between(1, 20)].drop_duplicates("_t")
    prices = {}
    if "price" in cols:
        p = pd.to_numeric(df[cols["price"]], errors="coerce")
        prices = {t: float(v) for t, v in zip(df["_t"], p) if pd.notna(v) and v > 0}
    return list(df["_t"]), prices


tickers, price_overrides = read_tickers(up) if up else ([], {})
if tickers:
    st.write(f"**{len(tickers)} tickers:** " + ", ".join(tickers[:40]) + (" …" if len(tickers) > 40 else ""))

run = st.button("Run analysis", type="primary", disabled=not (tickers and "@" in (contact or "")))
if tickers and "@" not in (contact or ""):
    st.info("Enter your email in the sidebar to run.")

if run:
    sec = SecClient(contact)
    rows, bar, status = [], st.progress(0.0), st.empty()
    for i, t in enumerate(tickers, 1):
        status.write(f"Analyzing **{t}** ({i}/{len(tickers)})…")
        try:
            rows.append(A.analyze(fetch(sec, t, price_overrides.get(t))))
        except Exception as e:  # one bad ticker never stops the run
            rows.append({"ticker": t, "valuation": "Error", "notes": [friendly_error(e)]})
        bar.progress(i / len(tickers))
    status.write(f"Done — {len(rows)} stocks analyzed.")
    st.session_state["results"] = rows
    st.session_state["run_at"] = pd.Timestamp.now().strftime("%b %d, %Y %I:%M %p")

# ---- results ---------------------------------------------------------------
VAL_ORDER = ["Strongly Undervalued", "Probably Undervalued", "Fairly Valued",
             "Probably Overvalued", "Strongly Overvalued"]

def build_table(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in ["name", "price", "roic_7yr_avg", "roic_test", "erosion_ratio", "erosion_test",
              "valuation", "margin_of_safety", "iv_bear", "iv_base", "iv_bull",
              "implied_fcf_cagr_10y", "actual_fcf_cagr_5y", "fcf_source", "notes"]:
        if c not in df:
            df[c] = None
    df["notes"] = df["notes"].apply(lambda n: "; ".join(n) if isinstance(n, list) else (n or ""))
    df["_rank"] = df["valuation"].apply(lambda v: VAL_ORDER.index(v) if v in VAL_ORDER else 9)
    df = df.sort_values(["_rank", "margin_of_safety"], ascending=[True, False])
    out = pd.DataFrame({
        "Ticker": df["ticker"],
        "Name": df["name"],
        "Price": df["price"],
        "ROIC 7-yr avg %": df["roic_7yr_avg"],
        "ROIC test (≥10%)": df["roic_test"],
        "Erosion ratio": df["erosion_ratio"],
        "Erosion test": df["erosion_test"],
        "Valuation": df["valuation"],
        "Margin of safety %": df["margin_of_safety"] * 100,
        "IV bear": df["iv_bear"], "IV base": df["iv_base"], "IV bull": df["iv_bull"],
        "Market-implied 10-yr FCF CAGR %": df["implied_fcf_cagr_10y"] * 100,
        "Actual 5-yr FCF CAGR %": df["actual_fcf_cagr_5y"] * 100,
        "FCF basis": df["fcf_source"],
        "Notes": df["notes"],
    })
    return out.reset_index(drop=True)


def _color(v):
    return {"Pass": "background-color:#d9f2e3", "Fail": "background-color:#f8d7da",
            "Strongly Undervalued": "background-color:#b7e4c7", "Probably Undervalued": "background-color:#d9f2e3",
            "Fairly Valued": "background-color:#fff3cd", "Probably Overvalued": "background-color:#fde2c8",
            "Strongly Overvalued": "background-color:#f8d7da"}.get(v, "")


if "results" in st.session_state:
    table = build_table(st.session_state["results"])
    st.subheader(f"Results · {st.session_state['run_at']}")

    c1, c2, c3 = st.columns(3)
    both = table[(table["ROIC test (≥10%)"] == "Pass") & (table["Erosion test"] == "Pass")]
    c1.metric("Pass both ROIC tests", f"{len(both)} / {len(table)}")
    c2.metric("…and undervalued", int(both["Valuation"].isin(VAL_ORDER[:2]).sum()))
    c3.metric("…and fairly valued", int((both["Valuation"] == "Fairly Valued").sum()))

    only_pass = st.toggle("Show only stocks passing both ROIC tests")
    view = both if only_pass else table

    money = {c: "${:,.2f}" for c in ["Price", "IV bear", "IV base", "IV bull"]}
    pct = {c: "{:,.1f}%" for c in ["ROIC 7-yr avg %", "Margin of safety %",
                                    "Market-implied 10-yr FCF CAGR %", "Actual 5-yr FCF CAGR %"]}
    styled = (view.style.format({**money, **pct, "Erosion ratio": "{:.2f}x"}, na_rep="—")
              .map(_color, subset=["ROIC test (≥10%)", "Erosion test", "Valuation"]))
    st.dataframe(styled, width="stretch", hide_index=True, height=min(600, 40 + 35 * len(view)))

    st.caption("**Margin of safety** = (base IV − price) ÷ base IV; positive = cheaper than base case. "
               "**Market-implied CAGR** = the 10-yr FCF growth today's price requires (8% discount, 2.5% terminal). "
               "If it's above the actual 5-yr CAGR, the market expects acceleration.")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        table.to_excel(xw, index=False, sheet_name="Results")
    st.download_button("Download results (.xlsx)", buf.getvalue(),
                       file_name=f"moat_screener_{pd.Timestamp.now():%Y%m%d}.xlsx")

    with st.expander("Per-stock detail (ROIC & FCF history)"):
        pick = st.selectbox("Stock", [r["ticker"] for r in st.session_state["results"]])
        r = next(r for r in st.session_state["results"] if r["ticker"] == pick)
        st.json({k: v for k, v in r.items() if k not in ("notes",)} | {"notes": r.get("notes", [])},
                expanded=False)
