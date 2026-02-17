"""Entry point – wires configuration, database, Polymarket APIs,
the copy-trading engine, AI assistant, and Telegram handlers into a single application.
"""

import logging
import os
import sys

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import load_config
from copy_engine import CopyEngine
from algo_engine import AlgoEngine
from database import Database
from handlers import (
    connect_cmd,
    create_wallet_cmd,
    deposit_cmd,
    follow_cmd,
    history_cmd,
    leaders_cmd,
    pause_cmd,
    resume_cmd,
    set_proxy_cmd,
    setup_proxy_cmd,
    start_cmd,
    status_cmd,
    unfollow_cmd,
)
from algo_handlers import (
    enable_algo_cmd,
    disable_algo_cmd,
    algo_status_cmd,
    set_strategy_cmd,
)
from ai_handlers import (
    ai_message_handler,
    trade_confirm_callback,
    trade_cancel_callback,
)
from ai_assistant import GrokAssistant
from polymarket_api import PolymarketAPI
from wallet_manager import WalletManager

# ── logging ──────────────────────────────────────────────────

_data_dir = os.getenv("DATA_DIR", ".")
os.makedirs(_data_dir, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(_data_dir, "bot.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


# ── application lifecycle hooks ──────────────────────────────


async def post_init(application: Application):
    """Called once after the bot connects but before polling begins."""
    cfg = application.bot_data["config"]

    # Database
    db = Database()
    await db.connect()

    # Shared services
    wm = WalletManager(cfg.encryption_key)
    api = PolymarketAPI(cfg, wm)

    # AI Assistant (Grok)
    xai_key = os.getenv("XAI_API_KEY")
    grok = None
    if xai_key:
        grok = GrokAssistant(xai_key)
        logger.info("Grok AI assistant initialized")
    else:
        logger.warning("XAI_API_KEY not set – AI features disabled")

    # Copy-trading engine (receives bot instance for user notifications)
    engine = CopyEngine(cfg, db, wm, api, bot=application.bot)

    # Algo-trading engine
    algo_engine = AlgoEngine(cfg, db, wm, api, bot=application.bot)

    # Store in bot_data so every handler can reach them
    application.bot_data.update(
        {
            "db": db,
            "wallet_mgr": wm,
            "api": api,
            "engine": engine,
            "algo_engine": algo_engine,
            "grok": grok,
        }
    )

    await engine.start()
    await algo_engine.start()

    # Register command menu so Telegram shows commands when user types /
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("start", "Show welcome and all commands"),
        BotCommand("create_wallet", "Make a new wallet"),
        BotCommand("setup_proxy", "Set up your trading wallet"),
        BotCommand("deposit", "See where to send money"),
        BotCommand("connect", "Turn on trading"),
        BotCommand("follow", "Copy someone's trades"),
        BotCommand("unfollow", "Stop copying someone"),
        BotCommand("leaders", "See who you're copying"),
        BotCommand("enable_algo", "Turn on auto trading"),
        BotCommand("disable_algo", "Turn off auto trading"),
        BotCommand("algo_status", "Check auto trading status"),
        BotCommand("set_strategy", "Pick a trading style"),
        BotCommand("pause", "Pause all trading"),
        BotCommand("resume", "Resume all trading"),
        BotCommand("status", "See your account info"),
        BotCommand("history", "See your past trades"),
    ])

    logger.info("Bot fully initialised – copy engine + algo engine running")


async def post_shutdown(application: Application):
    """Graceful teardown."""
    engine: CopyEngine | None = application.bot_data.get("engine")
    if engine:
        await engine.stop()

    algo_engine: AlgoEngine | None = application.bot_data.get("algo_engine")
    if algo_engine:
        await algo_engine.stop()

    api: PolymarketAPI | None = application.bot_data.get("api")
    if api:
        await api.close()

    db: Database | None = application.bot_data.get("db")
    if db:
        await db.close()

    logger.info("Bot shut down cleanly")


# ── main ─────────────────────────────────────────────────────


def main():
    cfg = load_config()

    app = (
        Application.builder()
        .token(cfg.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Seed config into bot_data before the event loop starts
    app.bot_data["config"] = cfg

    # Register every command handler
    commands = {
        "start": start_cmd,
        "create_wallet": create_wallet_cmd,
        "setup_proxy": setup_proxy_cmd,
        "set_proxy": set_proxy_cmd,
        "deposit": deposit_cmd,
        "connect": connect_cmd,
        "follow": follow_cmd,
        "unfollow": unfollow_cmd,
        "leaders": leaders_cmd,
        "pause": pause_cmd,
        "resume": resume_cmd,
        "status": status_cmd,
        "history": history_cmd,
        "enable_algo": enable_algo_cmd,
        "disable_algo": disable_algo_cmd,
        "algo_status": algo_status_cmd,
        "set_strategy": set_strategy_cmd,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))

    # AI message handler (catches all non-command messages)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            ai_message_handler
        )
    )

    # Callback handlers for trade confirmation
    app.add_handler(CallbackQueryHandler(trade_confirm_callback, pattern="^trade_confirm$"))
    app.add_handler(CallbackQueryHandler(trade_cancel_callback, pattern="^trade_cancel$"))

    logger.info("Starting Polymarket Copy-Trading Bot …")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
