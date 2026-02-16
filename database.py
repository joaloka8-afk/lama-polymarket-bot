"""Async MySQL database layer for user data, leaders, trades, and logging."""

import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiomysql

logger = logging.getLogger(__name__)


def _parse_mysql_url(url: str) -> dict:
    """Parse MYSQL_URL into connection kwargs for aiomysql."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "db": parsed.path.lstrip("/") or "railway",
    }


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        telegram_id         BIGINT PRIMARY KEY,
        owner_address       VARCHAR(42) UNIQUE NOT NULL,
        encrypted_private_key TEXT NOT NULL,
        proxy_wallet_address VARCHAR(42),
        encrypted_api_key   TEXT,
        encrypted_api_secret TEXT,
        encrypted_passphrase TEXT,
        is_paused           TINYINT DEFAULT 0,
        algo_trading_enabled TINYINT DEFAULT 0,
        algo_strategy       VARCHAR(50) DEFAULT 'momentum',
        algo_min_confidence DOUBLE DEFAULT 0.70,
        sizing_mode         VARCHAR(20) DEFAULT 'fixed',
        sizing_value        DOUBLE DEFAULT 10.0,
        max_slippage        DOUBLE DEFAULT 0.02,
        max_daily_loss      DOUBLE DEFAULT 100.0,
        max_per_market      DOUBLE DEFAULT 50.0,
        max_open_positions  INT DEFAULT 10,
        daily_loss_today    DOUBLE DEFAULT 0.0,
        daily_loss_date     VARCHAR(10),
        created_at          VARCHAR(30) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS leaders (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        telegram_id     BIGINT NOT NULL,
        leader_wallet   VARCHAR(42) NOT NULL,
        added_at        VARCHAR(30) NOT NULL,
        UNIQUE KEY uq_leader (telegram_id, leader_wallet),
        FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
    )""",
    """CREATE TABLE IF NOT EXISTS processed_trades (
        trade_hash   VARCHAR(128) NOT NULL,
        telegram_id  BIGINT NOT NULL,
        processed_at VARCHAR(30) NOT NULL,
        PRIMARY KEY (trade_hash, telegram_id)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_log (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        timestamp_utc     VARCHAR(30) NOT NULL,
        telegram_user_id  BIGINT NOT NULL,
        owner_eoa         VARCHAR(42),
        proxy_wallet      VARCHAR(42),
        leader_wallet     VARCHAR(42),
        condition_id      TEXT,
        outcome_side      VARCHAR(100),
        size              DOUBLE,
        price             DOUBLE,
        order_id          VARCHAR(200),
        status            VARCHAR(20) NOT NULL,
        error             TEXT
    )""",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Async wrapper around a MySQL database via aiomysql."""

    def __init__(self):
        self._pool: aiomysql.Pool | None = None

    async def connect(self):
        mysql_url = os.getenv("MYSQL_URL") or os.getenv("DATABASE_URL")
        if not mysql_url:
            raise RuntimeError(
                "MYSQL_URL or DATABASE_URL environment variable is required"
            )

        conn_kwargs = _parse_mysql_url(mysql_url)
        self._pool = await aiomysql.create_pool(
            minsize=1,
            maxsize=5,
            autocommit=True,
            **conn_kwargs,
        )

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                for stmt in _SCHEMA:
                    await cur.execute(stmt)

        logger.info("Database initialised (MySQL %s/%s)", conn_kwargs["host"], conn_kwargs["db"])

    async def close(self):
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()

    async def _fetchone(self, query: str, args=None) -> dict | None:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, args)
                return await cur.fetchone()

    async def _fetchall(self, query: str, args=None) -> list[dict]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, args)
                return await cur.fetchall()

    async def _execute(self, query: str, args=None):
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args)

    # ── users ────────────────────────────────────────────────

    async def create_user(
        self, telegram_id: int, owner_address: str, encrypted_pk: str, config
    ):
        await self._execute(
            """INSERT INTO users
                   (telegram_id, owner_address, encrypted_private_key,
                    sizing_value, max_slippage, max_daily_loss,
                    max_per_market, max_open_positions, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                telegram_id,
                owner_address,
                encrypted_pk,
                config.default_order_size,
                config.default_max_slippage,
                config.default_max_daily_loss,
                config.default_max_per_market,
                config.default_max_open_positions,
                _now(),
            ),
        )

    async def get_user(self, telegram_id: int):
        return await self._fetchone(
            "SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)
        )

    _ALLOWED_USER_COLUMNS = frozenset({
        "proxy_wallet_address",
        "encrypted_api_key",
        "encrypted_api_secret",
        "encrypted_passphrase",
        "is_paused",
        "algo_trading_enabled",
        "algo_strategy",
        "algo_min_confidence",
        "sizing_mode",
        "sizing_value",
        "max_slippage",
        "max_daily_loss",
        "max_per_market",
        "max_open_positions",
        "daily_loss_today",
        "daily_loss_date",
    })

    async def update_user(self, telegram_id: int, **kwargs):
        if not kwargs:
            return
        bad = set(kwargs) - self._ALLOWED_USER_COLUMNS
        if bad:
            raise ValueError(f"Disallowed column(s): {bad}")
        cols = ", ".join(f"{k} = %s" for k in kwargs)
        vals = list(kwargs.values()) + [telegram_id]
        await self._execute(
            f"UPDATE users SET {cols} WHERE telegram_id = %s", vals
        )

    async def get_active_users(self):
        return await self._fetchall(
            """SELECT * FROM users
               WHERE is_paused = 0
                 AND proxy_wallet_address IS NOT NULL
                 AND encrypted_api_key IS NOT NULL"""
        )

    # ── leaders ──────────────────────────────────────────────

    async def add_leader(self, telegram_id: int, leader_wallet: str):
        await self._execute(
            "INSERT IGNORE INTO leaders (telegram_id, leader_wallet, added_at) "
            "VALUES (%s, %s, %s)",
            (telegram_id, leader_wallet, _now()),
        )

    async def remove_leader(self, telegram_id: int, leader_wallet: str):
        await self._execute(
            "DELETE FROM leaders WHERE telegram_id = %s AND leader_wallet = %s",
            (telegram_id, leader_wallet),
        )

    async def get_leaders(self, telegram_id: int) -> list[str]:
        rows = await self._fetchall(
            "SELECT leader_wallet FROM leaders WHERE telegram_id = %s",
            (telegram_id,),
        )
        return [r["leader_wallet"] for r in rows]

    async def get_all_followed_leaders(self) -> list[str]:
        rows = await self._fetchall(
            """SELECT DISTINCT l.leader_wallet
               FROM leaders l
               JOIN users u ON l.telegram_id = u.telegram_id
               WHERE u.is_paused = 0
                 AND u.encrypted_api_key IS NOT NULL"""
        )
        return [r["leader_wallet"] for r in rows]

    async def get_followers_of(self, leader_wallet: str):
        return await self._fetchall(
            """SELECT u.*
               FROM users u
               JOIN leaders l ON u.telegram_id = l.telegram_id
               WHERE l.leader_wallet = %s
                 AND u.is_paused = 0
                 AND u.encrypted_api_key IS NOT NULL""",
            (leader_wallet,),
        )

    # ── dedup ────────────────────────────────────────────────

    async def is_trade_processed(self, trade_hash: str, telegram_id: int) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM processed_trades WHERE trade_hash = %s AND telegram_id = %s",
            (trade_hash, telegram_id),
        )
        return row is not None

    async def mark_trade_processed(self, trade_hash: str, telegram_id: int):
        await self._execute(
            "INSERT IGNORE INTO processed_trades "
            "(trade_hash, telegram_id, processed_at) VALUES (%s, %s, %s)",
            (trade_hash, telegram_id, _now()),
        )

    # ── trade log ────────────────────────────────────────────

    async def log_trade(
        self,
        telegram_user_id: int,
        owner_eoa: str,
        proxy_wallet: str,
        leader_wallet: str,
        condition_id: str,
        outcome_side: str,
        size: float,
        price: float,
        order_id: str,
        status: str,
        error: str | None = None,
    ):
        await self._execute(
            """INSERT INTO trade_log
                   (timestamp_utc, telegram_user_id, owner_eoa, proxy_wallet,
                    leader_wallet, condition_id, outcome_side, size, price,
                    order_id, status, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                _now(),
                telegram_user_id,
                owner_eoa,
                proxy_wallet,
                leader_wallet,
                condition_id,
                outcome_side,
                size,
                price,
                order_id,
                status,
                error,
            ),
        )

    async def get_trade_history(self, telegram_id: int, limit: int = 20):
        return await self._fetchall(
            "SELECT * FROM trade_log WHERE telegram_user_id = %s "
            "ORDER BY id DESC LIMIT %s",
            (telegram_id, limit),
        )

    # ── risk helpers ─────────────────────────────────────────

    async def get_daily_loss(self, telegram_id: int) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user = await self.get_user(telegram_id)
        if user and user["daily_loss_date"] == today:
            return float(user["daily_loss_today"])
        await self.update_user(telegram_id, daily_loss_today=0.0, daily_loss_date=today)
        return 0.0

    async def add_daily_loss(self, telegram_id: int, amount: float):
        current = await self.get_daily_loss(telegram_id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.update_user(
            telegram_id, daily_loss_today=current + amount, daily_loss_date=today
        )

    async def count_open_positions(self, telegram_id: int) -> int:
        row = await self._fetchone(
            """SELECT COUNT(DISTINCT condition_id) as cnt FROM trade_log
               WHERE telegram_user_id = %s AND status = 'placed'""",
            (telegram_id,),
        )
        return row["cnt"] if row else 0

    async def get_market_exposure(self, telegram_id: int, condition_id: str) -> float:
        row = await self._fetchone(
            """SELECT COALESCE(SUM(size * price), 0) as total FROM trade_log
               WHERE telegram_user_id = %s AND condition_id = %s
                 AND status = 'placed'""",
            (telegram_id, condition_id),
        )
        return float(row["total"]) if row else 0.0
