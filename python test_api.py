"""
API test skripti - botni ishga tushirishdan oldin tekshiring
python test_api.py
"""
import asyncio
import os
from dotenv import load_dotenv
from mexc_futures import MEXCFutures

load_dotenv()

async def test():
    api = MEXCFutures(
        api_key=os.getenv("MEXC_API_KEY", ""),
        secret_key=os.getenv("MEXC_SECRET_KEY", ""),
    )

    print("=" * 50)
    print("MEXC Futures API Test")
    print("=" * 50)

    # 1. Balans
    print("\n1️⃣  Balans tekshiruvi...")
    balance = await api.get_balance()
    if balance > 0:
        print(f"   ✅ Balans: {balance:.4f} USDT")
    else:
        print(f"   ❌ Balans 0 — API kalitlari noto'g'ri yoki Futures hisob yo'q!")

    # 2. Ticker
    print("\n2️⃣  Ticker tekshiruvi (BTC_USDT)...")
    ticker = await api.get_ticker("BTC_USDT")
    if ticker:
        price = ticker.get("lastPrice", ticker.get("last", "?"))
        print(f"   ✅ BTC narxi: ${price}")
    else:
        print("   ❌ Ticker olishda xato")

    # 3. Klines
    print("\n3️⃣  Klines tekshiruvi (BTC_USDT)...")
    klines = await api.get_klines("BTC_USDT", "Min1", 10)
    if klines and len(klines) >= 5:
        print(f"   ✅ Klines: {len(klines)} ta sham | so'nggi close: {klines[-1].get('close')}")
    else:
        print(f"   ❌ Klines xato: {klines}")

    # 4. Leverage (write test)
    print("\n4️⃣  Leverage o'rnatish (BTC_USDT, 3x)...")
    lev = await api.set_leverage("BTC_USDT", 3)
    print(f"   {'✅' if lev else '⚠️'} Leverage: {'OK' if lev else 'Xato (lekin order ochilishi mumkin)'}")

    await api.close()
    print("\n" + "=" * 50)
    if balance > 0 and ticker and klines:
        print("✅ API to'g'ri ishlayapti! Bot ishga tushishga tayyor.")
    else:
        print("❌ Muammo bor — yuqoridagi xatolarni tekshiring.")
    print("=" * 50)

asyncio.run(test())
