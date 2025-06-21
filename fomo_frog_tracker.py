# fomo_frog_tracker.py

import asyncio
import json
import os
import datetime
import requests
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import nest_asyncio

# ─── CONFIG ─────────────────────────────────────────────────────
# Get these from your Render (or environment), or replace with literal strings.
TOKEN          = os.getenv("TOKEN", "8199259072:AAFmFBve-8gCFB2lut4XRGf5KEnlbkc3OM8")
CHECK_INTERVAL = 60    # seconds between blockchain checks
SPONSORED_MSG  = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"   # stores {"wallet": user_id}
STATE_FILE = "wallet_last_tx.json"    # stores {"wallet": last_seen_digest}

API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ─────────────────────────────────────────────────────────────────

def load_json(path, default):
    return json.load(open(path, "r")) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

# Load stored state
tracked_wallets = load_json(TRACK_FILE, {})   # { wallet_address: user_id }
last_seen       = load_json(STATE_FILE, {})    # { wallet_address: last_tx_digest }

# ─── TELEGRAM COMMANDS ────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "🔥 Features:\n"
        "• 🐋 *Whale Wallet Tracker*\n"
        "• 📦 *Multi‑Wallet Support*\n"
        "• 📢 *Sponsored Alerts*\n\n"
        "👉 Use `/track <wallet>` to begin.\n"
        "Use `/listwallets` to see your tracked wallets.\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /track <wallet_address>")
    wallet = context.args[0].lower()
    user_id = update.effective_chat.id
    tracked_wallets[wallet] = user_id
    save_json(TRACK_FILE, tracked_wallets)
    await update.message.reply_text(f"✅ Now tracking `{wallet}`", parse_mode="Markdown")

async def untrack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /untrack <wallet_address>")
    wallet = context.args[0].lower()
    user_id = update.effective_chat.id
    if tracked_wallets.get(wallet) == user_id:
        tracked_wallets.pop(wallet)
        save_json(TRACK_FILE, tracked_wallets)
        await update.message.reply_text(f"❌ Untracked `{wallet}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("That wallet isn’t in your tracking list.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    user_wallets = [w for w, uid in tracked_wallets.items() if uid == user_id]
    if not user_wallets:
        return await update.message.reply_text("You have no wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in user_wallets)
    await update.message.reply_text(f"📋 *Your tracked wallets:*\n{lines}", parse_mode="Markdown")

# ─── CHAIN INTERACTIONS ───────────────────────────────────────────
def get_latest_txs(wallet):
    r = requests.get(API_TX.format(wallet), timeout=10)
    return r.json() if r.ok else []

def get_balance(wallet):
    r = requests.get(API_BAL.format(wallet), timeout=10)
    if not r.ok:
        return "unknown"
    data = r.json()
    sui = next((b for b in data if b["type"] == "SUI"), {"balance": 0})["balance"]
    tokens = len([b for b in data if b["type"] != "SUI"])
    return f"{int(sui)/1e9:,.0f} SUI + {tokens} tokens"

def shorten(addr, n=6):
    return addr[:n] + "…" + addr[-n:]

async def monitor_wallets(bot: Bot):
    global last_seen
    while True:
        for wallet, user_id in list(tracked_wallets.items()):
            try:
                txs = get_latest_txs(wallet)
                if not txs:
                    continue
                latest = txs[0]["digest"]
                if latest == last_seen.get(wallet):
                    continue
                # collect any unseen txs
                unseen = []
                for tx in reversed(txs):
                    if tx["digest"] == last_seen.get(wallet):
                        break
                    unseen.append(tx)
                for tx in unseen:
                    await send_alert(bot, user_id, wallet, tx)
                last_seen[wallet] = latest
            except Exception as e:
                print(f"[ERROR] monitoring {wallet}: {e}")
        save_json(STATE_FILE, last_seen)
        await asyncio.sleep(CHECK_INTERVAL)

async def send_alert(bot: Bot, user_id, wallet, tx):
    action    = tx.get("action", "TX").upper()
    ts        = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
    timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
    token_addr = tx.get("object_id", "unknown")
    token_name = tx.get("symbol",    "unknown")
    amount     = tx.get("amount",    "")
    balance    = get_balance(wallet)

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
    await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")

# ─── BOT BOOTSTRAP ────────────────────────────────────────────────
async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets", list_cmd))

    bot = Bot(token=TOKEN)
    # clear any existing webhook so polling won’t conflict
    await bot.delete_webhook()

    # start background monitor and polling
    asyncio.create_task(monitor_wallets(bot))
    await app.run_polling()

if __name__ == "__main__":
    nest_asyncio.apply()  # fix event-loop issue on Python 3.13
    asyncio.get_event_loop().run_until_complete(main())
