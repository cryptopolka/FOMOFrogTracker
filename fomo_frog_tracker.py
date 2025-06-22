# fomo_frog_tracker.py

import asyncio
import json
import os
import datetime
import requests
import nest_asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ─── CONFIG ─────────────────────────────────────────────────────
TOKEN          = os.getenv("TOKEN", "8199259072:AAGqpEGdKGVfhO5UwhuJ9oFgM5FKVY2nUVw")
CHECK_INTERVAL = 60    # seconds
SPONSORED_MSG  = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"
STATE_FILE = "wallet_last_tx.json"

API_TX  = "https://api.suiscan.xyz/v1/accounts/{}/txns?limit=5"
API_BAL = "https://api.suiscan.xyz/v1/accounts/{}/balances"
# ─────────────────────────────────────────────────────────────────

def load_json(path, default):
    return json.load(open(path, "r")) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})
last_seen       = load_json(STATE_FILE, {})

# ─── COMMAND HANDLERS ────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "• /track <wallet>\n"
        "• /untrack <wallet>\n"
        "• /listwallets\n\n"
        "Alerts will come here privately whenever your tracked wallet transacts.",
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
    my = [w for w,u in tracked_wallets.items() if u==uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    await update.message.reply_text(
        "📋 *Your wallets:*\n" + "\n".join(f"- `{w}`" for w in my),
        parse_mode="Markdown"
    )

# ─── CHAIN & ALERT LOGIC ────────────────────────────────────────
def get_latest_txs(w): return requests.get(API_TX.format(w),timeout=10).json() or []
def get_balance(w):
    r = requests.get(API_BAL.format(w),timeout=10)
    if not r.ok: return "unknown"
    d = r.json()
    s = next((b["balance"] for b in d if b["type"]=="SUI"),0)
    t = len([b for b in d if b["type"]!="SUI"])
    return f"{int(s)/1e9:,.0f} SUI + {t} tokens"
def shorten(a,n=6): return a[:n]+"…"+a[-n:]

async def monitor_wallets(bot):
    global last_seen
    while True:
        for w,uid in list(tracked_wallets.items()):
            print(f"🔍 Checking {w}, last_seen={last_seen.get(w)}")
            txs = get_latest_txs(w)
            print(f"   → fetched {len(txs)} txs")
            if not txs: continue
            d = txs[0]["digest"]
            if d==last_seen.get(w): continue
            unseen=[]
            for tx in reversed(txs):
                if tx["digest"]==last_seen.get(w): break
                unseen.append(tx)
            for tx in unseen:
                print(f"   → alert for {tx['digest']}")
                await send_alert(bot, uid, w, tx)
            last_seen[w]=d
        save_json(STATE_FILE, last_seen)
        await asyncio.sleep(CHECK_INTERVAL)

async def send_alert(bot, uid, w, tx):
    act = tx.get("action","TX").upper()
    ts  = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
    ts  = ts.strftime("%Y-%m-%d %H:%M:%S")
    addr= tx.get("object_id","unknown"); sym=tx.get("symbol","unknown")
    amt = tx.get("amount",""); bal=get_balance(w)
    msg = (
        f"🐋 *Wallet Alert!*\n"
        f"`{shorten(w)}` • {act}\n"
        f"{sym} • {amt}\n"
        f"Contract `{addr}`\n"
        f"Bal: {bal}\n"
        f"{ts}\n"
        f"https://suivision.xyz/tx/{tx['digest']}"
        f"{SPONSORED_MSG}"
    )
    await bot.send_message(uid, msg, parse_mode="Markdown")

# ─── MAIN ─────────────────────────────────────────────────────────
async def main():
    nest_asyncio.apply()
    app = ApplicationBuilder().token(TOKEN).build()

    for cmd,fn in [("start",start),("track",track_cmd),
                   ("untrack",untrack_cmd),("listwallets",list_cmd)]:
        app.add_handler(CommandHandler(cmd,fn))

    # clear both webhook and pending updates in one call
    await app.bot.delete_webhook(drop_pending_updates=True)

    asyncio.create_task(monitor_wallets(app.bot))
    await app.run_polling()

if __name__=="__main__":
    asyncio.get_event_loop().run_until_complete(main())
