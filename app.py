import os
import time
from typing import List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
COINS = [
    "BTC-USD",
    "ETH-USD",
    "BCH-USD",
    "SOL-USD",
    "LTC-USD",
    "AVAX-USD",
    "LINK-USD",
    "DOT-USD",
    "ADA-USD",
    "DOGE-USD",
]

# tiny in-memory "bot state"
BOT_STATE = {
    "mode": "off",          # "off" | "paper" | "live"
    "last_run": 0.0,
    "paper_balance": 10_000.0,  # fake dollars
    "paper_positions": {},      # "BTC-USD": {"amount": 0.01, "avg": 60000}
}

# ---------------------------------------------------------
# FASTAPI APP
# ---------------------------------------------------------
app = FastAPI(title="Coinbase Bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# ---------------------------------------------------------
# UTIL
# ---------------------------------------------------------
async def fetch_spot(client: httpx.AsyncClient, product_id: str) -> float:
    # try official public prices first
    url = f"https://api.coinbase.com/v2/prices/{product_id}/spot"
    r = await client.get(url, timeout=10.0)
    if r.status_code == 200:
        data = r.json()
        return float(data["data"]["amount"])
    # fallback
    raise HTTPException(status_code=502, detail=f"could not fetch price for {product_id}")


async def fetch_candles(client: httpx.AsyncClient, product_id: str, granularity: int) -> List[List[float]]:
    """
    We try Coinbase Advanced-like/pro-like endpoint.
    If it fails, we return [] and the frontend will draw fallback.
    """
    # old CB Pro style
    url = f"https://api.pro.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    r = await client.get(url, timeout=10.0)
    if r.status_code == 200:
        # format: [ time, low, high, open, close, volume ]
        return r.json()
    # give empty, frontend will mock
    return []


def decide_signal(prices: Dict[str, float]) -> Dict[str, Dict[str, Any]]:
    """
    we do a VERY LOW SENSITIVITY signal.
    idea:
      - we don't know exact % change per coin from history here,
        so we just say HOLD 50% for everything.
    front-end looks nicer with a signal, so we send it.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for p, v in prices.items():
        out[p] = {
            "action": "HOLD",
            "confidence": 50,
        }
    return out


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "mode": BOT_STATE["mode"]}


@app.get("/prices")
async def prices():
    async with httpx.AsyncClient() as client:
        result: Dict[str, float] = {}
        for coin in COINS:
            try:
                price = await fetch_spot(client, coin)
            except Exception:
                price = 0.0
            result[coin] = price

    signals = decide_signal(result)
    return {
        "coins": result,
        "signals": signals,
        "updated": time.strftime("%-I:%M:%S %p"),
        "mode": BOT_STATE["mode"],
    }


@app.get("/history/{symbol}")
async def history(
    symbol: str,
    range: str = Query("1D", regex="^(1D|1W|1M|6M|1Y)$")
):
    # map range -> seconds
    # coinbase max granularity we try to stay small
    gran_map = {
        "1D": 3600,       # 1h candles
        "1W": 10800,      # 3h
        "1M": 21600,      # 6h
        "6M": 86400,      # 1d
        "1Y": 86400,      # 1d
    }
    granularity = gran_map.get(range, 3600)

    async with httpx.AsyncClient() as client:
        candles = await fetch_candles(client, symbol, granularity)

    if not candles:
        # frontend will draw sine
        return {
            "symbol": symbol,
            "range": range,
            "fallback": True,
            "candles": [],
        }

    # normalize: newest last
    candles = sorted(candles, key=lambda x: x[0])

    formatted = [
        {
            "time": c[0],
            "low": c[1],
            "high": c[2],
            "open": c[3],
            "close": c[4],
            "volume": c[5],
        }
        for c in candles
    ]

    return {
        "symbol": symbol,
        "range": range,
        "fallback": False,
        "candles": formatted,
    }


@app.post("/trade-mode")
async def trade_mode(mode: str):
    """
    mode = "off" | "paper" | "live"
    """
    if mode not in ("off", "paper", "live"):
        raise HTTPException(status_code=400, detail="invalid mode")
    BOT_STATE["mode"] = mode
    return {"ok": True, "mode": mode}


@app.get("/bot-state")
async def bot_state():
    return BOT_STATE
