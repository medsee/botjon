"""
Scalping Strategy — texnik tahlil asosida signal beradi
Indikatorlar: EMA, RSI, Bollinger Bands, Volume
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    side: str           # BUY yoki SELL
    strength: float     # 0.0 – 1.0
    reason: str
    price: float
    volume_24h: float


def ema(prices: list, period: int) -> float:
    """Exponential Moving Average"""
    if len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema_val = sum(prices[:period]) / period
    for p in prices[period:]:
        ema_val = p * k + ema_val * (1 - k)
    return ema_val


def rsi(prices: list, period: int = 14) -> float:
    """Relative Strength Index"""
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger(prices: list, period: int = 20, std_mult: float = 2.0) -> tuple:
    """Bollinger Bands (upper, middle, lower)"""
    if len(prices) < period:
        p = prices[-1]
        return p, p, p
    window = prices[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    return mid + std_mult * std, mid, mid - std_mult * std


def parse_klines(raw: list) -> dict:
    """MEXC klines formatini parse qilish"""
    opens = [float(k[1]) for k in raw]
    highs = [float(k[2]) for k in raw]
    lows = [float(k[3]) for k in raw]
    closes = [float(k[4]) for k in raw]
    volumes = [float(k[5]) for k in raw]
    return {
        "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes
    }


class ScalpingStrategy:
    def __init__(self):
        # EMA davrlar
        self.ema_fast = 9
        self.ema_slow = 21
        self.rsi_period = 14
        self.bb_period = 20

        # Signal praglar
        self.rsi_oversold = 35     # BUY signal
        self.rsi_overbought = 65   # SELL signal
        self.min_signal_strength = 0.60  # Min kuch

    def analyze(self, symbol: str, klines: list, ticker: dict) -> Optional[Signal]:
        """Teknik tahlil qilib signal qaytarish"""
        if len(klines) < 25:
            return None

        candles = parse_klines(klines)
        closes = candles["close"]
        volumes = candles["volume"]

        price = closes[-1]
        vol_24h = float(ticker.get("quoteVolume", 0))

        # Indikatorlar
        fast = ema(closes, self.ema_fast)
        slow = ema(closes, self.ema_slow)
        rsi_val = rsi(closes, self.rsi_period)
        bb_upper, bb_mid, bb_lower = bollinger(closes, self.bb_period)

        # Hajm o'rtachasi
        avg_vol = sum(volumes[-10:]) / 10 if volumes else 0
        last_vol = volumes[-1] if volumes else 0
        vol_spike = last_vol > avg_vol * 1.5  # Hajm oshishi

        # Narx harakati
        price_change_3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0

        signals = []
        score = 0.0

        # BUY signallari
        buy_reasons = []
        if fast > slow:
            score += 0.25
            buy_reasons.append("EMA bullish")
        if rsi_val < self.rsi_oversold:
            score += 0.30
            buy_reasons.append(f"RSI={rsi_val:.0f} oversold")
        if price <= bb_lower * 1.002:
            score += 0.25
            buy_reasons.append("BB lower bounce")
        if vol_spike:
            score += 0.15
            buy_reasons.append("hajm spike")
        if price_change_3 > 0.1:
            score += 0.05
            buy_reasons.append("momentum+")

        # SELL signallari
        sell_score = 0.0
        sell_reasons = []
        if fast < slow:
            sell_score += 0.25
            sell_reasons.append("EMA bearish")
        if rsi_val > self.rsi_overbought:
            sell_score += 0.30
            sell_reasons.append(f"RSI={rsi_val:.0f} overbought")
        if price >= bb_upper * 0.998:
            sell_score += 0.25
            sell_reasons.append("BB upper rejection")
        if vol_spike:
            sell_score += 0.15
            sell_reasons.append("hajm spike")
        if price_change_3 < -0.1:
            sell_score += 0.05
            sell_reasons.append("momentum-")

        # Eng kuchli signalni tanlash
        if score >= self.min_signal_strength and score >= sell_score:
            return Signal(
                symbol=symbol,
                side="BUY",
                strength=min(score, 1.0),
                reason=", ".join(buy_reasons),
                price=price,
                volume_24h=vol_24h,
            )
        elif sell_score >= self.min_signal_strength and sell_score > score:
            return Signal(
                symbol=symbol,
                side="SELL",
                strength=min(sell_score, 1.0),
                reason=", ".join(sell_reasons),
                price=price,
                volume_24h=vol_24h,
            )

        return None
