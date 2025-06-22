import os
import requests
import numpy as np
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ApplicationBuilder, CommandHandler

# ── CONFIG ──────────────────────────────────────────────────
TELE_TOKEN = os.getenv("TELE_TOKEN")
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
OVERBOT    = int(os.getenv("RSI_OVERBOUGHT", 70))
OVERSOLD   = int(os.getenv("RSI_OVERSOLD", 30))
PAIR_IDS   = os.getenv("PAIR_IDS", "").split(",")

# ── DYNAMIC CHAT STORAGE ────────────────────────────────────
# Stores chat IDs of users/groups that started the bot
RECIPIENTS = set()

# ── OPTIONAL LEGIT CHECK STUB ───────────────────────────────
def is_legit(pair_id):
    # TODO: implement your liquidity‑lock, age, holder, volume, audit checks
    return True

# ── RSI CALCULATION ─────────────────────────────────────────
def compute_rsi(prices, period):
    deltas   = np.diff(prices)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.convolve(gains,  np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(losses, np.ones(period)/period, mode='valid')
    rs       = avg_gain / (avg_loss + 1e-8)
    return 100 - (100 / (1 + rs))

# ── /start COMMAND ──────────────────────────────────────────
async def start(update, ctx):
    chat_id = update.effective_chat.id
    RECIPIENTS.add(chat_id)
    await update.message.reply_text(
        "🐸 Welcome to *FOMO Frog Tracker*! I scan your Sui tokens every 30 min.\n"
        "You will receive periodic RSI alerts here.\n"
        "Type /help for more info.",
        parse_mode="Markdown"
    )

# ── /help COMMAND ───────────────────────────────────────────
async def help_cmd(update, ctx):
    await update.message.reply_text(
        "Commands:\n"
        "/start  – Register this chat for periodic RSI alerts\n"
        "/help   – Show this help message\n"
        "The bot scans automatically every 30 min; no further commands needed."
    )

# ── SCAN JOB ─────────────────────────────────────────────────
async def scan_all(app, _):
    overbought, oversold = [], []

    for pid in PAIR_IDS:
        try:
            resp       = requests.get(
                f"https://api.dexscreener.com/latest/dex/pairs/sui/{pid}"
            ).json()["pairs"][0]
            symbol     = resp["symbol"]
            dex        = resp["dexId"]
            liquidity  = float(resp["liquidity"]["usd"])
            closes     = np.array([pt[1] for pt in resp["chart"]])
            rsi_value  = compute_rsi(closes, RSI_PERIOD)[-1]
        except Exception:
            continue

        if not is_legit(pid):
            continue

        label = (
            f"{symbol} on *{dex}* (Liquidity: ${liquidity:,.0f}) — RSI {rsi_value:.1f}"
        )
        if rsi_value > OVERBOT:
            overbought.append(label)
        elif rsi_value < OVERSOLD:
            oversold.append(label)

    report  = "🐸 *FOMO Frog Tracker — RSI Scan Results* (every 30 min)\n\n"
    if overbought:
        report += "⚠️ *Overbought:*\n" + "\n".join(overbought) + "\n\n"
    if oversold:
        report += "✅ *Oversold:*\n"   + "\n".join(oversold) + "\n\n"
    report += (
        "_Note: FOMO Frog Tracker is for alerts only. DYOR—any trades are at your own risk._"
    )

    # Send report to all registered chats
    for chat_id in RECIPIENTS:
        await app.bot.send_message(chat_id, report, parse_mode="Markdown")

# ── MAIN ENTRYPOINT ─────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    # register commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))

    # schedule the 30‑min scanner
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: scan_all(app, None), "interval", minutes=30)
    scheduler.start()

    app.run_polling()
