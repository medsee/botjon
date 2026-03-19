"""
Telegram orqali scalping botni boshqarish
"""
import asyncio
import logging
import os
import sys
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

from mexc_trading import MEXCTrading
from scalping_bot import ScalpingBot
from risk_manager import RiskConfig

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

trading_bot: ScalpingBot = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("▶️ Botni ishga tushir", callback_data="start_trading"),
            InlineKeyboardButton("⏹ To'xtat", callback_data="stop_trading"),
        ],
        [
            InlineKeyboardButton("📊 Holat", callback_data="status"),
            InlineKeyboardButton("💰 Balans", callback_data="balance"),
        ],
        [
            InlineKeyboardButton("📂 Pozitsiyalar", callback_data="positions"),
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings"),
        ],
    ]
    await update.message.reply_text(
        "🤖 *MEXC Scalping Bot*\n\n"
        "Avtomatik savdo boti boshqaruv paneli.\n\n"
        "▶️ tugmasini bosib botni ishga tushiring!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if trading_bot and trading_bot.running:
        await msg.reply_text("⚠️ Bot allaqachon ishlayapti!")
        return

    api_key = os.getenv("MEXC_API_KEY", "")
    if not api_key or api_key == "your_mexc_api_key_here":
        await msg.reply_text("❌ MEXC API kaliti topilmadi! .env faylni tekshiring.")
        return

    await msg.reply_text("⏳ Bot ishga tushirilmoqda...")

    trading_bot = ScalpingBot()
    trading_bot.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    trading_bot.telegram_chat_id = str(update.effective_chat.id)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(trading_bot.run())

    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()

    await msg.reply_text(
        "✅ *Scalping bot ishga tushdi!*\nStatistika avtomatik yuboriladi.",
        parse_mode="Markdown"
    )


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot or not trading_bot.running:
        await msg.reply_text("⚠️ Bot hozir ishlamayapti.")
        return
    trading_bot.running = False
    await msg.reply_text("🔴 Bot to'xtatilmoqda...")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot:
        await msg.reply_text("❌ Bot hali ishga tushmagan.")
        return
    status = "✅ Ishlayapti" if trading_bot.running else "🔴 To'xtatilgan"
    pos_count = len(trading_bot.positions)
    summary = trading_bot.risk.get_summary()
    positions_text = ""
    for sym, pos in trading_bot.positions.items():
        positions_text += f"\n• `{sym}` {pos.side} @ ${pos.entry_price:,.6f} ({pos.age_seconds:.0f}s)"
    text = (
        f"🤖 *Bot holati:* {status}\n\n"
        f"📂 Ochiq pozitsiyalar: *{pos_count}*{positions_text}\n\n"
        f"{summary}"
    )
    await msg.reply_text(text, parse_mode="Markdown")


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    api = MEXCTrading(
        api_key=os.getenv("MEXC_API_KEY", ""),
        secret_key=os.getenv("MEXC_SECRET_KEY", ""),
    )
    balance = await api.get_balance("USDT")
    await api.close()
    if balance == 0:
        await msg.reply_text("❌ Balans 0 yoki API ulana olmadi.")
        return
    await msg.reply_text(f"💰 *USDT Balans:* `{balance:.4f} USDT`", parse_mode="Markdown")


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot or not trading_bot.positions:
        await msg.reply_text("📭 Ochiq pozitsiyalar yo'q.")
        return
    text = "📂 *Ochiq Pozitsiyalar:*\n\n"
    for sym, pos in trading_bot.positions.items():
        text += (
            f"🔸 *{sym}*\n"
            f"   Yo'nalish: {pos.side}\n"
            f"   Kirish: `${pos.entry_price:,.6f}`\n"
            f"   TP: `${pos.tp:,.6f}` | SL: `${pos.sl:,.6f}`\n"
            f"   Vaqt: `{pos.age_seconds:.0f}s`\n\n"
        )
    await msg.reply_text(text, parse_mode="Markdown")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = RiskConfig()
    text = (
        "⚙️ *Joriy Sozlamalar:*\n\n"
        f"Stop-Loss: `{cfg.stop_loss_pct*100:.1f}%`\n"
        f"Take-Profit: `{cfg.take_profit_pct*100:.1f}%`\n"
        f"Max savdo: `{cfg.max_trade_pct*100:.0f}%` balansdan\n"
        f"Max pozitsiyalar: `{cfg.max_open_positions}`\n"
        f"Max kunlik zarar: `{cfg.max_daily_loss_pct*100:.0f}%`\n"
        f"Max kunlik savdolar: `{cfg.max_daily_trades}`\n"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "start_trading":
        await start_trading_cmd(update, context)
    elif d == "stop_trading":
        await stop_trading_cmd(update, context)
    elif d == "status":
        await status_cmd(update, context)
    elif d == "balance":
        await balance_cmd(update, context)
    elif d == "positions":
        await positions_cmd(update, context)
    elif d == "settings":
        await settings_cmd(update, context)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    # Windows uchun event loop muammosini hal qilish
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start_trading", start_trading_cmd))
    app.add_handler(CommandHandler("stop_trading", stop_trading_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Telegram boshqaruv boti ishga tushdi")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
