"""
MEXC API Debug Test - python test_api.py
"""
import asyncio, os, json, hmac, hashlib, time
import aiohttp
from dotenv import load_dotenv
load_dotenv()

API_KEY    = os.getenv("MEXC_API_KEY", "").strip()
SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "").strip()
BASE       = "https://contract.mexc.com"

def make_headers(params_str=""):
    ts  = str(int(time.time() * 1000))
    raw = API_KEY + ts + params_str
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return {"ApiKey": API_KEY, "Request-Time": ts, "Signature": sig, "Content-Type": "application/json"}

async def test():
    print("="*55)
    print(f"API_KEY : {API_KEY[:10]}..." if API_KEY else "❌ API_KEY YOQ!")
    print(f"SECRET  : {SECRET_KEY[:10]}..." if SECRET_KEY else "❌ SECRET YOQ!")
    print("="*55)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:

        # 1. Balans
        print("\n[1] Balans...")
        h = make_headers("")
        async with s.get(f"{BASE}/api/v1/private/account/assets", headers=h) as r:
            t = await r.text()
            print(f"    HTTP {r.status} | {t[:200]}")

        # 2. Order submit - haqiqiy test
        print("\n[2] Order submit test (BTC_USDT LONG 1)...")
        body = {"symbol":"BTC_USDT","price":"0","vol":1,"leverage":3,"side":1,"type":5,"openType":1}
        bs   = json.dumps(body, separators=(',',':'))
        h2   = make_headers(bs)
        async with s.post(f"{BASE}/api/v1/private/order/submit",
                          headers=h2, data=bs.encode()) as r:
            t = await r.text()
            print(f"    HTTP {r.status}")
            print(f"    RAW JAVOB: '{t[:400]}'")

asyncio.run(test())
