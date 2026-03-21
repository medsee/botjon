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

S = {
    "rocket":"🚀","fire":"🔥","gem":"💎","money":"💰",
    "chart_up":"📈","chart_dn":"📉","win":"✅","loss":"❌",
    "warn":"⚠️","shield":"🛡","target":"🎯","clock":"⏱",
    "coin":"🪙","bank":"🏦","stats":"📊","bolt":"⚡",
    "stop":"🛑","ok":"👌","trophy":"🏆","green":"🟢",
    "red":"🔴","dragon":"🐉","muscle":"💪","star":"⭐",
}


def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{S['rocket']} Ishga tushir", callback_data="start_trading"),
            InlineKeyboardButton(f"{S['stop']} To'xtat",        callback_data="stop_trading"),
        ],
        [
            InlineKeyboardButton(f"{S['stats']} Holat",         callback_data="status"),
            InlineKeyboardButton(f"{S['bank']} Balans",         callback_data="balance"),
        ],
        [
            InlineKeyboardButton(f"{S['gem']} Pozitsiyalar",    callback_data="positions"),
            InlineKeyboardButton(f"⚙️ Sozlamalar",              callback_data="settings"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{S['dragon']} *MEXC Spot Scalping Bot ULTRA PRO* {S['fire']}\n\n"
        f"{S['green']} Long only | ATR TP/SL | Sliv yo'q\n"
        f"{S['shield']} Break-even + Trailing Stop + HardSL\n"
        f"{S['bolt']} EMA · RSI · StochRSI · BB · Volume\n"
        f"{S['rocket']} Darhol analiz va pozitsiya ochadi!\n\n"
        f"👇 Quyidagi tugmani bosing:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def start_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if trading_bot and trading_bot.running:
        await msg.reply_text(
            f"{S['warn']} *Bot allaqachon ishlayapti!*\n"
            f"Holat tugmasini bosib tekshiring.",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return

    api_key = os.getenv("MEXC_API_KEY", "")
    if not api_key:
        await msg.reply_text(
            f"{S['loss']} *MEXC API kaliti topilmadi!*",
            parse_mode="Markdown",
        )
        return

    await msg.reply_text(
        f"{S['rocket']} *Bot ishga tushirilmoqda...*\n"
        f"{S['bolt']} Darhol bozor analiz qilinadi!",
        parse_mode="Markdown",
    )

    trading_bot = SpotBot()
    trading_bot.telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    trading_bot.telegram_chat_id = str(update.effective_chat.id)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(trading_bot.run())

    threading.Thread(target=run_bot, daemon=True).start()
    await msg.reply_text(
        f"{S['win']} *Bot ishga tushdi!*\n"
        f"{S['fire']} Telegram orqali xabar olasiz.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message
    if not trading_bot or not trading_bot.running:
        await msg.reply_text(
            f"ℹ️ *Bot hozir ishlamayapti.*",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return
    trading_bot.running = False
    await msg.reply_text(
        f"{S['stop']} *Bot to'xtatilmoqda...*\n"
        f"Ochiq pozitsiyalar sotiladi.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if not trading_bot:
        await msg.reply_text(
            f"{S['warn']} *Bot hali ishga tushmagan.*\n"
            f"{S['rocket']} Ishga tushirish tugmasini bosing!",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return

    status_icon = f"{S['green']} Ishlayapti" if trading_bot.running else f"{S['red']} To'xtatilgan"
    total   = trading_bot.win_count + trading_bot.loss_count
    wr      = trading_bot.win_count / total * 100 if total > 0 else 0
    pnl_ico = S['chart_up'] if trading_bot.total_pnl >= 0 else S['chart_dn']
    sign    = "+" if trading_bot.total_pnl >= 0 else ""

    pos_lines = ""
    for sym, pos in trading_bot.positions.items():
        age = int(pos.age_seconds)
        pos_lines += f"\n  {S['gem']} `{sym}` @ `${pos.entry_price:,.4f}` ({age}s)"
    if not pos_lines:
        pos_lines = f"\n  _Pozitsiya yo'q_"

    await msg.reply_text(
        f"{S['stats']} *Bot Holati*\n\n"
        f"🔌 Status: *{status_icon}*\n\n"
        f"{S['gem']} Ochiq: *{len(trading_bot.positions)}*{pos_lines}\n\n"
        f"{pnl_ico} Jami PnL: `{sign}{trading_bot.total_pnl:.4f} USDT`\n"
        f"{S['trophy']} Win rate: `{wr:.0f}%` (✅{trading_bot.win_count}/❌{trading_bot.loss_count})\n"
        f"{S['bolt']} Skanlar: `{trading_bot.scan_count}`",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text(f"{S['clock']} Balans yuklanmoqda...", parse_mode="Markdown")
    api = MEXCSpot(os.getenv("MEXC_API_KEY", ""), os.getenv("MEXC_SECRET_KEY", ""))
    bal = await api.get_balance("USDT")
    await api.close()
    await msg.reply_text(
        f"{S['bank']} *Spot USDT Balans*\n\n"
        f"{S['money']} `{bal:.4f} USDT`",
        parse_mode="Markdown",
        reply_markup=get_keyboard(),
    )


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_bot
    msg = update.effective_message

    if not trading_bot or not trading_bot.positions:
        await msg.reply_text(
            f"{S['gem']} *Ochiq pozitsiyalar yo'q.*",
            parse_mode="Markdown",
            reply_markup=get_keyboard(),
        )
        return

    text = f"{S['gem']} *Ochiq Pozitsiyalar:*\n\n"
    for sym, pos in trading_bot.positions.items():
        age  = int(pos.age_seconds)
        sl_p = (pos.entry_price - pos.sl) / pos.entry_price * 100
        tp_p = (pos.tp - pos.entry_price) / pos.entry_price * 100
        text += (
            f"{S['rocket']} *{sym}*\n"
            f"  {S['money']} Kirish: `${pos.entry_price:,.6f}`\n"
            f"  {S['coin']} Miqdor: `{pos.qty:.6f}`\n"
            f"  {S['target']} TP: `${pos.tp:,.6f}` _(+{tp_p:.1f}%)_\n"
            f"  {S['shield']} SL: `${pos.sl:,.6f}` _(-{sl_p:.1f}%)_\n"
            f"  {S['clock']} Vaqt: `{age}s`\n\n"
        )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard())


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"⚙️ *Bot Sozlamalari*\n\n"
        f"{S['shield']} Stop-Loss: `ATR x1.5` (max `2.5%`)\n"
        f"{S['target']} Take-Profit: `ATR x3.0`\n"
        f"{S['coin']} Har savdoga: `25%` balansdan\n"
        f"{S['stats']} Max pozitsiyalar: `3`\n"
        f"{S['warn']} Max kunlik zarar: `8%`\n"
        f"{S['clock']} Max ushlanish: `15 daqiqa`\n\n"
        f"{S['bolt']} Skan: har `8s` | Monitor: `2s`\n"
        f"{S['gem']} Juftliklar: `40` ta\n\n"
        f"{S['fire']} *Himoya tizimi:*\n"
        f"• HardSL (o'zgarmas 2.5%)\n"
        f"• Break-even (1.5% foydada)\n"
        f"• Trailing Stop (2.5% foydadan)\n"
        f"• Kunlik zarar limiti (8%)\n"
        f"• Max vaqt limiti (15 daqiqa)",
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

    logger.info("Telegram Spot Bot ULTRA PRO ishga tushdi!")

    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        await asyncio.Event().wait()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_app())
