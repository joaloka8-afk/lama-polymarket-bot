"""Centralised configuration loaded once from environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    encryption_key: str

    builder_api_key: str
    builder_secret: str
    builder_passphrase: str

    chain_id: int
    relayer_url: str
    clob_url: str
    data_api_url: str
    bridge_url: str

    poll_interval: int
    default_order_size: float
    default_max_slippage: float
    default_max_daily_loss: float
    default_max_per_market: float
    default_max_open_positions: int
    
    xai_api_key: str


def load_config() -> Config:
    required = ["TELEGRAM_BOT_TOKEN", "ENCRYPTION_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Config(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        encryption_key=os.environ["ENCRYPTION_KEY"],
        builder_api_key=os.getenv("BUILDER_API_KEY", ""),
        builder_secret=os.getenv("BUILDER_SECRET", ""),
        builder_passphrase=os.getenv("BUILDER_PASS_PHRASE", ""),
        chain_id=int(os.getenv("CHAIN_ID", "137")),
        relayer_url=os.getenv("RELAYER_URL", "https://relayer-v2.polymarket.com"),
        clob_url=os.getenv("CLOB_URL", "https://clob.polymarket.com"),
        data_api_url=os.getenv("DATA_API_URL", "https://data-api.polymarket.com"),
        bridge_url=os.getenv("BRIDGE_URL", "https://bridge.polymarket.com"),
        poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "30")),
        default_order_size=float(os.getenv("DEFAULT_ORDER_SIZE_USDC", "10.0")),
        default_max_slippage=float(os.getenv("DEFAULT_MAX_SLIPPAGE", "0.02")),
        default_max_daily_loss=float(os.getenv("DEFAULT_MAX_DAILY_LOSS", "100.0")),
        default_max_per_market=float(os.getenv("DEFAULT_MAX_PER_MARKET", "50.0")),
        default_max_open_positions=int(os.getenv("DEFAULT_MAX_OPEN_POSITIONS", "10")),
        xai_api_key=os.getenv("XAI_API_KEY", ""),
    )
