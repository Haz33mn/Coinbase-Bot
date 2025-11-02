# app.py
import os
import json
import logging
from io import StringIO
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

# this is the official SDK: https://github.com/coinbase/coinbase-advanced-py
# make sure requirements.txt has: fastapi uvicorn httpx coinbase-advanced-py
from coinbase.rest import RESTClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coinbase-bot-backend")

app = FastAPI(title="Coinbase Bot Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ────────────────────────────────────────────────────────────────
# ENV + GLOBALS
# ────────────────────────────────────────────────────────────────
COINBASE_PRICE_URL = "https://api.coinbase.com/v2/prices/{product_id}/spot"

FEE_RATE = float(os.getenv("ESTIMATED_FEE_RATE", "0.005"))
RAW_KEY_JSON = os.getenv("COINBASE_API_KEY_JSON")
REAL_TRADING_ENABLED = os.getenv("REAL_TRADING_ENABLED", "false").lower() == "true"
ADMIN_CONFIRM_TOKEN = os.getenv("ADMIN_CONFIRM_TOKEN")

if not RAW_KEY_JSON:
    logger.warning("⚠️  COINBASE_API_KEY_JSON is not set in environment!")

# keep the last 50 real trades in memory so UI can show them
REAL_TRADE_LOG: List[Dict[str, Any]] = []


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_cb_client() -> RESTClient:
    """
    Build a Coinbase REST client from the JSON the user pasted into
    the Render env var COINBASE_API_KEY_JSON.
    """
    if not RAW_KEY_JSON:
        raise RuntimeError("COINBASE_API_KEY_JSON not set")
    # user maybe pasted with single quotes – normalize
    raw = RAW_KEY_JSON.strip()
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    if raw and raw[0] != "{" and "'" in raw:
        raw = raw.replace("'", '"')
    return RESTClient(key_file=StringIO(raw))


# ────────────────────────────────────────────────────────────────
# MODELS
# ────────────────────────────────────────────────────────────────
class SimulateOrderRequest(BaseModel):
    product_id: str
    side: str
    usd_amount: float
    fee_rate: Optional[float] = None


class OrderRequest(BaseModel):
    product_id: str
    side: str   # "buy" or "sell"
    usd_amount: float


# ────────────────────────────────────────────────────────────────
# BASIC ENDPOINTS
# ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "real_trading": REAL_TRADING_ENABLED}


@app.get("/api/key-check")
def key_check():
    try:
        client = get_cb_client()
        # just test a cheap call
        _ = client.get_accounts(limit=1)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ────────────────────────────────────────────────────────────────
# LIST ALL COINBASE PRODUCTS (this is what you asked for)
# ────────────────────────────────────────────────────────────────
@app.get("/api/products")
def list_products():
    """
    Pull **all** products from Coinbase Advanced and return only the ones
    you can trade against USD.
    """
    try:
        client = get_cb_client()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"client init failed: {e}")

    products = client.get_products()  # SDK call
    out = []
    # products.products is a list of product objects
    for p in products.products:
        # pick what the frontend needs
        if getattr(p, "quote_currency_id", None) == "USD":
            out.append({
                "product_id": p.product_id,
                "price": p.price,
                "base": p.base_currency_id,
                "quote": p.quote_currency_id,
            })
    # sort for nice UI
    out.sort(key=lambda x: x["product_id"])
    return {"products": out}


# ────────────────────────────────────────────────────────────────
# PUBLIC PRICE (still useful for chart fallback)
# ────────────────────────────────────────────────────────────────
@app.get("/api/price/{product_id}")
async def get_current_price(product_id: str):
    url = COINBASE_PRICE_URL.format(product_id=product_id)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"price fetch failed: {r.text}")
        amt = float(r.json()["data"]["amount"])
        return {"product_id": product_id, "spot": amt}


