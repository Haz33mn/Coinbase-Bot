import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE_FILE = Path("state.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> Dict[str, Any]:
    return {
        "connected": True,
        "coins": [
            {"symbol": "BTC-USD", "price": 109_987.035, "signal": "HOLD", "conf": 50},
            {"symbol": "ETH-USD", "price": 3_828.332, "signal": "HOLD", "conf": 50},
            {"symbol": "BCH-USD", "price": 552.575, "signal": "HOLD", "conf": 50},
            {"symbol": "SOL-USD", "price": 195.295, "signal": "HOLD", "conf": 50},
            {"symbol": "LTC-USD", "price": 98.445, "signal": "HOLD", "conf": 50},
            {"symbol": "AVAX-USD", "price": 118.12, "signal": "HOLD", "conf": 50},
            {"symbol": "LINK-USD", "price": 171.38, "signal": "HOLD", "conf": 50},
            {"symbol": "DOT-USD", "price": 32.95, "signal": "HOLD", "conf": 50},
            {"symbol": "ADA-USD", "price": 0.612, "signal": "HOLD", "conf": 50},
        ],
        "selected": "BTC-USD",
        "real_auto": False,
        "real_trades": [],
        "last_update": now_iso(),
    }


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return default_state()
    return default_state()


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


STATE = load_state()


# ----- history generator (realistic-ish, no sine) -----
def _lcg(seed: int):
    a, c, m = 1103515245, 12345, 2**31
    x = seed
    while True:
        x = (a * x + c) % m
        yield x / m


def make_history(symbol: str, tf: str) -> List[Dict[str, float]]:
    if tf == "1D":
        n, vol = 96, 0.002
    elif tf == "1W":
        n, vol = 140, 0.0035
    elif tf == "1M":
        n, vol = 170, 0.005
    elif tf == "6M":
        n, vol = 210, 0.006
    else:  # 1Y
        n, vol = 240, 0.007

    base_price = 100.0
    for c in STATE.get("coins", []):
        if c["symbol"] == symbol:
            base_price = float(c.get("price") or 100.0)
            break

    seed = abs(hash(symbol + tf)) % (2**31 - 1)
    rnd = _lcg(seed)

    price = base_price
    out: List[Dict[str, float]] = []
    for i in range(n):
        r = next(rnd) - 0.5
        change = price * vol * r * 2.0
        price = max(0.0001, price + change)
        high = price * (1 + abs(r) * 0.25)
        low = price * (1 - abs(r) * 0.25)
        open_ = price * (1 - r * 0.1)
        out.append(
            {
                "t": i,
                "o": round(open_, 6),
                "h": round(high, 6),
                "l": round(low, 6),
                "c": round(price, 6),
            }
        )
    return out
# ------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root():
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(static_path)


@app.get("/state")
async def get_state():
    return {
        "connected": STATE.get("connected", False),
        "coins": STATE.get("coins", []),
        "selected": STATE.get("selected"),
        "real_auto": STATE.get("real_auto", False),
        "real_trades": (STATE.get("real_trades") or [])[-25:][::-1],
        "last_update": STATE.get("last_update"),
    }


@app.post("/select")
async def select_coin(payload: Dict[str, str] = Body(...)):
    sym = payload.get("symbol")
    if not sym:
        return {"ok": False, "error": "symbol required"}
    if not any(c["symbol"] == sym for c in STATE["coins"]):
        return {"ok": False, "error": "unknown symbol"}
    STATE["selected"] = sym
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "selected": sym}


@app.get("/history/{symbol}")
async def history(symbol: str, tf: str = "1D"):
    return {"symbol": symbol, "tf": tf, "points": make_history(symbol, tf)}


@app.post("/real/toggle")
async def real_toggle(payload: Dict[str, Any] = Body(...)):
    on = bool(payload.get("on", False))
    STATE["real_auto"] = on
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "real_auto": on}


@app.post("/real/trade")
async def real_trade(payload: Dict[str, Any] = Body(...)):
    """
    This is just a logger.
    Whatever actually hits Coinbase should call THIS with:
    { "symbol": "BTC-USD", "side": "BUY", "price": 109500, "qty": 0.0009 }
    """
    symbol = payload.get("symbol", STATE.get("selected", "BTC-USD"))
    side = (payload.get("side") or "BUY").upper()
    price = float(payload.get("price") or 0)
    qty = float(payload.get("qty") or 0)
    trade = {
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "at": now_iso(),
    }
    STATE.setdefault("real_trades", []).append(trade)
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "trade": trade}
