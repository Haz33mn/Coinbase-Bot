from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import requests

app = FastAPI()

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/prices")
def prices():
    # only coins Coinbase's public /v2/prices endpoint actually has
    coins = ["BTC", "ETH", "SOL", "ADA", "DOGE", "AVAX", "LTC", "DOT", "BCH", "LINK"]
    out = {}

    for coin in coins:
        pair = f"{coin}-USD"
        try:
            r = requests.get(f"https://api.coinbase.com/v2/prices/{pair}/spot", timeout=5)
            data = r.json()
            if "data" in data and "amount" in data["data"]:
                out[pair] = data["data"]["amount"]
        except Exception:
            # skip bad ones
            continue

    if not out:
        return JSONResponse({"error": "could not fetch prices"}, status_code=500)

    return out
