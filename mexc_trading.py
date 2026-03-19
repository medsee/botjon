"""
MEXC Trading API Client
Public + Private (signed) endpoints
"""
import asyncio
import aiohttp
import hashlib
import hmac
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)
MEXC_BASE = "https://api.mexc.com"


class MEXCTrading:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.session: Optional[aiohttp.ClientSession] = None

    async def _session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"X-MEXC-APIKEY": self.api_key},
            )
        return self.session

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.secret_key.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    async def _public(self, endpoint: str, params: dict = None):
        s = await self._session()
        async with s.get(f"{MEXC_BASE}{endpoint}", params=params) as r:
            if r.status == 200:
                return await r.json()
            logger.error(f"Public error {r.status}: {await r.text()}")
            return None

    async def _private(self, method: str, endpoint: str, params: dict = None):
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        params["signature"] = self._sign(params)
        s = await self._session()
        try:
            if method == "GET":
                async with s.get(f"{MEXC_BASE}{endpoint}", params=params) as r:
                    data = await r.json()
                    if r.status != 200:
                        logger.error(f"Private GET error {r.status}: {data}")
                        return None
                    return data
            elif method == "POST":
                async with s.post(f"{MEXC_BASE}{endpoint}", params=params) as r:
                    data = await r.json()
                    if r.status != 200:
                        logger.error(f"Private POST error {r.status}: {data}")
                        return None
                    return data
            elif method == "DELETE":
                async with s.delete(f"{MEXC_BASE}{endpoint}", params=params) as r:
                    data = await r.json()
                    if r.status != 200:
                        logger.error(f"Private DELETE error {r.status}: {data}")
                        return None
                    return data
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

    # ── PUBLIC ────────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        return await self._public("/api/v3/ticker/24hr", {"symbol": symbol})

    async def get_all_tickers(self) -> list:
        r = await self._public("/api/v3/ticker/24hr")
        return r if isinstance(r, list) else []

    async def get_klines(self, symbol: str, interval="Min1", limit=50) -> list:
        r = await self._public("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        return r if r else []

    async def get_order_book(self, symbol: str, limit=5) -> Optional[dict]:
        return await self._public("/api/v3/depth", {"symbol": symbol, "limit": limit})

    async def get_exchange_info(self) -> Optional[dict]:
        return await self._public("/api/v3/exchangeInfo")

    # ── PRIVATE ───────────────────────────────────────────────
    async def get_account(self) -> Optional[dict]:
        return await self._private("GET", "/api/v3/account")

    async def get_balance(self, asset: str = "USDT") -> float:
        acc = await self.get_account()
        if not acc:
            return 0.0
        for b in acc.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET"
    ) -> Optional[dict]:
        params = {
            "symbol": symbol,
            "side": side,           # BUY / SELL
            "type": order_type,
            "quantity": str(quantity),
        }
        return await self._private("POST", "/api/v3/order", params)

    async def cancel_order(self, symbol: str, order_id: str) -> Optional[dict]:
        return await self._private("DELETE", "/api/v3/order", {
            "symbol": symbol, "orderId": order_id
        })

    async def get_open_orders(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        r = await self._private("GET", "/api/v3/openOrders", params)
        return r if isinstance(r, list) else []

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[dict]:
        return await self._private("GET", "/api/v3/order", {
            "symbol": symbol, "orderId": order_id
        })

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
