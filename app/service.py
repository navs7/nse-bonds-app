"""Joins the layers and decides, honestly, what each bond's data status and signal are."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import yieldcalc
from .models import BondTerms, BondView, Quote


@dataclass(frozen=True)
class Rules:
    """Screening rules, mirroring the old Excel macro (12% YTM, 90+ days, 10,000 profit)."""

    min_ytm: float = 12.0  # percent, at best ask
    min_days: int = 90
    min_profit: float = 10_000.0
    max_sane_ytm: float = 50.0  # above this the price/face value is probably wrong, not a bargain


def classify(ytm: Optional[float], days_left: int, profit: Optional[float], rules: Rules) -> Optional[str]:
    """INVEST / REVIEW / LOW_YTM / SHORT / CHECK, or None when there is no yield to judge."""
    if ytm is None:
        return None
    if ytm > rules.max_sane_ytm:
        return "CHECK"
    if days_left <= rules.min_days:
        return "SHORT"
    if ytm < rules.min_ytm:
        return "LOW_YTM"
    return "INVEST" if (profit or 0) > rules.min_profit else "REVIEW"


def yield_blocked(terms: BondTerms) -> bool:
    """True when the terms are too doubtful to compute a yield from (never guess)."""
    return (
        terms.face_value is None
        or not terms.terms_verified
        or bool(terms.terms_conflict)
        or (terms.paid_up_value is not None and terms.paid_up_value != terms.face_value)
    )


def data_notes(terms: BondTerms) -> list[str]:
    notes = []
    if terms.terms_conflict:
        notes.append(f"TERMS CONFLICT - {terms.terms_conflict}: yield not computed until checked against the term sheet")
    if not terms.terms_verified:
        notes.append("terms parsed from BSE name, unverified: yield not computed "
                     "(confirm in data/overrides.csv: terms_verified=yes, face_value=...)")
    elif terms.face_value is None:
        notes.append("face value unknown: yield not computed")
    if terms.coupon_rate > 0 and not terms.coupon_freq:
        notes.append("coupon frequency unknown: yield not computed")
    if terms.face_value is not None and terms.paid_up_value is not None and terms.paid_up_value != terms.face_value:
        notes.append(f"paid-up {terms.paid_up_value:,.2f} differs from face {terms.face_value:,.2f}: "
                     "verify outstanding value with depository; yield not computed")
    return notes


def build_view(terms: BondTerms, quote: Optional[Quote], now: datetime, stale_after: int,
               rules: Rules = Rules()) -> BondView:
    notes = data_notes(terms)
    today = now.date()
    days_left = (terms.maturity_date - today).days
    if quote is None or (quote.ltp is None and not quote.asks and not quote.bids):
        return BondView(terms=terms, quote=quote, status="NO_QUOTE", days_left=days_left, notes=notes)
    # age = time since last trade (informational); status uses time since we last heard from the feed
    ref = quote.last_trade_time or quote.received_at
    age = (now - ref).total_seconds()
    recv_age = (now - quote.received_at).total_seconds()
    has_depth = bool(quote.asks or quote.bids)
    status = "LIVE" if (recv_age <= stale_after and has_depth) else "STALE"

    best_ask = quote.asks[0] if quote.asks else None
    best_bid = quote.bids[0] if quote.bids else None
    base = dict(terms=terms, quote=quote, status=status, age_seconds=round(age, 1),
                days_left=days_left, notes=notes)
    if yield_blocked(terms):
        return BondView(**base)

    settle = today + timedelta(days=1)
    ytm_ask = yieldcalc.ytm(terms, best_ask.price, settle) if best_ask else None
    gain = yieldcalc.total_gain(terms, best_ask.price, settle) if best_ask else None
    profit = round(gain * best_ask.qty, 2) if (gain is not None and best_ask) else None
    # A signal is only given on a LIVE quote: a stale price must never say "Invest".
    signal = classify(ytm_ask, days_left, profit, rules) if status == "LIVE" else None
    return BondView(
        **base,
        ytm_ltp=yieldcalc.ytm(terms, quote.ltp, settle) if quote.ltp else None,
        ytm_best_ask=ytm_ask,
        ytm_best_bid=yieldcalc.ytm(terms, best_bid.price, settle) if best_bid else None,
        gross_profit=profit,
        signal=signal,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
