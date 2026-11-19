# NSE Bonds (web app)

Live, honest NSE bond data. Rewrite of the old `upstoxbaji2` scripts as a web app.

## Layered design

Every layer has one job and one source. Nothing is shown without saying where it came from.

| Layer | Data | Source | Module |
|---|---|---|---|
| 1. Universe | Which bonds are listed | NSE/BSE daily debt bhavcopy | `app/sources/terms_csv.py` (CSV seam) |
| 2. Terms | Coupon, frequency, maturity, face value, rating | SEBI/depository database (Bond Central) | `app/sources/terms_csv.py` (CSV seam) |
| 3. Live quotes | LTP, 5-level bid/ask | Upstox stream (broker feed) | `app/sources/upstox_live.py` |
| 4. Computed | YTM from price + terms | This app (`app/yieldcalc.py`) | `app/service.py` |

Each bond is returned with a status: `LIVE`, `STALE` (no recent tick or no depth) or `NO_QUOTE`.
Stale or missing quotes are never shown as live.

**Known limits:** Upstox is not an official exchange data product, and no free official live feed
exists. For production, replace layer 3 with NSE Data & Analytics' corporate-bond feed (same
`Quote` model). Layers 1-2 have no confirmed public API yet, so they load from `data/bonds.csv`.

## Run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # add UPSTOX_ACCESS_TOKEN
copy data\bonds.example.csv data\bonds.csv   # replace with real terms
.venv\Scripts\uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API: `/api/bonds`, `/api/bonds/{isin}`, `/api/health`.
Tests: `.venv\Scripts\python -m pytest`.

## Yield convention

Annual effective yield, actual/365, dirty = clean + accrued, bullet repayment. Partially redeemed
bonds need the current face value in the CSV and (not yet built) an amortisation schedule.

## Next steps

1. Fill `data/bonds.csv` from Bond Central / depository data, and cross-check face value.
2. Import the NSE/BSE daily debt bhavcopy for the universe and last-traded reference prices.
3. Automate the daily Upstox token (port the Playwright login from the old repo; keep secrets in `.env`).
4. Add amortisation schedules; ask NSE Data & Analytics about the official corporate-bond feed.
