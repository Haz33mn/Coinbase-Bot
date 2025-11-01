@app.get("/check")
def check():
    key_id = os.getenv("COINBASE_API_KEY_ID")
    private_key = os.getenv("COINBASE_PRIVATE_KEY")

    env_ok = bool(key_id and private_key)

    coinbase_ok = False
    internet_ok = False

    # test general internet
    try:
        r = requests.get("https://httpbin.org/get", timeout=5)
        internet_ok = r.status_code == 200
    except Exception:
        internet_ok = False

    # test coinbase public
    try:
        r = requests.get("https://api.coinbase.com/api/v3/brokerage/products", timeout=5)
        coinbase_ok = r.status_code == 200
    except Exception:
        coinbase_ok = False

    return {
        "env_vars_present": env_ok,
        "internet_ok": internet_ok,
        "coinbase_reachable": coinbase_ok,
        "details": {
            "has_key_id": bool(key_id),
            "has_private_key": bool(private_key)
        }
    }
