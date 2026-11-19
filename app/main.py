from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .models import BondView
from .service import build_view, utcnow
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


@app.get("/api/bonds", response_model=list[BondView])
def bonds():
    quotes, now = _quotes(), utcnow()
    return [
        build_view(t, quotes.get(isin), now, config.QUOTE_STALE_AFTER_SECONDS)
        for isin, t in state["terms"].items()
    ]


@app.get("/api/bonds/{isin}", response_model=BondView)
def bond(isin: str):
    isin = isin.upper()
    t = state["terms"].get(isin)
    if not t:
        raise HTTPException(404, "ISIN not in universe")
    return build_view(t, _quotes().get(isin), utcnow(), config.QUOTE_STALE_AFTER_SECONDS)


app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")
