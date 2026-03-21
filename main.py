# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import sys
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

from mexc_spot import MEXCSpot
from spot_bot import SpotBot

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

trading_bot: SpotBot = None


def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Botni ishga tushir", callback_data="start_trading"),
            InlineKeyboardButton("⛔️ To'xtat",           callback_data="stop_trading"),
        ],
        [
            InlineKeyboardButton("📊 Holat",        callback_data="status"),
            InlineKeyboardButton("💰 Balans",       callback_data="balance"),
        ],
        [
            InlineKeyboardButton("📂 Pozitsiyalar", callback_data="positions"),
            InlineKeyboardButton("⚙️ Sozlamalar",   callback_data="settings"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *MEXC Spot Scalping Bot* 🤖\n\n"
        "🟢 Long only (buy/sell)\n"
        "🛡 TP/SL avtomatik | ⏱ 24/7 ishlaydi\n"
        "📡 EMA + RSI + BB + Volume\n\n"
        "👇 Quyidagi tugmani bosing:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def start_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if trading_bot and trading_bot.running:
        await msg.reply_text(
            "⚠️ *Bot allaqachon ishlayapti!*",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return

    api_key = os.getenv("MEXC_API_KEY", "")
    if not api_key:
        await msg.reply_text("❌ *MEXC API kaliti topilmadi!*", parse_mode="Markdown")
        return

    await msg.reply_text("⏳ *Bot ishga tushirilmoqda...*", parse_mode="Markdown")

    trading_bot = SpotBot()
    trading_bot.telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    trading_bot.telegram_chat_id = str(update.effective_chat.id)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(trading_bot.run())

    threading.Thread(target=run_bot, daemon=True).start()
    await msg.reply_text(
        "✅ *Spot bot ishga tushdi!*\n"
        "📲 Telegram orqali xabar olasiz.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot or not trading_bot.running:
        await msg.reply_text("ℹ️ *Bot hozir ishlamayapti.*", parse_mode="Markdown", reply_markup=get_keyboard())
        return
    trading_bot.running = False
    await msg.reply_text(
        "🛑 *Bot to'xtatilmoqda...*\nOchiq pozitsiyalar sotiladi.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if not trading_bot:
        await msg.reply_text(
            "⚠️ *Bot hali ishga tushmagan.*",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return

    status_icon = "🟢 Ishlayapti" if trading_bot.running else "🔴 To'xtatilgan"
    total   = trading_bot.win_count + trading_bot.loss_count
    wr      = trading_bot.win_count / total * 100 if total > 0 else 0
    pnl_icon = "📈" if trading_bot.total_pnl >= 0 else "📉"
    sign    = "+" if trading_bot.total_pnl >= 0 else ""

    pos_text = ""
    for sym, pos in trading_bot.positions.items():
        pos_text += f"\n  🟢 `{sym}` @ `${pos.entry_price:,.4f}` ({pos.age_seconds:.0f}s)"

    if not pos_text:
        pos_text = "\n  _Pozitsiya yo'q_"

    await msg.reply_text(
        f"📊 *Bot Holati*\n\n"
        f"🔌 Status: *{status_icon}*\n\n"
        f"💼 Ochiq pozitsiyalar: *{len(trading_bot.positions)}*{pos_text}\n\n"
        f"{pnl_icon} Jami PnL: `{sign}{trading_bot.total_pnl:.4f} USDT`\n"
        f"🎯 Win rate: `{wr:.0f}%` (✅{trading_bot.win_count}/❌{trading_bot.loss_count})\n"
        f"🔍 Skanlar: `{trading_bot.scan_count}`",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("⏳ Balans yuklanmoqda...", parse_mode="Markdown")
    api     = MEXCSpot(os.getenv("MEXC_API_KEY", ""), os.getenv("MEXC_SECRET_KEY", ""))
    balance = await api.get_balance("USDT")
    await api.close()
    await msg.reply_text(
        f"💰 *Spot USDT Balans*\n\n"
        f"💵 `{balance:.4f} USDT`",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if not trading_bot or not trading_bot.positions:
        await msg.reply_text("📂 *Ochiq pozitsiyalar yo'q.*", parse_mode="Markdown", reply_markup=get_keyboard())
        return

    text = "📂 *Ochiq Spot Pozitsiyalar:*\n\n"
    for sym, pos in trading_bot.positions.items():
        pnl = pos.pnl_pct(pos.peak_price)
        text += (
            f"🟢 *{sym}*\n"
            f"  💰 Kirish: `${pos.entry_price:,.6f}`\n"
            f"  📦 Miqdor: `{pos.qty:.6f}`\n"
            f"  🎯 TP: `${pos.tp:,.6f}` | 🛡 SL: `${pos.sl:,.6f}`\n"
            f"  ⏱ Vaqt: `{pos.age_seconds:.0f}s`\n\n"
        )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard())


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sl  = float(os.getenv("STOP_LOSS_PCT",      "0.015")) * 100
    tp  = float(os.getenv("TAKE_PROFIT_PCT",    "0.030")) * 100
    tr  = float(os.getenv("MAX_TRADE_PCT",      "0.20"))  * 100
    mp  = os.getenv("MAX_OPEN_POSITIONS", "3")
    ml  = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.10"))  * 100
    await update.effective_message.reply_text(
        f"⚙️ *Spot Bot Sozlamalari*\n\n"
        f"🛡 Stop-Loss: `{sl:.1f}%`\n"
        f"🎯 Take-Profit: `{tp:.1f}%`\n"
        f"💼 Har savdoga: `{tr:.0f}%` balansdan\n"
        f"📊 Max pozitsiyalar: `{mp}`\n"
        f"⚠️ Max kunlik zarar: `{ml:.0f}%`\n\n"
        f"⏱ Skan: har `10s` | Monitor: har `3s`\n"
        f"🔍 Juftliklar: `30` ta\n\n"
        f"📡 *Indikatorlar:*\n"
        f"EMA + RSI + BB + Volume",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    handlers = {
        "start_trading": start_trading_cmd,
        "stop_trading":  stop_trading_cmd,
        "status":        status_cmd,
        "balance":       balance_cmd,
        "positions":     positions_cmd,
        "settings":      settings_cmd,
    }
    if q.data in handlers:
        await handlers[q.data](update, context)


async def run_app():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("start_trading", start_trading_cmd))
    app.add_handler(CommandHandler("stop_trading",  stop_trading_cmd))
    app.add_handler(CommandHandler("status",        status_cmd))
    app.add_handler(CommandHandler("balance",       balance_cmd))
    app.add_handler(CommandHandler("positions",     positions_cmd))
    app.add_handler(CommandHandler("settings",      settings_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Telegram Spot bot ishga tushdi!")

    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        await asyncio.Event().wait()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_app())
