from datetime import date

import pytest

from app.sources import reconcile as rc
from app.sources.nse_universe import FormatChanged

TODAY = date(2026, 11, 19)

WDM = ("SECTYPE,SECURITY,ISSUE_NAME,ISSUE_DESC,ISSUE_DATE,MAT_DATE,Last IP Dt,Next IP Dt,Cpn Freq,"
       "Last Traded Date,Last Traded Price (in Rs.),ISIN NO.,STATUS\n"
       "PT,IRFC27,10.04%,IRFC 10.04% 2027,07-Jun-2007,07-Jun-2027,,,Half Yearly,,0,INE1,Listed\n"
       "DB,X30,8.0956,X 8.0956% 2030,01-Jan-2020,01-Jan-2030,,,Quarterly,,0,INE2,Listed\n"
       "DB,Z30,RESET,RESET 2030,01-Jan-2020,01-Jan-2030,,,Yearly,,0,INE3,Listed\n")

CORP = ("Sectype,Security,Issue Name,Issue Desc,Issuer,Face Value,Credit Rating,Issue Date,Maturity Date,Record Date,"
        "Step up Coupons,Coupon Frequency,ISIN,Status\n")
CORP_TEXT = (",,,National Stock Exchange Of India Limited,,\n,,,Negotiated Trade Reporting Platform,,\n"
             "Details report for :- 18-Nov-2026,,\n,,\n" + CORP +
             "PT,IRFC27,10.04%,IRFC 10.04% 2027,INDIAN RAILWAY FINANCE CORP,100.00,AAA,07-Jun-2007,07-Jun-2027,,,Yearly,INE1,Listed\n"
             "DB,X30,8.0956,X 8.0956% 2030,X LTD,100.00,,01-Jan-2020,01-Jan-2030,,,Quarterly,INE2,Listed\n")


def row(isin="INE1", ex="NSE", **kw):
    r = {"isin": isin, "exchange": ex, "coupon_rate": 0.0, "maturity_date": "2000-01-01", "coupon_freq": "",
         "rating": "", "terms_source": "", "ref_close": "", "terms_verified": "no", "terms_conflict": ""}
    r.update(kw)
    return r


def ctx(**kw):
    base = {"debt": {}, "corp": {}, "wdm": {}, "upx_nse": {}, "upx_bse": {}, "nse_bhav": {}, "bse_bhav": {}}
    base.update(kw)
    return base


def run(r, c, ov=None):
    rows, ev, stats = rc.reconcile_rows([r], c, ov or {}, TODAY)
    return rows[0], ev[0]


def test_both_nse_lists_are_parsed_including_title_lines_symbol_rating_issuer():
    wdm = rc.parse_nse_list(WDM)
    assert wdm["INE1"]["symbol"] == "IRFC27" and wdm["INE1"]["freq"] == 2 and wdm["INE1"]["coupon"] == 10.04
    assert wdm["INE2"]["coupon"] == 8.0956 and wdm["INE3"]["coupon"] is None  # RESET: never guessed
    corp = rc.parse_nse_list(CORP_TEXT)
    assert corp["INE1"]["rating"] == "AAA" and corp["INE1"]["issuer"].startswith("INDIAN RAILWAY")
    assert corp["INE1"]["freq"] == 1 and corp["INE1"]["label"] == rc.S_CORP
    with pytest.raises(FormatChanged):
        rc.parse_nse_list("A,B\n1,2\n")


def test_decide_statuses():
    same = rc._num_same
    assert rc.decide([], same)[1] == "none"
    assert rc.decide([("a", 8.0, True)], same)[1] == "single"
    assert rc.decide([("a", 8.0956, True), ("b", 8.09, True), ("c", 8.0956, False)], same)[:2] == (8.0956, "agree(3)")
    assert rc.decide([("a", 8.7, True), ("n", 8.65, False)], same)[1] == "supporting differs"
    assert rc.decide([("a", 9.43, True), ("b", 6.43, True)], same)[1] == "CONFLICT"
    # the first OFFICIAL value is the final one, even if a supporting source is listed first
    assert rc.decide([("n", 1, False), ("a", 2, True)], rc._eq)[0] == 2


