"""
MEXC Futures API Client - v2 FIX
- Klines format to'g'irlandi
- Debug log qo'shildi
"""
import asyncio
import aiohttp
import hashlib
import hmac
import time
import logging
import json
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)
MEXC_FUTURES_BASE = "https://contract.mexc.com"


class MEXCFutures:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key.strip()
        self.secret_key = secret_key.strip()
        self.session: Optional[aiohttp.ClientSession] = None
        self.leverage   = 3

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self.session

    def _get_headers(self, params_str: str = "") -> dict:
        timestamp = str(int(time.time() * 1000))
        sign_str  = self.api_key + timestamp + params_str
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return {
            "ApiKey":       self.api_key,
            "Request-Time": timestamp,
            "Signature":    signature,
            "Content-Type": "application/json",
        }

    async def _get(self, endpoint: str, params: dict = None, retry: int = 3) -> Optional[dict]:
        for attempt in range(retry):
            try:
                params = params or {}
                query  = urlencode(params)
                headers = self._get_headers(query)
                s   = await self._get_session()
                url = f"{MEXC_FUTURES_BASE}{endpoint}"
                if query:
                    url += f"?{query}"
                async with s.get(url, headers=headers) as r:
                    text = await r.text()
                    data = json.loads(text)
                    if data.get("success") is True or data.get("code") == 0:
                        return data.get("data", data)
                    if data.get("code") == 510:
                        await asyncio.sleep(2)
                        continue
                    logger.error(f"GET error {endpoint}: {data}")
                    return None
            except Exception as e:
                logger.error(f"GET exception {endpoint}: {e}")
                await asyncio.sleep(1)
        return None

    async def _post(self, endpoint: str, body: dict = None) -> Optional[dict]:
        try:
            body     = body or {}
            body_str = json.dumps(body)
            headers  = self._get_headers(body_str)
            s   = await self._get_session()
            url = f"{MEXC_FUTURES_BASE}{endpoint}"
            async with s.post(url, headers=headers, data=body_str) as r:
                text = await r.text()
                data = json.loads(text)
                if data.get("success") is True or data.get("code") == 0:
                    return data.get("data", data)
                logger.error(f"POST error {endpoint}: {data}")
                return None
        except Exception as e:
            logger.error(f"POST exception {endpoint}: {e}")
            return None

    # ── PUBLIC ──────────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        r = await self._get("/api/v1/contract/ticker", {"symbol": symbol})
        if r and isinstance(r, list):
            return r[0] if r else None
        return r

    async def get_all_tickers(self) -> list:
        r = await self._get("/api/v1/contract/ticker")
        if isinstance(r, list):
            return r
        return []

    async def get_klines(self, symbol: str, interval="Min1", limit=50) -> list:
        """
        MEXC Futures klines formati:
        data = {
          "time": [...],
          "open": [...],
          "close": [...],
          "high": [...],
          "low": [...],
          "vol": [...],
          "amount": [...]
        }
        Bu funksiya dict ro'yxatiga o'giradi: [{"open":..,"close":..}, ...]
        """
        r = await self._get(f"/api/v1/contract/kline/{symbol}", {
            "interval": interval,
            "limit": limit
        })

        if not r:
            logger.warning(f"Klines bo'sh: {symbol}")
            return []

        # Format 1: to'g'ridan dict list kelsa
        if isinstance(r, list):
            if len(r) > 0 and isinstance(r[0], dict):
                return r
            # list of lists
            if len(r) > 0 and isinstance(r[0], (list, tuple)):
                result = []
                for k in r:
                    try:
                        result.append({
                            "open": float(k[1]), "high": float(k[2]),
                            "low": float(k[3]),  "close": float(k[4]),
                            "vol": float(k[5]) if len(k) > 5 else 0,
                        })
                    except:
                        pass
                return result
            return []

        # Format 2: dict with arrays (MEXC asosiy format)
        if isinstance(r, dict):
            opens  = r.get("open",   r.get("opens",  []))
            highs  = r.get("high",   r.get("highs",  []))
            lows   = r.get("low",    r.get("lows",   []))
            closes = r.get("close",  r.get("closes", []))
            vols   = r.get("vol",    r.get("volume", r.get("volumes", [])))

            if not closes:
                logger.warning(f"Klines closes bo'sh: {symbol} | keys={list(r.keys())}")
                return []

            n = len(closes)
            result = []
            for i in range(n):
                try:
                    result.append({
                        "open":  float(opens[i])  if i < len(opens)  else float(closes[i]),
                        "high":  float(highs[i])  if i < len(highs)  else float(closes[i]),
                        "low":   float(lows[i])   if i < len(lows)   else float(closes[i]),
                        "close": float(closes[i]),
                        "vol":   float(vols[i])   if i < len(vols)   else 0,
                    })
                except Exception as e:
                    logger.debug(f"Kline row xato {symbol}[{i}]: {e}")
            logger.debug(f"Klines: {symbol} → {len(result)} ta sham")
            return result

        logger.warning(f"Klines noma'lum format: {symbol} | type={type(r)}")
        return []

    async def get_contract_info(self, symbol: str) -> Optional[dict]:
        return await self._get("/api/v1/contract/detail", {"symbol": symbol})

    # ── PRIVATE ─────────────────────────────────────────────────
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
        if isinstance(acc, dict):
            return float(acc.get("availableBalance", 0))
        return 0.0

    async def set_leverage(self, symbol: str, leverage: int = 3) -> bool:
        r = await self._post("/api/v1/private/position/change_leverage", {
            "symbol":   symbol,
            "leverage": leverage,
            "openType": 1,
        })
        return r is not None

    async def open_long(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._post("/api/v1/private/order/submit", {
            "symbol":   symbol,
            "price":    0,
            "vol":      vol,
            "leverage": self.leverage,
            "side":     1,
            "type":     5,
            "openType": 1,
        })

    async def open_short(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._post("/api/v1/private/order/submit", {
            "symbol":   symbol,
            "price":    0,
            "vol":      vol,
            "leverage": self.leverage,
            "side":     3,
            "type":     5,
            "openType": 1,
        })

    async def close_long(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._post("/api/v1/private/order/submit", {
            "symbol":   symbol,
            "price":    0,
            "vol":      vol,
            "leverage": self.leverage,
            "side":     2,
            "type":     5,
            "openType": 1,
        })

    async def close_short(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._post("/api/v1/private/order/submit", {
            "symbol":   symbol,
            "price":    0,
            "vol":      vol,
            "leverage": self.leverage,
            "side":     4,
            "type":     5,
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
        r = await self._post("/api/v1/private/order/cancel", {"orderId": order_id})
        return r is not None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
