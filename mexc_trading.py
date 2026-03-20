"""
MEXC Trading API Client - Tuzatilgan versiya
"""
import asyncio
import aiohttp
import hashlib
import hmac
import time
import logging
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)
MEXC_BASE = "https://api.mexc.com"


class MEXCTrading:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self.session

    def _sign(self, query_string: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _public(self, endpoint: str, params: dict = None):
        try:
            s = await self._get_session()
            async with s.get(f"{MEXC_BASE}{endpoint}", params=params) as r:
                text = await r.text()
                if r.status == 200:
                    import json
                    return json.loads(text)
                logger.error(f"Public error {r.status}: {text}")
                return None
        except Exception as e:
            logger.error(f"Public request error: {e}")
            return None

    async def _private(self, method: str, endpoint: str, params: dict = None):
        try:
            params = params or {}
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000

            # Query string imzolash
            query_string = urlencode(params)
            signature = self._sign(query_string)
            query_string += f"&signature={signature}"

            url = f"{MEXC_BASE}{endpoint}?{query_string}"
            headers = {
                "X-MEXC-APIKEY": self.api_key,
                "Content-Type": "application/json",
            }

            s = await self._get_session()
            if method == "GET":
                async with s.get(url, headers=headers) as r:
                    import json
                    text = await r.text()
                    if r.status == 200:
                        return json.loads(text)
                    logger.error(f"Private GET error {r.status}: {text}")
                    return None
            elif method == "POST":
                async with s.post(url, headers=headers) as r:
                    import json
                    text = await r.text()
                    if r.status == 200:
                        return json.loads(text)
                    logger.error(f"Private POST error {r.status}: {text}")
                    return None
            elif method == "DELETE":
                async with s.delete(url, headers=headers) as r:
                    import json
                    text = await r.text()
                    if r.status == 200:
                        return json.loads(text)
                    logger.error(f"Private DELETE error {r.status}: {text}")
                    return None
        except Exception as e:
            logger.error(f"Private request error: {e}")
            return None

    # ── PUBLIC ────────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        return await self._public("/api/v3/ticker/24hr", {"symbol": symbol})

    async def get_all_tickers(self) -> list:
        r = await self._public("/api/v3/ticker/24hr")
        return r if isinstance(r, list) else []

    async def get_klines(self, symbol: str, interval="1m", limit=50) -> list:
        r = await self._public("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        return r if r else []

    async def get_order_book(self, symbol: str, limit=5) -> Optional[dict]:
        return await self._public("/api/v3/depth", {"symbol": symbol, "limit": limit})

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
        # Har juftlik uchun to'g'ri kasr soni
        info = await self.get_symbol_info(symbol)
        step_size = 0.01

        if info:
            for f in info.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0.01))
                    min_qty = float(f.get("minQty", 0))
                    if quantity < min_qty:
                        quantity = min_qty
                    break

        qty_str = self._format_qty(quantity, step_size)

        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": qty_str,
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

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


    async def get_symbol_info(self, symbol: str) -> Optional[dict]:
        info = await self._public("/api/v3/exchangeInfo", {"symbol": symbol})
        if not info:
            return None
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        return None

    def _format_qty(self, quantity: float, step_size: float) -> str:
        if step_size >= 1:
            return str(int(quantity))
        decimals = len(str(step_size).rstrip('0').split('.')[-1])
        return f"{quantity:.{decimals}f}"
