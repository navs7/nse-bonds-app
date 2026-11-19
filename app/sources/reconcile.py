"""Cross-check every field of every (ISIN, exchange) row against ALL permitted sources, and record the evidence.

Sources (only these):
  NSE DEBT.csv               symbol, coupon, maturity         (open file)
  NSE corporate bond report  symbol, coupon, maturity, frequency, rating, issuer   (you download it)
  NSE WDM list               symbol, coupon, maturity, frequency                    (you download it)
  Upstox NSE / BSE lists     trading symbol, name (coupon, and for BSE the maturity, parsed from the name)
  NSE / BSE bhavcopy         traded symbol

Per field, per row: collect what each source says, pick the final value, and give a status.
  Official sources (for coupon/maturity/frequency) are the three NSE files; Upstox names are only supporting.
  agree(n)         n sources give the same value and none differ
  single           exactly one source has it
  supporting differs  an official value exists but an Upstox name-parsed value differs (a typo in the name)
  CONFLICT         official sources disagree with each other: the row is flagged and gets no yield
  none             no source has it (e.g. rating: not in any permitted file)
A bond whose coupon AND maturity come from an official source (and do not conflict) is "verified".
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from .nse_universe import FREQ, FormatChanged, MONTHS

COUPON_TOL = 0.0101  # percentage points: DEBT.csv rounds or truncates coupons to 2 decimals (8.0956 -> 8.09)

S_CORP = "NSE corporate bond report"
S_WDM = "NSE WDM list"
S_DEBT = "NSE DEBT.csv"
S_UPX = "Upstox list"
S_BHAV = "Exchange bhavcopy"
OFFICIAL_ORDER = [S_CORP, S_WDM, S_DEBT]  # most precise first

UPSTOX_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# column layouts of the two NSE lists from nseindia.com/all-reports-debt
LAYOUTS = [
    {"label": S_WDM, "isin": "ISIN NO.", "symbol": "SECURITY", "name": "ISSUE_NAME", "mat": "MAT_DATE",
     "freq": "Cpn Freq", "rating": None, "issuer": None},
    {"label": S_CORP, "isin": "ISIN", "symbol": "Security", "name": "Issue Name", "mat": "Maturity Date",
     "freq": "Coupon Frequency", "rating": "Credit Rating", "issuer": "Issuer"},
]

# the evidence columns written per row, in display order: (field, source)
EVIDENCE_COLUMNS = [
    ("symbol", S_DEBT), ("symbol", S_BHAV), ("symbol", S_UPX), ("symbol", S_WDM), ("symbol", S_CORP),
    ("trading_symbol", S_UPX),
    ("coupon", S_CORP), ("coupon", S_WDM), ("coupon", S_DEBT), ("coupon", S_UPX),
    ("maturity", S_CORP), ("maturity", S_WDM), ("maturity", S_DEBT), ("maturity", S_UPX),
    ("frequency", S_CORP), ("frequency", S_WDM),
    ("rating", S_CORP),
]
FIELDS = ["symbol", "trading_symbol", "coupon", "maturity", "frequency", "rating"]


def evidence_header(field_: str, source: str) -> str:
    return f"{field_}: {source}"


# ----------------------------------------------------------------------------- parsing the sources
def _date(s: str) -> Optional[date]:
    try:
        d, m, y = (s or "").strip().upper().split("-")
        y = int(y)
        return date(2000 + y if y < 100 else y, MONTHS[m], int(d))
    except (ValueError, KeyError):
        return None


COUPON_NAME = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%?\s*$")  # "8.72%" or a bare "8.0956"


def detect_layout(text: str):
    """(layout, csv text from its header row) or None. The corporate report has title lines above the header."""
    lines = text.lstrip("﻿").splitlines()
    for i, line in enumerate(lines[:25]):
        head = {h.strip() for h in next(csv.reader([line]), [])}
        for lay in LAYOUTS:
            need = {lay[k] for k in ("isin", "symbol", "name", "mat", "freq")}
            if need <= head:
                return lay, "\n".join(lines[i:])
    return None


def parse_nse_list(text: str) -> dict[str, dict]:
    """WDM list or corporate bond report -> {isin: {symbol, coupon, maturity, freq, rating, issuer, label}}."""
    found = detect_layout(text)
    if found is None:
        raise FormatChanged("Not an NSE WDM list or corporate bond report (columns not recognised)")
    lay, body = found
    out: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(body)):
        isin = (r.get(lay["isin"]) or "").strip().upper()
        if not isin:
            continue
        m = COUPON_NAME.match(r.get(lay["name"]) or "")
        out[isin] = {
            "symbol": (r.get(lay["symbol"]) or "").strip(),
            "coupon": float(m[1]) if m else None,
            "maturity": _date(r.get(lay["mat"]) or ""),
            "freq": FREQ.get((r.get(lay["freq"]) or "").strip().lower()),
            "rating": ((r.get(lay["rating"]) or "").strip() if lay["rating"] else ""),
            "issuer": ((r.get(lay["issuer"]) or "").strip() if lay["issuer"] else ""),
            "label": lay["label"],
        }
    return out


def index_upstox_nse(raw_gz: bytes) -> dict[str, dict]:
    """Upstox NSE list -> {isin: {trading_symbol, name, coupon (parsed from name), token, series}} for NSE_EQ."""
    data = json.loads(gzip.decompress(raw_gz))
    sample = next((x for x in data if x.get("segment") == "NSE_EQ"), None)
    if sample is None or not {"isin", "trading_symbol", "name"} <= set(sample):
        raise FormatChanged("Upstox NSE instrument list changed shape")
    out = {}
    for x in data:
        if x.get("segment") != "NSE_EQ" or not x.get("isin"):
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", x.get("name", ""))
        out[x["isin"].strip().upper()] = {
            "trading_symbol": (x.get("trading_symbol") or "").strip(), "name": x.get("name", ""),
            "coupon": float(m[1]) if m else None, "token": x.get("exchange_token"), "series": x.get("instrument_type")}
    return out


def find_newest(pattern: str, *dirs: Path) -> Optional[Path]:
    found = [p for d in dirs if d.is_dir() for p in d.glob(pattern)]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


# ----------------------------------------------------------------------------- deciding a field
def decide(values: list[tuple[str, object, bool]], same):
    """values = [(source, value, is_official)] in precedence order (value not None).
    -> (final, status, sources that agree with final, [(source, value)] that differ)."""
    if not values:
        return None, "none", [], []
    base = next((v for v in values if v[2]), values[0])
    final = base[1]
    agree = [s for s, v, _ in values if same(v, final)]
    differ = [(s, v) for s, v, _ in values if not same(v, final)]
    official_differ = any(o for s, v, o in values if not same(v, final))
    if official_differ:
        status = "CONFLICT"
    elif differ:
        status = "supporting differs"
    elif len(agree) >= 2:
        status = f"agree({len(agree)})"
    else:
        status = "single"
    return final, status, agree, differ


def _num_same(a, b):
    return abs(float(a) - float(b)) <= COUPON_TOL


def _eq(a, b):
    return a == b


def _txt_same(a, b):
    return str(a).strip().upper() == str(b).strip().upper()


# ----------------------------------------------------------------------------- the reconciliation
def reconcile_rows(rows: list[dict], ctx: dict, overrides: dict, today: date):
    """Update every row from all sources. Returns (rows, evidence_rows, stats)."""
    debt, corp, wdm = ctx.get("debt", {}), ctx.get("corp", {}), ctx.get("wdm", {})
    upx = {"NSE": ctx.get("upx_nse", {}), "BSE": ctx.get("upx_bse", {})}
    bhav = {"NSE": ctx.get("nse_bhav", {}), "BSE": ctx.get("bse_bhav", {})}
    evidence: list[dict] = []
    stats: dict = {}

    for row in rows:
        isin, ex = row["isin"], row["exchange"]
        d, c, w = debt.get(isin), corp.get(isin), wdm.get(isin)
        u = upx.get(ex, {}).get(isin)
        b = bhav.get(ex, {}).get(isin)
        ov = overrides.get(isin, {})
        ev: dict = {}

        # --- symbol (exchange specific)
        if ex == "NSE":
            sym = [(S_DEBT, d and d["symbol"]), (S_BHAV, b and b.get("symbol")), (S_UPX, u and u["trading_symbol"])]
        elif ex == "BSE":
            sym = [(S_BHAV, b and b.get("symbol")), (S_UPX, u and u.get("trading_symbol"))]
        else:  # WDM
            sym = [(S_WDM, w and w["symbol"]), (S_CORP, c and c["symbol"])]
        sym = [(s, v, True) for s, v in sym if v]
        for s, v, _ in sym:
            ev[("symbol", s)] = v
        symbol, sym_status, _, _ = decide(sym, _txt_same)

        # --- trading symbol (Upstox's instrument symbol for that exchange; WDM is not tradable there)
        ts = (u or {}).get("trading_symbol") if ex in ("NSE", "BSE") else None
        if ts:
            ev[("trading_symbol", S_UPX)] = ts
        ts_status = "single" if ts else "none"

        # --- coupon
        cp = []
        for s, rec, key in ((S_CORP, c, "coupon"), (S_WDM, w, "coupon"), (S_DEBT, d, "coupon")):
            if rec and rec.get(key) is not None:
                cp.append((s, rec[key], True))
        if u and u.get("coupon") is not None:
            cp.append((S_UPX, u["coupon"], False))
        for s, v, _ in cp:
            ev[("coupon", s)] = v
        coupon, cp_status, cp_agree, cp_differ = decide(cp, _num_same)

        # --- maturity
        mt = []
        for s, rec in ((S_CORP, c), (S_WDM, w)):
            if rec and rec.get("maturity"):
                mt.append((s, rec["maturity"], True))
        if d and d.get("maturity"):
            mt.append((S_DEBT, d["maturity"], True))
        if ex == "BSE" and u and u.get("maturity_date"):
            mt.append((S_UPX, u["maturity_date"], False))
        for s, v, _ in mt:
            ev[("maturity", s)] = v.isoformat()
        maturity, mt_status, mt_agree, mt_differ = decide(mt, _eq)

        # --- frequency (only the two NSE lists carry it)
        fq = [(s, rec["freq"], True) for s, rec in ((S_CORP, c), (S_WDM, w)) if rec and rec.get("freq")]
        for s, v, _ in fq:
            ev[("frequency", s)] = v
        freq, fq_status, _, fq_differ = decide(fq, _eq)

        # --- rating (the corporate report has the column; every value is empty in practice)
        rt = [(S_CORP, c["rating"], True)] if c and c.get("rating") else []
        for s, v, _ in rt:
            ev[("rating", s)] = v
        rating, rt_status, _, _ = decide(rt, _txt_same)

        # --- manual overrides always win (data/overrides.csv)
        if (ov.get("coupon_freq") or "").strip():
            freq, fq_status = int((ov["coupon_freq"]).strip()), "manual override"
        if (ov.get("rating") or "").strip():
            rating, rt_status = ov["rating"].strip(), "manual override"

        # --- write back
        if coupon is not None:
            row["coupon_rate"] = coupon
        if maturity is not None:
            row["maturity_date"] = maturity.isoformat()
        row["coupon_freq"] = freq if freq is not None else ""
        if fq_status == "CONFLICT":
            row["coupon_freq"] = ""  # the NSE lists disagree: never guess
        row["rating"] = rating or ""
        row["symbol"] = symbol or ""
        row["trading_symbol"] = ts or ""

        official_terms = (cp_status != "none" and any(s in OFFICIAL_ORDER for s in cp_agree)
                          and mt_status != "none" and any(s in OFFICIAL_ORDER for s in mt_agree))
        conflict_bits = []
        if cp_status == "CONFLICT":
            conflict_bits.append("coupon: " + ", ".join(f"{s}={v}" for s, v, _ in cp))
        if mt_status == "CONFLICT":
            conflict_bits.append("maturity: " + ", ".join(f"{s}={v}" for s, v, _ in mt))
        if fq_status == "CONFLICT":
            conflict_bits.append("frequency: " + ", ".join(f"{s}={v}" for s, v, _ in fq))
        row["terms_conflict"] = "sources disagree - " + "; ".join(conflict_bits) if conflict_bits else ""
        verified = official_terms and cp_status != "CONFLICT" and mt_status != "CONFLICT"
        if (ov.get("terms_verified") or "").strip().lower() == "yes":
            verified = True
        row["terms_verified"] = "yes" if verified else "no"

        # --- which sources the terms rest on, and where the price comes from
        terms_srcs = []
        for s in list(dict.fromkeys(cp_agree + mt_agree)):
            terms_srcs.append(f"Upstox {ex} list name" if s == S_UPX else s)
        base_src = " + ".join(terms_srcs) if terms_srcs else "no source"
        if not official_terms and terms_srcs:
            base_src += " (parsed from name, unverified)"
        price_src = {"NSE": "NSE bhavcopy", "BSE": "BSE bhavcopy", "WDM": "WDM last trade"}[ex]
        row["terms_source"] = base_src + (" + overrides" if ov else "") + (f"; price: {price_src}" if row.get("ref_close") not in ("", None) else "")

        statuses = {"symbol": sym_status, "trading_symbol": ts_status, "coupon": cp_status, "maturity": mt_status,
                    "frequency": fq_status, "rating": rt_status if rt else "none (no permitted source has it)"}
        row["checks"] = " | ".join(f"{k} {v}" for k, v in statuses.items() if k != "trading_symbol")

        e = {"isin": isin, "exchange": ex}
        for fld, src in EVIDENCE_COLUMNS:
            e[evidence_header(fld, src)] = ev.get((fld, src), "")
        finals = {"symbol": row["symbol"], "trading_symbol": row["trading_symbol"], "coupon": row["coupon_rate"],
                  "maturity": row["maturity_date"], "frequency": row["coupon_freq"], "rating": row["rating"]}
        for fld in FIELDS:
            e[f"{fld} (final)"] = finals[fld]
            e[f"{fld} status"] = statuses[fld].split(" (")[0] if fld == "rating" else statuses[fld]
        evidence.append(e)
        for fld in FIELDS:
            key = (ex, fld, e[f"{fld} status"].split("(")[0].strip() if e[f"{fld} status"].startswith("agree") else e[f"{fld} status"])
            stats[key] = stats.get(key, 0) + 1
    return rows, evidence, stats


def evidence_fieldnames() -> list[str]:
    cols = ["isin", "exchange"] + [evidence_header(f, s) for f, s in EVIDENCE_COLUMNS]
    for fld in FIELDS:
        cols += [f"{fld} (final)", f"{fld} status"]
    return cols


def write_evidence(evidence: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=evidence_fieldnames())
        w.writeheader()
        w.writerows(evidence)
