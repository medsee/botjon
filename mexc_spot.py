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
        self._session   = requests.Session()
        self._session.headers.update({
            "Content-Type":    "application/json",
            "X-MEXC-APIKEY":   self.api_key,
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
            query = self._signed_params(params)
        else:
            query = urlencode(params) if params else ""
        url = f"{BASE}{endpoint}" + (f"?{query}" if query else "")
        for attempt in range(3):
            try:
                r    = self._session.get(url, timeout=15)
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
                r    = self._session.post(url, timeout=15)
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
        # quoteOrderQty = USDT miqdori
        return await self._post("/api/v3/order", {
            "symbol":        sym,
            "side":          "BUY",
            "type":          "MARKET",
            "quoteOrderQty": round(usdt_amount, 2),
        })

    async def sell_market(self, symbol: str, qty: float) -> Optional[dict]:
        """Koin miqdori bilan market narxda sotish"""
        sym = symbol.replace("_", "")
        return await self._post("/api/v3/order", {
            "symbol":   sym,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": qty,
        })

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
