import os
import time
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------- CONFIG ----------------
COINS = [
    "BTC-USD", "ETH-USD", "BCH-USD", "SOL-USD", "LTC-USD",
    "AVAX-USD", "LINK-USD", "DOT-USD", "ADA-USD", "DOGE-USD"
]

BOT_STATE = {"mode": "off"}  # off | paper | live

# ---------------- APP ----------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# ---------------- UTIL ----------------
async def fetch_price(client, coin):
    url = f"https://api.coinbase.com/v2/prices/{coin}/spot"
    r = await client.get(url)
    if r.status_code == 200:
        return float(r.json()["data"]["amount"])
    return 0.0

async def fetch_candles(client, coin, granularity):
    url = f"https://api.pro.coinbase.com/products/{coin}/candles?granularity={granularity}"
    r = await client.get(url)
    if r.status_code == 200:
        return sorted(r.json(), key=lambda x: x[0])
    return []

# ---------------- ROUTES ----------------
@app.get("/prices")
async def prices():
    async with httpx.AsyncClient() as client:
        data = {c: await fetch_price(client, c) for c in COINS}
    return {"coins": data, "updated": time.strftime("%I:%M:%S %p"), "mode": BOT_STATE["mode"]}

@app.get("/history/{coin}")
async def history(coin: str, range: str = Query("1D", regex="^(1D|1W|1M|6M|1Y)$")):
    intervals = {"1D": 3600, "1W": 10800, "1M": 21600, "6M": 86400, "1Y": 86400}
    async with httpx.AsyncClient() as client:
        candles = await fetch_candles(client, coin, intervals[range])
    if not candles:
        return {"symbol": coin, "fallback": True}
    out = [{"time": c[0], "low": c[1], "high": c[2], "open": c[3], "close": c[4]} for c in candles]
    return {"symbol": coin, "fallback": False, "candles": out}

@app.post("/trade-mode")
async def trade_mode(mode: str):
    if mode not in ("off", "paper", "live"):
        raise HTTPException(400, "invalid mode")
    BOT_STATE["mode"] = mode
    return {"ok": True, "mode": mode}
