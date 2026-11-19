from datetime import date, datetime, timezone

import pytest

from app.models import BondTerms
from app.service import build_view
from app.sources import nse_universe as nu
from app.sources import wdm_universe as wu
from app.sources.terms_csv import load_terms

TODAY = date(2026, 11, 19)
HEAD = ("SECTYPE,SECURITY,ISSUE_NAME,ISSUE_DESC,ISSUE_DATE,MAT_DATE,Last IP Dt,Next IP Dt,Cpn Freq,"
        "Last Traded Date,Last Traded Price (in Rs.),ISIN NO.,STATUS\n")
ROWS = (
    "PT,A27,10.04%,IRFC 10.04% 2027(S-54B),07-Jun-2007,07-Jun-2027,,,Half Yearly,12-Nov-2026,101.5,INE053F09EO6,Listed\n"
    "DB,B30,8.72%,IRF 8.72% 2031,01-Jan-2020,01-Jan-2031,,,Yearly,,0,INE053F09GY0,Listed\n"            # never traded
    "CP,C27,0%,SOME CP,01-Jan-2026,01-Jan-2027,,,0,,0,INE111A14AA1,Listed\n"                           # commercial paper
    "GS,D36,8.33%,GOI LOAN 8.33% 2036,07-Jun-2006,07-Jun-2036,,,Half Yearly,12-Nov-2025,111.5,IN0020060045,Listed\n"  # govt
    "DB,E26,9%,OLD 9% 2026,01-Jan-2016,01-Jan-2026,,,Yearly,,0,INE222A07011,Listed\n"                   # matured
    "DB,F30,RESET,RESET BOND 2030,01-Jan-2020,01-Jan-2030,,,Yearly,,0,INE333A07011,Listed\n"           # reset rate
    "DB,G30,9.5119,XYZ 9.5119% 2030,01-Jan-2020,01-Jan-2030,,,Quarterly,,0,INE444A07011,Listed\n"      # bare number, confirmed by desc
    "DB,H30,9.1,XYZ 2030 SERIES,01-Jan-2020,01-Jan-2030,,,Yearly,,0,INE555A07011,Listed\n"            # bare number NOT confirmed
    "DB,I30,8%,IN NSE ALREADY,01-Jan-2020,01-Jan-2030,,,Yearly,,0,INE666A07011,Listed\n"
)


def test_parse_coupon_rules():
    assert wu.parse_coupon("8.72%", "x") == 8.72
    assert wu.parse_coupon("9.5119", "XYZ 9.5119% 2030") == 9.5119
    assert wu.parse_coupon("9.1", "XYZ 2030 SERIES") is None  # unconfirmed bare number: never guess
    assert wu.parse_coupon("RESET", "RESET 8.5% 2030") is None


def test_parse_wdmlist_filters_and_counts():
    parsed, skipped = wu.parse_wdmlist(HEAD + ROWS, TODAY)
    assert [x["isin"] for x in parsed] == ["INE053F09EO6", "INE053F09GY0", "INE444A07011", "INE666A07011"]
    assert skipped == {"not_corporate": 1, "commercial_paper": 1, "matured": 1, "unreadable_coupon": 2}
    traded, never = parsed[0], parsed[1]
    assert traded["ref_price"] == 101.5 and traded["ref_date"] == date(2026, 11, 12) and traded["freq"] == 2
    assert never["ref_price"] is None and never["ref_date"] is None  # price 0 / no date = never traded
    with pytest.raises(nu.FormatChanged):
        wu.parse_wdmlist("A,B\n1,2\n", TODAY)


def test_build_rows_skips_existing_sets_face_100_and_honours_overrides():
    parsed, _ = wu.parse_wdmlist(HEAD + ROWS, TODAY)
    ov = {"INE053F09GY0": {"isin": "INE053F09GY0", "face_value": "1000", "coupon_freq": "4"}}
    rows = wu.build_rows(parsed, ov, {"INE666A07011"}, date(2026, 11, 18), "NSE WDM list x.csv")
    by = {r["isin"]: r for r in rows}
    assert set(by) == {"INE053F09EO6", "INE053F09GY0", "INE444A07011"}  # INE666 already in the universe
    a = by["INE053F09EO6"]
    assert a["exchange"] == "WDM" and a["terms_verified"] == "yes" and a["face_value"] == 100
    assert a["ref_close"] == 101.5 and a["ref_date"] == "2026-11-12" and "per Rs 100" in a["terms_source"]
    assert by["INE053F09GY0"]["face_value"] == "1000" and by["INE053F09GY0"]["coupon_freq"] == "4"
    assert by["INE053F09GY0"]["ref_close"] == ""


def test_find_wdmlist_picks_newest_by_filename_date(tmp_path):
    (tmp_path / "wdmlist_17112026.csv").write_text("x")
    (tmp_path / "wdmlist_18112026.csv").write_text("x")
    assert wu.find_wdmlist(tmp_path).name == "wdmlist_18112026.csv"
    assert wu.find_wdmlist(tmp_path / "nope") is None


def _wdm_term(tmp_path, ref_date):
    parsed, _ = wu.parse_wdmlist(HEAD + ROWS.replace("12-Nov-2026", ref_date), TODAY)
    rows = wu.build_rows(parsed, {}, set(), TODAY, "NSE WDM list x.csv")
    p = tmp_path / "b.csv"
    nu.write_csv(rows, p)
    return load_terms(p)["WDM_EQ|INE053F09EO6"]


def test_wdm_bond_is_not_streamed_and_reference_yield_needs_a_recent_trade(tmp_path):
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    recent = _wdm_term(tmp_path, "12-Nov-2026")
    assert recent.exchange == "WDM" and not recent.streamable
    v = build_view(recent, None, now, 300)
    assert v.status == "NO_QUOTE" and v.signal is None and v.ytm_ref is not None and v.notes == []
    old = build_view(_wdm_term(tmp_path, "12-Nov-2025"), None, now, 300)
    assert old.ytm_ref is None and "days old" in old.notes[0]


def test_streamable_rules():
    def t(ex, ok=True):
        return BondTerms(isin="INE1", coupon_rate=9, maturity_date=date(2030, 1, 1), exchange=ex, terms_verified=ok)
    assert t("NSE").streamable and t("BSE", True).streamable
    assert not t("BSE", False).streamable and not t("WDM").streamable


def test_negative_yield_is_suppressed_with_a_note():
    # price far above everything the bond still pays (e.g. a cumulative bond whose redemption amount is unknown)
    t = BondTerms(isin="INE9", coupon_rate=0.0, maturity_date=date(2026, 12, 27), face_value=1000.0,
                  ref_close=1312.22, ref_date=date(2026, 11, 18))
    v = build_view(t, None, datetime(2026, 11, 19, tzinfo=timezone.utc), 300)
    assert v.ytm_ref is None and v.notes and "negative yield" in v.notes[0]
    ok = t.model_copy(update={"ref_close": 990.0})
    assert build_view(ok, None, datetime(2026, 11, 19, tzinfo=timezone.utc), 300).ytm_ref > 0
