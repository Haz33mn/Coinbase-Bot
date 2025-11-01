from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/prices")
def prices():
    # Free, live data from Coinbase public API
    coins = ["BTC", "ETH", "SOL"]
    prices = {}
    for coin in coins:
        r = requests.get(f"https://api.coinbase.com/v2/prices/{coin}-USD/spot").json()
        prices[f"{coin}-USD"] = r["data"]["amount"]
    return prices
