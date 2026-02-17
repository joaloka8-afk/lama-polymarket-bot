"""AI-powered message handlers for natural language interaction and chat-to-trade."""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ai_assistant import GrokAssistant
from handlers import (
    create_wallet_cmd,
    setup_proxy_cmd,
    deposit_cmd,
    connect_cmd,
    follow_cmd,
    unfollow_cmd,
    leaders_cmd,
    pause_cmd,
    resume_cmd,
    status_cmd,
    history_cmd,
)
from algo_handlers import (
    enable_algo_cmd,
    disable_algo_cmd,
    algo_status_cmd,
    set_strategy_cmd,
)

logger = logging.getLogger(__name__)


async def ai_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language messages using Grok AI."""
    bd = context.bot_data
    db = bd["db"]
    grok: GrokAssistant = bd.get("grok")

    if not grok:
        await update.message.reply_text(
            "⚠️ AI is not set up right now. Try using commands instead, like /start"
        )
        return

    uid = update.effective_user.id
    message_text = update.message.text

    user = await db.get_user(uid)
    leaders = await db.get_leaders(uid) if user else []

    user_context = {
        "has_wallet": user is not None,
        "has_proxy": user and user["proxy_wallet_address"] is not None,
        "trading_enabled": user and user["encrypted_api_key"] is not None,
        "is_paused": user and user["is_paused"] == 1,
        "leader_count": len(leaders),
    }

    await update.message.chat.send_action("typing")

    chat_history = await db.get_chat_history(uid, limit=20)

    try:
        result = await grok.understand_intent(message_text, user_context, chat_history)
    except Exception:
        logger.exception("Grok intent understanding failed")
        await update.message.reply_text(
            "I'm having trouble right now. Try again in a sec?"
        )
        return

    intent = result.get("intent", "chat")
    params = result.get("params", {})
    response = result.get("response", "")
    requires_confirmation = result.get("requires_confirmation", False)

    await db.save_chat_message(uid, "user", message_text)
    if response:
        await db.save_chat_message(uid, "assistant", response)

    if intent == "trade" and requires_confirmation:
        await handle_trade_intent(update, context, params, response)
    elif intent == "create_wallet":
        await create_wallet_cmd(update, context)
    elif intent == "setup_proxy":
        await setup_proxy_cmd(update, context)
    elif intent == "deposit":
        await deposit_cmd(update, context)
    elif intent == "connect":
        await connect_cmd(update, context)
    elif intent == "follow":
        leader = params.get("leader", "")
        context.args = [leader]
        await follow_cmd(update, context)
    elif intent == "unfollow":
        leader = params.get("leader", "")
        context.args = [leader]
        await unfollow_cmd(update, context)
    elif intent == "leaders":
        await leaders_cmd(update, context)
    elif intent == "pause":
        await pause_cmd(update, context)
    elif intent == "resume":
        await resume_cmd(update, context)
    elif intent == "status":
        await status_cmd(update, context)
    elif intent == "history":
        await history_cmd(update, context)
    elif intent == "enable_algo":
        await enable_algo_cmd(update, context)
    elif intent == "disable_algo":
        await disable_algo_cmd(update, context)
    elif intent == "algo_status":
        await algo_status_cmd(update, context)
    elif intent == "set_strategy":
        strategy = params.get("strategy", "momentum")
        context.args = [strategy]
        await set_strategy_cmd(update, context)
    else:
        await update.message.reply_text(response)


async def handle_trade_intent(
    update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict, ai_response: str
):
    """Handle trade intent with confirmation buttons."""
    bd = context.bot_data
    db = bd["db"]
    grok: GrokAssistant = bd.get("grok")
    uid = update.effective_user.id

    user = await db.get_user(uid)
    if not user or not user["proxy_wallet_address"] or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "⚠️ You need to finish setup first.\n"
            "Do these in order: /create_wallet /setup_proxy /deposit /connect"
        )
        return

    if user["is_paused"]:
        await update.message.reply_text(
            "⏸️ Trading is paused right now. Type /resume to turn it back on."
        )
        return

    summary = await grok.generate_trade_summary(params)

    context.user_data["pending_trade"] = params

    keyboard = [
        [
            InlineKeyboardButton("Yes, do it", callback_data="trade_confirm"),
            InlineKeyboardButton("No, cancel", callback_data="trade_cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(summary, reply_markup=reply_markup)


async def trade_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trade confirmation button press."""
    query = update.callback_query
    await query.answer()

    bd = context.bot_data
    db = bd["db"]
    wm = bd["wallet_mgr"]
    api = bd["api"]

    uid = query.from_user.id
    params = context.user_data.get("pending_trade")

    if not params:
        await query.edit_message_text("⏰ That trade expired. Try again.")
        return

    user = await db.get_user(uid)
    if not user:
        await query.edit_message_text("❌ Something went wrong. Try /start")
        return

    await query.edit_message_text("⏳ Placing your order...")

    try:
        pk = wm.decrypt(user["encrypted_private_key"])
        api_key = wm.decrypt(user["encrypted_api_key"])
        api_secret = wm.decrypt(user["encrypted_api_secret"])
        passphrase = wm.decrypt(user["encrypted_passphrase"])

        token_id = params.get("token_id") or params.get("market")

        if not token_id or not token_id.startswith("0x"):
            await query.edit_message_text(
                "I couldn't figure out which market you mean. "
                "Try giving me the token ID directly, like:\n"
                "buy 10 on token 0x1234..."
            )
            return

        side = params.get("side", "BUY")
        amount = float(params.get("amount", 0))

        book = await api.get_order_book(token_id)
        if side == "BUY":
            best_ask = float(book.get("asks", [[0.5]])[0][0])
            price = min(best_ask * 1.02, 0.99)
        else:
            best_bid = float(book.get("bids", [[0.5]])[0][0])
            price = max(best_bid * 0.98, 0.01)

        price = round(price, 4)
        size = round(amount / price, 2)

        result = await api.place_order(
            private_key=pk,
            proxy_address=user["proxy_wallet_address"],
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
        )

        order_id = str(result.get("orderID") or result.get("order_id") or "")

        await db.log_trade(
            telegram_user_id=uid,
            owner_eoa=user["owner_address"],
            proxy_wallet=user["proxy_wallet_address"],
            leader_wallet="MANUAL",
            condition_id=token_id,
            outcome_side=f"{side} {params.get('outcome', '')}",
            size=size,
            price=price,
            order_id=order_id,
            status="placed",
        )

        await query.edit_message_text(
            f"✅ Order placed!\n"
            f"\n"
            f"{side} {params.get('outcome', '')}\n"
            f"Amount: {size:.2f} at ${price:.4f}\n"
            f"Order ID: {order_id}"
        )

        logger.info("Manual trade placed by user %d: %s", uid, order_id)

    except Exception as exc:
        logger.exception("Manual trade failed for user %d", uid)
        await query.edit_message_text(
            f"❌ Trade failed: {str(exc)[:200]}"
        )

        await db.log_trade(
            telegram_user_id=uid,
            owner_eoa=user["owner_address"],
            proxy_wallet=user["proxy_wallet_address"],
            leader_wallet="MANUAL",
            condition_id=params.get("market", "unknown"),
            outcome_side=f"{params.get('side')} {params.get('outcome', '')}",
            size=0,
            price=0,
            order_id="",
            status="error",
            error=str(exc)[:500],
        )
    finally:
        context.user_data.pop("pending_trade", None)


async def trade_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trade cancellation button press."""
    query = update.callback_query
    await query.answer()

    context.user_data.pop("pending_trade", None)
    await query.edit_message_text("👍 Trade cancelled.")
