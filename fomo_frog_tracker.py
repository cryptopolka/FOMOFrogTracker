import os
import time
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

# ── SECURITY CHECKS ─────────────────────────────────────────
def is_liquidity_sufficient(pair, min_usd=1000):
    try:
        return float(pair.get("liquidity", {}).get("usd", 0)) >= min_usd
    except:
        return False

def is_token_old_enough(pair, min_hours=24):
    # chart timestamps are ms-since-epoch, oldest first
    chart = pair.get("chart", [])
    if not chart:
        return False
    first_ts = chart[0][0] / 1000
    age_hours = (time.time() - first_ts) / 3600
    return age_hours >= min_hours

def is_price_change_reasonable(pair, max_change_pct=100):
    # filter out tokens with > max_change_pct% 24h swing
    change = pair.get("priceChange", {}).get("h24", "0%")
    try:
        pct = abs(float(change.strip("%")))
        return pct <= max_change_pct
    except:
        return False

# Placeholder for holder distribution, social/audit checks
# def is_holder_distribution_ok(pair): ...
# def is_social_audit_valid(pair): ...

# Combined legitimacy check
def is_legit(pair):
    return (
        is_liquidity_sufficient(pair)
        and is_token_old_enough(pair)
        and is_price_change_reasonable(pair)
        # and is_holder_distribution_ok(pair)
        # and is_social_audit_valid(pair)
    )

# ── RSI CALCULATION ─────────────────────────────────────────
def compute_rsi(prices, period):
    deltas   = np.diff(prices)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.convolve(gains,  np.ones(period)/period, mode='valid')
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
