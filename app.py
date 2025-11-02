import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# CORS so the browser can call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve /static
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# in-memory toggle
TRADE_STATE = {
    "mode": "off",     # "off" | "paper" | "live"
    "last_decision": None,
}


@app.get("/")
async def index():
    # serve static/index.html
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


# coins we show in the left list
COINS = [
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


async def _fetch_ticker(client: httpx.AsyncClient, product_id: str):
    # Coinbase Exchange public endpoint
    url = f"https://api.exchange.coinbase.com/products/{product_id}/ticker"
    try:
        r = await client.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            price = float(data["price"])
            return {"product_id": product_id, "price": price}
    except Exception:
        pass
    return {"product_id": product_id, "price": 0.0}


@app.get("/api/prices")
async def get_prices():
    # returns:
    # { "coins": [ { "id": "BTC-USD", "price": 110000, "signal": "HOLD", "confidence": 50 }, ... ] }
    out = []
    async with httpx.AsyncClient() as client:
        for pid in COINS:
            ticker = await _fetch_ticker(client, pid)
            price = ticker["price"]
            # a super-simple signal: we DON'T want it to flip on penny moves
            # so we just say HOLD unless price is 2% above/below a fake moving avg
            # (front-end only needs the shape)
            signal = "HOLD"
            confidence = 50
            if price > 0:
                # fake reference price so it's stable
                ref = price * 0.995
                diff = (price - ref) / ref
                if diff > 0.02:
                    signal = "SELL"
                    confidence = 68
                elif diff < -0.02:
                    signal = "BUY"
                    confidence = 82

            out.append(
                {
                    "id": pid,
                    "price": price,
                    "signal": signal,
                    "confidence": confidence,
                }
            )
    return {"coins": out}


# map UI timeframes -> coinbase params
TIMEFRAMES = {
    "1d": {"granularity": 300, "delta": timedelta(days=1)},     # 5m
    "1w": {"granularity": 900, "delta": timedelta(days=7)},     # 15m
    "1m": {"granularity": 3600, "delta": timedelta(days=30)},   # 1h
    "6m": {"granularity": 21600, "delta": timedelta(days=182)}, # 6h
    "1y": {"granularity": 86400, "delta": timedelta(days=365)}, # 1d
}


@app.get("/api/history/{product_id}")
async def get_history(product_id: str, tf: str = "1d"):
    tf = tf.lower()
    if tf not in TIMEFRAMES:
        tf = "1d"
    tf_cfg = TIMEFRAMES[tf]
    end = datetime.now(timezone.utc)
    start = end - tf_cfg["delta"]
    params = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granularity": tf_cfg["granularity"],
    }
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, timeout=6)
            if r.status_code == 200:
                data = r.json()
                # coinbase returns newest first -> reverse
                data.reverse()
                # each item: [time, low, high, open, close, volume]
                candles = [
                    {
                        "t": c[0],
                        "low": c[1],
                        "high": c[2],
                        "open": c[3],
                        "close": c[4],
                        "volume": c[5],
                    }
                    for c in data
                ]
                return {"ok": True, "candles": candles}
    except Exception:
        pass

    # fallback if api fails
    fallback = []
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for i in range(60):
        fallback.append(
            {
                "t": now_ts - (60 - i) * 300,
                "low": 100,
                "high": 110,
                "open": 102,
                "close": 108,
                "volume": 1,
            }
        )
    return {"ok": False, "candles": fallback}


@app.get("/api/trade-mode")
async def get_trade_mode():
    return {"mode": TRADE_STATE["mode"]}


@app.post("/api/trade-mode")
async def set_trade_mode(payload: dict = Body(...)):
    mode = payload.get("mode", "off")
    if mode not in ("off", "paper", "live"):
        mode = "off"
    TRADE_STATE["mode"] = mode
    return {"mode": mode}
