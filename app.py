import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- simple file persistence ----------
STATE_FILE = Path("state.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> Dict[str, Any]:
    return {
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
        "selected": "BTC-USD",
        # paper
        "paper_enabled": True,
        "paper_auto": False,
        "paper_balance": 1000.0,
        "paper_equity": 1000.0,
        "paper_pl": 0.0,
        "paper_trades": [],
        # real
        "real_auto": False,
        "real_trades": [],
        "last_update": _now_iso(),
    }


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception:
            return default_state()
    return default_state()


def save_state(state: Dict[str, Any]) -> None:
    # render free dynos may lose this on restart/redeploy, but this survives page reloads
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


STATE = load_state()


# ---------- helpers ----------
def make_history(symbol: str, tf: str) -> List[Dict[str, float]]:
    # just mock data so UI always has something
    if tf == "1D":
        pts = 60
    elif tf == "1W":
        pts = 80
    elif tf == "1M":
        pts = 100
    elif tf == "6M":
        pts = 140
    else:  # 1Y or anything else
        pts = 160

    base = 100.0
    arr = []
    for i in range(pts):
        price = base + (i % 20) * 1.4
        arr.append(
            {
                "t": i,
                "o": price - 0.4,
                "h": price + 0.8,
                "l": price - 1.0,
                "c": price,
            }
        )
    return arr


# ---------- routes ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(static_path)


@app.get("/state")
async def get_state():
    # trim trades to last 12 each
    paper_trades = (STATE.get("paper_trades") or [])[-12:][::-1]
    real_trades = (STATE.get("real_trades") or [])[-12:][::-1]
    return {
        "connected": STATE.get("connected", False),
        "coins": STATE.get("coins", []),
        "selected": STATE.get("selected"),
        "paper_enabled": STATE.get("paper_enabled", True),
        "paper_auto": STATE.get("paper_auto", False),
        "paper_balance": STATE.get("paper_balance", 0.0),
        "paper_equity": STATE.get("paper_equity", 0.0),
        "paper_pl": STATE.get("paper_pl", 0.0),
        "paper_trades": paper_trades,
        "real_auto": STATE.get("real_auto", False),
        "real_trades": real_trades,
        "last_update": STATE.get("last_update"),
    }


@app.post("/select")
async def select_coin(payload: Dict[str, str] = Body(...)):
    symbol = payload.get("symbol")
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    if not any(c["symbol"] == symbol for c in STATE["coins"]):
        return {"ok": False, "error": "unknown symbol"}
    STATE["selected"] = symbol
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "selected": symbol}


@app.get("/history/{symbol}")
async def history(symbol: str, tf: str = "1D"):
    return {
        "symbol": symbol,
        "tf": tf,
        "points": make_history(symbol, tf),
    }


@app.post("/paper/balance")
async def set_paper_balance(payload: Dict[str, Any] = Body(...)):
    bal = float(payload.get("balance", 1000))
    STATE["paper_balance"] = bal
    STATE["paper_equity"] = bal
    STATE["paper_pl"] = 0.0
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "paper_balance": bal}


@app.post("/paper/toggle")
async def toggle_paper(payload: Dict[str, Any] = Body(...)):
    STATE["paper_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "paper_auto": STATE["paper_auto"]}


@app.post("/paper/fake-trade")
async def paper_fake_trade(payload: Dict[str, Any] = Body(...)):
    symbol = payload.get("symbol", STATE["selected"])
    side = payload.get("side", "BUY").upper()
    price = float(payload.get("price", 100.0))
    qty = float(payload.get("qty", 1.0))

    # update equity
    if side == "BUY":
        # pretend we open a position
        STATE["paper_equity"] -= price * qty * 0.0
    elif side == "SELL":
        STATE["paper_equity"] += price * qty * 0.0

    # recalc P/L vs balance
    STATE["paper_pl"] = STATE["paper_equity"] - STATE["paper_balance"]

    trade = {
        "kind": "paper",
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "at": _now_iso(),
    }
    STATE.setdefault("paper_trades", []).append(trade)
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "trade": trade}


@app.post("/real/toggle")
async def real_toggle(payload: Dict[str, Any] = Body(...)):
    STATE["real_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "real_auto": STATE["real_auto"]}


@app.post("/real/fake-trade")
async def real_fake_trade(payload: Dict[str, Any] = Body(...)):
    # this is just a logger so you can SEE what the AI would have done for real
    symbol = payload.get("symbol", STATE["selected"])
    side = payload.get("side", "BUY").upper()
    price = float(payload.get("price", 100.0))
    qty = float(payload.get("qty", 1.0))

    trade = {
        "kind": "real",
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "at": _now_iso(),
    }
    STATE.setdefault("real_trades", []).append(trade)
    STATE["last_update"] = _now_iso()
    save_state(STATE)
    return {"ok": True, "trade": trade}
