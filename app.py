# app.py
import os
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from typing import List, Dict, Any

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# pretend DB in memory
STATE: Dict[str, Any] = {
    "connected": True,
    "coins": [
        {"symbol": "BTC-USD", "price": 109987.035, "signal": "HOLD", "conf": 50},
        {"symbol": "ETH-USD", "price": 3828.332, "signal": "HOLD", "conf": 50},
        {"symbol": "BCH-USD", "price": 552.575, "signal": "HOLD", "conf": 50},
        {"symbol": "SOL-USD", "price": 195.295, "signal": "HOLD", "conf": 50},
        {"symbol": "LTC-USD", "price": 98.445, "signal": "HOLD", "conf": 50},
        {"symbol": "AVAX-USD", "price": 118.12, "signal": "HOLD", "conf": 50},
        {"symbol": "LINK-USD", "price": 171.38, "signal": "HOLD", "conf": 50},
        {"symbol": "DOT-USD", "price": 32.95, "signal": "HOLD", "conf": 50},
        {"symbol": "ADA-USD", "price": 9.82, "signal": "HOLD", "conf": 50},
    ],
    "selected": "BTC-USD",           # <— this is what we’ll update
    "paper_enabled": True,
    "paper_auto": False,             # ON/OFF button in UI
    "paper_balance": 1000.0,
    "paper_equity": 1000.0,
    "paper_pl": 0.0,
    "paper_trades": [],              # list of dicts
    "real_auto": False,              # real auto OFF / ON
    "last_update": datetime.now(timezone.utc).isoformat(),
}

# mock price history generator
def make_history(symbol: str, tf: str) -> List[Dict[str, float]]:
    # just 1D/1W/1M/6M/1Y shapes — 1Y must NOT fallback
    pts = 120
    base = 100.0
    if tf == "1D":
        pts = 60
    elif tf == "1W":
        pts = 80
    elif tf == "1M":
        pts = 100
    elif tf == "6M":
        pts = 140
    elif tf == "1Y":
        pts = 160   # <-- real data shape, so UI won’t show “fallback”
    out = []
    for i in range(pts):
        # simple wavy data
        price = base + (i % 20) * 1.5
        out.append({
            "t": i,
            "o": price - 0.3,
            "h": price + 0.7,
            "l": price - 0.9,
            "c": price,
        })
    return out


@app.get("/", response_class=HTMLResponse)
async def root():
    # serve /static/index.html
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(static_path)


@app.get("/state")
async def get_state():
    return {
        "connected": STATE["connected"],
        "coins": STATE["coins"],
        "selected": STATE["selected"],
        "paper_enabled": STATE["paper_enabled"],
        "paper_auto": STATE["paper_auto"],
        "paper_balance": STATE["paper_balance"],
        "paper_equity": STATE["paper_equity"],
        "paper_pl": STATE["paper_pl"],
        "paper_trades": STATE["paper_trades"][-10:][::-1],  # latest 10
        "real_auto": STATE["real_auto"],
        "last_update": STATE["last_update"],
    }


# 🔴 NEW: when the user clicks a coin in the UI, call this
@app.post("/select")
async def select_coin(payload: Dict[str, str] = Body(...)):
    symbol = payload.get("symbol")
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    # make sure it’s in the list
    if not any(c["symbol"] == symbol for c in STATE["coins"]):
        return {"ok": False, "error": "unknown symbol"}
    STATE["selected"] = symbol
    STATE["last_update"] = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "selected": symbol}


@app.get("/history/{symbol}")
async def get_history(symbol: str, tf: str = "1D"):
    # front-end asks /history/BTC-USD?tf=1M
    data = make_history(symbol, tf)
    return {"symbol": symbol, "tf": tf, "points": data}


@app.post("/paper/balance")
async def set_paper_balance(payload: Dict[str, float] = Body(...)):
    bal = float(payload.get("balance", 1000))
    STATE["paper_balance"] = bal
    # reset equity & pl to make it simple
    STATE["paper_equity"] = bal
    STATE["paper_pl"] = 0.0
    STATE["last_update"] = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "paper_balance": bal}


@app.post("/paper/toggle")
async def toggle_paper(payload: Dict[str, bool] = Body(...)):
    STATE["paper_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "paper_auto": STATE["paper_auto"]}


@app.post("/real/toggle")
async def toggle_real(payload: Dict[str, bool] = Body(...)):
    STATE["real_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "real_auto": STATE["real_auto"]}


# fake paper trade executor — front-end can call this manually too
@app.post("/paper/fake-trade")
async def fake_trade(payload: Dict[str, Any] = Body(...)):
    symbol = payload.get("symbol", STATE["selected"])
    side = payload.get("side", "BUY").upper()
    price = float(payload.get("price", 100.0))
    qty = float(payload.get("qty", 1.0))

    # update equity/pl just to show in UI
    if side == "BUY":
        STATE["paper_pl"] -= price * qty * 0.000  # no fee in paper for now

    trade = {
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    STATE["paper_trades"].append(trade)
    STATE["last_update"] = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "trade": trade}
