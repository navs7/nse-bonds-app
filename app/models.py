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
    face_value: Optional[float] = None  # nominal face value per NSE; None = unknown (BSE-only bonds)
    paid_up_value: Optional[float] = None  # NSE paid-up value; differs from face_value => verify
    exchange: str = "NSE"  # one row per exchange: a bond on NSE and BSE has two rows (same ISIN)
    terms_verified: bool = True  # False = coupon/maturity parsed from a BSE name, not an official field
    series: str = ""
    symbol: str = ""  # the exchange's symbol / scrip id
    trading_symbol: str = ""  # Upstox instrument symbol (blank for WDM: not tradable there)
    checks: str = ""  # per-field source agreement, e.g. "coupon agree(3) | maturity agree(2) | ..."
    category: str = ""
    rating: str = ""
    listing_date: Optional[date] = None
    ref_close: Optional[float] = None  # last NSE bhavcopy close (end of day reference, not live)
    ref_last: Optional[float] = None
    ref_volume: Optional[float] = None
    ref_date: Optional[date] = None
    terms_source: str = ""
    terms_as_of: Optional[date] = None
    terms_conflict: str = ""  # non-empty = two sources disagree on this bond's coupon/maturity

    @property
    def streamable(self) -> bool:
        """Upstox streams NSE/BSE capital-market scrips only (not WDM), and unverified BSE bonds get no yield anyway."""
        return self.exchange in ("NSE", "BSE") and (self.exchange == "NSE" or self.terms_verified)

    @property
    def instrument_key(self) -> str:
        return f"{self.exchange}_EQ|{self.isin}"


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
    exchange: str = "NSE"

    @property
    def key(self) -> str:
        return f"{self.exchange}_EQ|{self.isin}"


class BondView(BaseModel):
    """What the API returns: terms + quote + computed fields + honest status."""

    terms: BondTerms
    quote: Optional[Quote] = None
    status: str  # LIVE | STALE | NO_QUOTE
    age_seconds: Optional[float] = None
    ytm_ltp: Optional[float] = None  # percent
    ytm_best_ask: Optional[float] = None
    ytm_best_bid: Optional[float] = None
    ytm_ref: Optional[float] = None  # yield at the last close/trade price, only if that price is recent
    notes: list[str] = []  # why a value is missing, e.g. unknown coupon frequency
    days_left: Optional[int] = None
    gross_profit: Optional[float] = None  # undiscounted gain per unit x quantity at best ask
    signal: Optional[str] = None  # INVEST | REVIEW | LOW_YTM | SHORT | CHECK (LIVE quotes only)
