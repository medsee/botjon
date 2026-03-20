"""
Futures Scalping Strategy
- Long va Short signallar
- EMA + RSI + Bollinger + Volume + Trend
- Kuchli filtrlash - sliv yo'q
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FuturesSignal:
    symbol: str
    side: str        # LONG yoki SHORT
    strength: float  # 0.0 - 1.0
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
        d = prices[i] - prices[i-1]
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


def atr(highs, lows, closes, period=14):
    """Average True Range - volatillik o'lchovi"""
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


class FuturesStrategy:
    def __init__(self):
        self.ema_fast = 5
        self.ema_mid = 13
        self.ema_slow = 21
        self.ema_trend = 50   # Trend filtri
        self.rsi_period = 7
        self.bb_period = 20
        self.min_strength = 0.70  # Juda kuchli signal kerak

    def parse_klines(self, raw):
        if not raw:
            return None
        try:
            # MEXC Futures klines formati
            if isinstance(raw[0], dict):
                return {
                    "open":   [float(k.get("open", 0)) for k in raw],
                    "high":   [float(k.get("high", 0)) for k in raw],
                    "low":    [float(k.get("low", 0)) for k in raw],
                    "close":  [float(k.get("close", 0)) for k in raw],
                    "volume": [float(k.get("vol", 0)) for k in raw],
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
        if len(klines) < 55:
            return None

        c = self.parse_klines(klines)
        if not c:
            return None

        closes = c["close"]
        highs = c["high"]
        lows = c["low"]
        volumes = c["volume"]

        price = closes[-1]
        if price <= 0:
            return None

        vol_24h = float(ticker.get("volume24", ticker.get("quoteVolume", 0)))

        # Indikatorlar
        fast = ema(closes, self.ema_fast)
        mid = ema(closes, self.ema_mid)
        slow = ema(closes, self.ema_slow)
        trend = ema(closes, self.ema_trend)
        rsi_val = rsi(closes, self.rsi_period)
        bb_upper, bb_mid, bb_lower = bollinger(closes, self.bb_period)
        atr_val = atr(highs, lows, closes, 14)
        atr_pct = atr_val / price * 100

        # Juda kuchli o'zgarish bo'lsa kirma (pump/dump)
        if atr_pct > 3.0:
            return None

        # Hajm
        avg_vol = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else 1
        last_vol = volumes[-1]
        vol_spike = last_vol > avg_vol * 1.8  # Kuchli hajm

        # Momentum
        mom_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
        mom_1 = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0

        # Shamlar tahlil
        last_bull = closes[-1] > c["open"][-1]
        prev_bull = closes[-2] > c["open"][-2] if len(closes) >= 2 else False
        prev2_bull = closes[-3] > c["open"][-3] if len(closes) >= 3 else False

        # ── LONG signali ──────────────────────────────────────
        long_score = 0.0
        long_reasons = []

        # Asosiy trend (muhim filtr)
        if price > trend:
            long_score += 0.15
            long_reasons.append("trend yuqori")

        # EMA trend
        if fast > mid > slow:
            long_score += 0.20
            long_reasons.append("EMA bullish")
        elif fast > slow:
            long_score += 0.08

        # RSI oversold
        if rsi_val < 20:
            long_score += 0.35
            long_reasons.append(f"RSI={rsi_val:.0f} kuchli")
        elif rsi_val < 30:
            long_score += 0.20
            long_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val > 65:
            long_score = 0  # Overbought - LONG kirma

        # Bollinger
        if price <= bb_lower * 1.003:
            long_score += 0.20
            long_reasons.append("BB_lower")

        # Hajm
        if vol_spike:
            long_score += 0.15
            long_reasons.append("vol+")

        # Momentum
        if mom_1 > 0.1 and mom_5 > 0:
            long_score += 0.10
            long_reasons.append("mom+")

        # 3 ta bull sham ketma-ket
        if last_bull and prev_bull and prev2_bull:
            long_score += 0.08
            long_reasons.append("3xbull")

        # ── SHORT signali ─────────────────────────────────────
        short_score = 0.0
        short_reasons = []

        # Asosiy trend
        if price < trend:
            short_score += 0.15
            short_reasons.append("trend pastki")

        # EMA trend
        if fast < mid < slow:
            short_score += 0.20
            short_reasons.append("EMA bearish")
        elif fast < slow:
            short_score += 0.08

        # RSI overbought
        if rsi_val > 80:
            short_score += 0.35
            short_reasons.append(f"RSI={rsi_val:.0f} kuchli")
        elif rsi_val > 70:
            short_score += 0.20
            short_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val < 35:
            short_score = 0  # Oversold - SHORT kirma

        # Bollinger
        if price >= bb_upper * 0.997:
            short_score += 0.20
            short_reasons.append("BB_upper")

        # Hajm
        if vol_spike:
            short_score += 0.15
            short_reasons.append("vol+")

        # Momentum
        if mom_1 < -0.1 and mom_5 < 0:
            short_score += 0.10
            short_reasons.append("mom-")

        # 3 ta bear sham
        if not last_bull and not prev_bull and not prev2_bull:
            short_score += 0.08
            short_reasons.append("3xbear")

        # Eng kuchli signalni tanlash
        if long_score >= self.min_strength and long_score >= short_score:
            return FuturesSignal(
                symbol=symbol, side="LONG",
                strength=min(long_score, 1.0),
                reason=", ".join(long_reasons),
                price=price, volume_24h=vol_24h,
            )
        elif short_score >= self.min_strength and short_score > long_score:
            return FuturesSignal(
                symbol=symbol, side="SHORT",
                strength=min(short_score, 1.0),
                reason=", ".join(short_reasons),
                price=price, volume_24h=vol_24h,
            )

        return None
