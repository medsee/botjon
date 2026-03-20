# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import sys
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

from mexc_futures import MEXCFutures
from futures_bot import FuturesBot

load_dotenv()

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

trading_bot: FuturesBot = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Botni ishga tushir", callback_data="start_trading"),
            InlineKeyboardButton("Toxtat", callback_data="stop_trading"),
        ],
        [
            InlineKeyboardButton("Holat", callback_data="status"),
            InlineKeyboardButton("Balans", callback_data="balance"),
        ],
        [
            InlineKeyboardButton("Pozitsiyalar", callback_data="positions"),
            InlineKeyboardButton("Sozlamalar", callback_data="settings"),
        ],
    ]
    await update.message.reply_text(
        "*MEXC Futures Scalping Bot*\n\n"
        "2x Leverage | Long + Short\n"
        "Tinimsiz ishlaydi | Sliv yo'q\n\n"
        "Botni ishga tushirish tugmasini bosing!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if trading_bot and trading_bot.running:
        await msg.reply_text("Bot allaqachon ishlayapti!")
        return

    api_key = os.getenv("MEXC_API_KEY", "")
    if not api_key or api_key == "your_mexc_api_key_here":
        await msg.reply_text("MEXC API kaliti topilmadi!")
        return

    await msg.reply_text("Bot ishga tushirilmoqda...")

    trading_bot = FuturesBot()
    trading_bot.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    trading_bot.telegram_chat_id = str(update.effective_chat.id)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(trading_bot.run())

    threading.Thread(target=run_bot, daemon=True).start()
    await msg.reply_text("Futures bot ishga tushdi! Telegram orqali xabar olasiz.")


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    if not trading_bot or not trading_bot.running:
        await update.effective_message.reply_text("Bot hozir ishlamayapti.")
        return
    trading_bot.running = False
    await update.effective_message.reply_text("Bot toxtatilmoqda...")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot:
        await msg.reply_text("Bot hali ishga tushmagan.")
        return

    status = "Ishlayapti" if trading_bot.running else "Toxtatilgan"
    total = trading_bot.win_count + trading_bot.loss_count
    wr = trading_bot.win_count / total * 100 if total > 0 else 0
    sign = "+" if trading_bot.total_pnl >= 0 else ""

    positions_text = ""
    for sym, pos in trading_bot.positions.items():
        positions_text += f"\n- `{sym}` {pos.side} @ ${pos.entry_price:,.4f} ({pos.age_seconds:.0f}s)"

    await msg.reply_text(
        f"*Bot holati:* {status}\n\n"
        f"Ochiq pozitsiyalar: *{len(trading_bot.positions)}*{positions_text}\n\n"
        f"Jami PnL: `{sign}{trading_bot.total_pnl:.4f} USDT`\n"
        f"Win rate: `{wr:.0f}%` ({trading_bot.win_count}W/{trading_bot.loss_count}L)\n"
        f"Skanlar: `{trading_bot.scan_count}`",
        parse_mode="Markdown",
    )


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    api = MEXCFutures(os.getenv("MEXC_API_KEY", ""), os.getenv("MEXC_SECRET_KEY", ""))
    balance = await api.get_balance()
    await api.close()
    if balance == 0:
        await msg.reply_text("Balans 0 yoki Futures API ulana olmadi.\nFutures hisob ochilganini tekshiring.")
        return
    await msg.reply_text(f"*Futures USDT Balans:* `{balance:.4f} USDT`", parse_mode="Markdown")


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot or not trading_bot.positions:
        await msg.reply_text("Ochiq pozitsiyalar yo'q.")
        return

    text = "*Ochiq Futures Pozitsiyalar:*\n\n"
    for sym, pos in trading_bot.positions.items():
        text += (
            f"- *{sym}* ({pos.side} {pos.leverage}x)\n"
            f"  Kirish: `${pos.entry_price:,.4f}`\n"
            f"  TP: `${pos.tp:,.4f}` | SL: `${pos.sl:,.4f}`\n"
            f"  Vaqt: `{pos.age_seconds:.0f}s`\n\n"
        )
    await msg.reply_text(text, parse_mode="Markdown")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"*Futures Bot Sozlamalari:*\n\n"
        f"Leverage: `{os.getenv('LEVERAGE', '2')}x`\n"
        f"Stop-Loss: `{float(os.getenv('STOP_LOSS_PCT', '0.008'))*100:.1f}%`\n"
        f"Take-Profit: `{float(os.getenv('TAKE_PROFIT_PCT', '0.015'))*100:.1f}%`\n"
        f"Max savdo: `{float(os.getenv('MAX_TRADE_PCT', '0.05'))*100:.0f}%` balansdan\n"
        f"Max pozitsiyalar: `{os.getenv('MAX_OPEN_POSITIONS', '3')}`\n"
        f"Max kunlik zarar: `{float(os.getenv('MAX_DAILY_LOSS_PCT', '0.05'))*100:.0f}%`",
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    handlers = {
        "start_trading": start_trading_cmd,
        "stop_trading": stop_trading_cmd,
        "status": status_cmd,
        "balance": balance_cmd,
        "positions": positions_cmd,
        "settings": settings_cmd,
    }
    if q.data in handlers:
        await handlers[q.data](update, context)


async def run_app():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start_trading", start_trading_cmd))
    app.add_handler(CommandHandler("stop_trading", stop_trading_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Telegram Futures bot ishga tushdi!")

    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        await asyncio.Event().wait()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_app())
