"""Yield maths. We compute YTM ourselves so it never depends on third-party yield figures.

Convention: annual effective yield, actual/365, dirty price = clean + accrued.
Assumes a bullet bond unless an explicit principal schedule is given.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .models import BondTerms

CashFlow = tuple[date, float]


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    m += 1
    # clamp day to month length
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("bad date")


def coupon_dates(terms: BondTerms, settle: date) -> tuple[Optional[date], list[date]]:
    """(previous coupon date, upcoming coupon dates incl. maturity) generated backwards from maturity."""
    if not terms.coupon_freq:
        return None, [terms.maturity_date]
    step = 12 // terms.coupon_freq
    dates: list[date] = []
    i = 0
    d = terms.maturity_date
    while d > settle:
        dates.append(d)
        i += 1
        d = _add_months(terms.maturity_date, -step * i)
    return d, sorted(dates)


def cashflows(terms: BondTerms, settle: date) -> tuple[list[CashFlow], float]:
    """Future cash flows and accrued interest per unit of current face value."""
    prev, upcoming = coupon_dates(terms, settle)
    coupon = terms.face_value * terms.coupon_rate / 100 / terms.coupon_freq if terms.coupon_freq else 0.0
    flows: list[CashFlow] = [(d, coupon) for d in upcoming]
    last_d, last_c = flows[-1]
    flows[-1] = (last_d, last_c + terms.face_value)
    accrued = 0.0
    if prev is not None and coupon:
        period = (upcoming[0] - prev).days
        accrued = coupon * (settle - prev).days / period
    return flows, accrued


def _pv(flows: list[CashFlow], settle: date, r: float) -> float:
    return sum(cf / (1 + r) ** ((d - settle).days / 365) for d, cf in flows)


def total_gain(terms: BondTerms, clean_price: float, settle: Optional[date] = None) -> Optional[float]:
    """Undiscounted gain per bond if bought at clean_price and held to maturity:
    all remaining coupons + principal - (price + accrued interest)."""
    settle = settle or date.today() + timedelta(days=1)
    if clean_price <= 0 or terms.maturity_date <= settle:
        return None
    if terms.coupon_rate > 0 and not terms.coupon_freq:
        return None
    flows, accrued = cashflows(terms, settle)
    return sum(cf for _, cf in flows) - (clean_price + accrued)


def ytm(terms: BondTerms, clean_price: float, settle: Optional[date] = None) -> Optional[float]:
    """Yield to maturity in percent for a CLEAN price quoted per bond (same unit as face_value)."""
    settle = settle or date.today() + timedelta(days=1)
    if terms.coupon_rate > 0 and not terms.coupon_freq:
        return None  # never guess payment frequency
    if clean_price <= 0 or terms.maturity_date <= settle:
        return None
    flows, accrued = cashflows(terms, settle)
    dirty = clean_price + accrued
    lo, hi = -0.99, 10.0
    if _pv(flows, settle, lo) < dirty or _pv(flows, settle, hi) > dirty:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if _pv(flows, settle, mid) > dirty:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2 * 100, 4)
