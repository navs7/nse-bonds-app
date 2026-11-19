from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class BondTerms(BaseModel):
    """Layer 1+2: static terms (SEBI/depository data) and the listed universe."""

    isin: str
    name: str = ""
    issuer: str = ""
    coupon_rate: float  # percent per annum
    coupon_freq: int  # payments per year (0 = zero coupon)
    maturity_date: date
    face_value: float  # CURRENT face value (after any partial redemptions)
    rating: str = ""
    terms_source: str = ""
    terms_as_of: Optional[date] = None

    @property
    def instrument_key(self) -> str:
        return f"NSE_EQ|{self.isin}"


class Level(BaseModel):
    price: float
    qty: float


class Quote(BaseModel):
    """Layer 3: live market data, always carrying its own source + timestamps."""

    isin: str
    ltp: Optional[float] = None
    last_trade_time: Optional[datetime] = None
    bids: list[Level] = []
    asks: list[Level] = []
    received_at: datetime
    source: str = "upstox"


class BondView(BaseModel):
    """What the API returns: terms + quote + computed fields + honest status."""

    terms: BondTerms
    quote: Optional[Quote] = None
    status: str  # LIVE | STALE | NO_QUOTE
    age_seconds: Optional[float] = None
    ytm_ltp: Optional[float] = None  # percent
    ytm_best_ask: Optional[float] = None
    ytm_best_bid: Optional[float] = None
