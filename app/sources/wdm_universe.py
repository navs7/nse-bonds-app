"""NSE Wholesale Debt Market (WDM) bonds, from NSE's "WDM list" CSV (wdmlist_DDMMYYYY.csv).

You download that file from nseindia.com yourself (it is not fetched by script) and drop it in the project
folder or data/. It is an official NSE list with ISIN, coupon (ISSUE_NAME), maturity, coupon frequency and the
last traded price/date. This module adds the corporate bonds in it that NSE's capital-market list (DEBT.csv)
and BSE do not already provide.

What you get, and the limits:
  * Terms are official, so rows are terms_verified=yes. Coupon frequency comes with the list.
  * Prices are per Rs 100 of face value (the WDM convention), so face_value is set to 100.
    CAVEAT: for amortising bonds the price may be per 100 of the *outstanding* amount; check before relying on it.
  * Most of these bonds rarely trade. A price of 0 / blank date means "never traded" and is stored as no price.
    Reference yields are only shown when the last trade is recent (REF_MAX_AGE_DAYS, default 30).
  * They trade on the wholesale segment, which Upstox does not stream: no live bid/ask, so no Invest/Review signal.
  * Skipped (counted in the run summary): commercial paper, matured, non-INE ISINs (govt securities), reset-rate
    bonds and anything whose coupon cannot be read from the file.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Optional

from .nse_universe import FREQ, FormatChanged, parse_nse_date

REQUIRED = {"SECTYPE", "ISSUE_NAME", "ISSUE_DESC", "MAT_DATE", "Cpn Freq", "Last Traded Date",
            "Last Traded Price (in Rs.)", "ISIN NO.", "STATUS"}
PCT_FIRST = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%")
PLAIN_NUM = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def asof_from_filename(name: str) -> date:
    """wdmlist_18112026.csv -> 2026-11-18; falls back to today."""
    named = re.search(r"\d{1,2}-[A-Za-z]{3}-\d{4}", name)  # Corporate_bond_report_18-Nov-2026.csv
    if named:
        try:
            return parse_nse_date(named[0])
        except (ValueError, KeyError):
            pass
    m = re.search(r"(\d{2})(\d{2})(\d{4})", name)
    try:
        return date(int(m[3]), int(m[2]), int(m[1])) if m else date.today()
    except ValueError:
        return date.today()


def parse_coupon(issue_name: str, issue_desc: str) -> Optional[float]:
    """'8.72%' -> 8.72. A bare number like '9.5119' is accepted only if the description confirms it as a percent.
    'RESET' (reset-rate) and anything else is None: we never guess a coupon."""
    m = PCT_FIRST.match(issue_name)
    if m:
        return float(m[1])
    p = PLAIN_NUM.match(issue_name)
    if p and re.search(re.escape(p[1]) + r"\s*%", issue_desc):
        return float(p[1])
    return None


def _date(s: str) -> Optional[date]:
    try:
        return parse_nse_date(s)
    except (ValueError, KeyError):
        return None


def _price(s: str) -> Optional[float]:
    try:
        v = float((s or "").strip())
    except ValueError:
        return None
    return v if v > 0 else None  # 0 = never traded


def parse_wdmlist(text: str, today: date) -> tuple[list[dict], dict[str, int]]:
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not REQUIRED <= {h.strip() for h in (rd.fieldnames or [])}:
        raise FormatChanged(f"WDM list columns changed: {rd.fieldnames}")
    out: list[dict] = []
    skipped = {"not_corporate": 0, "commercial_paper": 0, "matured": 0, "unreadable_coupon": 0}
    for r in rd:
        isin = (r["ISIN NO."] or "").strip().upper()
        if not isin.startswith("INE"):
            skipped["not_corporate"] += 1
            continue
        if (r["SECTYPE"] or "").strip().upper() == "CP":
            skipped["commercial_paper"] += 1
            continue
        mat = _date(r["MAT_DATE"] or "")
        if mat is None or mat <= today:
            skipped["matured"] += 1
            continue
        coupon = parse_coupon(r["ISSUE_NAME"] or "", r["ISSUE_DESC"] or "")
        if coupon is None:
            skipped["unreadable_coupon"] += 1
            continue
        last_date = _date(r["Last Traded Date"] or "")
        price = _price(r["Last Traded Price (in Rs.)"])
        out.append({
            "isin": isin, "name": (r["ISSUE_DESC"] or "").strip(), "sectype": (r["SECTYPE"] or "").strip(),
            "coupon_rate": coupon, "maturity_date": mat,
            "freq": FREQ.get((r["Cpn Freq"] or "").strip().lower()),  # '0' / blank -> None
            "ref_price": price if (price is not None and last_date is not None) else None,
            "ref_date": last_date if (price is not None and last_date is not None) else None,
        })
    return out, skipped


def build_rows(parsed: list[dict], overrides: dict[str, dict], existing: set[str],
               asof: date, source: str) -> list[dict]:
    rows = []
    for x in parsed:
        if x["isin"] in existing:
            continue
        ov = overrides.get(x["isin"], {})
        freq = (ov.get("coupon_freq") or "").strip() or (str(x["freq"]) if x["freq"] else "")
        rows.append({
            "isin": x["isin"], "name": x["name"], "issuer": x["name"], "series": x["sectype"],
            "category": "corporate", "exchange": "WDM", "also_on": "", "terms_verified": "yes",
            "coupon_rate": x["coupon_rate"], "coupon_freq": freq,
            "maturity_date": x["maturity_date"].isoformat(),
            "face_value": (ov.get("face_value") or "").strip() or 100, "paid_up_value": "",
            "rating": ov.get("rating", "") or "", "listing_date": "",
            "ref_close": x["ref_price"] if x["ref_price"] is not None else "",
            "ref_last": x["ref_price"] if x["ref_price"] is not None else "", "ref_volume": "",
            "ref_date": x["ref_date"].isoformat() if x["ref_date"] else "",
            "terms_source": f"{source} (prices per Rs 100)" + (" + overrides" if ov else ""),
            "terms_as_of": asof.isoformat(),
        })
    return rows


def find_wdmlist(*dirs: Path) -> Optional[Path]:
    """Newest wdmlist_*.csv (by the date in its name, then mtime) in the given folders."""
    found = [p for d in dirs if d.is_dir() for p in d.glob("wdmlist_*.csv")]
    return max(found, key=lambda p: (asof_from_filename(p.name), p.stat().st_mtime), default=None)
