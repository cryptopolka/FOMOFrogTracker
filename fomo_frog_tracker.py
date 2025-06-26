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
        "🐸 *FOMO Frog Price‑Guess Bot*\n"
        "Hourly SUI‑price challenge lives here. Use `/guess <price>` to enter.\n"
        "Check `/score` for your total points.",
        parse_mode="Markdown"
    )

async def guess(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    try:
        price = float(ctx.args[0])
    except:
        return await update.message.reply_text("Usage: `/guess <price>` (e.g. `/guess 1.2345`)")
    now = int(time.time())
    top = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    challenge_ts = int(top.timestamp())
    if now > challenge_ts + 15*60:
        return await update.message.reply_text("⏰ Guessing closed for this challenge.")
    conn = init_db()
    conn.execute(
        "INSERT INTO guesses(challenge_ts,user_id,guess) VALUES(?,?,?)",
        (challenge_ts, user, price)
    )
    conn.commit()
    await update.message.reply_text(f"✅ Recorded ${price:.4f} for {top.strftime('%H:%M')} UTC challenge")

async def score(update: ContextTypes.DEFAULT_TYPE, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    conn = init_db()
    cur = conn.execute("SELECT points FROM scores WHERE user_id=?", (user,))
    row = cur.fetchone()
    pts = row[0] if row else 0
    await update.message.reply_text(f"🏅 You have *{pts}* FOMO points.", parse_mode="Markdown")

# ── SCHEDULE
