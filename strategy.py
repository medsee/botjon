"""
Kuchaytirilgan Scalping Strategy
- Ko'proq juftlik tahlil
- Tezroq signal
- Ko'proq foyda imkoniyati
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Signal:
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
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag/al))


def bollinger(prices, period=20, mult=2.0):
    if len(prices) < period:
        p = prices[-1]
        return p, p, p
    w = prices[-period:]
    mid = sum(w) / period
    std = (sum((x-mid)**2 for x in w) / period) ** 0.5
    return mid + mult*std, mid, mid - mult*std


def macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return 0, 0
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = ema_fast - ema_slow
    return macd_line, macd_line  # simplified


def parse_klines(raw):
    return {
        "open":   [float(k[1]) for k in raw],
        "high":   [float(k[2]) for k in raw],
        "low":    [float(k[3]) for k in raw],
        "close":  [float(k[4]) for k in raw],
        "volume": [float(k[5]) for k in raw],
    }


class ScalpingStrategy:
    def __init__(self):
        self.ema_fast = 5       # Tezroq EMA
        self.ema_mid = 13
        self.ema_slow = 21
        self.rsi_period = 7     # Tezroq RSI
        self.bb_period = 15
        self.min_strength = 0.55  # Quyi chegara - ko'proq signal

    def analyze(self, symbol: str, klines: list, ticker: dict) -> Optional[Signal]:
        if len(klines) < 30:
            return None

        c = parse_klines(klines)
        closes = c["close"]
        volumes = c["volume"]
        highs = c["high"]
        lows = c["low"]

        price = closes[-1]
        vol_24h = float(ticker.get("quoteVolume", 0))

        # Indikatorlar
        fast = ema(closes, self.ema_fast)
        mid = ema(closes, self.ema_mid)
        slow = ema(closes, self.ema_slow)
        rsi_val = rsi(closes, self.rsi_period)
        bb_upper, bb_mid, bb_lower = bollinger(closes, self.bb_period)

        # Hajm tahlil
        avg_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1
        last_vol = volumes[-1]
        vol_spike = last_vol > avg_vol * 1.3

        # Momentum
        mom_3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0
        mom_1 = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0

        # Sham tahlil (bullish/bearish)
        last_candle_bull = closes[-1] > c["open"][-1]
        prev_candle_bull = closes[-2] > c["open"][-2] if len(closes) >= 2 else False

        # ── BUY signali ──────────────────────────────
        buy_score = 0.0
        buy_reasons = []

        if fast > mid > slow:           # Trend yuqoriga
            buy_score += 0.20
            buy_reasons.append("trend+")
        elif fast > slow:
            buy_score += 0.10
            buy_reasons.append("EMA+")

        if rsi_val < 30:                # Kuchli oversold
            buy_score += 0.30
            buy_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val < 40:
            buy_score += 0.15
            buy_reasons.append(f"RSI={rsi_val:.0f}")

        if price <= bb_lower * 1.003:   # BB pastki chegara
            buy_score += 0.25
            buy_reasons.append("BB_lower")

        if vol_spike:
            buy_score += 0.15
            buy_reasons.append("vol_spike")

        if mom_1 > 0.05:               # 1 ta sham momentum
            buy_score += 0.10
            buy_reasons.append("mom+")

        if last_candle_bull and prev_candle_bull:
            buy_score += 0.05
            buy_reasons.append("bulls")

        # ── SELL signali ─────────────────────────────
        sell_score = 0.0
        sell_reasons = []

        if fast < mid < slow:
            sell_score += 0.20
            sell_reasons.append("trend-")
        elif fast < slow:
            sell_score += 0.10
            sell_reasons.append("EMA-")

        if rsi_val > 70:
            sell_score += 0.30
            sell_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val > 60:
            sell_score += 0.15
            sell_reasons.append(f"RSI={rsi_val:.0f}")

        if price >= bb_upper * 0.997:
            sell_score += 0.25
            sell_reasons.append("BB_upper")

        if vol_spike:
            sell_score += 0.15
            sell_reasons.append("vol_spike")

        if mom_1 < -0.05:
            sell_score += 0.10
            sell_reasons.append("mom-")

        if not last_candle_bull and not prev_candle_bull:
            sell_score += 0.05
            sell_reasons.append("bears")

        # Eng kuchli signal
        if buy_score >= self.min_strength and buy_score >= sell_score:
            return Signal(symbol, "BUY", min(buy_score, 1.0),
                         ", ".join(buy_reasons), price, vol_24h)
        elif sell_score >= self.min_strength and sell_score > buy_score:
            return Signal(symbol, "SELL", min(sell_score, 1.0),
                         ", ".join(sell_reasons), price, vol_24h)
        return None
