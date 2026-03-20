"""
MEXC Futures API Client - v6 WebSocket
REST API order placing bloklanganligi sababli WebSocket orqali order beriladi.
wss://contract.mexc.com/edge — rasmiy MEXC Futures WS endpoint
"""
import asyncio
import hashlib
import hmac
import time
import logging
import json
import requests
import websockets
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)
BASE    = "https://contract.mexc.com"
WS_URL  = "wss://contract.mexc.com/edge"


class MEXCFutures:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key.strip()
        self.secret_key = secret_key.strip()
        self.leverage   = 3
        self._session   = requests.Session()
        self._session.headers.update({
            "Content-Type":    "application/json",
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept":          "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          "https://futures.mexc.com",
            "Referer":         "https://futures.mexc.com/",
        })

    def _sign(self, timestamp: str, params_str: str) -> str:
        raw = self.api_key + timestamp + params_str
        return hmac.new(
            self.secret_key.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _rest_headers(self, params_str: str = "") -> dict:
        ts  = str(int(time.time() * 1000))
        sig = self._sign(ts, params_str)
        return {
            "ApiKey":       self.api_key,
            "Request-Time": ts,
            "Signature":    sig,
            "Content-Type": "application/json",
        }

    def _get_sync(self, endpoint: str, params: dict = None):
        params  = params or {}
        query   = urlencode(sorted(params.items()))
        headers = self._rest_headers(query)
        url     = f"{BASE}{endpoint}" + (f"?{query}" if query else "")
        for attempt in range(3):
            try:
                r    = self._session.get(url, headers=headers, timeout=15)
                text = r.text.strip()
                if not text:
                    logger.error(f"GET {endpoint} attempt {attempt+1}: Bo'sh javob (HTTP {r.status_code})")
                    time.sleep(1)
                    continue
                data = json.loads(text)
                if data.get("success") is True or data.get("code") == 0:
                    return data.get("data", data)
                if data.get("code") == 510:
                    time.sleep(2)
                    continue
                logger.error(f"GET {endpoint}: code={data.get('code')} msg={data.get('message','?')}")
                return None
            except Exception as e:
                logger.error(f"GET {endpoint} attempt {attempt+1}: {type(e).__name__}: {e}")
                time.sleep(1)
        return None

    async def _get(self, endpoint, params=None):
        return await asyncio.to_thread(self._get_sync, endpoint, params)

    def _post_sync(self, endpoint: str, body: dict = None):
        body     = body or {}
        body_str = json.dumps(body, separators=(',', ':'))
        headers  = self._rest_headers(body_str)
        url      = f"{BASE}{endpoint}"
        for attempt in range(3):
            try:
                r    = self._session.post(url, headers=headers, data=body_str, timeout=15)
                text = r.text.strip()
                logger.info(f"POST {endpoint} [{r.status_code}]: {text[:200]}")
                if not text:
                    time.sleep(2)
                    continue
                data = json.loads(text)
                if data.get("success") is True or data.get("code") == 0:
                    return data.get("data", data)
                logger.error(f"POST {endpoint}: code={data.get('code')} msg={data.get('message','?')}")
                return None
            except Exception as e:
                logger.error(f"POST {endpoint} attempt {attempt+1}: {e}")
                time.sleep(1)
        return None

    async def _post(self, endpoint, body=None):
        return await asyncio.to_thread(self._post_sync, endpoint, body)

    async def _ws_order(self, order_body: dict):
        """WebSocket orqali MEXC Futures order berish"""
        ts  = str(int(time.time() * 1000))
        sig = self._sign(ts, "")

        login_msg = {
            "method": "login",
            "param": {
                "apiKey":    self.api_key,
                "signature": sig,
                "reqTime":   ts,
            }
        }
        order_msg = {
            "method": "order.submit",
            "param":  order_body,
        }

        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                additional_headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Origin": "https://futures.mexc.com",
                }
            ) as ws:
                # Login
                await ws.send(json.dumps(login_msg))
                login_raw  = await asyncio.wait_for(ws.recv(), timeout=10)
                login_data = json.loads(login_raw)
                logger.info(f"WS Login: {login_data}")

                if "error" in str(login_data.get("channel", "")).lower():
                    logger.error(f"WS Login xato: {login_data}")
                    return None

                # Order yuborish
                await ws.send(json.dumps(order_msg))
                logger.info(f"WS Order yuborildi: {order_body.get('symbol')} side={order_body.get('side')}")

                # Javob kutish
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        raw  = await asyncio.wait_for(ws.recv(), timeout=5)
                        data = json.loads(raw)
                        logger.info(f"WS javob: {data}")
                        ch = str(data.get("channel", ""))
                        if "order" in ch or "rs.order" in ch:
                            return data.get("data", data)
                        if "error" in ch.lower():
                            logger.error(f"WS Order xato javob: {data}")
                            return None
                    except asyncio.TimeoutError:
                        continue

                logger.error("WS Order: 15 sekundda javob kelmadi")
                return None

        except Exception as e:
            logger.error(f"WS Order exception: {type(e).__name__}: {e}")
            return None

    # ── PUBLIC ──────────────────────────────────────────────
    async def get_ticker(self, symbol: str):
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
                "symbol": symbol, "leverage": leverage,
                "openType": 1, "positionType": pos_type,
            })
        return True

    async def open_long(self, symbol: str, vol: int):
        return await self._ws_order({
            "symbol": symbol, "price": 0, "vol": vol,
            "leverage": self.leverage, "side": 1, "type": 5, "openType": 1,
        })

    async def open_short(self, symbol: str, vol: int):
        return await self._ws_order({
            "symbol": symbol, "price": 0, "vol": vol,
            "leverage": self.leverage, "side": 3, "type": 5, "openType": 1,
        })

    async def close_long(self, symbol: str, vol: int):
        return await self._ws_order({
            "symbol": symbol, "price": 0, "vol": vol,
            "leverage": self.leverage, "side": 2, "type": 5, "openType": 1,
        })

    async def close_short(self, symbol: str, vol: int):
        return await self._ws_order({
            "symbol": symbol, "price": 0, "vol": vol,
            "leverage": self.leverage, "side": 4, "type": 5, "openType": 1,
        })

    async def get_positions(self) -> list:
        r = await self._get("/api/v1/private/position/open_positions")
        return r if isinstance(r, list) else []

    async def get_position(self, symbol: str):
        for p in await self.get_positions():
            if p.get("symbol") == symbol:
                return p
        return None

    async def close(self):
        self._session.close()
