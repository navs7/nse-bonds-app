from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .models import BondView
from .service import Rules, build_view, utcnow
from .sources.terms_csv import load_terms
from .sources.upstox_live import UpstoxLive

log = logging.getLogger("bonds")
state: dict = {"terms": {}, "live": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["terms"] = load_terms(config.BONDS_CSV)
    log.info("loaded %d bonds from %s", len(state["terms"]), config.BONDS_CSV)
    if config.UPSTOX_ACCESS_TOKEN and state["terms"]:
        live = UpstoxLive(config.UPSTOX_ACCESS_TOKEN, [t.instrument_key for t in state["terms"].values()])
        live.start()
        state["live"] = live
    else:
        log.warning("live quotes disabled: need UPSTOX_ACCESS_TOKEN and a bonds CSV")
    yield
    if state["live"]:
        state["live"].stop()


app = FastAPI(title="NSE Bonds", lifespan=lifespan)


def _quotes() -> dict:
    return state["live"].snapshot() if state["live"] else {}


@app.get("/api/health")
def health():
    return {"bonds": len(state["terms"]), "live_enabled": state["live"] is not None}


RULES = Rules(min_ytm=config.MIN_YTM, min_days=config.MIN_DAYS_LEFT, min_profit=config.MIN_GROSS_PROFIT)


@app.get("/api/bonds", response_model=list[BondView])
def bonds(
    signal: Optional[str] = Query(None, description="comma list, e.g. INVEST,REVIEW"),
    min_ytm: Optional[float] = Query(None, description="only bonds with YTM at best ask >= this (percent)"),
    min_days: Optional[int] = Query(None, description="only bonds with more than this many days left"),
    sort: Optional[str] = Query(None, description="'ytm' = highest YTM at best ask first"),
):
    quotes, now = _quotes(), utcnow()
    views = [
        build_view(t, quotes.get(isin), now, config.QUOTE_STALE_AFTER_SECONDS, RULES)
        for isin, t in state["terms"].items()
    ]
    if signal:
        wanted = {s.strip().upper() for s in signal.split(",")}
        views = [v for v in views if v.signal in wanted]
    if min_ytm is not None:
        views = [v for v in views if v.ytm_best_ask is not None and v.ytm_best_ask >= min_ytm]
    if min_days is not None:
        views = [v for v in views if v.days_left is not None and v.days_left > min_days]
    if sort == "ytm":
        views.sort(key=lambda v: (v.ytm_best_ask is None, -(v.ytm_best_ask or 0)))
    return views


@app.get("/api/bonds/{isin}", response_model=BondView)
def bond(isin: str):
    isin = isin.upper()
    t = state["terms"].get(isin)
    if not t:
        raise HTTPException(404, "ISIN not in universe")
    return build_view(t, _quotes().get(isin), utcnow(), config.QUOTE_STALE_AFTER_SECONDS, RULES)


app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")
