from fastapi import FastAPI
from fastapi.responses import FileResponse
import requests

app = FastAPI()

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/prices")
def prices():
    # Add as many coins as you want — just use their Coinbase ticker symbols
    coins = ["BTC", "ETH", "SOL", "ADA", "DOGE", "BNB", "XRP", "AVAX", "LTC", "DOT"]
    prices = {}
    for coin in coins:
        try:
            r = requests.get(f"https://api.coinbase.com/v2/prices/{coin}-USD/spot").json()
            prices[f"{coin}-USD"] = r["data"]["amount"]
        except Exception:
            prices[f"{coin}-USD"] = "N/A"
    return prices
