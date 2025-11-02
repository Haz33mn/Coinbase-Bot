import os
import time
from typing import Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# coins shown in UI
COINS = [
    "BTC-USD", "ETH-USD", "BCH-USD", "SOL-USD", "LTC-USD",
    "AVAX-USD", "LINK-USD", "DOT-USD", "ADA-USD", "DOGE-USD"
]

# bot state
BOT_STATE: Dict[str, Any] = {"mode": "off"}

app = FastAPI()

# allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve /static if we ever need it
app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/")
def root():
    # serve the frontend
    return FileResponse("index.html")


async def _fetch_spot(client: httpx.AsyncClient, pair: str) -> float:
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    resp = await client.get(url, timeout=10)
    if resp.status_code == 200:
        return float(resp.json()["data"]["amount"])
    return 0.0


async def _fetch_candles(client: httpx.AsyncClient, pair: str, granularity: int):
    # coinbase pro-style public endpoint
    url = f"https://api.pro.coinbase.com/products/{pair}/candles?granularity={granularity}"
    resp = await client.get(url, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        # newest → oldest; flip
        data.sort(key=lambda x: x[0])
        return data
    return []


@app.get("/prices")
async def prices():
    async with httpx.AsyncClient() as client:
        out = {}
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
    if pair not in COINS:
        raise HTTPException(404, "unknown pair")

    # map UI range → granularity (seconds)
    gran_map = {
        "1D": 3600,     # 1h
        "1W": 10800,    # 3h
        "1M": 21600,    # 6h
        "6M": 86400,    # 1d
        "1Y": 86400,    # 1d
    }
    gran = gran_map[range]

    async with httpx.AsyncClient() as client:
        candles = await _fetch_candles(client, pair, gran)

    if not candles:
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
    if mode not in ("off", "paper", "live"):
        raise HTTPException(400, "invalid mode")
    BOT_STATE["mode"] = mode
    return {"ok": True, "mode": mode}
