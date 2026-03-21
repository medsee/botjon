"""
MEXC Spot Scalping Bot - BALANCED v4
Tez ochadi, tez yopadi, sliv yo'q
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

S = {
    "rocket":"🚀","fire":"🔥","gem":"💎","money":"💰",
    "chart_up":"📈","chart_dn":"📉","win":"✅","loss":"❌",
    "warn":"⚠️","shield":"🛡","target":"🎯","clock":"⏱",
    "coin":"🪙","bank":"🏦","stats":"📊","bolt":"⚡",
    "stop":"🛑","ok":"👌","trophy":"🏆","green":"🟢",
    "red":"🔴","dragon":"🐉","muscle":"💪","star":"⭐",
    "boom":"💥","eyes":"👀","cry":"😢","bull":"🐂",
}


@dataclass
class SpotPosition:
    symbol: str
    entry_price: float
    qty: float
    tp: float
    sl: float
    hard_sl: float
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
        self.max_positions      = int(os.getenv("MAX_OPEN_POSITIONS",  "4"))
        self.trade_pct          = float(os.getenv("MAX_TRADE_PCT",     "0.22"))
        self.atr_sl_mult        = 1.0    # SL = 1x ATR
        self.atr_tp_mult        = 2.5    # TP = 2.5x ATR → RR 1:2.5
        self.hard_sl_pct        = 0.015  # 1.5% o'zgarmas SL (qattiq)
        self.max_daily_loss_pct = 0.08   # 8% kunlik limit
        self.max_hold_seconds   = 300    # 5 daqiqa max
        self.min_usdt           = 2.0

        # ── Tezlik ──────────────────────────────────────────
        self.scan_interval    = 5
        self.monitor_interval = 1
        self.top_symbols_limit = 50
        self.batch_size       = 10
        self.batch_delay      = 0.15

        self.positions: dict[str, SpotPosition] = {}
        self.blacklist: set   = set()
        self.blacklist_time: dict = {}  # vaqtinchalik blacklist
        self.symbol_cache: list = []
        self.cache_time: float  = 0
        self.cache_ttl: int     = 240

        self.telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False

        self.total_pnl        = 0.0
        self.win_count        = 0
        self.loss_count       = 0
        self.scan_count       = 0
        self.starting_balance = 0.0
        self.daily_loss       = 0.0
        self.daily_start_time = time.time()

    async def notify(self, msg: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id": self.telegram_chat_id,
                    "text": msg, "parse_mode": "Markdown",
                }, timeout=aiohttp.ClientTimeout(total=8))
        except Exception as e:
            logger.error(f"Telegram: {e}")

    async def get_top_symbols(self) -> list:
        now = time.time()
        if self.symbol_cache and (now - self.cache_time) < self.cache_ttl:
            return self.symbol_cache
        tickers = await self.api.get_all_tickers()
        if not tickers:
            return self.symbol_cache or []
        skip = ["DOWN", "UP", "BEAR", "BULL", "3L", "3S", "2L", "2S"]
        usdt = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if any(k in sym for k in skip):
                continue
            spot_sym = sym[:-4] + "_USDT"
            if spot_sym in self.blacklist:
                continue
            try:
                vol   = float(t.get("quoteVolume", 0))
                price = float(t.get("lastPrice", 0))
                if vol > 50000 and price > 0.0000001:
                    usdt.append((spot_sym, vol))
            except:
                pass
        usdt.sort(key=lambda x: x[1], reverse=True)
        self.symbol_cache = [s for s, _ in usdt[:self.top_symbols_limit]]
        self.cache_time   = now
        logger.info(f"Kesh: {len(self.symbol_cache)} juftlik")
        return self.symbol_cache

    def calc_tp_sl(self, entry: float, atr_val: float):
        sl      = entry - self.atr_sl_mult * atr_val
        tp      = entry + self.atr_tp_mult * atr_val
        hard_sl = entry * (1 - self.hard_sl_pct)
        sl      = max(sl, hard_sl)
        # Minimal TP tekshiruvi: kamida 0.8% bo'lsin (komissiya uchun)
        if (tp - entry) / entry < 0.008:
            tp = entry * 1.008
        return round(tp, 8), round(sl, 8), round(hard_sl, 8)

    async def open_position(self, signal: SpotSignal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False

        # Vaqtinchalik blacklistni tekshirish (5 daqiqa)
        if signal.symbol in self.blacklist_time:
            if time.time() - self.blacklist_time[signal.symbol] < 300:
                return False
            else:
                del self.blacklist_time[signal.symbol]

        usdt_amount = balance * self.trade_pct
        # MEXC minimal savdo: 2 USDT, lekin xavfsizlik uchun 1.5 USDT dan kam bo'lmasin
        if usdt_amount < 1.5:
            return False
        usdt_amount = max(usdt_amount, 1.5)

        tp, sl, hard_sl = self.calc_tp_sl(signal.price, signal.atr)
        sl_pct = (signal.price - sl) / signal.price * 100
        tp_pct = (tp - signal.price) / signal.price * 100

        # SL juda katta bo'lsa o'tkazib yuborish
        if sl_pct > 2.0:
            return False

        logger.info(f"BUY: {signal.symbol} ${usdt_amount:.2f} @ {signal.price:.6f} TP={tp_pct:.1f}% SL={sl_pct:.1f}%")

        order = await self.api.buy_market(signal.symbol, usdt_amount)
        if not order:
            logger.error(f"BUY xato: {signal.symbol}")
            self.blacklist_time[signal.symbol] = time.time()
            return False

        qty   = float(order.get("executedQty", 0))
        fills = order.get("fills", [])
        if qty <= 0 and fills:
            qty = sum(float(f.get("qty", 0)) for f in fills)
        if qty <= 0:
            qty = usdt_amount / signal.price

        spent        = float(order.get("cummulativeQuoteQty", usdt_amount))
        actual_price = spent / qty if qty > 0 else signal.price
        tp, sl, hard_sl = self.calc_tp_sl(actual_price, signal.atr)

        sl_pct2 = (actual_price - sl) / actual_price * 100
        tp_pct2 = (tp - actual_price) / actual_price * 100
        rr      = tp_pct2 / sl_pct2 if sl_pct2 > 0 else 0
        base    = signal.symbol.replace("_USDT", "")

        pos = SpotPosition(
            symbol=signal.symbol, entry_price=actual_price,
            qty=qty, tp=tp, sl=sl, hard_sl=hard_sl,
            usdt_spent=spent, peak_price=actual_price,
        )
        self.positions[signal.symbol] = pos

        await self.notify(
            f"{S['rocket']} *POZITSIYA OCHILDI* {S['fire']}\n\n"
            f"{S['gem']} `{signal.symbol}`\n"
            f"{S['money']} Narx: `${actual_price:,.6f}`\n"
            f"{S['coin']} `{qty:.4f} {base}` • `${spent:.2f} USDT`\n\n"
            f"{S['target']} TP: `${tp:,.6f}` _(+{tp_pct2:.1f}%)_\n"
            f"{S['shield']} SL: `${sl:,.6f}` _(-{sl_pct2:.1f}%)_\n\n"
            f"{S['stats']} Kuch: `{signal.strength:.0%}` | RR: `1:{rr:.1f}`\n"
            f"{S['bolt']} _{signal.reason}_"
        )
        return True

    async def close_position(self, symbol: str, reason: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        pnl_pct  = pos.pnl_pct(price)
        pnl_usdt = pos.pnl_usdt(price)
        won      = pnl_pct > 0

        logger.info(f"SELL: {symbol} @ {price:.6f} PnL={pnl_pct:.2f}% ({pnl_usdt:+.3f}) | {reason}")

        order = await self.api.sell_market(symbol, pos.qty)
        # SKIPPED = akkauntda token yo'q (allaqachon sotilgan)
        if order and order.get("reason") == "zero_balance":
            logger.warning(f"SELL {symbol}: token yo'q, pozitsiya o'chirildi")
            if symbol in self.positions:
                del self.positions[symbol]
            return
        if not order:
            await asyncio.sleep(1)
            order = await self.api.sell_market(symbol, pos.qty)

        self.total_pnl += pnl_usdt
        if won:
            self.win_count += 1
        else:
            self.loss_count += 1
            self.daily_loss -= abs(pnl_usdt)
            # Zarar chiqqan coinga 5 daqiqa blacklist
            self.blacklist_time[symbol] = time.time()

        del self.positions[symbol]

        total = self.win_count + self.loss_count
        wr    = self.win_count / total * 100 if total > 0 else 0
        hold  = int(pos.age_seconds)

        if won:
            header = f"{S['trophy']} *FOYDA!* {S['chart_up']}{S['fire']}"
        else:
            header = f"{S['cry']} *Zarar* {S['chart_dn']}"

        await self.notify(
            f"{header}\n\n"
            f"{S['gem']} `{symbol}`\n"
            f"{'📈' if won else '📉'} PnL: `{'+' if won else ''}{pnl_pct:.2f}%` "
            f"(`{'+' if won else ''}{pnl_usdt:.3f} USDT`)\n"
            f"{S['clock']} `{hold//60}:{hold%60:02d}` | {reason}\n\n"
            f"{S['trophy']} `{self.win_count}W/{self.loss_count}L` "
            f"Win: `{wr:.0f}%` | "
            f"PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.3f} USDT`"
        )

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
                if price > pos.peak_price:
                    pos.peak_price = price

                # 1) HardSL — o'zgarmas
                if price <= pos.hard_sl:
                    await self.close_position(symbol, f"{S['shield']}HardSL {pnl:.1f}%", price)
                    return

                # 2) Max vaqt
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"{S['clock']}Vaqt {pos.age_seconds/60:.0f}daq", price)
                    return

                # 3) TP
                if price >= pos.tp:
                    await self.close_position(symbol, f"{S['target']}TP +{pnl:.1f}%", price)
                    return

                # 4) SL
                if price <= pos.sl:
                    await self.close_position(symbol, f"{S['shield']}SL {pnl:.1f}%", price)
                    return

                # 5) Break-even: 1% foydada SL = kirish narxi
                if not pos.breakeven_moved and pnl >= 1.0:
                    new_sl = pos.entry_price * 1.002
                    if new_sl > pos.sl:
                        pos.sl = new_sl
                        pos.breakeven_moved = True
                        logger.info(f"Break-even: {symbol} SL={new_sl:.6f}")

                # 6) Trailing: 1.8% foydadan, peak dan 0.7% past
                if pnl >= 1.8:
                    pos.trailing_active = True
                    trail = pos.peak_price * 0.993
                    if trail > pos.sl:
                        pos.sl = trail

            except Exception as e:
                logger.error(f"Monitor {symbol}: {e}")

        await asyncio.gather(*[check(s, p) for s, p in list(self.positions.items())])

    async def check_daily_loss(self, balance: float) -> bool:
        if self.starting_balance <= 0:
            self.starting_balance = balance
            return True
        if time.time() - self.daily_start_time >= 86400:
            self.daily_loss = 0
            self.daily_start_time = time.time()
            self.starting_balance = balance
            return True
        if self.starting_balance > 0:
            loss_pct = abs(self.daily_loss) / self.starting_balance * 100
            if loss_pct >= self.max_daily_loss_pct * 100:
                await self.notify(
                    f"{S['warn']} *KUNLIK ZARAR LIMITI!*\n"
                    f"Zarar: `{loss_pct:.1f}%` | Bot to'xtatildi {S['stop']}"
                )
                return False
        return True

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
            if symbol in self.blacklist_time:
                if time.time() - self.blacklist_time[symbol] < 120:
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
            logger.info(f"#{self.scan_count} signal yo'q | {len(self.positions)}pos | ${balance:.2f}")
            return

        signals.sort(key=lambda x: x.strength, reverse=True)
        logger.info(f"#{self.scan_count} {len(signals)} signal | ${balance:.2f}")

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
                await asyncio.sleep(0.2)

    async def sync_positions(self):
        """MEXC dagi haqiqiy balanslarni tekshirib, bot pozitsiyalarini sinxronlashtirish"""
        try:
            r = await self.api._get("/api/v3/account", signed=True)
            if not r:
                return
            synced = 0
            for b in r.get("balances", []):
                asset = b.get("asset", "")
                free  = float(b.get("free", 0))
                if asset == "USDT" or free <= 0:
                    continue
                symbol = f"{asset}_USDT"
                # Agar bot da yo'q, lekin MEXC da bor
                if symbol not in self.positions and free > 0:
                    ticker = await self.api.get_ticker(symbol)
                    if not ticker:
                        continue
                    price = float(ticker.get("lastPrice", 0))
                    if price <= 0 or free * price < 1.0:
                        continue
                    # Taxminiy pozitsiya yaratish
                    tp = price * 1.025   # 2.5% TP
                    sl = price * 0.985   # 1.5% SL
                    pos = SpotPosition(
                        symbol=symbol,
                        entry_price=price,
                        qty=free,
                        tp=tp, sl=sl,
                        hard_sl=price * 0.980,
                        usdt_spent=free * price,
                        peak_price=price,
                    )
                    self.positions[symbol] = pos
                    synced += 1
                    logger.info(f"Sinxron: {symbol} qty={free:.6f} @ {price:.6f}")
            if synced > 0:
                msg = "Sinxron: " + str(synced) + " ta pozitsiya yuklandi"
                await self.notify(msg)
        except Exception as e:
            logger.error(f"Sinxron xato: {e}")


    async def sync_positions(self):
        """MEXC dagi haqiqiy balanslarni tekshirib sinxronlashtirish"""
        try:
            r = await self.api._get("/api/v3/account", signed=True)
            if not r:
                return
            synced = 0
            for b in r.get("balances", []):
                asset = b.get("asset", "")
                free  = float(b.get("free", 0))
                if asset == "USDT" or free <= 0:
                    continue
                symbol = asset + "_USDT"
                if symbol in self.positions:
                    continue
                ticker = await self.api.get_ticker(symbol)
                if not ticker:
                    continue
                price = float(ticker.get("lastPrice", 0))
                if price <= 0 or free * price < 1.0:
                    continue
                tp      = round(price * 1.025, 8)
                sl      = round(price * 0.985, 8)
                hard_sl = round(price * 0.980, 8)
                pos = SpotPosition(
                    symbol=symbol, entry_price=price,
                    qty=free, tp=tp, sl=sl, hard_sl=hard_sl,
                    usdt_spent=free * price, peak_price=price,
                )
                self.positions[symbol] = pos
                synced += 1
                logger.info(f"Sinxron: {symbol} qty={free:.6f} @ {price:.6f}")
            if synced > 0:
                msg = f"Sinxron: {synced} ta pozitsiya yuklandi"
                await self.notify(msg)
        except Exception as e:
            logger.error(f"Sinxron xato: {e}")

    async def run(self):
        logger.info("Spot Bot BALANCED v4 ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance("USDT")
        self.starting_balance = balance

        # MEXC dagi mavjud pozitsiyalarni yuklash
        await self.sync_positions()

        await self.notify(
            f"{S['dragon']} *MEXC Spot Bot* {S['fire']}\n\n"
            f"{S['bank']} Balans: `{balance:.2f} USDT`\n"
            f"{S['shield']} SL: `ATR x1.0` | TP: `ATR x2.5`\n"
            f"{S['target']} HardSL: `2.2%` | Max: `8 daqiqa`\n"
            f"{S['stats']} Max pozitsiya: `{self.max_positions}`\n"
            f"{S['bolt']} Skan: `{self.scan_interval}s` | Monitor: `{self.monitor_interval}s`\n"
            f"{S['rocket']} Darhol analiz boshlanmoqda..."
        )

        # Darhol birinchi skan
        await self.scan_and_trade()

        scan_t = monitor_t = hourly_t = 0

        while self.running:
            try:
                await asyncio.sleep(1)
                scan_t    += 1
                monitor_t += 1
                hourly_t  += 1

                if monitor_t >= self.monitor_interval:
                    await self.monitor_positions()
                    monitor_t = 0

                if scan_t >= self.scan_interval:
                    await self.scan_and_trade()
                    scan_t = 0

                if hourly_t >= 3600:
                    balance = await self.api.get_balance("USDT")
                    total   = self.win_count + self.loss_count
                    wr      = self.win_count / total * 100 if total > 0 else 0
                    await self.notify(
                        f"{S['stats']} *Soatlik Hisobot*\n\n"
                        f"{S['bank']} Balans: `{balance:.4f} USDT`\n"
                        f"{'📈' if self.total_pnl>=0 else '📉'} PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"{S['trophy']} `{self.win_count}W/{self.loss_count}L` Win: `{wr:.0f}%`\n"
                        f"{S['eyes']} Ochiq: `{len(self.positions)}` | Skan: `{self.scan_count}`"
                    )
                    hourly_t = 0

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop: {e}")
                await asyncio.sleep(3)

        for symbol in list(self.positions.keys()):
            ticker = await self.api.get_ticker(symbol)
            price  = float(ticker.get("lastPrice", 0)) if ticker else 0
            await self.close_position(symbol, f"{S['stop']} To'xtatildi", price)

        await self.api.close()
        await self.notify(f"{S['stop']} *Bot to'xtatildi* {S['ok']}")
