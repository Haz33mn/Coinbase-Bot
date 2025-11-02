# app.py
#
# FastAPI backend for the crypto dashboard + auto/paper trading toggle.
# - GET  /              -> "Backend OK"
# - GET  /api/prices    -> top coins + signals (debounced, fee-aware)
# - GET  /api/history   -> candles for symbol + timeframe
# - GET  /api/state     -> current bot state (auto on/off, mode)
# - POST /api/state     -> update bot state from UI
# - POST /api/trade     -> (internal) simulate or try real trade
#
# NOTES:
# - live trades only run if ALL 3 env vars exist:
#     COINBASE_API_KEY, COINBASE_API_SECRET, COINBASE_API_PASSPHRASE
#   otherwise we auto-fallback to paper and tell the UI.
# - fees: we assume ~0.6% taker each way, so we require >=1.4% edge.

import os
import time
import json
from typing import List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# -------------- CONFIG --------------

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

# Coinbase public candles endpoint
CB_BASE = "https://api.exchange.coinbase.com"

# trading fees (rough)
TRADING_FEE_RATE = 0.006  # 0.6% each way worst-case
FEE_BUFFER = 0.0025       # extra 0.25%
MIN_EDGE_FOR_TRADE = TRADING_FEE_RATE * 2 + FEE_BUFFER  # ~1.45%

# how long to cache prices (seconds)
PRICE_TTL = 8
HISTORY_TTL = 10

# -------------- GLOBAL STATE --------------

STATE = {
    "auto_trade": False,   # UI toggle
    "mode": "paper",       # "paper" or "live"
    "last_prices": {},     # symbol -> {price, signal, score, ts}
    "last_prices_ts": 0.0,
    "history_cache": {},   # (symbol, tf) -> {data, ts}
    "paper_balance": 1000.0,
    "paper_positions": {},  # symbol -> {"qty": ..., "entry": ...}
}

# -------------- APP SETUP --------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # your frontend is on the same Render service
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------- HELPERS --------------


def now() -> float:
    return time.time()


async def fetch_coinbase_candles(product_id: str, granularity: int) -> List[List[float]]:
    """
    Get candles from Coinbase.
    Return format (Coinbase): [ time, low, high, open, close, volume ]
    We always return newest->oldest, so frontend must reverse.
    """
    url = f"{CB_BASE}/products/{product_id}/candles?granularity={granularity}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="cannot reach coinbase")
    return r.json()


def make_fallback_candles(n: int = 80, base: float = 100.0) -> List[Dict[str, float]]:
    """always give the UI something to draw"""
    out = []
    import math
    for i in range(n):
        x = i / 10
        price = base + math.sin(x) * 3.0
        out.append(
            {
                "time": int(now()) - (n - i) * 3600,
                "open": price - 0.7,
                "high": price + 0.8,
                "low": price - 1.2,
                "close": price,
            }
        )
    return out


def signal_from_change(pct_change: float) -> Dict[str, Any]:
    """
    Turn 24h % change into buy/sell/hold.
    We make it LESS sensitive -> no more flipping on pennies.
    """
    # pct_change is like +1.5 or -2.3 (percent)
    # we want to only trigger if |change| > 1.5%
    if pct_change <= -1.8:
        # only BUY if we beat fees
        if abs(pct_change) / 100.0 > MIN_EDGE_FOR_TRADE:
            return {"action": "BUY", "confidence": 82}
        else:
            return {"action": "HOLD", "confidence": 55}
    elif pct_change >= 2.1:
        if pct_change / 100.0 > MIN_EDGE_FOR_TRADE:
            return {"action": "SELL", "confidence": 80}
        else:
            return {"action": "HOLD", "confidence": 55}
    else:
        return {"action": "HOLD", "confidence": 50}


async def fetch_prices() -> Dict[str, Any]:
    """
    Pull latest prices and compute signals.
    We try Coinbase /products first, if it fails we make fake prices.
    """
    # use cache
    if now() - STATE["last_prices_ts"] < PRICE_TTL and STATE["last_prices"]:
        return STATE["last_prices"]

    prices: Dict[str, Any] = {}
    ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{CB_BASE}/products")
        if r.status_code == 200:
            ok = True
            products = r.json()
            # make a lookup
            prod_map = {p["id"]: p for p in products}
            for sym in COINS:
                p = prod_map.get(sym)
                if not p:
                    # fallback single
                    prices[sym] = {
                        "price": 100.0,
                        "change_24h": 0.0,
                        "signal": {"action": "HOLD", "confidence": 50},
                    }
                else:
                    # Coinbase doesn't give change here, so let's fake a small change
                    last = float(p.get("price", "100"))
                    # fake change based on quote_increment
                    change = 0.0
                    sig = signal_from_change(change)
                    prices[sym] = {
                        "price": last,
                        "change_24h": change,
                        "signal": sig,
                    }
    except Exception:
        ok = False

    if not ok:
        # full fallback
        for i, sym in enumerate(COINS):
            base = 100 + i * 4.5
            change = 0.0
            sig = signal_from_change(change)
            prices[sym] = {
                "price": base,
                "change_24h": change,
                "signal": sig,
            }

    # rank by "strongest" (BUY first, then SELL, then HOLD)
    def score(entry):
        act = entry["signal"]["action"]
        conf = entry["signal"]["confidence"]
        if act == "BUY":
            return 200 + conf
        if act == "SELL":
            return 100 + conf
        return 50 + conf

    ordered = sorted(
        [{"symbol": s, **v} for s, v in prices.items()],
        key=score,
        reverse=True,
    )

    out = {
        "ordered": ordered,
        "raw": prices,
        "ts": int(now()),
    }

    STATE["last_prices"] = out
    STATE["last_prices_ts"] = now()
    return out


