"""Portfolio aggregation across a user's exchange accounts."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.core.logging import get_logger
from app.exchange.models import Balance
from app.services.account_service import AccountService

log = get_logger(__name__)


class PortfolioService:
    def __init__(self, accounts: AccountService) -> None:
        self._accounts = accounts

    async def aggregate_balances(self, user_id: str) -> dict[str, Balance]:
        """Sum balances of the same asset across all of a user's accounts.

        Failures on an individual account are isolated so one unhealthy exchange
        does not blank the whole portfolio.
        """
        totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
        accounts = await self._accounts.list_accounts(user_id)
        for account in accounts:
            try:
                async with self._accounts.adapter_session(account) as adapter:
                    balances = await adapter.fetch_balance()
            except Exception as exc:  # noqa: BLE001 - isolate per-account failures
                log.warning(
                    "balance_fetch_failed",
                    account=account.id,
                    exchange=account.exchange_id,
                    error=str(exc),
                )
                continue
            for bal in balances:
                totals[bal.asset][0] += bal.free
                totals[bal.asset][1] += bal.used
        return {
            asset: Balance(asset=asset, free=free, used=used)
            for asset, (free, used) in totals.items()
        }
