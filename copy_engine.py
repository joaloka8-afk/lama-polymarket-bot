"""Background copy-trading engine.

Polls the Data API for each followed leader, deduplicates trades,
enforces per-user risk limits, and places follower orders via the CLOB.
"""

import asyncio
import logging

from telegram import Bot

from config import Config
from database import Database
from polymarket_api import PolymarketAPI
from wallet_manager import WalletManager

logger = logging.getLogger(__name__)


class CopyEngine:
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

    # ── lifecycle ────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Copy engine started (poll every %d s)", self.cfg.poll_interval
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Copy engine stopped")

    # ── main loop ────────────────────────────────────────────

    async def _loop(self):
        while self._running:
            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Error in copy-trade poll cycle")
            await asyncio.sleep(self.cfg.poll_interval)

    async def _poll_cycle(self):
        leaders = await self.db.get_all_followed_leaders()
        if not leaders:
            return

        # Fetch all leader trades concurrently
        results = await asyncio.gather(
            *(self.api.get_leader_trades(lw) for lw in leaders),
            return_exceptions=True,
        )

        for leader_wallet, result in zip(leaders, results):
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to fetch trades for leader %s: %s",
                    leader_wallet,
                    result,
                )
                continue

            trades: list[dict] = result
            if not trades:
                continue

            followers = await self.db.get_followers_of(leader_wallet)
            if not followers:
                continue

            for trade in trades:
                t_hash = PolymarketAPI.trade_hash(trade)
                for follower in followers:
                    try:
                        await self._process_trade(
                            follower, leader_wallet, trade, t_hash
                        )
                    except Exception:
                        logger.exception(
                            "Error processing trade %s for user %d",
                            t_hash[:12],
                            follower["telegram_id"],
                        )

    # ── per-trade logic ──────────────────────────────────────

    async def _process_trade(
        self, follower, leader_wallet: str, trade: dict, t_hash: str
    ):
        uid: int = follower["telegram_id"]

        # ── dedup ────────────────────────────────────────────
        if await self.db.is_trade_processed(t_hash, uid):
            return

        # ── extract signal ───────────────────────────────────
        token_id = trade.get("asset")
        condition_id = trade.get("conditionId", "")
        side = trade.get("side")  # BUY | SELL
        leader_price = float(trade.get("price", 0))
        leader_size = float(trade.get("size", 0))
        outcome = trade.get("outcome", "")

        if not token_id or not side or leader_price <= 0:
            logger.warning(
                "Skipping invalid trade from leader %s (hash %s)",
                leader_wallet[:12],
                t_hash[:12],
            )
            await self.db.mark_trade_processed(t_hash, uid)
            return

        # ── risk limits ──────────────────────────────────────
        daily_loss = await self.db.get_daily_loss(uid)
        if daily_loss >= follower["max_daily_loss"]:
            logger.info("User %d: daily-loss limit reached", uid)
            await self.db.mark_trade_processed(t_hash, uid)
            return

        open_pos = await self.db.count_open_positions(uid)
        if open_pos >= follower["max_open_positions"]:
            logger.info("User %d: max-open-positions reached", uid)
            await self.db.mark_trade_processed(t_hash, uid)
            return

        if condition_id:
            exposure = await self.db.get_market_exposure(uid, condition_id)
            if exposure >= follower["max_per_market"]:
                logger.info(
                    "User %d: max-per-market reached for %s", uid, condition_id[:12]
                )
                await self.db.mark_trade_processed(t_hash, uid)
                return

        # ── sizing ───────────────────────────────────────────
        follower_size = self._calc_size(follower, leader_size)
        if follower_size <= 0:
            await self.db.mark_trade_processed(t_hash, uid)
            return

        # ── price with slippage guard ────────────────────────
        slip = follower["max_slippage"]
        if side.upper() == "BUY":
            limit_price = round(min(leader_price * (1 + slip), 0.99), 4)
        else:
            limit_price = round(max(leader_price * (1 - slip), 0.01), 4)

        # ── decrypt credentials ──────────────────────────────
        try:
            pk = self.wm.decrypt(follower["encrypted_private_key"])
            api_key = self.wm.decrypt(follower["encrypted_api_key"])
            api_secret = self.wm.decrypt(follower["encrypted_api_secret"])
            passphrase = self.wm.decrypt(follower["encrypted_passphrase"])
        except Exception:
            logger.error("Credential decryption failed for user %d", uid)
            await self.db.mark_trade_processed(t_hash, uid)
            return

        # ── execute ──────────────────────────────────────────
        outcome_side = f"{side} {outcome}" if outcome else side
        order_id: str = ""
        status = "error"
        error_msg: str | None = None

        try:
            result = await self.api.place_order(
                private_key=pk,
                proxy_address=follower["proxy_wallet_address"],
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                token_id=token_id,
                side=side,
                price=limit_price,
                size=follower_size,
            )
            order_id = str(
                result.get("orderID")
                or result.get("order_id")
                or result.get("id", "")
            )
            status = "placed"
            logger.info(
                "Order placed  user=%d  %s %s@%.4f x%.2f  leader=%s",
                uid,
                side,
                token_id[:16],
                limit_price,
                follower_size,
                leader_wallet[:12],
            )
        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.error("Order failed for user %d: %s", uid, error_msg)

        # ── log ──────────────────────────────────────────────
        await self.db.log_trade(
            telegram_user_id=uid,
            owner_eoa=follower["owner_address"],
            proxy_wallet=follower["proxy_wallet_address"],
            leader_wallet=leader_wallet,
            condition_id=condition_id,
            outcome_side=outcome_side,
            size=follower_size,
            price=limit_price,
            order_id=order_id,
            status=status,
            error=error_msg,
        )
        await self.db.mark_trade_processed(t_hash, uid)

        # ── notify ───────────────────────────────────────────
        await self._notify(uid, status, outcome_side, follower_size, limit_price, error_msg)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _calc_size(follower, leader_size: float) -> float:
        mode = follower["sizing_mode"]
        value = float(follower["sizing_value"])
        if mode == "proportional":
            return round(leader_size * value, 2)
        return value  # default: fixed USDC

    async def _notify(
        self,
        telegram_id: int,
        status: str,
        outcome_side: str,
        size: float,
        price: float,
        error: str | None,
    ):
        if not self.bot:
            return

        cost = size * price

        if status == "placed":
            # Get PNL stats for the card
            pnl = await self.db.get_pnl_stats(telegram_id)
            pnl_emoji = "🟢" if pnl["realized_pnl"] >= 0 else "🔴"
            today_emoji = "🟢" if pnl["today_pnl"] >= 0 else "🔴"

            text = (
                f"👥 COPY TRADE PLACED\n"
                f"\n"
                f"📊 {outcome_side}\n"
                f"💰 ${cost:.2f} ({size:.2f} shares at ${price:.4f})\n"
                f"\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 YOUR PNL CARD\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"{today_emoji} Today: ${pnl['today_pnl']:+.2f} ({pnl['today_trades']} trades)\n"
                f"{pnl_emoji} All time: ${pnl['realized_pnl']:+.2f} ({pnl['total_trades']} trades)\n"
                f"📈 Open positions: {pnl['open_positions']}\n"
                f"💵 Total bought: ${pnl['total_bought']:.2f}\n"
                f"💵 Total sold: ${pnl['total_sold']:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            text = (
                f"❌ COPY TRADE FAILED\n"
                f"\n"
                f"📊 {outcome_side}\n"
                f"💰 ${cost:.2f} ({size:.2f} shares at ${price:.4f})\n"
                f"\n"
                f"Error: {(error or 'unknown')[:200]}"
            )
        try:
            await self.bot.send_message(chat_id=telegram_id, text=text)
        except Exception:
            logger.debug("Could not notify user %d", telegram_id)
