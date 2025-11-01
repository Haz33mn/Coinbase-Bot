from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import requests

app = FastAPI()

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/prices")
def prices():
    try:
        r = requests.get("https://api.coinbase.com/v2/exchange-rates?currency=USD", timeout=5)
        data = r.json()["data"]["rates"]
        top = ["BTC", "ETH", "SOL", "ADA", "DOGE", "BNB", "XRP", "AVAX", "LTC", "DOT"]
        prices = {}
        for coin in top:
            if coin in data:
                prices[f"{coin}-USD"] = round(1 / float(data[coin]), 4)
        return prices
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
