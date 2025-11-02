from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import time
from datetime import datetime, timedelta, timezone

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# make sure static/ exists
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------- GLOBAL STATE ----------------
COIN_LIST = [
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

LAST_PRICES = {}

# paper account (simple)
PAPER_STATE = {
    "initial": 1000.0,     # user-set fake money
    "cash": 1000.0,
    "positions": {},       # "BTC-USD": {amount, avg_price}
    "equity": 1000.0,
    "pnl": 0.0,
}

# how much to use per paper trade (we keep this, but we hide complexity in UI)
PAPER_TRADE_DOLLARS = 25.0

# toggles
AUTO_PAPER = False   # (not used by default, we do manual button)
AUTO_REAL = False    # “real auto: on/off” button sets this

# ---------------- HELPERS ----------------
async def fetch_spot_price(symbol: str) -> float:
    url = f"https://api.coinbase.com/v2/prices/{symbol}/spot"
    async with httpx.AsyncClient(timeout=6.0) as client:
        r = await client.get(url)
    if r.status_code == 200:
        return float(r.json()["data"]["amount"])
    return 0.0


async def fetch_candles(symbol: str, tf: str):
    """
    Proper 1D, 1W, 1M, 6M, 1Y using Coinbase Exchange candles.
    6M and 1Y will now be different because we pass start/end.
    """
    # seconds per candle
    if tf == "1D":
        gran = 60 * 15  # 15m
        start = None
        end = None
    elif tf == "1W":
        gran = 60 * 60  # 1h
        start = None
        end = None
    elif tf == "1M":
        gran = 60 * 60 * 6  # 6h
        start = None
        end = None
    elif tf == "6M":
        gran = 60 * 60 * 24  # daily
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=180)
        start = start_dt.isoformat()
        end = end_dt.isoformat()
    elif tf == "1Y":
        gran = 60 * 60 * 24  # daily
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=365)
        start = start_dt.isoformat()
        end = end_dt.isoformat()
    else:
        gran = 60 * 15
        start = None
        end = None

    base_url = f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity={gran}"

    if start and end:
        url = f"{base_url}&start={start}&end={end}"
    else:
        url = base_url

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url, headers={"User-Agent": "cb-bot"})
        if r.status_code == 200:
            raw = r.json()
            raw.sort(key=lambda x: x[0])  # oldest -> newest
            candles = []
            for c in raw:
                candles.append(
                    {
                        "t": c[0],
                        "o": c[3],
                        "h": c[2],
                        "l": c[1],
                        "c": c[4],
                    }
                )
            if candles:
                return candles, False
    except Exception:
        pass

    # fallback mock
    base_price = LAST_PRICES.get(symbol, 100.0)
    mock = []
    price = base_price
    for i in range(80):
        if i % 2 == 0:
            price *= 1.003
        else:
            price *= 0.997
        ts = int(time.time()) - (80 - i) * gran
        mock.append(
            {
                "t": ts,
                "o": price * 0.998,
                "h": price * 1.003,
                "l": price * 0.995,
                "c": price,
            }
        )
    return mock, True


def recompute_paper_equity():
    total = PAPER_STATE["cash"]
    for sym, pos in PAPER_STATE["positions"].items():
        px = LAST_PRICES.get(sym, 0.0)
        total += pos["amount"] * px
    PAPER_STATE["equity"] = total
    PAPER_STATE["pnl"] = total - PAPER_STATE["initial"]


def do_one_paper_cycle():
    """
    What a single 'Paper Trade' button does.
    - find dip coin (< -1%)
    - find rip coin (> +1.2%)
    - buy dip, sell rip
    """
    if not LAST_PRICES:
        return {"did_trade": False, "reason": "no prices"}

    items = list(LAST_PRICES.items())
    avg = sum(p for _, p in items) / len(items)

    biggest_drop_sym = None
    biggest_drop_pct = 0
    biggest_rip_sym = None
    biggest_rip_pct = 0

    for sym, price in items:
      pct = (price - avg) / avg * 100
      if pct < biggest_drop_pct:
          biggest_drop_pct = pct
          biggest_drop_sym = sym
      if pct > biggest_rip_pct:
          biggest_rip_pct = pct
          biggest_rip_sym = sym

    did = False

    # BUY dip
    if biggest_drop_sym and biggest_drop_pct < -1.0:
        price = LAST_PRICES[biggest_drop_sym]
        dollars = min(PAPER_TRADE_DOLLARS, PAPER_STATE["cash"])
        if dollars > 5:
            amt = dollars / price
            pos = PAPER_STATE["positions"].get(biggest_drop_sym, {"amount": 0.0, "avg_price": price})
            new_amt = pos["amount"] + amt
            new_avg = ((pos["amount"] * pos["avg_price"]) + dollars) / new_amt
            PAPER_STATE["positions"][biggest_drop_sym] = {
                "amount": new_amt,
                "avg_price": new_avg,
            }
            PAPER_STATE["cash"] -= dollars
            did = True

    # SELL rip
    if biggest_rip_sym and biggest_rip_pct > 1.2:
        price = LAST_PRICES[biggest_rip_sym]
        pos = PAPER_STATE["positions"].get(biggest_rip_sym)
        if pos and pos["amount"] * price > 5:
            sell_val = min(PAPER_TRADE_DOLLARS, pos["amount"] * price * 0.25)
            sell_amt = sell_val / price
            pos["amount"] -= sell_amt
            if pos["amount"] <= 1e-9:
                del PAPER_STATE["positions"][biggest_rip_sym]
            PAPER_STATE["cash"] += sell_val
            did = True

    recompute_paper_equity()
    return {"did_trade": did}


