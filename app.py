from fastapi import FastAPI
import os
import requests

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/prices")
def prices():
    # mock data for now
    return {"ETH-USD": 3800.15, "BTC-USD": 66800.23}

@app.get("/check")
def check():
    # 1) check env vars
    key_id = os.getenv("COINBASE_API_KEY_ID")
    private_key = os.getenv("COINBASE_PRIVATE_KEY")

    env_ok = bool(key_id and private_key)

    # 2) ping a public Coinbase endpoint (no auth)
    try:
        r = requests.get("https://api.coinbase.com/api/v3/brokerage/products", timeout=5)
        coinbase_ok = r.status_code == 200
    except Exception as e:
        coinbase_ok = False

    return {
        "env_vars_present": env_ok,
        "coinbase_reachable": coinbase_ok,
        "details": {
            "has_key_id": bool(key_id),
            "has_private_key": bool(private_key)
        }
    }
