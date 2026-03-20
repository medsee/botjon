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
trading_bot = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Botni ishga tushir", callback_data="start_trading"),
         InlineKeyboardButton("⏹ To'xtat", callback_data="stop_trading")],
        [InlineKeyboardButton("📊 Holat", callback_data="status"),
         InlineKeyboardButton("💰 Balans", callback_data="balance")],
        [InlineKeyboardButton("📂 Pozitsiyalar", callback_data="positions"),
         InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings")],
    ]
    await update.message.reply_text(
        "🤖 *MEXC Scalping Bot*\n\nAvtomatik savdo boti.\n\n▶️ tugmasini bosing!",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def start_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if trading_bot and trading_bot.running:
        await msg.reply_text("⚠️ Bot allaqachon ishlayapti!")
        return
    api_key = os.getenv("MEXC_API_KEY", "")
    if not api_key or api_key == "your_mexc_api_key_here":
        await msg.reply_text("❌ MEXC API kaliti topilmadi!")
        return
    await msg.reply_text("⏳ Bot ishga tushirilmoqda...")
    trading_bot = ScalpingBot()
    trading_bot.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    trading_bot.telegram_chat_id = str(update.effective_chat.id)
    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(trading_bot.run())
    threading.Thread(target=run_bot, daemon=True).start()
    await msg.reply_text("✅ *Scalping bot ishga tushdi!*", parse_mode="Markdown")


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    if not trading_bot or not trading_bot.running:
        await update.effective_message.reply_text("⚠️ Bot hozir ishlamayapti.")
        return
    trading_bot.running = False
    await update.effective_message.reply_text("🔴 Bot to'xtatilmoqda...")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    if not trading_bot:
        await update.effective_message.reply_text("❌ Bot hali ishga tushmagan.")
        return
    status = "✅ Ishlayapti" if trading_bot.running else "🔴 To'xtatilgan"
    pos_count = len(trading_bot.positions)
    summary = trading_bot.risk.get_summary()
    positions_text = ""
    for sym, pos in trading_bot.positions.items():
        positions_text += f"\n• `{sym}` {pos.side} @ ${pos.entry_price:,.6f} ({pos.age_seconds:.0f}s)"
    await update.effective_message.reply_text(
        f"🤖 *Bot holati:* {status}\n\n📂 Pozitsiyalar: *{pos_count}*{positions_text}\n\n{summary}",
        parse_mode="Markdown")


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = MEXCTrading(os.getenv("MEXC_API_KEY", ""), os.getenv("MEXC_SECRET_KEY", ""))
    balance = await api.get_balance("USDT")
    await api.close()
    if balance == 0:
        await update.effective_message.reply_text("❌ Balans 0 yoki API ulana olmadi.")
        return
    await update.effective_message.reply_text(f"💰 *USDT Balans:* `{balance:.4f} USDT`", parse_mode="Markdown")


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    if not trading_bot or not trading_bot.positions:
        await update.effective_message.reply_text("📭 Ochiq pozitsiyalar yo'q.")
        return
    text = "📂 *Ochiq Pozitsiyalar:*\n\n"
    for sym, pos in trading_bot.positions.items():
        text += f"🔸 *{sym}*\n   {pos.side} @ `${pos.entry_price:,.6f}`\n   TP:`${pos.tp:,.6f}` SL:`${pos.sl:,.6f}`\n\n"
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = RiskConfig()
    await update.effective_message.reply_text(
        f"⚙️ *Sozlamalar:*\n\nStop-Loss: `{cfg.stop_loss_pct*100:.1f}%`\n"
        f"Take-Profit: `{cfg.take_profit_pct*100:.1f}%`\nMax savdo: `{cfg.max_trade_pct*100:.0f}%`\n"
        f"Max pozitsiyalar: `{cfg.max_open_positions}`\nMax kunlik zarar: `{cfg.max_daily_loss_pct*100:.0f}%`",
        parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    handlers = {
        "start_trading": start_trading_cmd, "stop_trading": stop_trading_cmd,
        "status": status_cmd, "balance": balance_cmd,
        "positions": positions_cmd, "settings": settings_cmd,
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
    logger.info("✅ Telegram bot ishga tushdi!")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        await asyncio.Event().wait()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_app())
