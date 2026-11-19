"""Layers 1+2 importer: bond universe from NSE's free public files.

Sources (both public, no login):
  * DEBT.csv                      list of debt securities: ISIN, coupon (IP RATE), face value,
                                  paid-up value, listing date, redemption date.
  * UDiFF capital-market bhavcopy daily file with ISIN + close/last price + volume.

NOT available from these files (so left blank, never guessed): coupon frequency, credit rating,
amortisation schedule. Supply them in data/overrides.csv (isin,coupon_freq,rating,face_value).
The paid WDM "DLYddmmyyyy.zip" bhavcopy is vendor-SFTP-only and is not used here.

Run:  python -m app.sources.nse_universe [--categories corporate,gsec] [--out data/bonds.csv]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

DEBT_URL = "https://nsearchives.nseindia.com/content/equities/DEBT.csv"
BHAV_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# coupon-frequency labels used by NSE files -> payments per year ("0"/blank = no periodic coupon: not mapped)
FREQ = {"yearly": 1, "annual": 1, "annually": 1, "half yearly": 2, "half-yearly": 2,
        "quarterly": 4, "monthly": 12, "1": 1, "2": 2, "4": 4, "12": 12}

ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}

OUT_FIELDS = [
    "isin", "name", "issuer", "series", "category", "exchange", "also_on", "terms_verified",
    "coupon_rate", "coupon_freq", "maturity_date",
    "face_value", "paid_up_value", "rating", "listing_date",
    "ref_close", "ref_last", "ref_volume", "ref_date", "terms_source", "terms_as_of", "terms_conflict",
]


class FormatChanged(RuntimeError):
    """NSE changed a file layout; fail loudly rather than import wrong data."""


def category_of(series: str) -> str:
    s = series.strip().upper()
    return {"GS": "gsec", "TB": "tbill", "SG": "sdl"}.get(s, "corporate")


def parse_nse_date(s: str) -> Optional[date]:
    s = s.strip().upper()
    if not s:
        return None
    d, m, y = s.split("-")
    return date(int(y), MONTHS[m], int(d))


def _num(s: str) -> Optional[float]:
    s = s.strip()
    return float(s) if s else None


def parse_debt_master(text: str) -> list[dict]:
    """DEBT.csv has 14 header names but 15 columns per row: the unnamed last one is the ISIN."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header = [h.strip().upper() for h in rows[0]]
    expected = ["SYMBOL", "NAME OF COMPANY", "SERIES", "FACE VALUE", "PAID UP VALUE", "MKT LOT",
                "IP RATE", "DATE OF LISTING", "DATE OF ALLOTMENT", "REDEMPTION DATE"]
    if header[: len(expected)] != expected:
        raise FormatChanged(f"DEBT.csv header changed: {header}")
    out = []
    for r in rows[1:]:
        if len(r) < 15 or not ISIN_RE.match(r[14].strip()):
            continue
        out.append({
            "symbol": r[0].strip(), "name": r[1].strip(), "series": r[2].strip(),
            "face_value": _num(r[3]), "paid_up_value": _num(r[4]),
            "coupon_rate": _num(r[6]) or 0.0,
            "listing_date": parse_nse_date(r[7]), "maturity_date": parse_nse_date(r[9]),
            "isin": r[14].strip(),
        })
    return out


def parse_bhavcopy(zip_bytes: bytes) -> dict[str, dict]:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return parse_bhavcopy_text(z.read(z.namelist()[0]).decode("utf-8-sig", errors="replace"))


def parse_bhavcopy_text(text: str) -> dict[str, dict]:
    """NSE and BSE publish the same UDiFF layout, so one parser serves both."""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    need = {"TradDt", "ISIN", "SctySrs", "ClsPric", "LastPric", "TtlTradgVol"}
    if not need <= set(rd.fieldnames or []):
        raise FormatChanged(f"bhavcopy columns changed: {rd.fieldnames}")
    return {
        r["ISIN"].strip(): {
            "ref_close": _num(r["ClsPric"]), "ref_last": _num(r["LastPric"]),
            "ref_volume": _num(r["TtlTradgVol"]), "ref_date": r["TradDt"].strip(),
        }
        for r in rd
    }


