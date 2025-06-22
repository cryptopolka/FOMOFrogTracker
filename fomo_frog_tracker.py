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
)

# ─── CONFIG ─────────────────────────────────────────────────────
# These must be set in your Render service's Environment Variables:
#  • TOKEN: your BotFather token
#  • WEBHOOK_URL: https://<your‑service>.onrender.com
TOKEN        = os.getenv("TOKEN", "8199259072:AAHfLDID2q6QGs43LnmF6FsixhdyNOR9pEQ")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "https://example.com")
PORT         = int(os.getenv("PORT", "8443"))
CHECK_INTERVAL = 60  # seconds

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"
STATE_FILE = "wallet_last_tx.json"

API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ───────────────────────────────────────────────────────────────────

# ─── STATE PERSISTENCE ────────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path, "r")) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})
last_seen       = load_json(STATE_FILE, {})

# ─── COMMAND HANDLERS ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "• /track <wallet>\n"
        "• /untrack <wallet>\n"
        "• /listwallets\n\n"
        "Alerts will arrive here privately whenever your tracked wallet transacts.",
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
    my = [w for w, u in tracked_wallets.items() if u == uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── ON‑CHAIN HELPERS ─────────────────────────────────────────────
def get_latest_txs(wallet):
    r = requests.get(API_TX.format(wallet), timeout=10)
    return r.json() if r.ok else []

def get_balance(wallet):
    r = requests.get(API_BAL.format(wallet), timeout=10)
    if not r.ok: return "unknown"
    data = r.json()
    sui = next((b["balance"] for b in data if b["type"]=="SUI"), 0)
    toks = len([b for b in data if b["type"]!="SUI"])
    return f"{int(sui)/1e9:,.0f} SUI + {toks} tokens"

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

# ─── MONITOR LOOP ─────────────────────────────────────────────────
async def monitor_wallets(context: ContextTypes.DEFAULT_TYPE):
    global last_seen
    bot = context.bot
    for wallet, uid in list(tracked_wallets.items()):
        logging.info(f"Checking {wallet}, last_seen={last_seen.get(wallet)}")
        txs = get_latest_txs(wallet)
        logging.info(f" → fetched {len(txs)} txs")
        if not txs:
            continue
        latest = txs[0]["digest"]
        if latest == last_seen.get(wallet):
            continue
        unseen = [tx for tx in reversed(txs) 
                  if tx["digest"] != last_seen.get(wallet)]
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
            await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
        last_seen[wallet] = latest
    save_json(STATE_FILE, last_seen)

# ─── ENTRY POINT ─────────────────────────────────────────────────
def main():
    # 1) Clear any webhook + pending updates
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}"
        f"/deleteWebhook?drop_pending_updates=true"
    )

    # 2) Logging
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO
    )

    # 3) Build the application
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
        )
        .build()
    )

    # 4) Register commands
    for cmd, fn in [
        ("start", start),
        ("track", track_cmd),
        ("untrack", untrack_cmd),
        ("listwallets", list_cmd),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # 5) Schedule the monitoring job
    app.job_queue.run_repeating(
        monitor_wallets,
        interval=CHECK_INTERVAL,
        first=10
    )

    # 6) Start webhook (this blocks)
    app.run_webhook()

if __name__ == "__main__":
    main()
