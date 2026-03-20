"""
MEXC Futures API Client - v4 FINAL
Sign algoritmi to'liq qayta yozildi
MEXC docs: https://mxcdevelop.github.io/apidocs/contract_v1_en/
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
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self.session

    def _sign(self, timestamp: str, params_str: str) -> str:
        """
        MEXC Futures sign:
        signature = HMAC-SHA256(api_key + timestamp + params_str, secret_key)
        """
        raw = self.api_key + timestamp + params_str
        return hmac.new(
            self.secret_key.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _headers(self, params_str: str = "") -> dict:
        ts  = str(int(time.time() * 1000))
        sig = self._sign(ts, params_str)
        return {
            "ApiKey":         self.api_key,
            "Request-Time":   ts,
            "Signature":      sig,
            "Content-Type":   "application/json",
        }

    # ── GET ─────────────────────────────────────────────────
    async def _get(self, endpoint: str, params: dict = None) -> Optional[any]:
        params = params or {}
        query  = urlencode(sorted(params.items()))   # sorted — deterministik
        headers = self._headers(query)
        url = f"{MEXC_FUTURES_BASE}{endpoint}"
        if query:
            url += f"?{query}"

        for attempt in range(3):
            try:
                s = await self._get_session()
                async with s.get(url, headers=headers) as r:
                    text = await r.text()
                    if not text.strip():
                        await asyncio.sleep(1); continue
                    data = json.loads(text)
                    code = data.get("code", -1)
                    if data.get("success") is True or code == 0:
                        return data.get("data", data)
                    if code == 510:   # rate limit
                        await asyncio.sleep(2); continue
                    logger.error(f"GET {endpoint}: {data}")
                    return None
            except Exception as e:
                logger.error(f"GET {endpoint} attempt {attempt+1}: {e}")
                await asyncio.sleep(1)
        return None

    # ── POST ────────────────────────────────────────────────
    async def _post(self, endpoint: str, body: dict = None) -> Optional[any]:
        body     = body or {}
        # JSON string — sign uchun ham, body uchun ham bir xil
        body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        headers  = self._headers(body_str)
        url      = f"{MEXC_FUTURES_BASE}{endpoint}"

        for attempt in range(3):
            try:
                s = await self._get_session()
                async with s.post(url, headers=headers, data=body_str.encode("utf-8")) as r:
                    status = r.status
                    text   = await r.text()
                    logger.debug(f"POST {endpoint} [{status}]: {text[:200]}")

                    if not text.strip():
                        logger.warning(f"POST {endpoint} bo'sh javob (attempt {attempt+1}), HTTP {status}")
                        await asyncio.sleep(1); continue

                    data = json.loads(text)
                    code = data.get("code", -1)
                    if data.get("success") is True or code == 0:
                        return data.get("data", data)

                    logger.error(f"POST {endpoint}: code={code} msg={data.get('message','?')}")
                    return None
            except Exception as e:
                logger.error(f"POST {endpoint} attempt {attempt+1}: {e}")
                await asyncio.sleep(1)
        return None

    # ── PUBLIC ──────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Optional[dict]:
        r = await self._get("/api/v1/contract/ticker", {"symbol": symbol})
        if isinstance(r, list):
            return r[0] if r else None
        return r

    async def get_all_tickers(self) -> list:
        r = await self._get("/api/v1/contract/ticker")
        return r if isinstance(r, list) else []

    async def get_klines(self, symbol: str, interval="Min1", limit=50) -> list:
        r = await self._get(f"/api/v1/contract/kline/{symbol}", {
            "interval": interval, "limit": limit
        })
        if not r:
            return []

        # list of dicts
        if isinstance(r, list):
            if r and isinstance(r[0], dict):
                return r
            if r and isinstance(r[0], (list, tuple)):
                out = []
                for k in r:
                    try:
                        out.append({"open": float(k[1]), "high": float(k[2]),
                                    "low":  float(k[3]), "close": float(k[4]),
                                    "vol":  float(k[5]) if len(k) > 5 else 0})
                    except: pass
                return out
            return []

        # dict of arrays (MEXC asosiy format)
        if isinstance(r, dict):
            closes = r.get("close", r.get("closes", []))
            opens  = r.get("open",  r.get("opens",  []))
            highs  = r.get("high",  r.get("highs",  []))
            lows   = r.get("low",   r.get("lows",   []))
            vols   = r.get("vol",   r.get("volume", []))
            if not closes:
                logger.warning(f"Klines bo'sh: {symbol} keys={list(r.keys())}")
                return []
            out = []
            for i in range(len(closes)):
                try:
                    out.append({
                        "open":  float(opens[i])  if i < len(opens)  else float(closes[i]),
                        "high":  float(highs[i])  if i < len(highs)  else float(closes[i]),
                        "low":   float(lows[i])   if i < len(lows)   else float(closes[i]),
                        "close": float(closes[i]),
                        "vol":   float(vols[i])   if i < len(vols)   else 0,
                    })
                except: pass
            return out
        return []

    # ── PRIVATE ─────────────────────────────────────────────
    async def get_account(self) -> Optional[any]:
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
        """Leverage o'rnatish — xato bo'lsa ham davom etamiz"""
        for pos_type in [1, 2]:   # 1=Long, 2=Short
            await self._post("/api/v1/private/position/change_leverage", {
                "symbol":       symbol,
                "leverage":     leverage,
                "openType":     1,
                "positionType": pos_type,
            })
        return True   # Har doim True — order bloklanmasin

    async def _submit_order(self, symbol: str, side: int, vol: int) -> Optional[dict]:
        """
        MEXC Futures order submit
        side: 1=OpenLong, 2=CloseLong, 3=OpenShort, 4=CloseShort
        type: 5=Market
        openType: 1=Cross, 2=Isolated
        """
        return await self._post("/api/v1/private/order/submit", {
            "symbol":   symbol,
            "price":    "0",        # Market order uchun string "0"
            "vol":      vol,
            "leverage": self.leverage,
            "side":     side,
            "type":     5,
            "openType": 1,
        })

    async def open_long(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._submit_order(symbol, 1, vol)

    async def open_short(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._submit_order(symbol, 3, vol)

    async def close_long(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._submit_order(symbol, 2, vol)

    async def close_short(self, symbol: str, vol: int) -> Optional[dict]:
        return await self._submit_order(symbol, 4, vol)

    async def get_positions(self) -> list:
        r = await self._get("/api/v1/private/position/open_positions")
        return r if isinstance(r, list) else []

    async def get_position(self, symbol: str) -> Optional[dict]:
        for p in await self.get_positions():
            if p.get("symbol") == symbol:
                return p
        return None

    async def cancel_order(self, order_id: str) -> bool:
        r = await self._post("/api/v1/private/order/cancel", {"orderId": order_id})
        return r is not None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
