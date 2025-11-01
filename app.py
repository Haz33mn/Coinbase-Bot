from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import random
import copy

app = FastAPI()

# base prices we start from
BASE_PRICES = {
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

# we keep a mutable copy so prices can move a LITTLE
CURRENT_PRICES = copy.deepcopy(BASE_PRICES)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def root():
    # serve the UI
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


def _tiny_tick():
    """move each price by <=0.25% so it looks alive but not crazy"""
    for pair, price in CURRENT_PRICES.items():
        # small random -0.25% .. +0.25%
        pct = random.uniform(-0.0025, 0.0025)
        CURRENT_PRICES[pair] = round(price * (1 + pct), 6)


@app.get("/prices")
def get_prices():
    # wiggle a bit every call
    _tiny_tick()
    return CURRENT_PRICES


@app.get("/signals")
def get_signals():
    """
    REAL FIX HERE:
    - if move < 1.5%  -> HOLD 50%
    - if move >= 1.5% -> BUY 82%
    - if move <= -1.5% -> SELL 80%
    this stops the “it changes on every penny” problem
    """
    signals = {}
    for pair, cur in CURRENT_PRICES.items():
        base = BASE_PRICES.get(pair, cur)
        if base <= 0:
            signals[pair] = {"signal": "HOLD", "confidence": 0.5}
            continue

        change_pct = (cur - base) / base * 100  # %
        abs_change = abs(change_pct)

        if abs_change < 1.5:      # < 1.5% → too small → HOLD
            sig = "HOLD"
            conf = 0.5
        elif change_pct >= 1.5:   # up more than 1.5% → BUY
            sig = "BUY"
            conf = 0.82
        else:                     # down more than 1.5% → SELL
            sig = "SELL"
            conf = 0.80

        # keep BTC a bit calmer
        if pair == "BTC-USD" and sig != "HOLD":
            sig = "HOLD"
            conf = 0.5

        signals[pair] = {"signal": sig, "confidence": conf}

    return signals


@app.get("/candles/{pair}")
def get_candles(pair: str, range: str = "24h"):
    """
    super-safe candles: ALWAYS return a list
    so frontend never shows 'failed'
    """
    base = float(CURRENT_PRICES.get(pair, 100.0))

    if range == "24h":
        n = 60
    elif range == "1m":
        n = 60
    elif range == "6m":
        n = 70
    else:
        n = 80

    candles = []
    price = base
    step_abs = base * 0.004

    for i in range(n):
        diff = random.uniform(-step_abs, step_abs)
        open_ = price
        close = max(0.0001, price + diff)
        high = max(open_, close) + random.uniform(0, step_abs * 0.3)
        low = min(open_, close) - random.uniform(0, step_abs * 0.3)
        price = close
        candles.append({
            "open": round(open_, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
            "timestamp": i
        })

    return {"pair": pair, "range": range, "candles": candles}
