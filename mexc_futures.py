"""
MEXC Futures API Client
- 2x leverage
- Long va Short pozitsiyalar
- Hedge mode
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
MEXC_FUTURES_BASE = "https://contract.mexc.com"


class MEXCFutures:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.session: Optional[aiohttp.ClientSession] = None
        self.leverage = 2

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self.session

    def _sign(self, params_str: str) -> str:
        timestamp = str(int(time.time() * 1000))
        sign_str = self.api_key + timestamp + params_str
        return hmac.new(
            self.secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _get_headers(self, params_str: str = "") -> dict:
        timestamp = str(int(time.time() * 1000))
        sign_str = self.api_key + timestamp + params_str
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return {
            "ApiKey": self.api_key,
            "Request-Time": timestamp,
            "Signature": signature,
            "Content-Type": "application/json",
        }

    async def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        try:
            params = params or {}
            query = urlencode(params)
            headers = self._get_headers(query)
            s = await self._get_session()
            url = f"{MEXC_FUTURES_BASE}{endpoint}"
            if query:
                url += f"?{query}"
            async with s.get(url, headers=headers) as r:
                import json
                text = await r.text()
                data = json.loads(text)
                if data.get("success") or data.get("code") == 0:
                    return data.get("data", data)
                logger.error(f"Futures GET error: {data}")
                return None
        except Exception as e:
            logger.error(f"Futures GET exception: {e}")
            return None

    async def _post(self, endpoint: str, body: dict = None) -> Optional[dict]:
        try:
            import json
            body = body or {}
            body_str = json.dumps(body)
            headers = self._get_headers(body_str)
            s = await self._get_session()
            url = f"{MEXC_FUTURES_BASE}{endpoint}"
            async with s.post(url, headers=headers, data=body_str) as r:
                text = await r.text()
                data = json.loads(text)
                if data.get("success") or data.get("code") == 0:
                    return data.get("data", data)
                logger.error(f"Futures POST error: {data}")
                return None
        except Exception as e:
            logger.error(f"Futures POST exception: {e}")
            return None

    # ── PUBLIC ────────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Futures ticker"""
        r = await self._get(f"/api/v1/contract/ticker", {"symbol": symbol})
        if r and isinstance(r, list):
            return r[0] if r else None
        return r

    async def get_all_tickers(self) -> list:
        r = await self._get("/api/v1/contract/ticker")
        if isinstance(r, list):
            return r
        return []

    async def get_klines(self, symbol: str, interval="Min1", limit=60) -> list:
        r = await self._get(f"/api/v1/contract/kline/{symbol}", {
            "interval": interval, "limit": limit
        })
        if r and isinstance(r, dict):
            return r.get("data", [])
        return []

    async def get_contract_info(self, symbol: str) -> Optional[dict]:
        r = await self._get(f"/api/v1/contract/detail", {"symbol": symbol})
        return r

    # ── PRIVATE ───────────────────────────────────────────────
    async def get_account(self) -> Optional[dict]:
        return await self._get("/api/v1/private/account/assets")

    async def get_balance(self) -> float:
        acc = await self.get_account()
        if not acc:
            return 0.0
        if isinstance(acc, list):
            for a in acc:
                if a.get("currency") == "USDT":
                    return float(a.get("availableBalance", 0))
        return float(acc.get("availableBalance", 0)) if acc else 0.0

    async def set_leverage(self, symbol: str, leverage: int = 2) -> bool:
        r = await self._post("/api/v1/private/position/change_leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "openType": 1,  # Cross margin
        })
        return r is not None

    async def open_long(self, symbol: str, vol: int) -> Optional[dict]:
        """Long pozitsiya ochish (BUY)"""
        return await self._post("/api/v1/private/order/submit", {
            "symbol": symbol,
            "price": 0,
            "vol": vol,
            "leverage": self.leverage,
            "side": 1,       # Open long
            "type": 5,       # Market order
            "openType": 1,   # Cross margin
        })

    async def open_short(self, symbol: str, vol: int) -> Optional[dict]:
        """Short pozitsiya ochish (SELL)"""
        return await self._post("/api/v1/private/order/submit", {
            "symbol": symbol,
            "price": 0,
            "vol": vol,
            "leverage": self.leverage,
            "side": 3,       # Open short
            "type": 5,       # Market order
            "openType": 1,   # Cross margin
        })

    async def close_long(self, symbol: str, vol: int) -> Optional[dict]:
        """Long pozitsiyani yopish"""
        return await self._post("/api/v1/private/order/submit", {
            "symbol": symbol,
            "price": 0,
            "vol": vol,
            "leverage": self.leverage,
            "side": 2,       # Close long
            "type": 5,
            "openType": 1,
        })

    async def close_short(self, symbol: str, vol: int) -> Optional[dict]:
        """Short pozitsiyani yopish"""
        return await self._post("/api/v1/private/order/submit", {
            "symbol": symbol,
            "price": 0,
            "vol": vol,
            "leverage": self.leverage,
            "side": 4,       # Close short
            "type": 5,
            "openType": 1,
        })

    async def get_positions(self) -> list:
        r = await self._get("/api/v1/private/position/open_positions")
        if isinstance(r, list):
            return r
        return []

    async def get_position(self, symbol: str) -> Optional[dict]:
        positions = await self.get_positions()
        for p in positions:
            if p.get("symbol") == symbol:
                return p
        return None

    async def cancel_order(self, order_id: str) -> bool:
        r = await self._post("/api/v1/private/order/cancel", {
            "orderId": order_id
        })
        return r is not None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
