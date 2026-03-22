"""
MEXC Spot Bot - PRECISION v8
Kam savdo, katta TP, faqat A+ signallar
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

        # ── Risk — konservativ ───────────────────────────────
        self.max_positions  = 2             # Max 2 ta ochiq pozitsiya
        self.trade_pct      = 0.30          # Har savdoga 30%

        # TP/SL — komissiyadan 10x katta
        # MEXC komissiya: 0.2% ikki tomon
        # TP minimal 2% = 10x komissiya
        self.atr_sl_mult    = 0.8           # SL = 0.8x ATR
        self.atr_tp_mult    = 4.0           # TP = 4x ATR — katta foyda
        self.min_tp_pct     = 0.020         # Minimal TP 2.0%
        self.min_sl_pct     = 0.008         # Minimal SL 0.8%
        self.hard_sl_pct    = 0.025         # HardSL 2.5%
        self.max_sl_pct     = 0.030         # Max SL 3%

        self.max_daily_loss_pct = 0.05      # 5% kunlik limit
        self.max_hold_seconds   = 900       # 15 daqiqa max
        self.min_usdt           = 2.0

        # ── Skan ────────────────────────────────────────────
        self.scan_interval     = 5
        self.monitor_interval  = 1
        self.top_symbols_limit = 200
        self.batch_size        = 20
        self.batch_delay       = 0.05
        self.min_price         = 0.00001
        self.min_volume        = 50_000
        self.cache_ttl         = 120

        self.positions: dict[str, SpotPosition] = {}
        self.blacklist: set   = set()
        self.blacklist_time: dict = {}
        self.symbol_cache: list = []
        self.cache_time: float  = 0

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
                if vol >= self.min_volume and price >= self.min_price:
                    usdt.append((spot_sym, vol))
            except:
                pass
        usdt.sort(key=lambda x: x[1], reverse=True)
        self.symbol_cache = [s for s, _ in usdt[:self.top_symbols_limit]]
        self.cache_time   = now
        logger.info(f"Kesh: {len(self.symbol_cache)} juftlik")
        return self.symbol_cache

    def calc_tp_sl(self, entry: float, atr_val: float):
        raw_tp  = entry + self.atr_tp_mult * atr_val
        raw_sl  = entry - self.atr_sl_mult * atr_val
        hard_sl = entry * (1 - self.hard_sl_pct)
        tp = max(raw_tp, entry * (1 + self.min_tp_pct))
        sl = min(raw_sl, entry * (1 - self.min_sl_pct))
        sl = max(sl, hard_sl)
        return round(tp, 8), round(sl, 8), round(hard_sl, 8)

    async def open_position(self, signal: SpotSignal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        if signal.symbol in self.blacklist_time:
            if time.time() - self.blacklist_time[signal.symbol] < 180:
                return False
            else:
                del self.blacklist_time[signal.symbol]

        usdt_amount = balance * self.trade_pct
        usdt_amount = max(usdt_amount, 2.0)
        if usdt_amount > balance * 0.95:
            usdt_amount = balance * 0.95

        tp, sl, hard_sl = self.calc_tp_sl(signal.price, signal.atr)
        sl_pct = (signal.price - sl) / signal.price * 100
        tp_pct = (tp - signal.price) / signal.price * 100

        if sl_pct > self.max_sl_pct * 100:
            return False
        if tp_pct < 1.0:
            return False

        decimals = await self.api.get_step_size(signal.symbol)
        min_qty  = 1.0 / (10 ** decimals) if decimals >= 0 else 1.0
        if usdt_amount / signal.price < min_qty:
            return False

        logger.info(f"BUY: {signal.symbol} ${usdt_amount:.2f} @ {signal.price:.6f} TP=+{tp_pct:.1f}% SL=-{sl_pct:.1f}%")
        order = await self.api.buy_market(signal.symbol, usdt_amount)
        if not order:
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

        self.positions[signal.symbol] = SpotPosition(
            symbol=signal.symbol, entry_price=actual_price,
            qty=qty, tp=tp, sl=sl, hard_sl=hard_sl,
            usdt_spent=spent, peak_price=actual_price,
        )

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
        won      = pnl_pct > 0.2

        logger.info(f"SELL: {symbol} @ {price:.6f} PnL={pnl_pct:.2f}% ({pnl_usdt:+.3f}) | {reason}")

        order = await self.api.sell_market(symbol, pos.qty)
        if order and order.get("reason") == "zero_balance":
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
            self.blacklist_time[symbol] = time.time()

        if symbol in self.positions:
            del self.positions[symbol]

        total  = self.win_count + self.loss_count
        wr     = self.win_count / total * 100 if total > 0 else 0
        hold   = int(pos.age_seconds)
        header = f"{S['trophy']} *FOYDA!* {S['chart_up']}{S['fire']}" if won else f"{S['cry']} *Zarar* {S['chart_dn']}"

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

                if price <= pos.hard_sl:
                    await self.close_position(symbol, f"{S['shield']}HardSL {pnl:.1f}%", price)
                    return
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"{S['clock']}Vaqt {pos.age_seconds/60:.0f}daq", price)
                    return
                if price >= pos.tp:
                    await self.close_position(symbol, f"{S['target']}TP +{pnl:.1f}%", price)
                    return
                if price <= pos.sl:
                    await self.close_position(symbol, f"{S['shield']}SL {pnl:.1f}%", price)
                    return

                # Break-even: 1.5% foydada SL = kirish + 0.2%
                if not pos.breakeven_moved and pnl >= 1.5:
                    new_sl = pos.entry_price * 1.002
                    if new_sl > pos.sl:
                        pos.sl = new_sl
                        pos.breakeven_moved = True
                        logger.info(f"BreakEven: {symbol} SL={new_sl:.6f}")

                # Trailing: 2.5% foydadan, peak dan 1% past
                if pnl >= 2.5:
                    trail = pos.peak_price * 0.990
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

        opened = 0
        for sig in signals:
            if opened >= 1:
                break
            if sig.symbol not in self.positions:
                ok = await self.open_position(sig, balance)
                if ok:
                    opened += 1
                await asyncio.sleep(0.1)

    async def sync_positions(self):
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
                tp      = round(price * 1.03, 8)
                sl      = round(price * 0.978, 8)
                hard_sl = round(price * 0.975, 8)
                self.positions[symbol] = SpotPosition(
                    symbol=symbol, entry_price=price,
                    qty=free, tp=tp, sl=sl, hard_sl=hard_sl,
                    usdt_spent=free * price, peak_price=price,
                )
                synced += 1
                logger.info(f"Sinxron: {symbol} qty={free:.6f} @ {price:.6f}")
            if synced > 0:
                await self.notify(f"Sinxron: {synced} ta pozitsiya yuklandi")
        except Exception as e:
            logger.error(f"Sinxron xato: {e}")

    async def run(self):
        logger.info("Spot Bot PRECISION v8 ishga tushdi!")
        self.running = True
        balance = await self.api.get_balance("USDT")
        self.starting_balance = balance
        await self.sync_positions()

        await self.notify(
            f"{S['dragon']} *MEXC Spot Bot PRECISION v8* {S['fire']}\n\n"
            f"{S['bank']} Balans: `{balance:.2f} USDT`\n"
            f"{S['shield']} SL: min `0.8%` | TP: min `2.0%`\n"
            f"{S['target']} HardSL: `2.5%` | Max: `15 daqiqa`\n"
            f"{S['stats']} Max pozitsiya: `{self.max_positions}`\n"
            f"{S['bolt']} Faqat A+ signallar | 200 coin skan\n"
            f"{S['rocket']} Darhol analiz boshlanmoqda..."
        )

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
