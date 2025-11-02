import os
import time
import hmac
import json
import base64
import hashlib
from typing import Optional, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# ------------------------------------------------------------
# CONFIG / STATE (in-memory)
# ------------------------------------------------------------
# start in paper mode so we don't blow your real money
STATE = {
    "mode": os.getenv("TRADING_MODE", "paper"),  # "paper" | "live"
    "auto": False,  # auto-trading off by default
    "last_trade_ts": 0.0,
    "min_seconds_between_trades": 45,  # don't spam
}

app = FastAPI(title="Coinbase Bot Backend")

# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

COINBASE_PUBLIC_API = "https://api.coinbase.com/api/v3"
ADVANCED_TRADE_API = "https://api.coinbase.com/api/v3/brokerage"

def coinbase_keys_present() -> bool:
    return bool(os.getenv("COINBASE_API_KEY") and os.getenv("COINBASE_PRIVATE_KEY"))

async def fetch_spot_price(symbol: str) -> Optional[float]:
    # symbol like "BTC-USD"
    url = f"{COINBASE_PUBLIC_API}/brokerage/products/{symbol}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
    if r.status_code != 200:
        return None
    data = r.json()
    price = data.get("price") or data.get("pricebook", {}).get("product_id")
    # real endpoint returns "price" as string
    return float(data["price"]) if "price" in data else None

async def fetch_candles(symbol: str, granularity: str) -> list:
    """
    Return candles as list of {t,o,h,l,c} for the UI.
    We map your UI ranges to Coinbase-ish intervals.
    """
    # map UI -> seconds
    # 1D -> 5m
    # 1W -> 1h
    # 1M -> 6h
    # 6M -> 1d
    # 1Y -> 1d
    gran_map = {
        "1d": 300,
        "1w": 3600,
        "1m": 21600,
        "6m": 86400,
        "1y": 86400,
    }
    gran_sec = gran_map.get(granularity, 300)

    url = f"{COINBASE_PUBLIC_API}/brokerage/products/{symbol}/candles?granularity={gran_sec}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
    if r.status_code != 200:
        # return fake shape so UI still draws
        return [
            {"t": int(time.time()) - 600, "o": 100, "h": 110, "l": 95, "c": 105},
            {"t": int(time.time()), "o": 105, "h": 115, "l": 99, "c": 110},
        ]

    raw = r.json()
    # Coinbase candles come as list of [start, low, high, open, close, volume]
    candles = []
    for row in raw:
        ts, low, high, open_, close, vol = row
        candles.append(
            {
                "t": ts,
                "o": float(open_),
                "h": float(high),
                "l": float(low),
                "c": float(close),
            }
        )
    # sort oldest -> newest
    candles.sort(key=lambda x: x["t"])
    return candles

def decide_action(current_price: float, history: list[float]) -> dict:
    """
    Very dumb but stable: only trade if move > 0.7%.
    history is list of past closes (oldest -> newest).
    """
    if not history:
        return {"action": "hold", "confidence": 50}

    last_price = history[-1]
    move_pct = ((current_price - last_price) / last_price) * 100

    # thresholds
    BUY_THRESH = -0.7   # price dropped 0.7% -> buy
    SELL_THRESH = 0.7   # price jumped 0.7% -> sell

    if move_pct <= BUY_THRESH:
        return {"action": "buy", "confidence": min(90, int(abs(move_pct) * 10))}
    elif move_pct >= SELL_THRESH:
        return {"action": "sell", "confidence": min(90, int(abs(move_pct) * 10))}
    else:
        return {"action": "hold", "confidence": 50}

def fees_ok(expected_profit_pct: float, est_fee_pct: float = 0.6) -> bool:
    """
    est_fee_pct ~0.6% = buy 0.3 + sell 0.3 (depends on your account)
    Only trade when expected_profit_pct > total fees.
    """
    return expected_profit_pct > est_fee_pct

