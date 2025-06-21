# fomo_frog_tracker.py

import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import json
import os

TOKEN = "8199259072:AAFmFBve-8gCFB2lut4XRGf5KEnlbkc3OM8"
CHANNEL_ID = "@fomofrogz"  # Or private user ID for direct messages

TRACKED_WALLETS_FILE = "tracked_wallets.json"
sent_tx_ids = set()

# Load or initialize tracked wallets
if os.path.exists(TRACKED_WALLETS_FILE):
    with open(TRACKED_WALLETS_FILE, "r") as f:
        tracked_wallets = set(json.load(f))
else:
    tracked_wallets = set()

def save_wallets():
    with open(TRACKED_WALLETS_FILE, "w") as f:
        json.dump(list(tracked_wallets), f)

# Scraping Moonbags recent transactions
def get_moonbags_transactions():
    url = "https://moonbags.io/bondingcurve"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    txs = []

    for card in soup.find_all("div", class_="launch-card"):
        try:
            name = card.find("h3").text.strip()
            link = card.find("a", href=True)["href"]
            tx_url = f"https://moonbags.io{link}"
            wallet_address = link.split("/")[-1]  # crude approximation
            txs.append({"name": name, "link": tx_url, "wallet": wallet_address})
        except Exception as e:
            continue
    return txs

# Telegram Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐸 Welcome to *FOMO Frog Tracker*! Use /track <wallet> to start tracking.", parse_mode="Markdown")

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        wallet = context.args[0].lower()
        tracked_wallets.add(wallet)
        save_wallets()
        await update.message.reply_text(f"✅ Wallet `{wallet}` is now being tracked.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Usage: /track <wallet_address>")

async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        wallet = context.args[0].lower()
        if wallet in tracked_wallets:
            tracked_wallets.remove(wallet)
            save_wallets()
            await update.message.reply_text(f"❌ Wallet `{wallet}` removed from tracking.", parse_mode="Markdown")
        else:
            await update.message.reply_text("Wallet not found in tracking list.")
    else:
        await update.message.reply_text("Usage: /untrack <wallet_address>")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if tracked_wallets:
        msg = "📋 *Tracked Wallets:*
" + "
".join(f"- `{w}`" for w in tracked_wallets)
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("No wallets are being tracked currently.")

async def monitor_wallets(bot: Bot):
    global sent_tx_ids
    while True:
        try:
            txs = get_moonbags_transactions()
            for tx in txs:
                if tx["wallet"].lower() in tracked_wallets and tx["link"] not in sent_tx_ids:
                    msg = (f"🐋 *Tracked Wallet Activity!*
"
                           f"Wallet: `{tx['wallet']}`
"
                           f"Token: *{tx['name']}*
"
                           f"[Moonbags Link]({tx['link']})")
                    await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
                    sent_tx_ids.add(tx["link"])
        except Exception as e:
            print("Error checking Moonbags:", e)
        await asyncio.sleep(60)

# Run bot
async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("untrack", untrack))
    app.add_handler(CommandHandler("listwallets", listwallets))

    bot = Bot(token=TOKEN)
    asyncio.create_task(monitor_wallets(bot))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
