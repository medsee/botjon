"""
MEXC Futures Scalping Bot - v3 TURBO
- 3x Leverage
- Long + Short
- Tez skan: har 15 soniyada
- Darhol pozitsiya ochadi
- Sliv yo'q: kuchli himoya
- Trailing Stop + Break-even
- Parallel monitoring
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from mexc_futures import MEXCFutures
from futures_strategy import FuturesStrategy, FuturesSignal

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("futures.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


@dataclass
class FuturesPosition:
    symbol: str
    side: str
    entry_price: float
    vol: int
    tp: float
    sl: float
    open_time: float = field(default_factory=time.time)
    peak_price: float = 0.0
    usdt_margin: float = 0.0
    leverage: int = 3
    breakeven_moved: bool = False   # Break-even bir marta ko'chirilsin

    @property
    def age_seconds(self):
        return time.time() - self.open_time

    def pnl_pct(self, current_price):
        if self.side == "LONG":
            return (current_price - self.entry_price) / self.entry_price * 100 * self.leverage
        else:
            return (self.entry_price - current_price) / self.entry_price * 100 * self.leverage


class FuturesBot:
    def __init__(self):
        self.api = MEXCFutures(
            api_key=os.getenv("MEXC_API_KEY", ""),
            secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        )
        self.strategy = FuturesStrategy()

        # ── Risk parametrlari ────────────────────────────────
        self.leverage        = 3      # 3x (yaxshi balans: xavfsiz + foydali)
        self.max_positions   = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
        self.trade_pct       = float(os.getenv("MAX_TRADE_PCT", "0.06"))       # 6% margin
        self.stop_loss_pct   = float(os.getenv("STOP_LOSS_PCT",   "0.008"))    # 0.8% SL (tight)
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", "0.020"))    # 2.0% TP → 2.5:1 RR
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.06"))  # 6%
        self.max_hold_seconds   = int(os.getenv("MAX_HOLD_SECONDS", "240"))    # 4 daqiqa max

        # ── Tezlik sozlamalari ───────────────────────────────
        self.scan_interval   = 10     # Har 10 soniyada skan
        self.monitor_interval = 3     # Har 3 soniyada pozitsiya tekshiruvi (eski: 5)
        self.top_symbols_limit = 40   # Ko'proq juftlik (eski: 30)
        self.batch_size      = 8      # Parallel tahlil batch (eski: 5)
        self.batch_delay     = 0.3    # Batch orasidagi kutish

        self.positions: dict[str, FuturesPosition] = {}
        self.blacklist: set  = set()
        self.symbol_cache: list = []        # Juftliklar keshi
        self.cache_time: float = 0          # Kesh yangilanish vaqti
        self.cache_ttl: int = 300           # 5 daqiqada bir yangilanadi

        self.telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False

        # ── Statistika ───────────────────────────────────────
        self.total_pnl       = 0.0
        self.win_count       = 0
        self.loss_count      = 0
        self.scan_count      = 0
        self.starting_balance = 0.0
        self.daily_loss      = 0.0
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
                    "chat_id": self.telegram_chat_id,
                    "text": msg,
                    "parse_mode": "Markdown",
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            logger.error(f"Telegram xato: {e}")

    # ── Juftliklar ro'yxati (kesh bilan) ─────────────────────
    async def get_top_symbols(self) -> list:
        now = time.time()
        if self.symbol_cache and (now - self.cache_time) < self.cache_ttl:
            return self.symbol_cache  # Keshdan qaytarish

        tickers = await self.api.get_all_tickers()
        if not tickers:
            return self.symbol_cache or []

        # USDT juftliklari, hajm bo'yicha saralash
        usdt = []
        for t in tickers:
            sym = t.get("symbol", "")
            if ("_USDT" in sym or sym.endswith("USDT")) and sym not in self.blacklist:
                try:
                    vol = float(t.get("volume24", t.get("amount24", 0)))
                    if vol > 0:
                        usdt.append((sym, vol))
                except:
                    pass

        usdt.sort(key=lambda x: x[1], reverse=True)
        self.symbol_cache = [s for s, _ in usdt[:self.top_symbols_limit]]
        self.cache_time = now
        logger.info(f"Juftlik keshi yangilandi: {len(self.symbol_cache)} ta")
        return self.symbol_cache

    # ── Hisob-kitoblar ───────────────────────────────────────
    def calc_vol(self, balance: float, price: float) -> int:
        margin   = balance * self.trade_pct
        notional = margin * self.leverage
        vol      = notional / price
        return max(1, int(vol))

    def calc_tp_sl(self, entry: float, side: str) -> tuple:
        if side == "LONG":
            tp = entry * (1 + self.take_profit_pct)
            sl = entry * (1 - self.stop_loss_pct)
        else:
            tp = entry * (1 - self.take_profit_pct)
            sl = entry * (1 + self.stop_loss_pct)
        return round(tp, 6), round(sl, 6)

    # ── Pozitsiya ochish ──────────────────────────────────────
    async def open_position(self, signal: FuturesSignal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        if balance < 5:
            return False

        # Leverage o'rnatish (xato bo'lsa ham davom etamiz)
        try:
            await self.api.set_leverage(signal.symbol, self.leverage)
        except:
            pass

        vol    = self.calc_vol(balance, signal.price)
        tp, sl = self.calc_tp_sl(signal.price, signal.side)
        margin = balance * self.trade_pct

        logger.info(f"OPEN {signal.side}: {signal.symbol} vol={vol} @ {signal.price:.6f} | strength={signal.strength:.0%}")

        if signal.side == "LONG":
            order = await self.api.open_long(signal.symbol, vol)
        else:
            order = await self.api.open_short(signal.symbol, vol)

        if not order:
            logger.error(f"Order xato: {signal.symbol}")
            self.blacklist.add(signal.symbol)
            return False

        pos = FuturesPosition(
            symbol=signal.symbol,
            side=signal.side,
            entry_price=signal.price,
            vol=vol, tp=tp, sl=sl,
            peak_price=signal.price,
            usdt_margin=margin,
            leverage=self.leverage,
        )
        self.positions[signal.symbol] = pos

        icon = "🟢" if signal.side == "LONG" else "🔴"
        emoji = "🚀 LONG" if signal.side == "LONG" else "🩸 SHORT"
        await self.notify(
            f"{icon} *{emoji} OCHILDI* {icon}\n"
            f"💎 `{signal.symbol}`\n"
            f"⚡ Leverage: `{self.leverage}x`\n"
            f"💰 Narx: `${signal.price:,.6f}`\n"
            f"📦 Hajm: `{vol}` kontakt\n"
            f"🏦 Margin: `${margin:.2f} USDT`\n"
            f"🎯 TP: `${tp:,.6f}` | 🛡 SL: `${sl:,.6f}`\n"
            f"📊 Kuch: `{signal.strength:.0%}` | _{signal.reason}_"
        )
        return True

    # ── Pozitsiya yopish ──────────────────────────────────────
    async def close_position(self, symbol: str, reason: str, current_price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        if pos.side == "LONG":
            order = await self.api.close_long(symbol, pos.vol)
        else:
            order = await self.api.close_short(symbol, pos.vol)

        if not order:
            logger.error(f"Yopish xato: {symbol}")
            return

        pnl_pct  = pos.pnl_pct(current_price)
        pnl_usdt = pos.usdt_margin * pnl_pct / 100
        self.total_pnl  += pnl_usdt
        self.daily_loss += min(pnl_usdt, 0)

        if pnl_usdt >= 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        del self.positions[symbol]

        total = self.win_count + self.loss_count
        wr    = self.win_count / total * 100 if total > 0 else 0
        sign  = "+" if pnl_usdt >= 0 else ""

        icon   = "💚" if pnl_usdt >= 0 else "🔴"
        result = "✅ FOYDA" if pnl_usdt >= 0 else "❌ ZARAR"

        logger.info(f"CLOSE {symbol}: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%) | {reason}")
        await self.notify(
            f"{icon} *{result}* {icon}\n"
            f"💎 `{symbol}` ({pos.side})\n"
            f"📝 Sabab: _{reason}_\n"
            f"📈 `${pos.entry_price:,.6f}` → `${current_price:,.6f}`\n"
            f"💰 PnL: `{sign}{pnl_usdt:.4f} USDT` ({sign}{pnl_pct:.2f}%)\n"
            f"🏦 Jami: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
            f"🎯 Win rate: `{wr:.0f}%` (✅{self.win_count}/❌{self.loss_count})"
        )

    # ── Pozitsiyalarni monitoring ─────────────────────────────
    async def monitor_positions(self):
        if not self.positions:
            return

        # Barcha tickerlarni parallel olish
        async def check(symbol, pos):
            try:
                ticker = await self.api.get_ticker(symbol)
                if not ticker:
                    return
                price = float(ticker.get("lastPrice", ticker.get("last", pos.entry_price)))
                if price <= 0:
                    return

                pnl = pos.pnl_pct(price)

                # Peak yangilash
                if pos.side == "LONG" and price > pos.peak_price:
                    pos.peak_price = price
                elif pos.side == "SHORT" and price < pos.peak_price:
                    pos.peak_price = price

                # 1) Timeout
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"⏰Timeout {self.max_hold_seconds}s", price)
                    return

                # 2) Take-Profit
                if pos.side == "LONG" and price >= pos.tp:
                    await self.close_position(symbol, f"🎯TP +{pnl:.1f}%", price)
                    return
                if pos.side == "SHORT" and price <= pos.tp:
                    await self.close_position(symbol, f"🎯TP +{pnl:.1f}%", price)
                    return

                # 3) Stop-Loss
                if pos.side == "LONG" and price <= pos.sl:
                    await self.close_position(symbol, f"🛡SL {pnl:.1f}%", price)
                    return
                if pos.side == "SHORT" and price >= pos.sl:
                    await self.close_position(symbol, f"🛡SL {pnl:.1f}%", price)
                    return

                # 4) Break-even: 0.8% foydada SL ni kirish narxiga ko'tar
                if not pos.breakeven_moved and pnl >= 0.8 * self.leverage:
                    if pos.side == "LONG":
                        new_sl = pos.entry_price * 1.001  # Biroz ustida
                        if new_sl > pos.sl:
                            pos.sl = new_sl
                            pos.breakeven_moved = True
                            logger.info(f"Break-even: {symbol} SL → {new_sl:.6f}")
                    else:
                        new_sl = pos.entry_price * 0.999
                        if new_sl < pos.sl:
                            pos.sl = new_sl
                            pos.breakeven_moved = True
                            logger.info(f"Break-even: {symbol} SL → {new_sl:.6f}")

                # 5) Trailing Stop: 1.5% foydadan keyin peak orqasida
                if pnl >= 1.5 * self.leverage:
                    trail_dist = self.stop_loss_pct * 0.6
                    if pos.side == "LONG":
                        new_sl = pos.peak_price * (1 - trail_dist)
                        if new_sl > pos.sl:
                            pos.sl = new_sl
                    else:
                        new_sl = pos.peak_price * (1 + trail_dist)
                        if new_sl < pos.sl:
                            pos.sl = new_sl

            except Exception as e:
                logger.error(f"Monitor xato {symbol}: {e}")

        await asyncio.gather(*[check(sym, pos) for sym, pos in list(self.positions.items())])

    # ── Kunlik zarar limiti ───────────────────────────────────
    async def check_daily_loss(self, balance: float) -> bool:
        if self.starting_balance <= 0:
            self.starting_balance = balance
            return True

        # Kun o'tganda reset
        if time.time() - self.daily_start_time >= 86400:
            self.daily_loss = 0
            self.daily_start_time = time.time()
            self.starting_balance = balance
            logger.info("Kunlik statistika reset qilindi")
            return True

        loss_pct = abs(self.daily_loss) / self.starting_balance * 100
        if loss_pct >= self.max_daily_loss_pct * 100:
            await self.notify(
                f"⚠️ *KUNLIK ZARAR LIMITI!* ⚠️\n"
                f"Zarar: `{loss_pct:.1f}%`\n"
                f"Bot bugun savdoni to'xtatdi.\n"
                f"Ertaga soat 00:00 da qayta boshlanadi."
            )
            return False
        return True

    # ── Skan va savdo ────────────────────────────────────────
    async def scan_and_trade(self):
        balance = await self.api.get_balance()
        if not await self.check_daily_loss(balance):
            return
        if len(self.positions) >= self.max_positions:
            return
        if balance < 5:
            logger.info(f"Balans kam: {balance:.2f} USDT")
            return

        self.scan_count += 1
        symbols = await self.get_top_symbols()
        if not symbols:
            return

        signals = []
        lock = asyncio.Lock()

        async def analyze(symbol):
            if symbol in self.positions or symbol in self.blacklist:
                return
            try:
                # Parallel klines + ticker
                klines_task = self.api.get_klines(symbol, "Min1", 50)
                ticker_task = self.api.get_ticker(symbol)
                klines, ticker = await asyncio.gather(klines_task, ticker_task)
                if not klines or not ticker:
                    return
                signal = self.strategy.analyze(symbol, klines, ticker)
                if signal:
                    async with lock:
                        signals.append(signal)
            except Exception as e:
                logger.debug(f"{symbol}: {e}")

        # Katta parallel batch — tezroq
        for i in range(0, len(symbols), self.batch_size):
            batch = symbols[i:i + self.batch_size]
            await asyncio.gather(*[analyze(s) for s in batch])
            await asyncio.sleep(self.batch_delay)

        if not signals:
            logger.info(f"#{self.scan_count} Skan: signal yo'q | {len(self.positions)}pos | ${balance:.2f}")
            return

        # Eng kuchli signallarni tanlash
        signals.sort(key=lambda x: x.strength, reverse=True)
        logger.info(f"#{self.scan_count} Skan: {len(signals)} signal topildi | ${balance:.2f}")

        # Bo'sh slot soniga qarab ochish
        slots = self.max_positions - len(self.positions)
        opened = 0
        for signal in signals:
            if opened >= min(slots, 3):  # Bir skanada max 3 ta
                break
            if signal.symbol not in self.positions:
                ok = await self.open_position(signal, balance)
                if ok:
                    opened += 1
                    balance -= balance * self.trade_pct  # Balans taxminiy kamaytirish
                await asyncio.sleep(0.3)

    # ── Asosiy loop ──────────────────────────────────────────
    async def run(self):
        logger.info("Futures bot v3 TURBO ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance()
        self.starting_balance = balance

        await self.notify(
            f"🤖 *MEXC Futures Bot v3 TURBO!* 🚀\n\n"
            f"💰 Balans: `{balance:.2f} USDT`\n"
            f"⚡ Leverage: `{self.leverage}x`\n"
            f"🛡 Stop-Loss: `{self.stop_loss_pct*100:.1f}%`\n"
            f"🎯 Take-Profit: `{self.take_profit_pct*100:.1f}%`\n"
            f"📊 Max pozitsiyalar: `{self.max_positions}`\n"
            f"⚠️ Max kunlik zarar: `{self.max_daily_loss_pct*100:.0f}%`\n"
            f"⏱ Skan: har `{self.scan_interval}s` | Monitor: har `{self.monitor_interval}s`\n"
            f"🔍 Juftliklar: `{self.top_symbols_limit}` ta\n\n"
            f"📡 EMA+RSI+SRSI+BB+MACD+ADX+Volume\n"
            f"🟢 LONG | 🔴 SHORT | Break-even | Trailing Stop"
        )

        scan_timer    = self.scan_interval   # Darhol birinchi skan
        hourly_timer  = 0
        monitor_timer = 0

        while self.running:
            try:
                now = time.time()

                # Monitor — tez-tez
                if monitor_timer >= self.monitor_interval:
                    await self.monitor_positions()
                    monitor_timer = 0

                # Skan — 15 soniyada
                if scan_timer >= self.scan_interval:
                    await self.scan_and_trade()
                    scan_timer = 0

                # Soatlik hisobot
                if hourly_timer >= 3600:
                    balance = await self.api.get_balance()
                    total = self.win_count + self.loss_count
                    wr = self.win_count / total * 100 if total > 0 else 0
                    pnl_icon = "📈" if self.total_pnl >= 0 else "📉"
                    await self.notify(
                        f"📊 *Soatlik hisobot* 📊\n\n"
                        f"🏦 Balans: `{balance:.4f} USDT`\n"
                        f"{pnl_icon} Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"🔢 Savdolar: `{total}` (✅{self.win_count}/❌{self.loss_count})\n"
                        f"🎯 Win rate: `{wr:.0f}%`\n"
                        f"💼 Ochiq: `{len(self.positions)}`\n"
                        f"🔍 Skanlar: `{self.scan_count}`"
                    )
                    hourly_timer = 0
                    self.daily_loss = 0

                await asyncio.sleep(1)
                scan_timer    += 1
                hourly_timer  += 1
                monitor_timer += 1

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop xato: {e}")
                await asyncio.sleep(5)

        # Barcha pozitsiyalarni yopish
        close_tasks = []
        for symbol in list(self.positions.keys()):
            ticker = await self.api.get_ticker(symbol)
            price = float(ticker.get("lastPrice", 0)) if ticker else 0
            close_tasks.append(self.close_position(symbol, "Bot toxtatildi", price))
        if close_tasks:
            await asyncio.gather(*close_tasks)

        await self.api.close()
        await self.notify("🔴 *Futures Bot toxtatildi.* Barcha pozitsiyalar yopildi.")