def load_overrides(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {r["isin"].strip().upper(): r for r in csv.DictReader(f)}


def load_conflicts(path: Path) -> dict[str, str]:
    """data/term_conflicts.csv (written by frequency_import): ISINs whose coupon/maturity two sources disagree on."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {r["isin"].strip().upper(): r["detail"] for r in csv.DictReader(f)}


def build_rows(master: Iterable[dict], bhav: dict[str, dict], overrides: dict[str, dict],
               categories: set[str], today: date) -> list[dict]:
    rows = []
    for m in master:
        cat = category_of(m["series"])
        if cat not in categories or not m["maturity_date"] or m["maturity_date"] <= today:
            continue
        ov = overrides.get(m["isin"], {})
        ref = bhav.get(m["isin"], {})
        rows.append({
            "isin": m["isin"], "name": m["name"], "issuer": m["name"], "series": m["series"],
            "category": cat, "exchange": "NSE", "also_on": "", "terms_verified": "yes",
            "coupon_rate": m["coupon_rate"],
            "coupon_freq": ov.get("coupon_freq", "") or "",
            "maturity_date": m["maturity_date"].isoformat(),
            "face_value": ov.get("face_value") or m["face_value"],
            "paid_up_value": m["paid_up_value"] if m["paid_up_value"] is not None else "",
            "rating": ov.get("rating", "") or "",
            "listing_date": m["listing_date"].isoformat() if m["listing_date"] else "",
            "ref_close": ref.get("ref_close", ""), "ref_last": ref.get("ref_last", ""),
            "ref_volume": ref.get("ref_volume", ""), "ref_date": ref.get("ref_date", ""),
            "terms_source": "NSE DEBT.csv" + (" + overrides" if ov else ""),
            "terms_as_of": today.isoformat(),
        })
    return sorted(rows, key=lambda r: (r["category"], r["name"], r["maturity_date"]))


def assert_unique_isins(rows: list[dict]) -> None:
    """A duplicate ISIN would be silently merged by the loader (last row wins), so refuse to write one."""
    seen: dict[str, int] = {}
    for r in rows:
        seen[r["isin"]] = seen.get(r["isin"], 0) + 1
    dups = sorted(i for i, n in seen.items() if n > 1)
    if dups:
        raise ValueError(f"{len(dups)} duplicate ISIN(s) in the universe, e.g. {dups[:5]}")


def write_csv(rows: list[dict], path: Path) -> None:
    assert_unique_isins(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(rows)


def fetch_latest_bhavcopy(client, today: date, lookback: int = 7) -> dict[str, dict]:
    """Walk back from today to the most recent trading day that has a published file."""
    for i in range(lookback + 1):
        d = today - timedelta(days=i)
        resp = client.get(BHAV_URL.format(ymd=d.strftime("%Y%m%d")))
        if resp.status_code == 200 and resp.content[:2] == b"PK":
            return parse_bhavcopy(resp.content)
    raise RuntimeError(f"no bhavcopy found in the last {lookback} days")


def add_bse(rows: list[dict], overrides: dict, today: date) -> list[dict]:
    """Append BSE-only bonds; mark ISINs NSE also lists. Prints parse quality so drift is visible."""
    import httpx

    from . import bse_universe as bu

    with httpx.Client(timeout=90, follow_redirects=True) as c:
        raw = c.get(bu.UPSTOX_BSE_URL)
        raw.raise_for_status()
        instruments, skipped = bu.parse_instruments(raw.content)
        try:
            bse_bhav = bu.fetch_latest_bse_bhavcopy(c, today)
        except (RuntimeError, httpx.HTTPError) as e:  # prices are a bonus: never lose the universe over them
            print(f"WARNING: BSE prices unavailable this run ({e}); BSE bonds will have no close price. Rerun later to add them.")
            bse_bhav = {}
    nse_terms = {r["isin"]: r for r in rows}
    agree, total = bu.agreement_with_nse(instruments, nse_terms)
    new_rows, stats = bu.build_rows(instruments, bse_bhav, overrides, set(nse_terms), today)
    for r in rows:
        if r["isin"] in stats["also_on_nse"]:
            r["also_on"] = "BSE"
    print(f"BSE: {len(instruments)} corporate debt scrips parsed ({skipped} skipped: CP/PTC/perpetual/unparsable names); "
          f"{len(stats['also_on_nse'])} also on NSE; name-parsed coupon+maturity agrees with NSE's official terms on "
          f"{agree}/{total}; {len(new_rows)} BSE-only added (terms unverified, no yield until confirmed)")
    return rows + new_rows


def add_wdm(rows: list[dict], overrides: dict, today: date, path: Optional[Path]) -> list[dict]:
    """Append corporate bonds that only NSE's wholesale-debt list has. Needs the user's downloaded wdmlist file."""
    from . import wdm_universe as wu

    path = path or wu.find_wdmlist(Path("."), Path("data"))
    if path is None:
        print("WDM: no wdmlist_*.csv found (download NSE's WDM list into this folder or data/) - skipping WDM-only bonds")
        return rows
    parsed, skipped = wu.parse_wdmlist(path.read_text(encoding="utf-8-sig", errors="replace"), today)
    new = wu.build_rows(parsed, overrides, {r["isin"] for r in rows}, wu.asof_from_filename(path.name),
                        f"NSE WDM list {path.name}")
    priced = sum(1 for r in new if r["ref_close"] != "")
    print(f"WDM ({path.name}): {len(parsed)} live corporate bonds, {len(parsed) - len(new)} already in the list -> "
          f"{len(new)} WDM-only added ({priced} with a last-traded price, the rest never traded); skipped: {skipped}")
    return rows + new


def main() -> None:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--categories", default="corporate", help="comma list: corporate,gsec,sdl,tbill")
    ap.add_argument("--out", default="data/bonds.csv")
    ap.add_argument("--overrides", default="data/overrides.csv")
    ap.add_argument("--conflicts", default="data/term_conflicts.csv")
    ap.add_argument("--no-bse", action="store_true", help="skip the BSE universe")
    ap.add_argument("--wdmlist", default=None, help="NSE WDM list CSV (default: newest wdmlist_*.csv in . or data/)")
    ap.add_argument("--no-wdm", action="store_true", help="skip WDM-only bonds")
    a = ap.parse_args()
    today = date.today()
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as c:
        r = c.get(DEBT_URL)
        r.raise_for_status()
        master = parse_debt_master(r.text)
        bhav = fetch_latest_bhavcopy(c, today)
    cats = {x.strip() for x in a.categories.split(",")}
    overrides = load_overrides(Path(a.overrides))
    rows = build_rows(master, bhav, overrides, cats, today)
    if not a.no_bse and "corporate" in cats:
        rows = add_bse(rows, overrides, today)
    if not a.no_wdm and "corporate" in cats:
        rows = add_wdm(rows, overrides, today, Path(a.wdmlist) if a.wdmlist else None)
    conflicts = load_conflicts(Path(a.conflicts))
    for r in rows:
        r["terms_conflict"] = conflicts.get(r["isin"], "")
    write_csv(rows, Path(a.out))
    traded = sum(1 for r in rows if r["ref_close"] != "")
    print(f"{len(master)} debt securities in DEBT.csv -> wrote {len(rows)} to {a.out} "
          f"({traded} with a bhavcopy price; {sum(1 for r in rows if not r['coupon_freq'] and float(r['coupon_rate'])>0)} "
          f"coupon bonds still need coupon_freq in overrides)")


if __name__ == "__main__":
    main()
