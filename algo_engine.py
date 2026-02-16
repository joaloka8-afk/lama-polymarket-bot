"""Algorithmic trading engine - runs strategies and executes signals."""

import asyncio
import logging

from telegram import Bot

from algo_strategies import get_strategy, list_strategies
from config import Config
from database import Database
from polymarket_api import PolymarketAPI
from wallet_manager import WalletManager

logger = logging.getLogger(__name__)


class AlgoEngine:
    """Background engine that runs algo strategies for users who enable it."""

    def __init__(
        self,
        config: Config,
        db: Database,
        wallet_mgr: WalletManager,
        api: PolymarketAPI,
        bot: Bot | None = None,
    ):
        self.cfg = config
        self.db = db
        self.wm = wallet_mgr
        self.api = api
        self.bot = bot
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the algo engine."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Algo engine started (poll every %d s)", self.cfg.poll_interval)

    async def stop(self):
        """Stop the algo engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Algo engine stopped")

    async def _loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Error in algo engine poll cycle")
            await asyncio.sleep(self.cfg.poll_interval)

    async def _poll_cycle(self):
        """Run strategies for all algo-enabled users."""
        users = await self._get_algo_users()
        if not users:
            return

        for user in users:
            try:
                await self._run_user_strategy(user)
            except Exception:
                logger.exception(
                    "Error running algo strategy for user %d", user["telegram_id"]
                )

    async def _get_algo_users(self):
        """Get all users with algo trading enabled."""
        cur = await self.db._db.execute(
            """SELECT * FROM users
               WHERE algo_trading_enabled = 1
                 AND is_paused = 0
                 AND proxy_wallet_address IS NOT NULL
                 AND encrypted_api_key IS NOT NULL"""
        )
        return await cur.fetchall()

    async def _run_user_strategy(self, user):
        """Run the configured strategy for one user."""
        uid = user["telegram_id"]
        strategy_name = user["algo_strategy"] or "momentum"
        min_confidence = float(user["algo_min_confidence"] or 0.70)

        strategy = get_strategy(strategy_name)
        if not strategy:
            logger.warning("Unknown strategy %s for user %d", strategy_name, uid)
            return

        # TODO: Fetch real market data from Polymarket Data API or CLOB
        # For now, use placeholder data
        # In production, you'd poll multiple markets and analyze them
        market_data = await self._get_market_data()

        for market in market_data:
            signal = await strategy.analyze(market)
            if not signal:
                continue

            # Check confidence threshold
            if signal["confidence"] < min_confidence:
                logger.debug(
                    "Signal confidence %.2f below threshold %.2f for user %d",
                    signal["confidence"],
                    min_confidence,
                    uid,
                )
                continue

            # Check risk limits
            daily_loss = await self.db.get_daily_loss(uid)
            if daily_loss >= user["max_daily_loss"]:
                logger.info("User %d hit daily loss limit", uid)
                continue

            open_pos = await self.db.count_open_positions(uid)
            if open_pos >= user["max_open_positions"]:
                logger.info("User %d at max open positions", uid)
                continue

            # Execute the trade
            await self._execute_algo_trade(user, signal)

    async def _get_market_data(self) -> list[dict]:
        """Fetch market data for analysis.
        
        TODO: Implement real market data fetching from Polymarket.
        This is a placeholder that returns empty list.
        """
        # In production:
        # 1. Fetch trending markets from Data API
        # 2. Get order book data from CLOB
        # 3. Calculate technical indicators
        # 4. Return list of market_data dicts
        return []

    async def _execute_algo_trade(self, user, signal: dict):
        """Execute an algo-generated trade signal."""
        uid = user["telegram_id"]
        
        try:
            # Decrypt credentials
            pk = self.wm.decrypt(user["encrypted_private_key"])
            api_key = self.wm.decrypt(user["encrypted_api_key"])
            api_secret = self.wm.decrypt(user["encrypted_api_secret"])
            passphrase = self.wm.decrypt(user["encrypted_passphrase"])

            # Get current order book for pricing
            book = await self.api.get_order_book(signal["token_id"])
            
            side = signal["side"]
            if side == "BUY":
                best_ask = float(book.get("asks", [[0.5]])[0][0])
                price = min(best_ask * 1.02, 0.99)
            else:
                best_bid = float(book.get("bids", [[0.5]])[0][0])
                price = max(best_bid * 0.98, 0.01)

            price = round(price, 4)
            amount = signal["suggested_size"]
            size = round(amount / price, 2)

            # Place order
            result = await self.api.place_order(
                private_key=pk,
                proxy_address=user["proxy_wallet_address"],
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                token_id=signal["token_id"],
                side=side,
                price=price,
                size=size,
            )

            order_id = str(result.get("orderID") or result.get("order_id") or "")

            # Log it
            await self.db.log_trade(
                telegram_user_id=uid,
                owner_eoa=user["owner_address"],
                proxy_wallet=user["proxy_wallet_address"],
                leader_wallet=f"ALGO:{signal['reason']}",
                condition_id=signal["token_id"],
                outcome_side=f"{side} {signal['outcome']}",
                size=size,
                price=price,
                order_id=order_id,
                status="placed",
            )

            logger.info(
                "Algo trade executed user=%d strategy=%s %s %s@%.4f reason=%s",
                uid,
                user["algo_strategy"],
                side,
                signal["token_id"][:16],
                price,
                signal["reason"][:50],
            )

            # Notify user
            await self._notify(
                uid,
                side,
                signal["outcome"],
                size,
                price,
                signal["reason"],
                signal["confidence"],
            )

        except Exception as exc:
            logger.error("Algo trade failed for user %d: %s", uid, exc)
            await self.db.log_trade(
                telegram_user_id=uid,
                owner_eoa=user["owner_address"],
                proxy_wallet=user["proxy_wallet_address"],
                leader_wallet=f"ALGO:{user['algo_strategy']}",
                condition_id=signal.get("token_id", "unknown"),
                outcome_side=f"{signal['side']} {signal['outcome']}",
                size=0,
                price=0,
                order_id="",
                status="error",
                error=str(exc)[:500],
            )

    async def _notify(
        self,
        telegram_id: int,
        side: str,
        outcome: str,
        size: float,
        price: float,
        reason: str,
        confidence: float,
    ):
        """Notify user of algo trade execution."""
        if not self.bot:
            return

        emoji = "🤖📈" if side == "BUY" else "🤖📉"
        text = (
            f"{emoji} **Algo Trade Executed**\n\n"
            f"{side} {outcome}\n"
            f"Size: {size:.2f} @ ${price:.4f}\n"
            f"Confidence: {confidence*100:.0f}%\n"
            f"Reason: {reason}"
        )

        try:
            await self.bot.send_message(chat_id=telegram_id, text=text)
        except Exception:
            logger.debug("Could not notify user %d", telegram_id)