async def place_live_order(symbol: str, side: Literal["BUY", "SELL"], amount_usd: float) -> dict:
    """
    Try to place a real order.
    This is written so that if PyJWT isn't installed, we just return a safe error
    instead of crashing your whole app (Render was doing that).
    """
    if not coinbase_keys_present():
        return {"ok": False, "reason": "live mode not configured on server"}

    # lazy import so Render won't crash on startup
    try:
        import jwt  # PyJWT
    except ImportError:
        return {"ok": False, "reason": "PyJWT not installed on server"}

    api_key = os.getenv("COINBASE_API_KEY")
    private_key = os.getenv("COINBASE_PRIVATE_KEY")

    # Coinbase advanced trade needs a JWT signed request.
    # This is a simplified placeholder — you will likely need to adjust to your account.
    now = int(time.time())
    payload = {
        "sub": api_key,
        "iss": "coinbase-python-bot",
        "nbf": now,
        "iat": now,
        "exp": now + 60,
    }
    token = jwt.encode(payload, private_key, algorithm="ES256")

    body = {
        "product_id": symbol,
        "side": side.lower(),
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(round(amount_usd, 2))
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "CB-ACCESS-KEY": api_key,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{ADVANCED_TRADE_API}/orders", headers=headers, json=body)

    if r.status_code != 200:
        return {"ok": False, "reason": f"coinbase error {r.status_code}", "body": r.text}

    return {"ok": True, "body": r.json()}

async def execute_trade(symbol: str, action: str, amount_usd: float) -> dict:
    """
    One place that actually 'does' the trade depending on STATE.
    """
    if action == "hold":
        return {"ok": True, "mode": STATE["mode"], "skipped": True}

    if STATE["mode"] == "paper":
        # fake fill
        return {
            "ok": True,
            "mode": "paper",
            "filled": True,
            "symbol": symbol,
            "action": action,
            "amount_usd": amount_usd,
        }

    # live mode
    side = "BUY" if action == "buy" else "SELL"
    live_res = await place_live_order(symbol, side, amount_usd)
    return live_res

# ------------------------------------------------------------
# ROUTES
# ------------------------------------------------------------

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Backend OK"

@app.get("/api/settings")
async def get_settings():
    return {
        "mode": STATE["mode"],
        "auto": STATE["auto"],
        "can_live": coinbase_keys_present(),
    }

@app.post("/api/settings")
async def set_settings(req: Request):
    body = await req.json()
    mode = body.get("mode")
    auto = body.get("auto")

    if mode in ("paper", "live"):
        STATE["mode"] = mode

    if isinstance(auto, bool):
        STATE["auto"] = auto

    return {
        "ok": True,
        "mode": STATE["mode"],
        "auto": STATE["auto"],
    }

@app.get("/api/prices")
async def get_prices():
    # your frontend was showing ~10 coins — let's just pull those
    symbols = [
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
    out = {}
    async with httpx.AsyncClient(timeout=8) as client:
        for s in symbols:
            r = await client.get(f"{COINBASE_PUBLIC_API}/brokerage/products/{s}")
            if r.status_code == 200:
                data = r.json()
                out[s] = float(data["price"])
    return out

@app.get("/api/history/{symbol}")
async def get_history(symbol: str, range: str = "1d"):
    candles = await fetch_candles(symbol, range)
    return {"symbol": symbol, "range": range, "candles": candles}

@app.post("/api/signal")
async def post_signal(req: Request):
    """
    Frontend can send:
    {
      "symbol": "BTC-USD",
      "current_price": 10987.12,
      "recent_closes": [10900, 10920, ...]
    }
    And we'll decide buy/sell/hold AND execute if auto=True and fees look ok.
    """
    body = await req.json()
    symbol = body["symbol"]
    current_price = float(body["current_price"])
    recent_closes = body.get("recent_closes") or []

    decision = decide_action(current_price, recent_closes)

    # if auto trading is OFF, just return the decision
    if not STATE["auto"]:
        return {
            "auto": False,
            "decision": decision,
            "mode": STATE["mode"],
        }

    # auto ON -> check fees + cooldown
    now = time.time()
    if now - STATE["last_trade_ts"] < STATE["min_seconds_between_trades"]:
        return {
            "auto": True,
            "skipped": "cooldown",
            "decision": decision,
            "mode": STATE["mode"],
        }

    # estimate profit = |%move|
    if recent_closes:
        last_price = recent_closes[-1]
        expected_move_pct = abs((current_price - last_price) / last_price * 100)
    else:
        expected_move_pct = 0.0

    if decision["action"] != "hold" and fees_ok(expected_move_pct, est_fee_pct=0.6):
        # execute small 5 USD test order
        res = await execute_trade(symbol, decision["action"], amount_usd=5.0)
        STATE["last_trade_ts"] = now
        return {
            "auto": True,
            "executed": True,
            "decision": decision,
            "trade_result": res,
            "mode": STATE["mode"],
        }
    else:
        return {
            "auto": True,
            "executed": False,
            "reason": "not enough edge to beat fees",
            "decision": decision,
            "mode": STATE["mode"],
        }

# ------------------------------------------------------------
# health for your frontend
# ------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "mode": STATE["mode"], "auto": STATE["auto"]}
