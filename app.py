from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime, timedelta
import random
import os

app = FastAPI()

# pretend data – same pairs you see in the UI
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

# serve index.html (your UI)
@app.get("/", response_class=HTMLResponse)
def root():
    # render/index.html is in same folder
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# 1) prices: UI hits /prices first
@app.get("/prices")
def get_prices():
    # in real life you'd call Coinbase here
    return MOCK_PRICES

# 2) signals: UI sorts these to “Top 10 best performing”
@app.get("/signals")
def get_signals():
    signals = {}
    for pair, price in MOCK_PRICES.items():
        # dumb scoring so UI has something to sort
        # make bigger coins more "confident"
        base_conf = 0.5
        if pair == "BTC-USD":
            signal = "HOLD"
            confidence = 0.5
        elif pair == "ETH-USD":
            signal = "SELL"
            confidence = 0.8
        else:
            # random-ish but stable enough
            roll = random.random()
            if roll < 0.35:
                signal = "BUY"
                confidence = 0.82
            elif roll < 0.6:
                signal = "HOLD"
                confidence = 0.5
            else:
                signal = "SELL"
                confidence = 0.8
        signals[pair] = {
            "signal": signal,
            "confidence": confidence if confidence else base_conf,
        }
    return signals

# 3) candles: UI hits /candles/{pair}?range=24h etc.
@app.get("/candles/{pair}")
def get_candles(pair: str, range: str = "24h"):
    # we don't have real Coinbase candles on free Render, so fake it
    # frontend only needs the shape: [{open,high,low,close,timestamp}, ...]
    price = float(MOCK_PRICES.get(pair, 100.0))

    if range == "24h":
        count = 48          # 30-min candles
        step = timedelta(minutes=30)
        vol = 0.012
    elif range == "1m":
        count = 30          # daily candles
        step = timedelta(days=1)
        vol = 0.03
    elif range == "6m":
        count = 26          # weekly
        step = timedelta(weeks=1)
        vol = 0.055
    else:   # 1y
        count = 52
        step = timedelta(weeks=1)
        vol = 0.08

    candles = []
    now = datetime.utcnow()
    cur = price

    for i in range(count):
        # move price a little
        change = (random.random() - 0.5) * 2 * vol * price
        open_ = cur
        close = max(0.0001, cur + change)
        high = max(open_, close) + random.random() * vol * price * 0.4
        low = min(open_, close) - random.random() * vol * price * 0.4
        ts = (now - step * (count - i)).isoformat() + "Z"
        candles.append({
            "open": round(open_, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
            "timestamp": ts,
        })
        cur = close

    return {"pair": pair, "range": range, "candles": candles}
