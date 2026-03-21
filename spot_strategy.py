"""
MEXC Spot Strategy - ULTRA PRO v3
Muammo: kichik TP, zaif signal
Yechim:
  - Kuchli trend konfirmatsiya (3 timeframe)
  - Minimal ATR filter (volatillik tekshiruvi)
  - RSI + StochRSI birgalikda oversold bo'lishi shart
  - BB pastidan qaytish signali
  - TP = kamida 2.5x SL (kuchli RR nisbat)
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
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


class SpotStrategy:
    def __init__(self):
        # Signal kuchi uchun minimal ball
        self.min_strength = 0.60

        # Minimal volatillik: narxning 0.3% dan kichik ATR bo'lsa o'tkazib yuboramiz
        # (juda "tekis" coinlar TP ga yetmaydi)
        self.min_atr_pct = 0.003   # 0.3%

        # Maksimal ATR: juda "vahshiy" harakatlanuvchi coinlar xavfli
        self.max_atr_pct = 0.06    # 6%

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
        if price <= 0:
            return None

        # ── Volatillik filtri ────────────────────────────────
        atr_val = atr(highs, lows, closes, 14)
        atr_pct = atr_val / price

        if atr_pct < self.min_atr_pct:
            return None   # Juda tekis — TP ga yetmaydi
        if atr_pct > self.max_atr_pct:
            return None   # Juda vahshiy — SL ga tez yetadi

        # ── Indikatorlar ─────────────────────────────────────
        e5   = ema(closes, 5)
        e10  = ema(closes, 10)
        e21  = ema(closes, 21)
        e50  = ema(closes, 50)

        rsi7  = rsi(closes, 7)
        rsi14 = rsi(closes, 14)

        srsi_k, srsi_d = stoch_rsi(closes, 14, 14)

        bb_up, bb_mid, bb_lo = bollinger(closes, 20, 2.0)
        bb_width = bb_up - bb_lo
        bb_pct   = (price - bb_lo) / bb_width if bb_width > 0 else 0.5

        avg_vol   = sum(vols[-14:]) / 14 if len(vols) >= 14 else 1
        vol_now   = vols[-1]
        vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1

        # Momentum
        mom3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0
        mom8 = (closes[-1] - closes[-9]) / closes[-9] * 100 if len(closes) >= 9 else 0

        # Shamlar
        bull1 = closes[-1] > opens[-1]
        bull2 = closes[-2] > opens[-2] if len(closes) >= 2 else True
        bull3 = closes[-3] > opens[-3] if len(closes) >= 3 else True
        body1 = abs(closes[-1] - opens[-1]) / (highs[-1] - lows[-1] + 1e-10)

        # ── SHART 1: RSI oversold bo'lishi SHART ─────────────
        # Bu eng muhim filtr — RSI > 55 bo'lsa signal bermaymiz
        if rsi7 > 55:
            return None

        # ── SHART 2: StochRSI ham past bo'lishi SHART ────────
        if srsi_k > 60:
            return None

        # ── SHART 3: Narx BB o'rtasidan past bo'lishi SHART ──
        if bb_pct > 0.55:
            return None

        # ── SHART 4: Tushib kelyotgan bo'lmasin ──────────────
        # 8 shamlik momentum juda manfiy bo'lsa — bozor tushyapti
        if mom8 < -3.0:
            return None

        # ── Ball hisoblash ───────────────────────────────────
        score   = 0.0
        reasons = []

        # 1. EMA trend — narx EMA ustida bo'lsin
        if e5 > e10 and e10 > e21:
            score += 0.15; reasons.append("EMA⬆️")
        elif e5 > e10:
            score += 0.08; reasons.append("EMA↗")
        if price > e50:
            score += 0.08; reasons.append("E50✅")

        # 2. RSI oversold (SHART o'tgani uchun qo'shimcha ball)
        if rsi7 < 20:
            score += 0.28; reasons.append(f"RSI💥{rsi7:.0f}")
        elif rsi7 < 30:
            score += 0.20; reasons.append(f"RSI🔥{rsi7:.0f}")
        elif rsi7 < 40:
            score += 0.13; reasons.append(f"RSI↓{rsi7:.0f}")
        else:
            score += 0.06; reasons.append(f"RSI{rsi7:.0f}")

        # 3. StochRSI oversold + kesishish
        if srsi_k < 15 and srsi_d < 15:
            score += 0.22; reasons.append(f"SRSI💥{srsi_k:.0f}")
        elif srsi_k < 25:
            score += 0.15; reasons.append(f"SRSI🔥{srsi_k:.0f}")
        elif srsi_k < 40:
            score += 0.09; reasons.append(f"SRSI↓{srsi_k:.0f}")
        # StochRSI kesishish (K yuqoriga D ni kesib o'tsa)
        if srsi_k > srsi_d and srsi_k < 40:
            score += 0.08; reasons.append("SRSI↗")

        # 4. Bollinger pastki chiziq
        if price < bb_lo:
            score += 0.20; reasons.append("BB💥")
        elif bb_pct < 0.15:
            score += 0.14; reasons.append("BB🔥")
        elif bb_pct < 0.30:
            score += 0.08; reasons.append("BB↓")

        # 5. Hajm tasdiqlashi
        if vol_ratio > 2.0:
            score += 0.14; reasons.append(f"Vol💥{vol_ratio:.1f}x")
        elif vol_ratio > 1.5:
            score += 0.09; reasons.append(f"Vol🔥{vol_ratio:.1f}x")
        elif vol_ratio > 1.2:
            score += 0.05; reasons.append(f"Vol↑{vol_ratio:.1f}x")

        # 6. Momentum qaytishi
        if 0.1 < mom3 < 2.0:
            score += 0.09; reasons.append(f"Mom↑{mom3:.1f}%")
        elif mom3 > 0:
            score += 0.04

        # 7. Sham pattern — oxirgi sham bull
        if bull1 and body1 > 0.5:
            score += 0.08; reasons.append("🕯💚")
        elif bull1:
            score += 0.04

        # 8. 3 ta ketma-ket tushish keyin burilish
        if not bull3 and not bull2 and bull1:
            score += 0.10; reasons.append("Reversal↗")

        # ── MINUS balllar ────────────────────────────────────
        # Hajm juda past — ishonchsiz
        if vol_ratio < 0.7:
            score -= 0.12

        # Narx EMA50 dan ancha past — kuchli tushish trendi
        if price < e50 * 0.95:
            score -= 0.10

        score = max(0.0, min(score, 1.0))

        logger.info(
            f"[SIG] {symbol} | score={score:.2f} | "
            f"RSI7={rsi7:.0f} SRSI={srsi_k:.0f} | "
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
