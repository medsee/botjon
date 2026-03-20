"""
Futures Scalping Strategy - v4 FIX
- Bug tuzatildi: RSI 35-68 oraligida ham signal beradi
- min_strength = 0.45 (real bozor uchun)
- vol_24h filtri olib tashlandi (MEXC futures hajm formati har xil)
- Toza, ishonchli kod
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


# ─── Indikatorlar ────────────────────────────────────────────────────────────

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


def macd_calc(prices, fast=12, slow=26, signal=9):
    """MACD + histogram + prev_histogram (kesishish uchun)"""
    if len(prices) < slow + signal:
        return 0, 0, 0, 0
    macd_vals = []
    for i in range(slow - 1, len(prices)):
        ef = ema(prices[:i + 1], fast)
        es = ema(prices[:i + 1], slow)
        macd_vals.append(ef - es)
    if len(macd_vals) < signal:
        return 0, 0, 0, 0
    ml = macd_vals[-1]
    prev_ml = macd_vals[-2] if len(macd_vals) >= 2 else ml
    sl = ema(macd_vals, signal)
    hist = ml - sl
    prev_sl = ema(macd_vals[:-1], signal) if len(macd_vals) > signal else sl
    prev_hist = prev_ml - prev_sl
    return ml, sl, hist, prev_hist


def adx_calc(highs, lows, closes, period=14):
    """ADX + DI+ + DI-"""
    if len(closes) < period * 2:
        return 0, 0, 0
    dmp, dmm, trs = [], [], []
    for i in range(1, len(closes)):
        hd = highs[i] - highs[i - 1]
        ld = lows[i - 1] - lows[i]
        dmp.append(max(hd, 0) if hd > ld else 0)
        dmm.append(max(ld, 0) if ld > hd else 0)
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)

    def smth(data, p):
        if not data:
            return 0
        if len(data) < p:
            return sum(data) / len(data)
        s = sum(data[:p])
        for d in data[p:]:
            s = s - s / p + d
        return s / p

    tr_s  = smth(trs, period)
    dmp_s = smth(dmp, period)
    dmm_s = smth(dmm, period)
    if tr_s == 0:
        return 0, 0, 0
    di_p = 100 * dmp_s / tr_s
    di_m = 100 * dmm_s / tr_s
    di_sum = di_p + di_m
    dx = 100 * abs(di_p - di_m) / di_sum if di_sum else 0
    return dx, di_p, di_m


# ─── Strategy ────────────────────────────────────────────────────────────────

class FuturesStrategy:
    def __init__(self):
        self.ema_fast   = 5
        self.ema_mid    = 13
        self.ema_slow   = 21
        self.ema_trend  = 50
        self.rsi_period = 7
        self.bb_period  = 20
        self.min_strength = 0.45   # Real bozor uchun optimallashtirilgan

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

        # ── Indikatorlar ──────────────────────────────────────
        fast  = ema(closes, self.ema_fast)
        mid   = ema(closes, self.ema_mid)
        slow_ = ema(closes, self.ema_slow)
        trend = ema(closes, self.ema_trend)
        rsi_v = rsi(closes, self.rsi_period)
        bb_up, bb_mid, bb_lo = bollinger(closes, self.bb_period)
        atr_v   = atr(highs, lows, closes, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0

        ml, sl, hist, prev_hist = macd_calc(closes)
        adx_v, di_p, di_m      = adx_calc(highs, lows, closes)

        # ── Faqat pump/dump filtri (qolganlarini olib tashladik) ──
        if atr_pct > 5.0:
            return None
        if price <= 0:
            return None

        # ── Hajm ─────────────────────────────────────────────
        avg_vol   = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else (volumes[-1] if volumes else 1)
        last_vol  = volumes[-1] if volumes else 0
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
        vol_up    = vol_ratio > 1.3

        # ── Momentum ─────────────────────────────────────────
        mom_3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0
        mom_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        # ── Shamlar ───────────────────────────────────────────
        bull_1 = closes[-1] > opens[-1]
        bull_2 = closes[-2] > opens[-2] if len(closes) >= 2 else False
        bull_3 = closes[-3] > opens[-3] if len(closes) >= 3 else False

        # ── MACD kesishish ────────────────────────────────────
        macd_cross_up = hist > 0 and prev_hist <= 0
        macd_cross_dn = hist < 0 and prev_hist >= 0
        macd_bull = ml > sl
        macd_bear = ml < sl

        # ── ADX ──────────────────────────────────────────────
        trend_ok   = adx_v >= 15
        trend_strong = adx_v >= 25

        # ══════════════════════════════════════════════════════
        # LONG hisoblash
        # ══════════════════════════════════════════════════════
        ls = 0.0
        lr = []

        # Trend
        if price > trend:
            ls += 0.10; lr.append("📈Trend")
        if fast > mid > slow_:
            ls += 0.12; lr.append("EMA🟢")
        elif fast > slow_:
            ls += 0.06

        # RSI — faqat overbought holatda LONG bloklanadi
        if rsi_v < 20:
            ls += 0.25; lr.append(f"RSI{rsi_v:.0f}🔥")
        elif rsi_v < 30:
            ls += 0.15; lr.append(f"RSI{rsi_v:.0f}↓")
        elif rsi_v < 40:
            ls += 0.08; lr.append(f"RSI{rsi_v:.0f}")
        elif rsi_v > 72:
            ls -= 0.20  # Penalti, lekin bloklamaydi

        # Bollinger
        if price <= bb_lo * 1.005:
            ls += 0.15; lr.append("BB📉")
        elif price <= bb_lo * 1.02:
            ls += 0.07

        # MACD
        if macd_cross_up:
            ls += 0.18; lr.append("MACD✂️🟢")
        elif macd_bull:
            ls += 0.08; lr.append("MACD🟢")

        # ADX
        if trend_strong and di_p > di_m:
            ls += 0.12; lr.append(f"ADX{adx_v:.0f}💪")
        elif trend_ok and di_p > di_m:
            ls += 0.06; lr.append(f"ADX{adx_v:.0f}")

        # Hajm
        if vol_up:
            ls += 0.10; lr.append(f"Vol{vol_ratio:.1f}x⬆️")

        # Momentum
        if mom_3 > 0.2 and mom_5 > 0:
            ls += 0.08; lr.append("Mom🟢")
        elif mom_3 > 0.05:
            ls += 0.04

        # Shamlar
        if bull_1 and bull_2 and bull_3:
            ls += 0.06; lr.append("3🕯🟢")

        # ══════════════════════════════════════════════════════
        # SHORT hisoblash
        # ══════════════════════════════════════════════════════
        ss = 0.0
        sr = []

        # Trend
        if price < trend:
            ss += 0.10; sr.append("📉Trend")
        if fast < mid < slow_:
            ss += 0.12; sr.append("EMA🔴")
        elif fast < slow_:
            ss += 0.06

        # RSI — faqat oversold holatda SHORT bloklanadi
        if rsi_v > 80:
            ss += 0.25; sr.append(f"RSI{rsi_v:.0f}🔥")
        elif rsi_v > 70:
            ss += 0.15; sr.append(f"RSI{rsi_v:.0f}↑")
        elif rsi_v > 60:
            ss += 0.08; sr.append(f"RSI{rsi_v:.0f}")
        elif rsi_v < 28:
            ss -= 0.20  # Penalti

        # Bollinger
        if price >= bb_up * 0.995:
            ss += 0.15; sr.append("BB📈")
        elif price >= bb_up * 0.98:
            ss += 0.07

        # MACD
        if macd_cross_dn:
            ss += 0.18; sr.append("MACD✂️🔴")
        elif macd_bear:
            ss += 0.08; sr.append("MACD🔴")

        # ADX
        if trend_strong and di_m > di_p:
            ss += 0.12; sr.append(f"ADX{adx_v:.0f}💪")
        elif trend_ok and di_m > di_p:
            ss += 0.06; sr.append(f"ADX{adx_v:.0f}")

        # Hajm
        if vol_up:
            ss += 0.10; sr.append(f"Vol{vol_ratio:.1f}x⬆️")

        # Momentum
        if mom_3 < -0.2 and mom_5 < 0:
            ss += 0.08; sr.append("Mom🔴")
        elif mom_3 < -0.05:
            ss += 0.04

        # Shamlar
        if not bull_1 and not bull_2 and not bull_3:
            ss += 0.06; sr.append("3🕯🔴")

        # ── Qaror ─────────────────────────────────────────────
        ls = max(ls, 0)
        ss = max(ss, 0)

        logger.debug(f"{symbol}: LONG={ls:.2f} SHORT={ss:.2f} RSI={rsi_v:.1f}")

        if ls >= self.min_strength and ls >= ss:
            return FuturesSignal(
                symbol=symbol, side="LONG",
                strength=min(ls, 1.0),
                reason=", ".join(lr) if lr else "Mix",
                price=price, volume_24h=vol_24h,
            )
        elif ss >= self.min_strength and ss > ls:
            return FuturesSignal(
                symbol=symbol, side="SHORT",
                strength=min(ss, 1.0),
                reason=", ".join(sr) if sr else "Mix",
                price=price, volume_24h=vol_24h,
            )

        return None
