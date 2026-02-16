"""Unified wrapper around every Polymarket service the bot talks to.

* Relayer  – proxy-wallet deployment  (py-builder-relayer-client)
* CLOB     – authentication + order placement  (py-clob-client)
* Bridge   – deposit-address generation  (REST)
* Data API – leader-trade polling  (REST)
"""

import asyncio
import hashlib
import logging
from typing import Any

import httpx
from py_builder_relayer_client.client import RelayClient
from py_builder_signing_sdk.config import BuilderApiKeyCreds, BuilderConfig
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from config import Config
from wallet_manager import WalletManager

logger = logging.getLogger(__name__)


class PolymarketAPI:
    """Façade that delegates to the right Polymarket SDK / HTTP endpoint."""

    def __init__(self, config: Config, wallet_manager: WalletManager):
        self.cfg = config
        self.wm = wallet_manager
        self._http = httpx.AsyncClient(timeout=30.0)

        self._builder_config: BuilderConfig | None = None
        if config.builder_api_key:
            self._builder_config = BuilderConfig(
                local_builder_creds=BuilderApiKeyCreds(
                    key=config.builder_api_key,
                    secret=config.builder_secret,
                    passphrase=config.builder_passphrase,
                )
            )

    async def close(self):
        await self._http.aclose()

    # ──────────────────────────────────────────────────────────
    # Proxy wallet (Gnosis Safe via Polymarket Relayer)
    # ──────────────────────────────────────────────────────────

    def _relay_client(self, private_key: str) -> RelayClient:
        if not self._builder_config:
            raise RuntimeError(
                "Builder credentials not configured. "
                "Set BUILDER_API_KEY / BUILDER_SECRET / BUILDER_PASS_PHRASE in .env"
            )
        return RelayClient(
            self.cfg.relayer_url,
            self.cfg.chain_id,
            private_key,
            self._builder_config,
        )

    async def deploy_proxy(self, private_key: str) -> dict[str, Any]:
        """Deploy a Gnosis-Safe proxy wallet; returns ``{address, already_deployed, tx}``."""

        def _work():
            relay = self._relay_client(private_key)
            safe_addr = relay.get_expected_safe()

            if relay.get_deployed(safe_addr):
                return {"address": safe_addr, "already_deployed": True, "tx": None}

            resp = relay.deploy()
            tx_result = resp.wait()
            return {"address": safe_addr, "already_deployed": False, "tx": tx_result}

        return await asyncio.to_thread(_work)

    async def is_proxy_deployed(self, proxy_address: str, private_key: str) -> bool:
        def _check():
            relay = self._relay_client(private_key)
            return relay.get_deployed(proxy_address)

        return await asyncio.to_thread(_check)

    def derive_proxy_address(self, owner_address: str) -> str:
        return WalletManager.derive_proxy_address(owner_address)

    # ──────────────────────────────────────────────────────────
    # Bridge (deposit-address generation)
    # ──────────────────────────────────────────────────────────

    async def create_deposit_addresses(self, proxy_wallet_address: str) -> dict:
        """POST to bridge; returns chain→deposit-address map."""
        resp = await self._http.post(
            f"{self.cfg.bridge_url}/deposit",
            json={"address": proxy_wallet_address},
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────
    # CLOB – authentication
    # ──────────────────────────────────────────────────────────

    async def derive_api_credentials(
        self, private_key: str, proxy_address: str
    ) -> dict[str, str]:
        """Derive L2 API creds from the L1 signer.  Returns dict with keys
        ``api_key``, ``api_secret``, ``passphrase``."""

        def _derive():
            client = ClobClient(
                host=self.cfg.clob_url,
                key=private_key,
                chain_id=self.cfg.chain_id,
                signature_type=2,  # POLY_GNOSIS_SAFE
                funder=proxy_address,
            )
            creds = client.create_or_derive_api_creds()
            if creds is None:
                raise RuntimeError(
                    "Polymarket returned no API credentials. "
                    "Ensure the proxy wallet is deployed and funded."
                )
            return {
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "passphrase": creds.api_passphrase,
            }

        return await asyncio.to_thread(_derive)

    # ──────────────────────────────────────────────────────────
    # CLOB – trading
    # ──────────────────────────────────────────────────────────

    def _clob_client(
        self,
        private_key: str,
        proxy_address: str,
        api_key: str,
        api_secret: str,
        passphrase: str,
    ) -> ClobClient:
        return ClobClient(
            host=self.cfg.clob_url,
            key=private_key,
            chain_id=self.cfg.chain_id,
            signature_type=2,
            funder=proxy_address,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=passphrase,
            ),
        )

    async def place_order(
        self,
        private_key: str,
        proxy_address: str,
        api_key: str,
        api_secret: str,
        passphrase: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """Place a GTC limit order and return the CLOB response."""

        def _place():
            client = self._clob_client(
                private_key, proxy_address, api_key, api_secret, passphrase
            )
            order_args = OrderArgs(
                price=price,
                size=size,
                side=BUY if side.upper() == "BUY" else SELL,
                token_id=token_id,
            )
            signed = client.create_order(order_args)
            return client.post_order(signed, OrderType.GTC)

        return await asyncio.to_thread(_place)

    async def get_order_book(self, token_id: str) -> dict:
        """Fetch the current order book for *token_id* (unauthenticated)."""

        def _book():
            client = ClobClient(host=self.cfg.clob_url, chain_id=self.cfg.chain_id)
            return client.get_order_book(token_id)

        return await asyncio.to_thread(_book)

    # ──────────────────────────────────────────────────────────
    # Data API – leader trades
    # ──────────────────────────────────────────────────────────

    async def get_leader_trades(
        self, leader_wallet: str, limit: int = 100
    ) -> list[dict]:
        """Return the most recent trades for *leader_wallet*."""
        resp = await self._http.get(
            f"{self.cfg.data_api_url}/trades",
            params={"user": leader_wallet, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    # ──────────────────────────────────────────────────────────
    # helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def trade_hash(trade: dict) -> str:
        """Deterministic dedup key for a Data-API trade object."""
        blob = (
            f"{trade.get('transactionHash', '')}:"
            f"{trade.get('asset', '')}:"
            f"{trade.get('timestamp', '')}:"
            f"{trade.get('size', '')}:"
            f"{trade.get('price', '')}"
        )
        return hashlib.sha256(blob.encode()).hexdigest()
