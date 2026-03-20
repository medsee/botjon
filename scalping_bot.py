"""
MEXC Scalping Bot - Kuchaytirilgan versiya
- 30 ta juftlik bir vaqtda tahlil
- 5 soniyada bir tekshiruv
- Tinimsiz ishlaydi
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from mexc_trading import MEXCTrading
from risk_manager import RiskManager, RiskConfig
from strategy import ScalpingStrategy, Signal

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("scalper.log", encoding='utf-8'),
              logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    qty: float
    tp: float
    sl: float
    order_id: str
    open_time: float = field(default_factory=time.time)
    usdt_invested: float = 0.0

    @property
    def age_seconds(self):
        return time.time() - self.open_time

    def pnl_pct(self, current_price):
        if self.side == "BUY":
            return (current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - current_price) / self.entry_price * 100


class ScalpingBot:
    def __init__(self):
        self.api = MEXCTrading(
            api_key=os.getenv("MEXC_API_KEY", ""),
            secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        )
        self.risk = RiskManager(RiskConfig(
            max_trade_pct=float(os.getenv("MAX_TRADE_PCT", "0.05")),      # 5%
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.008")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.015")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "5")),  # 5 ta
            max_daily_trades=int(os.getenv("MAX_DAILY_TRADES", "100")),
            min_volume_usdt=200_000,   # Quyi chegara
        ))
        self.strategy = ScalpingStrategy()
        self.positions: dict[str, Position] = {}
        self.blacklist: set = set()
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False
        self.total_pnl = 0.0
        self.scan_count = 0

    async def notify(self, msg: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id": self.telegram_chat_id,
                    "text": msg, "parse_mode": "Markdown"
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            logger.error(f"Telegram xato: {e}")

    async def get_top_symbols(self, limit=30) -> list:
        tickers = await self.api.get_all_tickers()
        usdt = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
            and float(t.get("quoteVolume", 0)) >= self.risk.cfg.min_volume_usdt
            and t["symbol"] not in self.blacklist
            and not any(s in t["symbol"] for s in
                       ["BUSD","TUSD","USDC","DAI","FDUSD","USDP"])
            and abs(float(t.get("priceChangePercent", 0))) < 15  # Juda harakatchan emas
        ]
        usdt.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in usdt[:limit]]

    async def open_position(self, signal: Signal, balance: float) -> bool:
        if signal.symbol in self.positions:
            return False
        if len(self.positions) >= self.risk.cfg.max_open_positions:
            return False

        qty = self.risk.calc_position_size(balance, signal.price)
        if qty <= 0:
            return False

        tp, sl = self.risk.calc_tp_sl(signal.price, signal.side)

        logger.info(f"ORDER: {signal.side} {signal.symbol} qty={qty:.6f} @ {signal.price:.6f}")

        order = await self.api.place_order(signal.symbol, signal.side, round(qty, 6))
        if not order:
            logger.error(f"Order xato: {signal.symbol}")
            return False

        filled_price = float(order.get("price", signal.price)) or signal.price
        order_id = str(order.get("orderId", ""))

        pos = Position(
            symbol=signal.symbol, side=signal.side,
            entry_price=filled_price, qty=qty,
            tp=tp, sl=sl, order_id=order_id,
            usdt_invested=qty * filled_price,
        )
        self.positions[signal.symbol] = pos

        await self.notify(
            f"*OCHILDI*: `{signal.symbol}`\n"
            f"Yonalish: {signal.side}\n"
            f"Narx: `${filled_price:,.6f}`\n"
            f"TP: `${tp:,.6f}` | SL: `${sl:,.6f}`\n"
            f"Signal kuch: {signal.strength:.0%}\n"
            f"Sabab: _{signal.reason}_"
        )
        return True

    async def close_position(self, symbol: str, reason: str, current_price: float = None):
        pos = self.positions.get(symbol)
        if not pos:
            return

        close_side = "SELL" if pos.side == "BUY" else "BUY"
        order = await self.api.place_order(symbol, close_side, round(pos.qty, 6))

        if not order:
            logger.error(f"Yopishda xato: {symbol}")
            return

        price = current_price or float(order.get("price", pos.entry_price)) or pos.entry_price
        pnl_pct = pos.pnl_pct(price)
        pnl_usdt = pos.usdt_invested * pnl_pct / 100
        self.total_pnl += pnl_usdt

        self.risk.record_trade(pnl_usdt)
        del self.positions[symbol]

        sign = "+" if pnl_usdt >= 0 else ""
        result = "FOYDA" if pnl_usdt >= 0 else "ZARAR"
        await self.notify(
            f"*{result}*: `{symbol}`\n"
            f"Sabab: _{reason}_\n"
            f"PnL: `{sign}{pnl_usdt:.4f} USDT` ({sign}{pnl_pct:.2f}%)\n"
            f"Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`"
        )
        logger.info(f"CLOSED {symbol}: {sign}{pnl_usdt:.4f} USDT | {reason}")

    async def monitor_positions(self):
        max_hold = int(os.getenv("MAX_HOLD_SECONDS", "180"))  # 3 daqiqa

        for symbol, pos in list(self.positions.items()):
            try:
                ticker = await self.api.get_ticker(symbol)
                if not ticker:
                    continue
                price = float(ticker.get("lastPrice", pos.entry_price))

                # Timeout
                if pos.age_seconds >= max_hold:
                    await self.close_position(symbol, f"Timeout {max_hold}s", price)
                    continue

                # Take-Profit
                if pos.side == "BUY" and price >= pos.tp:
                    await self.close_position(symbol, f"Take-Profit ${pos.tp:,.4f}", price)
                    continue
                if pos.side == "SELL" and price <= pos.tp:
                    await self.close_position(symbol, f"Take-Profit ${pos.tp:,.4f}", price)
                    continue

                # Stop-Loss
                if pos.side == "BUY" and price <= pos.sl:
                    await self.close_position(symbol, f"Stop-Loss ${pos.sl:,.4f}", price)
                    continue
                if pos.side == "SELL" and price >= pos.sl:
                    await self.close_position(symbol, f"Stop-Loss ${pos.sl:,.4f}", price)
                    continue

                # Trailing: agar 1% foyda bo'lsa SL ni break-even ga ko'tar
                pnl = pos.pnl_pct(price)
                if pnl >= 1.0:
                    new_sl = pos.entry_price * 1.001 if pos.side == "BUY" else pos.entry_price * 0.999
                    if pos.side == "BUY" and new_sl > pos.sl:
                        pos.sl = new_sl
                    elif pos.side == "SELL" and new_sl < pos.sl:
                        pos.sl = new_sl

            except Exception as e:
                logger.error(f"Monitor xato {symbol}: {e}")

    async def scan_and_trade(self):
        balance = await self.api.get_balance("USDT")
        self.risk.set_starting_balance(balance)

        can, reason = self.risk.can_trade(balance)
        if not can:
            logger.info(f"Savdo mumkin emas: {reason}")
            return

        if len(self.positions) >= self.risk.cfg.max_open_positions:
            return

        self.scan_count += 1
        symbols = await self.get_top_symbols(30)

        # Parallel tahlil — barcha juftliklarni bir vaqtda tekshirish
        signals = []

        async def analyze_symbol(symbol):
            if symbol in self.positions:
                return
            try:
                klines = await self.api.get_klines(symbol, "Min1", 50)
                ticker = await self.api.get_ticker(symbol)
                if not klines or not ticker:
                    return
                signal = self.strategy.analyze(symbol, klines, ticker)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.debug(f"{symbol} xato: {e}")

        # 10 tadan parallel
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            await asyncio.gather(*[analyze_symbol(s) for s in batch])
            await asyncio.sleep(0.2)

        if not signals:
            return

        # Kuchli signallarni tanlash
        signals.sort(key=lambda x: x.strength, reverse=True)

        # Bir vaqtda bir nechta pozitsiya ochish
        opened = 0
        for signal in signals:
            if opened >= 2:  # Har skanerda max 2 ta yangi
                break
            if signal.symbol not in self.positions:
                if await self.open_position(signal, balance):
                    opened += 1

        logger.info(
            f"Skan #{self.scan_count}: {len(symbols)} juftlik | "
            f"Signallar: {len(signals)} | "
            f"Pozitsiyalar: {len(self.positions)} | "
            f"Balans: {balance:.2f} USDT"
        )

    async def run(self):
        logger.info("Scalping bot ishga tushdi!")
        self.running = True

        balance = await self.api.get_balance("USDT")
        await self.notify(
            f"*Scalping Bot ishga tushdi!*\n"
            f"Balans: `{balance:.2f} USDT`\n"
            f"Strategiya: EMA + RSI + Bollinger\n"
            f"Stop-Loss: {self.risk.cfg.stop_loss_pct*100:.1f}%\n"
            f"Take-Profit: {self.risk.cfg.take_profit_pct*100:.1f}%\n"
            f"Max pozitsiyalar: {self.risk.cfg.max_open_positions}\n"
            f"Skanerlash: har 15 soniyada 30 juftlik"
        )

        scan_timer = 0
        hourly_timer = 0

        while self.running:
            try:
                # Har 5 soniyada pozitsiyalarni tekshir
                await self.monitor_positions()

                # Har 15 soniyada yangi signal qidir
                if scan_timer >= 15:
                    await self.scan_and_trade()
                    scan_timer = 0

                # Har soatda hisobot
                if hourly_timer >= 3600:
                    balance = await self.api.get_balance("USDT")
                    await self.notify(
                        f"*Soatlik hisobot*\n"
                        f"Balans: `{balance:.4f} USDT`\n"
                        f"Jami PnL: `{'+' if self.total_pnl>=0 else ''}{self.total_pnl:.4f} USDT`\n"
                        f"{self.risk.get_summary()}"
                    )
                    hourly_timer = 0

                await asyncio.sleep(5)
                scan_timer += 5
                hourly_timer += 5

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop xato: {e}")
                await asyncio.sleep(10)

        # Yopish
        for symbol in list(self.positions.keys()):
            await self.close_position(symbol, "Bot toxtatildi")
        await self.api.close()
        await self.notify("*Bot toxtatildi.* Barcha pozitsiyalar yopildi.")
