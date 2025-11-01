from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/prices")
def prices():
    # Use Coinbase’s free public price API
    try:
        btc = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot").json()
        eth = requests.get("https://api.coinbase.com/v2/prices/ETH-USD/spot").json()
        sol = requests.get("https://api.coinbase.com/v2/prices/SOL-USD/spot").json()
        return {
            "BTC-USD": btc["data"]["amount"],
            "ETH-USD": eth["data"]["amount"],
            "SOL-USD": sol["data"]["amount"]
        }
    except Exception as e:
        return {"error": str(e)}
