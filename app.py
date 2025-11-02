from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import time
import os

app = FastAPI()

# allow your browser to hit it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve /static/index.html
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    # always show the UI
    return RedirectResponse(url="/static/index.html")


# --------------------------------------------------------------------------------------
# GLOBAL STATE (kept in memory — fine for now on Render free)
# --------------------------------------------------------------------------------------
AUTO_MODE = "off"  # "off" | "paper" | "live"
LAST_PRICES = {}   # {"BTC-USD": 109000.0, ...}

PAPER_STATE = {
    "cash": 1000.0,
    "positions": {},  # "BTC-USD": {"amount": 0.0003, "avg_price": 108000}
    "last_equity": 1000.0,
    "pnl": 0.0,
    "trades": [],  # list of dicts
}

# how much to "paper trade" each time (very small)
PAPER_TRADE_DOLLARS = 25.0

# --------------------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------------------
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


async def fetch_spot_price(symbol: str) -> float:
    """
    Try Coinbase public spot endpoint.
    """
    url = f"https://api.coinbase.com/v2/prices/{symbol}/spot"
    async with httpx.AsyncClient(timeout=6.0) as client:
        r = await client.get(url)
    if r.status_code == 200:
        data = r.json()
        return float(data["data"]["amount"])
    # fallback
    return 0.0


async def fetch_candles(symbol: str, granularity: str) -> list:
    """
    We fake history if Coinbase doesn't give us any.
    granularity: "1D", "1W", "1M", "6M", "1Y"  -> we map to minutes roughly
    """
    # NOTE: their /candles endpoint is touchy, so we just build a mock if it fails
    # real path (no auth) for public products:
    gran_map = {
        "1D": 60,     # 1h candles
        "1W": 15,     # 15m candles
        "1M": 60,     # 1h candles (less data)
        "6M": 360,    # 6h
        "1Y": 1440,   # 1d
    }
    product_id = symbol.replace("-", "-")
    url = (
        f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"
        f"?granularity={gran_map.get(granularity, 60)}"
    )
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            # Coinbase returns newest first; we want oldest first
            data.reverse()
            candles = []
            for c in data:
                # [start, low, high, open, close, volume]
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
                return candles
    except Exception:
        pass

    # fallback mock
    mock = []
    price = LAST_PRICES.get(symbol, 100.0)
    for i in range(80):
        price = price * (1 + (0.002 if i % 5 else -0.002))
        mock.append(
            {
                "t": int(time.time()) - (80 - i) * 60,
                "o": price * 0.997,
                "h": price * 1.004,
                "l": price * 0.994,
                "c": price,
            }
        )
    return mock


def compute_equity_from_prices(prices: dict) -> None:
    """
    Recompute paper equity using current prices.
    """
    total = PAPER_STATE["cash"]
    for sym, pos in PAPER_STATE["positions"].items():
        px = prices.get(sym, 0.0)
        total += pos["amount"] * px
    PAPER_STATE["pnl"] = total - 1000.0
    PAPER_STATE["last_equity"] = total


# --------------------------------------------------------------------------------------
# ENDPOINTS
# --------------------------------------------------------------------------------------
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
    return {"prices": LAST_PRICES, "updated": time.strftime("%I:%M:%S %p")}


@app.get("/history/{symbol}")
async def get_history(symbol: str, tf: str = "1D"):
    candles = await fetch_candles(symbol, tf)
    return {"symbol": symbol, "tf": tf, "candles": candles}


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
    # make sure equity is fresh
    compute_equity_from_prices(LAST_PRICES)
    return {
        "cash": round(PAPER_STATE["cash"], 2),
        "equity": round(PAPER_STATE["last_equity"], 2),
        "pnl": round(PAPER_STATE["pnl"], 2),
        "positions": PAPER_STATE["positions"],
        "trades": PAPER_STATE["trades"][-20:],  # last 20
        "started_with": 1000.0,
    }


