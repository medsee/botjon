"""
MEXC Spot Scalping Bot
- Leverage yo'q (1x)
- Faqat LONG (buy/sell)
- TP/SL bot ichida boshqariladi
- Trailing Stop + Break-even
- Har 10 soniyada skan
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from mexc_spot import MEXCSpot
from futures_strategy import FuturesStrategy, FuturesSignal

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


@dataclass
class SpotPosition:
    symbol: str          # masalan: BTC_USDT
    entry_price: float
    qty: float           # koin miqdori
    tp: float
    sl: float
    usdt_spent: float    # sarflangan USDT
    open_time: float = field(default_factory=time.time)
    peak_price: float = 0.0
    breakeven_moved: bool = False

    @property
    def age_seconds(self):
        return time.time() - self.open_time

    def pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price * 100


class SpotBot:
    def __init__(self):
        self.api      = MEXCSpot(
            api_key=os.getenv("MEXC_API_KEY", ""),
            secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        )
        self.strategy = FuturesStrategy()

        # ── Risk parametrlari ────────────────────────────────
        self.max_positions      = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
        self.trade_pct          = float(os.getenv("MAX_TRADE_PCT",      "0.20"))   # 20% balansdan
        self.stop_loss_pct      = float(os.getenv("STOP_LOSS_PCT",      "0.015"))  # 1.5% SL
        self.take_profit_pct    = float(os.getenv("TAKE_PROFIT_PCT",    "0.030"))  # 3.0% TP
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.10"))   # 10% kunlik zarar
        self.max_hold_seconds   = int(os.getenv("MAX_HOLD_SECONDS",     "600"))    # 10 daqiqa max

        # ── Tezlik sozlamalari ───────────────────────────────
        self.scan_interval    = 10
        self.monitor_interval = 3
        self.top_symbols_limit = 30
        self.batch_size       = 6
        self.batch_delay      = 0.3
        self.min_usdt         = 2.0   # Minimal savdo miqdori USDT

        self.positions: dict[str, SpotPosition] = {}
        self.blacklist: set  = set()
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
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            logger.error(f"Telegram xato: {e}")

    # ── Symbol → base asset ──────────────────────────────────
    def get_base_asset(self, symbol: str) -> str:
        """BTC_USDT → BTC"""
        return symbol.replace("_USDT", "").replace("USDT", "")

    # ── Juftliklar ro'yxati ──────────────────────────────────
    async def get_top_symbols(self) -> list:
        now = time.time()
        if self.symbol_cache and (now - self.cache_time) < self.cache_ttl:
            return self.symbol_cache

        tickers = await self.api.get_all_tickers()
        if not tickers:
            return self.symbol_cache or []

        usdt = []
        for t in tickers:
            sym = t.get("symbol", "")
            # Spot symbollar BTCUSDT formatida, biz BTC_USDT ga aylantiramiz
            if sym.endswith("USDT") and not sym.endswith("DOWNUSDT") and not sym.endswith("UPUSDT"):
                # Blacklist tekshiruvi
                spot_sym = sym[:-4] + "_USDT"
                if spot_sym in self.blacklist:
                    continue
                try:
                    vol = float(t.get("quoteVolume", t.get("volume", 0)))
                    price = float(t.get("lastPrice", 0))
                    # Juda arzon va juda qimmat tokenlarni o'tkazib yuborish
                    if vol > 50000 and price > 0.000001:
                        usdt.append((spot_sym, vol))
                except:
                    pass

        usdt.sort(key=lambda x: x[1], reverse=True)
        self.symbol_cache = [s for s, _ in usdt[:self.top_symbols_limit]]
        self.cache_time   = now
        logger.info(f"Juftlik keshi yangilandi: {len(self.symbol_cache)} ta")
        return self.symbol_cache

    # ── TP/SL hisoblash ──────────────────────────────────────
    def calc_tp_sl(self, entry: float) -> tuple:
        tp = entry * (1 + self.take_profit_pct)
        sl = entry * (1 - self.stop_loss_pct)
        return round(tp, 8), round(sl, 8)

    # ── Pozitsiya ochish (BUY) ───────────────────────────────
    async def open_position(self, signal: FuturesSignal, balance: float) -> bool:
        # Faqat LONG signallar (Spot da SHORT yo'q)
        if signal.side != "LONG":
            return False
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False

        usdt_amount = balance * self.trade_pct
        if usdt_amount < self.min_usdt:
            logger.info(f"Balans kam: {balance:.2f} USDT → {usdt_amount:.2f} USDT savdo uchun")
            return False

        tp, sl = self.calc_tp_sl(signal.price)

        logger.info(f"BUY: {signal.symbol} ${usdt_amount:.2f} USDT @ {signal.price:.6f} | strength={signal.strength:.0%}")

        order = await self.api.buy_market(signal.symbol, usdt_amount)
        if not order:
            logger.error(f"BUY order xato: {signal.symbol}")
            self.blacklist.add(signal.symbol)
            return False

        # Sotib olingan miqdor
        qty = float(order.get("executedQty", 0))
        if qty <= 0:
            # fills dan hisoblash
            fills = order.get("fills", [])
            if fills:
                qty = sum(float(f.get("qty", 0)) for f in fills)
            else:
                qty = usdt_amount / signal.price  # taxminiy

        actual_price = float(order.get("cummulativeQuoteQty", usdt_amount)) / qty if qty > 0 else signal.price
        tp, sl       = self.calc_tp_sl(actual_price)

        pos = SpotPosition(
            symbol=signal.symbol,
            entry_price=actual_price,
            qty=qty,
            tp=tp, sl=sl,
            usdt_spent=usdt_amount,
            peak_price=actual_price,
        )
        self.positions[signal.symbol] = pos

        await self.notify(
            f"🟢 *BUY OCHILDI* 🟢\n"
            f"💎 `{signal.symbol}`\n"
            f"💰 Narx: `${actual_price:,.6f}`\n"
            f"📦 Miqdor: `{qty:.6f}` koin\n"
            f"💵 Sarflandi: `${usdt_amount:.2f} USDT`\n"
            f"🎯 TP: `${tp:,.6f}` (+{self.take_profit_pct*100:.1f}%)\n"
            f"🛡 SL: `${sl:,.6f}` (-{self.stop_loss_pct*100:.1f}%)\n"
            f"📊 Kuch: `{signal.strength:.0%}` | _{signal.reason}_"
        )
        return True

    # ── Pozitsiya yopish (SELL) ──────────────────────────────
    async def close_position(self, symbol: str, reason: str, current_price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        pnl_pct = pos.pnl_pct(current_price)
        pnl_usdt = pos.usdt_spent * pnl_pct / 100

        logger.info(f"SELL: {symbol} @ {current_price:.6f} | PnL={pnl_pct:.2f}% | {reason}")

        order = await self.api.sell_market(symbol, pos.qty)
        if not order:
            logger.error(f"SELL order xato: {symbol}")
            # Qayta urinish
            order = await self.api.sell_market(symbol, pos.qty)

        self.total_pnl += pnl_usdt
        if pnl_pct > 0:
            self.win_count  += 1
        else:
            self.loss_count += 1
            self.daily_loss -= abs(pnl_usdt)

        del self.positions[symbol]

        icon = "✅" if pnl_pct > 0 else "❌"
        await self.notify(
            f"{icon} *SOLD* {icon}\n"
            f"💎 `{symbol}`\n"
            f"📤 Sabab: `{reason}`\n"
            f"💰 Chiqish: `${current_price:,.6f}`\n"
            f"📈 PnL: `{'+' if pnl_pct>0 else ''}{pnl_pct:.2f}%` (`{'+' if pnl_usdt>0 else ''}{pnl_usdt:.3f} USDT`)\n"
            f"⏱ Ushlanish: `{pos.age_seconds:.0f}s`"
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

                # 1) Max ushlanish vaqti
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"⏱Vaqt {pos.age_seconds:.0f}s", price)
                    return

                # 2) Take Profit
                if price >= pos.tp:
                    await self.close_position(symbol, f"🎯TP +{pnl:.1f}%", price)
                    return

                # 3) Stop Loss
                if price <= pos.sl:
                    await self.close_position(symbol, f"🛡SL {pnl:.1f}%", price)
                    return

                # 4) Break-even: 1.5% foydada SL ni kirish narxiga ko'tar
                if not pos.breakeven_moved and pnl >= 1.5:
                    new_sl = pos.entry_price * 1.002
                    if new_sl > pos.sl:
                        pos.sl = new_sl
                        pos.breakeven_moved = True
                        logger.info(f"Break-even: {symbol} SL → {new_sl:.6f}")

                # 5) Trailing Stop: 2% foydadan keyin peak orqasida
                if pnl >= 2.0:
                    trail_dist = self.stop_loss_pct * 0.6
                    new_sl = pos.peak_price * (1 - trail_dist)
                    if new_sl > pos.sl:
                        pos.sl = new_sl

            except Exception as e:
                logger.error(f"Monitor xato {symbol}: {e}")

        await asyncio.gather(*[check(sym, pos) for sym, pos in list(self.positions.items())])

    # ── Kunlik zarar limiti ───────────────────────────────────
    async def check_daily_loss(self, balance: float) -> bool:
        if self.starting_balance <= 0:
            self.starting_balance = balance
            return True

        if time.time() - self.daily_start_time >= 86400:
            self.daily_loss       = 0
            self.daily_start_time = time.time()
            self.starting_balance = balance
            logger.info("Kunlik statistika reset qilindi")
            return True

        if self.starting_balance > 0:
            loss_pct = abs(self.daily_loss) / self.starting_balance * 100
            if loss_pct >= self.max_daily_loss_pct * 100:
                await self.notify(
                    f"⚠️ *KUNLIK ZARAR LIMITI!* ⚠️\n"
                    f"Zarar: `{loss_pct:.1f}%`\n"
                    f"Bot bugun savdoni to'xtatdi."
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
            logger.info(f"USDT balans kam: {balance:.2f}")
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
                klines_task = self.api.get_klines(symbol, "1m", 50)
                ticker_task = self.api.get_ticker(symbol)
                klines, ticker = await asyncio.gather(klines_task, ticker_task)
                if not klines or not ticker:
                    return
                # Ticker formatini futures bilan moslashtirish
                ticker_adapted = {
                    "lastPrice":   ticker.get("lastPrice", 0),
                    "volume24":    ticker.get("quoteVolume", 0),
                    "quoteVolume": ticker.get("quoteVolume", 0),
                }
                signal = self.strategy.analyze(symbol, klines, ticker_adapted)
                if signal and signal.side == "LONG":  # Faqat LONG
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
        for signal in signals:
            if opened >= min(slots, 2):
                break
            if signal.symbol not in self.positions:
                ok = await self.open_position(signal, balance)
                if ok:
                    opened  += 1
                    balance -= balance * self.trade_pct
                await asyncio.sleep(0.5)

    # ── Asosiy loop ──────────────────────────────────────────
    async def run(self):
        logger.info("Spot bot ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance("USDT")
        self.starting_balance = balance

        await self.notify(
            f"🤖 *MEXC Spot Scalping Bot* 🚀\n\n"
            f"💰 USDT Balans: `{balance:.2f} USDT`\n"
            f"🛡 Stop-Loss: `{self.stop_loss_pct*100:.1f}%`\n"
            f"🎯 Take-Profit: `{self.take_profit_pct*100:.1f}%`\n"
            f"📊 Max pozitsiyalar: `{self.max_positions}`\n"
            f"💵 Har savdoga: `{self.trade_pct*100:.0f}%` balansdan\n"
            f"⚠️ Max kunlik zarar: `{self.max_daily_loss_pct*100:.0f}%`\n"
            f"⏱ Skan: har `{self.scan_interval}s`\n"
            f"🔍 Juftliklar: `{self.top_symbols_limit}` ta\n\n"
            f"📡 EMA + RSI + BB + Volume\n"
            f"🟢 LONG only | Break-even | Trailing Stop"
        )

        scan_timer    = self.scan_interval
        hourly_timer  = 0
        monitor_timer = 0

        while self.running:
            try:
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
                    pnl_icon = "📈" if self.total_pnl >= 0 else "📉"
                    await self.notify(
                        f"📊 *Soatlik hisobot*\n\n"
                        f"🏦 USDT Balans: `{balance:.4f}`\n"
                        f"{pnl_icon} Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"🔢 Savdolar: `{total}` (✅{self.win_count}/❌{self.loss_count})\n"
                        f"🎯 Win rate: `{wr:.0f}%`\n"
                        f"💼 Ochiq: `{len(self.positions)}`\n"
                        f"🔍 Skanlar: `{self.scan_count}`"
                    )
                    hourly_timer  = 0
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
        for symbol in list(self.positions.keys()):
            ticker = await self.api.get_ticker(symbol)
            price  = float(ticker.get("lastPrice", 0)) if ticker else 0
            await self.close_position(symbol, "Bot toxtatildi", price)

        await self.api.close()
        await self.notify("🔴 *Spot Bot toxtatildi.*")
