# fomo_frog_tracker.py

import time
import json
import os
import datetime
import requests
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
# ────────────────────────────────────────────────────────────────
TOKEN          = os.getenv("TOKEN", "8199259072:AAHfLDID2q6QGs43LnmF6FsixhdyNOR9pEQ")
CHECK_INTERVAL = 60
SPONSORED_MSG  = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"
STATE_FILE = "wallet_last_tx.json"

API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ────────────────────────────────────────────────────────────────

def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})  # {wallet: user_id}
last_seen       = load_json(STATE_FILE, {})  # {wallet: last_digest}

# ─── TELEGRAM COMMANDS ────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "• /track <wallet>\n"
        "• /untrack <wallet>\n"
        "• /listwallets\n\n"
        "Alerts will arrive here when your tracked wallet transacts.",
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
    my = [w for w,u in tracked_wallets.items() if u==uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── CHAIN / ALERT LOGIC ─────────────────────────────────────────
def get_latest_txs(w):
    r = requests.get(API_TX.format(w), timeout=10)
    return r.json() if r.ok else []

def get_balance(w):
    r = requests.get(API_BAL.format(w), timeout=10)
    if not r.ok: return "unknown"
    d = r.json()
    s = next((b["balance"] for b in d if b["type"]=="SUI"),0)
    t = len([b for b in d if b["type"]!="SUI"])
    return f"{int(s)/1e9:,.0f} SUI + {t} tokens"

def shorten(a,n=6): return a[:n]+"…"+a[-n:]

async def monitor(bot: Bot):
    global last_seen
    while True:
        for w,uid in list(tracked_wallets.items()):
            print(f"🔍 Checking {w}, last_seen={last_seen.get(w)}")
            txs = get_latest_txs(w)
            print(f"   → fetched {len(txs)} txs")
            if not txs: continue
            digest = txs[0]["digest"]
            if digest == last_seen.get(w): continue
            unseen = []
            for tx in reversed(txs):
                if tx["digest"] == last_seen.get(w):
                    break
                unseen.append(tx)
            for tx in unseen:
                print(f"   → alert for {tx['digest']}")
                await send_alert(bot, uid, w, tx)
            last_seen[w] = digest
        save_json(STATE_FILE, last_seen)
        await asyncio.sleep(CHECK_INTERVAL)

async def send_alert(bot: Bot, uid, w, tx):
    act = tx.get("action","TX").upper()
    ts  = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
    tstr= ts.strftime("%Y-%m-%d %H:%M:%S")
    addr= tx.get("object_id","unknown")
    sym = tx.get("symbol","unknown")
    amt = tx.get("amount","")
    bal = get_balance(w)
    msg = (
        f"🐋 *Wallet Alert!*\n"
        f"`{shorten(w)}` • {act}\n"
        f"{sym} • {amt}\n"
        f"Contract `{addr}`\n"
        f"Bal: {bal}\n"
        f"{tstr}\n"
        f"https://suivision.xyz/tx/{tx['digest']}"
        f"{SPONSORED_MSG}"
    )
    await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")

# ─── ENTRY POINT ─────────────────────────────────────────────────
def main():
    # 1) ensure no webhook or old updates
    requests.post(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true")
    time.sleep(1)

    # 2) build the App
    app = ApplicationBuilder().token(TOKEN).build()
    for cmd,fn in [("start",start),("track",track_cmd),
                   ("untrack",untrack_cmd),("listwallets",list_cmd)]:
        app.add_handler(CommandHandler(cmd, fn))

    # 3) launch background monitor
    app.create_task(monitor(app.bot))

    # 4) start polling (blocks)
    app.run_polling()

if __name__ == "__main__":
    main()
