"""
Futures Scalping Strategy - v3 TURBO
- EMA + RSI + BB + MACD + ADX + Volume + Momentum
- Multi-timeframe tezkor tahlil
- Sliv yo'q: kuchli filtr
- Ko'proq signal: min_strength pasaytirildi, lekin sifatli
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


# ── Indikator funksiyalar ─────────────────────────────────────────────────────

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


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return 0, 0, 0
    macd_values = []
    for i in range(slow - 1, len(prices)):
        ef = ema(prices[:i + 1], fast)
        es = ema(prices[:i + 1], slow)
        macd_values.append(ef - es)
    if len(macd_values) < signal:
        return 0, 0, 0
    macd_line = macd_values[-1]
    prev_macd = macd_values[-2] if len(macd_values) >= 2 else macd_line
    signal_line = ema(macd_values, signal)
    histogram = macd_line - signal_line
    prev_histogram = prev_macd - signal_line
    return macd_line, signal_line, histogram, prev_histogram


def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return 0, 0, 0
    dm_plus, dm_minus, trs = [], [], []
    for i in range(1, len(closes)):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        dm_plus.append(max(h_diff, 0) if h_diff > l_diff else 0)
        dm_minus.append(max(l_diff, 0) if l_diff > h_diff else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)

    def smooth(data, p):
        if len(data) < p:
            return sum(data) / len(data) if data else 0
        s = sum(data[:p])
        for d in data[p:]:
            s = s - s / p + d
        return s / p

    tr_s = smooth(trs, period)
    dmp_s = smooth(dm_plus, period)
    dmm_s = smooth(dm_minus, period)
    if tr_s == 0:
        return 0, 0, 0
    di_p = 100 * dmp_s / tr_s
    di_m = 100 * dmm_s / tr_s
    di_sum = di_p + di_m
    dx = 100 * abs(di_p - di_m) / di_sum if di_sum else 0
    return dx, di_p, di_m


def stochastic_rsi(prices, rsi_period=14, stoch_period=14):
    """Stochastic RSI - overbought/oversold aniqroq"""
    if len(prices) < rsi_period + stoch_period:
        return 50.0
    rsi_values = []
    for i in range(rsi_period, len(prices)):
        rsi_values.append(rsi(prices[:i + 1], rsi_period))
    if len(rsi_values) < stoch_period:
        return 50.0
    recent = rsi_values[-stoch_period:]
    lo, hi = min(recent), max(recent)
    if hi == lo:
        return 50.0
    return 100 * (rsi_values[-1] - lo) / (hi - lo)


# ── Strategy ──────────────────────────────────────────────────────────────────

class FuturesStrategy:
    def __init__(self):
        self.ema_fast  = 5
        self.ema_mid   = 13
        self.ema_slow  = 21
        self.ema_trend = 50
        self.rsi_period = 7
        self.bb_period  = 20
        # Signal chegarasi: 0.55 — ko'proq savdo + sifat saqlanadi
        self.min_strength = 0.55

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
        if len(klines) < 55:
            return None

        c = self.parse_klines(klines)
        if not c:
            return None

        closes  = c["close"]
        highs   = c["high"]
        lows    = c["low"]
        volumes = c["volume"]
        opens   = c["open"]

        price = closes[-1]
        if price <= 0:
            return None

        vol_24h = float(ticker.get("volume24", ticker.get("quoteVolume", 0)))

        # ── Indikatorlar ─────────────────────────────────────
        fast  = ema(closes, self.ema_fast)
        mid   = ema(closes, self.ema_mid)
        slow  = ema(closes, self.ema_slow)
        trend = ema(closes, self.ema_trend)
        rsi_v = rsi(closes, self.rsi_period)
        srsi  = stochastic_rsi(closes, 14, 14)
        bb_up, bb_mid, bb_lo = bollinger(closes, self.bb_period)
        atr_v = atr(highs, lows, closes, 14)
        atr_pct = atr_v / price * 100

        macd_res = macd(closes, 12, 26, 9)
        macd_line, sig_line, hist, prev_hist = macd_res
        adx_v, di_p, di_m = adx(highs, lows, closes, 14)

        # ── Sliv filtrlari (QATTIQ) ──────────────────────────
        # 1) Juda yuqori volatillik → pump/dump → o'tkazib yubor
        if atr_pct > 4.0:
            return None
        # 2) 24s hajm juda kam bo'lsa likvidlik yo'q
        if vol_24h < 100_000:
            return None
        # 3) Narx juda kichik bo'lsa (scam coin)
        if price < 0.00001:
            return None

        # ── Yordamchi hisob-kitoblar ─────────────────────────
        avg_vol = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else 1
        last_vol = volumes[-1]
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
        vol_spike = vol_ratio > 1.4          # Hajm ko'tarildi

        # Momentumlar
        mom_3  = (closes[-1] - closes[-4])  / closes[-4]  * 100 if len(closes) >= 4  else 0
        mom_5  = (closes[-1] - closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0
        mom_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0

        # So'nggi shamlar
        last_bull  = closes[-1] > opens[-1]
        prev_bull  = closes[-2] > opens[-2] if len(closes) >= 2 else False
        prev2_bull = closes[-3] > opens[-3] if len(closes) >= 3 else False

        # MACD kesishishi (signal)
        macd_cross_up = hist > 0 and prev_hist <= 0   # MACD yuqoriga kesdi
        macd_cross_dn = hist < 0 and prev_hist >= 0   # MACD pastga kesdi
        macd_bull = macd_line > sig_line
        macd_bear = macd_line < sig_line

        # ADX trend kuchi
        strong_trend    = adx_v >= 18
        very_strong     = adx_v >= 28

        # BB squeeze: band toraygan (portlash oldidan)
        bb_width = (bb_up - bb_lo) / bb_mid if bb_mid > 0 else 0
        bb_squeeze = bb_width < 0.03         # Juda tor band

        # ════════════════════════════════════════════════════
        # LONG SIGNAL HISOBLASH
        # ════════════════════════════════════════════════════
        ls = 0.0   # long score
        lr = []    # long reasons

        # 1. Trend yo'nalishi
        if price > trend:
            ls += 0.10; lr.append("📈Trend")
        if fast > mid > slow:
            ls += 0.12; lr.append("EMA🟢")
        elif fast > slow:
            ls += 0.05

        # 2. RSI oversold
        if rsi_v < 20:
            ls += 0.25; lr.append(f"RSI{rsi_v:.0f}🔥")
        elif rsi_v < 28:
            ls += 0.15; lr.append(f"RSI{rsi_v:.0f}")
        elif rsi_v < 35:
            ls += 0.08
        elif rsi_v > 68:
            ls = 0     # Overbought - LONG kirma → return early
            return None if ls == 0 else None

        # 3. Stochastic RSI
        if srsi < 15:
            ls += 0.12; lr.append("SRSI🔥")
        elif srsi < 25:
            ls += 0.07; lr.append("SRSI-")

        # 4. Bollinger
        if price <= bb_lo * 1.005:
            ls += 0.15; lr.append("BB📉")
        elif price <= bb_lo * 1.015:
            ls += 0.07

        # 5. MACD
        if macd_cross_up:
            ls += 0.20; lr.append("MACD✂️🟢")   # Kesishish = kuchli signal
        elif macd_bull:
            ls += 0.08; lr.append("MACD🟢")

        # 6. ADX trend kuchi
        if very_strong and di_p > di_m:
            ls += 0.12; lr.append(f"ADX{adx_v:.0f}💪")
        elif strong_trend and di_p > di_m:
            ls += 0.06; lr.append(f"ADX{adx_v:.0f}")

        # 7. Hajm
        if vol_spike:
            ls += 0.10; lr.append(f"Vol{vol_ratio:.1f}x⬆️")

        # 8. Momentum
        if mom_3 > 0.15 and mom_5 > 0 and mom_10 > -1:
            ls += 0.08; lr.append("Mom🟢")
        elif mom_3 > 0.1 and mom_5 > 0:
            ls += 0.04

        # 9. Ketma-ket bull shamlar
        if last_bull and prev_bull and prev2_bull:
            ls += 0.06; lr.append("3🕯🟢")

        # 10. BB squeeze + momentum = portlash kutilmoqda
        if bb_squeeze and mom_3 > 0:
            ls += 0.08; lr.append("Squeeze🟢")

        # ════════════════════════════════════════════════════
        # SHORT SIGNAL HISOBLASH
        # ════════════════════════════════════════════════════
        ss = 0.0   # short score
        sr = []    # short reasons

        # 1. Trend yo'nalishi
        if price < trend:
            ss += 0.10; sr.append("📉Trend")
        if fast < mid < slow:
            ss += 0.12; sr.append("EMA🔴")
        elif fast < slow:
            ss += 0.05

        # 2. RSI overbought
        if rsi_v > 80:
            ss += 0.25; sr.append(f"RSI{rsi_v:.0f}🔥")
        elif rsi_v > 72:
            ss += 0.15; sr.append(f"RSI{rsi_v:.0f}")
        elif rsi_v > 65:
            ss += 0.08
        elif rsi_v < 32:
            ss = 0  # Oversold - SHORT kirma

        # 3. Stochastic RSI
        if srsi > 85:
            ss += 0.12; sr.append("SRSI🔥")
        elif srsi > 75:
            ss += 0.07; sr.append("SRSI+")

        # 4. Bollinger
        if price >= bb_up * 0.995:
            ss += 0.15; sr.append("BB📈")
        elif price >= bb_up * 0.985:
            ss += 0.07

        # 5. MACD
        if macd_cross_dn:
            ss += 0.20; sr.append("MACD✂️🔴")
        elif macd_bear:
            ss += 0.08; sr.append("MACD🔴")

        # 6. ADX
        if very_strong and di_m > di_p:
            ss += 0.12; sr.append(f"ADX{adx_v:.0f}💪")
        elif strong_trend and di_m > di_p:
            ss += 0.06; sr.append(f"ADX{adx_v:.0f}")

        # 7. Hajm
        if vol_spike:
            ss += 0.10; sr.append(f"Vol{vol_ratio:.1f}x⬆️")

        # 8. Momentum
        if mom_3 < -0.15 and mom_5 < 0 and mom_10 < 1:
            ss += 0.08; sr.append("Mom🔴")
        elif mom_3 < -0.1 and mom_5 < 0:
            ss += 0.04

        # 9. Ketma-ket bear shamlar
        if not last_bull and not prev_bull and not prev2_bull:
            ss += 0.06; sr.append("3🕯🔴")

        # 10. BB squeeze + pastga momentum
        if bb_squeeze and mom_3 < 0:
            ss += 0.08; sr.append("Squeeze🔴")

        # ── Qaror ────────────────────────────────────────────
        # RSI filtrlari qayta tekshirish
        if rsi_v > 68 and ls > 0:
            ls *= 0.3   # LONG kuchini kamaytir (overbought)
        if rsi_v < 32 and ss > 0:
            ss *= 0.3   # SHORT kuchini kamaytir (oversold)

        if ls >= self.min_strength and ls >= ss:
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
