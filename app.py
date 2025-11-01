from fastapi import FastAPI
from fastapi.responses import FileResponse
import requests
import os

app = FastAPI()

@app.get("/")
def home():
    # Serve the index.html file from the same folder
    return FileResponse("index.html")

@app.get("/prices")
def prices():
    # Free, live data from Coinbase public API
    coins = ["BTC", "ETH", "SOL"]
    prices = {}
    for coin in coins:
        r = requests.get(f"https://api.coinbase.com/v2/prices/{coin}-USD/spot").json()
        prices[f"{coin}-USD"] = r["data"]["amount"]
    return prices
