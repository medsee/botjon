"""
MEXC Scalping Bot — asosiy engine
- O'zi juftlik tanlaydi (hajm bo'yicha)
- Avtomatik BUY/SELL
- Stop-Loss / Take-Profit monitoring
- Telegram orqali hisobot
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
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
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
    def age_seconds(self) -> float:
        return time.time() - self.open_time

    def pnl_pct(self, current_price: float) -> float:
        if self.side == "BUY":
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100


class ScalpingBot:
    def __init__(self):
        self.api = MEXCTrading(
            api_key=os.getenv("MEXC_API_KEY", ""),
            secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        )
        self.risk = RiskManager(RiskConfig(
            max_trade_pct=float(os.getenv("MAX_TRADE_PCT", "0.03")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.008")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.015")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
            max_daily_trades=int(os.getenv("MAX_DAILY_TRADES", "50")),
        ))
        self.strategy = ScalpingStrategy()

        self.positions: dict[str, Position] = {}  # symbol -> Position
        self.blacklist: set[str] = set()          # muammoli juftliklar
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.running = False
        self.scan_interval = int(os.getenv("SCAN_INTERVAL", "30"))  # soniya

    async def notify(self, msg: str):
        """Telegram xabar yuborish"""
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
                })
        except Exception as e:
            logger.error(f"Telegram xabar xatosi: {e}")

    async def get_top_symbols(self, limit: int = 20) -> list[str]:
        """Hajm bo'yicha eng yaxshi USDT juftliklarni tanlash"""
        tickers = await self.api.get_all_tickers()
        usdt = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
            and float(t.get("quoteVolume", 0)) >= self.risk.cfg.min_volume_usdt
            and t["symbol"] not in self.blacklist
            # Stable coinlarni o'tkazib yuborish
            and not any(s in t["symbol"] for s in ["BUSD", "TUSD", "USDC", "DAI", "FDUSD"])
        ]
        usdt.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in usdt[:limit]]

    async def open_position(self, signal: Signal, balance: float) -> bool:
        """Pozitsiya ochish"""
        if signal.symbol in self.positions:
            return False

        if len(self.positions) >= self.risk.cfg.max_open_positions:
            logger.info(f"Max pozitsiyalar to'lgan, {signal.symbol} o'tkazildi")
            return False

        qty = self.risk.calc_position_size(balance, signal.price)
        if qty <= 0:
            return False

        tp, sl = self.risk.calc_tp_sl(signal.price, signal.side)

        logger.info(f"ORDER: {signal.side} {signal.symbol} qty={qty:.6f} @ {signal.price}")

        order = await self.api.place_order(signal.symbol, signal.side, round(qty, 6))
        if not order:
            logger.error(f"Order joylashtirishda xato: {signal.symbol}")
            return False

        order_id = str(order.get("orderId", ""))
        filled_price = float(order.get("price", signal.price)) or signal.price

        pos = Position(
            symbol=signal.symbol,
            side=signal.side,
            entry_price=filled_price,
            qty=qty,
            tp=tp,
            sl=sl,
            order_id=order_id,
            usdt_invested=qty * filled_price,
        )
        self.positions[signal.symbol] = pos

        await self.notify(
            f"🟢 *OCHILDI*: `{signal.symbol}`\n"
            f"Yo'nalish: {signal.side}\n"
            f"Narx: `${filled_price:,.6f}`\n"
            f"Miqdor: `{qty:.6f}`\n"
            f"TP: `${tp:,.6f}` | SL: `${sl:,.6f}`\n"
            f"Signal: _{signal.reason}_"
        )
        return True

    async def close_position(self, symbol: str, reason: str):
        """Pozitsiyani yopish"""
        pos = self.positions.get(symbol)
        if not pos:
            return

        close_side = "SELL" if pos.side == "BUY" else "BUY"
        order = await self.api.place_order(symbol, close_side, round(pos.qty, 6))

        if not order:
            logger.error(f"Yopishda xato: {symbol}")
            return

        current_price = float(order.get("price", pos.entry_price)) or pos.entry_price
        pnl_pct = pos.pnl_pct(current_price)
        pnl_usdt = pos.usdt_invested * pnl_pct / 100

        self.risk.record_trade(pnl_usdt)
        del self.positions[symbol]

        emoji = "✅" if pnl_usdt >= 0 else "❌"
        sign = "+" if pnl_usdt >= 0 else ""
        await self.notify(
            f"{emoji} *YOPILDI*: `{symbol}`\n"
            f"Sabab: _{reason}_\n"
            f"Narx: `${current_price:,.6f}`\n"
            f"PnL: `{sign}{pnl_usdt:.4f} USDT` ({sign}{pnl_pct:.2f}%)\n"
            f"{self.risk.get_summary()}"
        )
        logger.info(f"CLOSED {symbol}: {sign}{pnl_usdt:.4f} USDT ({reason})")

    async def monitor_positions(self):
        """Ochiq pozitsiyalarni kuzatish (TP/SL/Timeout)"""
        max_hold_seconds = int(os.getenv("MAX_HOLD_SECONDS", "300"))  # 5 daqiqa

        for symbol, pos in list(self.positions.items()):
            ticker = await self.api.get_ticker(symbol)
            if not ticker:
                continue

            price = float(ticker.get("lastPrice", pos.entry_price))

            # Timeout
            if pos.age_seconds >= max_hold_seconds:
                await self.close_position(symbol, f"⏱ Timeout ({max_hold_seconds}s)")
                continue

            # Take-Profit
            if pos.side == "BUY" and price >= pos.tp:
                await self.close_position(symbol, f"🎯 Take-Profit ${pos.tp:,.6f}")
                continue
            if pos.side == "SELL" and price <= pos.tp:
                await self.close_position(symbol, f"🎯 Take-Profit ${pos.tp:,.6f}")
                continue

            # Stop-Loss
            if pos.side == "BUY" and price <= pos.sl:
                await self.close_position(symbol, f"🛑 Stop-Loss ${pos.sl:,.6f}")
                continue
            if pos.side == "SELL" and price >= pos.sl:
                await self.close_position(symbol, f"🛑 Stop-Loss ${pos.sl:,.6f}")
                continue

    async def scan_and_trade(self):
        """Juftliklarni skanerlash va signal topish"""
        balance = await self.api.get_balance("USDT")
        self.risk.set_starting_balance(balance)

        can, reason = self.risk.can_trade(balance)
        if not can:
            logger.info(f"Savdo mumkin emas: {reason}")
            return

        logger.info(f"Balans: {balance:.2f} USDT | Pozitsiyalar: {len(self.positions)}")

        symbols = await self.get_top_symbols(20)
        best_signal: Optional[Signal] = None
        best_strength = 0.0

        for symbol in symbols:
            if symbol in self.positions:
                continue  # Allaqachon ochiq

            try:
                klines = await self.api.get_klines(symbol, "Min1", 50)
                ticker = await self.api.get_ticker(symbol)
                if not klines or not ticker:
                    continue

                signal = self.strategy.analyze(symbol, klines, ticker)
                if signal and signal.strength > best_strength:
                    best_strength = signal.strength
                    best_signal = signal

                await asyncio.sleep(0.1)  # Rate limit uchun

            except Exception as e:
                logger.error(f"{symbol} tahlil xatosi: {e}")

        if best_signal:
            logger.info(
                f"Signal: {best_signal.side} {best_signal.symbol} "
                f"kuch={best_signal.strength:.2f} sabab={best_signal.reason}"
            )
            await self.open_position(best_signal, balance)

    async def run(self):
        """Asosiy loop"""
        logger.info("🚀 Scalping bot ishga tushdi")
        self.running = True

        # Boshlang'ich xabar
        balance = await self.api.get_balance("USDT")
        await self.notify(
            f"🤖 *Scalping Bot ishga tushdi!*\n"
            f"💰 Balans: `{balance:.2f} USDT`\n"
            f"📊 Strategiya: EMA + RSI + Bollinger\n"
            f"🛡 Stop-Loss: {self.risk.cfg.stop_loss_pct*100:.1f}%\n"
            f"🎯 Take-Profit: {self.risk.cfg.take_profit_pct*100:.1f}%\n"
            f"⚠️ Max kunlik zarar: {self.risk.cfg.max_daily_loss_pct*100:.1f}%"
        )

        iteration = 0
        while self.running:
            try:
                # Pozitsiyalarni kuzatish (har iteratsiyada)
                await self.monitor_positions()

                # Yangi signal qidirish (har scan_interval soniyada)
                if iteration % max(1, self.scan_interval // 5) == 0:
                    await self.scan_and_trade()

                # Har soatda statistika
                if iteration > 0 and iteration % (3600 // 5) == 0:
                    await self.notify(f"📈 *Soatlik hisobot*\n{self.risk.get_summary()}")

                iteration += 1
                await asyncio.sleep(5)  # 5 soniyada bir tekshirish

            except KeyboardInterrupt:
                logger.info("Bot to'xtatilmoqda...")
                break
            except Exception as e:
                logger.error(f"Loop xatosi: {e}")
                await asyncio.sleep(10)

        # To'xtatishda barcha pozitsiyalarni yopish
        logger.info("Barcha pozitsiyalar yopilmoqda...")
        for symbol in list(self.positions.keys()):
            await self.close_position(symbol, "Bot to'xtatildi")

        await self.api.close()
        await self.notify("🔴 *Bot to'xtatildi.* Barcha pozitsiyalar yopildi.")
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    bot = ScalpingBot()
    asyncio.run(bot.run())
