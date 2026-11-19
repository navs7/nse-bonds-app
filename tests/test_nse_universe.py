import io
import zipfile
from datetime import date, datetime, timezone

import pytest

from app.models import Level, Quote
from app.service import build_view
from app.sources import nse_universe as nu
from app.sources.terms_csv import load_terms

DEBT = (
    "SYMBOL, NAME OF COMPANY, SERIES, FACE VALUE, PAID UP VALUE, MKT LOT, IP RATE, DATE OF LISTING,"
    " DATE OF ALLOTMENT, REDEMPTION DATE, REDEMPTION AMT, CONVERSION DATE, CONVERSION AMT, INTEREST PAYMENT DT\n"
    "1003SCL31A,Sammaan Capital Limited,N1,1000,1000,1,10.03,27-SEP-2024,,25-SEP-2031,,,,,INE148I07UR1\n"
    "0HFL28,Hero FinCorp Limited,N0,100000,51780,1,0,02-JUL-2026,,30-JUN-2028,,,,,INE957N07880\n"
    "577GS2030,Government of India,GS,100,100,1,5.77,04-AUG-2020,,03-AUG-2030,,,,,IN0020200153\n"
    "OLD25,Old Co,N1,1000,1000,1,9,01-JAN-2015,,01-JAN-2020,,,,,INE000000001\n"
    "BADROW,x,N1\n"
)

BHAV_HEAD = "TradDt,ISIN,SctySrs,ClsPric,LastPric,TtlTradgVol\n"


def bhav_zip(body: str, head: str = BHAV_HEAD) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", head + body)
    return buf.getvalue()


TODAY = date(2026, 11, 19)


def test_parse_debt_master_reads_isin_from_unnamed_last_column():
    m = nu.parse_debt_master(DEBT)
    assert [r["isin"] for r in m] == ["INE148I07UR1", "INE957N07880", "IN0020200153", "INE000000001"]
    assert m[0]["coupon_rate"] == 10.03 and m[0]["maturity_date"] == date(2031, 9, 25)


def test_header_change_fails_loudly():
    with pytest.raises(nu.FormatChanged):
        nu.parse_debt_master("FOO,BAR\n1,2\n")
    with pytest.raises(nu.FormatChanged):
        nu.parse_bhavcopy(bhav_zip("", BHAV_HEAD.replace("ClsPric", "Other")))


def test_build_rows_filters_category_and_matured_and_joins_prices():
    m = nu.parse_debt_master(DEBT)
    bhav = nu.parse_bhavcopy(bhav_zip("2026-11-18,INE148I07UR1,N1,1010.5,1010,25\n"))
    rows = nu.build_rows(m, bhav, {}, {"corporate"}, TODAY)
    assert {r["isin"] for r in rows} == {"INE148I07UR1", "INE957N07880"}  # no G-Sec, no matured
    r = next(x for x in rows if x["isin"] == "INE148I07UR1")
    assert r["ref_close"] == 1010.5 and r["ref_date"] == "2026-11-18" and r["coupon_freq"] == ""
    assert nu.build_rows(m, bhav, {}, {"gsec"}, TODAY)[0]["isin"] == "IN0020200153"


def test_overrides_supply_frequency_and_rating(tmp_path):
    m = nu.parse_debt_master(DEBT)
    ov = {"INE148I07UR1": {"isin": "INE148I07UR1", "coupon_freq": "4", "rating": "AA"}}
    r = next(x for x in nu.build_rows(m, {}, ov, {"corporate"}, TODAY) if x["isin"] == "INE148I07UR1")
    assert r["coupon_freq"] == "4" and r["rating"] == "AA" and "overrides" in r["terms_source"]
    p = tmp_path / "b.csv"
    nu.write_csv([r], p)
    t = load_terms(p)["INE148I07UR1"]
    assert t.coupon_freq == 4 and t.rating == "AA" and t.ref_close is None


def _terms(tmp_path, overrides):
    m = nu.parse_debt_master(DEBT)
    rows = nu.build_rows(m, {}, overrides, {"corporate"}, TODAY)
    p = tmp_path / "b.csv"
    nu.write_csv(rows, p)
    return load_terms(p)


def test_yield_is_withheld_when_frequency_unknown_or_paid_up_differs(tmp_path):
    terms = _terms(tmp_path, {})
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    q = Quote(isin="x", ltp=1000, asks=[Level(price=1000, qty=1)], received_at=now)
    v = build_view(terms["INE148I07UR1"], q, now, 300)
    assert v.ytm_ltp is None and "frequency unknown" in v.notes[0]
    z = build_view(terms["INE957N07880"], q, now, 300)  # paid-up 51780 vs face 100000
    assert z.ytm_ltp is None and any("verify outstanding" in n for n in z.notes)
    ok = _terms(tmp_path, {"INE148I07UR1": {"isin": "INE148I07UR1", "coupon_freq": "4"}})
    assert build_view(ok["INE148I07UR1"], q, now, 300).ytm_ltp is not None


def test_duplicate_isins_are_refused(tmp_path):
    rows = [{"isin": "INE1"}, {"isin": "INE2"}, {"isin": "INE1"}]
    with pytest.raises(ValueError, match="duplicate ISIN"):
        nu.assert_unique_isins(rows)
    with pytest.raises(ValueError):
        nu.write_csv(rows, tmp_path / "x.csv")
    nu.assert_unique_isins([{"isin": "INE1"}, {"isin": "INE2"}])  # unique list passes
