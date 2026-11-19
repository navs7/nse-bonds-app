"""Layers 1+2: bond universe and terms, loaded from a CSV you maintain.

Source of truth for the CSV should be the SEBI/depository database (Bond Central) and the NSE/BSE
daily debt bhavcopy. Those have no confirmed public API, so this loader is the stable seam: swap
in an importer for them later without touching the rest of the app.
"""
from __future__ import annotations

import csv
from datetime import date
from typing import Optional
from pathlib import Path

from ..models import BondTerms


def _f(v: Optional[str]) -> Optional[float]:
    v = (v or "").strip()
    return float(v) if v else None


def _d(v: Optional[str]) -> Optional[date]:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


def load_terms(path: Path) -> dict[str, BondTerms]:
    if not path.exists():
        return {}
    out: dict[str, BondTerms] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            isin = row["isin"].strip().upper()
            out[isin] = BondTerms(
                isin=isin,
                name=row.get("name", "").strip(),
                issuer=row.get("issuer", "").strip(),
                coupon_rate=float(row["coupon_rate"]),
                coupon_freq=int(row["coupon_freq"]) if (row.get("coupon_freq") or "").strip() else None,
                maturity_date=date.fromisoformat(row["maturity_date"]),
                face_value=float(row["face_value"]),
                paid_up_value=_f(row.get("paid_up_value")),
                series=row.get("series", "").strip(),
                category=row.get("category", "").strip(),
                rating=row.get("rating", "").strip(),
                listing_date=_d(row.get("listing_date")),
                ref_close=_f(row.get("ref_close")),
                ref_last=_f(row.get("ref_last")),
                ref_volume=_f(row.get("ref_volume")),
                ref_date=_d(row.get("ref_date")),
                terms_source=row.get("terms_source", "").strip(),
                terms_as_of=_d(row.get("terms_as_of")),
            )
    return out
