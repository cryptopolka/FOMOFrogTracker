# fomo_frog_tracker.py

import json
import os
import datetime
import requests
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

# ─── 1) Patch JobQueue to avoid weakref bug ─────────────────────────
def _patch_set_application(self, application):
    # store a callable that returns the application
    self._application = lambda: application
JobQueue.set_application = _patch_set_application
# ────────────────────────────────────────────────────────────────────

# ─── 2) Configuration ──────────────────────────────────────────────
TOKEN          = os.getenv("TOKEN", "8199259072:AAHfLDID2q6QGs43LnmF6FsixhdyNOR9pEQ")
CHECK_INTERVAL = 60  # seconds between checks
SPONSORED_MSG  = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"   # { wallet: user_id }
STATE_FILE = "wallet_last_tx.json"    # { wallet: last_seen_digest }

API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ────────────────────────────────────────────────────────────────────

# ─── 3) State persistence ──────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path, "r")) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})  # wallet → user_id
last_seen       = load_json(STATE_FILE, {})  # wallet → last_tx_digest

# ─── 4) Telegram command handlers ─────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "🔥 Features:\n"
        "• 🐋 Whale Wallet Tracker\n"
        "• 📦 Multi‑Wallet Support\n"
        "• 📢 Sponsored Alerts\n\n"
        "👉 Use `/track <wallet>` to begin.\n"
        "👉 Use `/listwallets` to view your wallets.",
        parse_mode="Markdown"
    )

async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /track <wallet_address>")
    w = context.args[0].lower()
    uid = update.effective_chat.id
    tracked_wallets[w] = uid
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
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── 5) Blockchain helpers ─────────────────────────────────────────
def get_latest_txs(wallet):
    r = requests.get(API_TX.format(wallet), timeout=10)
    return r.json() if r.ok else []

def get_balance(wallet):
    r = requests.get(API_BAL.format(wallet), timeout=10)
    if not r.ok:
        return "unknown"
    data = r.json()
    sui = next((b["balance"] for b in data if b["type"]=="SUI"), 0)
    tokens = len([b for b in data if b["type"]!="SUI"])
    return f"{int(sui)/1e9:,.0f} SUI + {tokens} tokens"

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

# ─── 6) Monitor job with debug logs ────────────────────────────────
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    global last_seen
    bot = context.bot

    for wallet, uid in list(tracked_wallets.items()):
        logging.info(f"🔍 Checking wallet {wallet}, last_seen={last_seen.get(wallet)}")
        txs = get_latest_txs(wallet)
        logging.info(f"   → fetched {len(txs)} txs: {[tx['digest'] for tx in txs]}")
        if not txs:
            continue

        latest = txs[0]["digest"]
        logging.info(f"   → latest digest is {latest}")
        if latest == last_seen.get(wallet):
            logging.info("   → no new transactions")
            continue

        unseen = []
        for tx in reversed(txs):
            if tx["digest"] == last_seen.get(wallet):
                break
            unseen.append(tx)
        logging.info(f"   → {len(unseen)} unseen tx(s)")

        for tx in unseen:
            logging.info(f"   → sending alert for digest {tx['digest']}")
            action    = tx.get("action","TX").upper()
            ts        = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
            timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
            token_addr= tx.get("object_id","unknown")
            token_name= tx.get("symbol","unknown")
            amount    = tx.get("amount","")
            balance   = get_balance(wallet)

            msg = (
                f"🐋 *Wallet Activity Alert!*\n"
                f"Wallet: `{shorten(wallet)}`\n"
                f"Action: *{action}*\n"
                f"Token: *{token_name}*\n"
                f"Amount: {amount}\n"
                f"Contract: `{token_addr}`\n"
                f"Balance: {balance}\n"
                f"Time: {timestamp}\n"
                f"Tx: https://suivision.xyz/tx/{tx['digest']}"
                f"{SPONSORED_MSG}"
            )
            await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")

        last_seen[wallet] = latest

    save_json(STATE_FILE, last_seen)

# ─── 7) Entry point ────────────────────────────────────────────────
def main():
    # Clear webhook & pending updates
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
    )

    # Basic logging
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO
    )

    # Build application
    app = ApplicationBuilder().token(TOKEN).build()

    # Register command handlers
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    # Schedule monitor_job every CHECK_INTERVAL seconds
    app.job_queue.run_repeating(
        monitor_job,
        interval=CHECK_INTERVAL,
        first=10
    )

    # Start polling (this call blocks)
    app.run_polling()

if __name__ == "__main__":
    main()
