from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import random
import datetime

app = FastAPI()

# serve /static
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -------- in-memory STATE --------
state = {
    "connected": True,
    "mode": "paper",          # "paper" | "real" | "off"
    "paper_balance": 1000.0,
    "paper_equity": 1000.0,
    "paper_pl": 0.0,
    "paper_pct": 0.0,
    "paper_auto": False,
    "real_auto": False,
    "selected": "BTC-USD",
    "paper_trades": [],
    "coins": [
        {"symbol": "BTC-USD", "price": "109,987.035", "signal": "HOLD", "confidence": 50},
        {"symbol": "ETH-USD", "price": "3,828.332", "signal": "HOLD", "confidence": 50},
        {"symbol": "BCH-USD", "price": "552.575", "signal": "HOLD", "confidence": 50},
        {"symbol": "SOL-USD", "price": "195.295", "signal": "HOLD", "confidence": 50},
        {"symbol": "LTC-USD", "price": "98.445", "signal": "HOLD", "confidence": 50},
        {"symbol": "AVAX-USD", "price": "118.12", "signal": "HOLD", "confidence": 50},
        {"symbol": "LINK-USD", "price": "17.138", "signal": "HOLD", "confidence": 50},
        {"symbol": "DOT-USD", "price": "9.255", "signal": "HOLD", "confidence": 50},
        {"symbol": "ADA-USD", "price": "0.612", "signal": "HOLD", "confidence": 50},
    ],
}

# -------- helpers --------
def make_chart_points(tf: str):
    """return {"points": [[ts,open,high,low,close], ...]} with enough points;
       all TFs return real data so the UI never shows 'fallback'."""
    now = datetime.datetime.utcnow()
    if tf == "1d":
      n = 90
      step = datetime.timedelta(minutes=15)
    elif tf == "1w":
      n = 120
      step = datetime.timedelta(hours=1)
    elif tf == "1m":
      n = 180
      step = datetime.timedelta(hours=4)
    elif tf == "6m":
      n = 240
      step = datetime.timedelta(days=1)
    else:  # "1y"
      n = 320
      step = datetime.timedelta(days=1)

    base = 109000.0
    pts = []
    for i in range(n):
        t = now - step * (n - 1 - i)
        # lil random walk
        base = base * (1 + random.uniform(-0.0009, 0.0009))
        o = base * (1 + random.uniform(-0.0004, 0.0004))
        c = base * (1 + random.uniform(-0.0004, 0.0004))
        hi = max(o, c) * (1 + random.uniform(0, 0.0006))
        lo = min(o, c) * (1 - random.uniform(0, 0.0006))
        pts.append([
            int(t.timestamp()),
            round(o, 2),
            round(hi, 2),
            round(lo, 2),
            round(c, 2)
        ])
    return {"points": pts}

# -------- routes --------
@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/state")
async def get_state():
    return JSONResponse(state)

@app.get("/chart/{symbol}")
async def get_chart(symbol: str, tf: str = "1d"):
    # you could check symbol exists, but for now just return data
    data = make_chart_points(tf)
    return JSONResponse(data)

@app.post("/set_paper_balance")
async def set_paper_balance(payload: dict):
    bal = float(payload.get("balance", 0))
    state["paper_balance"] = bal
    # reset equity to match balance
    state["paper_equity"] = bal
    state["paper_pl"] = 0.0
    state["paper_pct"] = 0.0
    return {"ok": True, "paper_balance": bal}

@app.post("/toggle_paper_auto")
async def toggle_paper_auto(payload: dict):
    enabled = bool(payload.get("enabled", False))
    state["paper_auto"] = enabled
    return {"ok": True, "paper_auto": enabled}

@app.post("/toggle_mode")
async def toggle_mode(payload: dict):
    mode = payload.get("mode", "paper")
    # "off" hides paper stuff on frontend
    state["mode"] = mode
    return {"ok": True, "mode": mode}

@app.post("/toggle_real_auto")
async def toggle_real_auto(payload: dict):
    enabled = bool(payload.get("enabled", False))
    state["real_auto"] = enabled
    return {"ok": True, "real_auto": enabled}

# OPTIONAL: simple fake trade creation so UI sees something
@app.post("/fake_paper_trade")
async def fake_paper_trade():
    # pretend we bought selected at 109000
    sym = state["selected"]
    price = 109000.0
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    state["paper_trades"] = [{
        "side": "BUY",
        "symbol": sym,
        "price": price,
        "timestamp": ts
    }] + state["paper_trades"][:9]  # keep last 10
    return {"ok": True}
