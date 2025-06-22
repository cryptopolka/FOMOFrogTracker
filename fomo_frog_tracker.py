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
TOKEN               = os.getenv("TOKEN")  # your Telegram bot token
WEBHOOK_URL         = os.getenv("WEBHOOK_URL")  # e.g. https://fomo-frog-tracker.onrender.com
PORT                = int(os.getenv("PORT", "80"))
CHECK_INTERVAL      = 60  # seconds

# Your BlockVision (SuiVision) API key, hard‑coded as provided
BLOCKVISION_API_KEY = "2yrKC52obCEwlOti0AVSr1RMCcF"

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE  = "tracked_wallets.json"
STATE_FILE  = "wallet_last_tx.json"

# Base URL for your SuiVision v1 HTTP endpoints
BV_BASE = f"https://sui-mainnet.blockvision.org/v1/{BLOCKVISION_API_KEY}"
# JSON‑RPC fallback endpoint
RPC_URL = "https://fullnode.mainnet.sui.io:443"
# ──────────────────────────────────────────────────────────────────

# ─── State persistence ────────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})  # wallet → chat_id
last_seen       = load_json(STATE_FILE, {})  # wallet → last_digest

# ─── Telegram command handlers ────────────────────────────────────
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
    w  = context.args[0].lower()
    uid = update.effective_chat.id
    if tracked_wallets.get(w) == uid:
        tracked_wallets.pop(w)
        save_json(TRACK_FILE, tracked_wallets)
        await update.message.reply_text(f"❌ Untracked `{w}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("That wallet isn’t in your list.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    my  = [w for w,u in tracked_wallets.items() if u == uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── On‑chain helpers w/ HTTP→RPC fallback ────────────────────────
def get_latest_txs(wallet):
    # 1) Try SuiVision v1 HTTP
    try:
        url = f"{BV_BASE}/account/activities"
        r   = requests.get(url, params={"address": wallet, "limit": 5}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            return [{
                "digest":       it.get("digest", ""),
                "action":       it.get("type", "TX"),
                "timestamp_ms": it.get("timestampMs", 0),
                "object_id":    (it.get("interactAddresses") or [{}])[0].get("address", ""),
                "symbol":       (it.get("coinChanges") or [{}])[0].get("symbol", ""),
                "amount":       (it.get("coinChanges") or [{}])[0].get("amount", ""),
            } for it in data]
    except Exception as e:
        logging.warning(f"BlockVision HTTP failed for {wallet}: {e}")

    # 2) Fallback to JSON‑RPC (only digests)
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "sui_getTransactions", "params": [wallet, 5]}
        rpc     = requests.post(RPC_URL, json=payload, timeout=10)
        rpc.raise_for_status()
        digs    = rpc.json().get("result", [])
        now_ms  = int(datetime.datetime.utcnow().timestamp() * 1000)
        return [{
            "digest":       d,
            "action":       "TX",
            "timestamp_ms": now_ms,
            "object_id":    "",
            "symbol":       "",
            "amount":       "",
        } for d in digs if isinstance(d, str)]
    except Exception as e2:
        logging.warning(f"RPC fallback failed for {wallet}: {e2}")
        return []

def get_balance(wallet):
    # 1) Try SuiVision v1 HTTP coins endpoint
    try:
        url   = f"{BV_BASE}/account/coins"
        r     = requests.get(url, params={"account": wallet}, timeout=10)
        r.raise_for_status()
        coins = r.json().get("data", [])
        sui = next((c.get("balance", 0) for c in coins if c.get("symbol") == "SUI"), 0)
        return f"{int(sui)/1e9:,.0f} SUI + {len(coins)-1} tokens"
    except Exception:
        pass

    # 2) Fallback to JSON‑RPC suix_getAllBalances
    try:
        payload = {"jsonrpc":"2.0","id":1,"method":"suix_getAllBalances","params":[wallet]}
        rpc     = requests.post(RPC_URL, json=payload, timeout=10)
        rpc.raise_for_status()
        bal_list = rpc.json().get("result", [])
        sui = 0
        for b in bal_list:
            if b.get("coinType","").endswith("::sui::SUI"):
                sui = int(b.get("totalBalance",0))
        return f"{sui/1e9:,.0f} SUI + {len(bal_list)-1} tokens"
    except Exception as e:
        logging.warning(f"Balance fallback failed for {wallet}: {e}")
        return "unknown"

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
            bal  = get_balance(wallet)
            msg  = (
                f"🐋 *Wallet Alert!*\n"
                f"`{shorten(wallet)}` • *{tx.get('action','TX').upper()}*\n"
                f"{tx.get('symbol')} • {tx.get('amount')}\n"
                f"Contract `{tx.get('object_id')}`\n"
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
        f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
    )
    # Set new webhook endpoint
    endpoint = f"{WEBHOOK_URL}/{TOKEN}"
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={endpoint}"
    )

    logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10)

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=endpoint,
    )

if __name__ == "__main__":
    main()
