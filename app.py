from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from datetime import datetime, timedelta, timezone
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


def http_json(url: str):
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
    except Exception:
        return None, False


@app.get("/")
def root():
    index_file = BASE_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


@app.get("/prices")
def prices():
    out = {}
    live = True
    for sym in COINS:
        url = f"https://api.coinbase.com/v2/prices/{sym}/spot"
        data, ok = http_json(url)
        if ok and data and "data" in data:
            try:
                price = float(data["data"]["amount"])
            except Exception:
                price = FALLBACK_PRICES.get(sym, 100.0)
                live = False
        else:
            price = FALLBACK_PRICES.get(sym, 100.0)
            live = False
        out[sym] = price
    return JSONResponse({"prices": out, "live": live})


@app.get("/history")
def history(
    symbol: str = Query("BTC-USD"),
    span: str = Query("1D"),  # 1D, 1W, 1M, 6M, 1Y
    mode: str = Query("line"),  # line or candles
):
    """
    We'll pull from Coinbase Exchange candles.
    We only have these granularities: 60, 300, 900, 3600, 21600, 86400.
    Strategy:
      - 1D  -> gran=900 (15m)   -> about 96 points
      - 1W  -> gran=3600 (1h)   -> about 168 points
      - 1M  -> gran=21600 (6h)
      - 6M  -> gran=86400 (1d)
      - 1Y  -> gran=86400 (1d)
    """
    span = span.upper()
    now = datetime.now(timezone.utc)

    if span == "1D":
        gran = 900
        start = now - timedelta(days=1)
    elif span == "1W":
        gran = 3600
        start = now - timedelta(days=7)
    elif span == "1M":
        gran = 21600
        start = now - timedelta(days=30)
    elif span == "6M":
        gran = 86400
        start = now - timedelta(days=180)
    else:  # 1Y
        gran = 86400
        start = now - timedelta(days=365)

    end_iso = now.isoformat()
    start_iso = start.isoformat()

    url = (
        f"https://api.exchange.coinbase.com/products/{symbol}/candles"
        f"?start={start_iso}&end={end_iso}&granularity={gran}"
    )
    data, ok = http_json(url)

    if ok and isinstance(data, list) and data:
        data.reverse()  # oldest first
        if mode == "candles":
            candles = []
            for c in data:
                candles.append(
                    {
                        "t": datetime.fromtimestamp(c[0], tz=timezone.utc).isoformat(),
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
            pts = []
            for c in data:
                pts.append(
                    {
                        "t": datetime.fromtimestamp(c[0], tz=timezone.utc).isoformat(),
                        "p": c[4],
                    }
                )
            return {
                "symbol": symbol,
                "span": span,
                "mode": mode,
                "source": "live",
                "points": pts,
            }

    # fallback wave
    base = FALLBACK_PRICES.get(symbol, 100.0)
    pts = []
    for i in range(80):
        t = (now - timedelta(minutes=(80 - i) * 15)).isoformat()
        wobble = base * 0.01 * __import__("math").sin(i / 4)
        pts.append({"t": t, "p": round(base + wobble, 4)})

    if mode == "candles":
        cnds = []
        for p in pts:
            price = p["p"]
            cnds.append(
                {
                    "t": p["t"],
                    "o": price * 0.999,
                    "h": price * 1.003,
                    "l": price * 0.997,
                    "c": price * 1.001,
                }
            )
        return {
            "symbol": symbol,
            "span": span,
            "mode": mode,
            "source": "mock",
            "candles": cnds,
        }

    return {
        "symbol": symbol,
        "span": span,
        "mode": mode,
        "source": "mock",
        "points": pts,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
