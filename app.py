from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from datetime import datetime, timedelta
import json
import urllib.request
import urllib.error

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent

# the coins we want to show
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

# fallback prices if Coinbase won’t answer
FALLBACK_PRICES = {
    "BTC-USD": 109_785.923,
    "ETH-USD": 3_828.794,
    "BCH-USD": 553.041,
    "SOL-USD": 190.341,
    "LTC-USD": 98.375,
    "AVAX-USD": 18.504,
    "LINK-USD": 17.208,
    "DOT-USD": 2.961,
    "ADA-USD": 0.617,
    "DOGE-USD": 0.188,
}


def get_json(url: str):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8")), True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None, False


@app.get("/")
def root():
    index_file = BASE_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "info": "index.html not found"}


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


@app.get("/prices")
def prices():
    """
    Get current spot prices from Coinbase.
    Public endpoint: https://api.coinbase.com/v2/prices/BTC-USD/spot
    """
    result = {}
    from_live = True

    for sym in COINS:
        url = f"https://api.coinbase.com/v2/prices/{sym}/spot"
        data, ok = get_json(url)
        if ok and data and "data" in data:
            try:
                price = float(data["data"]["amount"])
            except (ValueError, KeyError):
                price = FALLBACK_PRICES.get(sym, 100.0)
                from_live = False
        else:
            price = FALLBACK_PRICES.get(sym, 100.0)
            from_live = False
        result[sym] = price

    return JSONResponse({"prices": result, "live": from_live})


@app.get("/history")
def history(
    symbol: str = Query("BTC-USD"),
    span: str = Query("24h"),  # 24h, 1M, 6M, 1Y
    mode: str = Query("line"),  # line, candles
):
    """
    Real Coinbase candles:
      GET https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600
    returns: [ time, low, high, open, close, volume ]
    We'll map span -> granularity
    """
    # map spans to granularity
    if span == "24h":
        gran = 900        # 15m
    elif span == "1M":
        gran = 21600      # 6h
    elif span == "6M":
        gran = 86400      # 1d
    else:  # 1Y
        gran = 604800     # 1w

    url = f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity={gran}"
    data, ok = get_json(url)

    # Coinbase returns newest first
    if ok and isinstance(data, list) and len(data) > 0:
        data.reverse()  # oldest first
        if mode == "candles":
            candles = []
            for c in data:
                # c: [ time, low, high, open, close, volume ]
                candles.append(
                    {
                        "t": datetime.utcfromtimestamp(c[0]).isoformat() + "Z",
                        "o": c[3],
                        "h": c[2],
                        "l": c[1],
                        "c": c[4],
                    }
                )
            return {
                "symbol": symbol,
                "span": span,
                "mode": mode,
                "source": "live",
                "candles": candles,
            }
        else:
            points = []
            for c in data:
                points.append(
                    {
                        "t": datetime.utcfromtimestamp(c[0]).isoformat() + "Z",
                        "p": c[4],  # close
                    }
                )
            return {
                "symbol": symbol,
                "span": span,
                "mode": mode,
                "source": "live",
                "points": points,
            }

    # ---------- FALLBACK (what you were seeing) ----------
    # if we’re here, Coinbase said no – we make a wave
    now = datetime.utcnow()
    points = []
    for i in range(60):
        t = now - timedelta(minutes=(60 - i) * 10)
        base = FALLBACK_PRICES.get(symbol, 100.0)
        wobble = base * 0.012 * __import__("math").sin(i / 6)
        points.append({"t": t.isoformat() + "Z", "p": round(base + wobble, 4)})

    if mode == "candles":
        candles = []
        for p in points:
            price = p["p"]
            candles.append(
                {
                    "t": p["t"],
                    "o": price * 0.999,
                    "h": price * 1.004,
                    "l": price * 0.996,
                    "c": price * 1.001,
                }
            )
        return {
            "symbol": symbol,
            "span": span,
            "mode": mode,
            "source": "mock",
            "candles": candles,
        }

    return {
        "symbol": symbol,
        "span": span,
        "mode": mode,
        "source": "mock",
        "points": points,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
