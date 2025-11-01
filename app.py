from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import datetime
import math
import random

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# base coins we'll show
BASE_COINS = [
    {"symbol": "BTC-USD", "price": 109_785.923, "change_24h": -0.7},
    {"symbol": "ETH-USD", "price": 3_828.794, "change_24h": 0.3},
    {"symbol": "BCH-USD", "price": 553.041, "change_24h": 0.9},
    {"symbol": "SOL-USD", "price": 186.408, "change_24h": -2.4},
    {"symbol": "LTC-USD", "price": 98.375, "change_24h": 1.9},
    {"symbol": "AVAX-USD", "price": 18.504, "change_24h": -3.1},
    {"symbol": "LINK-USD", "price": 17.208, "change_24h": 2.2},
    {"symbol": "DOT-USD", "price": 2.961, "change_24h": -1.2},
    {"symbol": "ADA-USD", "price": 0.617, "change_24h": 0.1},
    {"symbol": "DOGE-USD", "price": 0.188, "change_24h": -0.5},
]


def classify_signal(pct_change: float):
    """
    low-sensitivity signals so it doesn't flip every few cents
    """
    if pct_change <= -3:
        return "BUY", 82
    if pct_change <= -1.5:
        return "BUY", 68
    if pct_change < 1.5:
        return "HOLD", 50
    if pct_change < 3:
        return "SELL", 68
    return "SELL", 82


@app.get("/")
def root():
    return FileResponse("index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/prices")
def prices():
    coins_out = []
    for c in BASE_COINS:
        # tiny wiggle so UI looks alive
        wiggle = random.uniform(-0.08, 0.08)
        pct = c["change_24h"] + wiggle
        signal, conf = classify_signal(pct)
        coins_out.append(
            {
                "symbol": c["symbol"],
                "price": round(c["price"], 6),
                "change_24h": round(pct, 3),
                "signal": signal,
                "confidence": conf,
            }
        )

    # sort by best performing
    coins_out.sort(key=lambda x: x["change_24h"], reverse=True)

    return {
        "updated": datetime.datetime.utcnow().isoformat() + "Z",
        "coins": coins_out,
    }


@app.get("/history")
def history(symbol: str, range: str = "24h", mode: str = "line"):
    """
    generate nice OHLC points for the frontend
    """
    coin = next((c for c in BASE_COINS if c["symbol"] == symbol), None)
    if coin is None:
        return JSONResponse({"error": "symbol not found"}, status_code=404)

    base_price = coin["price"]

    if range == "24h":
        n = 60
    elif range == "1M":
        n = 90
    elif range == "6M":
        n = 120
    else:  # 1Y
        n = 140

    points = []
    for i in range(n):
        wave = math.sin(i / 6) * (base_price * 0.015)  # ±1.5%
        center = base_price + wave
        candle_height = center * 0.004  # 0.4%

        open_p = center - candle_height * 0.25
        close_p = center + candle_height * 0.25
        high_p = center + candle_height
        low_p = center - candle_height

        points.append(
            {
                "t": i,
                "o": round(open_p, 6),
                "h": round(high_p, 6),
                "l": round(low_p, 6),
                "c": round(close_p, 6),
            }
        )

    return {
        "symbol": symbol,
        "range": range,
        "mode": mode,
        "points": points,
    }
