import gzip
import json
from datetime import date, datetime, timezone

import pytest

from app.models import Level, Quote
from app.service import build_view
from app.sources import bse_universe as bu
from app.sources.terms_csv import load_terms
from app.sources import nse_universe as nu

TODAY = date(2026, 11, 19)


def test_parse_name_real_shapes():
    assert bu.parse_name("SPPL-10%-11-6-28-PVT") == ("SPPL", 10.0, date(2028, 6, 11))
    assert bu.parse_name("HUDCO-7.64%-8-2-31-TAXFREENCD") == ("HUDCO", 7.64, date(2031, 2, 8))
    assert bu.parse_name("NJCPL-9.80%-25-07-28-PVT")[1:] == (9.8, date(2028, 7, 25))
    assert bu.parse_name("ABC-DEF-9%-1-1-2030-NCD")[0] == "ABC-DEF"  # hyphens in issuer, 4-digit year
    for bad in ("IREDA-8.40%-PERPETUAL-PVT", "PFCL-16-4-27-CP", "SBI Mutual Fund", "X-9%-31-2-30-PVT"):
        assert bu.parse_name(bad) is None  # incl. impossible date 31 Feb


def _gz(items):
    return gzip.compress(json.dumps(items).encode())


def inst(isin, name, typ="F", seg="BSE_EQ"):
    return {"segment": seg, "instrument_type": typ, "isin": isin, "name": name}


def test_parse_instruments_filters_type_isin_and_counts_skips():
    raw = _gz([{"segment": "BSE_INDEX", "name": "SENSEX"},  # first entry has no ISIN
               inst("INE111A07011", "AAA-9%-1-1-30-NCD"),
               inst("INE222A14011", "BBB-26-2-27-CP"),            # CP: name does not parse -> skipped
               inst("INE333A01011", "CCC-9%-1-1-30-NCD", typ="A"),  # not a debt group
               inst("IN0020200153", "577GS2030")])                # government, not INE
    out, skipped = bu.parse_instruments(raw)
    assert [x["isin"] for x in out] == ["INE111A07011"] and skipped == 1
    with pytest.raises(ValueError):
        bu.parse_instruments(_gz([{"segment": "BSE_EQ", "foo": 1}]))


def test_build_rows_bse_only_are_unverified_and_nse_overlap_is_skipped():
    ins = [{"isin": "INE1", "name": "A-9%-1-1-30-NCD", "issuer": "A", "coupon_rate": 9.0, "maturity_date": date(2030, 1, 1)},
           {"isin": "INE2", "name": "B-8%-1-1-31-NCD", "issuer": "B", "coupon_rate": 8.0, "maturity_date": date(2031, 1, 1)},
           {"isin": "INE3", "name": "C-8%-1-1-20-NCD", "issuer": "C", "coupon_rate": 8.0, "maturity_date": date(2020, 1, 1)}]
    rows, st = bu.build_rows(ins, {"INE1": {"ref_close": 99.0}}, {}, {"INE2"}, TODAY)
    assert [r["isin"] for r in rows] == ["INE1"] and st["also_on_nse"] == {"INE2"}  # INE3 matured
    assert rows[0]["terms_verified"] == "no" and rows[0]["face_value"] == "" and rows[0]["exchange"] == "BSE"
    # the user can confirm via overrides
    ov = {"INE1": {"isin": "INE1", "terms_verified": "yes", "face_value": "1000", "coupon_freq": "1"}}
    r = bu.build_rows(ins, {}, ov, set(), TODAY)[0][0]
    assert r["terms_verified"] == "yes" and r["face_value"] == "1000" and "overrides" in r["terms_source"]


def test_agreement_metric():
    ins = [{"isin": "INE1", "coupon_rate": 9.0, "maturity_date": date(2030, 1, 1)},
           {"isin": "INE2", "coupon_rate": 8.0, "maturity_date": date(2026, 3, 3)}]  # name typo: real year is 2036
    nse = {"INE1": {"coupon_rate": "9", "maturity_date": "2030-01-01"},
           "INE2": {"coupon_rate": "8", "maturity_date": "2036-03-03"}}
    assert bu.agreement_with_nse(ins, nse) == (1, 2)


def test_unverified_bse_bond_gets_no_yield_until_confirmed(tmp_path):
    ins = [{"isin": "INE1", "name": "A-9%-1-1-30-NCD", "issuer": "A", "coupon_rate": 9.0, "maturity_date": date(2030, 1, 1)}]
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    q = Quote(isin="INE1", ltp=800, asks=[Level(price=800, qty=100)], received_at=now)

    def view(ov):
        rows, _ = bu.build_rows(ins, {}, ov, set(), TODAY)
        p = tmp_path / "b.csv"
        nu.write_csv(rows, p)
        t = load_terms(p)["INE1"]
        return t, build_view(t, q, now, 300)

    t, v = view({})
    assert t.exchange == "BSE" and t.instrument_key == "BSE_EQ|INE1" and not t.terms_verified
    assert v.ytm_best_ask is None and v.signal is None and "unverified" in v.notes[0]
    _, v2 = view({"INE1": {"isin": "INE1", "terms_verified": "yes", "face_value": "1000", "coupon_freq": "1"}})
    assert v2.ytm_best_ask is not None and v2.notes == []
