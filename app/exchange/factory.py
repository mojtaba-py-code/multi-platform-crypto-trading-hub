"""Factory that builds exchange adapters while enforcing trading-safety policy.

This is the single choke point where the "safe by default" rule is enforced:

* If the server has ``ALLOW_LIVE_TRADING=false`` (the default), **every** trading
  adapter is a :class:`~app.exchange.paper.PaperAdapter`. Live order placement is
  impossible regardless of any per-account or per-request flag.
* If live trading is enabled, an account may still be explicitly a *paper*
  account, in which case it also gets a paper adapter.
* Only when live trading is enabled **and** the account is a live account does a
  credentialed :class:`~app.exchange.ccxt_adapter.CcxtAdapter` get created —
  pointed at the exchange testnet unless testnet is disabled.
"""

from __future__ import annotations

import functools

from app.config import Settings, get_settings
from app.core.exceptions import UnsupportedExchangeError
from app.core.logging import get_logger
from app.exchange.base import ExchangeAdapter
from app.exchange.ccxt_adapter import CcxtAdapter
from app.exchange.credentials import ExchangeCredentials
from app.exchange.paper import PaperAdapter
from app.exchange.paper_store import PaperLedger
from app.exchange.registry import is_supported

log = get_logger(__name__)


class ExchangeFactory:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def live_trading_enabled(self) -> bool:
        return self._settings.allow_live_trading

    def public_market_adapter(self, exchange_id: str) -> CcxtAdapter:
        """A credential-free adapter for public market data only."""
        if not is_supported(exchange_id):
            raise UnsupportedExchangeError(details={"exchange": exchange_id})
        return CcxtAdapter(exchange_id, credentials=None)

    def build(
        self,
        exchange_id: str,
        *,
        credentials: ExchangeCredentials | None = None,
        paper: bool = True,
        market_type: str = "spot",
        ledger: PaperLedger | None = None,
    ) -> ExchangeAdapter:
        """Build the appropriate adapter for an account.

        ``paper`` reflects the account's own mode; the server policy can only
        make trading *more* restrictive, never less. ``ledger`` is the persistent
        paper ledger for the account (supplied by the account service) so
        simulated balances survive across requests.
        """
        if not is_supported(exchange_id):
            raise UnsupportedExchangeError(details={"exchange": exchange_id})

        force_paper = paper or not self.live_trading_enabled
        if force_paper:
            if not self.live_trading_enabled and not paper:
                log.warning(
                    "live_trading_disabled_forcing_paper",
                    exchange=exchange_id,
                )
            market = self.public_market_adapter(exchange_id)
            return PaperAdapter(market, ledger=ledger)

        # Live path: requires complete credentials.
        if credentials is None or not credentials.is_complete():
            raise UnsupportedExchangeError(
                "Live trading requires complete API credentials.",
                details={"exchange": exchange_id},
            )
        creds = ExchangeCredentials(
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            passphrase=credentials.passphrase,
            subaccount=credentials.subaccount,
            testnet=self._settings.use_exchange_testnet,
        )
        log.info(
            "building_live_adapter",
            exchange=exchange_id,
            testnet=creds.testnet,
            market_type=market_type,
        )
        return CcxtAdapter(exchange_id, credentials=creds, default_market_type=market_type)


@functools.lru_cache(maxsize=1)
def get_exchange_factory() -> ExchangeFactory:
    return ExchangeFactory()
