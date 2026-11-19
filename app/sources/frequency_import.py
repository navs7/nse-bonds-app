"""Fill coupon_freq in data/overrides.csv from NSE's "Interest Payment Details" CSV.

That file is the issuers' own regulatory filings (field "Frequency"), published by NSE on the
"Securities available for Trading" page as "Interest Payment Details (.csv)". Download the CURRENT
version yourself (it is a normal browser download) and put it in data/. The copies on the NSE archive
host that are reachable by script are old (2022-2023) snapshots and cover almost none of today's bonds.

    python -m app.sources.frequency_import data/Interest_Payment_Details.csv [more files...] [--replace]

Rules (never guess):
  * only ISINs in data/bonds.csv with a coupon > 0 are considered
  * per ISIN the filing with the latest Reporting Date wins; if the latest date has conflicting
    frequencies the ISIN is skipped and reported
  * Yearly=1, Half Yearly=2, Quarterly=4, Monthly=12; "Others"/unknown are skipped
  * existing overrides are kept unless --replace is given; disagreements are reported
"""
from __future__ import annotations

import argparse
import csv
import io
from datetime import date
from pathlib import Path
from typing import Optional

from .nse_universe import MONTHS

FREQ = {"yearly": 1, "annual": 1, "annually": 1, "half yearly": 2, "half-yearly": 2,
        "quarterly": 4, "monthly": 12}
OVERRIDE_FIELDS = ["isin", "coupon_freq", "rating", "face_value"]


def _date(s: str) -> Optional[date]:
    try:
        d, m, y = s.strip().upper().split("-")
        y = int(y)
        return date(2000 + y if y < 100 else y, MONTHS[m], int(d))
    except (ValueError, KeyError):
        return None


def read_filings(text: str) -> dict[str, tuple[Optional[date], set[int]]]:
    """isin -> (latest reporting date, set of frequencies filed on that date)."""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not {"ISIN", "Reporting Date", "Frequency"} <= set(rd.fieldnames or []):
        raise ValueError(f"Not an Interest Payment Details file; columns: {rd.fieldnames}")
    best: dict[str, tuple[Optional[date], set[int]]] = {}
    for r in rd:
        isin = r["ISIN"].strip().upper()
        f = FREQ.get(r["Frequency"].strip().lower())
        d = _date(r["Reporting Date"])
        if not isin or f is None or d is None:
            continue
        cur = best.get(isin)
        if cur is None or d > cur[0]:
            best[isin] = (d, {f})
        elif d == cur[0]:
            cur[1].add(f)
    return best


def merge(filings: dict, coupon_isins: set[str], overrides: dict[str, dict], replace: bool):
    added, conflicts, disagree = {}, [], []
    for isin, (_, freqs) in filings.items():
        if isin not in coupon_isins:
            continue
        if len(freqs) != 1:
            conflicts.append(isin)
            continue
        f = next(iter(freqs))
        have = (overrides.get(isin, {}).get("coupon_freq") or "").strip()
        if have and int(have) != f:
            disagree.append((isin, int(have), f))
            if not replace:
                continue
        elif have:
            continue
        added[isin] = f
    return added, conflicts, disagree


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--bonds", default="data/bonds.csv")
    ap.add_argument("--overrides", default="data/overrides.csv")
    ap.add_argument("--replace", action="store_true", help="overwrite existing values that disagree")
    a = ap.parse_args()

    with open(a.bonds, newline="", encoding="utf-8") as f:
        bonds = {r["isin"]: r for r in csv.DictReader(f)}
    coupon_isins = {i for i, b in bonds.items() if float(b["coupon_rate"] or 0) > 0}
    op = Path(a.overrides)
    overrides: dict[str, dict] = {}
    if op.exists():
        with op.open(newline="", encoding="utf-8-sig") as f:
            overrides = {r["isin"].strip().upper(): r for r in csv.DictReader(f)}

    filings: dict = {}
    for fn in a.files:
        for isin, (d, fr) in read_filings(Path(fn).read_text(encoding="utf-8-sig", errors="replace")).items():
            cur = filings.get(isin)
            if cur is None or d > cur[0]:
                filings[isin] = (d, set(fr))
            elif d == cur[0]:
                cur[1].update(fr)

    added, conflicts, disagree = merge(filings, coupon_isins, overrides, a.replace)
    for isin, f in added.items():
        row = overrides.setdefault(isin, {"isin": isin, "rating": "", "face_value": ""})
        row["coupon_freq"] = str(f)
    with op.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(overrides.values(), key=lambda r: r["isin"]))

    left = sum(1 for i in coupon_isins if not (overrides.get(i, {}).get("coupon_freq") or "").strip())
    print(f"added {len(added)} | skipped conflicting filings {len(conflicts)} | "
          f"disagree with existing {len(disagree)}{' (replaced)' if a.replace else ' (kept existing)'} | "
          f"coupon bonds still without frequency: {left}")
    for isin, old, new in disagree[:10]:
        print(f"  disagree {isin}: existing {old}/yr vs NSE filing {new}/yr")


if __name__ == "__main__":
    main()
