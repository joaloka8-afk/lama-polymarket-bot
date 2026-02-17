"""Handlers for algo trading commands."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from algo_strategies import list_strategies

logger = logging.getLogger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE):
    bd = context.bot_data
    return bd["config"], bd["db"], bd["wallet_mgr"], bd["api"]


# /enable_algo

async def enable_algo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "You need to finish setup first.\n"
            "Do these in order: /create_wallet /setup_proxy /deposit /connect"
        )
        return

    if user["algo_trading_enabled"]:
        await update.message.reply_text(
            f"Auto trading is already on.\n"
            f"Strategy: {user['algo_strategy']}\n"
            f"Confidence: {user['algo_min_confidence']*100:.0f}%"
        )
        return

    await db.update_user(uid, algo_trading_enabled=1)

    await update.message.reply_text(
        f"Auto trading is on!\n"
        f"\n"
        f"Strategy: {user['algo_strategy'] or 'momentum'}\n"
        f"Confidence: {user['algo_min_confidence']*100:.0f}%\n"
        f"\n"
        f"I'll start looking for trades automatically.\n"
        f"Type /algo_status to check on it or /set_strategy to change how I trade."
    )
    logger.info("User %d enabled algo trading", uid)


# /disable_algo

async def disable_algo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("You don't have an account yet. Type /create_wallet")
        return

    if not user["algo_trading_enabled"]:
        await update.message.reply_text("Auto trading is already off.")
        return

    await db.update_user(uid, algo_trading_enabled=0)

    await update.message.reply_text(
        "Auto trading is off.\n"
        "Type /enable_algo to turn it back on."
    )
    logger.info("User %d disabled algo trading", uid)


# /algo_status

async def algo_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("You don't have an account yet. Type /create_wallet")
        return

    enabled = "on" if user["algo_trading_enabled"] else "off"
    strategy = user["algo_strategy"] or "momentum"
    min_conf = float(user["algo_min_confidence"] or 0.70)

    strategies_list = ", ".join(list_strategies())

    text = (
        f"AUTO TRADING\n"
        f"\n"
        f"Status: {enabled}\n"
        f"Strategy: {strategy}\n"
        f"Confidence needed: {min_conf*100:.0f}%\n"
        f"\n"
        f"Available strategies: {strategies_list}\n"
        f"\n"
        f"To change, type /set_strategy followed by the name."
    )

    await update.message.reply_text(text)


# /set_strategy

async def set_strategy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg, db, wm, api = _deps(context)
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text("You don't have an account yet. Type /create_wallet")
        return

    if not context.args:
        strategies = ", ".join(list_strategies())
        await update.message.reply_text(
            f"Type /set_strategy followed by the name.\n"
            f"\n"
            f"Available: {strategies}"
        )
        return

    strategy_name = context.args[0].strip().lower()
    if strategy_name not in list_strategies():
        await update.message.reply_text(
            f"I don't know that strategy.\n"
            f"\n"
            f"Available: {', '.join(list_strategies())}"
        )
        return

    await db.update_user(uid, algo_strategy=strategy_name)

    await update.message.reply_text(
        f"Strategy changed to {strategy_name}.\n"
        f"I'll use this from now on."
    )
    logger.info("User %d changed strategy to %s", uid, strategy_name)
