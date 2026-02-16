"""Async SQLite database layer for user data, leaders, trades, and logging."""

import logging
import os
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

DB_FILE = os.getenv("DATABASE_PATH", "polybot.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id         INTEGER PRIMARY KEY,
    owner_address       TEXT    UNIQUE NOT NULL,
    encrypted_private_key TEXT  NOT NULL,
    proxy_wallet_address TEXT,
    encrypted_api_key   TEXT,
    encrypted_api_secret TEXT,
    encrypted_passphrase TEXT,
    is_paused           INTEGER DEFAULT 0,
    algo_trading_enabled INTEGER DEFAULT 0,
    algo_strategy       TEXT    DEFAULT 'momentum',
    algo_min_confidence REAL    DEFAULT 0.70,
    sizing_mode         TEXT    DEFAULT 'fixed',
    sizing_value        REAL    DEFAULT 10.0,
    max_slippage        REAL    DEFAULT 0.02,
    max_daily_loss      REAL    DEFAULT 100.0,
    max_per_market      REAL    DEFAULT 50.0,
    max_open_positions  INTEGER DEFAULT 10,
    daily_loss_today    REAL    DEFAULT 0.0,
    daily_loss_date     TEXT,
    created_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS leaders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    leader_wallet   TEXT    NOT NULL,
    added_at        TEXT    NOT NULL,
    UNIQUE(telegram_id, leader_wallet),
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS processed_trades (
    trade_hash   TEXT    NOT NULL,
    telegram_id  INTEGER NOT NULL,
    processed_at TEXT    NOT NULL,
    PRIMARY KEY (trade_hash, telegram_id)
);

CREATE TABLE IF NOT EXISTS trade_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc     TEXT    NOT NULL,
    telegram_user_id  INTEGER NOT NULL,
    owner_eoa         TEXT,
    proxy_wallet      TEXT,
    leader_wallet     TEXT,
    condition_id      TEXT,
    outcome_side      TEXT,
    size              REAL,
    price             REAL,
    order_id          TEXT,
    status            TEXT    NOT NULL,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS chat_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    role            TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin async wrapper around an SQLite database."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ── lifecycle ────────────────────────────────────────────

    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("Database initialised (%s)", self.db_path)

    async def close(self):
        if self._db:
            await self._db.close()

    # ── users ────────────────────────────────────────────────

    async def create_user(
        self, telegram_id: int, owner_address: str, encrypted_pk: str, config
    ):
        await self._db.execute(
            """INSERT INTO users
                   (telegram_id, owner_address, encrypted_private_key,
                    sizing_value, max_slippage, max_daily_loss,
                    max_per_market, max_open_positions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        await self._db.commit()

    async def get_user(self, telegram_id: int):
        cur = await self._db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return await cur.fetchone()

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
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [telegram_id]
        await self._db.execute(
            f"UPDATE users SET {cols} WHERE telegram_id = ?", vals
        )
        await self._db.commit()

    async def get_active_users(self):
        cur = await self._db.execute(
            """SELECT * FROM users
               WHERE is_paused = 0
                 AND proxy_wallet_address IS NOT NULL
                 AND encrypted_api_key IS NOT NULL"""
        )
        return await cur.fetchall()

    # ── leaders ──────────────────────────────────────────────

    async def add_leader(self, telegram_id: int, leader_wallet: str):
        await self._db.execute(
            "INSERT OR IGNORE INTO leaders (telegram_id, leader_wallet, added_at) "
            "VALUES (?, ?, ?)",
            (telegram_id, leader_wallet, _now()),
        )
        await self._db.commit()

    async def remove_leader(self, telegram_id: int, leader_wallet: str):
        await self._db.execute(
            "DELETE FROM leaders WHERE telegram_id = ? AND leader_wallet = ?",
            (telegram_id, leader_wallet),
        )
        await self._db.commit()

    async def get_leaders(self, telegram_id: int) -> list[str]:
        cur = await self._db.execute(
            "SELECT leader_wallet FROM leaders WHERE telegram_id = ?",
            (telegram_id,),
        )
        return [r["leader_wallet"] for r in await cur.fetchall()]

    async def get_all_followed_leaders(self) -> list[str]:
        cur = await self._db.execute(
            """SELECT DISTINCT l.leader_wallet
               FROM leaders l
               JOIN users u ON l.telegram_id = u.telegram_id
               WHERE u.is_paused = 0
                 AND u.encrypted_api_key IS NOT NULL"""
        )
        return [r["leader_wallet"] for r in await cur.fetchall()]

    async def get_followers_of(self, leader_wallet: str):
        cur = await self._db.execute(
            """SELECT u.*
               FROM users u
               JOIN leaders l ON u.telegram_id = l.telegram_id
               WHERE l.leader_wallet = ?
                 AND u.is_paused = 0
                 AND u.encrypted_api_key IS NOT NULL""",
            (leader_wallet,),
        )
        return await cur.fetchall()

    # ── dedup ────────────────────────────────────────────────

    async def is_trade_processed(self, trade_hash: str, telegram_id: int) -> bool:
        cur = await self._db.execute(
            "SELECT 1 FROM processed_trades WHERE trade_hash = ? AND telegram_id = ?",
            (trade_hash, telegram_id),
        )
        return (await cur.fetchone()) is not None

    async def mark_trade_processed(self, trade_hash: str, telegram_id: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO processed_trades "
            "(trade_hash, telegram_id, processed_at) VALUES (?, ?, ?)",
            (trade_hash, telegram_id, _now()),
        )
        await self._db.commit()

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
        await self._db.execute(
            """INSERT INTO trade_log
                   (timestamp_utc, telegram_user_id, owner_eoa, proxy_wallet,
                    leader_wallet, condition_id, outcome_side, size, price,
                    order_id, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        await self._db.commit()

    async def get_trade_history(self, telegram_id: int, limit: int = 20):
        cur = await self._db.execute(
            "SELECT * FROM trade_log WHERE telegram_user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (telegram_id, limit),
        )
        return await cur.fetchall()

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
        cur = await self._db.execute(
            """SELECT COUNT(DISTINCT condition_id) FROM trade_log
               WHERE telegram_user_id = ? AND status = 'placed'""",
            (telegram_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def get_market_exposure(self, telegram_id: int, condition_id: str) -> float:
        cur = await self._db.execute(
            """SELECT COALESCE(SUM(size * price), 0) FROM trade_log
               WHERE telegram_user_id = ? AND condition_id = ?
                 AND status = 'placed'""",
            (telegram_id, condition_id),
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    # ── chat history ─────────────────────────────────────────

    async def save_chat_message(self, telegram_id: int, role: str, message: str):
        await self._db.execute(
            "INSERT INTO chat_history (telegram_id, role, message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (telegram_id, role, message[:2000], _now()),
        )
        await self._db.commit()

    async def get_chat_history(self, telegram_id: int, limit: int = 20) -> list[dict]:
        cur = await self._db.execute(
            "SELECT role, message FROM chat_history "
            "WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "message": r["message"]} for r in reversed(rows)]
