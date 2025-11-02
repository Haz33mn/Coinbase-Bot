from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# make sure /static exists
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


# ------------------ GLOBAL STATE ------------------
AUTO_MODE = "off"  # off | paper | live
LAST_PRICES = {}

# paper account
PAPER_STATE = {
    "cash": 1000.0,
    "positions": {},
    "last_equity": 1000.0,
    "pnl": 0.0,
    "trades": [],
}

# this is what you wanted to make editable from the UI
PAPER_TRADE_DOLLARS = 25.0  # default per paper trade

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


# ------------------ HELPERS ------------------
async def fetch_spot_price(symbol: str) -> float:
    url = f"https://api.coinbase.com/v2/prices/{symbol}/spot"
    async with httpx.AsyncClient(timeout=6.0) as client:
        r = await client.get(url)
    if r.status_code == 200:
        data = r.json()
        return float(data["data"]["amount"])
    return 0.0


async def fetch_candles(symbol: str, tf: str):
    """
    1) try reliable public exchange endpoint
    2) if it fails -> make fake candles so UI never goes blank
    returns (candles, used_fallback)
    """
    tf_map = {
        "1D": 60 * 15,      # 15m
        "1W": 60 * 60,      # 1h
        "1M": 60 * 60 * 6,  # 6h
        "6M": 60 * 60 * 24, # 1d
        "1Y": 60 * 60 * 24, # 1d
    }
    gran = tf_map.get(tf, 60 * 15)

    ex_url = f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity={gran}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(ex_url, headers={"User-Agent": "cb-bot"})
        if r.status_code == 200:
            raw = r.json()  # [[time, low, high, open, close, volume], ...] newest first
            raw.sort(key=lambda x: x[0])
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

    # fallback
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
    PAPER_STATE["last_equity"] = total
    PAPER_STATE["pnl"] = total - 1000.0


# ------------------ API ------------------
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
async def get_history(symbol: str, tf: str = "1D"):
    candles, fallback = await fetch_candles(symbol, tf)
    return {
        "symbol": symbol,
        "tf": tf,
        "candles": candles,
        "fallback": fallback,
    }


@app.get("/trade-mode")
async def get_trade_mode():
    return {"mode": AUTO_MODE}


@app.post("/trade-mode")
async def set_trade_mode(req: Request):
    global AUTO_MODE
    data = await req.json()
    mode = data.get("mode", "off")
    if mode not in ("off", "paper", "live"):
        mode = "off"
    AUTO_MODE = mode
    return {"mode": AUTO_MODE}


@app.get("/paper-stats")
async def paper_stats():
    recompute_paper_equity()
    return {
        "cash": round(PAPER_STATE["cash"], 2),
        "equity": round(PAPER_STATE["last_equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
        "positions": PAPER_STATE["positions"],
        "trades": PAPER_STATE["trades"][-30:],
        "started_with": 1000.0,
        "trade_dollars": PAPER_TRADE_DOLLARS,
    }


@app.get("/paper-config")
async def get_paper_config():
    recompute_paper_equity()
    return {
        "trade_dollars": PAPER_TRADE_DOLLARS,
        "equity": round(PAPER_STATE["last_equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
    }


@app.post("/paper-config")
async def set_paper_config(req: Request):
    """
    body: { "trade_dollars": 50 }
    """
    global PAPER_TRADE_DOLLARS
    data = await req.json()
    td = float(data.get("trade_dollars", PAPER_TRADE_DOLLARS))
    # keep it safe
    if td < 5:
        td = 5
    if td > 500:
        td = 500
    PAPER_TRADE_DOLLARS = td
    recompute_paper_equity()
    return {
        "ok": True,
        "trade_dollars": PAPER_TRADE_DOLLARS,
        "equity": round(PAPER_STATE["last_equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
    }


@app.get("/live-stats")
async def live_stats():
    return {
        "connected": True,
        "pnl": 0.0,
        "note": "live P/L not implemented yet",
    }


@app.post("/run-bot")
async def run_bot():
    """
    tiny paper strategy:
    - buy coin that's >1% under the basket
    - sell coin that's >1.2% over the basket
    - this now respects PAPER_TRADE_DOLLARS coming from the UI
    """
    global PAPER_TRADE_DOLLARS

    if AUTO_MODE != "paper":
        return {"status": "idle", "mode": AUTO_MODE}

    if not LAST_PRICES:
        return {"status": "no-prices"}

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

    did_trade = False

    # BUY dip (>1%) -> needs to clear fee
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
            PAPER_STATE["trades"].append(
                {
                    "side": "BUY",
                    "symbol": biggest_drop_sym,
                    "price": price,
                    "size_usd": dollars,
                    "ts": time.time(),
                }
            )
            did_trade = True

    # SELL rip (>1.2%)
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
            PAPER_STATE["trades"].append(
                {
                    "side": "SELL",
                    "symbol": biggest_rip_sym,
                    "price": price,
                    "size_usd": sell_val,
                    "ts": time.time(),
                }
            )
            did_trade = True

    recompute_paper_equity()
    return {
        "status": "ok",
        "did_trade": did_trade,
        "paper": {
            "cash": round(PAPER_STATE["cash"], 2),
            "equity": round(PAPER_STATE["last_equity"], 2),
            "pnl": round(PAPER_STATE["pnl"], 2),
            "trade_dollars": PAPER_TRADE_DOLLARS,
        },
    }
