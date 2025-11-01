from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from datetime import datetime, timedelta
import math

app = FastAPI()

# allow browser to hit our API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent


@app.get("/")
def root():
    index_file = BASE_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "info": "index.html not found"}


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


# this is what the UI uses to show the left list
@app.get("/prices")
def prices():
    # these are mock but stable – you can replace with real Coinbase later
    data = {
        "BTC-USD": 109785.923,
        "ETH-USD": 3828.794,
        "SOL-USD": 190.341,
        "ADA-USD": 0.617,
        "DOT-USD": 2.961,
        "AVAX-USD": 18.504,
        "LINK-USD": 17.208,
        "DOGE-USD": 0.188,
        "LTC-USD": 98.375,
        "BCH-USD": 553.041,
    }
    return data


# this is the one your chart kept failing on
@app.get("/history")
def history(
    symbol: str = "BTC-USD",
    span: str = "24h",   # 24h | 1M | 6M | 1Y
    mode: str = "line",  # line | candles
):
    """
    We return always-valid data so the chart can never be empty.
    No external calls. All generated right here.
    """
    now = datetime.utcnow()

    # how many points to return
    if span == "24h":
        points = 60
        step = timedelta(minutes=25)
    elif span == "1M":
        points = 90
        step = timedelta(hours=8)
    elif span == "6M":
        points = 120
        step = timedelta(days=2)
    else:  # 1Y
        points = 140
        step = timedelta(days=3)

    # baseline price – grab from the same numbers as /prices
    base_prices = {
        "BTC-USD": 109_785.923,
        "ETH-USD": 3_828.794,
        "SOL-USD": 190.341,
        "ADA-USD": 0.617,
        "DOT-USD": 2.961,
        "AVAX-USD": 18.504,
        "LINK-USD": 17.208,
        "DOGE-USD": 0.188,
        "LTC-USD": 98.375,
        "BCH-USD": 553.041,
    }
    base = base_prices.get(symbol, 100.0)

    # make it wobble a little so the chart looks real
    line_points = []
    candles = []
    for i in range(points):
        t = now - (points - i) * step
        # smooth-ish wave
        wave = math.sin(i / 7) * 0.012  # ~1.2%
        price = base * (1 + wave)
        line_points.append(
            {
                "t": t.isoformat() + "Z",
                "p": round(price, 4),
            }
        )

        if mode == "candles":
            high = price * 1.004
            low = price * 0.996
            o = price * 0.999
            c = price * 1.001
            candles.append(
                {
                    "t": t.isoformat() + "Z",
                    "o": round(o, 4),
                    "h": round(high, 4),
                    "l": round(low, 4),
                    "c": round(c, 4),
                }
            )

    payload = {
        "symbol": symbol,
        "span": span,
        "mode": mode,
        "generated_at": now.isoformat() + "Z",
    }

    if mode == "candles":
        payload["candles"] = candles
    else:
        payload["points"] = line_points

    return JSONResponse(payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