def test_nse_row_all_sources_agree_gets_symbols_precise_coupon_and_is_verified():
    c = ctx(debt={"INE2": {"symbol": "809ABC30", "coupon": 8.09, "maturity": date(2030, 1, 1)}},
            wdm=rc.parse_nse_list(WDM), corp=rc.parse_nse_list(CORP_TEXT),
            upx_nse={"INE2": {"trading_symbol": "809ABC30", "coupon": 8.0956, "name": "X 8.0956% 2030"}})
    r, ev = run(row("INE2"), c)
    assert r["coupon_rate"] == 8.0956 and r["maturity_date"] == "2030-01-01" and r["coupon_freq"] == 4
    assert r["symbol"] == "809ABC30" and r["trading_symbol"] == "809ABC30" and r["terms_verified"] == "yes"
    assert r["terms_conflict"] == "" and "coupon agree(4)" in r["checks"] and "frequency agree(2)" in r["checks"]
    assert ev["coupon (final)"] == 8.0956 and ev["coupon status"] == "agree(4)"
    assert ev[rc.evidence_header("coupon", rc.S_DEBT)] == 8.09  # each source's own value is kept


def test_rating_comes_only_from_a_source_and_is_blank_otherwise():
    r, ev = run(row("INE1"), ctx(corp=rc.parse_nse_list(CORP_TEXT), wdm=rc.parse_nse_list(WDM)))
    assert r["rating"] == "AAA" and ev["rating status"] == "single"
    r2, ev2 = run(row("INE2"), ctx(corp=rc.parse_nse_list(CORP_TEXT)))  # report has the column, but it is empty
    assert r2["rating"] == "" and ev2["rating status"] == "none"


def test_conflict_between_official_sources_blocks_verification():
    c = ctx(debt={"INE1": {"symbol": "S", "coupon": 9.17, "maturity": date(2027, 6, 7)}}, wdm=rc.parse_nse_list(WDM))
    r, _ = run(row("INE1"), c)
    assert r["terms_verified"] == "no" and r["terms_conflict"].startswith("sources disagree - coupon")
    assert "NSE DEBT.csv=9.17" in r["terms_conflict"] and "CONFLICT" in r["checks"]


def test_bse_only_row_is_verified_only_when_an_official_source_confirms_it():
    name_only = ctx(upx_bse={"INE9": {"trading_symbol": "980X28", "coupon": 9.8, "maturity_date": date(2028, 7, 25)}})
    r, _ = run(row("INE9", "BSE"), name_only)
    assert r["terms_verified"] == "no" and "unverified" in r["terms_source"] and r["symbol"] == "980X28"
    confirmed = ctx(upx_bse={"INE1": {"trading_symbol": "1004IRFC27", "coupon": 10.04, "maturity_date": date(2027, 6, 7)}},
                    wdm=rc.parse_nse_list(WDM))
    r2, _ = run(row("INE1", "BSE"), confirmed)
    assert r2["terms_verified"] == "yes" and "NSE WDM list" in r2["terms_source"] and r2["coupon_freq"] == 2
    # a typo in the BSE name does not block: the official value wins and the status says the name differs
    typo = ctx(upx_bse={"INE1": {"trading_symbol": "T", "coupon": 10.04, "maturity_date": date(2028, 6, 7)}},
               wdm=rc.parse_nse_list(WDM))
    r3, _ = run(row("INE1", "BSE"), typo)
    assert r3["maturity_date"] == "2027-06-07" and r3["terms_verified"] == "yes" and "maturity supporting differs" in r3["checks"]


def test_frequency_disagreement_leaves_it_blank_and_is_reported():
    r, _ = run(row("INE1"), ctx(wdm=rc.parse_nse_list(WDM), corp=rc.parse_nse_list(CORP_TEXT)))  # Half Yearly vs Yearly
    assert r["coupon_freq"] == "" and "frequency:" in r["terms_conflict"]


def test_manual_overrides_win_for_frequency_rating_and_verification():
    c = ctx(wdm=rc.parse_nse_list(WDM))
    r, ev = run(row("INE1"), c, {"INE1": {"coupon_freq": "4", "rating": "AA+", "terms_verified": "yes"}})
    assert r["coupon_freq"] == 4 and r["rating"] == "AA+" and "frequency manual override" in r["checks"]
    assert "+ overrides" in r["terms_source"]


def test_wdm_row_uses_wdm_symbol_and_has_no_trading_symbol():
    r, ev = run(row("INE1", "WDM", ref_close=101.0), ctx(wdm=rc.parse_nse_list(WDM), corp=rc.parse_nse_list(CORP_TEXT)))
    assert r["symbol"] == "IRFC27" and r["trading_symbol"] == "" and "symbol agree(2)" in r["checks"]
    assert r["terms_source"].endswith("price: WDM last trade")


def test_evidence_header_and_columns_are_complete():
    cols = rc.evidence_fieldnames()
    assert cols[:2] == ["isin", "exchange"] and "coupon (final)" in cols and "rating status" in cols
    _, ev = run(row("INE1"), ctx())
    assert set(cols) == set(ev)
