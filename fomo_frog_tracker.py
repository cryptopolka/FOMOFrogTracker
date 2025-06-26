import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── CONFIG ──────────────────────────────────────────────────
DB_PATH    = os.getenv("DB_PATH", "fomo_frog.db")
TELE_TOKEN = os.getenv("TELE_TOKEN")

# ── IN‑MEMORY CHAT REGISTRATION ─────────────────────────────
RECIPIENTS = set()

# ── DATABASE SETUP ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS guesses (
        challenge_ts INTEGER,
        user_id      INTEGER,
        guess        REAL
      )
    """)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS scores (
        user_id INTEGER PRIMARY KEY,
        points  INTEGER
      )
    """)
    conn.commit()
    return conn

# ── UTILITIES ───────────────────────────────────────────────
def fetch_sui_price() -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids":"sui","vs_currencies":"usd"}, timeout=5
    ).json()
    return float(resp.get("sui", {}).get("usd", 0))

def award_points(user_id: int, pts: int):
    conn = init_db()
    cur = conn.execute("SELECT points FROM scores WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        conn.execute("UPDATE scores SET points=points+? WHERE user_id=?", (pts, user_id))
    else:
        conn.execute("INSERT INTO scores(user_id,points) VALUES(?,?)", (user_id, pts))
    conn.commit()

# ── BOT COMMANDS ───────────────────────────────────────────
async def start(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    RECIPIENTS.add(chat_id)
    init_db()
    await update.message.reply_text(
        "🐸 *FOMO Frog Price Guess Bot*\n"
        "I'll post an hourly SUI price challenge. Use /guess <price> to enter.\n"
        "See /score for your total points.",
        parse_mode="Markdown"
    )

async def guess(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    try:
        price = float(ctx.args[0])
    except:
        return await update.message.reply_text("Usage: /guess <price> (e.g. /guess 1.2345)")
    now = int(time.time())
    top = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    challenge_ts = int(top.timestamp())
    if now > challenge_ts + 15*60:
        return await update.message.reply_text("Sorry, guessing is closed for this challenge.")
    conn = init_db()
    conn.execute(
        "INSERT INTO guesses(challenge_ts,user_id,guess) VALUES(?,?,?)",
        (challenge_ts, user, price)
    )
    conn.commit()
    await update.message.reply_text(f"Recorded your guess ${price:.4f} for {top.strftime('%H:%M')} UTC")

async def score(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    conn = init_db()
    cur = conn.execute("SELECT points FROM scores WHERE user_id=?", (user,))
    row = cur.fetchone()
    pts = row[0] if row else 0
    await update.message.reply_text(f"🏅 You have {pts} FOMO points.")

# ── SCHEDULED JOBS ──────────────────────────────────────────
def post_challenge(context: ContextTypes.DEFAULT_TYPE):
    top = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    nxt = (top + timedelta(minutes=30)).strftime("%H:%M")
    for chat_id in RECIPIENTS:
        context.bot.send_message(
            chat_id,
            f"🕐 *Guess the SUI Price!*\n"
            f"What will SUI/USD be at {nxt} UTC?\n"
            "You have 15 min to `/guess <price>`.",
            parse_mode="Markdown"
        )
        # schedule list & reveal
        context.job_queue.run_once(list_guesses, when=15*60, context=(chat_id, top.timestamp()))
        context.job_queue.run_once(reveal, when=30*60, context=(chat_id, top.timestamp()))

async def list_guesses(context: ContextTypes.DEFAULT_TYPE):
    chat_id, ts = context.job.context
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_id,guess FROM guesses WHERE challenge_ts=?", (ts,)
    ).fetchall()
    if not rows:
        msg = "No guesses submitted for this challenge."
    else:
        msg = "📋 *Current Guesses:*\n" + "\n".join(
            f"- [user](tg://user?id={u}): ${g:.4f}" for u, g in rows
        )
    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

async def reveal(context: ContextTypes.DEFAULT_TYPE):
    chat_id, ts = context.job.context
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_id,guess FROM guesses WHERE challenge_ts=?", (ts,)
    ).fetchall()
    actual = fetch_sui_price()
    if not rows:
        return await context.bot.send_message(chat_id, "No entries to reveal.")
    winners = sorted(rows, key=lambda x: abs(x[1]-actual))[:5]
    pts = [5,4,3,2,1]
    msg = (f"🎯 *Reveal!* Actual SUI price: ${actual:.4f}\n\n"
           "🏆 *Top 5 Winners:*\n")
    for (u,g),p in zip(winners, pts):
        msg += f"- [user](tg://user?id={u}) guessed ${g:.4f} → +{p} points\n"
        award_points(u, p)
    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler("score", score))
    app.job_queue.run_repeating(post_challenge, interval=3600, first=0)
    app.run_polling()
