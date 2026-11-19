# NSE Bonds (web app)

Live, honest NSE bond data. Rewrite of the old `upstoxbaji2` scripts as a web app.

## Layered design

Every layer has one job and one source. Nothing is shown without saying where it came from.

| Layer | Data | Source | Module |
|---|---|---|---|
| 1. Universe | Which bonds are listed | NSE `DEBT.csv` + daily capital-market bhavcopy (both free, public) | `app/sources/nse_universe.py` |
| 2. Terms | Coupon, maturity, face/paid-up value | NSE `DEBT.csv`, WDM list and corporate bond report, cross-checked per field | `reconcile.py`, `nse_universe.py` |
| 3. Live quotes | LTP, 5-level bid/ask | Upstox stream (broker feed) | `app/sources/upstox_live.py` |
| 4. Computed | YTM from price + terms | This app (`app/yieldcalc.py`) | `app/service.py` |

Each bond is returned with a status: `LIVE`, `STALE` (no recent tick or no depth) or `NO_QUOTE`.
Stale or missing quotes are never shown as live.

**Known limits:** Upstox is not an official exchange data product, and no free official live feed
exists. For production, replace layer 3 with NSE Data & Analytics' corporate-bond feed (same
`Quote` model). Layers 1-2 come from NSE's free files; rating and outstanding face value have no permitted source yet, so they stay blank unless you add them in `data/overrides.csv`.

## Importing the universe

```powershell
.venv\Scripts\python -m app.sources.nse_universe                    # corporate bonds -> data/bonds.csv
.venv\Scripts\python -m app.sources.nse_universe --categories corporate,gsec,sdl,tbill
```

Uses two free NSE files: `DEBT.csv` (ISIN, coupon, face and paid-up value, redemption date) and the daily
capital-market bhavcopy (ISIN, close, last, volume; it walks back to the latest trading day). Matured bonds are dropped.
The paid WDM `DLYddmmyyyy.zip` bhavcopy is vendor-SFTP-only and is not used.

**What NSE does not give us, and what the app does about it:**
- *Coupon frequency* is not in `DEBT.csv`; it comes from NSE's **WDM list** and **Corporate bond report** (the corporate report is now downloaded automatically from nsearchives.nseindia.com on each import, `--no-download` to skip; the WDM list has no known public URL, so download it from
  nseindia.com/all-reports-debt into the project folder; the import picks up the newest of each). See "Fields and sources" below.
  Until a coupon bond has a frequency, **yield is not computed** (it shows a note) rather than guessed. *Rating* is not in
  any permitted file (the report's rating column is empty); add ratings by hand in `data/overrides.csv`
  (`isin,coupon_freq,rating,face_value,terms_verified`), which always wins over the sources.
- *Data sources used, and only these:* NSE `DEBT.csv` and capital-market bhavcopy, Upstox's BSE list and BSE bhavcopy, the
  NSE WDM list and the NSE Corporate bond report. Hand-built spreadsheets are not used.
- *Paid-up value differs from face value* (e.g. 94 bonds in the Nov 2026 file): yield is withheld with a note until
  you verify the outstanding value with NSDL/CDSL. Fix it via `face_value` in overrides.
- Bhavcopy prices are end-of-day references, not live; only bonds that traded that day have one (123 of 1,667 corporate
  bonds on the first run).
- If NSE changes either file's layout the importer raises `FormatChanged` instead of importing wrong data.

## BSE bonds

The same import (`python -m app.sources.nse_universe`, add `--no-bse` to skip) also adds BSE debt, from two open files:
Upstox's BSE instrument list (ISIN + a name like `HUDCO-7.64%-8-2-31-TAXFREENCD`) and BSE's daily bhavcopy (prices).

- A bond listed on both exchanges gets **one row per exchange** (same ISIN, an NSE row and a BSE row, side by side). The
  BSE row carries NSE's official terms and BSE's own price. Uniqueness is on ISIN + exchange, never ISIN alone.
- **BSE-only bonds are unverified.** Coupon and maturity are parsed from the name. On bonds listed on both exchanges
  that parse agrees with NSE's official terms 620/636 times; the misses are typos in the BSE name (e.g. 2026 vs 2036).
  Face value and coupon frequency are not in any open BSE file. So BSE-only bonds show prices but **no yield or
  signal** until it is confirmed: an official NSE list covering the ISIN verifies it automatically, or set `terms_verified=yes`, `face_value`, and `coupon_freq` in `data/overrides.csv`
  after checking the bond's term sheet.
- Only NSE-listed and confirmed bonds are streamed from Upstox (unverified BSE bonds can't show a yield, and a
  big subscription may hit Upstox limits). BSE-only streaming uses `BSE_EQ|ISIN` keys once confirmed.
- Skipped: commercial paper, PTCs, perpetual/reset-rate and zero-coupon names that do not parse, and BSE G-secs/SDLs.
- The page and API have an exchange filter (`/api/bonds?exchange=BSE`).

## WDM bonds (NSE wholesale debt market)

`DEBT.csv` only covers NSE's capital-market segment. Corporate bonds that trade on the wholesale segment (private
placements and most debentures) are in NSE's **WDM list**. Download `wdmlist_DDMMYYYY.csv` from nseindia.com into the
project folder or `data/`; the universe import picks up the newest one automatically (`--wdmlist PATH` to choose,
`--no-wdm` to skip) and adds the live corporate bonds NSE/BSE don't already provide. Exchange shows as `WDM`.

