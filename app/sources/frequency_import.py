"""Fill coupon_freq in data/overrides.csv from NSE files that carry it. Two formats are recognised:

  * NSE "WDM list" (wdmlist_DDMMYYYY.csv: SECTYPE, ISSUE_NAME, MAT_DATE, Cpn Freq, ISIN NO., ...), a current
    listing of NSE debt with coupon frequency. Each ISIN's coupon and maturity are cross-checked against
    data/bonds.csv; if they disagree the ISIN is skipped (probably not the same instrument).
  * NSE "Interest Payment Details" CSV, below.

The interest-payment file is the issuers' own regulatory filings (field "Frequency"), published by NSE on the
"Securities available for Trading" page as "Interest Payment Details (.csv)". Download the CURRENT
version yourself (it is a normal browser download) and put it in data/. The copies on the NSE archive
host that are reachable by script are old (2022-2023) snapshots and cover almost none of today's bonds.

    python -m app.sources.frequency_import data/wdmlist_18112026.csv [data/Interest_Payment_Details.csv] [--replace]

Rules (never guess):
  * only ISINs in data/bonds.csv with a coupon > 0 are considered
  * per ISIN the filing with the latest Reporting Date wins; if the latest date has conflicting
    frequencies the ISIN is skipped and reported
  * Yearly=1, Half Yearly=2, Quarterly=4, Monthly=12; "Others", "0" (no periodic coupon) and blanks are skipped
  * existing overrides are kept unless --replace is given; disagreements are reported
"""
from __future__ import annotations

import argparse
import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Optional

from .nse_universe import MONTHS

FREQ = {"yearly": 1, "annual": 1, "annually": 1, "half yearly": 2, "half-yearly": 2,
        "quarterly": 4, "monthly": 12, "1": 1, "2": 2, "4": 4, "12": 12}
OVERRIDE_FIELDS = ["isin", "coupon_freq", "rating", "face_value", "terms_verified"]


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


def is_wdmlist(text: str) -> bool:
    head = next(csv.reader(io.StringIO(text.lstrip("﻿"))), [])
    return {"ISIN NO.", "Cpn Freq", "MAT_DATE", "ISSUE_NAME"} <= {h.strip() for h in head}


def asof_from_filename(name: str) -> date:
    """wdmlist_18112026.csv -> 2026-11-18; falls back to today."""
    m = re.search(r"(\d{2})(\d{2})(\d{4})", name)
    try:
        return date(int(m[3]), int(m[2]), int(m[1])) if m else date.today()
    except ValueError:
        return date.today()


def read_wdmlist(text: str, asof: date):
    """-> (isin -> (asof, {frequencies}), isin -> (coupon %, maturity) for cross-checking)."""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    freqs: dict[str, tuple[date, set[int]]] = {}
    terms: dict[str, tuple[Optional[float], Optional[date]]] = {}
    for r in rd:
        isin = (r.get("ISIN NO.") or "").strip().upper()
        f = FREQ.get((r.get("Cpn Freq") or "").strip().lower())
        if not isin or f is None:
            continue
        freqs.setdefault(isin, (asof, set()))[1].add(f)
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s*%", r.get("ISSUE_NAME") or "")
        terms[isin] = (float(m[1]) if m else None, _date(r.get("MAT_DATE") or ""))
    return freqs, terms


def terms_mismatches(terms: dict, bonds: dict[str, dict]) -> set[str]:
    """ISINs whose coupon or maturity in the WDM list contradict what we hold (skip those)."""
    bad = set()
    for isin, (coupon, mat) in terms.items():
        b = bonds.get(isin)
        if not b:
            continue
        if mat is not None and mat.isoformat() != b["maturity_date"]:
            bad.add(isin)
        # DEBT.csv rounds coupons to 2 decimals (7.685% -> 7.69%), so allow half a rounding unit
        elif coupon is not None and abs(coupon - float(b["coupon_rate"] or 0)) > 0.0051:
            bad.add(isin)
    return bad


def conflict_detail(isin: str, wdm_terms: dict, bonds: dict[str, dict]) -> str:
    c, m = wdm_terms[isin]
    b = bonds[isin]
    return (f"NSE WDM list says {c if c is not None else '?'}% maturing {m or '?'}; "
            f"{b.get('terms_source') or 'our data'} says {b['coupon_rate']}% maturing {b['maturity_date']}")


def write_conflicts(path: Path, suspect: set[str], wdm_terms: dict, bonds: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["isin", "detail"])
        for isin in sorted(suspect):
            w.writerow([isin, conflict_detail(isin, wdm_terms, bonds)])


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
    ap.add_argument("--conflicts", default="data/term_conflicts.csv",
                    help="where bonds whose terms conflict with the WDM list are recorded (shown on the page)")
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
    wdm_terms: dict = {}
    for fn in a.files:
        text = Path(fn).read_text(encoding="utf-8-sig", errors="replace")
        if is_wdmlist(text):
            parsed, terms = read_wdmlist(text, asof_from_filename(Path(fn).name))
            wdm_terms.update(terms)
        else:
            parsed = read_filings(text)
        for isin, (d, fr) in parsed.items():
            cur = filings.get(isin)
            if cur is None or d > cur[0]:
                filings[isin] = (d, set(fr))
            elif d == cur[0]:
                cur[1].update(fr)
    suspect = terms_mismatches(wdm_terms, bonds)
    for isin in suspect:
        filings.pop(isin, None)
    if wdm_terms:  # only a WDM-list run can (re)decide which bonds conflict
        write_conflicts(Path(a.conflicts), suspect, wdm_terms, bonds)

    added, conflicts, disagree = merge(filings, coupon_isins, overrides, a.replace)
    for isin, f in added.items():
        row = overrides.setdefault(isin, {"isin": isin, "rating": "", "face_value": "", "terms_verified": ""})
        row["coupon_freq"] = str(f)
    with op.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(overrides.values(), key=lambda r: r["isin"]))

    left = sum(1 for i in coupon_isins if not (overrides.get(i, {}).get("coupon_freq") or "").strip())
    print(f"added {len(added)} | skipped: conflicting filings {len(conflicts)}, coupon/maturity differs from "
          f"our data {len(suspect)} | "
          f"disagree with existing {len(disagree)}{' (replaced)' if a.replace else ' (kept existing)'} | "
          f"coupon bonds still without frequency: {left}")
    for isin in sorted(suspect)[:10]:
        print(f"  terms mismatch {isin}: WDM list {wdm_terms[isin][0]}% / {wdm_terms[isin][1]} vs ours "
              f"{bonds[isin]['coupon_rate']}% / {bonds[isin]['maturity_date']}")
    for isin, old, new in disagree[:10]:
        print(f"  disagree {isin}: existing {old}/yr vs NSE filing {new}/yr")


if __name__ == "__main__":
    main()
