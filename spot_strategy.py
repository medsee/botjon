"""
MEXC Spot Strategy - PRECISION v8
Kam savdo, faqat A+ signallar, komissiyadan 10x katta TP
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
        return closes[-1] * 0.015
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


BLACKLISTED_TOKENS = {
    "CARROT", "MEME", "PEPE", "SHIB", "FLOKI", "BONK", "WIF",
    "TURBO", "DEGEN", "NEIRO", "BOME", "MYRO", "POPCAT",
    "PONKE", "SLERF", "TRUMP", "MELANIA", "FARTCOIN", "GOAT",
    "MOODENG", "PNUT", "ACT", "AIDOGE", "BABYDOGE", "SAMO",
    "KISHU", "AKITA", "HOGE", "ELON", "CATE", "VOLT",
    "NEXFI", "REPAI", "WLFI", "TONIXAI", "BANANAS31",
}


class SpotStrategy:
    def __init__(self):
        # Faqat eng kuchli signallar
        self.min_strength = 0.60
        self.min_atr_pct  = 0.005   # Minimal 0.5% harakat
        self.max_atr_pct  = 0.06

    def analyze(self, symbol: str, klines: list, ticker: dict) -> Optional[SpotSignal]:
        if len(klines) < 50:
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
        if price <= 0 or price < 0.00001:
            return None

        base = symbol.replace("_USDT", "").upper()
        if base in BLACKLISTED_TOKENS:
            return None

        atr_val = atr(highs, lows, closes, 14)
        atr_pct = atr_val / price
        if atr_pct < self.min_atr_pct or atr_pct > self.max_atr_pct:
            return None

        # Indikatorlar
        e5  = ema(closes, 5)
        e10 = ema(closes, 10)
        e21 = ema(closes, 21)
        e50 = ema(closes, 50)

        rsi7   = rsi(closes, 7)
        rsi14  = rsi(closes, 14)
        srsi_k, srsi_d = stoch_rsi(closes, 14, 14)

        bb_up, bb_mid, bb_lo = bollinger(closes, 20, 2.0)
        bb_width = bb_up - bb_lo
        bb_pct   = (price - bb_lo) / bb_width if bb_width > 0 else 0.5

        avg_vol   = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
        vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 1

        mom3  = (closes[-1] - closes[-4])  / closes[-4]  * 100 if len(closes) >= 4  else 0
        mom10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0

        bull1 = closes[-1] > opens[-1]
        bull2 = closes[-2] > opens[-2] if len(closes) >= 2 else True
        bull3 = closes[-3] > opens[-3] if len(closes) >= 3 else True

        # ── A+ SIGNAL SHARTLARI ──────────────────────────────
        # Trend pastga bo'lsa — kirma
        if e5 < e21 < e50:
            return None
        # Juda overbought
        if rsi7 > 70:
            return None
        if rsi14 > 65:
            return None
        # BB juda yuqori
        if bb_pct > 0.80:
            return None
        # Kuchli tushish
        if mom3 < -5.0:
            return None
        if mom10 < -8.0:
            return None
        # Hajm yo'q
        if vol_ratio < 0.2:
            return None
        # Pump ichida
        if mom3 > 8.0:
            return None

        # ── BALL HISOBLASH ───────────────────────────────────
        score   = 0.0
        reasons = []

        # 1. EMA trend — muhim
        if e5 > e10 > e21 > e50:
            score += 0.20; reasons.append("EMA⬆️⬆️")
        elif e5 > e10 > e21:
            score += 0.14; reasons.append("EMA⬆️")
        elif e5 > e10:
            score += 0.07; reasons.append("EMA↗")
        elif e5 < e10:
            score -= 0.10

        # 2. RSI7 — eng muhim signal
        if rsi7 < 10:
            score += 0.45; reasons.append(f"RSI💥{rsi7:.0f}")
        elif rsi7 < 20:
            score += 0.35; reasons.append(f"RSI🔥{rsi7:.0f}")
        elif rsi7 < 30:
            score += 0.25; reasons.append(f"RSI↓{rsi7:.0f}")
        elif rsi7 < 40:
            score += 0.14; reasons.append(f"RSI{rsi7:.0f}")
        elif rsi7 < 50:
            score += 0.06
        elif rsi7 > 60:
            score -= 0.15

        # 3. RSI14 tasdiqlash
        if rsi14 < 30:
            score += 0.12; reasons.append(f"RSI14↓{rsi14:.0f}")
        elif rsi14 < 40:
            score += 0.06
        elif rsi14 > 55:
            score -= 0.08

        # 4. StochRSI
        if srsi_k < 5:
            score += 0.32; reasons.append(f"SRSI💥{srsi_k:.0f}")
        elif srsi_k < 15:
            score += 0.24; reasons.append(f"SRSI🔥{srsi_k:.0f}")
        elif srsi_k < 25:
            score += 0.15; reasons.append(f"SRSI↓{srsi_k:.0f}")
        elif srsi_k < 40:
            score += 0.07
        elif srsi_k > 75:
            score -= 0.12

        # Kesishish bonusi
        if srsi_k > srsi_d and srsi_k < 40:
            score += 0.10; reasons.append("SRSI↗")

        # 5. Bollinger
        if price < bb_lo:
            score += 0.30; reasons.append("BB💥")
        elif bb_pct < 0.08:
            score += 0.22; reasons.append("BB🔥")
        elif bb_pct < 0.20:
            score += 0.13; reasons.append("BB↓")
        elif bb_pct < 0.38:
            score += 0.05
        elif bb_pct > 0.70:
            score -= 0.10

        # 6. Hajm — tasdiqlash
        if vol_ratio > 3.5:
            score += 0.18; reasons.append(f"Vol💥{vol_ratio:.1f}x")
        elif vol_ratio > 2.5:
            score += 0.13; reasons.append(f"Vol🔥{vol_ratio:.1f}x")
        elif vol_ratio > 1.5:
            score += 0.07; reasons.append(f"Vol↑{vol_ratio:.1f}x")

        # 7. Momentum
        if 0.3 < mom3 < 4.0:
            score += 0.09; reasons.append(f"Mom↑{mom3:.1f}%")
        elif mom3 > 0:
            score += 0.03
        elif mom3 < -2.0:
            score -= 0.10

        # 8. Sham pattern — 3 ta yashil sham
        if bull1 and bull2 and bull3:
            score += 0.10; reasons.append("🕯💚💚💚")
        elif bull1 and bull2:
            score += 0.06; reasons.append("🕯💚💚")
        elif bull1:
            score += 0.02

        score = max(0.0, min(score, 1.0))

        logger.info(
            f"[SIG] {symbol} | score={score:.2f} | "
            f"RSI={rsi7:.0f}/{rsi14:.0f} SRSI={srsi_k:.0f} | "
            f"BB={bb_pct:.2f} | Vol={vol_ratio:.1f}x | "
            f"ATR={atr_pct*100:.2f}% | mom={mom3:.2f}%"
        )

        if score >= self.min_strength:
            return SpotSignal(
                symbol=symbol,
                strength=score,
                reason=", ".join(reasons[:4]),
                price=price,
                atr=atr_val,
            )
        return None