- Terms (coupon, maturity, frequency) are official, so they count as verified. Face value is set to 100 because WDM
  prices are per Rs 100; check this for amortising bonds.
- Most never trade: price 0 / no date means "never traded" and is stored as no price. In the Nov 2026 file only
  210 of 699 have a last trade, and only 14 were within 30 days.
- Upstox does not stream WDM, so there is no live bid/ask and **no Invest/Review signal**. The page shows the last
  traded price, its date, and **YTM @close**, but only if that trade is at most `REF_MAX_AGE_DAYS` (30) old.
- Skipped: commercial paper, matured, government securities, reset-rate bonds and unreadable coupons.

**YTM @close** also appears for NSE/BSE bonds (from the bhavcopy close). A yield that comes out negative is never shown:
it means the price is above everything the bond still pays (bad terms, an unknown redemption amount as on cumulative
bonds, or a price convention problem), and the Notes column says so.

## Fields and sources (per ISIN + exchange)

Every row carries **symbol, trading symbol, coupon rate, maturity, coupon frequency and rating**, each cross-checked against every
permitted source by `app/sources/reconcile.py`:

| Field | Sources |
|---|---|
| Symbol | NSE `DEBT.csv`, exchange bhavcopy (traded symbol), Upstox list; for WDM rows the WDM list and corporate report |
| Trading symbol | Upstox NSE / BSE instrument list (WDM is not tradable there, so blank) |
| Coupon rate | corporate bond report, WDM list, `DEBT.csv` (official), Upstox name (supporting) |
| Maturity | corporate bond report, WDM list, `DEBT.csv` (official), Upstox BSE name (supporting) |
| Coupon frequency | corporate bond report, WDM list |
| Rating | corporate bond report (empty in practice) - otherwise blank |

Per field the page's **Field checks** column and `data/source_values.csv` (what every source says) show a status:
`agree(n)`, `single`, `supporting differs` (an official value exists but an Upstox name differs: usually a typo in the name),
`CONFLICT` (the official NSE sources disagree: red note, no yield until checked) or `none`. The most precise official value wins
(e.g. 8.0956 over `DEBT.csv`'s 8.09). A bond is **verified** when an official source gives its coupon and maturity without a
conflict, so BSE-only bonds are confirmed automatically when an NSE list covers them. List conflicts with "Only terms conflicts"
(API: `/api/bonds?conflicts=true`). The Excel export has 'Source values', 'Field coverage' and 'Sources used' sheets.

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

## Screening signal (from the old Excel macro)

On a **LIVE** quote, using the best ask: `INVEST` = YTM >= 12%, more than 90 days left, and gross profit > 10,000;
`REVIEW` = same but profit <= 10,000; `LOW_YTM`, `SHORT` (<= 90 days) otherwise; `CHECK` = yield above 50%, which
usually means a price or face-value problem, not a bargain. Gross profit = (remaining coupons + principal - price -
accrued) x best-ask quantity. Stale or missing quotes never get a signal. Thresholds: `MIN_YTM`, `MIN_DAYS_LEFT`,
`MIN_GROSS_PROFIT` in `.env`.

API: `/api/bonds?signal=INVEST,REVIEW&sort=ytm` (also `min_ytm`, `min_days`). The page has a "Screen" checkbox for this.

## Yield convention

Annual effective yield, actual/365, dirty = clean + accrued, bullet repayment. Partially redeemed
bonds need the current face value in the CSV and (not yet built) an amortisation schedule.

## Next steps

1. Add ratings and outstanding face values (no permitted source has them) in `data/overrides.csv`.
2. Add BSE's debt list to cover bonds NSE does not list.
3. Automate the daily Upstox token (port the Playwright login from the old repo; keep secrets in `.env`).
4. Add amortisation schedules; ask NSE Data & Analytics about the official corporate-bond feed.
