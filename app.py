import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests  # make sure `requests` is in requirements.txt

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
COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> Dict[str, Any]:
    return {
        "connected": False,
        "coins": [],
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


# ---------- coinbase sync ----------

def fetch_coinbase_usd_markets() -> List[Dict[str, Any]]:
    """
    Pull all products from Coinbase Exchange, keep only USD quote ones,
    and build our internal coin objects.
    """
    try:
        resp = requests.get(COINBASE_PRODUCTS_URL, timeout=5)
        resp.raise_for_status()
        products = resp.json()
    except Exception:
        # if Coinbase is down, keep whatever we had
        return []

    coins: List[Dict[str, Any]] = []
    for p in products:
        # symbol is like "BTC-USD"
        if p.get("quote_currency") != "USD":
            continue
        symbol = p.get("id")
        if not symbol:
            continue

        # try to get a price (public ticker)
        price = 0.0
        try:
            t = requests.get(
                f"https://api.exchange.coinbase.com/products/{symbol}/ticker",
                timeout=4,
            )
            if t.status_code == 200:
                jt = t.json()
                price = float(jt.get("price") or 0.0)
        except Exception:
            pass

        coins.append(
            {
                "symbol": symbol,
                "price": price,
                "signal": "HOLD",
                "conf": 50,
            }
        )

    return coins


def ensure_coins():
    """
    On startup or when /sync-coins is hit, make sure we have
    *all* Coinbase USD markets.
    """
    global STATE
    coins = fetch_coinbase_usd_markets()
    if coins:
        # if we already had a selected coin, keep it if it still exists
        prev_selected = STATE.get("selected") or "BTC-USD"
        STATE["coins"] = sorted(coins, key=lambda c: c["symbol"])
        if any(c["symbol"] == prev_selected for c in STATE["coins"]):
            STATE["selected"] = prev_selected
        else:
            STATE["selected"] = STATE["coins"][0]["symbol"]
        STATE["connected"] = True
        STATE["last_update"] = now_iso()
        save_state(STATE)
    else:
        # no internet / no Coinbase -> fall back to small static list
        if not STATE.get("coins"):
            STATE["connected"] = False
            STATE["coins"] = [
                {"symbol": "BTC-USD", "price": 109_987.035, "signal": "HOLD", "conf": 50},
                {"symbol": "ETH-USD", "price": 3_828.332, "signal": "HOLD", "conf": 50},
            ]
            STATE["selected"] = "BTC-USD"
            STATE["last_update"] = now_iso()
            save_state(STATE)


# do it once on boot
ensure_coins()


# ---------- history generator ----------

def _lcg(seed: int):
    a, c, m = 1103515245, 12345, 2**31
    x = seed
    while True:
        x = (a * x + c) % m
        yield x / m


def make_history(symbol: str, tf: str) -> List[Dict[str, float]]:
    # more points for longer TFs
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

    # base price from state if we have it
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


# ---------- routes ----------

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

    # if symbol not in list but user asked for it, we try to add it
    if not any(c["symbol"] == sym for c in STATE["coins"]):
        # try to fetch price for that exact product
        try:
            t = requests.get(
                f"https://api.exchange.coinbase.com/products/{sym}/ticker", timeout=4
            )
            if t.status_code == 200:
                jt = t.json()
                price = float(jt.get("price") or 0.0)
            else:
                price = 0.0
        except Exception:
            price = 0.0
        STATE["coins"].append(
            {"symbol": sym, "price": price, "signal": "HOLD", "conf": 50}
        )

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
    Your real trading worker / webhook should call this EVERY time it
    actually buys/sells on Coinbase. This is just a logger.
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


@app.post("/sync-coins")
async def sync_coins():
    ensure_coins()
    return {"ok": True, "count": len(STATE.get("coins", []))}
