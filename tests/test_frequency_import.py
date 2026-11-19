from datetime import date

import pytest

from app.sources import frequency_import as fi

HEAD = "ISIN,Reporting Date,Frequency\n"


def test_latest_reporting_date_wins_and_two_digit_years_parse():
    t = HEAD + "INE1,01-Jan-22,Yearly\nINE1,14-Mar-24,Quarterly\nINE1,01-Aug-2022,Monthly\n"
    d, f = fi.read_filings(t)["INE1"]
    assert d == date(2024, 3, 14) and f == {4}


def test_same_day_conflict_is_kept_for_reporting_and_others_skipped():
    t = HEAD + "INE2,14-Mar-24,Yearly\nINE2,14-Mar-24,Monthly\nINE3,14-Mar-24,Others\n"
    r = fi.read_filings(t)
    assert r["INE2"][1] == {1, 12} and "INE3" not in r


def test_wrong_file_is_rejected():
    with pytest.raises(ValueError):
        fi.read_filings("A,B\n1,2\n")


def test_merge_rules():
    filings = {"A": (date(2024, 1, 1), {1}), "B": (date(2024, 1, 1), {1, 2}),
               "C": (date(2024, 1, 1), {4}), "D": (date(2024, 1, 1), {12}), "Z": (date(2024, 1, 1), {1})}
    coupon = {"A", "B", "C", "D"}  # Z is not a coupon bond in our universe
    ov = {"C": {"isin": "C", "coupon_freq": "2"}, "D": {"isin": "D", "coupon_freq": "12"}}
    added, conflicts, disagree = fi.merge(filings, coupon, ov, replace=False)
    assert added == {"A": 1} and conflicts == ["B"] and disagree == [("C", 2, 4)]
    added, _, _ = fi.merge(filings, coupon, ov, replace=True)
    assert added == {"A": 1, "C": 4}


WDM = ("SECTYPE,SECURITY,ISSUE_NAME,ISSUE_DESC,ISSUE_DATE,MAT_DATE,Last IP Dt,Next IP Dt,Cpn Freq,"
       "Last Traded Date,Last Traded Price (in Rs.),ISIN NO.,STATUS\n"
       "PT,A27,10.04%,x,07-Jun-2007,07-Jun-2027,,,Half Yearly,,,INE1,Listed\n"
       "PT,B27,9%,x,01-Jan-2020,01-Jan-2030,,,0,,,INE2,Listed\n"          # '0' = no periodic coupon -> skipped
       "PT,C27,9%,x,01-Jan-2020,01-Jan-2030,,,Quarterly,,,INE3,Listed\n"  # maturity differs from ours
       "PT,D27,8%,x,01-Jan-2020,01-Jan-2031,,,Monthly,,,INE4,Listed\n"
       "PT,E27,8%,x,01-Jan-2020,01-Jan-2031,,,4,,,INE5,Listed\n")          # numeric label accepted


def test_wdmlist_detected_and_parsed():
    assert fi.is_wdmlist(WDM) and not fi.is_wdmlist("ISIN,Reporting Date,Frequency\n")
    assert fi.asof_from_filename("wdmlist_18112026.csv") == date(2026, 11, 18)
    freqs, terms = fi.read_wdmlist(WDM, date(2026, 11, 18))
    assert freqs["INE1"][1] == {2} and "INE2" not in freqs and freqs["INE5"][1] == {4}
    assert terms["INE1"] == (10.04, date(2027, 6, 7))


def test_wdmlist_terms_cross_check_skips_wrong_instrument():
    _, terms = fi.read_wdmlist(WDM, date(2026, 11, 18))
    bonds = {"INE1": {"coupon_rate": "10.04", "maturity_date": "2027-06-07"},
             "INE3": {"coupon_rate": "9", "maturity_date": "2032-01-01"},   # different maturity
             "INE4": {"coupon_rate": "8.5", "maturity_date": "2031-01-01"}}  # different coupon
    assert fi.terms_mismatches(terms, bonds) == {"INE3", "INE4"}


def test_coupon_rounding_in_debt_csv_is_not_a_mismatch():
    terms = {"INE1": (7.685, date(2028, 7, 24)), "INE2": (9.71, date(2028, 7, 27))}
    bonds = {"INE1": {"coupon_rate": "7.69", "maturity_date": "2028-07-24"},   # 2-dp rounding of 7.685
             "INE2": {"coupon_rate": "9.17", "maturity_date": "2028-07-27"}}   # real difference (digits swapped)
    assert fi.terms_mismatches(terms, bonds) == {"INE2"}
