import os
import time
from typing import Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# coins we show in the UI
COINS = [
    "BTC-USD", "ETH-USD", "BCH-USD", "SOL-USD", "LTC-USD",
    "AVAX-USD", "LINK-USD", "DOT-USD", "ADA-USD", "DOGE-USD"
]

# bot mode: off = do nothing, paper = pretend, live = (would) trade
BOT_STATE: Dict[str, Any] = {"mode": "off"}

app = FastAPI()

# allow frontend to call backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve index.html from the SAME folder (no /static needed)
app.mount("/", StaticFiles(directory=".", html=True), name="static")


async def _fetch_spot(client: httpx.AsyncClient, pair: str) -> float:
    """get spot price from Coinbase"""
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    resp = await client.get(url, timeout=10)
    if resp.status_code == 200:
        return float(resp.json()["data"]["amount"])
    return 0.0


async def _fetch_candles(client: httpx.AsyncClient, pair: str, granularity: int):
    """
    get ohlc from public CB Pro-style endpoint
    returns sorted oldest → newest
    """
    url = f"https://api.pro.coinbase.com/products/{pair}/candles?granularity={granularity}"
    resp = await client.get(url, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        # API returns newest → oldest, we flip
        data.sort(key=lambda x: x[0])
        return data
    return []


@app.get("/prices")
async def prices():
    """return latest prices for the 10 coins"""
    async with httpx.AsyncClient() as client:
        out: Dict[str, float] = {}
        for c in COINS:
            out[c] = await _fetch_spot(client, c)
    return {
        "coins": out,
        "updated": time.strftime("%I:%M:%S %p"),
        "mode": BOT_STATE["mode"],
    }


@app.get("/history/{pair}")
async def history(
    pair: str,
    range: str = Query("1D", pattern="^(1D|1W|1M|6M|1Y)$"),
):
    """
    return OHLC for chart
    1D  => 1h candles
    1W  => 3h candles
    1M  => 6h candles
    6M  => 1d candles
    1Y  => 1d candles
    """
    if pair not in COINS:
        raise HTTPException(404, "unknown pair")

    ranges = {
        "1D": 3600,
        "1W": 10800,
        "1M": 21600,
        "6M": 86400,
        "1Y": 86400,
    }
    gran = ranges[range]

    async with httpx.AsyncClient() as client:
        candles = await _fetch_candles(client, pair, gran)

    if not candles:
        # UI will draw a fake curve
        return {"symbol": pair, "fallback": True}

    ohlc = []
    for ts, low, high, open_, close, vol in candles:
        ohlc.append(
            {
                "time": ts,
                "low": low,
                "high": high,
                "open": open_,
                "close": close,
            }
        )

    return {"symbol": pair, "fallback": False, "candles": ohlc}


@app.post("/trade-mode")
async def trade_mode(mode: str):
    """
    frontend calls:
      POST /trade-mode?mode=off
      POST /trade-mode?mode=paper
      POST /trade-mode?mode=live
    """
    if mode not in ("off", "paper", "live"):
        raise HTTPException(400, "invalid mode")
    BOT_STATE["mode"] = mode
    return {"ok": True, "mode": mode}
