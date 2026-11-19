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


# ---- signal rules (12% YTM / 90+ days / 10,000 profit, as in the old Excel macro) ----
from app.service import Rules, classify  # noqa: E402

R = Rules()


def test_classify_rules():
    assert classify(None, 500, 1e6, R) is None
    assert classify(13.0, 500, 20_000, R) == "INVEST"
    assert classify(13.0, 500, 5_000, R) == "REVIEW"
    assert classify(13.0, 500, 10_000, R) == "REVIEW"  # must EXCEED 10,000
    assert classify(11.9, 500, 1e6, R) == "LOW_YTM"
    assert classify(13.0, 90, 1e6, R) == "SHORT"  # needs MORE than 90 days
    assert classify(80.0, 500, 1e6, R) == "CHECK"  # implausible yield = suspect data


def _live_view(price, qty, recv_age=10, **tkw):
    t = terms(maturity_date=date(2029, 1, 1), **tkw)
    q = Quote(isin=t.isin, ltp=price, asks=[Level(price=price, qty=qty)],
              received_at=NOW - timedelta(seconds=recv_age))
    return build_view(t, q, NOW, 300, R)


def test_view_gets_signal_and_profit_only_when_live():
    v = _live_view(800.0, 100)  # 10% coupon bond bought well below par -> high yield
    assert v.status == "LIVE" and v.days_left > 90 and v.ytm_best_ask > 12
    assert v.gross_profit > 10_000 and v.signal == "INVEST"
    stale = _live_view(800.0, 100, recv_age=900)
    assert stale.status == "STALE" and stale.signal is None  # stale price never says Invest


def test_no_signal_without_frequency():
    v = _live_view(800.0, 100, coupon_freq=None)
    assert v.signal is None and v.gross_profit is None
