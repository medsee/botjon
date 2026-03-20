"""
MEXC Futures API Client - v5 FINAL
aiohttp o'rniga requests ishlatildi (ishonchli POST)
asyncio.to_thread orqali async bilan mos ishlaydi
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
BASE = "https://contract.mexc.com"


class MEXCFutures:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key.strip()
        self.secret_key = secret_key.strip()
        self.leverage   = 3
        self._session   = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _sign(self, timestamp: str, params_str: str) -> str:
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

    def _get_sync(self, endpoint: str, params: dict = None) -> Optional[any]:
        params  = params or {}
        query   = urlencode(sorted(params.items()))
        headers = self._headers(query)
        url     = f"{BASE}{endpoint}" + (f"?{query}" if query else "")
        for attempt in range(3):
            try:
                r    = self._session.get(url, headers=headers, timeout=15)
                text = r.text.strip()
                if not text:
                    logger.error(f"GET {endpoint} attempt {attempt+1}: Bo'sh javob (HTTP {r.status_code})")
                    time.sleep(1)
                    continue
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as je:
                    logger.error(f"GET {endpoint} attempt {attempt+1}: JSON xato: {je} | Raw: '{text[:200]}'")
                    time.sleep(1)
                    continue
                if data.get("success") is True or data.get("code") == 0:
                    return data.get("data", data)
                if data.get("code") == 510:
                    time.sleep(2)
                    continue
                logger.error(f"GET {endpoint}: code={data.get('code')} msg={data.get('message','?')}")
                return None
            except requests.exceptions.Timeout:
                logger.error(f"GET {endpoint} attempt {attempt+1}: Timeout")
                time.sleep(1)
            except Exception as e:
                logger.error(f"GET {endpoint} attempt {attempt+1}: {type(e).__name__}: {e}")
                time.sleep(1)
        return None

    def _post_sync(self, endpoint: str, body: dict = None) -> Optional[any]:
        body     = body or {}
        body_str = json.dumps(body, separators=(',', ':'))
        headers  = self._headers(body_str)
        url      = f"{BASE}{endpoint}"
        for attempt in range(3):
            try:
                r    = self._session.post(url, headers=headers, data=body_str, timeout=15)
                text = r.text.strip()
                logger.info(f"POST {endpoint} [{r.status_code}]: '{text[:300]}'")

                if not text:
                    logger.error(f"POST {endpoint} attempt {attempt+1}: Server bo'sh javob qaytardi (HTTP {r.status_code}). "
                                 f"Sabab: noto'g'ri API key, IP bloklash, yoki Futures API ruxsati yo'q.")
                    time.sleep(2)
                    continue

                try:
                    data = json.loads(text)
                except json.JSONDecodeError as je:
                    logger.error(f"POST {endpoint} attempt {attempt+1}: JSON parse xato: {je} | Raw: '{text[:200]}'")
                    time.sleep(1)
                    continue

                if data.get("success") is True or data.get("code") == 0:
                    return data.get("data", data)

                code = data.get("code")
                msg  = data.get("message") or data.get("msg", "?")
                logger.error(f"POST {endpoint}: code={code} msg={msg}")

                # Qayta urinish kerak bo'lmagan xatolar
                if code in (10007, 10008, 10009, 1337):  # Auth xatolar
                    logger.error("⛔ API autentifikatsiya xatosi! API key va Secret kalitni tekshiring.")
                    return None
                if code == 510:   # Rate limit
                    time.sleep(2)
                    continue
                return None

            except requests.exceptions.Timeout:
                logger.error(f"POST {endpoint} attempt {attempt+1}: Timeout (15s)")
                time.sleep(1)
            except requests.exceptions.ConnectionError as ce:
                logger.error(f"POST {endpoint} attempt {attempt+1}: Connection error: {ce}")
                time.sleep(2)
            except Exception as e:
                logger.error(f"POST {endpoint} attempt {attempt+1}: Kutilmagan xato: {type(e).__name__}: {e}")
                time.sleep(1)
        return None

    # Async wrappers
    async def _get(self, endpoint, params=None):
        return await asyncio.to_thread(self._get_sync, endpoint, params)

    async def _post(self, endpoint, body=None):
        return await asyncio.to_thread(self._post_sync, endpoint, body)

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
        if isinstance(r, dict):
            closes = r.get("close", r.get("closes", []))
            opens  = r.get("open",  r.get("opens",  []))
            highs  = r.get("high",  r.get("highs",  []))
            lows   = r.get("low",   r.get("lows",   []))
            vols   = r.get("vol",   r.get("volume", []))
            if not closes:
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
    async def get_account(self):
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
        for pos_type in [1, 2]:
            await self._post("/api/v1/private/position/change_leverage", {
                "symbol":       symbol,
                "leverage":     leverage,
                "openType":     1,
                "positionType": pos_type,
            })
        return True

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
        return r if isinstance(r, list) else []

    async def get_position(self, symbol: str) -> Optional[dict]:
        for p in await self.get_positions():
            if p.get("symbol") == symbol:
                return p
        return None

    async def close(self):
        self._session.close()
