from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/prices")
def prices():
    # mock data for now
    return {"ETH-USD": 3800.15, "BTC-USD": 66800.23}
