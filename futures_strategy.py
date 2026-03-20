"""
Futures Strategy v5 - SODDA VA ISHONCHLI
Muammo: oldingi versiyalar juda ko'p filtr = signal yo'q
Yechim: kam filtr, ko'p signal, tez ochadi
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FuturesSignal:
    symbol: str
    side: str
    strength: float
    reason: str
    price: float
    volume_24h: float


def ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def bollinger(prices, period=20, mult=2.0):
    if len(prices) < period:
        p = prices[-1]
        return p, p, p
    w = prices[-period:]
    mid = sum(w) / period
    std = (sum((x - mid) ** 2 for x in w) / period) ** 0.5
    return mid + mult * std, mid, mid - mult * std


class FuturesStrategy:
    def __init__(self):
        self.min_strength = 0.35   # JUDA PAST — ko'p signal uchun

    def parse_klines(self, raw):
        if not raw:
            return None
        try:
            if isinstance(raw[0], dict):
                return {
                    "open":   [float(k.get("open",  0)) for k in raw],
                    "high":   [float(k.get("high",  0)) for k in raw],
                    "low":    [float(k.get("low",   0)) for k in raw],
                    "close":  [float(k.get("close", 0)) for k in raw],
                    "volume": [float(k.get("vol",   0)) for k in raw],
                }
            else:
                return {
                    "open":   [float(k[1]) for k in raw],
                    "high":   [float(k[2]) for k in raw],
                    "low":    [float(k[3]) for k in raw],
                    "close":  [float(k[4]) for k in raw],
                    "volume": [float(k[5]) for k in raw],
                }
        except Exception as e:
            logger.error(f"Kline parse xato: {e}")
            return None

    def analyze(self, symbol: str, klines: list, ticker: dict) -> Optional[FuturesSignal]:
        if len(klines) < 30:
            return None

        c = self.parse_klines(klines)
        if not c:
            return None

        closes = c["close"]
        opens  = c["open"]
        highs  = c["high"]
        lows   = c["low"]
        vols   = c["volume"]

        price = closes[-1]
        if price <= 0:
            return None

        vol_24h = float(ticker.get("volume24", ticker.get("quoteVolume", 0)))

        # === INDIKATORLAR ===
        e5  = ema(closes, 5)
        e13 = ema(closes, 13)
        e21 = ema(closes, 21)
        rsi7 = rsi(closes, 7)
        bb_up, bb_mid, bb_lo = bollinger(closes, 20)

        # Hajm o'rtacha
        avg_vol = sum(vols[-10:]) / 10 if len(vols) >= 10 else (vols[-1] if vols else 1)
        vol_now = vols[-1] if vols else 0
        vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1

        # Momentum (3 shamlik)
        mom = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0

        # So'nggi sham
        bull = closes[-1] > opens[-1]
        prev_bull = closes[-2] > opens[-2] if len(closes) >= 2 else bull

        # === LONG ===
        ls = 0.0
        lr = []

        # EMA bullish
        if e5 > e13:
            ls += 0.15; lr.append("EMA🟢")
        if e13 > e21:
            ls += 0.10; lr.append("Trend🟢")

        # RSI oversold — eng kuchli signal
        if rsi7 < 25:
            ls += 0.30; lr.append(f"RSI{rsi7:.0f}🔥")
        elif rsi7 < 35:
            ls += 0.20; lr.append(f"RSI{rsi7:.0f}↓")
        elif rsi7 < 45:
            ls += 0.10; lr.append(f"RSI{rsi7:.0f}")

        # Bollinger pastki chiziq
        if price < bb_lo:
            ls += 0.20; lr.append("BB📉🔥")
        elif price < bb_lo * 1.01:
            ls += 0.12; lr.append("BB📉")

        # Hajm oshgan
        if vol_ratio > 1.5:
            ls += 0.12; lr.append(f"Vol{vol_ratio:.1f}x")

        # Momentum ijobiy
        if mom > 0.3:
            ls += 0.10; lr.append("Mom🟢")
        elif mom > 0:
            ls += 0.05

        # Bull sham
        if bull and prev_bull:
            ls += 0.08; lr.append("🕯🟢")

        # === SHORT ===
        ss = 0.0
        sr = []

        # EMA bearish
        if e5 < e13:
            ss += 0.15; sr.append("EMA🔴")
        if e13 < e21:
            ss += 0.10; sr.append("Trend🔴")

        # RSI overbought
        if rsi7 > 75:
            ss += 0.30; sr.append(f"RSI{rsi7:.0f}🔥")
        elif rsi7 > 65:
            ss += 0.20; sr.append(f"RSI{rsi7:.0f}↑")
        elif rsi7 > 55:
            ss += 0.10; sr.append(f"RSI{rsi7:.0f}")

        # Bollinger yuqori chiziq
        if price > bb_up:
            ss += 0.20; sr.append("BB📈🔥")
        elif price > bb_up * 0.99:
            ss += 0.12; sr.append("BB📈")

        # Hajm oshgan
        if vol_ratio > 1.5:
            ss += 0.12; sr.append(f"Vol{vol_ratio:.1f}x")

        # Momentum manfiy
        if mom < -0.3:
            ss += 0.10; sr.append("Mom🔴")
        elif mom < 0:
            ss += 0.05

        # Bear sham
        if not bull and not prev_bull:
            ss += 0.08; sr.append("🕯🔴")

        # === QAROR ===
        logger.info(f"[SIGNAL] {symbol} | LONG={ls:.2f} SHORT={ss:.2f} | RSI={rsi7:.1f} | mom={mom:.2f}%")

        if ls >= self.min_strength and ls > ss:
            return FuturesSignal(
                symbol=symbol, side="LONG",
                strength=min(ls, 1.0),
                reason=", ".join(lr),
                price=price, volume_24h=vol_24h,
            )
        elif ss >= self.min_strength and ss > ls:
            return FuturesSignal(
                symbol=symbol, side="SHORT",
                strength=min(ss, 1.0),
                reason=", ".join(sr),
                price=price, volume_24h=vol_24h,
            )

        return None
