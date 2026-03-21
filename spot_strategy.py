"""
MEXC Spot Strategy - ULTRA PRO v2
EMA + RSI + StochRSI + Bollinger + MACD + ATR + Volume
Faqat LONG, kuchli signallar, sliv yo'q
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SpotSignal:
    symbol: str
    strength: float
    reason: str
    price: float
    atr: float


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


def stoch_rsi(prices, rsi_p=14, stoch_p=14):
    if len(prices) < rsi_p + stoch_p:
        return 50.0, 50.0
    rsi_vals = [rsi(prices[:i], rsi_p) for i in range(rsi_p, len(prices) + 1)]
    if len(rsi_vals) < stoch_p:
        return 50.0, 50.0
    recent = rsi_vals[-stoch_p:]
    lo, hi = min(recent), max(recent)
    if hi == lo:
        return 50.0, 50.0
    k = (rsi_vals[-1] - lo) / (hi - lo) * 100
    d = sum((r - lo) / (hi - lo) * 100 for r in recent[-3:]) / 3
    return round(k, 1), round(d, 1)


def bollinger(prices, period=20, mult=2.0):
    if len(prices) < period:
        p = prices[-1]
        return p, p, p
    w = prices[-period:]
    mid = sum(w) / period
    std = (sum((x - mid) ** 2 for x in w) / period) ** 0.5
    return mid + mult * std, mid, mid - mult * std


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.01
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


class SpotStrategy:
    def __init__(self):
        self.min_strength = 0.55   # Kuchli filtr — faqat ishonchli signallar

    def analyze(self, symbol: str, klines: list, ticker: dict) -> Optional[SpotSignal]:
        if len(klines) < 35:
            return None
        try:
            closes = [float(k.get("close", 0)) for k in klines]
            opens  = [float(k.get("open",  0)) for k in klines]
            highs  = [float(k.get("high",  0)) for k in klines]
            lows   = [float(k.get("low",   0)) for k in klines]
            vols   = [float(k.get("vol",   0)) for k in klines]
        except:
            return None

        price = closes[-1]
        if price <= 0:
            return None

        # ── Indikatorlar ────────────────────────────────────
        e5  = ema(closes, 5)
        e10 = ema(closes, 10)
        e20 = ema(closes, 20)
        e50 = ema(closes, 50) if len(closes) >= 50 else e20

        rsi7  = rsi(closes, 7)
        rsi14 = rsi(closes, 14)

        srsi_k, srsi_d = stoch_rsi(closes, 14, 14)

        bb_up, bb_mid, bb_lo = bollinger(closes, 20)
        atr_val = atr(highs, lows, closes, 14)

        avg_vol  = sum(vols[-10:]) / 10 if len(vols) >= 10 else 1
        vol_now  = vols[-1]
        vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1

        mom3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0
        mom5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        bull_candle  = closes[-1] > opens[-1]
        bull_candle2 = closes[-2] > opens[-2] if len(closes) >= 2 else True
        candle_body  = abs(closes[-1] - opens[-1])
        candle_range = highs[-1] - lows[-1]
        body_ratio   = candle_body / candle_range if candle_range > 0 else 0

        # BB foizi (price ning BB ichidagi joylashuvi)
        bb_width = bb_up - bb_lo
        bb_pct   = (price - bb_lo) / bb_width if bb_width > 0 else 0.5

        # ── LONG ball hisoblash ──────────────────────────────
        score = 0.0
        reasons = []

        # 1. EMA trend (muhim)
        if e5 > e10 > e20:
            score += 0.18; reasons.append("EMA⬆️")
        elif e5 > e10:
            score += 0.10; reasons.append("EMA↗")

        # 2. RSI oversold (eng kuchli signal)
        if rsi7 < 20:
            score += 0.30; reasons.append(f"RSI💥{rsi7:.0f}")
        elif rsi7 < 30:
            score += 0.22; reasons.append(f"RSI🔥{rsi7:.0f}")
        elif rsi7 < 40:
            score += 0.14; reasons.append(f"RSI↓{rsi7:.0f}")
        elif rsi7 < 50:
            score += 0.07; reasons.append(f"RSI{rsi7:.0f}")

        # 3. Stoch RSI oversold + kesishish
        if srsi_k < 20 and srsi_d < 20:
            score += 0.20; reasons.append(f"SRSI💥{srsi_k:.0f}")
        elif srsi_k < 30:
            score += 0.13; reasons.append(f"SRSI🔥{srsi_k:.0f}")
        elif srsi_k > srsi_d and srsi_k < 50:
            score += 0.08; reasons.append("SRSI↗")

        # 4. Bollinger pastki chiziq
        if price < bb_lo:
            score += 0.20; reasons.append("BB💥")
        elif bb_pct < 0.2:
            score += 0.13; reasons.append("BB🔥")
        elif bb_pct < 0.35:
            score += 0.07; reasons.append("BB↓")

        # 5. Hajm oshgan
        if vol_ratio > 2.5:
            score += 0.15; reasons.append(f"Vol💥{vol_ratio:.1f}x")
        elif vol_ratio > 1.8:
            score += 0.10; reasons.append(f"Vol🔥{vol_ratio:.1f}x")
        elif vol_ratio > 1.3:
            score += 0.06; reasons.append(f"Vol↑{vol_ratio:.1f}x")

        # 6. Momentum ijobiy
        if mom3 > 0.5:
            score += 0.10; reasons.append(f"Mom🚀{mom3:.1f}%")
        elif mom3 > 0.2:
            score += 0.06; reasons.append(f"Mom↑{mom3:.1f}%")
        elif mom3 > 0:
            score += 0.03

        # 7. Kuchli bull sham
        if bull_candle and bull_candle2 and body_ratio > 0.6:
            score += 0.10; reasons.append("🕯💚")
        elif bull_candle and body_ratio > 0.5:
            score += 0.06; reasons.append("🕯↑")

        # 8. EMA50 ustida (trend muhim)
        if price > e50:
            score += 0.08; reasons.append("Trend✅")

        # ── Sliv filtrlari (MINUS) ───────────────────────────
        # RSI overbought — xavfli
        if rsi7 > 75:
            score -= 0.25
        elif rsi7 > 65:
            score -= 0.12

        # Stoch RSI overbought
        if srsi_k > 80:
            score -= 0.15
        elif srsi_k > 70:
            score -= 0.08

        # BB yuqorida — qimmat
        if bb_pct > 0.85:
            score -= 0.20
        elif bb_pct > 0.70:
            score -= 0.10

        # Hajm juda past — ishonchsiz
        if vol_ratio < 0.5:
            score -= 0.10

        # Tushish momentumi
        if mom5 < -2.0:
            score -= 0.15
        elif mom5 < -1.0:
            score -= 0.08

        score = max(0.0, min(score, 1.0))

        logger.info(f"[SIGNAL] {symbol} | score={score:.2f} | RSI={rsi7:.0f} | SRSI={srsi_k:.0f} | BB={bb_pct:.2f} | Vol={vol_ratio:.1f}x | mom={mom3:.2f}%")

        if score >= self.min_strength:
            return SpotSignal(
                symbol=symbol,
                strength=score,
                reason=", ".join(reasons[:4]),
                price=price,
                atr=atr_val,
            )
        return None
