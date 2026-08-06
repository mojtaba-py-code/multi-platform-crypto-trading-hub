"""Registry of supported exchanges and their capabilities.

Adding a new exchange is a matter of appending one :class:`ExchangeInfo` entry
here (assuming ccxt supports it) — the adapter, factory and API pick it up
automatically. This is the single source of truth for "which exchanges exist".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExchangeInfo:
    id: str  # ccxt id
    name: str  # human-readable
    requires_passphrase: bool = False
    supports_futures: bool = True
    supports_margin: bool = True
    has_testnet: bool = True


SUPPORTED_EXCHANGES: dict[str, ExchangeInfo] = {
    e.id: e
    for e in [
        ExchangeInfo("binance", "Binance", supports_futures=True, supports_margin=True),
        ExchangeInfo("bybit", "Bybit", supports_futures=True, supports_margin=True),
        ExchangeInfo("okx", "OKX", requires_passphrase=True),
        ExchangeInfo("kucoin", "KuCoin", requires_passphrase=True),
        ExchangeInfo("kraken", "Kraken", has_testnet=False),
        ExchangeInfo("coinbase", "Coinbase", supports_futures=False, has_testnet=False),
        ExchangeInfo("bitget", "Bitget", requires_passphrase=True),
        ExchangeInfo("gateio", "Gate.io"),
        ExchangeInfo("mexc", "MEXC", has_testnet=False),
    ]
}


def is_supported(exchange_id: str) -> bool:
    return exchange_id.lower() in SUPPORTED_EXCHANGES


def get_info(exchange_id: str) -> ExchangeInfo | None:
    return SUPPORTED_EXCHANGES.get(exchange_id.lower())