@app.get("/live-stats")
async def live_stats():
    # right now we don't hit Coinbase for real — keeps you safe
    return {
        "connected": True,
        "executed_trades": 0,
        "note": "live trading stub — safe mode",
    }


@app.post("/run-bot")
async def run_bot():
    """
    Called by the frontend every ~25s.
    If mode == paper -> do tiny buys/sells, but ONLY if the move is big enough
    so that fees would not eat it (we assume ~0.5% round trip -> we want >=1.0% move).
    """
    if AUTO_MODE != "paper":
        return {"status": "idle", "mode": AUTO_MODE}

    # need prices
    if not LAST_PRICES:
        return {"status": "no-prices"}

    # super dumb strategy: look for the coin that dropped the most from its max in this batch
    # and buy a tiny bit; if something popped >1.2%, sell a tiny bit
    biggest_drop_sym = None
    biggest_drop_pct = 0
    biggest_rip_sym = None
    biggest_rip_pct = 0

    # turn prices into list to compare
    prices_items = list(LAST_PRICES.items())
    if len(prices_items) < 2:
        return {"status": "not-enough-prices"}

    avg_price = sum(p for _, p in prices_items) / len(prices_items)

    for sym, price in prices_items:
        # compare to avg, just to get a % move
        pct = (price - avg_price) / avg_price * 100
        if pct < biggest_drop_pct:
            biggest_drop_pct = pct
            biggest_drop_sym = sym
        if pct > biggest_rip_pct:
            biggest_rip_pct = pct
            biggest_rip_sym = sym

    did_something = False

    # BUY case: dropped more than 1.0% -> buy small
    if biggest_drop_sym and biggest_drop_pct < -1.0:
        px = LAST_PRICES[biggest_drop_sym]
        dollars = min(PAPER_TRADE_DOLLARS, PAPER_STATE["cash"])
        if dollars > 5:  # don't buy dust
            amount = dollars / px
            pos = PAPER_STATE["positions"].get(biggest_drop_sym, {"amount": 0.0, "avg_price": px})
            new_amount = pos["amount"] + amount
            # new avg price
            new_avg = ((pos["amount"] * pos["avg_price"]) + dollars) / new_amount
            PAPER_STATE["positions"][biggest_drop_sym] = {
                "amount": new_amount,
                "avg_price": new_avg,
            }
            PAPER_STATE["cash"] -= dollars
            PAPER_STATE["trades"].append(
                {
                    "side": "BUY",
                    "symbol": biggest_drop_sym,
                    "price": px,
                    "size_usd": dollars,
                    "ts": time.time(),
                }
            )
            did_something = True

    # SELL case: ripped more than 1.2% -> sell small
    if biggest_rip_sym and biggest_rip_pct > 1.2:
        px = LAST_PRICES[biggest_rip_sym]
        pos = PAPER_STATE["positions"].get(biggest_rip_sym)
        if pos and pos["amount"] * px > 5:  # have at least $5 to dump
            # sell 25% of position
            sell_value = min(PAPER_TRADE_DOLLARS, pos["amount"] * px * 0.25)
            sell_amount = sell_value / px
            pos["amount"] -= sell_amount
            if pos["amount"] <= 1e-8:
                del PAPER_STATE["positions"][biggest_rip_sym]
            PAPER_STATE["cash"] += sell_value
            PAPER_STATE["trades"].append(
                {
                    "side": "SELL",
                    "symbol": biggest_rip_sym,
                    "price": px,
                    "size_usd": sell_value,
                    "ts": time.time(),
                }
            )
            did_something = True

    # recompute equity after maybe trading
    compute_equity_from_prices(LAST_PRICES)

    return {
        "status": "ok",
        "did_trade": did_something,
        "paper": {
            "cash": round(PAPER_STATE["cash"], 2),
            "equity": round(PAPER_STATE["last_equity"], 2),
            "pnl": round(PAPER_STATE["pnl"], 2),
        },
    }
