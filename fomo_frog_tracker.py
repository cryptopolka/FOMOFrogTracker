# fomo_frog_tracker.py

import os
import json
import datetime
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ─── CONFIG ───────────────────────────────────────────────────────
TOKEN           = os.getenv("TOKEN")                 # Telegram bot token
WEBHOOK_URL     = os.getenv("WEBHOOK_URL")           # e.g. https://<your-service>.onrender.com
PORT            = int(os.getenv("PORT", "80"))
CHECK_INTERVAL  = 60                                  # seconds between checks

RAIDENX_KEY     = os.getenv("RAIDENX_KEY")           # your API key
RAIDENX_NETWORK = os.getenv("RAIDENX_NETWORK", "sui")# network identifier
API_BASE        = "https://api-public.raidenx.io"
API_TX_URL      = f"{API_BASE}/{RAIDENX_NETWORK}/v1/wallet/tx_list"

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE  = "tracked_wallets.json"
STATE_FILE  = "wallet_last_tx.json"

# ─── SETUP LOGGING ─────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ─── PATCH JobQueue weakref bug ────────────────────────────────────
import telegram.ext._jobqueue as _jq
def _patch_set_app(self, application):
    self._application = lambda: application
_jq.JobQueue.set_application = _patch_set_app

# ─── PERSISTENCE ────────────────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})
last_seen       = load_json(STATE_FILE, {})

# ─── TELEGRAM COMMANDS ──────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker (RaidenX‑Sui edition)!*\n\n"
        "• /track `<wallet>`\n"
        "• /untrack `<wallet>`\n"
        "• /listwallets\n\n"
        "Alerts fire on any new RaidenX txs for your tracked wallets.",
        parse_mode="Markdown"
    )

async def track_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /track <wallet_address>")
    w = ctx.args[0].lower()
    tracked_wallets[w] = update.effective_chat.id
    save_json(TRACK_FILE, tracked_wallets)
    await update.message.reply_text(f"✅ Now tracking `{w}`", parse_mode="Markdown")

async def untrack_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /untrack <wallet_address>")
    w   = ctx.args[0].lower()
    uid = update.effective_chat.id
    if tracked_wallets.get(w) == uid:
        tracked_wallets.pop(w)
        save_json(TRACK_FILE, tracked_wallets)
        await update.message.reply_text(f"❌ Untracked `{w}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("That wallet isn’t in your list.")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    my  = [w for w,u in tracked_wallets.items() if u == uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    msg = "📋 *Your wallets:*\n" + "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(msg, parse_mode="Markdown")

# ─── RAIDENX FETCHER ───────────────────────────────────────────────
def get_latest_txs(wallet):
    headers = {
        "accept": "application/json",
        "X-API-Key": RAIDENX_KEY
    }
    params = {"address": wallet, "limit": 5}
    try:
        resp = requests.get(API_TX_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        logging.warning(f"RaidenX API error for {wallet}: {e}")
        return []

    if not data:
        logging.info(f"RaidenX returned no txs for {wallet}")
        return []

    txs = []
    for it in data:
        txs.append({
            "digest":       it.get("txHash", ""),
            "action":       it.get("type", ""),
            "timestamp_ms": it.get("timestamp", 0),
            "symbol":       it.get("tokenSymbol", ""),
            "amount":       it.get("amount", ""),
            "pair":         it.get("pair", "")
        })
    return txs

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

# ─── MONITOR JOB ────────────────────────────────────────────────────
async def monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    global last_seen
    bot = ctx.bot

    for wallet, chat_id in list(tracked_wallets.items()):
        logging.info(f"Checking {wallet}, last_seen={last_seen.get(wallet)}")
        txs = get_latest_txs(wallet)
        if not txs:
            continue

        newest = txs[0]["digest"]
        if newest == last_seen.get(wallet):
            continue

        unseen = [tx for tx in reversed(txs) if tx["digest"] != last_seen.get(wallet)]
        logging.info(f" → {len(unseen)} new tx(s) for {wallet}")

        for tx in unseen:
            ts   = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
            when = ts.strftime("%Y-%m-%d %H:%M:%S")
            msg  = (
                f"🐋 *RaidenX Tx Alert!*\n"
                f"`{shorten(wallet)}` • *{tx['action'].upper()}*\n"
                f"{tx['symbol']} • {tx['amount']}\n"
                f"Pair: `{tx['pair']}`\n"
                f"Time: {when}\n"
                f"Tx: https://raidenx.io/tx/{tx['digest']}"
                f"{SPONSORED_MSG}"
            )
            await bot.send_message(chat_id, msg, parse_mode="Markdown")

        last_seen[wallet] = newest

    save_json(STATE_FILE, last_seen)

# ─── ENTRY POINT ───────────────────────────────────────────────────
def main():
    # Clear old webhook & pending updates
    requests.post(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true")
    endpoint = f"{WEBHOOK_URL}/{TOKEN}"
    requests.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={endpoint}")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("track",       track_cmd))
    app.add_handler(CommandHandler("untrack",     untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10)
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=endpoint)

if __name__ == "__main__":
    main()
