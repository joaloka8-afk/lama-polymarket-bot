"""Telegram command handlers.

Every handler pulls shared objects from ``context.bot_data`` so there is
zero global state.
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from database import Database
from polymarket_api import PolymarketAPI
from wallet_manager import WalletManager

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────

def _deps(context: ContextTypes.DEFAULT_TYPE):
    """Unpack the four shared singletons from bot_data."""
    bd = context.bot_data
    return (
        bd["config"],       # Config
        bd["db"],           # Database
        bd["wallet_mgr"],   # WalletManager
        bd["api"],          # PolymarketAPI
    )


def _esc(text: str) -> str:
    """Minimal Markdown-safe escaping for user-supplied addresses."""
    return text.replace("_", "\\_").replace("*", "\\*")


# ═════════════════════════════════════════════════════════════
# /start
# ═════════════════════════════════════════════════════════════

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦙 **Hi, I'm Lama!** Your AI-powered Polymarket trading assistant.\n"
        "\n"
        "💬 **Chat with me naturally!** I understand what you want to do.\n"
        "Or use these commands:\n"
        "\n"
        "🔧 **Setup** (run in order):\n"
        "  1️⃣ /create\\_wallet  – generate owner EOA\n"
        "  2️⃣ /setup\\_proxy    – deploy Polymarket proxy wallet\n"
        "  3️⃣ /deposit        – fund the proxy wallet\n"
        "  4️⃣ /connect        – enable CLOB trading\n"
        "\n"
        "📊 **Copy-trading:**\n"
        "  👥 /follow <leader\\_address>\n"
        "  ❌ /unfollow <leader\\_address>\n"
        "  📋 /leaders   – list followed leaders\n"
        "\n"
        "🤖 **Algo-trading:**\n"
        "  🚀 /enable\\_algo   – start algorithmic trading\n"
        "  ⏹️ /disable\\_algo  – stop algorithmic trading\n"
        "  📊 /algo\\_status   – show algo settings\n"
        "  🎯 /set\\_strategy <name> – change strategy\n"
        "\n"
        "⚙️ **Controls:**\n"
        "  ⏸️ /pause  ▶️ /resume  📈 /status  📜 /history\n"
        "\n"
        "💬 **Or just chat:** \"buy 10 on Yes\", \"enable algo trading\", \"show my status\"",
        parse_mode=ParseMode.MARKDOWN
    )


# ═════════════════════════════════════════════════════════════
# /create_wallet
# ═════════════════════════════════════════════════════════════

async def create_wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    existing = await db.get_user(uid)
    if existing:
        await update.message.reply_text(
            f"✅ You already have a wallet.\n"
            f"Owner EOA: `{existing['owner_address']}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address, private_key = wm.generate_wallet()
    encrypted_pk = wm.encrypt(private_key)

    await db.create_user(uid, address, encrypted_pk, cfg)

    await update.message.reply_text(
        f"🎉 **Wallet created!**\n\n"
        f"Owner EOA:\n`{address}`\n\n"
        f"🔒 Your private key is stored encrypted. It will never be shown.",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Wallet created for user %d: %s", uid, address)


# ═════════════════════════════════════════════════════════════
# /setup_proxy
# ═════════════════════════════════════════════════════════════

async def setup_proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "No wallet found. Run /create_wallet first."
        )
        return

    if user["proxy_wallet_address"]:
        await update.message.reply_text(
            f"Proxy wallet already configured.\n"
            f"Address: `{user['proxy_wallet_address']}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        "Deploying your Polymarket proxy wallet (Gnosis Safe).\n"
        "This may take up to a minute …"
    )

    try:
        pk = wm.decrypt(user["encrypted_private_key"])
        result = await api.deploy_proxy(pk)
        proxy_addr = result["address"]

        await db.update_user(uid, proxy_wallet_address=proxy_addr)

        tag = "already live" if result.get("already_deployed") else "newly deployed"
        await update.message.reply_text(
            f"Proxy wallet {tag}!\n\n"
            f"Proxy (funder) address:\n`{proxy_addr}`\n\n"
            f"This is your Polymarket wallet. Fund it via /deposit.",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("Proxy for user %d: %s (%s)", uid, proxy_addr, tag)

    except Exception as exc:
        logger.exception("Proxy deployment failed for user %d", uid)
        await update.message.reply_text(
            f"Proxy deployment failed: {str(exc)[:200]}\n\n"
            f"Alternative: manually set your Polymarket wallet address:\n"
            f"/set_proxy <your_polymarket_wallet_address>"
        )


# ═════════════════════════════════════════════════════════════
# /set_proxy  (manual fallback)
# ═════════════════════════════════════════════════════════════

async def set_proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "No wallet found. Run /create_wallet first."
        )
        return

    if not context.args:
        await update.message.reply_text("Usage: /set\\_proxy <proxy\\_wallet\\_address>")
        return

    addr = context.args[0].strip()
    if not addr.startswith("0x") or len(addr) != 42:
        await update.message.reply_text(
            "❌ Invalid address. Must be 0x followed by 40 hex characters."
        )
        return

    await db.update_user(uid, proxy_wallet_address=addr)
    await update.message.reply_text(
        f"✅ Proxy wallet set:\n`{addr}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d manually set proxy to %s", uid, addr)


# ═════════════════════════════════════════════════════════════
# /deposit
# ═════════════════════════════════════════════════════════════

async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["proxy_wallet_address"]:
        await update.message.reply_text(
            "Set up your proxy wallet first:\n/create_wallet then /setup_proxy"
        )
        return

    await update.message.reply_text("Fetching deposit addresses …")

    try:
        info = await api.create_deposit_addresses(user["proxy_wallet_address"])

        lines = [
            "Send USDC to any of the addresses below.\n"
            "Funds will be bridged to your proxy wallet.\n",
            f"Proxy wallet: `{user['proxy_wallet_address']}`\n",
        ]

        if isinstance(info, dict):
            for chain, value in info.items():
                if isinstance(value, str) and value:
                    lines.append(f"*{_esc(str(chain))}*: `{value}`")
                elif isinstance(value, dict):
                    addr = value.get("address", str(value))
                    lines.append(f"*{_esc(str(chain))}*: `{addr}`")
        elif isinstance(info, list):
            for entry in info:
                if isinstance(entry, dict):
                    chain = entry.get("chain", entry.get("network", "?"))
                    addr = entry.get("address", str(entry))
                    lines.append(f"*{_esc(str(chain))}*: `{addr}`")

        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    except Exception as exc:
        logger.exception("Deposit fetch failed for user %d", uid)
        await update.message.reply_text(
            f"Could not fetch deposit addresses: {str(exc)[:200]}"
        )


# ═════════════════════════════════════════════════════════════
# /connect
# ═════════════════════════════════════════════════════════════

async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("Run /create_wallet first.")
        return
    if not user["proxy_wallet_address"]:
        await update.message.reply_text("Run /setup_proxy first.")
        return
    if user["encrypted_api_key"]:
        await update.message.reply_text(
            "CLOB trading is already connected. You're good to go!"
        )
        return

    await update.message.reply_text("Deriving CLOB API credentials …")

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
            "CLOB trading connected!\n\n"
            "API credentials stored (encrypted). "
            "Use /follow <leader\\_address> to start copy-trading."
        )
        logger.info("CLOB connected for user %d", uid)

    except Exception as exc:
        logger.exception("CLOB connect failed for user %d", uid)
        await update.message.reply_text(
            f"Failed to derive API credentials: {str(exc)[:200]}"
        )


# ═════════════════════════════════════════════════════════════
# /follow  /unfollow  /leaders
# ═════════════════════════════════════════════════════════════

async def follow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "❌ Complete setup first: /create_wallet → /setup_proxy → /deposit → /connect"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /follow <leader\\_proxy\\_wallet\\_address>"
        )
        return

    leader = context.args[0].strip()
    if not leader.startswith("0x") or len(leader) != 42:
        await update.message.reply_text(
            "❌ Invalid address. Must be 0x + 40 hex chars."
        )
        return

    await db.add_leader(uid, leader.lower())
    count = len(await db.get_leaders(uid))

    await update.message.reply_text(
        f"👥 Now following `{leader}`\n📊 Total leaders: {count}",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d following %s", uid, leader)


async def unfollow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Usage: /unfollow <leader\\_proxy\\_wallet\\_address>"
        )
        return

    leader = context.args[0].strip().lower()
    await db.remove_leader(uid, leader)

    await update.message.reply_text(
        f"✅ Unfollowed `{leader}`", parse_mode=ParseMode.MARKDOWN
    )
    logger.info("User %d unfollowed %s", uid, leader)


async def leaders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    leaders = await db.get_leaders(uid)
    if not leaders:
        await update.message.reply_text(
            "👥 You're not following anyone yet.\nUse /follow <address> to start."
        )
        return

    lines = ["👥 **Leaders you follow:**\n"]
    for i, addr in enumerate(leaders, 1):
        lines.append(f"  {i}. `{addr}`")

    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


# ═════════════════════════════════════════════════════════════
# /pause  /resume
# ═════════════════════════════════════════════════════════════

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("❌ No account. Run /create_wallet first.")
        return

    await db.update_user(uid, is_paused=1)
    await update.message.reply_text("⏸️ Copy-trading PAUSED. Use /resume to restart.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("❌ No account. Run /create_wallet first.")
        return

    await db.update_user(uid, is_paused=0)
    await update.message.reply_text("▶️ Copy-trading RESUMED!")


# ═════════════════════════════════════════════════════════════
# /status
# ═════════════════════════════════════════════════════════════

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(
            "No account found. Run /create_wallet to get started."
        )
        return

    leaders = await db.get_leaders(uid)
    daily_loss = await db.get_daily_loss(uid)
    open_pos = await db.count_open_positions(uid)

    wallet_ok = "✅" if user["owner_address"] else "⏳"
    proxy_ok = "✅" if user["proxy_wallet_address"] else "⏳"
    clob_ok = "✅" if user["encrypted_api_key"] else "⏳"
    state = "⏸️ PAUSED" if user["is_paused"] else "▶️ ACTIVE"
    
    algo_enabled = user.get("algo_trading_enabled", 0)
    algo_state = "🟢 ON" if algo_enabled else "🔴 OFF"
    algo_strategy = user.get("algo_strategy", "momentum")

    proxy_display = user["proxy_wallet_address"] or "not set"

    text = (
        f"📈 **Account Status: {state}**\n\n"
        f"Owner EOA: `{user['owner_address']}`\n"
        f"Proxy Wallet: `{proxy_display}`\n\n"
        f"**🔧 Setup**\n"
        f"  Wallet: {wallet_ok}  Proxy: {proxy_ok}  CLOB: {clob_ok}\n\n"
        f"**📊 Copy-Trading**\n"
        f"  Leaders followed: {len(leaders)}\n"
        f"  Open positions: {open_pos} / {user['max_open_positions']}\n"
        f"  Daily loss: ${daily_loss:.2f} / ${user['max_daily_loss']:.2f}\n\n"
        f"**🤖 Algo-Trading**\n"
        f"  Status: {algo_state}\n"
        f"  Strategy: {algo_strategy}\n\n"
        f"**⚙️ Settings**\n"
        f"  Order size: {user['sizing_mode']} = {user['sizing_value']}\n"
        f"  Max slippage: {user['max_slippage']*100:.1f}%\n"
        f"  Max per market: ${user['max_per_market']:.2f}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ═════════════════════════════════════════════════════════════
# /history
# ═════════════════════════════════════════════════════════════

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    trades = await db.get_trade_history(uid)
    if not trades:
        await update.message.reply_text("📜 No trade history yet.")
        return

    lines = ["📜 **Recent trades:**\n"]
    for t in trades[:15]:
        icon = "✅" if t["status"] == "placed" else "❌"
        size = float(t["size"] or 0)
        price = float(t["price"] or 0)
        lines.append(
            f"{icon} {t['timestamp_utc'][:16]}  "
            f"{t['outcome_side']}  "
            f"${size:.2f} @ {price:.4f}  "
            f"{t['status']}"
        )
        if t["error"]:
            lines.append(f"      ⚠️ {t['error'][:80]}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
