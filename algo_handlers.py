"""Handlers for algo trading commands."""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from algo_strategies import list_strategies

logger = logging.getLogger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE):
    """Unpack shared singletons from bot_data."""
    bd = context.bot_data
    return bd["config"], bd["db"], bd["wallet_mgr"], bd["api"]


# ═════════════════════════════════════════════════════════════
# /enable_algo
# ═════════════════════════════════════════════════════════════

async def enable_algo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "❌ Complete setup first: /create\\_wallet → /setup\\_proxy → /deposit → /connect",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if user["algo_trading_enabled"]:
        await update.message.reply_text(
            "✅ Algo trading is already enabled!\n"
            f"Strategy: **{user['algo_strategy']}**\n"
            f"Min confidence: {user['algo_min_confidence']*100:.0f}%",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await db.update_user(uid, algo_trading_enabled=1)

    await update.message.reply_text(
        "🤖 **Algo trading enabled!**\n\n"
        f"Strategy: **{user['algo_strategy'] or 'momentum'}**\n"
        f"Min confidence: {user['algo_min_confidence']*100:.0f}%\n\n"
        f"Lama will now automatically trade based on market signals.\n"
        f"Use /algo\\_status to see details or /set\\_strategy to change.",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d enabled algo trading", uid)


# ═════════════════════════════════════════════════════════════
# /disable_algo
# ═════════════════════════════════════════════════════════════

async def disable_algo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("❌ No account. Run /create_wallet first.")
        return

    if not user["algo_trading_enabled"]:
        await update.message.reply_text("ℹ️ Algo trading is already disabled.")
        return

    await db.update_user(uid, algo_trading_enabled=0)

    await update.message.reply_text(
        "⏹️ **Algo trading disabled.**\n\n"
        "Use /enable\\_algo to turn it back on.",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d disabled algo trading", uid)


# ═════════════════════════════════════════════════════════════
# /algo_status
# ═════════════════════════════════════════════════════════════

async def algo_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("❌ No account. Run /create_wallet first.")
        return

    enabled = "🟢 **ACTIVE**" if user["algo_trading_enabled"] else "🔴 **DISABLED**"
    strategy = user["algo_strategy"] or "momentum"
    min_conf = float(user["algo_min_confidence"] or 0.70)

    strategies_list = ", ".join(list_strategies())

    text = (
        f"🤖 **Algo Trading Status**\n\n"
        f"Status: {enabled}\n"
        f"Strategy: **{strategy}**\n"
        f"Min confidence: {min_conf*100:.0f}%\n\n"
        f"**Available strategies:**\n{strategies_list}\n\n"
        f"Use /set\\_strategy <name> to change."
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ═════════════════════════════════════════════════════════════
# /set_strategy
# ═════════════════════════════════════════════════════════════

async def set_strategy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("❌ No account. Run /create_wallet first.")
        return

    if not context.args:
        strategies = ", ".join(list_strategies())
        await update.message.reply_text(
            f"Usage: /set\\_strategy <name>\n\n"
            f"**Available strategies:**\n{strategies}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    strategy_name = context.args[0].strip().lower()
    if strategy_name not in list_strategies():
        await update.message.reply_text(
            f"❌ Unknown strategy: {strategy_name}\n\n"
            f"**Available:** {', '.join(list_strategies())}",
        )
        return

    await db.update_user(uid, algo_strategy=strategy_name)

    await update.message.reply_text(
        f"✅ Strategy changed to **{strategy_name}**\n\n"
        f"Lama will use this strategy for algo trading.",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d changed strategy to %s", uid, strategy_name)
