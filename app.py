from fastapi import FastAPI
from fastapi.responses import FileResponse
import requests

app = FastAPI()

COINBASE_PRODUCTS_URL = "https://api.coinbase.com/api/v3/brokerage/products"

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/coins")
def coins():
    # get all tradable spot products from Coinbase
    r = requests.get(COINBASE_PRODUCTS_URL, timeout=5).json()
    out = []
    for p in r.get("products", []):
        # only show USD / USDC markets so it’s not 10,000 lines
        if p.get("quote_currency_id") in ("USD", "USDC"):
            out.append({
                "product_id": p["product_id"],     # e.g. "TAO-USD"
                "base": p["base_name"],            # e.g. "Bittensor"
                "symbol": p["base_currency_id"],   # e.g. "TAO"
            })
    return out

@app.get("/price/{product_id}")
def price(product_id: str):
    # single-coin price lookup
    r = requests.get(f"https://api.coinbase.com/v2/prices/{product_id}/spot").json()
    return {"product_id": product_id, "price": r["data"]["amount"]}
