"""Layer 3: live quotes from the Upstox market-data WebSocket.

Upstox is a broker feed, not the exchange's licensed data product, and bonds appear as NSE_EQ with
no bond metadata. So every quote is stored with timestamps and the API labels it LIVE/STALE.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import Level, Quote

log = logging.getLogger(__name__)


def _ms_to_dt(ms: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_feed(instrument_key: str, feed: dict, now: datetime) -> Quote:
    """Convert one Upstox 'full' feed entry into a Quote. Tolerates missing fields/levels."""
    mff = feed.get("ff", {}).get("marketFF", {})
    ltpc = mff.get("ltpc", {})
    bids: list[Level] = []
    asks: list[Level] = []
    for lvl in mff.get("marketLevel", {}).get("bidAskQuote", []):
        if lvl.get("bp") and lvl.get("bq"):
            bids.append(Level(price=float(lvl["bp"]), qty=float(lvl["bq"])))
        if lvl.get("ap") and lvl.get("aq"):
            asks.append(Level(price=float(lvl["ap"]), qty=float(lvl["aq"])))
    return Quote(
        isin=instrument_key.split("|", 1)[-1],
        ltp=ltpc.get("ltp"),
        last_trade_time=_ms_to_dt(ltpc.get("ltt")),
        bids=bids,
        asks=asks,
        received_at=now,
    )


class UpstoxLive:
    def __init__(self, access_token: str, instrument_keys: list[str]):
        self._token = access_token
        self._keys = instrument_keys
        self._quotes: dict[str, Quote] = {}
        self._lock = threading.Lock()
        self._streamer = None

    def snapshot(self) -> dict[str, Quote]:
        with self._lock:
            return dict(self._quotes)

    def _on_message(self, message: dict) -> None:
        now = datetime.now(timezone.utc)
        for key, feed in message.get("feeds", {}).items():
            try:
                q = parse_feed(key, feed, now)
            except Exception:  # one bad tick must not kill the stream
                log.exception("bad feed for %s", key)
                continue
            with self._lock:
                self._quotes[q.isin] = q

    def start(self) -> None:
        import upstox_client

        cfg = upstox_client.Configuration()
        cfg.access_token = self._token
        self._streamer = upstox_client.MarketDataStreamer(
            upstox_client.ApiClient(cfg), self._keys, "full"
        )
        self._streamer.on("message", self._on_message)
        self._streamer.on("error", lambda e: log.error("stream error: %s", e))
        self._streamer.auto_reconnect(True, 5, 20)
        self._streamer.connect()  # runs in its own thread inside the SDK

    def stop(self) -> None:
        if self._streamer:
            self._streamer.disconnect()
