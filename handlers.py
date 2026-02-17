"""Telegram command handlers."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from database import Database
from polymarket_api import PolymarketAPI
from wallet_manager import WalletManager

logger = logging.getLogger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE):
    bd = context.bot_data
    return bd["config"], bd["db"], bd["wallet_mgr"], bd["api"]


# /start

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦙 Hey, I'm Lama. I help you trade on Polymarket.\n"
        "\n"
        "💬 Just talk to me like a normal person and I'll figure out what you need. "
        "Or use these commands:\n"
        "\n"
        "🚀 GETTING STARTED (do these in order):\n"
        "\n"
        "1️⃣ /create_wallet - makes you a wallet\n"
        "2️⃣ /setup_proxy - sets up your trading wallet\n"
        "3️⃣ /deposit - shows you where to send money\n"
        "4️⃣ /connect - turns on trading\n"
        "\n"
        "👥 COPY TRADING:\n"
        "\n"
        "/follow (paste a wallet address) - copy someone's trades\n"
        "/unfollow (paste a wallet address) - stop copying them\n"
        "/leaders - see who you're copying\n"
        "\n"
        "🤖 AUTO TRADING:\n"
        "\n"
        "/enable_algo - let me trade for you automatically\n"
        "/disable_algo - turn off auto trading\n"
        "/algo_status - see how auto trading is doing\n"
        "/set_strategy (name) - pick a trading style\n"
        "\n"
        "⚙️ OTHER:\n"
        "\n"
        "/pause - stop all trading\n"
        "/resume - start trading again\n"
        "/status - see your account info\n"
        "/history - see your past trades\n"
        "\n"
        "💬 Or just type something like \"buy 10 dollars on yes\" or \"show my status\" and I'll handle it."
    )


# /create_wallet

async def create_wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    existing = await db.get_user(uid)
    if existing:
        await update.message.reply_text(
            f"✅ You already have a wallet.\n"
            f"\n"
            f"Your address: {existing['owner_address']}"
        )
        return

    address, private_key = wm.generate_wallet()
    encrypted_pk = wm.encrypt(private_key)

    await db.create_user(uid, address, encrypted_pk, cfg)

    await update.message.reply_text(
        f"🎉 Done! Your wallet is ready.\n"
        f"\n"
        f"Your address: {address}\n"
        f"\n"
        f"🔒 Your private key is saved safely. I'll never show it to anyone.\n"
        f"\n"
        f"👉 Next step: type /setup_proxy"
    )
    logger.info("Wallet created for user %d: %s", uid, address)


# /setup_proxy

async def setup_proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "⚠️ You need a wallet first. Type /create_wallet"
        )
        return

    if user["proxy_wallet_address"]:
        await update.message.reply_text(
            f"✅ Your trading wallet is already set up.\n"
            f"\n"
            f"Address: {user['proxy_wallet_address']}"
        )
        return

    await update.message.reply_text(
        "⏳ Setting up your trading wallet. This can take up to a minute..."
    )

    try:
        pk = wm.decrypt(user["encrypted_private_key"])
        result = await api.deploy_proxy(pk)
        proxy_addr = result["address"]

        await db.update_user(uid, proxy_wallet_address=proxy_addr)

        await update.message.reply_text(
            f"🎉 Your trading wallet is ready!\n"
            f"\n"
            f"Address: {proxy_addr}\n"
            f"\n"
            f"💰 This is where your money goes. Next step: type /deposit"
        )
        logger.info("Proxy for user %d: %s", uid, proxy_addr)

    except Exception as exc:
        logger.exception("Proxy deployment failed for user %d", uid)
        await update.message.reply_text(
            f"❌ Something went wrong: {str(exc)[:200]}\n"
            f"\n"
            f"You can also set it manually:\n"
            f"/set_proxy (paste your polymarket wallet address)"
        )


# /set_proxy (manual fallback)

async def set_proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "⚠️ You need a wallet first. Type /create_wallet"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Type /set_proxy followed by your wallet address.\n"
            "\n"
            "Example: /set_proxy 0x1234...abcd"
        )
        return

    addr = context.args[0].strip()
    if not addr.startswith("0x") or len(addr) != 42:
        await update.message.reply_text(
            "❌ That doesn't look like a valid address. It should start with 0x and be 42 characters long."
        )
        return

    await db.update_user(uid, proxy_wallet_address=addr)
    await update.message.reply_text(
        f"✅ Got it. Your trading wallet is set to:\n{addr}"
    )
    logger.info("User %d manually set proxy to %s", uid, addr)


# /deposit

async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["proxy_wallet_address"]:
        await update.message.reply_text(
            "⚠️ You need to set up your wallets first.\n"
            "Type /create_wallet then /setup_proxy"
        )
        return

    await update.message.reply_text("⏳ Getting your deposit info...")

    try:
        info = await api.create_deposit_addresses(user["proxy_wallet_address"])

        lines = [
            "💰 Send USDC to one of these addresses to add money to your account.\n",
            f"Your trading wallet: {user['proxy_wallet_address']}\n",
        ]

        if isinstance(info, dict):
            for chain, value in info.items():
                if isinstance(value, str) and value:
                    lines.append(f"{chain}: {value}")
                elif isinstance(value, dict):
                    addr = value.get("address", str(value))
                    lines.append(f"{chain}: {addr}")
        elif isinstance(info, list):
            for entry in info:
                if isinstance(entry, dict):
                    chain = entry.get("chain", entry.get("network", "?"))
                    addr = entry.get("address", str(entry))
                    lines.append(f"{chain}: {addr}")

        lines.append("\n👉 After sending, type /connect to finish setup.")

        await update.message.reply_text("\n".join(lines))

    except Exception as exc:
        logger.exception("Deposit fetch failed for user %d", uid)
        await update.message.reply_text(
            f"❌ Couldn't get deposit addresses: {str(exc)[:200]}"
        )


# /connect

async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("⚠️ Type /create_wallet first.")
        return
    if not user["proxy_wallet_address"]:
        await update.message.reply_text("⚠️ Type /setup_proxy first.")
        return
    if user["encrypted_api_key"]:
        await update.message.reply_text(
            "✅ Trading is already connected. You're good to go!"
        )
        return

    await update.message.reply_text("⏳ Connecting your trading account...")

    try:
        pk = wm.decrypt(user["encrypted_private_key"])
        creds = await api.derive_api_credentials(pk, user["proxy_wallet_address"])

        await db.update_user(
            uid,
            encrypted_api_key=wm.encrypt(creds["api_key"]),
            encrypted_api_secret=wm.encrypt(creds["api_secret"]),
            encrypted_passphrase=wm.encrypt(creds["passphrase"]),
        )

        await update.message.reply_text(
            "🎉 You're all set! Trading is connected.\n"
            "\n"
            "You can now:\n"
            "👥 Copy someone's trades with /follow (address)\n"
            "🤖 Turn on auto trading with /enable_algo\n"
            "💬 Or just tell me what to trade"
        )
        logger.info("CLOB connected for user %d", uid)

    except Exception as exc:
        logger.exception("CLOB connect failed for user %d", uid)
        await update.message.reply_text(
            f"❌ Couldn't connect trading: {str(exc)[:200]}"
        )


# /follow /unfollow /leaders

async def follow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "⚠️ You need to finish setup first.\n"
            "Do these in order: /create_wallet /setup_proxy /deposit /connect"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Type /follow followed by the wallet address you want to copy.\n"
            "\n"
            "Example: /follow 0x1234...abcd"
        )
        return

    leader = context.args[0].strip()
    if not leader.startswith("0x") or len(leader) != 42:
        await update.message.reply_text(
            "❌ That doesn't look right. The address should start with 0x and be 42 characters."
        )
        return

    await db.add_leader(uid, leader.lower())
    count = len(await db.get_leaders(uid))

    await update.message.reply_text(
        f"👥 Now copying trades from {leader}\n"
        f"📊 You're following {count} trader{'s' if count != 1 else ''} total."
    )
    logger.info("User %d following %s", uid, leader)


async def unfollow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Type /unfollow followed by the wallet address.\n"
            "\n"
            "Example: /unfollow 0x1234...abcd"
        )
        return

    leader = context.args[0].strip().lower()
    await db.remove_leader(uid, leader)

    await update.message.reply_text(f"✅ Stopped copying {leader}")
    logger.info("User %d unfollowed %s", uid, leader)


async def leaders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    leaders = await db.get_leaders(uid)
    if not leaders:
        await update.message.reply_text(
            "👥 You're not copying anyone yet.\n"
            "Use /follow (address) to start."
        )
        return

    lines = ["👥 Traders you're copying:\n"]
    for i, addr in enumerate(leaders, 1):
        lines.append(f"{i}. {addr}")

    await update.message.reply_text("\n".join(lines))


# /pause /resume

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("⚠️ You don't have an account yet. Type /create_wallet")
        return

    await db.update_user(uid, is_paused=1)
    await update.message.reply_text("⏸️ Trading is paused. Type /resume when you want to start again.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("⚠️ You don't have an account yet. Type /create_wallet")
        return

    await db.update_user(uid, is_paused=0)
    await update.message.reply_text("▶️ Trading is back on!")


# /status

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "No account found. Type /create_wallet to get started."
        )
        return

    leaders = await db.get_leaders(uid)
    daily_loss = await db.get_daily_loss(uid)
    open_pos = await db.count_open_positions(uid)

    wallet_done = "✅" if user["owner_address"] else "⏳"
    proxy_done = "✅" if user["proxy_wallet_address"] else "⏳"
    trading_done = "✅" if user["encrypted_api_key"] else "⏳"
    state = "⏸️ PAUSED" if user["is_paused"] else "▶️ ACTIVE"

    algo_on = "🟢 on" if user.get("algo_trading_enabled", 0) else "🔴 off"
    algo_strategy = user.get("algo_strategy", "momentum")

    proxy_display = user["proxy_wallet_address"] or "not set yet"

    text = (
        f"📊 YOUR ACCOUNT\n"
        f"\n"
        f"Status: {state}\n"
        f"\n"
        f"🔑 Wallet: {user['owner_address']}\n"
        f"💰 Trading wallet: {proxy_display}\n"
        f"\n"
        f"🔧 SETUP\n"
        f"Wallet: {wallet_done}  Trading wallet: {proxy_done}  Trading: {trading_done}\n"
        f"\n"
        f"👥 COPY TRADING\n"
        f"Copying {len(leaders)} trader{'s' if len(leaders) != 1 else ''}\n"
        f"Open positions: {open_pos} out of {user['max_open_positions']} max\n"
        f"Lost today: ${daily_loss:.2f} out of ${user['max_daily_loss']:.2f} max\n"
        f"\n"
        f"🤖 AUTO TRADING\n"
        f"Auto trading: {algo_on}\n"
        f"Strategy: {algo_strategy}\n"
        f"\n"
        f"⚙️ SETTINGS\n"
        f"Order size: {user['sizing_value']} USDC\n"
        f"Max slippage: {user['max_slippage']*100:.1f}%\n"
        f"Max per market: ${user['max_per_market']:.2f}"
    )
    await update.message.reply_text(text)


# /history

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    trades = await db.get_trade_history(uid)
    if not trades:
        await update.message.reply_text("📜 No trades yet.")
        return

    lines = ["📜 Your recent trades:\n"]
    for t in trades[:15]:
        icon = "✅" if t["status"] == "placed" else "❌"
        size = float(t["size"] or 0)
        price = float(t["price"] or 0)
        lines.append(
            f"{icon} {t['timestamp_utc'][:16]} - "
            f"{t['outcome_side']} - "
            f"${size:.2f} at {price:.4f}"
        )
        if t["error"]:
            lines.append(f"   ⚠️ {t['error'][:80]}")

    await update.message.reply_text("\n".join(lines))
