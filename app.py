import os
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import jwt        # PyJWT
import httpx
from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, HTMLResponse

# =========================================================
# ENV / CONFIG
# =========================================================
# these 2 are from your downloaded Coinbase API key
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET")

# public (no auth) endpoints we use for prices + history
CB_PUBLIC_PRICE = "https://api.coinbase.com/v2/prices/{pair}/spot"
CB_PUBLIC_CANDLES = "https://api.exchange.coinbase.com/products/{pair}/candles"

# authenticated (needs JWT) endpoint we use for balances + orders
CB_BROKERAGE_BASE = "https://api.coinbase.com/api/v3/brokerage"

# fee + trading controls
# single-side fee, e.g. 0.006 = 0.6% per trade
FEE_RATE = float(os.getenv("TRADING_FEE_RATE", "0.006"))
# we want to earn a bit more than just fees
FEE_BUFFER_PCT = float(os.getenv("TRADING_BUFFER_PCT", "0.5"))  # +0.5%
# minimum quote we use for auto trades
MIN_QUOTE = float(os.getenv("TRADING_MIN_QUOTE", "5"))
# cooldown between actual trades
COOLDOWN_MINUTES = int(os.getenv("TRADING_COOLDOWN_MINUTES", "15"))

# coins to show in UI
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

HAS_CREDS = bool(COINBASE_API_KEY and COINBASE_API_SECRET)

# auto-trader state
AUTO_TRADER_ON: bool = False
LAST_TRADE_AT: Optional[datetime] = None

app = FastAPI()


# =========================================================
# JWT for Coinbase brokerage (trading)
# =========================================================
def make_cb_jwt() -> str:
    if not HAS_CREDS:
        raise RuntimeError("Coinbase API credentials missing")
    now = int(time.time())
    payload = {
        "iss": COINBASE_API_KEY,
        "sub": COINBASE_API_KEY,
        "aud": "retail_rest_api",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }
    token = jwt.encode(
        payload,
        COINBASE_API_SECRET,
        algorithm="ES256",
        headers={"kid": COINBASE_API_KEY},
    )
    return token


async def cb_auth_get(path: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """GET to brokerage (auth)"""
    if not HAS_CREDS:
        return {"error": "no_creds"}
    url = f"{CB_BROKERAGE_BASE}{path}"
    token = make_cb_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=headers, params=params)
    if r.status_code >= 400:
        return {"error": r.text, "status_code": r.status_code}
    return r.json()


