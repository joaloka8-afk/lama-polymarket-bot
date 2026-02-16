"""AI-powered message handlers for natural language interaction and chat-to-trade."""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
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


# ═════════════════════════════════════════════════════════════
# AI Message Handler
# ═════════════════════════════════════════════════════════════

async def ai_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language messages using Grok AI."""
    bd = context.bot_data
    db = bd["db"]
    grok: GrokAssistant = bd.get("grok")
    
    if not grok:
        await update.message.reply_text(
            "❌ AI assistant not configured. Set XAI_API_KEY in your .env file."
        )
        return
    
    uid = update.effective_user.id
    message_text = update.message.text
    
    # Get user context
    user = await db.get_user(uid)
    leaders = await db.get_leaders(uid) if user else []
    
    user_context = {
        "has_wallet": user is not None,
        "has_proxy": user and user["proxy_wallet_address"] is not None,
        "trading_enabled": user and user["encrypted_api_key"] is not None,
        "is_paused": user and user["is_paused"] == 1,
        "leader_count": len(leaders),
    }
    
    # Send typing indicator
    await update.message.chat.send_action("typing")
    
    # Understand intent using Grok
    try:
        result = await grok.understand_intent(message_text, user_context)
    except Exception as exc:
        logger.exception("Grok intent understanding failed")
        await update.message.reply_text(
            "🤔 I'm having trouble understanding. Could you try rephrasing?"
        )
        return
    
    intent = result.get("intent", "chat")
    params = result.get("params", {})
    response = result.get("response", "")
    requires_confirmation = result.get("requires_confirmation", False)
    
    # Route to appropriate handler
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
        # Just chat
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


# ═════════════════════════════════════════════════════════════
# Trade Intent Handler (with confirmation)
# ═════════════════════════════════════════════════════════════

async def handle_trade_intent(
    update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict, ai_response: str
):
    """Handle trade intent with confirmation buttons."""
    bd = context.bot_data
    db = bd["db"]
    grok: GrokAssistant = bd.get("grok")
    uid = update.effective_user.id
    
    # Check if user is set up
    user = await db.get_user(uid)
    if not user or not user["proxy_wallet_address"] or not user["encrypted_api_key"]:
        await update.message.reply_text(
            "❌ You need to complete setup first:\n"
            "/create\\_wallet → /setup\\_proxy → /deposit → /connect",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    if user["is_paused"]:
        await update.message.reply_text(
            "⏸️ Your account is paused. Use /resume to enable trading."
        )
        return
    
    # Generate trade summary
    summary = await grok.generate_trade_summary(params)
    
    # Store trade params in user_data for callback
    context.user_data["pending_trade"] = params
    
    # Create confirmation buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="trade_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        summary,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )


# ═════════════════════════════════════════════════════════════
# Trade Confirmation Callbacks
# ═════════════════════════════════════════════════════════════

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
        await query.edit_message_text("❌ Trade expired or not found.")
        return
    
    # Get user
    user = await db.get_user(uid)
    if not user:
        await query.edit_message_text("❌ User not found.")
        return
    
    await query.edit_message_text("⏳ Placing order...")
    
    # Execute the trade
    try:
        # Decrypt credentials
        pk = wm.decrypt(user["encrypted_private_key"])
        api_key = wm.decrypt(user["encrypted_api_key"])
        api_secret = wm.decrypt(user["encrypted_api_secret"])
        passphrase = wm.decrypt(user["encrypted_passphrase"])
        
        # TODO: Resolve market description to token_id
        # For now, user needs to provide token_id directly or we need market search
        token_id = params.get("token_id") or params.get("market")
        
        if not token_id or not token_id.startswith("0x"):
            await query.edit_message_text(
                "❌ Could not resolve market. Please provide the token ID directly.\n"
                "Example: \"buy 10 on token 0x123...\""
            )
            return
        
        side = params.get("side", "BUY")
        amount = float(params.get("amount", 0))
        
        # Get current price from order book
        book = await api.get_order_book(token_id)
        if side == "BUY":
            # Use best ask + slippage
            best_ask = float(book.get("asks", [[0.5]])[0][0])
            price = min(best_ask * 1.02, 0.99)
        else:
            # Use best bid - slippage
            best_bid = float(book.get("bids", [[0.5]])[0][0])
            price = max(best_bid * 0.98, 0.01)
        
        price = round(price, 4)
        size = round(amount / price, 2)
        
        # Place order
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
        
        # Log it
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
            f"✅ **Order placed!**\n\n"
            f"{side} {params.get('outcome', '')}\n"
            f"Size: {size:.2f} @ ${price:.4f}\n"
            f"Order ID: `{order_id}`",
            parse_mode=ParseMode.MARKDOWN,
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
        # Clear pending trade
        context.user_data.pop("pending_trade", None)


async def trade_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trade cancellation button press."""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop("pending_trade", None)
    await query.edit_message_text("❌ Trade cancelled.")
