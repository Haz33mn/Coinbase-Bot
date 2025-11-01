from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import random

app = FastAPI()

# coins we show in the UI
MOCK_PRICES = {
    "BTC-USD": 110_245.315,
    "ETH-USD": 3_876.465,
    "SOL-USD": 186.005,
    "ADA-USD": 0.613,
    "DOGE-USD": 0.187,
    "AVAX-USD": 18.635,
    "LTC-USD": 99.64,
    "DOT-USD": 2.948,
    "BCH-USD": 554.105,
    "LINK-USD": 17.229,
}

@app.get("/health")
def health():
    return {"status": "ok"}

# serve the UI
@app.get("/", response_class=HTMLResponse)
def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# frontend calls this first
@app.get("/prices")
def get_prices():
    return MOCK_PRICES

# frontend uses this to sort “top 10 best performing”
@app.get("/signals")
def get_signals():
    data = {}
    for pair in MOCK_PRICES.keys():
        # make it look real-ish
        r = random.random()
        if r < 0.25:
            signal = "BUY"
            conf = 0.82
        elif r < 0.6:
            signal = "HOLD"
            conf = 0.50
        else:
            signal = "SELL"
            conf = 0.80

        # keep BTC/LTC neutral
        if pair in ("BTC-USD", "LTC-USD"):
            signal = "HOLD"
            conf = 0.50

        data[pair] = {
            "signal": signal,
            "confidence": conf,
        }
    return data

# this was the one failing
@app.get("/candles/{pair}")
def get_candles(pair: str, range: str = "24h"):
    # super simple fake series – the UI just needs this shape
    base = float(MOCK_PRICES.get(pair, 100.0))
    # how many points we want on the chart
    if range == "24h":
        n = 50
    elif range == "1m":
        n = 60
    elif range == "6m":
        n = 70
    else:
        n = 80

    candles = []
    price = base
    for i in range(n):
        # random walk
        step = base * 0.004
        price_change = random.uniform(-step, step)
        open_ = price
        close = max(0.0001, price + price_change)
        high = max(open_, close) + random.uniform(0, step * 0.4)
        low = min(open_, close) - random.uniform(0, step * 0.4)
        price = close

        candles.append({
            "open": round(open_, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
            # frontend doesn't care about exact time, just needs *something*
            "timestamp": i,
        })

    return {
        "pair": pair,
        "range": range,
        "candles": candles,
    }
