"""
MEXC Futures Scalping Bot
- 2x Leverage (xavfsiz)
- Long va Short
- Kuchli himoya - sliv yo'q
- Trailing Stop
- 24/7 tinimsiz ishlaydi
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
    side: str           # LONG yoki SHORT
    entry_price: float
    vol: int            # Futures hajmi (kontraktlar)
    tp: float
    sl: float
    open_time: float = field(default_factory=time.time)
    peak_price: float = 0.0
    usdt_margin: float = 0.0
    leverage: int = 2

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

        # Risk parametrlari
        self.leverage = 2
        self.max_positions = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
        self.trade_pct = float(os.getenv("MAX_TRADE_PCT", "0.05"))  # 5% margin
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "0.008"))   # 0.8% narxdan
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", "0.015"))  # 1.5% narxdan
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))  # 5%
        self.max_hold_seconds = int(os.getenv("MAX_HOLD_SECONDS", "300"))

        self.positions: dict[str, FuturesPosition] = {}
        self.blacklist: set = set()
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False

        # Statistika
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0
        self.scan_count = 0
        self.starting_balance = 0.0
        self.daily_loss = 0.0

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

    async def get_top_symbols(self, limit=30) -> list:
        """Eng likvidli futures juftliklarni olish"""
        tickers = await self.api.get_all_tickers()
        if not tickers:
            return []

        # USDT-M perpetual kontraktlar
        usdt = [
            t for t in tickers
            if "_USDT" in t.get("symbol", "") or t.get("symbol", "").endswith("USDT")
            and t["symbol"] not in self.blacklist
        ]

        # Hajm bo'yicha saralash
        def get_vol(t):
            try:
                return float(t.get("volume24", t.get("amount24", 0)))
            except:
                return 0

        usdt.sort(key=get_vol, reverse=True)
        return [t["symbol"] for t in usdt[:limit]]

    def calc_vol(self, balance: float, price: float) -> int:
        """Kontraktlar sonini hisoblash"""
        margin = balance * self.trade_pct
        notional = margin * self.leverage
        vol = notional / price
        return max(1, int(vol))

    def calc_tp_sl(self, entry: float, side: str) -> tuple:
        if side == "LONG":
            tp = entry * (1 + self.take_profit_pct)
            sl = entry * (1 - self.stop_loss_pct)
        else:
            tp = entry * (1 - self.take_profit_pct)
            sl = entry * (1 + self.stop_loss_pct)
        return round(tp, 6), round(sl, 6)

    async def open_position(self, signal: FuturesSignal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        if balance < 5:
            return False

        # Leverage o'rnatish
        await self.api.set_leverage(signal.symbol, self.leverage)

        vol = self.calc_vol(balance, signal.price)
        tp, sl = self.calc_tp_sl(signal.price, signal.side)
        margin = balance * self.trade_pct

        logger.info(f"FUTURES {signal.side}: {signal.symbol} vol={vol} @ {signal.price}")

        if signal.side == "LONG":
            order = await self.api.open_long(signal.symbol, vol)
        else:
            order = await self.api.open_short(signal.symbol, vol)

        if not order:
            logger.error(f"Futures order xato: {signal.symbol}")
            self.blacklist.add(signal.symbol)
            return False

        pos = FuturesPosition(
            symbol=signal.symbol,
            side=signal.side,
            entry_price=signal.price,
            vol=vol,
            tp=tp,
            sl=sl,
            peak_price=signal.price,
            usdt_margin=margin,
            leverage=self.leverage,
        )
        self.positions[signal.symbol] = pos

        side_emoji = "LONG" if signal.side == "LONG" else "SHORT"
        await self.notify(
            f"*{side_emoji} OCHILDI* `{signal.symbol}`\n"
            f"Leverage: `{self.leverage}x`\n"
            f"Narx: `${signal.price:,.4f}`\n"
            f"Hajm: `{vol}` kontakt\n"
            f"Margin: `${margin:.2f} USDT`\n"
            f"TP: `${tp:,.4f}` | SL: `${sl:,.4f}`\n"
            f"Signal: _{signal.reason}_ ({signal.strength:.0%})"
        )
        return True

    async def close_position(self, symbol: str, reason: str, current_price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        if pos.side == "LONG":
            order = await self.api.close_long(symbol, pos.vol)
        else:
            order = await self.api.close_short(symbol, pos.vol)

        if not order:
            logger.error(f"Yopishda xato: {symbol}")
            return

        pnl_pct = pos.pnl_pct(current_price)
        pnl_usdt = pos.usdt_margin * pnl_pct / 100
        self.total_pnl += pnl_usdt
        self.daily_loss += min(pnl_usdt, 0)

        if pnl_usdt >= 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        del self.positions[symbol]

        total = self.win_count + self.loss_count
        wr = self.win_count / total * 100 if total > 0 else 0
        sign = "+" if pnl_usdt >= 0 else ""
        result = "FOYDA" if pnl_usdt >= 0 else "ZARAR"

        await self.notify(
            f"*{result}* `{symbol}`\n"
            f"Sabab: _{reason}_\n"
            f"Kirish: `${pos.entry_price:,.4f}` → `${current_price:,.4f}`\n"
            f"PnL: `{sign}{pnl_usdt:.4f} USDT` ({sign}{pnl_pct:.2f}%)\n"
            f"Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
            f"Win rate: `{wr:.0f}%` ({self.win_count}W/{self.loss_count}L)"
        )

    async def monitor_positions(self):
        for symbol, pos in list(self.positions.items()):
            try:
                ticker = await self.api.get_ticker(symbol)
                if not ticker:
                    continue

                price = float(ticker.get("lastPrice", ticker.get("last", pos.entry_price)))
                if price <= 0:
                    continue

                pnl = pos.pnl_pct(price)

                # Peak yangilash
                if pos.side == "LONG" and price > pos.peak_price:
                    pos.peak_price = price
                elif pos.side == "SHORT" and price < pos.peak_price:
                    pos.peak_price = price

                # Timeout
                if pos.age_seconds >= self.max_hold_seconds:
                    await self.close_position(symbol, f"Timeout {self.max_hold_seconds}s", price)
                    continue

                # Take-Profit
                if pos.side == "LONG" and price >= pos.tp:
                    await self.close_position(symbol, f"TP +{pnl:.1f}%", price)
                    continue
                if pos.side == "SHORT" and price <= pos.tp:
                    await self.close_position(symbol, f"TP +{pnl:.1f}%", price)
                    continue

                # Stop-Loss
                if pos.side == "LONG" and price <= pos.sl:
                    await self.close_position(symbol, f"SL {pnl:.1f}%", price)
                    continue
                if pos.side == "SHORT" and price >= pos.sl:
                    await self.close_position(symbol, f"SL {pnl:.1f}%", price)
                    continue

                # Trailing Stop: 1% foydada SL ni break-even ga ko'tar
                if pnl >= 1.0:
                    if pos.side == "LONG":
                        new_sl = pos.peak_price * (1 - self.stop_loss_pct * 0.5)
                        if new_sl > pos.sl:
                            pos.sl = new_sl
                    else:
                        new_sl = pos.peak_price * (1 + self.stop_loss_pct * 0.5)
                        if new_sl < pos.sl:
                            pos.sl = new_sl

            except Exception as e:
                logger.error(f"Monitor xato {symbol}: {e}")

    async def check_daily_loss(self, balance: float) -> bool:
        """Kunlik zarar limitini tekshirish"""
        if self.starting_balance <= 0:
            self.starting_balance = balance
            return True

        loss_pct = abs(self.daily_loss) / self.starting_balance * 100
        if loss_pct >= self.max_daily_loss_pct * 100:
            await self.notify(
                f"*KUNLIK ZARAR LIMITI!*\n"
                f"Zarar: `{loss_pct:.1f}%`\n"
                f"Bot bugun savdoni to'xtatdi.\n"
                f"Ertaga qayta boshlanadi."
            )
            return False
        return True

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
        symbols = await self.get_top_symbols(30)
        if not symbols:
            return

        signals = []

        async def analyze(symbol):
            if symbol in self.positions or symbol in self.blacklist:
                return
            try:
                klines = await self.api.get_klines(symbol, "Min1", 60)
                ticker = await self.api.get_ticker(symbol)
                if not klines or not ticker:
                    return
                signal = self.strategy.analyze(symbol, klines, ticker)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.debug(f"{symbol} xato: {e}")

        # Parallel tahlil
        for i in range(0, len(symbols), 10):
            batch = symbols[i:i+10]
            await asyncio.gather(*[analyze(s) for s in batch])
            await asyncio.sleep(0.3)

        if not signals:
            logger.info(f"Skan #{self.scan_count}: Signal yo'q | {len(self.positions)} pozitsiya | {balance:.2f} USDT")
            return

        signals.sort(key=lambda x: x.strength, reverse=True)
        logger.info(
            f"Skan #{self.scan_count}: {len(signals)} signal | "
            f"{len(self.positions)} pozitsiya | {balance:.2f} USDT"
        )

        # Eng kuchli 2 ta signal
        for signal in signals[:2]:
            if signal.symbol not in self.positions:
                await self.open_position(signal, balance)
                await asyncio.sleep(0.5)

    async def run(self):
        logger.info("Futures bot ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance()
        self.starting_balance = balance

        await self.notify(
            f"*MEXC Futures Bot ishga tushdi!*\n"
            f"Balans: `{balance:.2f} USDT`\n"
            f"Leverage: `{self.leverage}x`\n"
            f"Stop-Loss: `{self.stop_loss_pct*100:.1f}%`\n"
            f"Take-Profit: `{self.take_profit_pct*100:.1f}%`\n"
            f"Max pozitsiyalar: `{self.max_positions}`\n"
            f"Max kunlik zarar: `{self.max_daily_loss_pct*100:.0f}%`\n"
            f"Skanerlash: har 15 soniyada 30 juftlik\n"
            f"Long va Short ikkalasi ishlaydi!"
        )

        scan_timer = 0
        hourly_timer = 0

        while self.running:
            try:
                await self.monitor_positions()

                if scan_timer >= 15:
                    await self.scan_and_trade()
                    scan_timer = 0

                if hourly_timer >= 3600:
                    balance = await self.api.get_balance()
                    total = self.win_count + self.loss_count
                    wr = self.win_count / total * 100 if total > 0 else 0
                    await self.notify(
                        f"*Soatlik hisobot*\n"
                        f"Balans: `{balance:.4f} USDT`\n"
                        f"Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"Savdolar: `{total}` (W:{self.win_count}/L:{self.loss_count})\n"
                        f"Win rate: `{wr:.0f}%`\n"
                        f"Ochiq: `{len(self.positions)}`"
                    )
                    hourly_timer = 0
                    self.daily_loss = 0  # Kunlik reset

                await asyncio.sleep(5)
                scan_timer += 5
                hourly_timer += 5

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop xato: {e}")
                await asyncio.sleep(10)

        for symbol in list(self.positions.keys()):
            ticker = await self.api.get_ticker(symbol)
            price = float(ticker.get("lastPrice", 0)) if ticker else 0
            await self.close_position(symbol, "Bot toxtatildi", price)
        await self.api.close()
        await self.notify("*Futures Bot toxtatildi.* Barcha pozitsiyalar yopildi.")