# ────────────────────────────────────────────────────────────────
# SIMULATE (for fee check)
# ────────────────────────────────────────────────────────────────
@app.post("/api/simulate-order")
async def simulate_order(req: SimulateOrderRequest):
    # get real price from public
    url = COINBASE_PRICE_URL.format(product_id=req.product_id)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        price = float(r.json()["data"]["amount"])

    fee_rate = req.fee_rate if req.fee_rate is not None else FEE_RATE
    usd = float(req.usd_amount)
    fee_usd = usd * fee_rate
    if req.side.lower() == "buy":
        usd_after_fee = max(0.0, usd - fee_usd)
        coin_amount = usd_after_fee / price
    else:
        # selling – we just report the fee and how much you'd get
        usd_after_fee = max(0.0, usd - fee_usd)
        coin_amount = usd_after_fee / price

    return {
        "product_id": req.product_id.upper(),
        "side": req.side.lower(),
        "spot_price": price,
        "usd_requested": usd,
        "fee_rate": fee_rate,
        "fee_usd": round(fee_usd, 8),
        "usd_after_fee": round(usd_after_fee, 8),
        "estimated_coin_amount": round(coin_amount, 12),
    }


# ────────────────────────────────────────────────────────────────
# REAL ORDER (this is the part you wanted “to just work”)
# ────────────────────────────────────────────────────────────────
@app.post("/api/order")
def place_order(
    order: OrderRequest,
    x_admin_token: Optional[str] = Header(None),
):
    # 1) safety gates
    if not REAL_TRADING_ENABLED:
        raise HTTPException(status_code=403, detail="REAL_TRADING_ENABLED is false on the server")

    if not ADMIN_CONFIRM_TOKEN:
        raise HTTPException(status_code=403, detail="ADMIN_CONFIRM_TOKEN is not set on the server")

    if x_admin_token != ADMIN_CONFIRM_TOKEN:
        raise HTTPException(status_code=403, detail="bad admin token")

    # 2) build client
    try:
        client = get_cb_client()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"coinbase client init failed: {e}")

    product_id = order.product_id.upper()
    side = order.side.lower()
    usd_amount = float(order.usd_amount)
    if usd_amount <= 0:
        raise HTTPException(status_code=400, detail="usd_amount must be > 0")

    # 3) actually send to Coinbase using the SDK
    # buy = quote_size (usd), sell = base size (coin)
    # so for SELL we need to know how much coin to sell -> we fetch price to convert
    client_order_id = ""  # let Coinbase generate

    try:
        if side == "buy":
            # market buy spending X USD
            res = client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=str(usd_amount),
            )
        elif side == "sell":
            # get spot to convert USD -> coin
            # (you can later change this to "sell everything in the account")
            price_url = COINBASE_PRICE_URL.format(product_id=product_id)
            with httpx.Client(timeout=10) as hc:
                pr = hc.get(price_url)
                pr.raise_for_status()
                price_val = float(pr.json()["data"]["amount"])
            base_size = usd_amount / price_val
            res = client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                size=str(base_size),
            )
        else:
            raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")
    except Exception as e:
        logger.exception("real order failed")
        raise HTTPException(status_code=502, detail=f"Coinbase order failed: {e}")

    # 4) log it in memory for the UI
    trade_entry = {
        "product_id": product_id,
        "side": side,
        "usd_amount": usd_amount,
        "coin_amount": res.order.total_quantity if hasattr(res, "order") else None,
        "placed_at": utc_now_iso(),
        "raw": res.to_dict() if hasattr(res, "to_dict") else str(res),
    }
    REAL_TRADE_LOG.insert(0, trade_entry)
    # keep only last 50
    del REAL_TRADE_LOG[50:]

    return {"status": "ok", "from": "coinbase", "result": trade_entry}


# ────────────────────────────────────────────────────────────────
# FRONTEND CAN CALL THIS TO FILL THE “real trades (latest)” LIST
# ────────────────────────────────────────────────────────────────
@app.get("/api/real-trades")
def get_real_trades():
    return {"trades": REAL_TRADE_LOG}
