"""
MEXC Futures API - To'liq debug test
python test_api.py
"""
import asyncio, os, json, hmac, hashlib, time
import aiohttp
from dotenv import load_dotenv
load_dotenv()

API_KEY    = os.getenv("MEXC_API_KEY", "").strip()
SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "").strip()
BASE       = "https://contract.mexc.com"

def make_sign(ts, params_str):
    raw = API_KEY + ts + params_str
    return hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()

def make_headers(params_str=""):
    ts  = str(int(time.time() * 1000))
    sig = make_sign(ts, params_str)
    return {
        "ApiKey":       API_KEY,
        "Request-Time": ts,
        "Signature":    sig,
        "Content-Type": "application/json",
    }

async def test():
    async with aiohttp.ClientSession() as s:
        print("="*60)
        print(f"API_KEY: {API_KEY[:8]}..." if API_KEY else "❌ API_KEY YO'Q!")
        print(f"SECRET:  {SECRET_KEY[:8]}..." if SECRET_KEY else "❌ SECRET YO'Q!")
        print("="*60)

        # 1. Balans tekshiruvi
        print("\n1. GET balance...")
        h = make_headers("")
        async with s.get(f"{BASE}/api/v1/private/account/assets", headers=h) as r:
            text = await r.text()
            print(f"   HTTP {r.status}: {text[:300]}")

        # 2. Order submit - asosiy test
        print("\n2. POST order/submit (BTC_USDT LONG 1 lot)...")
        body     = {"symbol":"BTC_USDT","price":"0","vol":1,"leverage":3,"side":1,"type":5,"openType":1}
        body_str = json.dumps(body, separators=(',',':'))
        h2 = make_headers(body_str)
        print(f"   Yuborilayotgan: {body_str}")
        async with s.post(f"{BASE}/api/v1/private/order/submit",
                          headers=h2, data=body_str.encode("utf-8")) as r:
            text = await r.text()
            print(f"   HTTP {r.status}: '{text[:500]}'")
            print(f"   Content-Type: {r.headers.get('content-type','?')}")

        # 3. Open positions
        print("\n3. GET open_positions...")
        h3 = make_headers("")
        async with s.get(f"{BASE}/api/v1/private/position/open_positions", headers=h3) as r:
            text = await r.text()
            print(f"   HTTP {r.status}: {text[:300]}")

    print("\nTest yakunlandi!")

asyncio.run(test())