# ---------------- ROUTES ----------------
@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/prices")
async def get_prices():
    global LAST_PRICES
    out = {}
    for sym in COIN_LIST:
        price = await fetch_spot_price(sym)
        if price > 0:
            out[sym] = price
    if out:
        LAST_PRICES = out
        recompute_paper_equity()
    return {"prices": LAST_PRICES, "updated": time.strftime("%I:%M:%S %p")}


@app.get("/history/{symbol}")
async def history(symbol: str, tf: str = "1D"):
    candles, fallback = await fetch_candles(symbol, tf)
    return {"symbol": symbol, "tf": tf, "candles": candles, "fallback": fallback}


@app.get("/paper-stats")
async def paper_stats():
    recompute_paper_equity()
    percent = 0.0
    if PAPER_STATE["initial"] > 0:
        percent = (PAPER_STATE["pnl"] / PAPER_STATE["initial"]) * 100.0
    return {
        "initial": round(PAPER_STATE["initial"], 2),
        "equity": round(PAPER_STATE["equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
        "percent": round(percent, 2),
    }


@app.post("/paper-config")
async def paper_config(req: Request):
    """
    body can have:
    {
      "initial_balance": 2000,
      "trade_dollars": 25  (optional)
    }
    """
    global PAPER_TRADE_DOLLARS
    data = await req.json()
    init_bal = data.get("initial_balance")
    if init_bal is not None:
        init_bal = float(init_bal)
        if init_bal < 100:
            init_bal = 100.0
        if init_bal > 50000:
            init_bal = 50000.0
        PAPER_STATE["initial"] = init_bal
        PAPER_STATE["cash"] = init_bal
        PAPER_STATE["positions"] = {}
        recompute_paper_equity()

    td = data.get("trade_dollars")
    if td is not None:
        td = float(td)
        if td < 5:
            td = 5.0
        if td > 500:
            td = 500.0
        PAPER_TRADE_DOLLARS = td

    percent = 0.0
    if PAPER_STATE["initial"] > 0:
        percent = (PAPER_STATE["pnl"] / PAPER_STATE["initial"]) * 100.0

    return {
        "ok": True,
        "initial": PAPER_STATE["initial"],
        "equity": round(PAPER_STATE["equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
        "percent": round(percent, 2),
        "trade_dollars": PAPER_TRADE_DOLLARS,
    }


@app.post("/paper-trade-once")
async def paper_trade_once():
    res = do_one_paper_cycle()
    percent = 0.0
    if PAPER_STATE["initial"] > 0:
        percent = (PAPER_STATE["pnl"] / PAPER_STATE["initial"]) * 100.0
    return {
        "ok": True,
        "did_trade": res["did_trade"],
        "equity": round(PAPER_STATE["equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
        "percent": round(percent, 2),
    }


@app.get("/real-auto")
async def real_auto_state():
    return {"on": AUTO_REAL}


@app.post("/real-auto")
async def real_auto_toggle(req: Request):
    global AUTO_REAL
    data = await req.json()
    on = bool(data.get("on", False))
    AUTO_REAL = on
    return {"on": AUTO_REAL, "note": "real auto is just a toggle right now — hook to Coinbase Advanced API to trade for real."}


@app.post("/run-bot")
async def run_bot():
    # auto paper (if we ever want to turn it on later)
    if AUTO_PAPER:
        do_one_paper_cycle()

    # auto real (placeholder)
    if AUTO_REAL:
        # here is where you'd place real order logic
        return {"auto_real": True, "status": "would place real orders here"}
    return {"status": "idle"}
