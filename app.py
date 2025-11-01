from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
import requests
from datetime import datetime, timedelta, timezone

app = FastAPI()

# coins to show in the list
COINS = ["BTC", "ETH", "SOL", "ADA", "DOGE", "AVAX", "LTC", "DOT", "BCH", "LINK"]


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/prices")
def prices():
    out = {}
    for coin in COINS:
        pair = f"{coin}-USD"
        try:
            r = requests.get(f"https://api.coinbase.com/v2/prices/{pair}/spot", timeout=5)
            data = r.json()
            if "data" in data and "amount" in data["data"]:
                out[pair] = float(data["data"]["amount"])
        except Exception:
            continue
    if not out:
        return JSONResponse({"error": "could not fetch prices"}, status_code=500)
    return out


def _get_time_range(range_name: str):
    now = datetime.now(timezone.utc)
    if range_name == "24h":
        start = now - timedelta(hours=24)
        gran = 3600  # 1h
    elif range_name == "1m":
        start = now - timedelta(days=30)
        gran = 86400  # 1d
    elif range_name == "6m":
        start = now - timedelta(days=180)
        gran = 604800  # 1w
    elif range_name == "1y":
        start = now - timedelta(days=365)
        gran = 604800  # 1w
    else:
        start = now - timedelta(hours=24)
        gran = 3600
    return now, start, gran


def fetch_brokerage_candles(product_id: str, start_iso: str, end_iso: str, granularity: int):
    """
    First attempt: new/advanced Coinbase endpoint.
    Often returns [] on free hosting, so we still need fallback.
    """
    url = (
        f"https://api.coinbase.com/api/v3/brokerage/products/"
        f"{product_id}/candles?start={start_iso}&end={end_iso}&granularity={granularity}"
    )
    r = requests.get(url, timeout=5)
    if r.status_code != 200:
        return []
    data = r.json()
    candles = data.get("candles", [])
    if not candles:
        return []
    # normalize and sort oldest -> newest
    out = [
        {
            "time": c["start"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        }
        for c in candles
    ]
    return sorted(out, key=lambda x: x["time"])


def fetch_exchange_candles(product_id: str, start_iso: str, end_iso: str, granularity: int):
    """
    Fallback: old/public Coinbase Exchange API.
    It returns: [ time, low, high, open, close, volume ]
    """
    # convert ISO -> unix
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    url = (
        f"https://api.exchange.coinbase.com/products/{product_id}/candles"
        f"?granularity={granularity}&start={start_ts}&end={end_ts}"
    )
    r = requests.get(url, timeout=5)
    if r.status_code != 200:
        return []
    rows = r.json()
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        # [ time, low, high, open, close, volume ]
        ts, low, high, open_, close, _vol = row
        # convert to ISO for frontend
        t_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        out.append(
            {
                "time": t_iso,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
            }
        )
    # exchange returns newest first → sort
    return sorted(out, key=lambda x: x["time"])


def get_candles_for(product_id: str, range_name: str = "24h"):
    """
    Shared helper used by /candles AND /signals.
    Tries brokerage → fallback to exchange.
    """
    now, start, granularity = _get_time_range(range_name)
    start_iso = start.isoformat().replace("+00:00", "Z")
    end_iso = now.isoformat().replace("+00:00", "Z")

    # 1) try brokerage
    data = fetch_brokerage_candles(product_id, start_iso, end_iso, granularity)
    if data:
        return data

    # 2) fallback → exchange
    data = fetch_exchange_candles(product_id, start_iso, end_iso, granularity)
    return data


@app.get("/candles/{product_id}")
def candles(product_id: str, range: str = Query("24h")):
    data = get_candles_for(product_id, range)
    return {"product_id": product_id, "range": range, "candles": data}


@app.get("/signals")
def signals():
    """
    Signals that actually look at candles.
    BUY  = uptrend + momentum up + vol ok
    SELL = downtrend + momentum down + vol ok
    AVOID = vol trash
    HOLD = everything else
    """
    prices_res = prices()
    if isinstance(prices_res, JSONResponse):
        return prices_res

    result = {}

    for pair, spot_price in prices_res.items():
        candles = get_candles_for(pair, "24h")

        # not enough data → no risky moves
        if len(candles) < 5:
            result[pair] = {
                "signal": "HOLD",
                "confidence": 0.2,
                "reason": "not enough candles"
            }
            continue

        closes = [c["close"] for c in candles]

        # tiny SMA helper
        def sma(data, n):
            if len(data) < n:
                return sum(data) / len(data)
            return sum(data[-n:]) / n

        fast = sma(closes, 3)
        slow = sma(closes, 8)

        last_close = closes[-1]
        prev_close = closes[-2]

        momentum_up = last_close > prev_close
        momentum_down = last_close < prev_close

        # volatility
        ranges = [(c["high"] - c["low"]) for c in candles[-6:]]
        avg_range = sum(ranges) / len(ranges)
        atr_pct = (avg_range / last_close) * 100
        volatility_ok = 0.2 <= atr_pct <= 5

        # too crazy or too dead
        if not volatility_ok:
            result[pair] = {
                "signal": "AVOID",
                "confidence": 0.4,
                "reason": f"volatility {atr_pct:.2f}% out of range"
            }
            continue

        # ✅ BUY
        if fast > slow and momentum_up:
            stop = round(last_close * 0.985, 2)
            take = round(last_close * 1.015, 2)
            result[pair] = {
                "signal": "BUY",
                "confidence": 0.82,
                "reason": "uptrend + momentum up",
                "entry": last_close,
                "stop_loss": stop,
                "take_profit": take
            }
            continue

        # ✅ SELL
        if fast < slow and momentum_down:
            stop = round(last_close * 1.015, 2)  # above
            take = round(last_close * 0.985, 2)  # below
            result[pair] = {
                "signal": "SELL",
                "confidence": 0.8,
                "reason": "downtrend + momentum down",
                "entry": last_close,
                "stop_loss": stop,
                "take_profit": take
            }
            continue

        # default
        result[pair] = {
            "signal": "HOLD",
            "confidence": 0.5,
            "reason": "trend/momentum not aligned"
        }

    return result
