"""
MEXC Spot API Client
MEXC Spot API O'zbekistonda to'liq ishlaydi.
Long only (buy/sell), leverage yo'q.
"""
import asyncio
import hashlib
import hmac
import time
import logging
import json
import requests
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)
BASE = "https://api.mexc.com"


class MEXCSpot:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key.strip()
        self.secret_key = secret_key.strip()
        self._symbol_info_cache: dict = {}   # stepSize cache
        self._session   = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        # Private so'rovlar uchun alohida session
        self._private_session = requests.Session()
        self._private_session.headers.update({
            "Content-Type":  "application/json",
            "X-MEXC-APIKEY": self.api_key,
            "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def _sign(self, params: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            params.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _signed_params(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig   = self._sign(query)
        return query + f"&signature={sig}"

    def _get_sync(self, endpoint: str, params: dict = None, signed: bool = False):
        params = params or {}
        if signed:
            query   = self._signed_params(params)
            session = self._private_session
        else:
            query   = urlencode(params) if params else ""
            session = self._session
        url = f"{BASE}{endpoint}" + (f"?{query}" if query else "")
        for attempt in range(3):
            try:
                r    = session.get(url, timeout=15)
                text = r.text.strip()
                if not text:
                    logger.error(f"GET {endpoint} attempt {attempt+1}: Bo'sh javob")
                    time.sleep(1); continue
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                if "code" in data and data["code"] != 200 and data.get("code", 0) != 0:
                    logger.error(f"GET {endpoint}: {data}")
                    return None
                return data
            except Exception as e:
                logger.error(f"GET {endpoint} attempt {attempt+1}: {e}")
                time.sleep(1)
        return None

    def _post_sync(self, endpoint: str, params: dict = None):
        params = params or {}
        query  = self._signed_params(params)
        url    = f"{BASE}{endpoint}?{query}"
        for attempt in range(3):
            try:
                r    = self._private_session.post(url, timeout=15)
                text = r.text.strip()
                logger.info(f"POST {endpoint} [{r.status_code}]: {text[:300]}")
                if not text:
                    logger.error(f"POST {endpoint} attempt {attempt+1}: Bo'sh javob (HTTP {r.status_code})")
                    time.sleep(2); continue
                data = json.loads(text)
                if isinstance(data, dict) and data.get("code") and data["code"] != 200:
                    logger.error(f"POST {endpoint}: code={data.get('code')} msg={data.get('msg','?')}")
                    return None
                return data
            except Exception as e:
                logger.error(f"POST {endpoint} attempt {attempt+1}: {e}")
                time.sleep(1)
        return None

    def _delete_sync(self, endpoint: str, params: dict = None):
        params = params or {}
        query  = self._signed_params(params)
        url    = f"{BASE}{endpoint}?{query}"
        for attempt in range(3):
            try:
                r    = self._session.delete(url, timeout=15)
                text = r.text.strip()
                if not text:
                    time.sleep(1); continue
                return json.loads(text)
            except Exception as e:
                logger.error(f"DELETE {endpoint} attempt {attempt+1}: {e}")
                time.sleep(1)
        return None

    async def _get(self, endpoint, params=None, signed=False):
        return await asyncio.to_thread(self._get_sync, endpoint, params, signed)

    async def _post(self, endpoint, params=None):
        return await asyncio.to_thread(self._post_sync, endpoint, params)

    async def _delete(self, endpoint, params=None):
        return await asyncio.to_thread(self._delete_sync, endpoint, params)

    # ── PUBLIC ──────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Narx ma'lumoti"""
        # MEXC Spot symbol formati: BTCUSDT (pastki chiziqsiz)
        sym = symbol.replace("_", "")
        r   = await self._get("/api/v3/ticker/24hr", {"symbol": sym})
        return r if isinstance(r, dict) else None

    async def get_all_tickers(self) -> list:
        r = await self._get("/api/v3/ticker/24hr")
        return r if isinstance(r, list) else []

    async def get_klines(self, symbol: str, interval="1m", limit=50) -> list:
        sym = symbol.replace("_", "")
        r   = await self._get("/api/v3/klines", {
            "symbol": sym, "interval": interval, "limit": limit
        })
        if not isinstance(r, list):
            return []
        out = []
        for k in r:
            try:
                out.append({
                    "open":  float(k[1]),
                    "high":  float(k[2]),
                    "low":   float(k[3]),
                    "close": float(k[4]),
                    "vol":   float(k[5]),
                })
            except:
                pass
        return out

    async def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Symbol minimal miqdori va step size"""
        sym  = symbol.replace("_", "")
        info = await self._get("/api/v3/exchangeInfo", {"symbol": sym})
        if not info:
            return None
        for s in info.get("symbols", []):
            if s.get("symbol") == sym:
                return s
        return None

    # ── PRIVATE ─────────────────────────────────────────────
    async def get_balance(self, asset: str = "USDT") -> float:
        r = await self._get("/api/v3/account", signed=True)
        if not r:
            return 0.0
        for b in r.get("balances", []):
            if b.get("asset") == asset:
                return float(b.get("free", 0))
        return 0.0

    async def get_asset_balance(self, asset: str) -> float:
        """Ma'lum bir koin balansini olish"""
        r = await self._get("/api/v3/account", signed=True)
        if not r:
            return 0.0
        for b in r.get("balances", []):
            if b.get("asset") == asset:
                return float(b.get("free", 0))
        return 0.0

    async def buy_market(self, symbol: str, usdt_amount: float) -> Optional[dict]:
        """USDT bilan market narxda sotib olish"""
        sym = symbol.replace("_", "")
        # MEXC minimal savdo: 1 USDT dan kam bo'lmasin
        amount = max(round(usdt_amount, 2), 1.0)
        logger.info(f"BUY {sym}: quoteOrderQty={amount}")
        return await self._post("/api/v3/order", {
            "symbol":        sym,
            "side":          "BUY",
            "type":          "MARKET",
            "quoteOrderQty": amount,
        })

    async def get_step_size(self, symbol: str) -> int:
        """StepSize ni exchangeInfo dan olib, narxga qarab tekshirish"""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]

        sym  = symbol.replace("_", "")
        decimals = 6  # default

        # 1. exchangeInfo dan olish
        try:
            info = await self._get("/api/v3/exchangeInfo", {"symbol": sym})
            if info:
                for s in info.get("symbols", []):
                    if s.get("symbol") == sym:
                        for f in s.get("filters", []):
                            if f.get("filterType") == "LOT_SIZE":
                                step = str(f.get("stepSize", "0.000001")).rstrip("0")
                                if "." in step:
                                    decimals = len(step.split(".")[1])
                                else:
                                    decimals = 0
                                break
                        break
        except Exception as e:
            logger.warning(f"exchangeInfo {sym}: {e}")

        # 2. Narxga qarab decimals ni tekshirish va to'g'irlash
        # Agar narx > 0.01 bo'lsa ko'pincha decimals <= 4
        # Agar narx > 1 bo'lsa ko'pincha decimals <= 2
        try:
            ticker = self._session.get(
                f"{BASE}/api/v3/ticker/price?symbol={sym}", timeout=5
            )
            price = float(ticker.json().get("price", 0))
            if price >= 100:
                decimals = min(decimals, 2)
            elif price >= 10:
                decimals = min(decimals, 3)
            elif price >= 1:
                decimals = min(decimals, 4)
            elif price >= 0.01:
                decimals = min(decimals, 5)
        except:
            pass

        self._symbol_info_cache[symbol] = decimals
        logger.info(f"StepSize {sym}: decimals={decimals}")
        return decimals

    async def sell_market(self, symbol: str, qty: float) -> Optional[dict]:
        """Koin miqdori bilan market narxda sotish.
        Haqiqiy akkaunt balansidan to'g'ri miqdorni oladi."""
        sym   = symbol.replace("_", "")
        base  = sym.replace("USDT", "")

        # 1. Haqiqiy akkaunt balansini ol
        real_qty = await self.get_asset_balance(base)
        if real_qty <= 0:
            logger.warning(f"SELL {sym}: akkauntda {base} yo'q (real=0), bekor qilindi")
            return {"status": "SKIPPED", "reason": "zero_balance"}
        use_qty = min(real_qty, qty)

        # 2. StepSize bo'yicha yaxlitlash
        decimals = await self.get_step_size(symbol)

        # 3. Eng yaxshi decimals ni avtomatik topish (kattadan kichikka)
        qty_adj  = 0.0
        best_dec = decimals
        for d in range(decimals, -1, -1):
            f = 10 ** d
            candidate = int(use_qty * f) / f
            if candidate > 0:
                qty_adj  = candidate
                best_dec = d
                break

        logger.info(f"SELL {sym}: real={real_qty:.8f} use={use_qty:.8f} adj={qty_adj:.8f} (dec={best_dec})")

        if qty_adj <= 0:
            logger.error(f"SELL {sym}: qty_adj=0, miqdor birja minimumidan kichik — sotib bo'lmadi")
            return None

        result = await self._post("/api/v3/order", {
            "symbol":   sym,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": qty_adj,
        })

        # Agar quantity scale xatosi bo'lsa — cache tozalab, har bir decimals ni sinab ko'r
        if result is None:
            if symbol in self._symbol_info_cache:
                del self._symbol_info_cache[symbol]
            logger.warning(f"SELL {sym}: quantity xato, cache tozalanib barcha decimals sinab ko'riladi")
            for d in range(best_dec, -1, -1):
                f2 = 10 ** d
                qa = int(use_qty * f2) / f2
                if qa > 0:
                    logger.info(f"SELL {sym}: retry decimals={d} qty={qa}")
                    result = await self._post("/api/v3/order", {
                        "symbol":   sym,
                        "side":     "SELL",
                        "type":     "MARKET",
                        "quantity": qa,
                    })
                    if result:
                        self._symbol_info_cache[symbol] = d
                        break

        return result

    async def can_sell(self, symbol: str, qty: float) -> bool:
        """SELL qilishdan oldin miqdor yetarlimi tekshirish"""
        sym  = symbol.replace("_", "")
        base = sym.replace("USDT", "")
        real_qty = await self.get_asset_balance(base)
        if real_qty <= 0:
            return False
        use_qty  = min(real_qty, qty)
        decimals = await self.get_step_size(symbol)
        for d in range(decimals, -1, -1):
            f = 10 ** d
            if int(use_qty * f) / f > 0:
                return True
        return False

    async def get_open_orders(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("_", "")
        r = await self._get("/api/v3/openOrders", params, signed=True)
        return r if isinstance(r, list) else []

    async def cancel_order(self, symbol: str, order_id: str) -> Optional[dict]:
        sym = symbol.replace("_", "")
        return await self._delete("/api/v3/order", {
            "symbol":  sym,
            "orderId": order_id,
        })

    async def close(self):
        self._session.close()
        self._private_session.close()
