# NSE Bonds (web app)

Live, honest NSE bond data. Rewrite of the old `upstoxbaji2` scripts as a web app.

## Layered design

Every layer has one job and one source. Nothing is shown without saying where it came from.

| Layer | Data | Source | Module |
|---|---|---|---|
| 1. Universe | Which bonds are listed | NSE `DEBT.csv` + daily capital-market bhavcopy (both free, public) | `app/sources/nse_universe.py` |
| 2. Terms | Coupon, maturity, face/paid-up value | NSE `DEBT.csv`; frequency and rating from `data/overrides.csv` (SEBI/depository data) | `nse_universe.py`, `terms_csv.py` |
| 3. Live quotes | LTP, 5-level bid/ask | Upstox stream (broker feed) | `app/sources/upstox_live.py` |
| 4. Computed | YTM from price + terms | This app (`app/yieldcalc.py`) | `app/service.py` |

Each bond is returned with a status: `LIVE`, `STALE` (no recent tick or no depth) or `NO_QUOTE`.
Stale or missing quotes are never shown as live.

**Known limits:** Upstox is not an official exchange data product, and no free official live feed
exists. For production, replace layer 3 with NSE Data & Analytics' corporate-bond feed (same
`Quote` model). Layers 1-2 come from NSE's free files; coupon frequency, rating and outstanding face value have no confirmed public API yet, so they come from `data/overrides.csv`.

## Importing the universe

```powershell
.venv\Scripts\python -m app.sources.nse_universe                    # corporate bonds -> data/bonds.csv
.venv\Scripts\python -m app.sources.nse_universe --categories corporate,gsec,sdl,tbill
```

Uses two free NSE files: `DEBT.csv` (ISIN, coupon, face and paid-up value, redemption date) and the daily
capital-market bhavcopy (ISIN, close, last, volume; it walks back to the latest trading day). Matured bonds are dropped.
The paid WDM `DLYddmmyyyy.zip` bhavcopy is vendor-SFTP-only and is not used.

**What NSE does not give us, and what the app does about it:**
- *Coupon frequency, rating* are blank. Add them in `data/overrides.csv` (`isin,coupon_freq,rating,face_value`).
  Until a coupon bond has a frequency, **yield is not computed** (it shows a note) rather than guessed.
- *Paid-up value differs from face value* (e.g. 94 bonds in the Nov 2026 file): yield is withheld with a note until
  you verify the outstanding value with NSDL/CDSL. Fix it via `face_value` in overrides.
- Bhavcopy prices are end-of-day references, not live; only bonds that traded that day have one (123 of 1,667 corporate
  bonds on the first run).
- If NSE changes either file's layout the importer raises `FormatChanged` instead of importing wrong data.

## Daily Upstox login (automated)

Upstox tokens expire every day. `app/upstox_auth.py` logs in with Playwright (mobile + TOTP + PIN), exchanges the
code for a token and saves it to `.env`. A token that still works is reused, so it only logs in when needed.

One-time setup:
1. Put `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_MOBILE`, `UPSTOX_PIN`, `UPSTOX_TOTP_KEY` in `.env`
   (see `.env.example`; `UPSTOX_REDIRECT_URI` must match your Upstox app). They stay in `.env`, which is git-ignored.
2. `.venv\Scripts\python -m playwright install chromium`

Then each day: `.\start.ps1` (refreshes token and bond list, then starts the app), or just the login with
`.venv\Scripts\python -m app.upstox_auth` (`--force` to log in again regardless).

## Run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # add UPSTOX_ACCESS_TOKEN
python -m app.sources.nse_universe            # builds data\bonds.csv from NSE files
.venv\Scripts\uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API: `/api/bonds`, `/api/bonds/{isin}`, `/api/health`.
Tests: `.venv\Scripts\python -m pytest`.

## Yield convention

Annual effective yield, actual/365, dirty = clean + accrued, bullet repayment. Partially redeemed
bonds need the current face value in the CSV and (not yet built) an amortisation schedule.

## Next steps

1. Fill `data/overrides.csv` (coupon frequency, rating, outstanding face value) from Bond Central / depository data.
2. Add BSE's debt list to cover bonds NSE does not list.
3. Automate the daily Upstox token (port the Playwright login from the old repo; keep secrets in `.env`).
4. Add amortisation schedules; ask NSE Data & Analytics about the official corporate-bond feed.