async def cb_auth_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST to brokerage (auth)"""
    if not HAS_CREDS:
        return {"error": "no_creds"}
    url = f"{CB_BROKERAGE_BASE}{path}"
    token = make_cb_jwt()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, headers=headers, json=body)
    if r.status_code >= 400:
      return {"error": r.text, "status_code": r.status_code}
    return r.json()


# =========================================================
# helpers
# =========================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_base_from_pair(pair: str) -> str:
    # "BTC-USD" -> "BTC"
    if "-" in pair:
        return pair.split("-")[0]
    return pair


def parse_quote_from_pair(pair: str) -> str:
    # "BTC-USD" -> "USD"
    if "-" in pair:
        return pair.split("-")[1]
    return "USD"


async def get_account_balance(currency: str) -> float:
    """
    Look up available balance for a currency (USD, BTC, ETH, ...)
    via brokerage /accounts
    """
    if not HAS_CREDS:
        return 0.0
    res = await cb_auth_get("/accounts")
    if "error" in res:
        return 0.0
    for acc in res.get("accounts", []):
        if acc.get("currency") == currency:
            # some responses use 'available_balance': {'value': ..., 'currency': ...}
            ab = acc.get("available_balance") or {}
            val = ab.get("value") or "0"
            try:
                return float(val)
            except ValueError:
                return 0.0
    return 0.0


# =========================================================
# FRONTEND
# =========================================================
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("index.html")


# =========================================================
# PRICES (public, no auth)
# =========================================================
@app.get("/api/prices")
async def api_prices():
    coins_out = []
    async with httpx.AsyncClient(timeout=6.0) as client:
        for pair in COINS:
            url = CB_PUBLIC_PRICE.format(pair=pair)
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    amount = float(data["data"]["amount"])
                else:
                    amount = 0.0
            except Exception:
                amount = 0.0
            coins_out.append(
                {
                    "symbol": pair,
                    "price": amount,
                    "signal": "HOLD",
                    "confidence": 50,
                }
            )
    # order by price, take top 10
    coins_out.sort(key=lambda x: x["price"], reverse=True)
    coins_out = coins_out[:10]
    return {"coins": coins_out, "fetched_at": now_iso(), "live": True}


# =========================================================
# HISTORY (public, real candles) with 1Y fix (300 days)
# =========================================================
@app.get("/api/history")
async def api_history(product_id: str, range_key: str = "1D"):
    """
    Use exchange public endpoint:
      /products/<product_id>/candles?start=...&end=...&granularity=...
    We must give start/end for long ranges, and for 1Y we cap to 300 days
    (Coinbase max ~300 points).
    """
    now = now_utc()

    range_key = range_key.upper()
    if range_key == "1D":
        gran = 900  # 15m
        start = now - timedelta(days=1)
    elif range_key == "1W":
        gran = 3600  # 1h
        start = now - timedelta(days=7)
    elif range_key == "1M":
        gran = 21600  # 6h
        start = now - timedelta(days=30)
    elif range_key == "6M":
        gran = 86400  # 1d
        start = now - timedelta(days=180)
    else:  # 1Y
        gran = 86400  # 1d
        start = now - timedelta(days=300)  # cap to 300d to avoid 400

    params = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "granularity": gran,
    }

    url = CB_PUBLIC_CANDLES.format(pair=product_id)
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(url, params=params)

    if r.status_code != 200:
        # fallback simple wave
        pts = []
        base = 100.0
        for i in range(60):
            pts.append(
                {
                    "t": (now - timedelta(minutes=10 * (60 - i))).isoformat(),
                    "o": base,
                    "h": base * 1.002,
                    "l": base * 0.998,
                    "c": base * (1 + 0.01 * (i / 60)),
                }
            )
        return {"ok": False, "fallback": True, "points": pts}

    # exchange candles: [ time, low, high, open, close, volume ]
    candles_raw = r.json()
    candles_raw.reverse()  # oldest -> newest

    points = []
    for c in candles_raw:
        ts, low, high, open_, close, vol = c
        points.append(
            {
                "t": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "o": open_,
                "h": high,
                "l": low,
                "c": close,
            }
        )

    return {"ok": True, "fallback": False, "points": points}


# =========================================================
# ACCOUNTS (auth)
# =========================================================
@app.get("/api/accounts")
async def api_accounts():
    if not HAS_CREDS:
        return {"ok": False, "error": "missing_coinbase_creds"}
    r = await cb_auth_get("/accounts")
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "accounts": r.get("accounts", [])}


# =========================================================
# TRADE (auth)
# =========================================================
@app.post("/api/trade")
async def api_trade(payload: Dict[str, Any] = Body(...)):
    """
    payload:
      {
        "product_id": "BTC-USD",
        "side": "BUY" | "SELL",
        "quote_size": "10"   # buy/sell with $10
        or
        "base_size": "0.001" # sell with 0.001 BTC
      }
    """
    if not HAS_CREDS:
        return {"ok": False, "error": "missing_coinbase_creds"}

    product_id = payload.get("product_id")
    side = (payload.get("side") or "").upper()
    quote_size = payload.get("quote_size")
    base_size = payload.get("base_size")

    if not product_id or side not in ("BUY", "SELL"):
        return {"ok": False, "error": "bad_request"}

    if not quote_size and not base_size:
        return {"ok": False, "error": "need_quote_or_base"}

    if quote_size:
        order_conf = {"market_market_ioc": {"quote_size": str(quote_size)}}
    else:
        order_conf = {"market_market_ioc": {"base_size": str(base_size)}}

    body = {
        "product_id": product_id,
        "side": side,
        "order_configuration": order_conf,
    }

    r = await cb_auth_post("/orders", body)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "result": r}


# =========================================================
# AUTO TRADER (fee-aware)
# =========================================================
@app.post("/api/auto/on")
async def api_auto_on():
    global AUTO_TRADER_ON
    AUTO_TRADER_ON = True
    return {"ok": True, "auto": True}


@app.post("/api/auto/off")
async def api_auto_off():
    global AUTO_TRADER_ON
    AUTO_TRADER_ON = False
    return {"ok": True, "auto": False}


@app.get("/api/auto/status")
async def api_auto_status(product_id: str = "BTC-USD"):
    """
    Fee-aware logic:
      - round-trip fee = FEE_RATE (buy) + FEE_RATE (sell)
      - we require move >= round-trip fee + buffer
      - before BUY: check quote (USD) balance >= MIN_QUOTE * (1 + FEE_RATE)
      - before SELL: check base balance >= (MIN_QUOTE / price) * 1.02
    """
    global LAST_TRADE_AT

    # 1) get recent prices
    hist = await api_history(product_id=product_id, range_key="1D")
    if not hist.get("ok"):
        return {
            "ok": False,
            "auto_on": AUTO_TRADER_ON,
            "action": "HOLD",
            "reason": "no_history",
        }

    points = hist.get("points", [])
    closes = [float(p["c"]) for p in points if p.get("c") is not None]
    if not closes:
        return {
            "ok": False,
            "auto_on": AUTO_TRADER_ON,
            "action": "HOLD",
            "reason": "no_closes",
        }

    current_price = closes[-1]
    window = min(12, len(closes))
    recent_avg = sum(closes[-window:]) / window

    diff_pct = (current_price - recent_avg) / recent_avg * 100.0

    # how big does the move need to be?
    round_trip_fee_pct = FEE_RATE * 2 * 100.0      # e.g. 0.6% * 2 = 1.2%
    min_move_pct = round_trip_fee_pct + FEE_BUFFER_PCT  # e.g. 1.2% + 0.5% = 1.7%

    action = "HOLD"
    reason = f"move {diff_pct:.2f}% < required {min_move_pct:.2f}% (fee-aware)"

    # cooldown check
    now = now_utc()
    if LAST_TRADE_AT and now - LAST_TRADE_AT < timedelta(minutes=COOLDOWN_MINUTES):
        return {
            "ok": True,
            "auto_on": AUTO_TRADER_ON,
            "action": "HOLD",
            "reason": f"cooldown {COOLDOWN_MINUTES}m",
            "price": current_price,
        }

    # figure out if it's a buy-dip or sell-pump
    if diff_pct <= -min_move_pct:
        action = "BUY"
        reason = f"dip {abs(diff_pct):.2f}% >= {min_move_pct:.2f}%"
    elif diff_pct >= min_move_pct:
        action = "SELL"
        reason = f"pump {diff_pct:.2f}% >= {min_move_pct:.2f}%"

    # if auto is off, just report
    if not AUTO_TRADER_ON or action == "HOLD":
        return {
            "ok": True,
            "auto_on": AUTO_TRADER_ON,
            "action": action,
            "reason": reason,
            "price": current_price,
        }

    # =====================================================
    # AUTO IS ON → we might actually TRADE
    # =====================================================
    # before BUY → check USD
    if action == "BUY":
        quote_cur = parse_quote_from_pair(product_id)  # probably "USD"
        needed = MIN_QUOTE * (1 + FEE_RATE)           # buy + fee
        bal = await get_account_balance(quote_cur)
        if bal < needed:
            return {
                "ok": True,
                "auto_on": True,
                "action": "HOLD",
                "reason": f"not enough {quote_cur} ({bal:.2f}) for buy",
                "price": current_price,
            }

        # place BUY
        res = await api_trade(
            {
                "product_id": product_id,
                "side": "BUY",
                "quote_size": str(MIN_QUOTE),
            }
        )
        if res.get("ok"):
            LAST_TRADE_AT = now
            return {
                "ok": True,
                "auto_on": True,
                "action": "BUY",
                "reason": reason,
                "price": current_price,
                "executed": True,
            }
        else:
            return {
                "ok": False,
                "auto_on": True,
                "action": "BUY",
                "reason": f"trade failed: {res.get('error')}",
                "price": current_price,
            }

    # before SELL → check base
    if action == "SELL":
        base_cur = parse_base_from_pair(product_id)  # e.g. "BTC"
        # how much base do we need to sell to get MIN_QUOTE?
        base_needed = (MIN_QUOTE / current_price) * 1.02  # +2% safety
        bal = await get_account_balance(base_cur)
        if bal < base_needed:
            return {
                "ok": True,
                "auto_on": True,
                "action": "HOLD",
                "reason": f"not enough {base_cur} ({bal:.6f}) for sell",
                "price": current_price,
            }

        # place SELL by base_size
        res = await api_trade(
            {
                "product_id": product_id,
                "side": "SELL",
                "base_size": f"{base_needed:.8f}",
            }
        )
        if res.get("ok"):
            LAST_TRADE_AT = now
            return {
                "ok": True,
                "auto_on": True,
                "action": "SELL",
                "reason": reason,
                "price": current_price,
                "executed": True,
            }
        else:
            return {
                "ok": False,
                "auto_on": True,
                "action": "SELL",
                "reason": f"trade failed: {res.get('error')}",
                "price": current_price,
            }

    # fallback
    return {
        "ok": True,
        "auto_on": AUTO_TRADER_ON,
        "action": "HOLD",
        "reason": "no trade",
        "price": current_price,
    }


# =========================================================
# for local run
# =========================================================
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
