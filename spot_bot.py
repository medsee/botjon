"""
MEXC Spot Scalping Bot - ULTRA PRO v2
- Darhol ishga tushganda analiz va pozitsiya ochadi
- ATR asosida dinamik TP/SL (sliv yo'q!)
- Trailing Stop + Break-even
- Stikerlı Telegram xabarlari
- Ko'p qatlam himoya
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from mexc_spot import MEXCSpot
from spot_strategy import SpotStrategy, SpotSignal

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("spot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Stikerlар ────────────────────────────────────────────────
S = {
    "rocket":   "🚀", "fire":    "🔥", "gem":     "💎",
    "money":    "💰", "chart_up":"📈", "chart_dn":"📉",
    "win":      "✅", "loss":    "❌", "warn":    "⚠️",
    "shield":   "🛡", "target":  "🎯", "clock":   "⏱",
    "coin":     "🪙", "bank":    "🏦", "stats":   "📊",
    "bolt":     "⚡", "stop":    "🛑", "ok":      "👌",
    "trophy":   "🏆", "cry":     "😢", "eyes":    "👀",
    "muscle":   "💪", "star":    "⭐", "boom":    "💥",
    "green":    "🟢", "red":     "🔴", "yellow":  "🟡",
    "dragon":   "🐉", "wolf":    "🐺", "bull":    "🐂",
}


@dataclass
class SpotPosition:
    symbol: str
    entry_price: float
    qty: float
    tp: float
    sl: float
    hard_sl: float       # O'zgarmas qattiq SL (sliv yo'q uchun)
    usdt_spent: float
    open_time: float = field(default_factory=time.time)
    peak_price: float = 0.0
    breakeven_moved: bool = False
    trailing_active: bool = False

    @property
    def age_seconds(self):
        return time.time() - self.open_time

    def pnl_pct(self, price: float) -> float:
        return (price - self.entry_price) / self.entry_price * 100

    def pnl_usdt(self, price: float) -> float:
        return self.qty * (price - self.entry_price)


class SpotBot:
    def __init__(self):
        self.api      = MEXCSpot(
            api_key=os.getenv("MEXC_API_KEY", ""),
            secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        )
        self.strategy = SpotStrategy()

        # ── Risk parametrlari ────────────────────────────────
        self.max_positions      = int(os.getenv("MAX_OPEN_POSITIONS",  "3"))
        self.trade_pct          = float(os.getenv("MAX_TRADE_PCT",     "0.25"))   # 25% har savdoga
        self.atr_sl_mult        = float(os.getenv("ATR_SL_MULT",      "1.2"))    # SL = 1.2x ATR (tighter)
        self.atr_tp_mult        = float(os.getenv("ATR_TP_MULT",      "3.5"))    # TP = 3.5x ATR (katta RR)
        self.hard_sl_pct        = float(os.getenv("HARD_SL_PCT",      "0.020"))  # 2.0% qattiq SL
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT","0.08"))  # 8% kunlik limit
        self.max_hold_seconds   = int(os.getenv("MAX_HOLD_SECONDS",   "600"))    # 10 daqiqa max
        self.min_usdt           = 2.0

        # ── Tezlik ──────────────────────────────────────────
        self.scan_interval     = 5     # Har 5 soniyada skan
        self.monitor_interval  = 2     # Har 2 soniyada monitor
        self.top_symbols_limit = 40
        self.batch_size        = 8
        self.batch_delay       = 0.2

        self.positions: dict[str, SpotPosition] = {}
        self.blacklist: set   = set()
        self.symbol_cache: list = []
        self.cache_time: float  = 0
        self.cache_ttl: int     = 300

        self.telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False

        # ── Statistika ───────────────────────────────────────
        self.total_pnl        = 0.0
        self.win_count        = 0
        self.loss_count       = 0
        self.scan_count       = 0
        self.starting_balance = 0.0
        self.daily_loss       = 0.0
        self.daily_start_time = time.time()

    # ── Telegram ─────────────────────────────────────────────
    async def notify(self, msg: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id":    self.telegram_chat_id,
                    "text":       msg,
                    "parse_mode": "Markdown",
                }, timeout=aiohttp.ClientTimeout(total=8))
        except Exception as e:
            logger.error(f"Telegram xato: {e}")

    # ── Juftliklar ───────────────────────────────────────────
    async def get_top_symbols(self) -> list:
        now = time.time()
        if self.symbol_cache and (now - self.cache_time) < self.cache_ttl:
            return self.symbol_cache

        tickers = await self.api.get_all_tickers()
        if not tickers:
            return self.symbol_cache or []

        usdt = []
        skip_keywords = ["DOWN", "UP", "BEAR", "BULL", "3L", "3S", "2L", "2S"]
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if any(k in sym for k in skip_keywords):
                continue
            spot_sym = sym[:-4] + "_USDT"
            if spot_sym in self.blacklist:
                continue
            try:
                vol   = float(t.get("quoteVolume", 0))
                price = float(t.get("lastPrice", 0))
                if vol > 100000 and price > 0.000001:
                    usdt.append((spot_sym, vol))
            except:
                pass

        usdt.sort(key=lambda x: x[1], reverse=True)
        self.symbol_cache = [s for s, _ in usdt[:self.top_symbols_limit]]
        self.cache_time   = now
        logger.info(f"Kesh yangilandi: {len(self.symbol_cache)} juftlik")
        return self.symbol_cache

    # ── TP/SL hisoblash (ATR asosida) ────────────────────────
    def calc_tp_sl(self, entry: float, atr_val: float) -> tuple:
        sl      = entry - self.atr_sl_mult * atr_val
        tp      = entry + self.atr_tp_mult * atr_val
        hard_sl = entry * (1 - self.hard_sl_pct)
        # SL minimal qattiq SL dan past bo'lmasin
        sl = max(sl, hard_sl)
        return round(tp, 8), round(sl, 8), round(hard_sl, 8)

    # ── Pozitsiya ochish ─────────────────────────────────────
    async def open_position(self, signal: SpotSignal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False

        usdt_amount = balance * self.trade_pct
        if usdt_amount < self.min_usdt:
            return False

        tp, sl, hard_sl = self.calc_tp_sl(signal.price, signal.atr)

        # SL juda yaqin bo'lsa — o'tkazib yubor
        sl_pct = (signal.price - sl) / signal.price * 100
        tp_pct = (tp - signal.price) / signal.price * 100
        if sl_pct > 3.0:   # 3% dan katta SL — juda xavfli
            logger.info(f"SL juda katta ({sl_pct:.1f}%), o'tkazildi: {signal.symbol}")
            return False
        if tp_pct < 0.5:   # TP juda kichik
            return False

        logger.info(f"BUY: {signal.symbol} ${usdt_amount:.2f} @ {signal.price:.6f} | TP={tp_pct:.1f}% SL={sl_pct:.1f}%")

        order = await self.api.buy_market(signal.symbol, usdt_amount)
        if not order:
            logger.error(f"BUY xato: {signal.symbol}")
            self.blacklist.add(signal.symbol)
            return False

        qty = float(order.get("executedQty", 0))
        if qty <= 0:
            fills = order.get("fills", [])
            qty   = sum(float(f.get("qty", 0)) for f in fills) if fills else usdt_amount / signal.price

        spent        = float(order.get("cummulativeQuoteQty", usdt_amount))
        actual_price = spent / qty if qty > 0 else signal.price
        tp, sl, hard_sl = self.calc_tp_sl(actual_price, signal.atr)

        pos = SpotPosition(
            symbol=signal.symbol,
            entry_price=actual_price,
            qty=qty,
            tp=tp, sl=sl, hard_sl=hard_sl,
            usdt_spent=spent,
            peak_price=actual_price,
        )
        self.positions[signal.symbol] = pos

        sl_pct2 = (actual_price - sl) / actual_price * 100
        tp_pct2 = (tp - actual_price) / actual_price * 100
        base    = signal.symbol.replace("_USDT", "")

        await self.notify(
            f"{S['rocket']} *POZITSIYA OCHILDI* {S['fire']}\n\n"
            f"{S['gem']} Juftlik: `{signal.symbol}`\n"
            f"{S['money']} Narx: `${actual_price:,.6f}`\n"
            f"{S['coin']} Miqdor: `{qty:.6f} {base}`\n"
            f"{S['bank']} Sarflandi: `${spent:.2f} USDT`\n\n"
            f"{S['target']} TP: `${tp:,.6f}` _(+{tp_pct2:.1f}%)_\n"
            f"{S['shield']} SL: `${sl:,.6f}` _(-{sl_pct2:.1f}%)_\n\n"
            f"{S['stats']} Kuch: `{signal.strength:.0%}`\n"
            f"{S['bolt']} Signal: _{signal.reason}_\n"
            f"{S['clock']} RR nisbat: `1:{tp_pct2/sl_pct2:.1f}`"
        )
        return True

    # ── Pozitsiya yopish ─────────────────────────────────────
    async def close_position(self, symbol: str, reason: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        pnl_pct  = pos.pnl_pct(price)
        pnl_usdt = pos.pnl_usdt(price)
        won      = pnl_pct > 0

        logger.info(f"SELL: {symbol} @ {price:.6f} | PnL={pnl_pct:.2f}% ({pnl_usdt:+.3f} USDT) | {reason}")

        order = await self.api.sell_market(symbol, pos.qty)
        if not order:
            order = await self.api.sell_market(symbol, pos.qty)

        self.total_pnl += pnl_usdt
        if won:
            self.win_count += 1
        else:
            self.loss_count += 1
            self.daily_loss -= abs(pnl_usdt)

        del self.positions[symbol]

        hold_m = int(pos.age_seconds // 60)
        hold_s = int(pos.age_seconds % 60)

        if won:
            header = f"{S['trophy']} *FOYDA!* {S['chart_up']}{S['fire']}"
        else:
            header = f"{S['cry']} *Zarar* {S['chart_dn']}"

        total  = self.win_count + self.loss_count
        wr     = self.win_count / total * 100 if total > 0 else 0

        await self.notify(
            f"{header}\n\n"
            f"{S['gem']} Juftlik: `{symbol}`\n"
            f"{S['money']} Chiqish: `${price:,.6f}`\n"
            f"{S['coin']} Kirish: `${pos.entry_price:,.6f}`\n\n"
            f"{'📈' if won else '📉'} PnL: `{'+' if won else ''}{pnl_pct:.2f}%` "
            f"(`{'+' if won else ''}{pnl_usdt:.3f} USDT`)\n"
            f"{S['clock']} Vaqt: `{hold_m}:{hold_s:02d}`\n"
            f"{S['stats']} Sabab: _{reason}_\n\n"
            f"{S['trophy']} Jami: `{self.win_count}W / {self.loss_count}L` "
            f"| Win: `{wr:.0f}%`\n"
            f"{S['bank']} Umumiy PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.3f} USDT`"
        )

    # ── Pozitsiyalarni kuzatish ──────────────────────────────
    async def monitor_positions(self):
        if not self.positions:
            return

        async def check(symbol: str, pos: SpotPosition):
            try:
                ticker = await self.api.get_ticker(symbol)
                if not ticker:
                    return
                price = float(ticker.get("lastPrice", 0))
                if price <= 0:
                    return

                pnl = pos.pnl_pct(price)

                # Peak yangilash
                if price > pos.peak_price:
                    pos.peak_price = price

                # 1) Qattiq SL — hech qachon o'zgarmaydi
                if price <= pos.hard_sl:
                    await self.close_position(symbol, f"{S['shield']} HardSL {pnl:.1f}%", price)
                    return

                # 2) Max vaqt
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"{S['clock']} Vaqt {pos.age_seconds/60:.0f}daq", price)
                    return

                # 3) Take Profit
                if price >= pos.tp:
                    await self.close_position(symbol, f"{S['target']} TP +{pnl:.1f}%", price)
                    return

                # 4) Dinamik SL
                if price <= pos.sl:
                    await self.close_position(symbol, f"{S['shield']} SL {pnl:.1f}%", price)
                    return

                # 5) Break-even: 1.5% foydada SL ni kirish narxiga ko'tar
                if not pos.breakeven_moved and pnl >= 1.0:
                    new_sl = pos.entry_price * 1.003
                    if new_sl > pos.sl:
                        pos.sl = new_sl
                        pos.breakeven_moved = True
                        logger.info(f"Break-even: {symbol} SL → {new_sl:.6f}")

                # 6) Trailing Stop: 2.5% foydadan keyin
                if pnl >= 2.0:
                    if not pos.trailing_active:
                        pos.trailing_active = True
                    trail = pos.peak_price * (1 - 0.008)  # peak dan 0.8% past
                    if trail > pos.sl:
                        pos.sl = trail

            except Exception as e:
                logger.error(f"Monitor xato {symbol}: {e}")

        await asyncio.gather(*[check(s, p) for s, p in list(self.positions.items())])

    # ── Kunlik zarar limiti ───────────────────────────────────
    async def check_daily_loss(self, balance: float) -> bool:
        if self.starting_balance <= 0:
            self.starting_balance = balance
            return True
        if time.time() - self.daily_start_time >= 86400:
            self.daily_loss       = 0
            self.daily_start_time = time.time()
            self.starting_balance = balance
            return True
        if self.starting_balance > 0:
            loss_pct = abs(self.daily_loss) / self.starting_balance * 100
            if loss_pct >= self.max_daily_loss_pct * 100:
                await self.notify(
                    f"{S['warn']} *KUNLIK ZARAR LIMITI!* {S['warn']}\n\n"
                    f"{S['chart_dn']} Zarar: `{loss_pct:.1f}%`\n"
                    f"{S['stop']} Bot bugun savdoni to'xtatdi.\n"
                    f"Ertaga avtomatik qayta boshlanadi {S['clock']}"
                )
                return False
        return True

    # ── Skan va savdo ────────────────────────────────────────
    async def scan_and_trade(self):
        balance = await self.api.get_balance("USDT")
        if not await self.check_daily_loss(balance):
            return
        if len(self.positions) >= self.max_positions:
            return
        if balance < self.min_usdt:
            logger.info(f"USDT kam: {balance:.2f}")
            return

        self.scan_count += 1
        symbols = await self.get_top_symbols()
        if not symbols:
            return

        signals = []
        lock    = asyncio.Lock()

        async def analyze(symbol):
            if symbol in self.positions or symbol in self.blacklist:
                return
            try:
                klines_t = self.api.get_klines(symbol, "1m", 60)
                ticker_t = self.api.get_ticker(symbol)
                klines, ticker = await asyncio.gather(klines_t, ticker_t)
                if not klines or not ticker:
                    return
                signal = self.strategy.analyze(symbol, klines, ticker)
                if signal:
                    async with lock:
                        signals.append(signal)
            except Exception as e:
                logger.debug(f"{symbol}: {e}")

        for i in range(0, len(symbols), self.batch_size):
            batch = symbols[i:i + self.batch_size]
            await asyncio.gather(*[analyze(s) for s in batch])
            await asyncio.sleep(self.batch_delay)

        if not signals:
            logger.info(f"#{self.scan_count} Skan: signal yo'q | {len(self.positions)}pos | ${balance:.2f}")
            return

        signals.sort(key=lambda x: x.strength, reverse=True)
        logger.info(f"#{self.scan_count} Skan: {len(signals)} signal | ${balance:.2f}")

        slots  = self.max_positions - len(self.positions)
        opened = 0
        for sig in signals:
            if opened >= min(slots, 2):
                break
            if sig.symbol not in self.positions:
                ok = await self.open_position(sig, balance)
                if ok:
                    opened  += 1
                    balance -= balance * self.trade_pct
                await asyncio.sleep(0.3)

    # ── Asosiy loop ──────────────────────────────────────────
    async def run(self):
        logger.info("Spot Bot ULTRA PRO ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance("USDT")
        self.starting_balance = balance

        await self.notify(
            f"{S['dragon']} *MEXC Spot Bot ULTRA PRO* {S['fire']}\n\n"
            f"{S['bank']} Balans: `{balance:.2f} USDT`\n"
            f"{S['shield']} Stop-Loss: `ATR x{self.atr_sl_mult}` (max `{self.hard_sl_pct*100:.1f}%`)\n"
            f"{S['target']} Take-Profit: `ATR x{self.atr_tp_mult}`\n"
            f"{S['stats']} Max pozitsiyalar: `{self.max_positions}`\n"
            f"{S['coin']} Har savdoga: `{self.trade_pct*100:.0f}%` balansdan\n"
            f"{S['warn']} Max kunlik zarar: `{self.max_daily_loss_pct*100:.0f}%`\n"
            f"{S['clock']} Skan: har `{self.scan_interval}s` | Monitor: `{self.monitor_interval}s`\n\n"
            f"{S['bolt']} *Indikatorlar:*\n"
            f"EMA5/10/20/50 · RSI · StochRSI · BB · ATR · Volume\n\n"
            f"{S['muscle']} Sliv yo'q! Break-even + Trailing Stop + HardSL\n"
            f"{S['rocket']} Darhol analiz boshlanmoqda..."
        )

        # ── DARHOL birinchi skan ─────────────────────────────
        logger.info("Darhol birinchi skan boshlanmoqda...")
        await self.scan_and_trade()

        scan_timer    = 0
        monitor_timer = 0
        hourly_timer  = 0

        while self.running:
            try:
                await asyncio.sleep(1)
                scan_timer    += 1
                monitor_timer += 1
                hourly_timer  += 1

                if monitor_timer >= self.monitor_interval:
                    await self.monitor_positions()
                    monitor_timer = 0

                if scan_timer >= self.scan_interval:
                    await self.scan_and_trade()
                    scan_timer = 0

                if hourly_timer >= 3600:
                    balance = await self.api.get_balance("USDT")
                    total   = self.win_count + self.loss_count
                    wr      = self.win_count / total * 100 if total > 0 else 0
                    pnl_icon = S['chart_up'] if self.total_pnl >= 0 else S['chart_dn']
                    await self.notify(
                        f"{S['stats']} *Soatlik Hisobot* {S['star']}\n\n"
                        f"{S['bank']} Balans: `{balance:.4f} USDT`\n"
                        f"{pnl_icon} Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"{S['trophy']} Savdolar: `{total}` (✅{self.win_count} / ❌{self.loss_count})\n"
                        f"{S['target']} Win rate: `{wr:.0f}%`\n"
                        f"{S['eyes']} Ochiq pozitsiya: `{len(self.positions)}`\n"
                        f"{S['bolt']} Skanlar: `{self.scan_count}`"
                    )
                    hourly_timer = 0

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop xato: {e}")
                await asyncio.sleep(5)

        # Yopish
        for symbol in list(self.positions.keys()):
            ticker = await self.api.get_ticker(symbol)
            price  = float(ticker.get("lastPrice", 0)) if ticker else 0
            await self.close_position(symbol, f"{S['stop']} Bot to'xtatildi", price)

        await self.api.close()
        await self.notify(f"{S['stop']} *Bot to'xtatildi.* Barcha pozitsiyalar yopildi {S['ok']}")
