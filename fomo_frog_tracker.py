# fomo_frog_tracker.py

import os
import json
import datetime
import logging
import requests

# ─── Silence verbose HTTP logs ───────────────────────────────────
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# ─── Patch JobQueue weakref bug ──────────────────────────────────
import telegram.ext._jobqueue as _jq
def _patch_set_app(self, application):
    self._application = lambda: application
_jq.JobQueue.set_application = _patch_set_app

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ─── CONFIG ──────────────────────────────────────────────────────
TOKEN         = os.getenv("TOKEN")        # BotFather token
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")  # e.g. https://fomo-frog-tracker.onrender.com
PORT          = int(os.getenv("PORT", "80"))
CHECK_INTERVAL = 60                       # seconds

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE    = "tracked_wallets.json"
STATE_FILE    = "wallet_last_tx.json"

API_TX        = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL       = "https://api.suiscan.xyz/v1/accounts/{}/balances"
FALLBACK_RPC  = "https://fullnode.mainnet.sui.io:443"  # JSON‑RPC endpoint
# ──────────────────────────────────────────────────────────────────

# ─── State persistence ────────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})  # wallet → chat_id
last_seen       = load_json(STATE_FILE, {})  # wallet → last_digest

# ─── Command handlers ─────────────────────────────────────────────
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
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── On‑chain helpers (with REST→RPC fallback) ────────────────────
def get_latest_txs(wallet):
    try:
        r = requests.get(API_TX.format(wallet), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning(f"Primary API failed for {wallet}: {e}")
        # Fallback via JSON‑RPC
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sui_getTransactions",
                "params": [wallet, 5]
            }
            rpc = requests.post(FALLBACK_RPC, json=payload, timeout=10)
            rpc.raise_for_status()
            return rpc.json().get("result", [])
        except Exception as e2:
            logging.warning(f"Fallback RPC failed for {wallet}: {e2}")
            return []

def get_balance(wallet):
    try:
        r = requests.get(API_BAL.format(wallet), timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.warning(f"Balance fetch failed for {wallet}: {e}")
        return "unknown"
    sui = next((b["balance"] for b in data if b["type"]=="SUI"), 0)
    toks = len([b for b in data if b["type"]!="SUI"])
    return f"{int(sui)/1e9:,.0f} SUI + {toks} tokens"

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

# ─── Background monitor job ───────────────────────────────────────
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
        logging.info(f" → {len(unseen)} new tx(s) for {wallet}")

        for tx in unseen:
            ts   = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
            when = ts.strftime("%Y-%m-%d %H:%M:%S")
            addr = tx.get("object_id","unknown")
            sym  = tx.get("symbol","unknown")
            amt  = tx.get("amount","")
            bal  = get_balance(wallet)
            msg  = (
                f"🐋 *Wallet Alert!*\n"
                f"`{shorten(wallet)}` • *{tx.get('action','TX').upper()}*\n"
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

# ─── ENTRY POINT ─────────────────────────────────────────────────
def main():
    # Clear old webhook + pending updates
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}"
        f"/deleteWebhook?drop_pending_updates=true"
    )
    # Set new webhook endpoint
    endpoint = f"{WEBHOOK_URL}/{TOKEN}"
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}"
        f"/setWebhook?url={endpoint}"
    )

    # Logging
    logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO)

    # Build and configure the bot
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    # Schedule background monitoring
    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10)

    # Start the webhook server (blocks)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=endpoint,
    )

if __name__ == "__main__":
    main()
