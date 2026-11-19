"""BSE bond universe, built from two open files (no login, no scraping):

  * Upstox BSE instrument list (assets.upstox.com ... BSE.json.gz): every BSE debt scrip (type "F")
    with its ISIN and a descriptive name such as "HUDCO-7.64%-8-2-31-TAXFREENCD".
  * BSE's daily UDiFF bhavcopy (bseindia.com/download/BhavCopy/Equity/...): close/last/volume by ISIN.

HONEST LIMITS - read before trusting the output:
  * Coupon and maturity are PARSED FROM THE NAME. Checked against NSE's official DEBT.csv on bonds
    listed on both exchanges, ~97.5% agree; the rest are typos in the BSE name (e.g. 2026 vs 2036).
    So BSE-only rows are marked terms_verified=no and get NO yield or signal until you confirm them
    in data/overrides.csv (terms_verified=yes, face_value=...).
  * Face value is not in either file. Coupon frequency and rating are not either.
  * Bonds also listed on NSE keep NSE's official terms; BSE is only recorded in `also_on`.
  * Only INE-ISIN corporate debt with a parsable "<coupon>%" name is imported. Commercial paper,
    PTCs, perpetual/reset-rate and zero-coupon names that do not parse are skipped and counted.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import date, timedelta
from typing import Optional

from .nse_universe import parse_bhavcopy_text

UPSTOX_BSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
BSE_BHAV_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"

NAME_RX = re.compile(
    r"^(?P<issuer>.+?)-(?P<c>\d+(?:\.\d+)?)%-(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{2}|\d{4})(?:-(?P<sfx>.+))?$"
)


def parse_name(name: str) -> Optional[tuple[str, float, date]]:
    """'SPPL-10%-11-6-28-PVT' -> ('SPPL', 10.0, date(2028, 6, 11)); None if not in that shape."""
    m = NAME_RX.match(name.strip())
    if not m:
        return None
    y = int(m["y"])
    y = 2000 + y if y < 100 else y
    try:
        return m["issuer"], float(m["c"]), date(y, int(m["m"]), int(m["d"]))
    except ValueError:
        return None


def parse_instruments(raw_gz: bytes) -> tuple[list[dict], int]:
    """(parsed corporate debt instruments, count skipped because the name did not parse)."""
    data = json.loads(gzip.decompress(raw_gz))
    sample = next((x for x in data if x.get("segment") == "BSE_EQ"), None)  # indices have no ISIN
    if sample is None or not {"instrument_type", "isin", "name"} <= set(sample):
        raise ValueError("Upstox BSE instrument list changed shape")
    out, skipped = [], 0
    for x in data:
        if x.get("segment") != "BSE_EQ" or x.get("instrument_type") != "F":
            continue
        isin = (x.get("isin") or "").strip().upper()
        if not isin.startswith("INE"):
            continue
        p = parse_name(x.get("name", ""))
        if p is None:
            skipped += 1
            continue
        out.append({"isin": isin, "name": x["name"].strip(), "issuer": p[0],
                    "coupon_rate": p[1], "maturity_date": p[2]})
    return out, skipped


def build_rows(instruments: list[dict], bhav: dict[str, dict], overrides: dict[str, dict],
               nse_isins: set[str], today: date) -> tuple[list[dict], dict]:
    """BSE-only rows (ISINs NSE already lists are skipped) plus quality stats."""
    rows, also_on = [], set()
    for x in instruments:
        if x["isin"] in nse_isins:
            also_on.add(x["isin"])
            continue
        if x["maturity_date"] <= today:
            continue
        ov = overrides.get(x["isin"], {})
        ref = bhav.get(x["isin"], {})
        verified = (ov.get("terms_verified") or "").strip().lower() == "yes"
        rows.append({
            "isin": x["isin"], "name": x["name"], "issuer": x["issuer"], "series": "", "category": "corporate",
            "exchange": "BSE", "also_on": "", "terms_verified": "yes" if verified else "no",
            "coupon_rate": x["coupon_rate"], "coupon_freq": ov.get("coupon_freq", "") or "",
            "maturity_date": x["maturity_date"].isoformat(),
            "face_value": ov.get("face_value", "") or "", "paid_up_value": "",
            "rating": ov.get("rating", "") or "", "listing_date": "",
            "ref_close": ref.get("ref_close", ""), "ref_last": ref.get("ref_last", ""),
            "ref_volume": ref.get("ref_volume", ""), "ref_date": ref.get("ref_date", ""),
            "terms_source": "Upstox BSE list, parsed from name" + (" + overrides" if ov else ""),
            "terms_as_of": today.isoformat(),
        })
    return rows, {"also_on_nse": also_on}


def agreement_with_nse(instruments: list[dict], nse_terms: dict[str, dict]) -> tuple[int, int]:
    """(agree, total) of name-parsed coupon+maturity vs NSE's official terms on shared ISINs."""
    agree = total = 0
    for x in instruments:
        n = nse_terms.get(x["isin"])
        if not n:
            continue
        total += 1
        if abs(x["coupon_rate"] - float(n["coupon_rate"])) < 0.005 and x["maturity_date"].isoformat() == n["maturity_date"]:
            agree += 1
    return agree, total


def fetch_latest_bse_bhavcopy(client, today: date, lookback: int = 7) -> dict[str, dict]:
    for i in range(lookback + 1):
        d = today - timedelta(days=i)
        r = client.get(BSE_BHAV_URL.format(ymd=d.strftime("%Y%m%d")))
        if r.status_code == 200 and r.content[:6] == b"TradDt":
            return parse_bhavcopy_text(r.content.decode("utf-8-sig", errors="replace"))
    raise RuntimeError(f"no BSE bhavcopy found in the last {lookback} days")