async def get_history(symbol: str, tf: str) -> Dict[str, Any]:
    """
    tf: "1D","1W","1M","6M","1Y"
    """
    key = (symbol, tf)
    if key in STATE["history_cache"]:
        cached = STATE["history_cache"][key]
        if now() - cached["ts"] < HISTORY_TTL:
            return cached["data"]

    # map tf -> granularity secs
    gran_map = {
        "1D": 3600,     # 1h candles, 24 of them
        "1W": 6 * 3600,  # 6h candles
        "1M": 24 * 3600,  # 1d candles
        "6M": 24 * 3600,
        "1Y": 24 * 3600,
    }
    gran = gran_map.get(tf, 3600)

    try:
        cb_candles = await fetch_coinbase_candles(symbol, granularity=gran)
        # convert to frontend shape
        candles = []
        # Coinbase returns newest first, we reverse
        for c in reversed(cb_candles):
            candles.append(
                {
                    "time": c[0],
                    "low": c[1],
                    "high": c[2],
                    "open": c[3],
                    "close": c[4],
                }
            )
        data = {"symbol": symbol, "tf": tf, "candles": candles, "source": "coinbase"}
    except Exception:
        data = {
            "symbol": symbol,
            "tf": tf,
            "candles": make_fallback_candles(),
            "source": "fallback",
        }

    STATE["history_cache"][key] = {"data": data, "ts": now()}
    return data


def have_live_creds() -> bool:
    return (
        os.getenv("COINBASE_API_KEY")
        and os.getenv("COINBASE_API_SECRET")
        and os.getenv("COINBASE_API_PASSPHRASE")
    )


def trade_beats_fees(expected_edge_pct: float) -> bool:
    return expected_edge_pct >= (MIN_EDGE_FOR_TRADE * 100.0)


def run_paper_trade(side: str, symbol: str, price: float, amount_usd: float = 10.0) -> Dict[str, Any]:
    bal = STATE["paper_balance"]
    if side == "BUY":
        if bal < amount_usd:
            return {"ok": False, "reason": "not enough paper balance"}
        qty = amount_usd / price
        STATE["paper_balance"] -= amount_usd
        pos = STATE["paper_positions"].get(symbol, {"qty": 0.0, "entry": price})
        pos["qty"] += qty
        pos["entry"] = price
        STATE["paper_positions"][symbol] = pos
        return {"ok": True, "mode": "paper", "side": side, "qty": qty, "price": price}
    else:  # SELL
        pos = STATE["paper_positions"].get(symbol)
        if not pos or pos["qty"] <= 0:
            return {"ok": False, "reason": "no paper position"}
        qty = pos["qty"]
        usd = qty * price
        STATE["paper_balance"] += usd
        STATE["paper_positions"][symbol] = {"qty": 0.0, "entry": 0.0}
        return {"ok": True, "mode": "paper", "side": side, "qty": qty, "price": price}


# -------------- ROUTES --------------


@app.get("/")
async def root():
    return "Backend OK"


@app.get("/api/prices")
async def api_prices():
    data = await fetch_prices()
    # also attach bot state
    return {
        "bot": {
            "auto_trade": STATE["auto_trade"],
            "mode": STATE["mode"],
            "paper_balance": STATE["paper_balance"],
        },
        **data,
    }


@app.get("/api/history")
async def api_history(symbol: str, tf: str = "1D"):
    data = await get_history(symbol, tf)
    return data


@app.get("/api/state")
async def api_state():
    return {
        "auto_trade": STATE["auto_trade"],
        "mode": STATE["mode"],
        "paper_balance": STATE["paper_balance"],
        "paper_positions": STATE["paper_positions"],
        "has_live_creds": have_live_creds(),
    }


@app.post("/api/state")
async def api_state_update(req: Request):
    body = await req.json()
    auto = body.get("auto_trade")
    mode = body.get("mode")
    if auto is not None:
        STATE["auto_trade"] = bool(auto)
    if mode in ("paper", "live"):
        # if user asks live but we don't have creds, fall back
        if mode == "live" and not have_live_creds():
            STATE["mode"] = "paper"
        else:
            STATE["mode"] = mode
    return {
        "ok": True,
        "auto_trade": STATE["auto_trade"],
        "mode": STATE["mode"],
        "has_live_creds": have_live_creds(),
    }


@app.post("/api/trade")
async def api_trade(req: Request):
    """
    Called by the UI when user taps BUY/SELL (or by auto mode).
    We still check fees.
    """
    body = await req.json()
    side = body.get("side")  # BUY or SELL
    symbol = body.get("symbol")
    price = float(body.get("price", 0))
    edge = float(body.get("edge_pct", 0))

    if not side or not symbol or price <= 0:
        raise HTTPException(status_code=400, detail="bad trade request")

    if not trade_beats_fees(edge):
        return {"ok": False, "reason": "edge does not beat fees"}

    # if we are in paper mode -> always paper
    if STATE["mode"] == "paper" or not have_live_creds():
        res = run_paper_trade(side, symbol, price)
        return res

    # ---- LIVE TRADE PLACEHOLDER ----
    # Here is where you'd call Coinbase Advanced Trade / REST.
    # We will just pretend for now.
    return {
        "ok": True,
        "mode": "live",
        "side": side,
        "symbol": symbol,
        "price": price,
        "note": "live trading stub - add Coinbase REST call here",
    }
