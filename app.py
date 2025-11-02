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
        "paper_enabled": True,
        "paper_auto": False,
        "paper_balance": 1_000.0,
        "paper_equity": 1_000.0,
        "paper_pl": 0.0,
        "paper_trades": [],
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


# ---------- history generator (no more sine) ----------
def _lcg(seed: int):
    # very tiny deterministic PRNG
    a = 1103515245
    c = 12345
    m = 2**31
    x = seed
    while True:
        x = (a * x + c) % m
        yield x / m


def make_history(symbol: str, tf: str) -> List[Dict[str, float]]:
    # how many points per tf
    if tf == "1D":
        n = 96
        vol = 0.002  # 0.2%
    elif tf == "1W":
        n = 140
        vol = 0.0035
    elif tf == "1M":
        n = 170
        vol = 0.005
    elif tf == "6M":
        n = 210
        vol = 0.006
    else:  # 1Y
        n = 240
        vol = 0.007

    # find base from coins
    coins = STATE.get("coins", [])
    base_price = 100.0
    for c in coins:
        if c["symbol"] == symbol:
            base_price = float(c.get("price") or 100.0)
            break

    # seed based on symbol+tf so it looks stable
    seed = abs(hash(symbol + tf)) % (2**31 - 1)
    rnd = _lcg(seed)

    price = base_price
    out: List[Dict[str, float]] = []
    for i in range(n):
        # move around current price
        r = next(rnd) - 0.5  # -0.5..0.5
        change = price * vol * r * 2.0
        price = max(0.0001, price + change)
        high = price * (1 + abs(r) * 0.25)
        low = price * (1 - abs(r) * 0.25)
        open_ = price * (1 - r * 0.1)
        close_ = price
        out.append(
            {
                "t": i,
                "o": round(open_, 6),
                "h": round(high, 6),
                "l": round(low, 6),
                "c": round(close_, 6),
            }
        )
    return out
# ------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root():
    # serve /static/index.html
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(static_path)


@app.get("/state")
async def get_state():
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
    STATE["last_update"] = now_iso()
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
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "paper_balance": bal}


@app.post("/paper/enable")
async def enable_paper(payload: Dict[str, Any] = Body(...)):
    enabled = bool(payload.get("enabled", True))
    STATE["paper_enabled"] = enabled
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "paper_enabled": enabled}


@app.post("/paper/toggle")
async def toggle_paper(payload: Dict[str, Any] = Body(...)):
    STATE["paper_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "paper_auto": STATE["paper_auto"]}


@app.post("/paper/fake-trade")
async def paper_fake_trade(payload: Dict[str, Any] = Body(...)):
    symbol = payload.get("symbol", STATE["selected"])
    side = payload.get("side", "BUY").upper()
    price = float(payload.get("price", 100.0))
    qty = float(payload.get("qty", 1.0))

    fee = price * qty * 0.001  # 0.1% fee
    if side == "BUY":
      STATE["paper_equity"] -= fee

    STATE["paper_pl"] = STATE["paper_equity"] - STATE["paper_balance"]
    trade = {
        "kind": "paper",
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "at": now_iso(),
    }
    STATE.setdefault("paper_trades", []).append(trade)
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "trade": trade}


@app.post("/real/toggle")
async def real_toggle(payload: Dict[str, Any] = Body(...)):
    STATE["real_auto"] = bool(payload.get("on", False))
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "real_auto": STATE["real_auto"]}


@app.post("/real/fake-trade")
async def real_fake_trade(payload: Dict[str, Any] = Body(...)):
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
        "at": now_iso(),
    }
    STATE.setdefault("real_trades", []).append(trade)
    STATE["last_update"] = now_iso()
    save_state(STATE)
    return {"ok": True, "trade": trade}
