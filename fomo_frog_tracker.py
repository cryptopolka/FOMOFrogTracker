import asyncio
import requests
import json
import os
import nest_asyncio
from bs4 import BeautifulSoup
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Replace with your actual Telegram Bot token from BotFather
TOKEN = "8199259072:AAFmFBve-8gCFB2lut4XRGf5KEnlbkc3OM8"

# Replace with your channel username or user ID
CHANNEL_ID = "@fomofrogz"

TRACKED_WALLETS_FILE = "tracked_wallets.json"
sent_tx_ids = set()

# Load previously tracked wallets from file (if exists)
if os.path.exists(TRACKED_WALLETS_FILE):
    with open(TRACKED_WALLETS_FILE, "r") as f:
        tracked_wallets = set(json.load(f))
else:
    tracked_wallets = set()

def save_wallets():
    with open(TRACKED_WALLETS_FILE, "w") as f:
        json.dump(list(tracked_wallets), f)

# Scrape transactions from Moonbags bonding curve page
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
            wallet_address = link.split("/")[-1]
            txs.append({"name": name, "link": tx_url, "wallet": wallet_address})
        except Exception:
            continue
    return txs

# Telegram bot commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐸 Welcome to *FOMO Frog Tracker!*\nUse /track <wallet> to start tracking.",
        parse_mode="Markdown"
    )

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
        msg = "📋 *Tracked Wallets:*\n" + "\n".join(f"- `{w}`" for w in tracked_wallets)
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("No wallets are being tracked currently.")

# Monitor wallet activity and send alerts
async def monitor_wallets(bot: Bot):
    global sent_tx_ids
    while True:
        try:
            txs = get_moonbags_transactions()
            for tx in txs:
                wallet = tx["wallet"].lower()
                if wallet in tracked_wallets and tx["link"] not in sent_tx_ids:
                    msg = (
                        f"🐋 *Tracked Wallet Activity!*\n"
                        f"Wallet: `{wallet}`\n"
                        f"Token: *{tx['name']}*\n"
                        f"[Moonbags Link]({tx['link']})"
                    )
                    await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
                    sent_tx_ids.add(tx["link"])
        except Exception as e:
            print("Error while checking Moonbags:", e)
        await asyncio.sleep(60)

# Main function to run the bot
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("untrack", untrack))
    app.add_handler(CommandHandler("listwallets", listwallets))

    bot = Bot(token=TOKEN)
    asyncio.create_task(monitor_wallets(bot))
    await app.run_polling()

# Entry point (fixes event loop issues on Python 3.13)
if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(main())
