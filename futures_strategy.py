"""
Futures Scalping Strategy - YANGILANGAN v2
- Long va Short signallar
- EMA + RSI + Bollinger + Volume + Trend
- MACD + ADX (yangi)
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


def macd(prices, fast=12, slow=26, signal=9):
    """MACD indikatori - trend o'zgarishini aniqlaydi"""
    if len(prices) < slow + signal:
        return 0, 0, 0

    macd_values = []
    for i in range(slow - 1, len(prices)):
        ef = ema(prices[:i+1], fast)
        es = ema(prices[:i+1], slow)
        macd_values.append(ef - es)

    if len(macd_values) < signal:
        return 0, 0, 0

    macd_line = macd_values[-1]
    signal_line = ema(macd_values, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def adx(highs, lows, closes, period=14):
    """ADX - trend kuchini o'lchaydi (0-100)
    25+ = kuchli trend, 20- = zaif trend"""
    if len(closes) < period * 2:
        return 0, 0, 0

    dm_plus = []
    dm_minus = []
    trs = []

    for i in range(1, len(closes)):
        h_diff = highs[i] - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        dm_plus.append(max(h_diff, 0) if h_diff > l_diff else 0)
        dm_minus.append(max(l_diff, 0) if l_diff > h_diff else 0)
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)

    def smooth(data, p):
        if len(data) < p:
            return sum(data) / len(data) if data else 0
        s = sum(data[:p])
        for d in data[p:]:
            s = s - s / p + d
        return s / p

    tr_smooth = smooth(trs, period)
    dmp_smooth = smooth(dm_plus, period)
    dmm_smooth = smooth(dm_minus, period)

    if tr_smooth == 0:
        return 0, 0, 0

    di_plus = 100 * dmp_smooth / tr_smooth
    di_minus = 100 * dmm_smooth / tr_smooth

    di_sum = di_plus + di_minus
    if di_sum == 0:
        return 0, di_plus, di_minus

    dx = 100 * abs(di_plus - di_minus) / di_sum
    return dx, di_plus, di_minus


class FuturesStrategy:
    def __init__(self):
        self.ema_fast = 5
        self.ema_mid = 13
        self.ema_slow = 21
        self.ema_trend = 50
        self.rsi_period = 7
        self.bb_period = 20
        self.min_strength = 0.60  # 0.70 dan pasaytirildi: ko'proq savdo uchun

    def parse_klines(self, raw):
        if not raw:
            return None
        try:
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
        opens = c["open"]

        price = closes[-1]
        if price <= 0:
            return None

        vol_24h = float(ticker.get("volume24", ticker.get("quoteVolume", 0)))

        # ── Asosiy indikatorlar ──────────────────────────────
        fast = ema(closes, self.ema_fast)
        mid = ema(closes, self.ema_mid)
        slow = ema(closes, self.ema_slow)
        trend = ema(closes, self.ema_trend)
        rsi_val = rsi(closes, self.rsi_period)
        bb_upper, bb_mid, bb_lower = bollinger(closes, self.bb_period)
        atr_val = atr(highs, lows, closes, 14)
        atr_pct = atr_val / price * 100

        # MACD (yangi)
        macd_line, signal_line, histogram = macd(closes, 12, 26, 9)
        macd_bullish = macd_line > signal_line
        macd_bearish = macd_line < signal_line
        macd_cross_up = histogram > 0
        macd_cross_dn = histogram < 0

        # ADX (yangi)
        adx_val, di_plus, di_minus = adx(highs, lows, closes, 14)
        trend_strong = adx_val >= 20
        trend_very_strong = adx_val >= 30

        # Pump/dump filtri - biroz kengroq
        if atr_pct > 3.5:
            return None

        # Hajm
        avg_vol = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else 1
        last_vol = volumes[-1]
        vol_spike = last_vol > avg_vol * 1.5  # 1.8 dan 1.5 ga

        # Momentum
        mom_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
        mom_1 = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0

        # Shamlar tahlil
        last_bull = closes[-1] > opens[-1]
        prev_bull = closes[-2] > opens[-2] if len(closes) >= 2 else False
        prev2_bull = closes[-3] > opens[-3] if len(closes) >= 3 else False

        # ════════════════════════════════════════════════════
        # LONG SIGNAL
        # ════════════════════════════════════════════════════
        long_score = 0.0
        long_reasons = []

        if price > trend:
            long_score += 0.12
            long_reasons.append("📈Trend")

        if fast > mid > slow:
            long_score += 0.15
            long_reasons.append("EMA🟢")
        elif fast > slow:
            long_score += 0.07

        if rsi_val < 20:
            long_score += 0.30
            long_reasons.append(f"RSI={rsi_val:.0f}🔥")
        elif rsi_val < 30:
            long_score += 0.18
            long_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val > 65:
            long_score = 0

        if price <= bb_lower * 1.003:
            long_score += 0.18
            long_reasons.append("BB📉")

        # MACD
        if macd_bullish and macd_cross_up:
            long_score += 0.18
            long_reasons.append("MACD🟢")
        elif macd_bullish:
            long_score += 0.08
            long_reasons.append("MACD+")

        # ADX
        if trend_very_strong and di_plus > di_minus:
            long_score += 0.15
            long_reasons.append(f"ADX={adx_val:.0f}💪")
        elif trend_strong and di_plus > di_minus:
            long_score += 0.08
            long_reasons.append(f"ADX={adx_val:.0f}")

        if vol_spike:
            long_score += 0.12
            long_reasons.append("Vol⬆️")

        if mom_1 > 0.1 and mom_5 > 0:
            long_score += 0.08
            long_reasons.append("Mom+")

        if last_bull and prev_bull and prev2_bull:
            long_score += 0.07
            long_reasons.append("3🕯🟢")

        # ════════════════════════════════════════════════════
        # SHORT SIGNAL
        # ════════════════════════════════════════════════════
        short_score = 0.0
        short_reasons = []

        if price < trend:
            short_score += 0.12
            short_reasons.append("📉Trend")

        if fast < mid < slow:
            short_score += 0.15
            short_reasons.append("EMA🔴")
        elif fast < slow:
            short_score += 0.07

        if rsi_val > 80:
            short_score += 0.30
            short_reasons.append(f"RSI={rsi_val:.0f}🔥")
        elif rsi_val > 70:
            short_score += 0.18
            short_reasons.append(f"RSI={rsi_val:.0f}")
        elif rsi_val < 35:
            short_score = 0

        if price >= bb_upper * 0.997:
            short_score += 0.18
            short_reasons.append("BB📈")

        # MACD
        if macd_bearish and macd_cross_dn:
            short_score += 0.18
            short_reasons.append("MACD🔴")
        elif macd_bearish:
            short_score += 0.08
            short_reasons.append("MACD-")

        # ADX
        if trend_very_strong and di_minus > di_plus:
            short_score += 0.15
            short_reasons.append(f"ADX={adx_val:.0f}💪")
        elif trend_strong and di_minus > di_plus:
            short_score += 0.08
            short_reasons.append(f"ADX={adx_val:.0f}")

        if vol_spike:
            short_score += 0.12
            short_reasons.append("Vol⬆️")

        if mom_1 < -0.1 and mom_5 < 0:
            short_score += 0.08
            short_reasons.append("Mom-")

        if not last_bull and not prev_bull and not prev2_bull:
            short_score += 0.07
            short_reasons.append("3🕯🔴")

        # ── Eng kuchli signalni tanlash ──────────────────────
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
