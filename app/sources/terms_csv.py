"""Layers 1+2: bond universe and terms, loaded from a CSV you maintain.

Source of truth for the CSV should be the SEBI/depository database (Bond Central) and the NSE/BSE
daily debt bhavcopy. Those have no confirmed public API, so this loader is the stable seam: swap
in an importer for them later without touching the rest of the app.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..models import BondTerms


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
                coupon_freq=int(row["coupon_freq"]),
                maturity_date=date.fromisoformat(row["maturity_date"]),
                face_value=float(row["face_value"]),
                rating=row.get("rating", "").strip(),
                terms_source=row.get("terms_source", "").strip(),
                terms_as_of=date.fromisoformat(row["terms_as_of"]) if row.get("terms_as_of") else None,
            )
    return out
