import os
import time
import requests
import numpy as np
from telegram.ext import ApplicationBuilder, CommandHandler

# ── CONFIG ──────────────────────────────────────────────────
TELE_TOKEN = os.getenv("TELE_TOKEN")
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
OVERBOT    = int(os.getenv("RSI_OVERBOUGHT", 70))
OVERSOLD   = int(os.getenv("RSI_OVERSOLD", 30))

# ── DYNAMIC CHAT STORAGE ────────────────────────────────────
RECIPIENTS = set()

# ── SECURITY CHECKS ─────────────────────────────────────────
def is_liquidity_sufficient(pair, min_usd=1000):
    try:
        return float(pair.get("liquidity", {}).get("usd", 0)) >= min_usd
    except:
        return False

def is_token_old_enough(pair, min_hours=24):
    chart = pair.get("chart", [])
    if not chart:
        return False
    first_ts = chart[0][0] / 1000.0
    return (time.time() - first_ts) / 3600.0 >= min_hours

def is_price_change_reasonable(pair, max_change_pct=100):
    change = pair.get("priceChange", {}).get("h24", "0%")
    try:
        pct = abs(float(change.strip("%")))
        return pct <= max_change_pct
    except:
        return False

# Combined legitimacy check
def is_legit(pair):
    return (
        is_liquidity_sufficient(pair)
        and is_token_old_enough(pair)
        and is_price_change_reasonable(pair)
    )

# ── RSI CALCULATION ─────────────────────────────────────────
def compute_rsi(prices, period):
    deltas    = np.diff(prices)
    gains     = np.where(deltas > 0, deltas, 0)
    losses    = np.where(deltas < 0, -deltas, 0)
    avg_gain  = np.convolve(gains,  np.ones(period)/period, mode='valid')
    avg_loss  = np.convolve(losses, np.ones(period)/period, mode='valid')
    rs        = avg_gain / (avg_loss + 1e-8)
    return 100 - (100 / (1 + rs))

# ── BOT COMMANDS ───────────────────────────────────────────
async def start(update, context):
    chat_id = update.effective_chat.id
    RECIPIENTS.add(chat_id)
    await update.message.reply_text(
        "🐸 Welcome to *FOMO Frog Tracker*! I scan top Sui pairs every 30 min.\n"
        "You will receive top 5 overbought & oversold tokens by RSI after security checks.\n"
        "Type /help for more info.",
        parse_mode="Markdown"
    )

async def help_cmd(update, context):
    await update.message.reply_text(
        "Commands:\n"
        "/start  – Register this chat for RSI alerts\n"
        "/help   – Show this help message\n"
        "The bot auto-scans top tokens every 30 min with security filters."
    )

# ── SCAN JOB ─────────────────────────────────────────────────
async def scan_job(context):
    entries = []
    try:
        data = requests.get(
            "https://api.dexscreener.com/latest/dex/pairs/sui"
        ).json().get("pairs", [])
    except:
        return

    for pair in data:
        if not is_legit(pair):
            continue
        closes = [pt[1] for pt in pair.get("chart", [])]
        if len(closes) < RSI_PERIOD:
            continue
        rsi = compute_rsi(np.array(closes), RSI_PERIOD)[-1]
        entries.append({
            'symbol':    pair.get('symbol'),
            'dex':       pair.get('dexId'),
            'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
            'rsi':       rsi
        })

    sorted_by_rsi   = sorted(entries, key=lambda x: x['rsi'])
    oversold_list   = sorted_by_rsi[:5]
    overbought_list = sorted_by_rsi[-5:][::-1]

    report = "🐸 *FOMO Frog Tracker — RSI Scan Results* (every 30 min)\n\n"
    if overbought_list:
        report += "⚠️ *Top 5 Overbought:*\n"
        for e in overbought_list:
            report += (
                f"{e['symbol']} on *{e['dex']}* "
                f"(Liq ${e['liquidity']:,.0f}) — RSI {e['rsi']:.1f}\n"
            )
        report += "\n"
    if oversold_list:
        report += "✅ *Top 5 Oversold:*\n"
        for e in oversold_list:
            report += (
                f"{e['symbol']} on *{e['dex']}* "
                f"(Liq ${e['liquidity']:,.0f}) — RSI {e['rsi']:.1f}\n"
            )
        report += "\n"
    report += (
        "_Note: FOMO Frog Tracker is for alerts only. "
        "DYOR—any trades are at your own risk._"
    )

    for chat_id in RECIPIENTS:
        await context.bot.send_message(chat_id, report, parse_mode="Markdown")

# ── MAIN ENTRYPOINT ─────────────────────────────────────────
if __name__ == "__main__":
    # Build and register handlers
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))

    # Schedule recurring scan: every 30m, first in 60s
    app.job_queue.run_repeating(scan_job, interval=1800, first=60)

    # Start long-polling
    app.run_polling()
