# fomo_frog_tracker.py

import os
import re
import json
import datetime
import logging
import requests

# ─── Silence noisy HTTP logs ────────────────────────────────────
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# ─── Patch PTB JobQueue weakref bug ──────────────────────────────
import telegram.ext._jobqueue as _jq
def _patch_set_app(self, application):
    self._application = lambda: application
_jq.JobQueue.set_application = _patch_set_app

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ─── CONFIG ──────────────────────────────────────────────────────
TOKEN          = os.getenv("TOKEN")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
PORT           = int(os.getenv("PORT","80"))
CHECK_INTERVAL = 60  # seconds

SPONSORED_MSG = (
    "\n\n📢 *Sponsored*: Check out $MetaWhale – now live on Moonbags! "
    "Join the chat: https://t.me/MetaWhaleOfficial"
)

TRACK_FILE = "tracked_wallets.json"
STATE_FILE = "wallet_last_tx.json"
# ──────────────────────────────────────────────────────────────────

# ─── Persistence ──────────────────────────────────────────────────
def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_wallets = load_json(TRACK_FILE, {})
last_seen       = load_json(STATE_FILE, {})

# ─── Bot commands ─────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 *Welcome to FOMO Frog Tracker!*\n\n"
        "• /track `<wallet>`\n"
        "• /untrack `<wallet>`\n"
        "• /listwallets\n\n"
        "Alerts fire whenever new on‐chain activity appears on Suivision.",
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
    w  = ctx.args[0].lower()
    uid = update.effective_chat.id
    if tracked_wallets.get(w)==uid:
        tracked_wallets.pop(w)
        save_json(TRACK_FILE, tracked_wallets)
        await update.message.reply_text(f"❌ Untracked `{w}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("That wallet isn’t in your list.")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    my  = [w for w,u in tracked_wallets.items() if u==uid]
    if not my:
        return await update.message.reply_text("No wallets being tracked.")
    lines = "\n".join(f"- `{w}`" for w in my)
    await update.message.reply_text(f"📋 *Your wallets:*\n{lines}", parse_mode="Markdown")

# ─── Scrape Activity page for txs ─────────────────────────────────
def get_latest_txs(wallet):
    url = f"https://suivision.xyz/account/{wallet}?tab=Activity"
    try:
        html = requests.get(url, timeout=10).text
    except Exception as e:
        logging.warning(f"Failed to fetch HTML for {wallet}: {e}")
        return []

    # try <script id="__NEXT_DATA__">…</script>
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
    # if missing, try window.__NEXT_DATA__
    if not m:
        m = re.search(r'<script>\s*window\.__NEXT_DATA__ = (.*?);\s*</script>', html, flags=re.S)

    if not m:
        logging.warning(f"No JSON blob on Suivision page for {wallet}")
        return []

    raw = m.group(1)
    # For window pattern we may have trailing semicolon
    raw = raw.strip().rstrip(';')
    try:
        blob = json.loads(raw)
    except Exception as e:
        logging.warning(f"Couldn’t parse JSON blob for {wallet}: {e}")
        return []

    # log first 1 000 chars so you can inspect the structure
    snippet = json.dumps(blob.get("props",{}).get("pageProps",{}))[:1000]
    logging.info(f"Suivision JSON for {wallet}: {snippet}…")

    # drill in—likely under props.pageProps.initialState.account.activity.transactions
    page = blob.get("props",{}).get("pageProps",{})
    acts = (page.get("initialState",{})
               .get("account",{})
               .get("activity",{})
               .get("transactions",[]))

    txs = []
    for tx in acts:
        txs.append({
            "digest":       tx.get("digest",""),
            "action":       tx.get("type","TX"),
            "timestamp_ms": tx.get("timestamp",0),
            "object_id":    tx.get("objectId",""),
            "symbol":       tx.get("coinSymbol",""),
            "amount":       tx.get("coinAmount",""),
        })
    return txs

def shorten(a, n=6): return a[:n]+"…"+a[-n:]

# ─── Monitor Job ─────────────────────────────────────────────────
async def monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    global last_seen
    bot = ctx.bot

    for wallet, chat_id in tracked_wallets.items():
        logging.info(f"Checking {wallet} (last_seen={last_seen.get(wallet)})")
        txs = get_latest_txs(wallet)
        if not txs:
            continue

        newest = txs[0]["digest"]
        if newest == last_seen.get(wallet):
            continue

        new = [tx for tx in reversed(txs) if tx["digest"]!=last_seen.get(wallet)]
        logging.info(f" → found {len(new)} new tx(s)")

        for tx in new:
            ts   = datetime.datetime.fromtimestamp(tx["timestamp_ms"]/1000)
            when = ts.strftime("%Y-%m-%d %H:%M:%S")
            msg = (
                f"🐋 *Wallet Alert!*\n"
                f"`{shorten(wallet)}` • *{tx['action'].upper()}*\n"
                f"{tx['symbol']} • {tx['amount']}\n"
                f"Contract `{tx['object_id']}`\n"
                f"Time: {when}\n"
                f"Tx: https://suivision.xyz/tx/{tx['digest']}"
                f"{SPONSORED_MSG}"
            )
            await bot.send_message(chat_id, msg, parse_mode="Markdown")

        last_seen[wallet] = newest

    save_json(STATE_FILE, last_seen)

# ─── ENTRY POINT ─────────────────────────────────────────────────
def main():
    # clear any old webhook & pending updates
    requests.post(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true")

    endpoint = f"{WEBHOOK_URL}/{TOKEN}"
    requests.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={endpoint}")

    logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.INFO)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("track",      track_cmd))
    app.add_handler(CommandHandler("untrack",    untrack_cmd))
    app.add_handler(CommandHandler("listwallets",list_cmd))

    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10)
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=endpoint)

if __name__ == "__main__":
    main()
