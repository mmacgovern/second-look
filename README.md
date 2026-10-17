# Second Look

A Streamlit app that gives an AI second opinion on an investment portfolio. All
numbers are computed in Python; the AI only explains them. The app never tells
anyone to buy, sell or hold a specific security.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
.venv/Scripts/pre-commit install                 # gitleaks secret scan on every commit
cp .env.example .env                             # then add your Portkey key to .env
.venv/Scripts/python -m pytest
```

Without a `PORTKEY_API_KEY`, the app runs in demo mode and replays recorded AI
responses from `demo/recorded/`.

## Sample portfolios

`data/sample_portfolios/` holds three made-up portfolios in a common brokerage
export format (`Symbol, Description, Quantity, Price, Market Value, Account Number`).
Prices are closes from 2026-10-16. Account numbers (`DEMO-0000-…`) are fake.

| File | Size | What it shows |
|---|---|---|
| `beginner.csv` | ~$15k | A target-date fund (VTTSX) plus a few popular stocks |
| `midcareer.csv` | ~$181k | Overlapping funds (VOO, VTI, QQQ, XLK) plus big-tech stocks: hidden concentration in the same few companies |
| `retiree.csv` | ~$657k | Stock and bond index funds, dividend stocks, and a high-cost active fund (GFACX, 1.35% expense ratio) |

## Market data: live first, snapshot fallback

`secondlook/market_data.py` fetches from yfinance first. If a live request
fails or comes back empty, it falls back to the offline snapshot in
`data/snapshot/`. Every result reports its source (`live`, `snapshot` or
`missing`), so the app can say which data it used.

Rebuild the snapshot (about a minute; needs internet):

```bash
.venv/Scripts/python scripts/build_snapshot.py
```

It covers every ticker in the sample portfolios plus these crash-replay
proxies with long histories: `^GSPC`, `^IXIC`, `SPY`, `QQQ`, `IWM`, `EFA`,
`AGG`, `TLT`, `GLD`.

```
data/snapshot/
  manifest.json            build time, per-ticker row counts and date ranges, failures
  prices/<TICKER>.parquet  daily close and adjusted close, 2007-01-01 to build date
  profiles/<TICKER>.json   name, type, sector, expense ratio, holdings, sector weights
```

Index tickers are saved with `^` replaced by `_` (for example `_GSPC.parquet`).
A fund launched after 2007 (for example VOO in 2010) starts at its first trading
day. Crash replays use the proxies for earlier dates.

### Which fields come from top holdings only

yfinance gives only a fund's **largest positions**, usually the top 10, not its
full holdings list. So:

| Field | Coverage |
|---|---|
| `top_holdings` (symbol, name, weight) | **Top holdings only.** For example, XLK's top 10 make up about 65% of the fund, VOO's about 39% and VTI's about 34%. Weights do not sum to 100%. |
| Look-through exposure to a single company (for example "how much NVDA do I really own across VOO + QQQ + XLK") | **Top holdings only.** Built from `top_holdings`, so it is a floor: the true exposure can be higher, never lower. |
| `sector_weights` | Whole fund (reported by the fund) |
| `asset_classes` (stock, bond, cash share) | Whole fund |
| `expense_ratio` | Whole fund (annual report figure, stored as a fraction: 0.0003 = 0.03%) |

Some funds report no holdings or sectors at all. For example, BND, TLT and GLD
have no top holdings, and AGG lists only one. Target-date funds such as VTTSX
list their underlying funds rather than stocks.

## Personas and reference data

`data/sample_portfolios/personas.json` gives each sample a made-up investor:
age, retirement age, desired income, Social Security estimate, yearly
contribution, account types and risk-quiz answers. Load one with
`secondlook.quant.profile.load_persona("midcareer")`.

`data/reference/glide_path.json` is a **placeholder**. Fill in the stock share
by years to retirement from a cited target-date fund family, add the source,
and set `is_placeholder` to `false`. Until then the app says the reference
glide path is "not filled in yet" instead of guessing.

Planning assumptions (withdrawal rate, real returns, flat tax rate on pre-tax
accounts) live in `ASSUMPTIONS` in `secondlook/quant/plan.py`, and every one
is repeated as a labeled value in the Facts JSON.
