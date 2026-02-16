"""EOA wallet generation, Fernet encryption, and deterministic proxy-address derivation."""

import logging

from cryptography.fernet import Fernet
from eth_abi import encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

logger = logging.getLogger(__name__)

# Polymarket Gnosis-Safe proxy factory (Polygon mainnet + Amoy testnet)
SAFE_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
SAFE_INIT_CODE_HASH = (
    "0x2bce2127ff07fb632d16c8347c4ebf501f4841168bed00d9e6ef715ddb6fcecf"
)


class WalletManager:
    """Handles key generation, encryption/decryption, and proxy-address derivation."""

    def __init__(self, encryption_key: str):
        key = (
            encryption_key.encode()
            if isinstance(encryption_key, str)
            else encryption_key
        )
        self.fernet = Fernet(key)

    # ── key generation ───────────────────────────────────────

    @staticmethod
    def generate_wallet() -> tuple[str, str]:
        """Return ``(checksum_address, hex_private_key)``."""
        acct = Account.create()
        return acct.address, acct.key.hex()

    # ── encryption helpers ───────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.fernet.decrypt(ciphertext.encode()).decode()

    # ── proxy-address derivation ─────────────────────────────

    @staticmethod
    def derive_proxy_address(
        owner_address: str,
        factory: str = SAFE_FACTORY,
    ) -> str:
        """Reproduce the CREATE2 derivation used by the Polymarket relayer."""
        owner = to_checksum_address(owner_address)
        factory_cs = to_checksum_address(factory)

        salt = keccak(encode(["address"], [owner]))
        init_hash = bytes.fromhex(SAFE_INIT_CODE_HASH[2:])
        factory_bytes = bytes.fromhex(factory_cs[2:])

        addr_bytes = keccak(b"\xff" + factory_bytes + salt + init_hash)[-20:]
        return to_checksum_address(addr_bytes.hex())
