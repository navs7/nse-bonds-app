"""Joins the layers and decides, honestly, what each bond's data status is."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import yieldcalc
from .models import BondTerms, BondView, Quote


def build_view(terms: BondTerms, quote: Optional[Quote], now: datetime, stale_after: int) -> BondView:
    if quote is None or (quote.ltp is None and not quote.asks and not quote.bids):
        return BondView(terms=terms, quote=quote, status="NO_QUOTE")
    # age = time since last trade (informational); status uses time since we last heard from the feed
    ref = quote.last_trade_time or quote.received_at
    age = (now - ref).total_seconds()
    recv_age = (now - quote.received_at).total_seconds()
    has_depth = bool(quote.asks or quote.bids)
    status = "LIVE" if (recv_age <= stale_after and has_depth) else "STALE"

    best_ask = quote.asks[0].price if quote.asks else None
    best_bid = quote.bids[0].price if quote.bids else None
    return BondView(
        terms=terms,
        quote=quote,
        status=status,
        age_seconds=round(age, 1),
        ytm_ltp=yieldcalc.ytm(terms, quote.ltp) if quote.ltp else None,
        ytm_best_ask=yieldcalc.ytm(terms, best_ask) if best_ask else None,
        ytm_best_bid=yieldcalc.ytm(terms, best_bid) if best_bid else None,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
