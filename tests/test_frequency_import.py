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
