# fomo_frog_tracker.py

import os
import json
import datetime
import logging
import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

# ─── 1) Patch JobQueue for PTB 20.3 weakref bug ─────────────────
import telegram.ext._jobqueue as _jq
def _patch_set_app(self, application):
    self._application = lambda: application
_jq.JobQueue.set_application = _patch_set_app
# ─────────────────────────────────────────────────────────────────

# ─── 2) Configuration ────────────────────────────────────────────
TOKEN        = os.getenv("TOKEN")        # set this in Render’s Env
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")  # e.g. https://fomo-frog-tracker.onrender.com
PORT         = int(os.getenv("PORT", "80"))
CHECK_INTERVAL = 60                      # seconds between SUI checks

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE  = "tracked_wallets.json"
STATE_FILE  = "wallet_last_tx.json"
API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ───────────────────────────────────────────────────────────────────

# ─── 3) State load/save ───────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path, "r")) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})  # wallet → chat_id
last_seen       = load_json(STATE_FILE, {})  # wallet → last_digest

# ─── 4) Command handlers ──────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "• /track `<wallet>`\n"
        "• /untrack `<wallet>`\n"
        "• /listwallets\n\n"
        "You’ll get private alerts when your tracked wallets transact.",
        parse_mode="Markdown"
    )

async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /track <wallet_address>")
    w = context.args[0].lower()
    tracked_wallets[w] = update.effective_chat.id
    save_json(TRACK_FILE, tracked_wallets)
    await update.message.reply_text(f"✅ Now tracking `{w}`", parse_mode="Markdown")

async def untrack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /untrack <wallet_address>")
    w = context.args[0].lower()
    uid = update.effective_chat.id
    if tracked_wallets.get(w) == uid:
        tracked_wallets.pop(w)
        save_json(TRACK_FILE, tracked_wallets)
        await update.message.reply_text(f"❌ Untracked `{w}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("That wallet isn’t in your list.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    my = [w for w,u in tracked_wallets.items() if u == uid]
    if not my:
        return await update.message.reply_text("No wallets tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── 5) On‑chain helpers ─────────────────────────────────────────
def get_latest_txs(wallet):
    r = requests.get(API_TX.format(wallet), timeout=10)
    return r.json() if r.ok else []

def get_balance(wallet):
    r = requests.get(API_BAL.format(wallet), timeout=10)
    if not r.ok:
        return "unknown"
    data = r.json()
    sui = next((b["balance"] for b in data if b["type"]=="SUI"), 0)
    toks = len([b for b in data if b["type"]!="SUI"])
    return f"{int(sui)/1e9:,.0f} SUI + {toks} tokens"

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

# ─── 6) Background monitor job ────────────────────────────────────
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    global last_seen
    bot = context.bot
    for wallet, chat_id in list(tracked_wallets.items()):
        logging.info(f"Checking {wallet}, last_seen={last_seen.get(wallet)}")
        txs = get_latest_txs(wallet)
        if not txs:
            continue
        latest = txs[0]["digest"]
        if latest == last_seen.get(wallet):
            continue
        unseen = [tx for tx in reversed(txs) if tx["digest"] != last_seen.get(wallet)]
        for tx in unseen:
            action = tx.get("action","TX").upper()
            ts = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
            when = ts.strftime("%Y-%m-%d %H:%M:%S")
            addr = tx.get("object_id","unknown")
            sym  = tx.get("symbol","unknown")
            amt  = tx.get("amount","")
            bal  = get_balance(wallet)
            msg = (
                f"🐋 *Wallet Alert!*\n"
                f"`{shorten(wallet)}` • *{action}*\n"
                f"{sym} • {amt}\n"
                f"Contract `{addr}`\n"
                f"Balance: {bal}\n"
                f"Time: {when}\n"
                f"Tx: https://suivision.xyz/tx/{tx['digest']}"
                f"{SPONSORED_MSG}"
            )
            await bot.send_message(chat_id, msg, parse_mode="Markdown")
        last_seen[wallet] = latest
    save_json(STATE_FILE, last_seen)

# ─── 7) Entry point ───────────────────────────────────────────────
def main():
    # Clear any old webhook + pending updates
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}"
        f"/deleteWebhook?drop_pending_updates=true"
    )

    # Logging
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s", 
        level=logging.INFO
    )

    # Build app
    app = ApplicationBuilder().token(TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    # Schedule the on‑chain monitor
    app.job_queue.run_repeating(
        monitor_job, interval=CHECK_INTERVAL, first=10
    )

    # Set webhook endpoint
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    app.bot.set_webhook(webhook_url)

    # Start the webhook server (blocks)
    app.run_webhook(
        listen="0.0.0.0", 
        port=PORT, 
        url_path=TOKEN, 
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
