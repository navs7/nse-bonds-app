from datetime import date, datetime, timedelta, timezone

from app import yieldcalc
from app.models import BondTerms, Level, Quote
from app.service import build_view
from app.sources.upstox_live import parse_feed

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def terms(**kw):
    base = dict(isin="INE000000000", coupon_rate=10.0, coupon_freq=1,
                maturity_date=date(2029, 1, 1), face_value=1000.0)
    base.update(kw)
    return BondTerms(**base)


def test_par_bond_yields_its_coupon():
    # settle on a coupon date, priced at par -> YTM == coupon (annual pay)
    y = yieldcalc.ytm(terms(), 1000.0, settle=date(2026, 1, 1))
    assert abs(y - 10.0) < 0.05


def test_discount_bond_yields_more_than_coupon():
    assert yieldcalc.ytm(terms(), 950.0, settle=date(2026, 1, 1)) > 10.0


def test_matured_or_bad_price_returns_none():
    assert yieldcalc.ytm(terms(maturity_date=date(2025, 1, 1)), 1000, settle=date(2026, 1, 1)) is None
    assert yieldcalc.ytm(terms(), 0, settle=date(2026, 1, 1)) is None


def test_parse_feed_handles_missing_levels():
    feed = {"ff": {"marketFF": {"ltpc": {"ltp": 1020.0, "ltt": "1729239921221"},
            "marketLevel": {"bidAskQuote": [{"bq": 16, "bp": 1001.0, "aq": 150, "ap": 1020.0},
                                            {"bq": 10, "bp": 1013.0}, {}]}}}}
    q = parse_feed("NSE_EQ|INE342T07403", feed, NOW)
    assert q.isin == "INE342T07403" and q.ltp == 1020.0
    assert len(q.bids) == 2 and len(q.asks) == 1


def test_status_no_quote_live_and_stale():
    t = terms()
    assert build_view(t, None, NOW, 300).status == "NO_QUOTE"
    fresh = Quote(isin=t.isin, ltp=1000, asks=[Level(price=1001, qty=1)], received_at=NOW - timedelta(seconds=10))
    old = fresh.model_copy(update={"received_at": NOW - timedelta(seconds=900)})
    assert build_view(t, fresh, NOW, 300).status == "LIVE"
    assert build_view(t, old, NOW, 300).status == "STALE"
    nodepth = Quote(isin=t.isin, ltp=1000, received_at=NOW)
    assert build_view(t, nodepth, NOW, 300).status == "STALE"
