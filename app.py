import os, time, math, httpx
from typing import List, Dict, Any, Optional
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

STATE = {
    "auto_enabled": False,
    "paper_mode": True,
    "last_trade": None,
    "paper_balance": 1000.0
}

COINS = ["BTC-USD","ETH-USD","SOL-USD","ADA-USD","AVAX-USD","DOT-USD","BCH-USD","LTC-USD","LINK-USD","DOGE-USD"]
FEE = 0.006

async def get_price(symbol:str)->Optional[float]:
    url=f"https://api.coinbase.com/v2/prices/{symbol}/spot"
    async with httpx.AsyncClient(timeout=8.0) as c:
        r=await c.get(url)
    if r.status_code!=200:return None
    return float(r.json()["data"]["amount"])

async def get_candles(symbol:str,range:str)->List[List[float]]:
    gran={"1D":900,"1W":3600,"1M":21600,"6M":86400,"1Y":86400}.get(range.upper(),900)
    url=f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity={gran}"
    async with httpx.AsyncClient(timeout=8.0) as c:
        r=await c.get(url)
    if r.status_code!=200:return []
    data=r.json();data.sort(key=lambda x:x[0]);return data

def signal(candles:List[List[float]])->Dict[str,Any]:
    if len(candles)<3:return{"signal":"HOLD"}
    closes=[c[4]for c in candles];p=closes[-1];prev=closes[-2]
    pct=(p-prev)/prev
    if pct>FEE+0.003:return{"signal":"BUY"}
    if pct<-(FEE+0.003):return{"signal":"SELL"}
    return{"signal":"HOLD"}

@app.get("/")
async def root():return FileResponse("index.html")

@app.get("/api/prices")
async def prices():
    data={}
    for c in COINS:
        p=await get_price(c)
        if p:data[c]=p
    return{"prices":data,"state":STATE}

@app.get("/api/history/{symbol}")
async def history(symbol:str,range:str="1D"):
    c=await get_candles(symbol,range)
    return{"ok":bool(c),"candles":c,"signal":signal(c),"state":STATE}

@app.post("/api/control")
async def control(payload:Dict[str,Any]):
    STATE["auto_enabled"]=payload.get("auto_enabled",STATE["auto_enabled"])
    STATE["paper_mode"]=payload.get("paper_mode",STATE["paper_mode"])
    return{"state":STATE}

@app.get("/api/status")
async def status():return{"state":STATE}
