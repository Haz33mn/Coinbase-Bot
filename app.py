# app.py
import os
import math
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# serve /static/index.html
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# -----------------------------
# in-memory state (simple)
# -----------------------------
STATE: Dict[str, Any] = {
    "connected": True,
    "coins": [
        {"symbol": "BTC-USD", "price": "109,987.25", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "ETH-USD", "price": "3,879.64", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "BCH-USD", "price": "552.37", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "SOL-USD", "price": "186.39", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "LTC-USD", "price": "99.29", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "AVAX-USD", "price": "18.75", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "LINK-USD", "price": "17.13", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "DOT-USD", "price": "2.95", "signal": "HOLD", "signal_class": "", "confidence": 50},
        {"symbol": "ADA-USD", "price": "0.61", "signal": "HOLD", "signal_class": "", "confidence": 50},
    ],
    "selected": "BTC-USD",
    "mode": "paper",          # "off" | "paper"
    "real_auto": False,
    "paper_balance": 1000.0,
    "paper_equity": 1000.0,
    "paper_pl": 0.0,
    "paper_pct": 0.0,
    "paper_trades": [],       # list of {symbol, side, price, timestamp}
}

# -----------------------------
# helpers
# -----------------------------


def _fmt_price(v: float) -> str:
    # match your earlier look
    return f"{v:,.3f}".rstrip("0").rstrip(".")


def _generate_series(symbol: str, tf: str) -> Dict[str, Any]:
    """
    Make fake but smooth data for every tf.
    Returns:
      { "points": [[t, v], ...], "fallback": false }
    For candles, frontend will still read this (open, high, low, close)
    so we send OHLC-like arrays: [t, open, high, low, close]
    """
    random.seed(symbol + tf)

    if tf == "1d":
        n = 90
    elif tf == "1w":
        n = 120
    elif tf == "1m":
        n = 140
    elif tf == "6m":
        n = 160
    elif tf == "1y":
        # you wanted 1y to NOT be fallback → give long series
        n = 180
    else:
        n = 120

    base = {
        "BTC-USD": 109_000,
        "ETH-USD": 3_800,
        "SOL-USD": 180,
        "BCH-USD": 550,
    }.get(symbol, 100)

    pts: List[List[float]] = []
    for i in range(n):
        # smooth wave
        wave = math.sin(i / 10) * (base * 0.012)
        noise = random.uniform(-base * 0.002, base * 0.002)
        value = base + wave + noise
        # we return OHLC-ish so candles don’t crash
        open_ = value
        close_ = value + random.uniform(-base * 0.001, base * 0.001)
        high_ = max(open_, close_) + base * 0.0006
        low_ = min(open_, close_) - base * 0.0006
        pts.append([i, open_, high_, low_, close_])

    return {
        "points": pts,
        "fallback": False,
    }


def _recalc_paper_from_balance():
    bal = float(STATE["paper_balance"])
    eq = float(STATE["paper_equity"])
    pl = eq - bal
    pct = 0.0 if bal == 0 else (pl / bal) * 100
    STATE["paper_pl"] = round(pl, 2)
    STATE["paper_pct"] = round(pct, 2)


def _add_paper_trade(symbol: str, side: str, price: float):
    STATE["paper_trades"].insert(
        0,
        {
            "symbol": symbol,
            "side": side,
            "price": _fmt_price(price),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )
    # keep history short for UI
    STATE["paper_trades"] = STATE["paper_trades"][:10]


# -----------------------------
# routes
# -----------------------------


@app.get("/", response_class=FileResponse)
async def root():
    """
    Serve the UI.
    We point to static/index.html because that's where you're editing.
    """
    return FileResponse("static/index.html")


@app.get("/state")
async def get_state():
    # simulate prices changing a little so UI looks alive
    for c in STATE["coins"]:
        try:
            p = float(c["price"].replace(",", ""))
        except ValueError:
            p = 0.0
        p = max(0.01, p + random.uniform(-0.4, 0.4))
        c["price"] = _fmt_price(p)
    # recalc paper PL from balance/equity
    _recalc_paper_from_balance()
    return STATE


@app.get("/chart/{symbol}")
async def get_chart(symbol: str, tf: str = "1d"):
    data = _generate_series(symbol, tf)
    return data


@app.post("/toggle_mode")
async def toggle_mode(payload: Dict[str, Any]):
    # payload: { "mode": "paper" | "off" }
    mode = payload.get("mode", "off")
    if mode not in ("off", "paper"):
        mode = "off"
    STATE["mode"] = mode
    return {"ok": True, "mode": mode}


@app.post("/toggle_real_auto")
async def toggle_real_auto(payload: Dict[str, Any]):
    enabled = bool(payload.get("enabled", False))
    STATE["real_auto"] = enabled
    return {"ok": True, "real_auto": enabled}


@app.post("/set_paper_balance")
async def set_paper_balance(payload: Dict[str, Any]):
    balance = float(payload.get("balance", 0))
    if balance < 0:
        balance = 0
    STATE["paper_balance"] = balance
    # when user changes balance, set equity to SAME number for clean start
    STATE["paper_equity"] = balance
    _recalc_paper_from_balance()
    return {"ok": True, "paper_balance": balance}


@app.post("/toggle_paper_auto")
async def toggle_paper_auto(payload: Dict[str, Any]):
    enabled = bool(payload.get("enabled", False))
    # we just store it; the actual "auto logic" is super simple here
    STATE["paper_auto"] = enabled

    # tiny demo: if user just turned it ON, pretend it placed a paper trade
    if enabled:
        sym = STATE.get("selected", "BTC-USD")
        # fake price
        price = float(STATE["coins"][0]["price"].replace(",", "")) if STATE["coins"] else 100.0
        _add_paper_trade(sym, "BUY", price)
        # pretend we made $0.15
        STATE["paper_equity"] = float(STATE["paper_equity"]) + 0.15
        _recalc_paper_from_balance()

    return {"ok": True, "paper_auto": enabled}


# this is optional: just to see if the backend is alive
@app.get("/health")
async def health():
    return {"status": "ok"}


# run locally: uvicorn app:app --host 0.0.0.0 --port 8000
