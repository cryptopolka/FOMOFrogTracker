import os
import sqlite3
import requests
import numpy as np
import time
from datetime import datetime, timedelta
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── CONFIG ──────────────────────────────────────────────────
DB_PATH    = os.getenv("DB_PATH", "fomo_frog.db")
TELE_TOKEN = os.getenv("TELE_TOKEN")
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
OVERBOT    = int(os.getenv("RSI_OVERBOUGHT", 70))
OVERSOLD   = int(os.getenv("RSI_OVERSOLD", 30))

# ── DYNAMIC CHAT REGISTRATION ───────────────────────────────
# Store chats where users invoked /start
RECIPIENTS = set()

# ── DATABASE SETUP ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS holdings (
        chat_id TEXT, source TEXT, symbol TEXT,
        amount REAL, cost REAL,
        PRIMARY KEY(chat_id, source, symbol)
      )
    """
    )
    conn.execute("""
      CREATE TABLE IF NOT EXISTS guesses (
        challenge_ts INTEGER, user_id INTEGER, guess REAL
      )
    """
    )
    conn.commit()
    return conn

# ── PRICE FETCHERS ──────────────────────────────────────────
def fetch_price_coingecko(token_id: str) -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": token_id, "vs_currencies": "usd"}, timeout=5
    ).json()
    return float(resp.get(token_id, {}).get("usd", 0))


def fetch_price_moonbags(symbol: str) -> float:
    try:
        resp = requests.get(
            f"https://moonbags.io/api/bondingcurve/{symbol}", timeout=5
        ).json()
        return float(resp.get("currentPrice", 0))
    except:
        return 0.0


def fetch_sui_price() -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids":"sui","vs_currencies":"usd"}, timeout=5
    ).json()
    return float(resp.get("sui", {}).get("usd", 0))

# ── RSI CALCULATION ─────────────────────────────────────────
def compute_rsi(prices, period):
    deltas = np.diff(prices)
    gains  = np.where(deltas>0, deltas, 0)
    losses = np.where(deltas<0, -deltas, 0)
    avg_gain = np.convolve(gains, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(losses, np.ones(period)/period, mode='valid')
    rs = avg_gain / (avg_loss + 1e-8)
    return 100 - (100 / (1 + rs))

# ── /start COMMAND ──────────────────────────────────────────
async def start(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    RECIPIENTS.add(chat_id)
    init_db()
    await update.message.reply_text(
        "🐸 *FOMO Frog Tracker* activated in this chat!\n"
        "Use /add, /remove, /portfolio for portfolio, and /guess for hourly SUI challenges.",
        parse_mode="Markdown"
    )

# ── PORTFOLIO COMMANDS ──────────────────────────────────────
async def add_holding(update, ctx):
    chat_id = str(update.effective_chat.id)
    try:
        src, sym, amt, cost = ctx.args
        amt, cost = float(amt), float(cost)
    except:
        return await update.message.reply_text(
            "Usage: /add <coingecko|moonbags> <symbol> <amount> <cost>"
        )
    conn = init_db()
    conn.execute(
        "REPLACE INTO holdings(chat_id,source,symbol,amount,cost) VALUES(?,?,?,?,?)",
        (chat_id, src, sym.lower(), amt, cost)
    )
    conn.commit()
    await update.message.reply_text(f"Added {amt} {sym} ({src}) at cost {cost}")

async def remove_holding(update, ctx):
    chat_id = str(update.effective_chat.id)
    try:
        src, sym = ctx.args
    except:
        return await update.message.reply_text(
            "Usage: /remove <coingecko|moonbags> <symbol>"
        )
    conn = init_db()
    conn.execute(
        "DELETE FROM holdings WHERE chat_id=? AND source=? AND symbol=?",
        (chat_id, src, sym.lower())
    )
    conn.commit()
    await update.message.reply_text(f"Removed {sym} ({src})")

async def portfolio(update, ctx):
    chat_id = str(update.effective_chat.id)
    conn = init_db()
    rows = conn.execute(
        "SELECT source,symbol,amount,cost FROM holdings WHERE chat_id=?", (chat_id,)
    ).fetchall()
    if not rows:
        return await update.message.reply_text("Your portfolio is empty.")
    total_val = total_cost = 0
    msg = "🐸 *Your Portfolio:*\n"
    for src, sym, amt, cost in rows:
        price = fetch_price_coingecko(sym) if src=="coingecko" else fetch_price_moonbags(sym)
        val = price*amt; pnl = val - cost*amt
        total_val += val; total_cost += cost*amt
        rsi_stat = "N/A"
        if src=="coingecko":
            data = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{sym}/ohlc",
                params={"vs_currency":"usd","days":1}, timeout=5
            ).json()
            closes = [p[4] for p in data]
            if len(closes)>=RSI_PERIOD:
                r = compute_rsi(np.array(closes), RSI_PERIOD)[-1]
                rsi_stat = 'OB' if r>OVERBOT else ('OS' if r<OVERSOLD else f"{r:.1f}")
        msg += (f"\n• {sym.upper()} [{src}]\n"
                f"  amt: {amt}, price: ${price:.4f}, val: ${val:.2f}\n"
                f"  P&L: ${pnl:.2f}, RSI: {rsi_stat}\n")
    msg += f"\n*Total Value:* ${total_val:.2f}\n*Total P&L:* ${total_val-total_cost:.2f}"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ── PRICE‑GUESS COMMAND ──────────────────────────────────────
async def guess(update, ctx):
    user = update.effective_user.id
    try:
        price = float(ctx.args[0])
    except:
        return await update.message.reply_text("Usage: /guess <price>")
    now = int(time.time())
    top = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    challenge_ts = int(top.timestamp())
    if now > challenge_ts + 15*60:
        return await update.message.reply_text("Guessing closed.")
    conn = init_db()
    conn.execute(
        "INSERT INTO guesses(challenge_ts,user_id,guess) VALUES(?,?,?)",
        (challenge_ts, user, price)
    )
    conn.commit()
    await update.message.reply_text(
        f"Recorded ${price:.4f} for {top.strftime('%H:%M')} UTC challenge"
    )

# ── SCHEDULED JOBS ──────────────────────────────────────────
def post_challenge(context: ContextTypes.DEFAULT_TYPE):
    top = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    nxt = (top + timedelta(minutes=30)).strftime("%H:%M")
    for chat_id in RECIPIENTS:
        context.bot.send_message(
            chat_id,
            f"🕐 *Guess the SUI Price!*\n"
            f"What will SUI/USD be at {nxt} UTC?\n"
            "You have 15 min to /guess <price>.",
            parse_mode="Markdown"
        )
        context.job_queue.run_once(list_guesses, when=15*60, context=(chat_id, top.timestamp()))
        context.job_queue.run_once(reveal, when=30*60, context=(chat_id, top.timestamp()))

async def list_guesses(context: ContextTypes.DEFAULT_TYPE):
    chat_id, ts = context.job.context
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT user_id,guess FROM guesses WHERE challenge_ts=?", (ts,)).fetchall()
    if not rows:
        msg = "No guesses submitted."
    else:
        msg = "📋 *Current Guesses:*\n" + "\n".join(
            f"- [user](tg://user?id={u}): ${g:.4f}" for u,g in rows
        )
    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

async def reveal(context: ContextTypes.DEFAULT_TYPE):
    chat_id, ts = context.job.context
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT user_id,guess FROM guesses WHERE challenge_ts=?", (ts,)).fetchall()
    actual = fetch_sui_price()
    if not rows:
        return await context.bot.send_message(chat_id, "No entries to reveal.")
    winners = sorted(rows, key=lambda x: abs(x[1]-actual))[:5]
    pts = [5,4,3,2,1]
    msg = (f"🎯 *Reveal!* SUI price: ${actual:.4f}\n\n"
           "🏆 *Top 5 Winners:*\n")
    for (u,g),p in zip(winners, pts):
        msg += f"- [user](tg://user?id={u}) guessed ${g:.4f} → +{p} points\n"
    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

# ── MAIN ENTRYPOINT ─────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_holding))
    app.add_handler(CommandHandler("remove", remove_holding))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("guess", guess))
    app.job_queue.run_repeating(post_challenge, interval=3600, first=0)
    app.run_polling()
