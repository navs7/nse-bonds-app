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
    coupon_freq: Optional[int] = None  # payments per year; None = unknown (NSE files don't give it)
    maturity_date: date
    face_value: float  # nominal face value per NSE; override in data/overrides.csv if outstanding differs
    paid_up_value: Optional[float] = None  # NSE paid-up value; differs from face_value => verify
    series: str = ""
    category: str = ""
    rating: str = ""
    listing_date: Optional[date] = None
    ref_close: Optional[float] = None  # last NSE bhavcopy close (end of day reference, not live)
    ref_last: Optional[float] = None
    ref_volume: Optional[float] = None
    ref_date: Optional[date] = None
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
    notes: list[str] = []  # why a value is missing, e.g. unknown coupon frequency
