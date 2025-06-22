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

# ── DYNAMIC CHAT STORAGE ────────────────────────────────────
RECIPIENTS = set()

# ── OPTIONAL LEGIT CHECK STUB ───────────────────────────────
def is_legit(pair):
    # TODO: implement liquidity‑lock, age, holder, volume, audit checks
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
        "🐸 Welcome to *FOMO Frog Tracker*! I scan top Sui token pairs every 30 min.\n"
        "You will receive a report of the top 5 overbought & oversold tokens by RSI.\n"
        "Type /help for more info.",
        parse_mode="Markdown"
    )

# ── /help COMMAND ───────────────────────────────────────────
async def help_cmd(update, ctx):
    await update.message.reply_text(
        "Commands:\n"
        "/start  – Register this chat for periodic RSI scans\n"
        "/help   – Show this help message\n"
        "Scans top pairs on Dexscreener every 30 min, no further commands needed."
    )

# ── SCAN JOB ─────────────────────────────────────────────────
async def scan_all(app, _):
    overbought, oversold = [], []
    entries = []

    # Fetch all Sui pairs from Dexscreener
    resp = requests.get(
        "https://api.dexscreener.com/latest/dex/pairs/sui"
    ).json().get("pairs", [])

    for pair in resp:
        try:
            if not is_legit(pair):
                continue
            symbol    = pair.get("symbol")
            dex       = pair.get("dexId")
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            # extract price series
            closes    = np.array([pt[1] for pt in pair.get("chart", [])])
            if len(closes) < RSI_PERIOD:
                continue
            rsi_value = compute_rsi(closes, RSI_PERIOD)[-1]
            entries.append({
                "symbol": symbol,
                "dex": dex,
                "liquidity": liquidity,
                "rsi": rsi_value
            })
        except Exception:
            continue

    # Sort by RSI
    sorted_entries = sorted(entries, key=lambda x: x["rsi"])
    # Top 5 oversold (lowest RSI)
    oversold_list   = sorted_entries[:5]
    # Top 5 overbought (highest RSI)
    overbought_list = sorted_entries[-5:][::-1]

    # Build report
    report  = "🐸 *FOMO Frog Tracker — RSI Scan Results* (every 30 min)\n\n"
    if overbought_list:
        report += "⚠️ *Top 5 Overbought:*\n"
        for e in overbought_list:
            report += f"{e['symbol']} on *{e['dex']}* (Liq ${e['liquidity']:,.0f}) — RSI {e['rsi']:.1f}\n"
        report += "\n"
    if oversold_list:
        report += "✅ *Top 5 Oversold:*\n"
        for e in oversold_list:
            report += f"{e['symbol']} on *{e['dex']}* (Liq ${e['liquidity']:,.0f}) — RSI {e['rsi']:.1f}\n"
        report += "\n"
    report += (
        "_Note: FOMO Frog Tracker is for alerts only. DYOR—any trades are at your own risk._"
    )

    # Broadcast to registered chats
    for chat_id in RECIPIENTS:
        await app.bot.send_message(chat_id, report, parse_mode="Markdown")

# ── MAIN ENTRYPOINT ─────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: scan_all(app, None), "interval", minutes=30)
    scheduler.start()
    app.run_polling()
